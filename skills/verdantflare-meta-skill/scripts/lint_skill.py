#!/usr/bin/env python3
"""Initialize skills and validate declared static invariants (Python 3.10+)."""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from urllib.parse import unquote, urlsplit

try:
    import yaml
    from markdown_it import MarkdownIt
except ImportError as exc:
    raise SystemExit('Missing dependency: run python -m pip install -r '
                     f'"{Path(__file__).with_name("requirements.txt")}"') from exc

DIMENSIONS = ('category', 'workflow', 'rules', 'acceptance')
NAME = re.compile(r'[a-z0-9]+(?:-[a-z0-9]+)*\Z')
CATEGORY = re.compile(r'C\d+\.[a-z0-9]+(?:-[a-z0-9]+)*\Z')
MATRIX_FILE = re.compile(r'(C\d+\.[a-z0-9]+(?:-[a-z0-9]+)*)-(category|workflow|rules|acceptance)\.md\Z')
# ASCII-only boundaries: Python treats CJK as word characters, so \b misses "TODO定义".
PLACEHOLDER = re.compile(r'(?<![A-Za-z0-9_])(?:TODO|TBD)(?![A-Za-z0-9_])|\{\{[^{}\n]+\}\}|<(?:skill-name|slug|Cx|填写[^>]*|技能中文名称|分类中文名称|main_script|evaluator_script)>')
INLINE_CODE = re.compile(r'(`+).+?\1')
XML_TAG = re.compile(r'</?[A-Za-z][^<>]*>')
RESERVED = ('anthropic', 'claude')
MAX_NAME, MAX_DESCRIPTION = 64, 1024
# Entry budget: SKILL.md is a router; details live in the four base references.
MAX_ENTRY_CHARS, MAX_ENTRY_CODE_LINES, MAX_ENTRY_H3 = 4000, 20, 3
MAX_REFERENCE_CHARS = 10000
# Required H2 order (scope, reading route, tools); a repo-mandated coding section may come first.
ENTRY_SECTIONS = (('范围', 'scope'), ('读取', 'read'), ('工具', 'tool'))
OPTIONAL_FIRST_SECTION = ('编码', 'coding')
MD = MarkdownIt('commonmark').enable('table')
# Parse rejected browser schemes too so the linter can diagnose them.
# Tokens are inspected only; this tool never renders or opens these URLs.
MD.validateLink = lambda target: True


class UniqueLoader(yaml.SafeLoader):
    """Reject duplicate keys rather than silently replacing an instruction."""


def unique_mapping(loader, node, deep=False):
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        try:
            if key in result:
                raise ValueError(f'duplicate YAML key: {key}')
            result[key] = loader.construct_object(value_node, deep=deep)
        except TypeError as exc:
            raise ValueError('YAML mapping keys must be scalar') from exc
    return result


UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, unique_mapping)


def parse_frontmatter(text):
    """Return metadata, body, and number of lines preceding the body."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != '---':
        return {}, text, 0
    end = next((i for i in range(1, len(lines)) if lines[i].strip() == '---'), None)
    if end is None:
        raise ValueError('unclosed YAML frontmatter')
    meta = yaml.load('\n'.join(lines[1:end]), Loader=UniqueLoader)
    if not isinstance(meta, dict):
        raise ValueError('YAML frontmatter must be a mapping')
    return meta, '\n'.join(lines[end + 1:]), end + 1


def plain_inline(token):
    return ''.join(child.content for child in (token.children or [])
                   if child.type in ('text', 'code_inline', 'image'))


def anchors(tokens):
    """Common GitHub-style heading ids, including duplicate suffixes."""
    result = set()
    for i, token in enumerate(tokens):
        if token.type != 'heading_open':
            continue
        title = plain_inline(tokens[i + 1]).lower()
        slug = re.sub(r'[^\w\- ]', '', title).replace(' ', '-')
        candidate, suffix = slug, 0
        while candidate in result:
            suffix += 1
            candidate = f'{slug}-{suffix}'
        result.add(candidate)
    return result


def check_style(tokens, label):
    errors, counters = [], []
    h1 = 0
    for i, token in enumerate(tokens):
        if token.type == 'heading_open':
            level = int(token.tag[1:])
            title = plain_inline(tokens[i + 1])
            match = re.match(r'^(\d+(?:\.\d+)*)\.?\s+\S', title)
            if level == 1:
                h1 += 1
                if match:
                    errors.append(f'{label}: H1 must be unnumbered')
                continue
            depth = level - 1
            counters = (counters + [0] * depth)[:depth]
            counters[-1] += 1
            if not match or [int(n) for n in match[1].split('.')] != counters:
                errors.append(f'{label}: H{level} requires {".".join(map(str, counters))}: {title}')
        if token.type in ('html_inline', 'html_block') and re.search(r'<br\s*/?>', token.content, re.I):
            errors.append(f'{label}: HTML line break outside code')
        if token.children:
            for child in token.children:
                if child.type == 'html_inline' and re.search(r'<br\s*/?>', child.content, re.I):
                    errors.append(f'{label}: HTML line break outside code')
    if h1 != 1:
        errors.append(f'{label}: expected one H1, found {h1}')
    return errors


def code_lines(tokens, offset):
    """1-based source lines inside fenced or indented code blocks."""
    lines = set()
    for token in tokens:
        if token.type in ('fence', 'code_block') and token.map:
            lines.update(range(token.map[0] + 1 + offset, token.map[1] + 1 + offset))
    return lines


def sibling_skill(root, dest):
    """True when dest lies inside another skill next to root (inter-skill collaboration)."""
    skills = root.parent
    if not dest.is_relative_to(skills) or dest.is_relative_to(root):
        return False
    parts = dest.relative_to(skills).parts
    return bool(parts) and (skills / parts[0] / 'SKILL.md').is_file()


def repo_root(root):
    """Nearest ancestor containing .git (directory or worktree file), else None."""
    for parent in root.parents:
        if (parent / '.git').exists():
            return parent
    return None


def entry_errors(root, body, tokens):
    """Enforce the uniform entry shape: a short router with three fixed H2 sections."""
    errors = []
    if len(body) > MAX_ENTRY_CHARS:
        errors.append(f'SKILL.md: body has {len(body)} characters (maximum {MAX_ENTRY_CHARS}); move details to references')
    code = sum(t.map[1] - t.map[0] for t in tokens if t.type in ('fence', 'code_block') and t.map)
    if code > MAX_ENTRY_CODE_LINES:
        errors.append(f'SKILL.md: code blocks span {code} lines (maximum {MAX_ENTRY_CODE_LINES}); '
                      'move full commands to 01.base-workflow.md')
    h2, h3 = [], 0
    for i, token in enumerate(tokens):
        if token.type != 'heading_open':
            continue
        if token.tag == 'h2':
            h2.append(plain_inline(tokens[i + 1]).lower())
        elif token.tag == 'h3':
            h3 += 1
        elif token.tag != 'h1':
            errors.append(f'SKILL.md: {token.tag.upper()} not allowed in entry')
    if h3 > MAX_ENTRY_H3:
        errors.append(f'SKILL.md: {h3} H3 headings (maximum {MAX_ENTRY_H3})')
    if h2 and any(word in h2[0] for word in OPTIONAL_FIRST_SECTION):
        h2 = h2[1:]
    if len(h2) != len(ENTRY_SECTIONS) or not all(
            any(word in title for word in words) for title, words in zip(h2, ENTRY_SECTIONS)):
        expected = ' / '.join(words[0] for words in ENTRY_SECTIONS)
        errors.append(f'SKILL.md: H2 sections must be exactly {expected} (optional 编码规范 first): {h2}')
    linked = {unquote(urlsplit(child.attrGet('href') or '').path)
              for token in tokens for child in token.children or [] if child.type == 'link_open'}
    for dim in DIMENSIONS:
        rel = f'references/01.base-{dim}.md'
        if rel not in linked:
            errors.append(f'SKILL.md: must link {rel}')
    for extra in sorted(root.glob('*.md')):
        if extra.name != 'SKILL.md':
            errors.append(f'{extra.name}: no Markdown besides SKILL.md at skill root; move into references or project docs')
    return errors


def metadata_errors(root, meta):
    errors = []
    name, description = meta.get('name'), meta.get('description')
    if not isinstance(name, str) or not NAME.fullmatch(name) or len(name) > MAX_NAME:
        errors.append(f'SKILL.md: name must be lowercase kebab-case, at most {MAX_NAME} characters')
    elif name != root.name:
        errors.append('SKILL.md: name does not match directory')
    elif any(word in name for word in RESERVED) and not name.startswith('verdantflare-agent-'):
        errors.append('SKILL.md: name must not contain reserved words anthropic/claude')
    if not isinstance(description, str) or not description.strip():
        errors.append('SKILL.md: description must be a nonempty string')
    else:
        if len(description) > MAX_DESCRIPTION:
            errors.append(f'SKILL.md: description has {len(description)} characters (maximum {MAX_DESCRIPTION})')
        if XML_TAG.search(description):
            errors.append('SKILL.md: description must not contain XML tags')
    return errors


def matrix_errors(root):
    errors, groups, ids = [], {}, {}
    refs = root / 'references'
    for dim in DIMENSIONS:
        if not (refs / f'01.base-{dim}.md').is_file():
            errors.append(f'references: missing 01.base-{dim}.md')
    for file in sorted(refs.glob('*.md')):
        match = MATRIX_FILE.fullmatch(file.name)
        if not match:
            if re.match(r'^C\d+\.', file.name):
                errors.append(f'matrix: malformed category filename {file.name}')
            continue
        category, dim = match.groups()
        groups.setdefault(category, set()).add(dim)
        cid = category.split('.')[0]
        if cid in ids and ids[cid] != category:
            errors.append(f'matrix: duplicate category id {cid}')
        ids[cid] = category
    for category, dims in groups.items():
        for missing in set(DIMENSIONS) - dims:
            errors.append(f'matrix: missing {category}-{missing}.md')
    return errors


def lint(root, style='generic', allow_placeholders=False, structure=True):
    """structure=False skips the uniform layout checks; used only to unit-test link logic."""
    root = Path(root).resolve()
    errors, warnings, cache = [], [], {}
    if not (root / 'SKILL.md').is_file():
        return ['missing SKILL.md'], warnings
    repo = repo_root(root)
    paths = [root / 'SKILL.md']
    paths.extend(sorted((root / 'references').rglob('*.md')))

    def document(path):
        path = path.resolve()
        if path not in cache:
            text = path.read_text(encoding='utf-8-sig')
            meta, body, offset = parse_frontmatter(text)
            cache[path] = (text, meta, MD.parse(body), offset)
        return cache[path]

    for path in paths:
        label = path.relative_to(root).as_posix()
        if not path.resolve().is_relative_to(root):
            errors.append(f'{label}: document resolves outside skill directory')
            continue
        try:
            text, meta, tokens, offset = document(path)
        except (OSError, UnicodeError, ValueError, yaml.YAMLError) as exc:
            errors.append(f'{label}: {exc}')
            continue
        if path.name == 'SKILL.md' and path.parent == root:
            errors.extend(metadata_errors(root, meta))
            if structure:
                errors.extend(entry_errors(root, '\n'.join(text.splitlines()[offset:]), tokens))
        elif structure and len(text) > MAX_REFERENCE_CHARS:
            warnings.append(f'{label}: {len(text)} characters (over {MAX_REFERENCE_CHARS}); split by branch or trim history')
        skip = code_lines(tokens, offset)
        for number, line in enumerate(text.splitlines(), 1):
            if number not in skip and PLACEHOLDER.search(INLINE_CODE.sub('', line)):
                message = f'{label}:{number}: unfinished placeholder'
                (warnings if allow_placeholders else errors).append(message)
        if style == 'numbered':
            errors.extend(check_style(tokens, label))
        for token in tokens:
            for child in token.children or []:
                if child.type not in ('link_open', 'image'):
                    continue
                target = child.attrGet('href' if child.type == 'link_open' else 'src') or ''
                line = (token.map[0] + 1 + offset) if token.map else 0
                loc = f'{label}:{line}'
                try:
                    parts = urlsplit(target)
                    if parts.scheme in ('http', 'https', 'mailto', 'data') or target.startswith('//'):
                        continue
                    if parts.scheme or unquote(parts.path).startswith(('/', '\\')):
                        errors.append(f'{loc}: nonportable local link: {target}')
                        continue
                    relative = unquote(parts.path)
                    if '\\' in relative:
                        errors.append(f'{loc}: use forward slashes: {target}')
                        continue
                    dest = (path.parent / relative).resolve() if relative else path.resolve()
                    if not (dest.is_relative_to(root) or sibling_skill(root, dest)
                            or (repo is not None and dest.is_relative_to(repo))):
                        errors.append(f'{loc}: link escapes skill and repository: {target}')
                    elif not dest.exists():
                        errors.append(f'{loc}: missing link target: {target}')
                    elif parts.fragment and dest.suffix.lower() == '.md':
                        if unquote(parts.fragment) not in anchors(document(dest)[2]):
                            errors.append(f'{loc}: missing heading anchor: {target}')
                except (OSError, UnicodeError, ValueError, yaml.YAMLError) as exc:
                    errors.append(f'{loc}: cannot check {target}: {exc}')
    if structure:
        errors.extend(matrix_errors(root))
    return errors, warnings


def categories_from(raw):
    categories = []
    ids = set()
    if not raw.strip():
        return categories
    for item in raw.split(','):
        item = item.strip()
        if not CATEGORY.fullmatch(item):
            raise ValueError(f'invalid category: {item!r}')
        cid = item.split('.')[0]
        if cid in ids:
            raise ValueError(f'duplicate category id: {cid}')
        ids.add(cid)
        categories.append(item)
    return categories


def initialize(root, categories_arg=''):
    root = Path(root).absolute()
    if not NAME.fullmatch(root.name) or len(root.name) > MAX_NAME:
        raise ValueError(f'target directory name must be lowercase kebab-case, at most {MAX_NAME} characters')
    if any(word in root.name for word in RESERVED) and not root.name.startswith('verdantflare-agent-'):
        raise ValueError('target directory name must not contain reserved words anthropic/claude')
    categories = categories_from(categories_arg)
    templates = Path(__file__).resolve().parent.parent / 'resources' / 'templates'
    outputs = {}

    def render(filename, **values):
        text = (templates / filename).read_text(encoding='utf-8')
        for key, value in {'skill_name': root.name, **values}.items():
            text = text.replace('{{' + key + '}}', value)
        if re.search(r'\{\{[^{}]+\}\}', text):
            raise ValueError(f'unresolved template variable in {filename}')
        return text

    for dim in DIMENSIONS:
        cat_links = '\n'.join(f'- [{c}](./{c}-{dim}.md)' for c in categories) or '本技能当前没有独立分支。'
        outputs[f'references/01.base-{dim}.md'] = render(f'01.base-{dim}.template.md', category_links=cat_links)
        for category in categories:
            outputs[f'references/{category}-{dim}.md'] = render(f'Cx.slug-{dim}.template.md', category=category)
    # Branch rows go right after the acceptance row so the template stays a valid, formattable table.
    entry = render('SKILL.template.md').splitlines(keepends=True)
    anchor = next(i for i, line in enumerate(entry) if 'references/01.base-acceptance.md' in line)
    rows = [f'| 输入属于 `{c}` | ' + '、'.join(f'[{dim}](references/{c}-{dim}.md)' for dim in DIMENSIONS) + ' |\n'
            for c in categories]
    outputs['SKILL.md'] = ''.join(entry[:anchor + 1] + rows + entry[anchor + 1:])
    # Preflight every path before any mkdir/write; symlinks cannot redirect output.
    resolved_root = root.resolve()
    for rel in outputs:
        target = root / rel
        if not target.resolve().is_relative_to(resolved_root):
            raise ValueError(f'output escapes target directory: {rel}')
        if target.exists() and not target.is_file():
            raise ValueError(f'output is not a file: {rel}')
        for parent in target.parents:
            if parent.exists() and not parent.is_dir():
                raise ValueError(f'parent is not a directory: {parent}')
    created, skipped = [], []
    for rel, text in outputs.items():
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            with target.open('x', encoding='utf-8', newline='\n') as handle:
                handle.write(text)
            created.append(rel)
        except FileExistsError:
            skipped.append(rel)
    return created, skipped


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    init = commands.add_parser('init', help='create a non-overwriting scaffold')
    init.add_argument('skill_dir', type=Path)
    init.add_argument('--categories', default='', help='e.g. C1.local,C2.remote; adds matrix branch files')
    check = commands.add_parser('lint', help='check static invariants, not business correctness')
    check.add_argument('skill_dir', type=Path)
    check.add_argument('--style', choices=('generic', 'numbered'), default='generic')
    check.add_argument('--allow-placeholders', action='store_true')
    args = parser.parse_args()
    try:
        if args.command == 'init':
            created, skipped = initialize(args.skill_dir, args.categories)
            print(f'SCAFFOLD: created {len(created)}, preserved {len(skipped)} files; fill placeholders before delivery.')
            if skipped:
                print('Existing files preserved; merge indexes manually when adding branches.')
            return 0
        errors, warnings = lint(args.skill_dir, args.style, args.allow_placeholders)
        status = 'FAIL' if errors else ('SCAFFOLD ONLY (not delivery-ready)' if args.allow_placeholders else 'PASS (static checks only)')
        print(f'{status}: {len(errors)} errors, {len(warnings)} warnings')
        for message in errors:
            print('ERROR:', message)
        for message in warnings:
            print('WARNING:', message)
        return int(bool(errors))
    except (OSError, ValueError) as exc:
        print(f'FAIL: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
