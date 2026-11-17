"""Measure Markdown structure: list depth, blocks in lists, table columns and width, heading numbers, links, local paths, and core R&D directory contracts.

Text tables must stay readable in plain source: Prettier pads every cell to its column's widest cell,
so the formatted row width is sum(widest cell per column) + 3 * columns + 1, counted in display
columns (East Asian wide/fullwidth characters count 2). Media tables (every header cell holds an
image or <video>) are exempt from the column and width limits.

Errors (E) must be fixed; warnings (W) need a stated reason when kept. Exit 1 when any error exists.
"""
import argparse
from pathlib import Path
import re
import sys
import unicodedata
from urllib.parse import unquote

from markdown_it import MarkdownIt

MAX_LIST_DEPTH = 2
MAX_TABLE_COLUMNS = 4
MAX_TABLE_WIDTH = 120
SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")
LOCAL_PATH = re.compile(r"(?<![\w/])/?[A-Za-z]:[\\/]|file://", re.IGNORECASE)
H2_NUMBER = re.compile(r"^\d+\.\s")

RD_REVIEW_NAME_RE = re.compile(r"^review_\d{8}_[a-z0-9.-]+_(t1|t2|t4)(?:-r(?:[2-9]|[1-9]\d+))?\.md$")
ACCEPTANCE_NAME_RE = re.compile(r"^accept_\d{8}_[a-z0-9.-]+\.md$")
META_KV_RE = re.compile(r"^(?:-\s+)?(?:\*\*)?([^：:*\n#`]+?)(?:\*\*)?[：:]\s*(.*)$")

RD_REVIEW_REQUIRED_META = ("负责人", "评审助手", "关联计划", "关联阶段", "评审对象哈希", "评审结论", "更新时间")
RD_REVIEW_STATUSES = {"待修订", "复评中", "已通过", "已废弃"}
RD_REVIEW_CHAPTERS = (
    "1. 当前依据",
    "2. 架构核验与抽查",
    "3. 工程细节、防漏与验收核验",
    "4. 架构疑难升级裁定",
    "5. 问题闭环与放行结论",
)
# Legacy chapter titles kept valid for existing (in-flight or archived) reports.
RD_REVIEW_LEGACY_CHAPTERS = {
    "2. 架构核验与抽查": ("2. 总体架构核验与符合性审计",),
    "3. 工程细节、防漏与验收核验": ("3. 工程细节与防漏评审",),
}

ACCEPTANCE_REQUIRED_META = ("负责人", "执行助手", "验真助手", "关联计划", "验收状态", "更新时间")
ACCEPTANCE_STATUSES = {"考卷草案", "考卷已锁定", "实跑取证中", "智能体已验真", "CP1已通过", "已废弃"}
ACCEPTANCE_CHAPTERS = (
    "1. 当前依据",
    "2. 锁定验收场景与判据",
    "3. 智能体实跑与演示证据",
    "4. 防伪锚点与双轨验真",
    "5. CP1 呈报包",
)
ACCEPTANCE_LEGACY_CHAPTERS = {
    "5. CP1 呈报包": ("5. CP1 双抽检核验单",),
}


def chapters_match(titles, expected, legacy):
    """Compare H2 titles position by position, accepting legacy aliases."""
    if len(titles) != len(expected):
        return False
    return all(t == e or t in legacy.get(e, ()) for t, e in zip(titles, expected))


def parser():
    return MarkdownIt("commonmark").enable("table")


def detect_doc_type(path):
    """Identify controlled document primary type; fail-closed on docs/{reviews,acceptance}/."""
    p = Path(path).resolve()
    name = p.name
    parts = tuple(seg.lower() for seg in p.parts[:-1])
    if parts[-2:] == ("docs", "reviews") or parts[-3:] == ("docs", "archive", "reviews"):
        if name in {"AGENTS.md", "README.md"}:
            return None
        return "rd-review"
    if parts[-2:] == ("docs", "acceptance") or parts[-3:] == ("docs", "archive", "acceptance"):
        if name in {"AGENTS.md", "README.md"}:
            return None
        return "acceptance"
    if name.lower().endswith(".review.md"):
        return "review"
    return None


def slugify(text):
    slug = re.sub(r"[^\w\- ]", "", text.strip().lower())
    return slug.replace(" ", "-")


def anchors(tokens):
    seen, result = {}, set()
    for i, tok in enumerate(tokens):
        if tok.type == "heading_open":
            text = "".join(c.content for c in tokens[i + 1].children or [] if c.type in ("text", "code_inline"))
            base = slugify(text)
            count = seen.get(base, 0)
            seen[base] = count + 1
            result.add(base if count == 0 else f"{base}-{count}")
    return result


def is_media_cell(inline):
    """Header cell holding an image or <video>: media tables are exempt from the text-table column limit."""
    return any(
        c.type == "image" or (c.type == "html_inline" and re.match(r"<video\b", c.content, re.I))
        for c in inline.children or []
    )


def display_width(text):
    return sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in text.strip())


def table_width(tokens, start):
    """Formatted row width of the table opening at tokens[start]: widest cell per column plus pipes and padding."""
    widths, col, j = [], 0, start
    while tokens[j].type != "table_close":
        if tokens[j].type == "tr_open":
            col = 0
        elif tokens[j].type == "inline":
            widths.extend([0] * (col + 1 - len(widths)))
            widths[col] = max(widths[col], display_width(tokens[j].content))
            col += 1
        j += 1
    return sum(max(w, 3) for w in widths) + 3 * len(widths) + 1


def line_of(tok, fallback):
    return tok.map[0] + 1 if tok.map else fallback


def check_controlled_contract(path, text, tokens, add):
    doc_type = detect_doc_type(path)
    if doc_type not in ("rd-review", "acceptance"):
        return

    lines = text.splitlines()
    code_lines = set()
    h2_titles = []
    first_h2_idx = len(lines)
    for idx, tok in enumerate(tokens):
        if tok.type in ("fence", "code_block") and tok.map:
            code_lines.update(range(tok.map[0], tok.map[1]))
        elif tok.type == "heading_open" and tok.tag == "h2":
            if first_h2_idx == len(lines) and tok.map:
                first_h2_idx = tok.map[0]
            h2_titles.append(tokens[idx + 1].content.strip())

    meta = {}
    dup_keys = []
    for idx in range(first_h2_idx):
        if idx in code_lines:
            continue
        s = lines[idx].strip()
        if not s or s.startswith("#"):
            continue
        m = META_KV_RE.match(s)
        if m:
            k, v = m.group(1).strip(), m.group(2).strip()
            if k in meta:
                dup_keys.append(k)
            meta[k] = v

    if doc_type == "rd-review":
        if not RD_REVIEW_NAME_RE.match(path.name):
            add(1, "E", "rd-review-name", f"invalid rd-review filename in controlled directory: {path.name}")
        missing = [k for k in RD_REVIEW_REQUIRED_META if not meta.get(k)]
        if missing or dup_keys:
            add(1, "E", "rd-review-meta", f"missing={missing} duplicate={dup_keys}")
        elif meta.get("评审结论") not in RD_REVIEW_STATUSES:
            add(1, "E", "rd-review-meta", f"invalid 评审结论: {meta.get('评审结论')!r}")
        if not chapters_match(h2_titles, RD_REVIEW_CHAPTERS, RD_REVIEW_LEGACY_CHAPTERS):
            add(1, "E", "rd-review-chapters", f"expected 5 fixed chapters {RD_REVIEW_CHAPTERS}, got {tuple(h2_titles)}")
        if not re.search(r"未闭环\s*HIGH/MEDIUM\s*[：:]\s*\d+", text):
            add(1, "E", "rd-review-summary", "missing '未闭环 HIGH/MEDIUM：<整数>' summary line in chapter 5")
    elif doc_type == "acceptance":
        if not ACCEPTANCE_NAME_RE.match(path.name):
            add(1, "E", "acceptance-name", f"invalid acceptance filename in controlled directory: {path.name}")
        missing = [k for k in ACCEPTANCE_REQUIRED_META if not meta.get(k)]
        if missing or dup_keys:
            add(1, "E", "acceptance-meta", f"missing={missing} duplicate={dup_keys}")
        elif meta.get("验收状态") not in ACCEPTANCE_STATUSES:
            add(1, "E", "acceptance-meta", f"invalid 验收状态: {meta.get('验收状态')!r}")
        if not chapters_match(h2_titles, ACCEPTANCE_CHAPTERS, ACCEPTANCE_LEGACY_CHAPTERS):
            add(1, "E", "acceptance-chapters", f"expected 5 fixed chapters {ACCEPTANCE_CHAPTERS}, got {tuple(h2_titles)}")


def check_file(path, md=None):
    md = md or parser()
    path = Path(path)
    raw_bytes = path.read_bytes()
    text = raw_bytes.decode("utf-8")
    tokens = md.parse(text)
    issues = []
    own_anchors = anchors(tokens)
    anchor_cache = {}

    def add(line, level, code, message):
        issues.append((line, level, code, message))

    if b"\r" in raw_bytes:
        first_cr_line = raw_bytes.split(b"\r", 1)[0].count(b"\n") + 1
        add(first_cr_line, "E", "eol-crlf", "file contains CR/CRLF line endings; use LF (\\n) across all OSes")

    check_controlled_contract(path, text, tokens, add)

    depth, line, in_table = 0, 1, False
    numbered = None
    counters = []
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        line = line_of(tok, line)
        if tok.type in ("bullet_list_open", "ordered_list_open"):
            depth += 1
            if depth > MAX_LIST_DEPTH:
                add(line, "E", "list-depth", f"list nested {depth} levels (max {MAX_LIST_DEPTH}); promote parent to heading")
        elif tok.type in ("bullet_list_close", "ordered_list_close"):
            depth -= 1
        elif tok.type in ("fence", "code_block") and depth:
            add(line, "W", "block-in-list", "code block inside list item; keep only for required templates")
        elif tok.type == "table_close":
            in_table = False
        elif tok.type == "table_open":
            in_table = True
            if depth:
                add(line, "W", "block-in-list", "table inside list item")
            columns, media, j = 0, 0, i + 1
            while tokens[j].type != "tr_close":
                columns += tokens[j].type == "th_open"
                media += tokens[j].type == "inline" and is_media_cell(tokens[j])
                j += 1
            if media < columns:
                if columns > MAX_TABLE_COLUMNS:
                    add(line, "E", "table-columns", f"table has {columns} columns (max {MAX_TABLE_COLUMNS})")
                width = table_width(tokens, i)
                if width > MAX_TABLE_WIDTH:
                    add(
                        line,
                        "E",
                        "table-width",
                        f"formatted row is {width} display columns (max {MAX_TABLE_WIDTH}); shorten cells and move explanations below the table",
                    )
        elif tok.type == "heading_open":
            level = int(tok.tag[1])
            title = tokens[i + 1].content.strip()
            if level == 1:
                if re.match(r"^\d", title):
                    add(line, "E", "heading-number", "level-1 heading must not be numbered")
            else:
                if numbered is None and level == 2:
                    numbered = bool(H2_NUMBER.match(title))
                if numbered:
                    while len(counters) < level - 1:
                        counters.append(0)
                    del counters[level - 1 :]
                    counters[level - 2] += 1
                    if 0 in counters:
                        add(line, "E", "heading-number", "heading level skipped")
                    else:
                        expected = ".".join(map(str, counters)) + ("." if level == 2 else "")
                        if not title.startswith(expected + " "):
                            add(line, "E", "heading-number", f"expected '{expected} ...', got '{title}'")
        elif tok.type == "inline":
            for child in tok.children or []:
                if child.type == "text" and LOCAL_PATH.search(child.content):
                    add(line, "E", "local-path", "local absolute path or file:// in text")
                if in_table and child.type == "html_inline" and re.match(r"<br\s*/?>", child.content, re.I):
                    add(line, "E", "table-br", "<br> in table cell; move long content below the table")
                target = (
                    child.attrGet("href")
                    if child.type == "link_open"
                    else child.attrGet("src")
                    if child.type == "image"
                    else None
                )
                if target:
                    check_target(path, target, line, own_anchors, anchor_cache, md, add)
        i += 1
    return sorted(issues)


def check_target(path, target, line, own_anchors, cache, md, add):
    if LOCAL_PATH.match(target):
        add(line, "E", "local-path", f"link to local absolute path: {target}")
        return
    if SCHEME.match(target):
        return
    raw, _, fragment = target.partition("#")
    fragment = unquote(fragment)
    if not raw:
        if fragment and fragment not in own_anchors:
            add(line, "W", "anchor", f"anchor not found: #{fragment}")
        return
    dest = (path.parent / unquote(raw)).resolve()
    if not dest.exists():
        add(line, "E", "broken-link", f"target not found: {raw}")
        return
    if fragment and dest.suffix.lower() == ".md" and not re.match(r"^L\d+", fragment):
        if dest not in cache:
            cache[dest] = anchors(md.parse(dest.read_text(encoding="utf-8")))
        if fragment not in cache[dest]:
            add(line, "W", "anchor", f"anchor not found: {raw}#{fragment}")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("files", nargs="+")
    args = ap.parse_args(argv)
    md = parser()
    errors = warnings = 0
    for name in args.files:
        for line, level, code, message in check_file(name, md):
            print(f"{name}:{line}: {level} {code}: {message}")
            errors += level == "E"
            warnings += level == "W"
    print(f"{'FAIL' if errors else 'PASS'}: {errors} errors, {warnings} warnings")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
