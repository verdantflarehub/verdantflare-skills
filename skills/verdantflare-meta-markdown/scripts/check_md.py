"""Measure Markdown structure: list depth, blocks in lists, table columns and width, heading numbers, links, local paths.

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


def parser():
    return MarkdownIt("commonmark").enable("table")


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
    return any(c.type == "image" or (c.type == "html_inline" and re.match(r"<video\b", c.content, re.I))
               for c in inline.children or [])


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
                    add(line, "E", "table-width", f"formatted row is {width} display columns "
                        f"(max {MAX_TABLE_WIDTH}); shorten cells and move explanations below the table")
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
                    del counters[level - 1:]
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
                target = child.attrGet("href") if child.type == "link_open" else \
                    child.attrGet("src") if child.type == "image" else None
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
