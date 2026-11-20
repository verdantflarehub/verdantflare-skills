"""Deterministic Markdown relocation, with original bytes retained as evidence.

No file is moved by this module. Consumers verify the physical result before
replaying original technical checks through the scoped, read-only logical view.
"""
from __future__ import annotations

from pathlib import Path
import os
import re
from urllib.parse import quote, unquote, urlsplit, urlunsplit

from delegated_policy import PolicyError, keys, need, parse, path_name
import evidence_view as ev
import process_receipts as pr

CODE = 'PL-DELEGATE-ARCHIVE'
KINDS = ('plan', 'reviews', 'acceptance', 'changes')
MAX_FILES = 2048
MAX_BYTES = 32 * 1024 * 1024
HEADER = '归档映射'


def safe_path(root: Path, rel: str) -> Path:
    path_name(rel)
    need(rel.endswith('.md') and not rel.startswith(('.tmp/', '.git/', '.gdrive/')),
         CODE, 'Archive originals must be repository Markdown')
    target = root / rel
    # Do not allow internal aliases to turn one declared source into another.
    for parent in (target, *target.parents):
        if parent == root:
            break
        need(not parent.is_symlink(), CODE, 'Archive paths cannot use symbolic links')
    need(target.resolve().is_relative_to(root), CODE, 'Archive path escapes repository')
    return target


def _target(root: Path, path: Path, href: str) -> str | None:
    u = urlsplit(href)
    if u.scheme or u.netloc or not u.path:
        return None
    if Path(unquote(u.path)).is_absolute() or '\\' in u.path:
        return None
    lexical = Path(os.path.abspath(path.parent / unquote(u.path)))
    if not lexical.is_relative_to(root):
        return None
    return lexical.relative_to(root).as_posix()


def related(doc, root: Path, active: str) -> bool:
    from plan_lint import extract_md_links
    values = [doc.header_fields.get(k, '') for k in ('关联计划', '关联任务')]
    return any(_target(root, doc.path, link) == active
               for value in values for link in extract_md_links(value))


def moves_from_docs(root: Path, active: str, docs: dict[str, object], *, cancellation: bool = False) -> dict[str, str]:
    need(active in docs and active.startswith('docs/plan/'), CODE, 'Original active plan is missing')
    result = {active: active.replace('docs/plan/', 'docs/archive/plan/', 1)}
    for rel, doc in docs.items():
        category = next((kind for kind in KINDS[1:] if rel.startswith(f'docs/{kind}/')), None)
        if category and related(doc, root, active):
            if category == 'changes':
                permitted = {'已完成', '已废弃'} if cancellation else {'已完成'}
                need(doc.header_fields.get('变更状态') in permitted, CODE, 'Related active change is not closed')
            result[rel] = rel.replace(f'docs/{category}/', f'docs/archive/{category}/', 1)
    return result


def relocate_markdown(raw: bytes, root: Path, source: str, destination: str, moves: dict[str, str]) -> bytes:
    """Rewrite inline/reference/HTML link destinations, never fenced/inline code."""
    from plan_lint import parse_doc_text
    text = raw.decode('utf-8')
    doc = parse_doc_text(text, root / source)
    src = root / source; dst = root / destination

    def rewrite(href: str) -> str:
        wrapped = href.startswith('<') and href.endswith('>')
        plain = href[1:-1] if wrapped else href
        target = _target(root, src, plain)
        if target is None or (source == destination and target not in moves):
            return href
        mapped = moves.get(target, target)
        u = urlsplit(plain)
        path = os.path.relpath(root / mapped, dst.parent).replace(os.sep, '/')
        replacement = urlunsplit(('', '', quote(path, safe='/._-~'), u.query, u.fragment))
        return '<' + replacement + '>' if wrapped else replacement

    output = []
    for index, line in enumerate(text.splitlines(keepends=True)):
        if index in doc.code_lines:
            output.append(line); continue
        # Preserve exact inline code spans, including multi-backtick delimiters.
        code = [(m.start(), m.end()) for m in re.finditer(r'(`+).*?\1', line)]
        edits = []
        patterns = [r'\]\(\s*(?P<url><[^>\n]*>|[^\s)]+)',
                    r'^ {0,3}\[[^\]\n]+\]:\s*(?P<url><[^>\n]*>|[^\s]+)',
                    r'\b(?:href|src)=[\"\x27](?P<url>[^\"\x27]+)[\"\x27]']
        for pattern in patterns:
            for match in re.finditer(pattern, line):
                start, end = match.span('url')
                if any(a <= start < b for a, b in code):
                    continue
                replacement = rewrite(match.group('url'))
                if replacement != match.group('url'):
                    edits.append((start, end, replacement))
        for start, end, replacement in sorted(set(edits), reverse=True):
            line = line[:start] + replacement + line[end:]
        output.append(line)
    result = ''.join(output)
    # Unsupported Markdown destination syntax fails instead of leaving a
    # semantically changed link (e.g. a path with unescaped parentheses).
    updated = parse_doc_text(result, dst)
    before = [href for href, _ in doc.links] + [href for href, _ in doc.images]
    after = [href for href, _ in updated.links] + [href for href, _ in updated.images]
    need(len(before) == len(after), CODE, 'Relocation changed Markdown link structure')
    for old, new in zip(before, after):
        target = _target(root, src, old)
        if target is not None:
            need(_target(root, dst, new) == moves.get(target, target), CODE, 'Unsupported Markdown link relocation')
    return result.encode('utf-8')


def map_header(root: Path, archived: str, reference: dict) -> str:
    rel = os.path.relpath(root / reference['path'], (root / archived).parent).replace(os.sep, '/')
    return f'{HEADER}：[归档证据]({rel})'


def attach_header(raw: bytes, header: str) -> bytes:
    first, sep, rest = raw.partition(b'\n')
    need(bool(sep) and first.startswith(b'# '), CODE, 'Archive plan must start with its title')
    return first + sep + b'\n' + header.encode('utf-8') + b'\n' + rest


def prepare(root: Path, plan: Path, repository_id: str, plan_id: str) -> dict:
    """Caller has already passed current native H2 and active preflight."""
    from plan_lint import iter_repo_markdown_files, parse_doc_text
    root = root.resolve(); active = plan.resolve().relative_to(root).as_posix()
    paths = iter_repo_markdown_files(root)
    originals = {}; docs = {}
    for path in paths:
        rel = path.relative_to(root).as_posix()
        raw = path.read_bytes()
        originals[rel] = raw
        docs[rel] = parse_doc_text(raw.decode('utf-8'), path)
    doc = docs[active]
    cancellation = doc.header_fields.get('计划状态') == '已废弃'
    need(not doc.duplicate_keys and HEADER not in doc.header_fields and doc.tasks
         and (cancellation or doc.header_fields.get('计划状态') == '已完成' and all(t.checked for t in doc.tasks)),
         CODE, 'Archive preparation requires a fully completed plan without an existing map')
    moves = moves_from_docs(root, active, docs, cancellation=cancellation)
    for old, new in moves.items():
        safe_path(root, old); target = safe_path(root, new)
        need(not target.exists(), CODE, 'Archive destination already exists')
    entries = []; total = 0
    for old, raw in originals.items():
        new = moves.get(old, old)
        converted = relocate_markdown(raw, root, old, new, moves)
        if new == old and converted == raw:
            continue
        need(bool(docs[old].h1), CODE, 'Archive originals must be Markdown documents, not disguised policy or source records')
        need(len(raw) <= pr.MAX_DOCUMENT // 2, CODE, 'Affected Markdown source is too large for archive record')
        safe_path(root, old); safe_path(root, new)
        total += len(raw)
        need(len(entries) < MAX_FILES and total <= MAX_BYTES, CODE, 'Archive bundle exceeds limit')
        entries.append({'source': old, 'destination': new, 'original': raw,
                        'result_sha256': pr.sha(converted)})
    return {'schema_version': 1, 'kind': 'archive-relocation-v1', 'outcome': 'cancellation' if cancellation else 'delivery', 'repository_id': repository_id,
            'plan_id': plan_id, 'active_plan_path': active, 'archive_plan_path': moves[active],
            'tool': pr.tool_fingerprint(), 'entries': sorted(entries, key=lambda e: e['source'])}


def persist(root: Path, prepared: dict) -> dict:
    entries = []
    for entry in prepared['entries']:
        original = pr.store_bytes(root, pr.canonical({'schema_version': 1, 'kind': 'archive-markdown-v1',
                                                      'text': entry['original'].decode('utf-8')}))
        entries.append({**entry, 'original': original})
    return pr.store_bytes(root, pr.canonical({**prepared, 'entries': entries}))


def validate(root: Path, reference: dict, *, repository_id: str, plan_id: str,
             archived: str) -> tuple[dict, dict[Path, bytes]]:
    """Validate physical bytes first; no logical view is active during this read."""
    from plan_lint import iter_repo_markdown_files, parse_doc_text
    root = root.resolve()
    mapping = parse(pr.read_content(root, reference))
    keys(mapping, {'schema_version', 'kind', 'outcome', 'repository_id', 'plan_id', 'active_plan_path',
                   'archive_plan_path', 'tool', 'entries'})
    need(type(mapping['schema_version']) is int and mapping['schema_version'] == 1
         and mapping['kind'] == 'archive-relocation-v1' and mapping['outcome'] in {'delivery', 'cancellation'}
         and mapping['repository_id'] == repository_id
         and mapping['plan_id'] == plan_id and mapping['archive_plan_path'] == archived,
         CODE, 'Archive map identity differs')
    need(pr.canonical(mapping['tool']) == pr.canonical(pr.tool_fingerprint()), CODE, 'Archive tool changed')
    active = mapping['active_plan_path']
    need(isinstance(active, str) and active.startswith('docs/plan/')
         and archived == active.replace('docs/plan/', 'docs/archive/plan/', 1), CODE, 'Archive is not isomorphic')
    entries = mapping['entries']
    need(isinstance(entries, list) and 0 < len(entries) <= MAX_FILES, CODE, 'Archive entries are missing or oversized')
    originals = {}; destinations = set(); docs = {}; total = 0
    for entry in entries:
        keys(entry, {'source', 'destination', 'original', 'result_sha256'})
        old, new = entry['source'], entry['destination']
        safe_path(root, old); safe_path(root, new)
        need(old not in originals and new not in destinations, CODE, 'Duplicate archive paths')
        blob = parse(pr.read_content(root, entry['original']))
        keys(blob, {'schema_version', 'kind', 'text'})
        need(type(blob['schema_version']) is int and blob['schema_version'] == 1
             and blob['kind'] == 'archive-markdown-v1' and isinstance(blob['text'], str), CODE, 'Invalid original Markdown')
        raw = blob['text'].encode('utf-8'); total += len(raw)
        need(total <= MAX_BYTES, CODE, 'Archive original bytes exceed limit')
        originals[old] = raw; destinations.add(new)
        docs[old] = parse_doc_text(blob['text'], root / old)
        need(bool(docs[old].h1), CODE, 'Archive originals must be Markdown documents')
    need(active in docs, CODE, 'Archive plan original is absent')
    plan = docs[active]
    cancellation = mapping['outcome'] == 'cancellation'
    need(not plan.duplicate_keys and HEADER not in plan.header_fields
         and plan.header_fields.get('计划 ID') == plan_id
         and plan.header_fields.get('计划状态') == ('已废弃' if cancellation else '已完成')
         and plan.tasks and (cancellation or all(t.checked for t in plan.tasks)), CODE, 'Original plan is incomplete or differs')
    moves = moves_from_docs(root, active, docs, cancellation=cancellation)
    need(set(moves.values()).isdisjoint(set(originals)), CODE, 'Archive destinations overlap original paths')
    for entry in entries:
        old, new = entry['source'], entry['destination']
        need(new == moves.get(old, old), CODE, 'Unrelated or cross-plan archive mapping')
        raw = originals[old]
        result = relocate_markdown(raw, root, old, new, moves)
        need(old != new or result != raw, CODE, 'Archive map includes an unchanged unrelated document')
        need(entry['result_sha256'] == pr.sha(result), CODE, 'Archive transformation digest differs')
        if old in moves:
            need(not (root / old).exists() and not (root / old).is_symlink(), CODE, 'Active archive source remains')
        if old == active:
            result = attach_header(result, map_header(root, archived, reference))
        with (root / new).open('rb') as stream:
            need(stream.read(pr.MAX_DOCUMENT + 1) == result, CODE, 'Physical archive differs from deterministic relocation')
    # Do not omit related reports/acceptance/changes from either directory side.
    for path in iter_repo_markdown_files(root):
        rel = path.relative_to(root).as_posix()
        text = path.read_text(encoding='utf-8')
        doc = None
        if any(Path(old).name in text for old in moves):
            doc = parse_doc_text(text, path)
            for href, _ in doc.links + doc.images:
                need(_target(root, path, href) not in moves, CODE, 'Repository link still targets an active archive source')
        if not any(rel.startswith(f'docs/{prefix}{kind}/') for prefix in ('', 'archive/') for kind in KINDS[1:]):
            continue
        doc = doc or parse_doc_text(text, path)
        if related(doc, root, active):
            need(False, CODE, 'Related document still points to the active plan')
        if related(doc, root, archived) and rel.startswith(('docs/archive/reviews/', 'docs/archive/acceptance/')):
            need(rel in moves.values(), CODE, 'Unmapped archived review or acceptance')
    return mapping, {root / key: value for key, value in originals.items()}
