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
DIMENSION_LABELS = {'category': '分类', 'workflow': '工作流', 'rules': '规范', 'acceptance': '验收'}
NAME = re.compile(r'[a-z0-9]+(?:-[a-z0-9]+)*\Z')
CAPABILITY_ID = re.compile(r'(0[1-9]|[1-9]\d)\.([a-z0-9]+(?:-[a-z0-9]+)*)\Z')
ENTRYPOINT_FILE = re.compile(r'((?:0[1-9]|[1-9]\d)\.[a-z0-9]+(?:-[a-z0-9]+)*)\.entrypoint\.md\Z')
CAPABILITY_DIM_FILE = re.compile(
    r'((?:0[1-9]|[1-9]\d)\.[a-z0-9]+(?:-[a-z0-9]+)*)\.(category|workflow|rules|acceptance)\.md\Z')
COMMON_FILE = re.compile(r'00\.common\.(category|workflow|rules|acceptance)\.md\Z')
# Legacy 01.base-* + Cx.<slug>-<dim>.md compatibility patterns.
MATRIX_FILE = re.compile(r'(C\d+\.[a-z0-9]+(?:-[a-z0-9]+)*)-(category|workflow|rules|acceptance)\.md\Z')
# ASCII-only boundaries: Python treats CJK as word characters, so \b misses "TODO定义".
PLACEHOLDER = re.compile(
    r'(?<![A-Za-z0-9_])(?:TODO|TBD)(?![A-Za-z0-9_])|\{\{[^{}\n]+\}\}|'
    r'<(?:skill-name|slug|Cx|capability|填写[^>]*|技能中文名称|分类中文名称|main_script|evaluator_script)>')
INLINE_CODE = re.compile(r'(`+).+?\1')
XML_TAG = re.compile(r'</?[A-Za-z][^<>]*>')
RESERVED = ('anthropic', 'claude')
MAX_NAME, MAX_DESCRIPTION = 64, 1024
# Entry budget: SKILL.md is a router; details live in capability entrypoints and optional 00.common.*.md.
MAX_ENTRY_CHARS, MAX_ENTRY_CODE_LINES, MAX_ENTRY_H3 = 4000, 20, 3
MAX_REFERENCE_CHARS = 10000
MAX_CAPABILITY_LABEL_CHARS = 12
# Required H2 order (scope, reading route, tools); a repo-mandated coding section may come first.
ENTRY_SECTIONS = (('范围', 'scope'), ('读取', 'read'), ('工具', 'tool'))
OPTIONAL_FIRST_SECTION = ('编码', 'coding')
# Each NN.<cap>.entrypoint.md must declare input/output followed by category, workflow, rules, acceptance.
ENTRYPOINT_SECTIONS = (
    ('输入与输出', ('输入', '输出', 'input', 'output')),
    ('分类', ('分类', 'category')),
    ('工作流', ('工作流', '流程', 'workflow')),
    ('规范', ('规范', '规则', 'rules')),
    ('验收', ('验收', 'acceptance')),
)
GENERIC_DIMENSION_HINTS = {
    'category': ('分类', '边界', '适用', '分支', 'category', 'scope'),
    'workflow': ('流程', '执行', '操作', '恢复', 'workflow', 'procedure'),
    'rules': ('规则', '约束', '默认值', '例外', '写法', 'rule', 'constraint'),
    'acceptance': ('验收', '验证', '门禁', '交付', '评分', 'acceptance', 'verify'),
}
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


def linked_paths(tokens):
    """Return normalized relative link paths from markdown tokens."""
    return {unquote(urlsplit(child.attrGet('href') or '').path)
            for token in tokens for child in token.children or [] if child.type == 'link_open'}


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


def detect_layout(root):
    """Use capability-entrypoint layout by default; fall back to legacy 01.base-* when present."""
    refs = root / 'references'
    if refs.is_dir():
        if any(ENTRYPOINT_FILE.fullmatch(f.name) for f in refs.glob('*.md')):
            return 'capability'
        if any((refs / f'01.base-{dim}.md').is_file() for dim in DIMENSIONS):
            return 'legacy'
    return 'capability'


def entry_errors(root, body, tokens, layout):
    """Enforce the uniform entry shape: a short router with three fixed H2 sections."""
    errors = []
    if len(body) > MAX_ENTRY_CHARS:
        errors.append(f'SKILL.md: body has {len(body)} characters (maximum {MAX_ENTRY_CHARS}); move details to references')
    code = sum(t.map[1] - t.map[0] for t in tokens if t.type in ('fence', 'code_block') and t.map)
    if code > MAX_ENTRY_CODE_LINES:
        errors.append(f'SKILL.md: code blocks span {code} lines (maximum {MAX_ENTRY_CODE_LINES}); '
                      'move full commands to capability workflow references')
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
    linked = linked_paths(tokens)
    if layout == 'legacy':
        for dim in DIMENSIONS:
            rel = f'references/01.base-{dim}.md'
            if rel not in linked:
                errors.append(f'SKILL.md: must link {rel}')
    else:
        refs = root / 'references'
        if refs.is_dir():
            for ep in sorted(f for f in refs.glob('*.md') if ENTRYPOINT_FILE.fullmatch(f.name)):
                rel = f'references/{ep.name}'
                if rel not in linked:
                    errors.append(f'SKILL.md: must link capability entrypoint {rel}')
    for extra in sorted(root.glob('*.md')):
        if extra.name != 'SKILL.md':
            errors.append(f'{extra.name}: no Markdown besides SKILL.md at skill root; move into references or project docs')
    if layout == 'capability':
        for _, links in route_table_rows(tokens):
            if any(COMMON_FILE.fullmatch(Path(unquote(u.path)).name) for u in links):
                errors.append('SKILL.md: do not list 00.common.*.md as a capability row in the routing table; '
                              'reference it from entrypoints or a note below the table')
                break
    return errors


def route_table_rows(tokens):
    """Extract (left_text, right_link_parts) tuples from the first table in the reading route H2 section."""
    in_route, in_tbody = False, False
    rows, row, cell_inline = [], None, None
    for i, token in enumerate(tokens):
        if token.type == 'heading_open' and token.tag == 'h2':
            title = plain_inline(tokens[i + 1]).lower()
            if in_route:
                break
            if any(word in title for word in ENTRY_SECTIONS[1]):
                in_route = True
            continue
        if not in_route:
            continue
        if token.type == 'tbody_open':
            in_tbody = True
        elif token.type == 'tbody_close' and in_tbody:
            break
        elif not in_tbody:
            continue
        elif token.type == 'tr_open':
            row = []
        elif token.type == 'tr_close' and row is not None:
            rows.append(row)
            row = None
        elif token.type == 'td_open' and row is not None:
            cell_inline = []
        elif token.type == 'inline' and cell_inline is not None:
            cell_inline.append(token)
        elif token.type == 'td_close' and row is not None and cell_inline is not None:
            row.append(cell_inline)
            cell_inline = None
    parsed = []
    for cells in rows:
        if len(cells) < 2:
            continue
        left = ' '.join(plain_inline(t) for t in cells[0]).lower()
        links = [urlsplit(child.attrGet('href') or '')
                 for t in cells[1] for child in (t.children or []) if child.type == 'link_open']
        parsed.append((left, links))
    return parsed


def capability_label_warnings(tokens):
    """Warn when capability names in the SKILL.md routing table are overly verbose."""
    warnings = []
    for left, _ in route_table_rows(tokens):
        compact = re.sub(r'\s+', '', left)
        if len(compact) > MAX_CAPABILITY_LABEL_CHARS:
            warnings.append(
                f'SKILL.md: capability label too long ({len(compact)} chars, max {MAX_CAPABILITY_LABEL_CHARS}): '
                f'{left!r}; tighten via iterative word-reduction')
    return warnings


def routing_warnings(tokens):
    """Warn when a legacy reading route table only lists the 4 generic base dimensions."""
    parsed = route_table_rows(tokens)
    if len(parsed) != len(DIMENSIONS):
        return []
    matched_dims = {}
    for left, links in parsed:
        if len(links) != 1 or links[0].fragment:
            return []
        path = unquote(links[0].path)
        dim = next((d for d in DIMENSIONS if path == f'references/01.base-{d}.md'), None)
        if dim is None or dim in matched_dims:
            return []
        if not any(hint in left for hint in GENERIC_DIMENSION_HINTS[dim]):
            return []
        matched_dims[dim] = left
    if len(matched_dims) == len(DIMENSIONS):
        return ['SKILL.md: reading route table lists only the 4 generic base dimensions; '
                'list concrete capability phrases linking to specific references or section anchors']
    return []


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


def capability_errors(root, document, skill_links):
    """Validate flat NN.<cap>.entrypoint.md + optional NN.<cap>.<dim>.md + optional 00.common.<dim>.md."""
    errors = []
    refs = root / 'references'
    if not refs.is_dir():
        return ['references: missing capability entrypoint (e.g. 01.<capability>.entrypoint.md)']
    entrypoints, numbers, dim_files = {}, {}, []
    for file in sorted(refs.glob('*.md')):
        ep = ENTRYPOINT_FILE.fullmatch(file.name)
        if ep:
            cap = ep.group(1)
            num = cap.split('.')[0]
            if num in numbers and numbers[num] != cap:
                errors.append(f'capability: duplicate numeric prefix {num} ({numbers[num]} vs {cap})')
            numbers[num] = cap
            entrypoints[cap] = file
            continue
        dim = CAPABILITY_DIM_FILE.fullmatch(file.name)
        if dim:
            dim_files.append((dim.group(1), dim.group(2), file))
            continue
        if COMMON_FILE.fullmatch(file.name):
            continue
        if re.match(r'^00\.common\.', file.name):
            errors.append(f'common: malformed filename {file.name} (expected 00.common.<category|workflow|rules|acceptance>.md)')
        elif re.match(r'^\d{2}\.', file.name):
            errors.append(f'capability: malformed filename {file.name} '
                          '(expected NN.<slug>.entrypoint.md or NN.<slug>.<category|workflow|rules|acceptance>.md)')
    if not entrypoints:
        errors.append('references: missing capability entrypoint (e.g. 01.<capability>.entrypoint.md)')
        return errors
    ep_links = {}
    for cap, file in entrypoints.items():
        try:
            _, _, tokens, _ = document(file)
        except (OSError, UnicodeError, ValueError, yaml.YAMLError):
            continue
        ep_links[cap] = {Path(p).name for p in linked_paths(tokens) if p}
        h2 = [plain_inline(tokens[i + 1]).lower()
              for i, token in enumerate(tokens) if token.type == 'heading_open' and token.tag == 'h2']
        for label, keywords in ENTRYPOINT_SECTIONS:
            if not any(any(word in title for word in keywords) for title in h2):
                errors.append(f'references/{file.name}: missing H2 section for {label}')
    skill_ref_names = {Path(p).name for p in skill_links if p.startswith('references/')}
    for cap, _, file in dim_files:
        if cap not in entrypoints:
            errors.append(f'capability: orphan dimension file {file.name} (missing {cap}.entrypoint.md)')
        elif file.name not in ep_links.get(cap, set()) and file.name not in skill_ref_names:
            errors.append(f'capability: {file.name} must be linked from {cap}.entrypoint.md or SKILL.md')
    return errors


def matrix_errors(root):
    """Validate legacy 01.base-* + Cx.<slug>-<dim>.md layout."""
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
    layout = detect_layout(root)
    paths = [root / 'SKILL.md']
    paths.extend(sorted((root / 'references').rglob('*.md')))

    def document(path):
        path = path.resolve()
        if path not in cache:
            text = path.read_text(encoding='utf-8-sig')
            meta, body, offset = parse_frontmatter(text)
            cache[path] = (text, meta, MD.parse(body), offset)
        return cache[path]

    skill_links = set()
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
            skill_links = linked_paths(tokens)
            errors.extend(metadata_errors(root, meta))
            if structure:
                errors.extend(entry_errors(root, '\n'.join(text.splitlines()[offset:]), tokens, layout))
                if layout == 'legacy':
                    warnings.extend(routing_warnings(tokens))
                else:
                    warnings.extend(capability_label_warnings(tokens))
        elif structure and len(text) > MAX_REFERENCE_CHARS:
            warnings.append(f'{label}: {len(text)} characters (over {MAX_REFERENCE_CHARS}); split by dimension or trim history')
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
        if layout == 'legacy':
            errors.extend(matrix_errors(root))
        else:
            errors.extend(capability_errors(root, document, skill_links))
    return errors, warnings


def capabilities_from(raw):
    """Parse comma-separated capability ids (e.g. '01.t2v,02.i2v' or 't2v,i2v')."""
    if not raw.strip():
        return ['01.core']
    items, seen_nums, seen_slugs = [], set(), set()
    for idx, part in enumerate(raw.split(','), 1):
        item = part.strip()
        if not item:
            raise ValueError(f'invalid capability: {item!r}')
        if '.' not in item:
            if not NAME.fullmatch(item):
                raise ValueError(f'invalid capability slug: {item!r}')
            item = f'{idx:02d}.{item}'
        match = CAPABILITY_ID.fullmatch(item)
        if not match:
            raise ValueError(f'invalid capability: {item!r} (expected NN.<slug> with NN in 01..99)')
        num, slug = match.groups()
        if num in seen_nums:
            raise ValueError(f'duplicate capability number: {num}')
        if slug in seen_slugs:
            raise ValueError(f'duplicate capability slug: {slug}')
        seen_nums.add(num)
        seen_slugs.add(slug)
        items.append(item)
    return items


def common_from(raw):
    """Parse optional comma-separated 00.common.<dim>.md dimensions."""
    if not raw.strip():
        return []
    dims = []
    for part in raw.split(','):
        dim = part.strip()
        if dim not in DIMENSIONS:
            raise ValueError(f'invalid common dimension: {dim!r} (expected one of {", ".join(DIMENSIONS)})')
        if dim not in dims:
            dims.append(dim)
    return dims


def initialize(root, capabilities_arg='', common_arg='', split=False):
    root = Path(root).absolute()
    if not NAME.fullmatch(root.name) or len(root.name) > MAX_NAME:
        raise ValueError(f'target directory name must be lowercase kebab-case, at most {MAX_NAME} characters')
    if any(word in root.name for word in RESERVED) and not root.name.startswith('verdantflare-agent-'):
        raise ValueError('target directory name must not contain reserved words anthropic/claude')
    capabilities = capabilities_from(capabilities_arg)
    commons = common_from(common_arg)
    templates = Path(__file__).resolve().parent.parent / 'resources' / 'templates'
    outputs = {}

    def render(filename, **values):
        text = (templates / filename).read_text(encoding='utf-8')
        for key, value in {'skill_name': root.name, **values}.items():
            text = text.replace('{{' + key + '}}', value)
        if re.search(r'\{\{[^{}]+\}\}', text):
            raise ValueError(f'unresolved template variable in {filename}')
        return text

    for dim in commons:
        outputs[f'references/00.common.{dim}.md'] = render(
            '00.common.dim.template.md', dimension=dim, dimension_label=DIMENSION_LABELS[dim])

    common_links = ('、'.join(f'[公共{DIMENSION_LABELS[d]}](./00.common.{d}.md)' for d in commons)
                    or '无（本能力无跨能力共享文档，需要时按需补充 `00.common.<维度>.md`）。')
    ep_template = 'NN.cap.entrypoint-split.template.md' if split else 'NN.cap.entrypoint.template.md'
    rows = []
    for cap in capabilities:
        outputs[f'references/{cap}.entrypoint.md'] = render(ep_template, capability=cap, common_links=common_links)
        rows.append(f'| TODO {cap} 能力说明 | [{cap}.entrypoint.md](references/{cap}.entrypoint.md) |')
        if split:
            for dim in DIMENSIONS:
                outputs[f'references/{cap}.{dim}.md'] = render(
                    'NN.cap.dim.template.md', capability=cap, dimension=dim, dimension_label=DIMENSION_LABELS[dim])
    common_note = ('跨能力公共文档：' + '、'.join(f'[00.common.{d}.md](references/00.common.{d}.md)' for d in commons)
                   + '（由各能力入口引用）。' if commons else '')
    outputs['SKILL.md'] = render('SKILL.template.md', capability_rows='\n'.join(rows), common_note=common_note)

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
    init = commands.add_parser('init', help='create a non-overwriting capability-entrypoint scaffold')
    init.add_argument('skill_dir', type=Path)
    init.add_argument('--capabilities', default='', help='e.g. 01.t2v,02.i2v,03.fl2v,04.ref2v (or t2v,i2v)')
    init.add_argument('--common', default='', help='optional shared 00.common.<dim>.md files, e.g. rules,acceptance')
    init.add_argument('--split', action='store_true', help='split each capability into 4 dimension files')
    check = commands.add_parser('lint', help='check static invariants, not business correctness')
    check.add_argument('skill_dir', type=Path)
    check.add_argument('--style', choices=('generic', 'numbered'), default='generic')
    check.add_argument('--allow-placeholders', action='store_true')
    args = parser.parse_args()
    try:
        if args.command == 'init':
            created, skipped = initialize(args.skill_dir, args.capabilities, args.common, args.split)
            print(f'SCAFFOLD: created {len(created)}, preserved {len(skipped)} files; fill placeholders before delivery.')
            if skipped:
                print('Existing files preserved; merge capability rows into SKILL.md manually when adding capabilities.')
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
