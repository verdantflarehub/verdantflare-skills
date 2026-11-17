#!/usr/bin/env python3
"""AST-based R&D plan classifier, scaffolder, deterministic hasher, and 5-gate checker."""
from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from datetime import date
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from urllib.parse import unquote, urlsplit

from markdown_it import MarkdownIt

MD = MarkdownIt("commonmark").enable("table")
MD.validateLink = lambda target: True

SLUG_RE = re.compile(r"^[a-z0-9]+(?:[.-][a-z0-9]+)*$")
REVIEW_NAME_RE = re.compile(r"^review_\d{8}_[a-z0-9.-]+_(t1|t2|t4)(?:-r([2-9]|[1-9]\d+))?\.md$")
ACCEPT_NAME_RE = re.compile(r"^accept_\d{8}_[a-z0-9.-]+\.md$")
GDRIVE_IMG_RE = re.compile(
    r"(?:^|/)\.gdrive/managed/(approved|pending|references)/(.+/[^/]+--([0-9a-f]{64})\.(png|jpg|jpeg|webp))$",
    re.IGNORECASE,
)
GDRIVE_BUCKET_RE = re.compile(r"\.gdrive/managed/(?:pending|approved|references)/")
HEADER_KV_RE = re.compile(r"^(?:-\s+)?(?:\*\*)?([^：:*\n#`]+?)(?:\*\*)?[：:]\s*(.*)$")
TASK_RE = re.compile(r"^-\s+\[([ xX])\]\s+(T\d+(?:\.\d+)?(?:-r\d+)?)[：:]\s*(.+)$")
PROGRESS_SUMMARY_RE = re.compile(r"完成\s*(?:\*\*)?(\d+)\s*/\s*(\d+)\s*项")
STAGE_COUNT_RE = re.compile(r"[（(](\d+)\s*/\s*(\d+)[）)]")
LOCK_HASH_IN_TEXT_RE = re.compile(r"sha256:([0-9a-f]{64})")
SHA256_HEX_RE = re.compile(r"\b[0-9a-f]{64}\b")

COMPLEX_STAGES = (
    ("T1", "完善计划与方案"),
    ("T2", "分析影响范围"),
    ("T3", "实施子计划"),
    ("T4", "验收子计划"),
    ("T5", "人类验收子计划"),
)
VALID_REVIEW_STATUS = {"待修订", "复评中", "已通过", "已废弃"}
VALID_ACCEPT_STATUS = {"考卷草案", "考卷已锁定", "实跑取证中", "智能体已验真", "CP1已通过", "已废弃"}
KNOWN_AGENTS = ("Claude", "Codex", "Gemini", "Grok")
KNOWN_AGENTS_SET = set(KNOWN_AGENTS)
RD_AGENTS = {"Codex", "Gemini", "Grok"}
AGENT_MCP_CHANNELS = {
    "Claude": "verdantflare-agent-claude",
    "Codex": "verdantflare-agent-codex",
    "Gemini": "verdantflare-agent-antigravity",
    "Grok": "verdantflare-agent-grok",
}
PRUNE_WALK_DIRS = {
    ".git",
    ".tmp",
    ".gdrive",
    ".output",
    "node_modules",
    "__pycache__",
    ".venv",
    "venv",
    ".pytest_cache",
    ".mypy_cache",
}
APPROVAL_NEGATION_RE = re.compile(
    r"不同意|不予同意|不批准|不予批准|不通过|不予通过|不核签|不予核签|未批准|未通过|未同意|未核签|"
    r"尚未|暂不|暂缓|暂停|搁置|延期|推迟|拒绝|否决|驳回|退回|打回|撤回|撤销|作废|失效|"
    r"待定|待审批|待批准|待通过|待核签|待复议|仅记录|候选摘要|仅作候选|草拟待批|假定批准|拟批准|拟通过"
)
APPROVAL_AFFIRM_RE = re.compile(r"(?<![不未勿莫非无免难待拟缓])(?:批准|通过|核签|同意)")
ALL_RECEIPT_KV_RE = re.compile(r"(?:session_id|thread_id|task_id|turn_id)\s*[=:：]\s*`?([A-Za-z0-9._-]+)`?")

NEGATION_SPAN_RE = re.compile(
    r"(?:不涉及|无需|不需要|不修改|不含|免除|不触及|不改动|不新建|不破坏|不改变|无|非|未涉及)[^，。！？；,\n]{0,26}"
)
AUTHOR_ACTION_RE = re.compile(r"编制|实现|执行|修改|新建|升级|开发|编码|扫描|重制|取证|实跑|制作|起草|生成|同步|统筹|落地")
REVIEW_ONLY_RE = re.compile(r"评审|复核|审计|验真|主审|终审|核验|裁定|裁决")


@dataclass
class TaskItem:
    task_id: str
    stage_index: int
    checked: bool
    title: str
    line: int
    doc_links: list[str] = field(default_factory=list)
    progress_links: list[str] = field(default_factory=list)
    progress_raw: str = ""


@dataclass
class HeadingItem:
    level: int
    title: str
    line: int
    end_line: int


@dataclass
class ParsedDoc:
    path: Path
    text: str
    lines: list[str]
    h1: str
    header_fields: dict[str, str]
    duplicate_keys: list[str]
    headings: list[HeadingItem]
    fences: list[tuple[str, str, int]]
    links: list[tuple[str, int]]
    images: list[tuple[str, int]]
    tasks: list[TaskItem]
    code_lines: set[int] = field(default_factory=set)


@dataclass
class Issue:
    code: str
    message: str
    path: str = ""
    line: int = 0


def canon_lines(text: str) -> str:
    s = text.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    return "\n".join(line.rstrip(" \t") for line in s.split("\n")).strip("\n")


def normalize_gdrive_buckets(text: str) -> str:
    return GDRIVE_BUCKET_RE.sub(".gdrive/managed/<bucket>/", text)


def inline_plain(token) -> str:
    return "".join(c.content for c in (token.children or []) if c.type in ("text", "code_inline", "image"))


def extract_md_links(line: str) -> list[str]:
    cleaned = re.sub(r"(`+).+?\1", "", line)
    results = []
    for raw in re.findall(r"\]\(([^)]+)\)", cleaned):
        parts = urlsplit(raw.strip().strip("<>"))
        if not parts.scheme and parts.path:
            results.append(unquote(parts.path))
    return results


def parse_doc_text(raw: str, path: Path) -> ParsedDoc:
    norm = raw.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    lines = norm.split("\n")
    tokens = MD.parse(norm)

    code_line_set: set[int] = set()
    fences: list[tuple[str, str, int]] = []
    links: list[tuple[str, int]] = []
    images: list[tuple[str, int]] = []
    raw_headings: list[tuple[int, str, int]] = []
    h1 = ""

    for idx, tok in enumerate(tokens):
        if tok.type in ("fence", "code_block") and tok.map:
            for ln in range(tok.map[0], tok.map[1]):
                code_line_set.add(ln)
            if tok.type == "fence":
                fences.append((tok.info.strip(), tok.content, tok.map[0] + 1))
        elif tok.type == "heading_open":
            lvl = int(tok.tag[1:])
            ln = (tok.map[0] + 1) if tok.map else 1
            title = inline_plain(tokens[idx + 1]).strip()
            if lvl == 1 and not h1:
                h1 = title
            raw_headings.append((lvl, title, ln))
        elif tok.type == "inline":
            ln = (tok.map[0] + 1) if tok.map else 1
            for child in tok.children or []:
                if child.type == "link_open":
                    href = child.attrGet("href") or ""
                    if href:
                        links.append((href, ln))
                elif child.type == "image":
                    src = child.attrGet("src") or ""
                    if src:
                        images.append((src, ln))

    headings: list[HeadingItem] = []
    for i, (lvl, title, start_ln) in enumerate(raw_headings):
        end_ln = len(lines)
        for next_lvl, _, next_ln in raw_headings[i + 1 :]:
            if next_lvl <= lvl:
                end_ln = next_ln - 1
                break
        headings.append(HeadingItem(level=lvl, title=title, line=start_ln, end_line=end_ln))

    first_h2_idx = len(lines)
    for h in headings:
        if h.level == 2:
            first_h2_idx = h.line - 1
            break

    header_fields: dict[str, str] = {}
    duplicate_keys: list[str] = []
    for idx in range(first_h2_idx):
        if idx in code_line_set:
            continue
        stripped = lines[idx].strip()
        if not stripped or stripped.startswith("#"):
            continue
        m = HEADER_KV_RE.match(stripped)
        if m:
            key = m.group(1).strip()
            val = m.group(2).strip()
            if key in header_fields:
                duplicate_keys.append(key)
            header_fields[key] = val

    tasks: list[TaskItem] = []
    ch5 = next((h for h in headings if h.level == 2 and h.title.startswith("5.")), None)
    if ch5:
        current_stage = 0
        current_task: TaskItem | None = None
        for idx in range(ch5.line, ch5.end_line):
            if idx in code_line_set:
                continue
            raw_ln = lines[idx]
            s_ln = raw_ln.strip()
            h3_m = re.match(r"^###\s+5\.(\d+)\b", s_ln)
            if h3_m:
                current_stage = int(h3_m.group(1))
                current_task = None
                continue
            t_m = TASK_RE.match(s_ln)
            if t_m and not raw_ln.startswith("  "):
                current_task = TaskItem(
                    task_id=t_m.group(2),
                    stage_index=current_stage,
                    checked=(t_m.group(1).lower() == "x"),
                    title=t_m.group(3).strip(),
                    line=idx + 1,
                )
                tasks.append(current_task)
                continue
            if current_task and raw_ln.startswith("  - "):
                sub = raw_ln.strip()[2:].strip()
                if sub.startswith("关联文档：") or sub.startswith("关联文档:"):
                    current_task.doc_links.extend(extract_md_links(sub))
                elif sub.startswith("关联进展：") or sub.startswith("关联进展:"):
                    current_task.progress_raw = sub.split("：", 1)[-1].split(":", 1)[-1].strip()
                    current_task.progress_links.extend(extract_md_links(sub))

    return ParsedDoc(
        path=path,
        text=norm,
        lines=lines,
        h1=h1,
        header_fields=header_fields,
        duplicate_keys=duplicate_keys,
        headings=headings,
        fences=fences,
        links=links,
        images=images,
        tasks=tasks,
        code_lines=code_line_set,
    )


def parse_doc(path: Path) -> ParsedDoc:
    return parse_doc_text(path.read_text(encoding="utf-8"), path)


def section_slice(doc: ParsedDoc, level: int, prefix: str, *, skip_fences: bool = False) -> str:
    for h in doc.headings:
        if h.level == level and h.title.startswith(prefix):
            if not skip_fences:
                return "\n".join(doc.lines[h.line : h.end_line])
            return "\n".join(
                doc.lines[idx]
                for idx in range(h.line, h.end_line)
                if idx not in doc.code_lines
            )
    return ""


def _validate_accept_structure_for_hash(
    doc: ParsedDoc,
) -> tuple[HeadingItem, HeadingItem, HeadingItem, HeadingItem, HeadingItem | None]:
    h2_list = [h for h in doc.headings if h.level == 2]
    if any(h.level == 1 and h2_list and h.line > h2_list[0].line for h in doc.headings):
        raise ValueError(f"{doc.path}: unexpected H1 after H2 chapters")
    prefixes = []
    for h in h2_list:
        m = re.match(r"^(\d+\.)\s+", h.title)
        prefixes.append(m.group(1) if m else "")
    if prefixes != ["1.", "2.", "3.", "4.", "5."]:
        raise ValueError(
            f"{doc.path}: H2 chapters must be unique and strictly ordered 1..5 (got {prefixes})"
        )
    _, h2_2, h2_3, h2_4, h2_5 = h2_list

    ch2_sub_nums: list[int] = []
    ch2_scen_ids: set[str] = set()
    for h in doc.headings:
        if h.level == 3 and h2_2.line < h.line <= h2_2.end_line:
            m2 = re.match(r"^2\.(\d+)\s+(?:(S\d+)\b)?", h.title)
            if not m2:
                raise ValueError(f"{doc.path}: invalid H3 inside Chapter 2: {h.title!r}")
            sub_n = int(m2.group(1))
            if ch2_sub_nums and sub_n <= ch2_sub_nums[-1]:
                raise ValueError(f"{doc.path}: duplicate or out-of-order Chapter 2 H3: {h.title!r}")
            ch2_sub_nums.append(sub_n)
            if m2.group(2):
                sid = m2.group(2)
                if sid in ch2_scen_ids:
                    raise ValueError(f"{doc.path}: duplicate scenario ID {sid} in Chapter 2")
                ch2_scen_ids.add(sid)

    for h in doc.headings:
        if h.level == 3 and not (h2_4.line < h.line <= h2_4.end_line) and re.match(r"^4\.\d+\b", h.title):
            raise ValueError(f"{doc.path}: rogue 4.x H3 outside Chapter 4 at line {h.line}: {h.title!r}")

    ch4_h3s = [h for h in doc.headings if h.level == 3 and h2_4.line < h.line <= h2_4.end_line]
    ch4_nums: list[int] = []
    h45: HeadingItem | None = None
    for h in ch4_h3s:
        m4 = re.match(r"^4\.([1-5])\b", h.title)
        if not m4:
            raise ValueError(f"{doc.path}: invalid H3 inside Chapter 4: {h.title!r}")
        num = int(m4.group(1))
        if ch4_nums and num <= ch4_nums[-1]:
            raise ValueError(f"{doc.path}: duplicate or out-of-order Chapter 4 H3: {h.title!r}")
        ch4_nums.append(num)
        if num == 5:
            h45 = h
    if h45 is not None and ch4_h3s[-1] is not h45:
        raise ValueError(f"{doc.path}: 4.5 must be the final H3 inside Chapter 4")
    return h2_2, h2_3, h2_4, h2_5, h45


def compute_lock_hash(accept_path: Path) -> tuple[str, int, str]:
    doc = parse_doc(accept_path)
    h2_2, h2_3, _, _, _ = _validate_accept_structure_for_hash(doc)
    slice_lines = doc.lines[h2_2.line : h2_3.line - 1]
    canon = canon_lines("\n".join(slice_lines))
    digest = "sha256:" + hashlib.sha256(canon.encode("utf-8")).hexdigest()
    scenarios = sum(
        1
        for h in doc.headings
        if h.level == 3 and h2_2.line < h.line <= h2_2.end_line and re.match(r"^2\.\d+\s+S\d+\b", h.title)
    )
    return digest, scenarios, canon


PROC_RECORD_LABELS = ("执行者起草", "研发核验修订", "Claude 修订")
PROC_SCENARIO_LABELS = ("取证流程", "证据内容", "充分性判据", "判定")


def check_acceptance_procedure(acc_doc: "ParsedDoc", issues: list) -> None:
    """PL-CP0-PROC: Chapter 2 must carry the three-step procedure review record and the four elements per scenario."""
    label = acc_doc.path.as_posix()
    h2s = [h for h in acc_doc.headings if h.level == 2]
    ch2 = next((h for h in h2s if re.match(r"^2\.\s+", h.title)), None)
    if ch2 is None:
        return
    scen_heads = [
        h for h in acc_doc.headings if h.level == 3 and ch2.line < h.line <= ch2.end_line and re.match(r"^2\.\d+\s+S\d+\b", h.title)
    ]
    intro_end = scen_heads[0].line - 1 if scen_heads else ch2.end_line
    intro = "\n".join(acc_doc.lines[ch2.line : intro_end])
    missing_rec = [k for k in PROC_RECORD_LABELS if not re.search(r"\*\*" + re.escape(k) + r"\*\*\s*[：:]", intro)]
    if missing_rec:
        issues.append(
            Issue("PL-CP0-PROC", f"考卷第 2 章开头缺验收流程审定记录项 {missing_rec}（执行者起草 → 研发核验修订 → Claude 修订）", label, ch2.line)
        )
    for idx, h in enumerate(scen_heads):
        end = scen_heads[idx + 1].line - 1 if idx + 1 < len(scen_heads) else ch2.end_line
        body = "\n".join(acc_doc.lines[h.line : end])
        missing = [k for k in PROC_SCENARIO_LABELS if not re.search(r"\*\*" + re.escape(k) + r"\*\*\s*[：:]", body)]
        if missing:
            issues.append(Issue("PL-CP0-PROC", f"考卷场景 '{h.title}' 缺验收流程要素 {missing}", label, h.line))


def compute_review_hash(stage: str, accept_path: Path, design_path: Path | None = None) -> str:
    base_stage = stage.lower().split("-r")[0]
    if base_stage in ("t1", "t2"):
        if design_path is None or not design_path.is_file():
            raise ValueError("review-hash for t1/t2 requires an existing --design file")
        d_doc = parse_doc(design_path)
        d_h2s = [h for h in d_doc.headings if h.level == 2]
        if not d_h2s or not re.match(r"^1\.\s+", d_h2s[0].title):
            raise ValueError(f"{design_path}: missing leading '## 1. ' heading")
        d_nums: list[int] = []
        for h in d_h2s:
            md = re.match(r"^(\d+)\.\s+", h.title)
            if md:
                n = int(md.group(1))
                if d_nums and n <= d_nums[-1]:
                    raise ValueError(f"{design_path}: duplicate or out-of-order H2 heading: {h.title!r}")
                d_nums.append(n)
        id1 = d_h2s[0].line - 1
        d_norm = normalize_gdrive_buckets("\n".join(d_doc.lines[id1:]))
        d_canon = canon_lines(d_norm)
        _, _, ch2_canon = compute_lock_hash(accept_path)
        combined = d_canon + "\n===ACCEPT_CH2===\n" + normalize_gdrive_buckets(ch2_canon)
        return "sha256:" + hashlib.sha256(combined.encode("utf-8")).hexdigest()
    if base_stage == "t4":
        a_doc = parse_doc(accept_path)
        h2_2, _, _, h2_5, h45 = _validate_accept_structure_for_hash(a_doc)
        i2 = h2_2.line - 1
        iend = (h45.line - 1) if h45 is not None else (h2_5.line - 1)
        t4_canon = canon_lines(normalize_gdrive_buckets("\n".join(a_doc.lines[i2:iend])))
        return "sha256:" + hashlib.sha256(t4_canon.encode("utf-8")).hexdigest()
    raise ValueError(f"Unsupported review stage: {stage}")


def strip_fences(text: str) -> str:
    out, in_fence = [], False
    for ln in text.splitlines():
        if ln.lstrip().startswith(("```", "~~~")):
            in_fence = not in_fence
            continue
        if not in_fence:
            out.append(ln)
    return "\n".join(out)


def classify_content(text: str, *, plan_doc: ParsedDoc | None = None, ignore_header_level: bool = False) -> dict:
    clean = strip_fences(text)
    if ignore_header_level:
        clean = re.sub(r"^(?:-\s+)?(?:\*\*)?计划等级(?:\*\*)?[：:].*$", "", clean, flags=re.MULTILINE)

    clauses = re.split(r"[。！？；.!?;，,\n]+", clean)
    positive_clauses = [NEGATION_SPAN_RE.sub("", c) for c in clauses]
    joined = " | ".join(positive_clauses)

    score = 0
    matched: list[str] = []

    if not ignore_header_level and plan_doc and plan_doc.header_fields.get("计划等级") == "复杂研发计划":
        score += 100
        matched.append("header_complex_level")
    elif re.search(r"复杂研发计划|按复杂计划管控|标记为复杂计划", joined):
        score += 100
        matched.append("explicit_complex_mandate")

    ext_repos = set()
    if plan_doc:
        for href, _ in plan_doc.links:
            p = unquote(urlsplit(href).path)
            m = re.match(r"^(?:\.\./){2,}([a-zA-Z0-9._-]+)/", p)
            if m and m.group(1) not in {"docs", "agents", "scripts", ".gdrive"}:
                ext_repos.add(m.group(1))
    if (
        len(ext_repos) >= 2
        or re.search(r"跨仓|跨\s*\d+\s*个?(?:代码)?仓|多仓|多微服务", joined)
        or (re.search(r"新建(?:独立|完整)?技能", joined) and re.search(r"全局提示词", joined))
    ):
        score += 60
        matched.append("cross_repo_or_multidomain")

    if re.search(
        r"公共契约|docs/design/contracts/|状态机变更|并发(?:锁|仲裁)状态机|数据库\s*(?:Schema|表结构|迁移)|持久化数据模型|安全权限边界",
        joined,
    ):
        score += 60
        matched.append("contract_or_statemachine")

    if re.search(r"GPU\s*显存调度|跨宿主多进程|反作弊验真|集群滚动(?:状态)?切换", joined):
        score += 50
        matched.append("high_risk_anti_cheat")

    if plan_doc:
        cp_count = count_plan_checkpoints(plan_doc)
        if cp_count >= 2:
            score += 60
            matched.append("multiple_human_checkpoints")

    if score >= 50:
        level = "复杂研发计划"
        mode = "C2.complex"
    else:
        level = "简单计划"
        mode = "S1" if re.search(r"凭据|口令|人工确认|桌面体验|1\s*CP|S1-CP", joined) else "S0"

    return {
        "level": level,
        "recommended_mode": mode,
        "score": score,
        "matched_rules": matched,
    }


def count_plan_checkpoints(doc: ParsedDoc) -> int:
    h32 = next((h for h in doc.headings if h.level == 3 and h.title.startswith("3.2")), None)
    if not h32:
        return 0
    m = re.search(r"[（(](\d+)\s*个[）)]", h32.title)
    declared = int(m.group(1)) if m else 0
    sub_h4 = sum(1 for h in doc.headings if h.level == 4 and h32.line < h.line <= h32.end_line)
    return max(declared, sub_h4)


def rel_display(path: Path, repo_root: Path | None = None) -> str:
    try:
        if repo_root is not None:
            return path.resolve().relative_to(repo_root.resolve()).as_posix()
    except ValueError:
        pass
    home = Path.home().resolve()
    try:
        sub = path.resolve().relative_to(home).as_posix()
        return f"%USERPROFILE%/{sub}"
    except ValueError:
        return path.name


def infer_repo_root(plan_path: Path) -> Path:
    resolved = plan_path.resolve()
    parts = resolved.parts
    if len(parts) >= 4 and parts[-3:] == ("docs", "plan", resolved.name):
        return resolved.parents[2]
    if len(parts) >= 5 and parts[-4:] == ("docs", "archive", "plan", resolved.name):
        return resolved.parents[3]
    for parent in resolved.parents:
        if (parent / "docs" / "plan").is_dir() or (parent / ".git").exists():
            return parent
    return resolved.parent


def detect_git_repo_id(repo_root: Path) -> str:
    if not (repo_root / ".git").exists():
        return ""
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo_root), "remote", "get-url", "origin"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=5,
            check=False,
        )
    except OSError:
        return ""
    url = proc.stdout.strip()
    m = re.search(r"[:/]([^/:]+/[^/]+?)(?:\.git)?$", url)
    return m.group(1) if m else ""


def git_ever_had_plan_level(repo_root: Path, rel_posix: str) -> tuple[bool | None, str]:
    if not (repo_root / ".git").exists():
        return None, "目标仓库缺少 .git 元数据，无法核验存量计划双棘轮 Git 历史"
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo_root), "log", "-S", "计划等级：", "--oneline", "--", rel_posix],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=8,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return None, f"Git 历史核验执行异常: {type(exc).__name__}"
    if proc.returncode != 0:
        return None, f"Git 历史查询失败 (exit={proc.returncode})"
    return bool(proc.stdout.strip()), ""


def iter_repo_markdown_files(repo_root: Path) -> list[Path]:
    root_res = repo_root.resolve()
    found: list[Path] = []
    for cur_dir, dirnames, filenames in os.walk(root_res, topdown=True, followlinks=False):
        cur_path = Path(cur_dir)
        kept_dirs: list[str] = []
        for d in sorted(dirnames):
            if d in PRUNE_WALK_DIRS:
                continue
            sub_p = cur_path / d
            if sub_p.is_symlink():
                continue
            if cur_path != root_res and (sub_p / ".git").exists():
                continue
            kept_dirs.append(d)
        dirnames[:] = kept_dirs
        for fn in sorted(filenames):
            if not fn.lower().endswith(".md"):
                continue
            fp = cur_path / fn
            if fp.is_symlink():
                continue
            found.append(fp)
    return found


def git_earliest_commit_date(repo_root: Path, rel_posix: str) -> str:
    if not (repo_root / ".git").exists():
        return ""
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo_root), "log", "--diff-filter=A", "--follow", "--format=%cs", "--", rel_posix],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=8,
            check=False,
        )
    except OSError:
        return ""
    dates = [ln.strip() for ln in proc.stdout.splitlines() if re.match(r"^\d{4}-\d{2}-\d{2}$", ln.strip())]
    return dates[-1] if dates else ""


def check_no_symlink_escape(root: Path, target: Path) -> None:
    root_abs = Path(root).absolute()
    if root_abs.is_symlink():
        raise ValueError(f"Root is a symlink: {root_abs}")
    root_res = root_abs.resolve()
    rel = target.absolute().relative_to(root_abs)
    cur = root_abs
    for part in rel.parts:
        cur = cur / part
        if cur.exists() and cur.is_symlink():
            raise ValueError(f"Symlink component rejected: {cur}")
        if cur.is_symlink():
            raise ValueError(f"Broken symlink component rejected: {cur}")
    if not target.resolve().is_relative_to(root_res):
        raise ValueError(f"Target escapes root: {target}")


def discover_linked_docs(
    repo_root: Path, plan_doc: ParsedDoc, archived: bool = False
) -> tuple[Path | None, list[ParsedDoc], list[ParsedDoc]]:
    plan_res = plan_doc.path.resolve()
    design_path: Path | None = None
    for href, _ in plan_doc.links:
        u = urlsplit(href.strip())
        if u.scheme or not u.path:
            continue
        cand = (plan_doc.path.parent / unquote(u.path)).resolve()
        if cand.suffix.lower() != ".md" or cand.name in {"AGENTS.md", "README.md"}:
            continue
        try:
            rel = cand.relative_to(repo_root.resolve()).as_posix()
        except ValueError:
            continue
        if rel.startswith("docs/design/") or rel.startswith("docs/archive/design/"):
            design_path = cand
            break

    rev_dir = repo_root / ("docs/archive/reviews" if archived else "docs/reviews")
    acc_dir = repo_root / ("docs/archive/acceptance" if archived else "docs/acceptance")
    reviews: list[ParsedDoc] = []
    accepts: list[ParsedDoc] = []

    if rev_dir.is_dir():
        for f in sorted(rev_dir.glob("*.md")):
            if f.name in {"AGENTS.md", "README.md"}:
                continue
            d = parse_doc(f)
            raw_link = d.header_fields.get("关联计划", "")
            for lk in extract_md_links(raw_link):
                if (f.parent / lk).resolve() == plan_res:
                    reviews.append(d)
                    break

    if acc_dir.is_dir():
        for f in sorted(acc_dir.glob("*.md")):
            if f.name in {"AGENTS.md", "README.md"}:
                continue
            d = parse_doc(f)
            raw_link = d.header_fields.get("关联计划", "")
            for lk in extract_md_links(raw_link):
                if (f.parent / lk).resolve() == plan_res:
                    accepts.append(d)
                    break

    return design_path, reviews, accepts


def effective_reviews_by_stage(reviews: list[ParsedDoc]) -> dict[str, ParsedDoc]:
    best: dict[str, tuple[int, ParsedDoc]] = {}
    for d in reviews:
        m = REVIEW_NAME_RE.match(d.path.name)
        if not m:
            continue
        stg = m.group(1)
        rev_num = int(m.group(2)) if m.group(2) else 1
        if stg not in best or rev_num >= best[stg][0]:
            best[stg] = (rev_num, d)
    return {k: v[1] for k, v in best.items()}


def parse_agent_roles(raw_field: str) -> list[tuple[str, str]]:
    chunks: list[str] = []
    buf: list[str] = []
    depth = 0
    for ch in raw_field:
        if ch in "（(":
            depth += 1
            buf.append(ch)
        elif ch in "）)":
            depth = max(0, depth - 1)
            buf.append(ch)
        elif depth == 0 and ch in "、,，;；":
            chunks.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    if buf:
        chunks.append("".join(buf))

    items: list[tuple[str, str]] = []
    for chunk in chunks:
        chunk = chunk.strip()
        if not chunk or chunk in {"暂无", "无", "待登记"}:
            continue
        m = re.match(r"^([^（(\s]+)\s*(?:[（(](.*)[）)])?$", chunk)
        if m:
            raw_name = m.group(1).strip("*`")
            role = (m.group(2) or "").strip()
            norm_name = next((a for a in KNOWN_AGENTS if a.lower() == raw_name.lower()), raw_name)
            items.append((norm_name, role))
    return items


def extract_session_ids(text: str) -> set[str]:
    return set(
        m.group(1)
        for m in re.finditer(r"(?:session_id|thread_id)\s*[=:：]\s*`?([A-Za-z0-9._-]+)`?", text)
    )


def extract_receipt_ids(text: str) -> set[str]:
    return set(
        m.group(1)
        for m in re.finditer(r"(?:session_id|thread_id|task_id)\s*[=:：]\s*`?([A-Za-z0-9._-]+)`?", text)
    )


def extract_turn_ids(text: str) -> set[str]:
    return set(m.group(1) for m in re.finditer(r"turn_id\s*[=:：]\s*`?([A-Za-z0-9._-]+)`?", text))


def has_full_receipt(text: str) -> bool:
    has_sess = bool(re.search(r"(?:session_id|thread_id)\s*[=:：]\s*`?[A-Za-z0-9._-]+`?", text))
    has_task = bool(re.search(r"task_id\s*[=:：]\s*`?[A-Za-z0-9._-]+`?", text))
    has_turn = bool(re.search(r"turn_id\s*[=:：]\s*`?[A-Za-z0-9._-]+`?", text))
    return has_sess and has_task and has_turn


def _norm_agent_name(raw_name: str) -> str:
    return next((a for a in KNOWN_AGENTS if a.lower() == raw_name.lower()), raw_name)


def extract_section_claimed_agents(section_text: str) -> set[str]:
    claimed: set[str] = set()
    for agent, channel in AGENT_MCP_CHANNELS.items():
        if channel in section_text:
            claimed.add(agent)
    for ln in section_text.splitlines():
        has_rec_key = bool(re.search(r"(?:session_id|thread_id|task_id|turn_id)\s*[=:：]", ln))
        if has_rec_key:
            for m in re.finditer(r"\b(Claude|Codex|Gemini|Grok)\b", ln, re.IGNORECASE):
                claimed.add(_norm_agent_name(m.group(1)))
        for m in re.finditer(r"由\s*(?:\*\*)?(Claude|Codex|Gemini|Grok)\b[^\n]{0,48}担任", ln, re.IGNORECASE):
            claimed.add(_norm_agent_name(m.group(1)))
        m_lead = re.match(
            r"^\s*(?:-\s*)?(?:\*\*)?(Claude|Codex|Gemini|Grok)(?:\*\*)?\s*(?:[（(][^）)]*[）)])?\s*"
            r"(?:细节评审|防漏审|全仓防漏|反作弊|完成脚本|核验通过|基于|评审通过|复核通过|架构审计|总体架构"
            r"|全面核验|抽查|验收流程修订|修订定稿|[：:])",
            ln,
            re.IGNORECASE,
        )
        if m_lead:
            claimed.add(_norm_agent_name(m_lead.group(1)))
    return claimed


def _strip_md_link_urls(text: str) -> str:
    return re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text).replace("`", "")


def iter_ch7_human_entries(doc: ParsedDoc) -> list[tuple[str, str, str]]:
    ch7 = section_slice(doc, 2, "7.", skip_fences=True)
    if not ch7:
        return []
    entries: list[str] = []
    for ln in ch7.splitlines():
        if ln.startswith("- "):
            entries.append(ln)
        elif entries and (ln.startswith("  ") or ln.startswith("\t")) and ln.strip():
            entries[-1] += " " + ln.strip()
    out: list[tuple[str, str, str]] = []
    for item in entries:
        m = re.match(r"^-\s+(?:\*\*)?(A\d+)\s*[（(]([^）)]*)[）)](?:\*\*)?\s*[：:]\s*(.+)$", item.strip())
        if not m:
            continue
        aid, meta, body = m.group(1), m.group(2).strip(), m.group(3).strip()
        if any(re.search(rf"\b{ag}\b", meta, re.IGNORECASE) for ag in KNOWN_AGENTS):
            continue
        out.append((aid, meta, body))
    return out


def parse_ch7_human_approvals(doc: ParsedDoc) -> list[tuple[str, str, str]]:
    valid: list[tuple[str, str, str]] = []
    for aid, meta, body in iter_ch7_human_entries(doc):
        if not APPROVAL_AFFIRM_RE.search(meta) or APPROVAL_NEGATION_RE.search(meta) or "指示" in meta:
            continue
        prose = _strip_md_link_urls(body)
        if APPROVAL_NEGATION_RE.search(prose):
            continue
        valid.append((aid, meta, body))
    return valid


def extract_approved_cp0_lock_hash(doc: ParsedDoc) -> tuple[str | None, str]:
    cp0_sec = section_slice(doc, 4, "3.2.1", skip_fences=True).replace("**", "")
    if "当前结论：已通过" not in cp0_sec:
        return None, "第 3.2.1 节 CP0 当前结论尚未置为 '已通过'"
    approved_hex: str | None = None
    for _, meta, body in iter_ch7_human_entries(doc):
        prose = _strip_md_link_urls(body)
        mentions_cp0 = bool(re.search(r"\bCP0\b", prose))
        if mentions_cp0 and (
            APPROVAL_NEGATION_RE.search(meta)
            or APPROVAL_NEGATION_RE.search(prose)
            or "指示" in meta
            or re.search(r"重新提交\s*CP0", prose)
        ):
            approved_hex = None
            continue
        if (
            not APPROVAL_AFFIRM_RE.search(meta)
            or APPROVAL_NEGATION_RE.search(meta)
            or "指示" in meta
            or APPROVAL_NEGATION_RE.search(prose)
        ):
            continue
        clauses = [c.strip() for c in re.split(r"[。；;\n]+", prose) if c.strip()]
        is_affirm_cp0 = any(
            re.search(
                r"(?<![不未勿莫非无免难待拟缓])(?:正式)?(?:批准|通过|核签|同意)[^，,。；;\n]{0,16}\bCP0\b"
                r"|\bCP0\b[^，,。；;\n]{0,16}(?<![不未勿莫非无免难待拟缓])(?:已批准|已通过|批准通过|审批通过|核签通过)",
                cl,
            )
            and not re.search(r"(?:提交|呈报|上报|报请|进入)\s*CP0", cl)
            for cl in clauses
        )
        if not is_affirm_cp0:
            continue
        hashes = LOCK_HASH_IN_TEXT_RE.findall(body)
        if not hashes:
            return None, "第 7 章 CP0 批准记录缺少锁卷 sha256 摘要"
        if len(set(hashes)) != 1:
            return None, "第 7 章单条 CP0 批准记录内存在多个不一致的 sha256 摘要"
        approved_hex = hashes[0]
    if not approved_hex:
        return None, "第 7 章缺少绑定锁卷哈希的有效人类 CP0 批准记录"
    sec_hashes = LOCK_HASH_IN_TEXT_RE.findall(cp0_sec)
    if sec_hashes and any(h != approved_hex for h in sec_hashes):
        return None, "第 3.2.1 节与第 7 章 CP0 锁卷哈希不一致"
    return f"sha256:{approved_hex}", ""


def has_affirmative_cp1_approval(doc: ParsedDoc) -> bool:
    cp1_sec = section_slice(doc, 4, "3.2.2", skip_fences=True).replace("**", "")
    if "当前结论：已通过" not in cp1_sec:
        return False
    cp1_ok = False
    for _, meta, body in iter_ch7_human_entries(doc):
        prose = _strip_md_link_urls(body)
        if re.search(r"\bCP1\b", prose) and (
            APPROVAL_NEGATION_RE.search(meta)
            or APPROVAL_NEGATION_RE.search(prose)
            or "指示" in meta
            or re.search(r"重新提交\s*CP1", prose)
        ):
            cp1_ok = False
            continue
        if (
            not APPROVAL_AFFIRM_RE.search(meta)
            or APPROVAL_NEGATION_RE.search(meta)
            or "指示" in meta
            or APPROVAL_NEGATION_RE.search(prose)
        ):
            continue
        clauses = [c.strip() for c in re.split(r"[。；;\n]+", prose) if c.strip()]
        if any(
            re.search(
                r"(?<![不未勿莫非无免难待拟缓])(?:正式)?(?:批准|通过|核签|同意)[^，,。；;\n]{0,20}\bCP1\b"
                r"|\bCP1\b[^，,。；;\n]{0,20}(?<![不未勿莫非无免难待拟缓])(?:已批准|已通过|批准通过|审批通过|验收通过|双抽检通过)",
                cl,
            )
            and not re.search(r"(?:提交|呈报|上报|报请|进入)\s*CP1", cl)
            for cl in clauses
        ):
            cp1_ok = True
    return cp1_ok


def has_affirmative_s1_approval(doc: ParsedDoc) -> bool:
    ch32 = section_slice(doc, 3, "3.2", skip_fences=True).replace("**", "")
    if "当前结论：已通过" not in ch32:
        return False
    cp_mode = doc.header_fields.get("管控模式", "").upper()
    wants_cp0 = ("CP0" in cp_mode) or ("CP0" in ch32 and "CP1" not in ch32)
    wants_cp1 = ("CP1" in cp_mode) or ("CP1" in ch32 and "CP0" not in ch32)
    if wants_cp0:
        cp_pat = (
            r"(?<![不未勿莫非无免难待拟缓])(?:正式)?(?:批准|通过|核签|同意)[^，,。；;\n]{0,16}\bCP0\b"
            r"|\bCP0\b[^，,。；;\n]{0,16}(?<![不未勿莫非无免难待拟缓])(?:已批准|已通过|确认通过|审批通过)"
            r"|(?:前置|单点|方案|口令|范围)确认(?:已)?通过"
        )
    elif wants_cp1:
        cp_pat = (
            r"(?<![不未勿莫非无免难待拟缓])(?:正式)?(?:批准|通过|核签|同意)[^，,。；;\n]{0,16}\bCP1\b"
            r"|\bCP1\b[^，,。；;\n]{0,16}(?<![不未勿莫非无免难待拟缓])(?:已批准|已通过|确认通过|抽检通过|验收通过)"
            r"|(?:单点|结项|交付|体验)(?:抽检|确认|验收)(?:已)?通过"
        )
    else:
        cp_pat = (
            r"(?<![不未勿莫非无免难待拟缓])(?:正式)?(?:批准|通过|核签|同意)[^，,。；;\n]{0,16}\b(?:CP0|CP1|S1)\b"
            r"|\b(?:CP0|CP1|S1)\b[^，,。；;\n]{0,16}(?<![不未勿莫非无免难待拟缓])(?:已批准|已通过|确认通过)"
            r"|(?:单点|前置|结项|交付|体验)(?:确认|抽检|验收)(?:已)?通过"
        )
    s1_ok = False
    for _, meta, body in iter_ch7_human_entries(doc):
        prose = _strip_md_link_urls(body)
        if APPROVAL_NEGATION_RE.search(meta) or APPROVAL_NEGATION_RE.search(prose) or "指示" in meta:
            s1_ok = False
            continue
        if not APPROVAL_AFFIRM_RE.search(meta):
            continue
        clauses = [c.strip() for c in re.split(r"[。；;\n]+", prose) if c.strip()]
        if any(
            re.search(cp_pat, cl) and not re.search(r"(?:提交|呈报|上报|报请|进入)\s*(?:CP0|CP1)", cl)
            for cl in clauses
        ):
            s1_ok = True
    return s1_ok


def extract_stage_authors(plan_doc: ParsedDoc, stage_indices: set[int]) -> tuple[set[str], set[str], list[str]]:
    author_agents: set[str] = set()
    author_sessions: set[str] = set()
    errors: list[str] = []
    deliverable_tasks = 0
    for t in plan_doc.tasks:
        if t.stage_index not in stage_indices:
            continue
        # Pure review/verification subtasks whose sole output is review_*_{t1,t2,t4}.md are not deliverable authors.
        if re.search(r"review_\d{8}_.+_(?:t1|t2|t4)", t.title) and not re.search(
            r"修订|重制|汇总|刷新|实现|新建|升级", t.title
        ):
            continue
        deliverable_tasks += 1
        chg_links = [lk for lk in t.progress_links if Path(lk).name.startswith("change_")]
        if not chg_links:
            errors.append(f"{t.task_id} 缺少关联 change_*.md 进展链接")
            continue
        for lk in chg_links:
            chg_path = (plan_doc.path.parent / lk).resolve()
            if not chg_path.is_file():
                errors.append(f"{t.task_id} 关联变更文档不存在: {Path(lk).name}")
                continue
            chg = parse_doc(chg_path)
            chg_authors: set[str] = set()
            for agent, role in parse_agent_roles(chg.header_fields.get("助手", "")):
                if role and REVIEW_ONLY_RE.search(role) and not AUTHOR_ACTION_RE.search(role):
                    continue
                if agent in KNOWN_AGENTS_SET:
                    chg_authors.add(agent)
            if not chg_authors:
                errors.append(f"{chg_path.name} 未登记有效交付作者助手")
                continue
            author_agents.update(chg_authors)
            non_authors = KNOWN_AGENTS_SET - chg_authors
            for idx, ln in enumerate(chg.lines):
                if idx in chg.code_lines:
                    continue
                mentions_other = any(
                    re.search(rf"\b{ag}\b", ln, re.IGNORECASE)
                    or AGENT_MCP_CHANNELS[ag] in ln
                    or f"{ag.lower()}_task_" in ln.lower()
                    for ag in non_authors
                )
                if not mentions_other:
                    author_sessions.update(extract_receipt_ids(ln))
    if deliverable_tasks == 0:
        errors.append("未找到阶段交付子任务")
    return author_agents, author_sessions, errors


def check_review_open_counts(rev_doc: ParsedDoc) -> bool:
    ch5 = section_slice(rev_doc, 2, "5.", skip_fences=True)
    if not ch5:
        return False
    m = re.search(r"未闭环\s*HIGH/MEDIUM\s*[：:]\s*(\d+)", ch5)
    if not m:
        return False
    summary_val = int(m.group(1))
    open_items = 0
    for ln in ch5.splitlines():
        s = ln.strip()
        if "[OPEN]" in s:
            open_items += 1
    return summary_val == 0 and open_items == 0


def check_review_independence_and_roles(
    rev_doc: ParsedDoc,
    stage: str,
    author_agents: set[str],
    author_sessions: set[str],
    author_errors: list[str],
    effective_mode: str,
    missing_code: str,
    issues: list[Issue],
) -> None:
    rel_label = rev_doc.path.as_posix()
    if author_errors or not author_agents:
        detail = "; ".join(author_errors) if author_errors else "无有效交付作者"
        issues.append(Issue("PL-REV-INDEP", f"{rev_doc.path.name}: 阶段交付作者来源缺失或无法核验 ({detail})", rel_label, 1))
        return

    roles = parse_agent_roles(rev_doc.header_fields.get("评审助手", ""))
    if any(a not in KNOWN_AGENTS_SET for a, _ in roles):
        issues.append(Issue("PL-REV-INDEP", f"{rev_doc.path.name}: 文首评审助手存在未知身份", rel_label, 1))
        return
    reviewer_agents = {a for a, _ in roles}
    rd_header_reviewers = reviewer_agents & RD_AGENTS

    ch2 = section_slice(rev_doc, 2, "2.", skip_fences=True)
    ch3 = section_slice(rev_doc, 2, "3.", skip_fences=True)
    ch2_claimed = extract_section_claimed_agents(ch2)
    ch3_claimed = extract_section_claimed_agents(ch3)
    ch2_ids = extract_receipt_ids(ch2) if stage in ("t1", "t4") else set()
    ch3_ids = extract_receipt_ids(ch3)

    # Rule 1: Claude must NEVER serve as detail / anti-omission / anti-cheat reviewer.
    for agent, role in roles:
        if agent == "Claude" and re.search(r"细节|防漏|反作弊|脚本|codex_detail", role):
            issues.append(Issue("PL-REV-INDEP", f"{rev_doc.path.name}: Claude 严禁承担工程细节审或替补验收结果核验", rel_label, 1))
            return

    if "Claude" in ch3_claimed or (stage == "t2" and "Claude" in reviewer_agents and not rd_header_reviewers):
        issues.append(Issue("PL-REV-INDEP", f"{rev_doc.path.name}: 第 3 章工程细节审严禁由 Claude 替补或混入 Claude 收据", rel_label, 1))
        return

    # Rule 2: Stage-specific role & strict header-to-body receipt binding.
    if stage in ("t1", "t4"):
        if len(reviewer_agents) < 2:
            if reviewer_agents:
                issues.append(Issue("PL-REV-INDEP", f"{rev_doc.path.name}: 禁止单一智能体兼任架构与工程细节双棒评审", rel_label, 1))
                return
            issues.append(Issue(missing_code, f"{rev_doc.path.name}: 未登记有效评审助手", rel_label, 1))
            return
        if "Claude" not in reviewer_agents or "Claude" not in ch2_claimed or not has_full_receipt(ch2):
            issues.append(Issue(missing_code, f"{rev_doc.path.name}: 缺少第 2 章 Claude 架构核验/抽查原生会话收据", rel_label, 1))
            return
        if ch2_claimed != {"Claude"}:
            issues.append(
                Issue("PL-REV-INDEP", f"{rev_doc.path.name}: 第 2 章架构收据归属异常 {sorted(ch2_claimed)}", rel_label, 1)
            )
            return

    if not rd_header_reviewers or not (ch3_claimed & RD_AGENTS) or not has_full_receipt(ch3):
        issues.append(Issue(missing_code, f"{rev_doc.path.name}: 缺少第 3 章研发智能体工程细节/验收核验原生会话收据", rel_label, 1))
        return

    if ch3_claimed != rd_header_reviewers:
        issues.append(
            Issue(
                "PL-REV-INDEP",
                f"{rev_doc.path.name}: 第 3 章收据归属智能体 {sorted(ch3_claimed)} 与文首研发评审助手 {sorted(rd_header_reviewers)} 不一致",
                rel_label,
                1,
            )
        )
        return

    if stage in ("t1", "t4") and (ch2_ids & ch3_ids):
        issues.append(Issue("PL-REV-INDEP", f"{rev_doc.path.name}: 第 2 章与第 3 章复用了相同的会话或任务收据 ID", rel_label, 1))
        return

    # Rule 3: Author vs Reviewer independence (D4-A default vs D4-B approved) + unconditional session disjointness.
    if (ch2_ids | ch3_ids) & author_sessions:
        issues.append(
            Issue("PL-REV-INDEP", f"{rev_doc.path.name}: 评审收据与阶段交付作者复用了相同物理会话或任务 ID", rel_label, 1)
        )
        return

    overlap = author_agents & reviewer_agents
    if overlap:
        if effective_mode != "d4b":
            issues.append(
                Issue(
                    "PL-REV-INDEP",
                    f"{rev_doc.path.name}: 评审助手与阶段交付作者重叠 {sorted(overlap)}（D4-A 强制身份互斥）",
                    rel_label,
                    1,
                )
            )
        else:
            iso_flag = rev_doc.header_fields.get("同源隔离会话评审", "").strip()
            rev_sessions = extract_session_ids(ch2 + "\n" + ch3)
            if iso_flag != "是" or not rev_sessions or (author_sessions & rev_sessions):
                issues.append(
                    Issue(
                        "PL-REV-INDEP",
                        f"{rev_doc.path.name}: D4-B 模式必须标注 '同源隔离会话评审：是' 且物理会话 ID 互斥",
                        rel_label,
                        1,
                    )
                )


def extract_single_owner_receipt(section_text: str) -> tuple[str, str, list[str]] | None:
    sess_vals = [
        m.group(1)
        for m in re.finditer(r"(?:session_id|thread_id)\s*[=:：]\s*`?([A-Za-z0-9._-]+)`?", section_text)
    ]
    task_vals = [
        m.group(1)
        for m in re.finditer(r"task_id\s*[=:：]\s*`?([A-Za-z0-9._-]+)`?", section_text)
    ]
    turn_vals = [
        m.group(1)
        for m in re.finditer(r"turn_id\s*[=:：]\s*`?([A-Za-z0-9._-]+)`?", section_text)
    ]
    total_kv = len(ALL_RECEIPT_KV_RE.findall(section_text))
    if (
        not sess_vals
        or len(set(sess_vals)) != 1
        or not task_vals
        or len(set(task_vals)) != 1
        or not turn_vals
        or len(turn_vals) != len(set(turn_vals))
        or total_kv != len(sess_vals) + len(task_vals) + len(turn_vals)
    ):
        return None
    return sess_vals[0], task_vals[0], turn_vals


def parse_ch45_agent_receipts(ch45_text: str) -> tuple[dict[str, tuple[str, str, list[str]]], str]:
    blocks: list[list[str]] = []
    for raw_ln in ch45_text.splitlines():
        s = raw_ln.strip()
        if not s:
            continue
        is_top_bullet = bool(re.match(r"^-\s+", raw_ln))
        is_indented = raw_ln.startswith(("  ", "\t"))
        if is_top_bullet:
            blocks.append([raw_ln])
        elif is_indented and blocks:
            blocks[-1].append(raw_ln)
        else:
            if ALL_RECEIPT_KV_RE.search(raw_ln):
                return {}, "第 4.5 节存在未归属独立智能体列表项的游离收据字段"

    parsed: dict[str, tuple[str, str, list[str]]] = {}
    consumed_kv = 0
    for blk in blocks:
        blk_text = "\n".join(blk)
        blk_kv_count = len(ALL_RECEIPT_KV_RE.findall(blk_text))
        claimed = extract_section_claimed_agents(blk_text)
        if blk_kv_count == 0 and not claimed:
            continue
        if len(claimed) != 1:
            return {}, f"第 4.5 节单条收据项必须精确绑定唯一智能体身份（实测 {sorted(claimed)}）"
        agent = next(iter(claimed))
        if agent in parsed:
            return {}, f"第 4.5 节存在重复的 {agent} 收据登记块"
        rec = extract_single_owner_receipt(blk_text)
        if rec is None:
            return {}, f"第 4.5 节 {agent} 收据缺失完整三元组或混入重复/额外收据字段"
        parsed[agent] = rec
        consumed_kv += blk_kv_count

    if consumed_kv != len(ALL_RECEIPT_KV_RE.findall(ch45_text)):
        return {}, "第 4.5 节存在额外未归属的收据字段"
    return parsed, ""


def verify_acceptance_t4_binding(acc_doc: ParsedDoc, t4_rev: ParsedDoc, issues: list[Issue]) -> None:
    acc_label = acc_doc.path.as_posix()
    acc_roles = parse_agent_roles(acc_doc.header_fields.get("验真助手", ""))
    t4_roles = parse_agent_roles(t4_rev.header_fields.get("评审助手", ""))
    if not acc_roles or any(a not in KNOWN_AGENTS_SET for a, _ in acc_roles):
        issues.append(Issue("PL-REV-INDEP", f"{acc_doc.path.name}: 文首未登记有效的验真助手身份", acc_label, 1))
        return
    acc_verifiers = {a for a, _ in acc_roles}
    t4_reviewers = {a for a, _ in t4_roles if a in KNOWN_AGENTS_SET}
    if acc_verifiers != t4_reviewers:
        issues.append(
            Issue(
                "PL-REV-INDEP",
                f"{acc_doc.path.name}: 文首验真助手 {sorted(acc_verifiers)} 与 {t4_rev.path.name} 评审助手 {sorted(t4_reviewers)} 不一致",
                acc_label,
                1,
            )
        )
        return
    rd_verifiers = t4_reviewers - {"Claude"}
    if "Claude" not in t4_reviewers or len(rd_verifiers) != 1:
        issues.append(
            Issue(
                "PL-REV-INDEP",
                f"{acc_doc.path.name}: T4 验真助手必须由 Claude（抽查）与唯一研发核验助手组成（实测 {sorted(t4_reviewers)}）",
                acc_label,
                1,
            )
        )
        return
    rd_agent = next(iter(rd_verifiers))

    ch2_t4 = section_slice(t4_rev, 2, "2.", skip_fences=True)
    ch3_t4 = section_slice(t4_rev, 2, "3.", skip_fences=True)
    c_rev_rec = extract_single_owner_receipt(ch2_t4)
    rd_rev_rec = extract_single_owner_receipt(ch3_t4)
    if c_rev_rec is None or rd_rev_rec is None:
        issues.append(
            Issue(
                "PL-REV-INDEP",
                f"{t4_rev.path.name}: 第 2/3 章收据三元组不完整或混入多任务/多会话 ID",
                acc_label,
                1,
            )
        )
        return

    ch45 = section_slice(acc_doc, 3, "4.5", skip_fences=True)
    ch45_map, parse_err = parse_ch45_agent_receipts(ch45)
    if parse_err or set(ch45_map.keys()) != t4_reviewers:
        msg = parse_err or f"第 4.5 节收据归属 {sorted(ch45_map.keys())} 与验真助手 {sorted(t4_reviewers)} 不一致"
        issues.append(Issue("PL-REV-INDEP", f"{acc_doc.path.name}: {msg}", acc_label, 1))
        return

    expected_by_agent = {"Claude": c_rev_rec, rd_agent: rd_rev_rec}
    c_all_ids = {c_rev_rec[0], c_rev_rec[1], *c_rev_rec[2]}
    rd_all_ids = {rd_rev_rec[0], rd_rev_rec[1], *rd_rev_rec[2]}
    if c_all_ids & rd_all_ids:
        issues.append(Issue("PL-REV-INDEP", f"{t4_rev.path.name}: Claude 与 {rd_agent} 之间存在收据 ID 重叠", acc_label, 1))
        return

    for agent, (rev_sess, rev_task, rev_turns) in expected_by_agent.items():
        acc_sess, acc_task, acc_turns = ch45_map[agent]
        if (
            acc_sess != rev_sess
            or acc_task != rev_task
            or not set(acc_turns).issubset(set(rev_turns))
            or acc_turns[-1] != rev_turns[-1]
        ):
            issues.append(
                Issue(
                    "PL-REV-INDEP",
                    f"{acc_doc.path.name}: 第 4.5 节 {agent} 收据 (session/task/effective_turn) 与 {t4_rev.path.name} 失配",
                    acc_label,
                    1,
                )
            )
            return


def validate_design_and_images(repo_root: Path, design_path: Path | None, plan_doc: ParsedDoc) -> tuple[list[Issue], int]:
    issues: list[Issue] = []
    p_label = plan_doc.path.as_posix()
    if design_path is None or not design_path.is_file():
        issues.append(Issue("PL-CP0-TXT", "未找到有效关联专题设计文档", p_label, 1))
        issues.append(Issue("PL-CP0-IMG", "未找到包含已上图配图的关联专题设计文档", p_label, 1))
        return issues, 1

    d_doc = parse_doc(design_path)
    d_label = design_path.as_posix()

    has_txt_diagram = False
    for info, content, _ in d_doc.fences:
        lang = info.split()[0].lower() if info else ""
        non_empty_lines = [ln for ln in content.splitlines() if ln.strip()]
        if lang == "text" and len(non_empty_lines) >= 3 and any(
            tok in content for tok in ("+--", "-->", "──", "│", "->", "===", "|")
        ):
            has_txt_diagram = True
            break
    if not has_txt_diagram:
        issues.append(Issue("PL-CP0-TXT", f"{design_path.name}: 缺少非空 ```text 流程/拓扑/技术原理图代码块", d_label, 1))

    gdrive_imgs = []
    for src, ln in d_doc.images:
        norm_src = unquote(urlsplit(src.strip()).path).replace("\\", "/")
        if ".gdrive/managed/" in norm_src:
            gdrive_imgs.append((norm_src, ln))

    if not gdrive_imgs:
        issues.append(Issue("PL-CP0-IMG", f"{design_path.name}: 正文未嵌入 .gdrive/managed/ 受管高清架构配图", d_label, 1))
        return issues, (1 if issues else 0)

    gdrive_root = repo_root / ".gdrive"
    managed_root = gdrive_root / "managed"
    try:
        mounted = gdrive_root.exists() and managed_root.is_dir()
    except OSError:
        mounted = False

    if not mounted:
        issues.append(
            Issue("PL-GDRIVE-UNMOUNTED", ".gdrive/managed 未挂载或不可访问", d_label, gdrive_imgs[0][1])
        )
        return issues, 2

    for norm_src, ln in gdrive_imgs:
        m = GDRIVE_IMG_RE.search(norm_src)
        if not m:
            issues.append(Issue("PL-CP0-IMG", f"配图路径不符合受管哈希命名契约: {norm_src}", d_label, ln))
            continue
        bucket, rel_asset, expected_hex, ext = m.group(1), m.group(2), m.group(3).lower(), m.group(4).lower()
        img_file = (design_path.parent / norm_src).resolve()
        receipt_file = managed_root / "receipts" / bucket / f"{rel_asset}.json"
        if not img_file.is_file():
            issues.append(Issue("PL-CP0-IMG", f"受管配图文件不存在: {norm_src}", d_label, ln))
            continue
        if not receipt_file.is_file():
            issues.append(Issue("PL-CP0-IMG", f"缺少受管入库回执文件: {receipt_file.name}", d_label, ln))
            continue
        try:
            receipt_data = json.loads(receipt_file.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            issues.append(Issue("PL-CP0-IMG", f"受管回执 JSON 损坏: {receipt_file.name}", d_label, ln))
            continue
        rec_sha = str(receipt_data.get("sha256", "")).lower().removeprefix("sha256:")
        if rec_sha and rec_sha != expected_hex:
            issues.append(Issue("PL-CP0-IMG", f"回执 sha256 与文件名后缀不一致: {receipt_file.name}", d_label, ln))
            continue

        data = img_file.read_bytes()
        valid_magic = (
            (ext == "png" and data.startswith(b"\x89PNG\r\n\x1a\n"))
            or (ext in {"jpg", "jpeg"} and data.startswith(b"\xff\xd8\xff"))
            or (ext == "webp" and len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP")
        )
        if not valid_magic:
            issues.append(Issue("PL-CP0-IMG", f"配图文件魔数头与扩展名 .{ext} 不匹配: {img_file.name}", d_label, ln))
            continue
        actual_hex = hashlib.sha256(data).hexdigest()
        if actual_hex != expected_hex:
            issues.append(Issue("PL-CP0-IMG", f"配图内容 SHA-256 失配: 期望 {expected_hex}，实测 {actual_hex}", d_label, ln))

    return issues, (1 if issues else 0)


def check_plan(
    plan_path: Path,
    gate: str,
    repo_id: str = "",
    indep_mode: str = "auto",
) -> tuple[list[Issue], int]:
    if not plan_path.is_file():
        return [Issue("PL-IO-ERR", f"Plan file not found: {plan_path}")], 2

    repo_root = infer_repo_root(plan_path)
    doc = parse_doc(plan_path)
    p_label = plan_path.as_posix()
    issues: list[Issue] = []

    if not doc.h1:
        issues.append(Issue("PL-DRAFT-STRUCT", "缺少一级标题", p_label, 1))
    if doc.duplicate_keys:
        issues.append(Issue("PL-DRAFT-STRUCT", f"文首存在重复元数据键: {doc.duplicate_keys}", p_label, 1))
        return issues, 1

    try:
        rel_plan = plan_path.resolve().relative_to(repo_root.resolve()).as_posix()
    except ValueError:
        rel_plan = plan_path.name

    level = doc.header_fields.get("计划等级", "").strip()
    legacy_exempt = False
    if not level:
        manifest_file = repo_root / "docs" / "plan" / ".legacy-plans.json"
        effective_repo_id = repo_id or detect_git_repo_id(repo_root)
        if gate == "draft" and manifest_file.is_file() and effective_repo_id:
            try:
                manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                issues.append(Issue("PL-IO-ERR", f"存量清单 .legacy-plans.json 读取或解析失败: {exc}", p_label, 1))
                return issues, 2
            plans_map = manifest.get("plans", {}) if isinstance(manifest, dict) else {}
            entry = plans_map.get(rel_plan)
            if (
                isinstance(manifest, dict)
                and manifest.get("repo_id") == effective_repo_id
                and isinstance(entry, dict)
                and entry.get("migrated") is False
            ):
                had_level, git_err = git_ever_had_plan_level(repo_root, rel_plan)
                if had_level is None:
                    issues.append(Issue("PL-IO-ERR", f"存量豁免双棘轮核验失败（拒绝放行）: {git_err}", p_label, 1))
                    return issues, 2
                if had_level is False:
                    legacy_exempt = True
        if not legacy_exempt:
            issues.append(Issue("PL-LVL-REQ", "缺失必填文首字段 '计划等级'（或已触发双棘轮单向退出豁免）", p_label, 1))
            return issues, 1
        return issues, (1 if issues else 0)

    if level not in {"简单计划", "复杂研发计划"}:
        issues.append(Issue("PL-LVL-REQ", f"非法计划等级取值: {level!r}", p_label, 1))
        return issues, 1

    # Validate stage structure & anti-downgrade
    ch7_text = section_slice(doc, 2, "7.", skip_fences=True)
    ch5_text = section_slice(doc, 2, "5.", skip_fences=True)
    stage_h3s = [h for h in doc.headings if h.level == 3 and re.match(r"^5\.\d+\s+", h.title)]
    stage_h4s = [h for h in doc.headings if h.level == 4 and re.match(r"^4\.1\.\d+\s+", h.title)]

    if level == "简单计划":
        cp_count = count_plan_checkpoints(doc)
        scope_probe = classify_content(doc.text, plan_doc=doc, ignore_header_level=True)
        has_downgrade_approval = any(
            re.search(r"(?:批准|特批|同意)[^。\n]{0,20}(?:降级|按简单计划|简单计划)", body)
            for _, _, body in parse_ch7_human_approvals(doc)
        )
        if cp_count >= 2 or (scope_probe["score"] >= 50 and not has_downgrade_approval):
            issues.append(
                Issue(
                    "PL-LVL-DOWNGRADE",
                    f"简单计划违规: 检查点数={cp_count}，复杂判据命中={scope_probe['matched_rules']} 且无第 7 章特批降级记录",
                    p_label,
                    1,
                )
            )
        if not (2 <= len(stage_h3s) <= 3):
            issues.append(Issue("PL-DRAFT-STRUCT", f"简单计划第 5 章阶段数须为 2～3 步，当前为 {len(stage_h3s)}", p_label, 1))
        if gate in {"cp0", "cp1"}:
            issues.append(Issue("PL-DRAFT-STRUCT", f"--gate {gate} 仅适用于复杂研发计划", p_label, 1))
            return issues, 1
    else:
        if len(stage_h4s) != 5 or len(stage_h3s) != 5:
            issues.append(
                Issue(
                    "PL-DRAFT-STRUCT",
                    f"复杂研发计划必须严格包含 T1–T5 五个阶段（当前 4.1 子节={len(stage_h4s)}，5.x 子节={len(stage_h3s)}）",
                    p_label,
                    1,
                )
            )
        else:
            for idx, (tid, std_name) in enumerate(COMPLEX_STAGES):
                h4_title = stage_h4s[idx].title
                h3_title = stage_h3s[idx].title
                if tid not in h4_title or std_name not in h4_title or tid not in h3_title or std_name not in h3_title:
                    issues.append(
                        Issue(
                            "PL-DRAFT-STRUCT",
                            f"复杂研发计划阶段 {idx + 1} 标题必须固定为 '{tid} {std_name}'（实测: '{h4_title}' / '{h3_title}'）",
                            p_label,
                            stage_h3s[idx].line,
                        )
                    )
        h32 = next((h for h in doc.headings if h.level == 3 and h.title.startswith("3.2")), None)
        cp_h4_titles = [h.title for h in doc.headings if h32 and h.level == 4 and h32.line < h.line <= h32.end_line]
        if len(cp_h4_titles) < 2 or not any("CP0" in t for t in cp_h4_titles) or not any("CP1" in t for t in cp_h4_titles):
            issues.append(Issue("PL-DRAFT-STRUCT", "复杂研发计划第 3.2 节必须同时定义 CP0 与 CP1 两个检查点", p_label, 1))

    # Checkbox summary closure check
    total_tasks = len(doc.tasks)
    checked_tasks = sum(1 for t in doc.tasks if t.checked)
    sum_m = PROGRESS_SUMMARY_RE.search(ch5_text)
    if not sum_m or (int(sum_m.group(1)), int(sum_m.group(2))) != (checked_tasks, total_tasks):
        issues.append(
            Issue(
                "PL-DRAFT-STRUCT",
                f"第 5 章总进度计数与复选框不闭合（实测勾选 {checked_tasks}/{total_tasks}）",
                p_label,
                1,
            )
        )
    for s_idx, sh in enumerate(stage_h3s, 1):
        st_tasks = [t for t in doc.tasks if t.stage_index == s_idx]
        st_done = sum(1 for t in st_tasks if t.checked)
        cm = STAGE_COUNT_RE.search(sh.title)
        if cm and (int(cm.group(1)), int(cm.group(2))) != (st_done, len(st_tasks)):
            issues.append(
                Issue(
                    "PL-DRAFT-STRUCT",
                    f"阶段 '{sh.title}' 分子/分母与末级复选框不闭合（实测 {st_done}/{len(st_tasks)}）",
                    p_label,
                    sh.line,
                )
            )

    if gate == "draft":
        return issues, (1 if issues else 0)

    # Determine D4 independence policy from affirmative Chapter 7 human approvals
    ch7_approves_d4b = any(
        re.search(r"(?<![不未勿莫非无免难待拟缓])(?:批准|特批|采用|同意|启用)[^。\n]{0,24}\bD4-B\b", body.replace("`", ""))
        and not re.search(r"未批\s*D4-B|优于\s*D4-B", body.replace("`", ""))
        for _, _, body in parse_ch7_human_approvals(doc)
    )
    if indep_mode == "d4b" and not ch7_approves_d4b:
        issues.append(Issue("PL-REV-INDEP", "计划第 7 章未特批 D4-B，禁止强传宽松模式 d4b", p_label, 1))
    effective_indep = "d4b" if (ch7_approves_d4b and indep_mode in {"auto", "d4b"}) else "d4a"

    in_archive_gate = gate == "archive"
    design_path, reviews, accepts = discover_linked_docs(repo_root, doc, archived=in_archive_gate)
    eff_reviews = effective_reviews_by_stage(reviews)

    if gate == "cp0":
        img_issues, img_rc = validate_design_and_images(repo_root, design_path, doc)
        issues.extend(img_issues)
        if img_rc == 2:
            return issues, 2

        # PL-CP0-ACC
        if len(accepts) != 1 or not ACCEPT_NAME_RE.match(accepts[0].path.name):
            issues.append(Issue("PL-CP0-ACC", "缺少反向关联本计划的合规 docs/acceptance/accept_*.md 考卷", p_label, 1))
        else:
            acc_doc = accepts[0]
            acc_status = acc_doc.header_fields.get("验收状态", "").strip()
            if acc_status not in VALID_ACCEPT_STATUS or acc_status == "已废弃":
                issues.append(Issue("PL-CP0-ACC", f"预设考卷验收状态非法或已废弃: {acc_status!r}", acc_doc.path.as_posix(), 1))
            try:
                _, sc_count, _ = compute_lock_hash(acc_doc.path)
                if sc_count < 1:
                    issues.append(Issue("PL-CP0-ACC", "预设考卷第 2 章未定义 S1..Sn 锁定验收场景", acc_doc.path.as_posix(), 1))
            except ValueError as exc:
                issues.append(Issue("PL-CP0-ACC", str(exc), acc_doc.path.as_posix(), 1))
            else:
                check_acceptance_procedure(acc_doc, issues)

        # PL-CP0-REV / PL-REV-STALE / PL-REV-INDEP
        t1_rev = eff_reviews.get("t1")
        t2_rev = eff_reviews.get("t2")
        authors_cp0, sessions_cp0, author_errs_cp0 = extract_stage_authors(doc, {1, 2})
        if not t1_rev or not t2_rev:
            issues.append(Issue("PL-CP0-REV", "缺少反向关联本计划的 t1 或 t2 阶段评审报告", p_label, 1))
        else:
            for stg_key, rdoc in (("t1", t1_rev), ("t2", t2_rev)):
                if rdoc.header_fields.get("评审结论", "").strip() != "已通过" or not check_review_open_counts(rdoc):
                    issues.append(
                        Issue("PL-CP0-REV", f"{rdoc.path.name}: 评审结论未通过或未闭环 HIGH/MEDIUM 不为 0", rdoc.path.as_posix(), 1)
                    )
                check_review_independence_and_roles(
                    rdoc, stg_key, authors_cp0, sessions_cp0, author_errs_cp0, effective_indep, "PL-CP0-REV", issues
                )
            if accepts and design_path and design_path.is_file():
                try:
                    expected_t2_hash = compute_review_hash("t2", accepts[0].path, design_path)
                    recorded_hash = t2_rev.header_fields.get("评审对象哈希", "").strip()
                    if recorded_hash != expected_t2_hash:
                        issues.append(
                            Issue(
                                "PL-REV-STALE",
                                f"{t2_rev.path.name}: 评审对象哈希陈旧失配（登记 {recorded_hash}，当前 {expected_t2_hash}）",
                                t2_rev.path.as_posix(),
                                1,
                            )
                        )
                except ValueError as exc:
                    issues.append(Issue("PL-CP0-REV", str(exc), t2_rev.path.as_posix(), 1))

        return issues, (1 if issues else 0)

    if gate == "cp1":
        img_issues, img_rc = validate_design_and_images(repo_root, design_path, doc)
        issues.extend(img_issues)
        if img_rc == 2:
            return issues, 2

        t1_rev = eff_reviews.get("t1")
        t2_rev = eff_reviews.get("t2")
        if (
            not t1_rev
            or not t2_rev
            or t1_rev.header_fields.get("评审结论", "").strip() != "已通过"
            or t2_rev.header_fields.get("评审结论", "").strip() != "已通过"
            or not check_review_open_counts(t1_rev)
            or not check_review_open_counts(t2_rev)
        ):
            issues.append(Issue("PL-CP0-REV", "前置 t1 或 t2 评审报告缺失或未闭环通过", p_label, 1))

        # PL-CP1-TASKS: all T1..T4 leaf tasks must be [x]
        unchecked_t1_t4 = [t.task_id for t in doc.tasks if t.stage_index in {1, 2, 3, 4} and not t.checked]
        if unchecked_t1_t4:
            issues.append(Issue("PL-CP1-TASKS", f"T1–T4 存在未勾选完成任务: {unchecked_t1_t4}", p_label, 1))

        # PL-CP1-LOCK & PL-CP1-ACC (strictly bound to affirmative human CP0 approval in Ch.7)
        approved_lock_hash, cp0_lock_err = extract_approved_cp0_lock_hash(doc)
        if len(accepts) != 1 or not ACCEPT_NAME_RE.match(accepts[0].path.name):
            issues.append(Issue("PL-CP1-ACC", "缺少反向关联本计划的 accept_*.md 验收文档", p_label, 1))
        else:
            acc_doc = accepts[0]
            acc_label = acc_doc.path.as_posix()
            try:
                live_lock_hash, sc_count, _ = compute_lock_hash(acc_doc.path)
                if approved_lock_hash is None:
                    issues.append(Issue("PL-CP1-LOCK", f"CP0 锁卷批准校验未通过: {cp0_lock_err}", p_label, 1))
                elif approved_lock_hash != live_lock_hash or sc_count < 1:
                    issues.append(
                        Issue(
                            "PL-CP1-LOCK",
                            f"考卷第 2 章锁卷哈希与计划第 7 章已批准 CP0 记录不一致（登记 {approved_lock_hash}，实测 {live_lock_hash}）",
                            acc_label,
                            1,
                        )
                    )
            except ValueError as exc:
                issues.append(Issue("PL-CP1-LOCK", str(exc), acc_label, 1))

            acc_status = acc_doc.header_fields.get("验收状态", "").strip()
            ch3_acc = section_slice(acc_doc, 2, "3.", skip_fences=True)
            ch4_acc = section_slice(acc_doc, 2, "4.", skip_fences=True)
            has_all_5_anchors = all(
                any(h.level == 3 and h.title.startswith(f"4.{n}") for h in acc_doc.headings) for n in range(1, 6)
            )
            has_placeholder = bool(re.search(r"待\s*T4(?:\.4)?\s*(?:实跑回填|验真签署后追加登记)", ch3_acc + "\n" + ch4_acc))
            has_sha256 = bool(SHA256_HEX_RE.search(ch4_acc))
            ch45_acc = section_slice(acc_doc, 3, "4.5", skip_fences=True)
            has_t4_receipt_in_acc = has_full_receipt(ch45_acc)
            if (
                acc_status not in {"智能体已验真", "CP1已通过"}
                or has_placeholder
                or not has_all_5_anchors
                or not has_sha256
                or not has_t4_receipt_in_acc
            ):
                issues.append(
                    Issue(
                        "PL-CP1-ACC",
                        f"验收文档未就绪（状态={acc_status!r}，待回填残留={has_placeholder}，五维锚点={has_all_5_anchors}，SHA256={has_sha256}，4.5收据={has_t4_receipt_in_acc}）",
                        acc_label,
                        1,
                    )
                )

        # PL-CP1-REV / PL-REV-STALE / PL-REV-INDEP
        t4_rev = eff_reviews.get("t4")
        authors_cp1, sessions_cp1, author_errs_cp1 = extract_stage_authors(doc, {3, 4})
        if not t4_rev:
            issues.append(Issue("PL-CP1-REV", "缺少反向关联本计划的 t4 阶段核验与抽查报告", p_label, 1))
        else:
            if t4_rev.header_fields.get("评审结论", "").strip() != "已通过" or not check_review_open_counts(t4_rev):
                issues.append(
                    Issue("PL-CP1-REV", f"{t4_rev.path.name}: 评审结论未通过或未闭环 HIGH/MEDIUM 不为 0", t4_rev.path.as_posix(), 1)
                )
            check_review_independence_and_roles(
                t4_rev, "t4", authors_cp1, sessions_cp1, author_errs_cp1, effective_indep, "PL-CP1-REV", issues
            )
            if accepts:
                verify_acceptance_t4_binding(accepts[0], t4_rev, issues)
                try:
                    expected_t4_hash = compute_review_hash("t4", accepts[0].path)
                    rec_t4_hash = t4_rev.header_fields.get("评审对象哈希", "").strip()
                    if rec_t4_hash != expected_t4_hash:
                        issues.append(
                            Issue(
                                "PL-REV-STALE",
                                f"{t4_rev.path.name}: T4 评审对象哈希失配（登记 {rec_t4_hash}，当前 {expected_t4_hash}）",
                                t4_rev.path.as_posix(),
                                1,
                            )
                        )
                except ValueError as exc:
                    issues.append(Issue("PL-CP1-REV", str(exc), t4_rev.path.as_posix(), 1))

        return issues, (1 if issues else 0)

    # archive-preflight and archive
    plan_status = doc.header_fields.get("计划状态", "").strip()
    is_deprecated = plan_status == "已废弃"

    if gate == "archive-preflight":
        if not rel_plan.startswith("docs/plan/"):
            issues.append(Issue("PL-ARC-SYM", "archive-preflight 必须在计划尚位于 docs/plan/ 时调用", p_label, 1))
        if not is_deprecated:
            unchecked = [t for t in doc.tasks if not t.checked]
            if len(unchecked) > 1 or (len(unchecked) == 1 and doc.tasks and unchecked[0].task_id != doc.tasks[-1].task_id):
                issues.append(
                    Issue(
                        "PL-ARC-SYM",
                        f"archive-preflight 仅允许最后一个收尾归档子任务处于 [ ]，当前未勾选: {[t.task_id for t in unchecked]}",
                        p_label,
                        1,
                    )
                )
            if level == "复杂研发计划":
                approved_cp0_h, cp0_err = extract_approved_cp0_lock_hash(doc)
                if approved_cp0_h is None or not has_affirmative_cp1_approval(doc):
                    issues.append(
                        Issue(
                            "PL-ARC-SYM",
                            f"复杂计划归档前须同时具备有效的第 7 章人类 CP0（含锁卷哈希）与 CP1 批准记录 ({cp0_err or '缺有效 CP1 批准记录'})",
                            p_label,
                            1,
                        )
                    )
                if len(accepts) != 1 or accepts[0].header_fields.get("验收状态", "").strip() != "CP1已通过":
                    issues.append(Issue("PL-ARC-SYM", "复杂计划归档前验收文档状态必须为 'CP1已通过'", p_label, 1))
                elif approved_cp0_h is not None:
                    try:
                        live_lh, _, _ = compute_lock_hash(accepts[0].path)
                        if live_lh != approved_cp0_h:
                            issues.append(Issue("PL-CP1-LOCK", "归档预检检测到考卷第 2 章锁卷哈希失配", accepts[0].path.as_posix(), 1))
                    except ValueError as exc:
                        issues.append(Issue("PL-CP1-LOCK", str(exc), accepts[0].path.as_posix(), 1))
                t4_rev = eff_reviews.get("t4")
                if not t4_rev or t4_rev.header_fields.get("评审结论", "").strip() != "已通过":
                    issues.append(Issue("PL-ARC-SYM", "复杂计划归档前缺少已通过的 t4 评审报告", p_label, 1))
                elif accepts:
                    try:
                        exp_h = compute_review_hash("t4", accepts[0].path)
                        if t4_rev.header_fields.get("评审对象哈希", "").strip() != exp_h:
                            issues.append(Issue("PL-REV-STALE", "归档预检检测到 t4 冻结证据被篡改", t4_rev.path.as_posix(), 1))
                    except ValueError as exc:
                        issues.append(Issue("PL-ARC-SYM", str(exc), accepts[0].path.as_posix(), 1))
            elif count_plan_checkpoints(doc) == 1:
                if not has_affirmative_s1_approval(doc):
                    issues.append(Issue("PL-ARC-SYM", "S1 简单计划归档前单点检查点须为 '已通过' 且第 7 章具备有效人类批准记录", p_label, 1))
        else:
            if level == "复杂研发计划":
                if not accepts or any(a.header_fields.get("验收状态", "").strip() != "已废弃" for a in accepts):
                    issues.append(Issue("PL-ARC-SYM", "已废弃复杂计划关联的 acceptance 状态必须同步置为 '已废弃'", p_label, 1))
                if not reviews or any(r.header_fields.get("评审结论", "").strip() != "已废弃" for r in reviews):
                    issues.append(Issue("PL-ARC-SYM", "已废弃复杂计划关联的 reviews 结论必须同步置为 '已废弃'", p_label, 1))
        return issues, (1 if issues else 0)

    if gate == "archive":
        if not rel_plan.startswith("docs/archive/plan/"):
            issues.append(Issue("PL-ARC-SYM", "archive 门禁要求计划已移入 docs/archive/plan/", p_label, 1))
            return issues, 1
        if plan_status not in {"已完成", "已废弃"}:
            issues.append(Issue("PL-ARC-SYM", f"已归档计划状态必须为 '已完成' 或 '已废弃'（实测 {plan_status!r}）", p_label, 1))

        # Active directory zero-remnant check
        if (repo_root / "docs" / "plan" / plan_path.name).exists():
            issues.append(Issue("PL-ARC-SYM", f"活动目录 docs/plan/ 仍残留副本: {plan_path.name}", p_label, 1))

        for active_sub in ("changes", "reviews", "acceptance"):
            act_dir = repo_root / "docs" / active_sub
            if not act_dir.is_dir():
                continue
            for f in act_dir.glob("*.md"):
                if f.name in {"AGENTS.md", "README.md"}:
                    continue
                txt = f.read_text(encoding="utf-8")
                if plan_path.name in txt:
                    issues.append(Issue("PL-ARC-SYM", f"活动目录 docs/{active_sub}/{f.name} 仍残留未归档关联文档", f.as_posix(), 1))

        if not is_deprecated:
            if any(not t.checked for t in doc.tasks):
                issues.append(Issue("PL-ARC-SYM", "已完成归档计划所有末级任务必须 100% 勾选 [x]", p_label, 1))
            if level == "复杂研发计划":
                approved_cp0_h, cp0_err = extract_approved_cp0_lock_hash(doc)
                if approved_cp0_h is None or not has_affirmative_cp1_approval(doc):
                    issues.append(
                        Issue(
                            "PL-ARC-SYM",
                            f"已归档复杂计划须保留有效的第 7 章人类 CP0（含锁卷哈希）与 CP1 批准记录 ({cp0_err or '缺有效 CP1 批准记录'})",
                            p_label,
                            1,
                        )
                    )
                if len(accepts) != 1 or accepts[0].header_fields.get("验收状态", "").strip() != "CP1已通过":
                    issues.append(Issue("PL-ARC-SYM", "已归档复杂计划缺少 docs/archive/acceptance/ 下 CP1已通过 的验收文档", p_label, 1))
                elif approved_cp0_h is not None:
                    try:
                        live_lh, _, _ = compute_lock_hash(accepts[0].path)
                        if live_lh != approved_cp0_h:
                            issues.append(Issue("PL-CP1-LOCK", "归档后检测到考卷第 2 章锁卷哈希失配", accepts[0].path.as_posix(), 1))
                    except ValueError as exc:
                        issues.append(Issue("PL-CP1-LOCK", str(exc), accepts[0].path.as_posix(), 1))
                eff_arc_rev = effective_reviews_by_stage(reviews)
                if not all(k in eff_arc_rev for k in ("t1", "t2", "t4")):
                    issues.append(Issue("PL-ARC-SYM", "已归档复杂计划缺少同构归入 docs/archive/reviews/ 的 t1/t2/t4 报告", p_label, 1))
                elif accepts:
                    t4_r = eff_arc_rev["t4"]
                    try:
                        if t4_r.header_fields.get("评审对象哈希", "").strip() != compute_review_hash("t4", accepts[0].path):
                            issues.append(Issue("PL-REV-STALE", "归档后 t4 冻结证据哈希失配", t4_r.path.as_posix(), 1))
                    except ValueError as exc:
                        issues.append(Issue("PL-ARC-SYM", str(exc), accepts[0].path.as_posix(), 1))
            elif count_plan_checkpoints(doc) == 1:
                if not has_affirmative_s1_approval(doc):
                    issues.append(Issue("PL-ARC-SYM", "已归档 S1 简单计划须具备有效的单点检查点与第 7 章人类批准记录", p_label, 1))
        else:
            if level == "复杂研发计划":
                if not accepts or any(a.header_fields.get("验收状态", "").strip() != "已废弃" for a in accepts):
                    issues.append(Issue("PL-ARC-SYM", "已废弃归档计划关联 acceptance 须移入 archive 且状态为 '已废弃'", p_label, 1))
                if not reviews or any(r.header_fields.get("评审结论", "").strip() != "已废弃" for r in reviews):
                    issues.append(Issue("PL-ARC-SYM", "已废弃归档计划关联 reviews 须移入 archive 且结论为 '已废弃'", p_label, 1))

        # Outbound & Full-repo inbound broken-link verification across all managed repo *.md files
        archived_bundle = [doc] + reviews + accepts
        target_names = {d.path.name for d in archived_bundle}
        for b_doc in archived_bundle:
            for href, ln in b_doc.links:
                u = urlsplit(href.strip())
                if u.scheme or not u.path:
                    continue
                dest = (b_doc.path.parent / unquote(u.path)).resolve()
                if not dest.exists():
                    issues.append(Issue("PL-ARC-SYM", f"{b_doc.path.name}: 归档文档内部存在断链 {href}", b_doc.path.as_posix(), ln))

        for md_file in iter_repo_markdown_files(repo_root):
            try:
                raw_txt = md_file.read_text(encoding="utf-8")
            except (OSError, UnicodeError):
                continue
            if not any(name in raw_txt for name in target_names):
                continue
            m_doc = parse_doc_text(raw_txt, md_file)
            for href, ln in m_doc.links:
                u = urlsplit(href.strip())
                if u.scheme or not u.path:
                    continue
                target_p = unquote(u.path)
                if Path(target_p).name not in target_names and not any(name in target_p for name in target_names):
                    continue
                dest = (md_file.parent / target_p).resolve()
                if not dest.exists():
                    issues.append(
                        Issue(
                            "PL-ARC-SYM",
                            f"全仓入链断链: {md_file.relative_to(repo_root.resolve()).as_posix()} -> {href}",
                            md_file.as_posix(),
                            ln,
                        )
                    )

        return issues, (1 if issues else 0)

    return [Issue("PL-IO-ERR", f"Unknown gate: {gate}")], 2


def cmd_freeze(args) -> int:
    root = Path(args.root).resolve()
    manifest_path = root / "docs" / "plan" / ".legacy-plans.json"
    if manifest_path.exists():
        print(f"ERROR: legacy manifest already exists (one-time init only): {manifest_path}", file=sys.stderr)
        return 2
    plan_dir = root / "docs" / "plan"
    if not plan_dir.is_dir():
        print(f"ERROR: docs/plan directory not found under {root}", file=sys.stderr)
        return 2

    plans: dict[str, dict] = {}
    for p in sorted(plan_dir.glob("*.plan.md")):
        doc = parse_doc(p)
        if doc.header_fields.get("计划等级", "").strip():
            continue
        rel = p.relative_to(root).as_posix()
        origin = git_earliest_commit_date(root, rel)
        if not origin:
            ch1_ch7 = section_slice(doc, 2, "1.") + "\n" + section_slice(doc, 2, "7.")
            found_dates = sorted(re.findall(r"\b(20\d{2}-\d{2}-\d{2})\b", ch1_ch7))
            if found_dates:
                origin = found_dates[0]
        if origin and origin < args.before_date:
            plans[rel] = {"frozen_at": origin, "migrated": False}

    payload = {
        "repo_id": args.repo_id,
        "frozen_before": args.before_date,
        "plans": plans,
    }
    manifest_rel = rel_display(manifest_path, root)
    manifest_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    if args.json:
        print(json.dumps({"manifest": manifest_rel, "count": len(plans), "repo_id": args.repo_id}, ensure_ascii=False))
    else:
        print(f"FROZEN: {len(plans)} legacy plans recorded in {manifest_rel}")
    return 0


def cmd_migrate_legacy(args) -> int:
    plan_path = Path(args.plan).resolve()
    if not plan_path.is_file():
        print(f"ERROR: plan not found: {plan_path}", file=sys.stderr)
        return 2
    root = Path(args.root).resolve() if args.root else infer_repo_root(plan_path)
    manifest_path = root / "docs" / "plan" / ".legacy-plans.json"
    if not manifest_path.is_file():
        print(f"ERROR: legacy manifest not found: {manifest_path}", file=sys.stderr)
        return 2
    doc = parse_doc(plan_path)
    if doc.header_fields.get("计划等级", "").strip() not in {"简单计划", "复杂研发计划"}:
        print("FAIL [PL-LVL-REQ]: plan must declare valid '计划等级' before migrate-legacy", file=sys.stderr)
        return 1
    rel = plan_path.relative_to(root).as_posix()
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    if rel not in data.get("plans", {}):
        print(f"ERROR: {rel} is not in {manifest_path}", file=sys.stderr)
        return 2
    data["plans"][rel]["migrated"] = True
    manifest_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(f"MIGRATED: {rel} permanently exited legacy exemption")
    return 0


def cmd_init(args) -> int:
    if not SLUG_RE.fullmatch(args.slug):
        print(f"ERROR: invalid --slug {args.slug!r}; use lowercase [a-z0-9.-]", file=sys.stderr)
        return 2
    if not args.title.strip() or any(ch in args.title for ch in ("\r", "\n", "\x00")):
        print("ERROR: invalid --title (must be single-line non-empty text)", file=sys.stderr)
        return 2
    if not args.owner.strip() or any(ch in args.owner for ch in ("\r", "\n", "\x00", "：", ":")):
        print("ERROR: invalid --owner", file=sys.stderr)
        return 2

    today_str = args.date or date.today().isoformat()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", today_str):
        print(f"ERROR: invalid --date {today_str!r}; expected YYYY-MM-DD", file=sys.stderr)
        return 2
    try:
        date.fromisoformat(today_str)
    except ValueError:
        print(f"ERROR: invalid calendar --date {today_str!r}", file=sys.stderr)
        return 2
    ym = today_str[:7].replace("-", ".")
    ymd = today_str.replace("-", "")

    root = Path(args.root)
    if not root.exists() or not root.is_dir():
        print(f"ERROR: --root does not exist or is not a directory: {root}", file=sys.stderr)
        return 2

    tpl_dir = Path(__file__).resolve().parents[1] / "resources" / "templates"
    plan_rel = f"docs/plan/{ym}.{args.slug}.plan.md"
    rev_rel = f"docs/reviews/review_{ymd}_{args.slug}_t1.md"
    acc_rel = f"docs/acceptance/accept_{ymd}_{args.slug}.md"

    outputs: dict[Path, str] = {}
    if args.level == "simple":
        cp_mode = (args.cp_mode or "s0").upper()
        tpl = (tpl_dir / "plan-simple.template.md").read_text(encoding="utf-8")
        rendered = (
            tpl.replace("@TITLE@", args.title)
            .replace("@OWNER@", args.owner)
            .replace("@DATE@", today_str)
            .replace("@CP_MODE@", cp_mode)
            .replace("(plan-simple.template.md)", f"({ym}.{args.slug}.plan.md)")
        )
        if cp_mode in {"S1-CP0", "S1-CP1"}:
            cp_tag = "CP0：前置确认" if cp_mode == "S1-CP0" else "CP1：结项体验抽检"
            cp_block = (
                "### 3.2 人类检查点（1 个）\n\n"
                f"#### 3.2.1 {cp_tag}\n\n"
                "- **何时检查**：按简单计划单点确认节点执行。\n"
                "- **检查什么**：确认关键输入或交付表现。\n"
                "- **当前结论**：待提交。"
            )
            rendered = rendered.replace(
                "### 3.2 人类检查点（0 个）\n\n本计划默认采用 `S0` 人类零参与自闭环模式；若切换为 `S1` 模式，仅保留单一 `CP0` 或 `CP1` 小节。",
                cp_block,
            )
        outputs[root / plan_rel] = rendered
    else:
        tpl_plan = (tpl_dir / "plan-complex.template.md").read_text(encoding="utf-8")
        rev_link = f"../reviews/review_{ymd}_{args.slug}_t1.md" if args.with_core_docs else f"{ym}.{args.slug}.plan.md"
        acc_link = f"../acceptance/accept_{ymd}_{args.slug}.md" if args.with_core_docs else f"{ym}.{args.slug}.plan.md"
        rendered_plan = (
            tpl_plan.replace("@TITLE@", args.title)
            .replace("@OWNER@", args.owner)
            .replace("@DATE@", today_str)
            .replace("(plan-complex.template.md)", f"({ym}.{args.slug}.plan.md)")
            .replace("(rd-review.template.md)", f"({rev_link})")
            .replace("(acceptance.template.md)", f"({acc_link})")
        )
        outputs[root / plan_rel] = rendered_plan
        if args.with_core_docs:
            tpl_rev = (tpl_dir / "rd-review.template.md").read_text(encoding="utf-8")
            outputs[root / rev_rel] = (
                tpl_rev.replace("@TITLE@", args.title)
                .replace("@OWNER@", args.owner)
                .replace("@DATE@", today_str)
                .replace("(plan-complex.template.md)", f"(../plan/{ym}.{args.slug}.plan.md)")
                .replace("(acceptance.template.md)", f"(../acceptance/accept_{ymd}_{args.slug}.md)")
            )
            tpl_acc = (tpl_dir / "acceptance.template.md").read_text(encoding="utf-8")
            outputs[root / acc_rel] = (
                tpl_acc.replace("@TITLE@", args.title)
                .replace("@OWNER@", args.owner)
                .replace("@DATE@", today_str)
                .replace("(plan-complex.template.md)", f"(../plan/{ym}.{args.slug}.plan.md)")
                .replace("(rd-review.template.md)", f"(../reviews/review_{ymd}_{args.slug}_t1.md)")
            )

    # Full pre-write validation in memory BEFORE creating directories or touching any target on disk
    try:
        for target, content in outputs.items():
            check_no_symlink_escape(root, target)
            if target.exists() and target.is_dir():
                print(f"ERROR: target path is a directory: {target}", file=sys.stderr)
                return 2
            if not args.force and target.exists():
                print(f"ERROR: target already exists (pass --force to overwrite): {target}", file=sys.stderr)
                return 2
            doc_check = parse_doc_text(content, target)
            if not doc_check.h1:
                print(f"ERROR: scaffold output missing H1 heading: {target.name}", file=sys.stderr)
                return 2
            if doc_check.duplicate_keys:
                print(f"ERROR: duplicate metadata keys generated in scaffold output: {doc_check.duplicate_keys}", file=sys.stderr)
                return 2
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    root_resolved = root.resolve()
    prior_state: dict[Path, bytes | None] = {
        target: (target.read_bytes() if target.exists() else None) for target in outputs
    }
    written_targets: list[Path] = []
    created: list[str] = []
    try:
        for target, content in outputs.items():
            target.parent.mkdir(parents=True, exist_ok=True)
            encoded = content.replace("\r\n", "\n").replace("\r", "\n").encode("utf-8")
            fd, tmp_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=str(target.parent))
            tmp_path = Path(tmp_name)
            try:
                with os.fdopen(fd, "wb") as fh:
                    fh.write(encoded)
                os.replace(tmp_path, target)
            except BaseException:
                tmp_path.unlink(missing_ok=True)
                raise
            written_targets.append(target)
            created.append(rel_display(target.resolve(), root_resolved))
    except OSError as exc:
        for t in written_targets:
            old = prior_state[t]
            if old is None:
                t.unlink(missing_ok=True)
            else:
                t.write_bytes(old)
        print(f"ERROR: failed to write scaffold output: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps({"created": created}, ensure_ascii=False))
    else:
        for c in created:
            print(f"CREATED: {c}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p_cls = sub.add_parser("classify", help="classify R&D demand text or plan file")
    grp = p_cls.add_mutually_exclusive_group(required=True)
    grp.add_argument("--text", help="demand description text")
    grp.add_argument("--plan", type=Path, help="existing plan markdown file")
    p_cls.add_argument("--json", action="store_true")

    p_frz = sub.add_parser("freeze", help="one-time freeze of pre-governance ungraded plans in target repo")
    p_frz.add_argument("--root", default=".", help="target repository root")
    p_frz.add_argument("--repo-id", required=True, help="canonical repository id, e.g. org/repo")
    p_frz.add_argument("--before-date", default="2026-11-15", help="exclusive YYYY-MM-DD cutoff date")
    p_frz.add_argument("--json", action="store_true")

    p_mig = sub.add_parser("migrate-legacy", help="permanently mark a legacy plan as migrated")
    p_mig.add_argument("plan", type=Path)
    p_mig.add_argument("--root", default="")

    p_init = sub.add_parser("init", help="scaffold a simple or complex plan (and optional core docs)")
    p_init.add_argument("--level", choices=("simple", "complex"), required=True)
    p_init.add_argument("--slug", required=True)
    p_init.add_argument("--title", required=True)
    p_init.add_argument("--owner", required=True)
    p_init.add_argument("--cp-mode", choices=("s0", "s1-cp0", "s1-cp1"), default="s0")
    p_init.add_argument("--with-core-docs", action="store_true")
    p_init.add_argument("--force", action="store_true")
    p_init.add_argument("--date", default="")
    p_init.add_argument("--root", default=".")
    p_init.add_argument("--json", action="store_true")

    p_lh = sub.add_parser("lock-hash", help="compute deterministic lock SHA-256 of acceptance Chapter 2")
    p_lh.add_argument("accept", type=Path)
    p_lh.add_argument("--json", action="store_true")

    p_rh = sub.add_parser("review-hash", help="compute deterministic review target slice SHA-256")
    p_rh.add_argument("--stage", choices=("t1", "t2", "t4"), required=True)
    p_rh.add_argument("--design", type=Path, default=None)
    p_rh.add_argument("--accept", type=Path, required=True)
    p_rh.add_argument("--json", action="store_true")

    p_chk = sub.add_parser("check", help="run 5-stage AST governance gate on a plan")
    p_chk.add_argument("plan", type=Path)
    p_chk.add_argument("--gate", choices=("draft", "cp0", "cp1", "archive-preflight", "archive"), required=True)
    p_chk.add_argument("--repo-id", default="")
    p_chk.add_argument("--indep-mode", choices=("auto", "d4a", "d4b"), default="auto")
    p_chk.add_argument("--json", action="store_true")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "classify":
        if args.plan is not None:
            if not args.plan.is_file():
                print(f"ERROR: plan file not found: {args.plan}", file=sys.stderr)
                return 2
            p_doc = parse_doc(args.plan)
            res = classify_content(p_doc.text, plan_doc=p_doc, ignore_header_level=False)
        else:
            res = classify_content(args.text)
        if args.json:
            print(json.dumps(res, ensure_ascii=False))
        else:
            print(f"{res['level']} ({res['recommended_mode']}) score={res['score']} rules={res['matched_rules']}")
        return 0

    if args.command == "freeze":
        return cmd_freeze(args)

    if args.command == "migrate-legacy":
        return cmd_migrate_legacy(args)

    if args.command == "init":
        return cmd_init(args)

    if args.command == "lock-hash":
        if not args.accept.is_file():
            print(f"ERROR: acceptance file not found: {args.accept}", file=sys.stderr)
            return 2
        try:
            digest, count, _ = compute_lock_hash(args.accept)
        except ValueError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 2
        if args.json:
            print(json.dumps({"lock_hash": digest, "scenario_count": count}, ensure_ascii=False))
        else:
            print(f"{digest} (scenarios={count})")
        return 0

    if args.command == "review-hash":
        if not args.accept.is_file():
            print(f"ERROR: acceptance file not found: {args.accept}", file=sys.stderr)
            return 2
        try:
            digest = compute_review_hash(args.stage, args.accept, args.design)
        except ValueError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 2
        if args.json:
            print(json.dumps({"stage": args.stage, "review_hash": digest}, ensure_ascii=False))
        else:
            print(digest)
        return 0

    if args.command == "check":
        issues, rc = check_plan(args.plan, args.gate, repo_id=args.repo_id, indep_mode=args.indep_mode)
        repo_root = infer_repo_root(args.plan)
        for iss in issues:
            if iss.path:
                p_obj = Path(iss.path)
                if p_obj.is_absolute():
                    iss.path = rel_display(p_obj, repo_root)
        codes = sorted({i.code for i in issues})
        p_disp = rel_display(args.plan, repo_root)
        if args.json:
            print(
                json.dumps(
                    {
                        "ok": rc == 0,
                        "exit_code": rc,
                        "gate": args.gate,
                        "plan": p_disp,
                        "codes": codes,
                        "issues": [i.__dict__ for i in issues],
                    },
                    ensure_ascii=False,
                )
            )
        else:
            if rc == 0:
                print(f"PASS [{args.gate}]: {p_disp} (0 issues)")
            else:
                print(f"FAIL [{args.gate}] (exit={rc}, codes={codes}):")
                for iss in issues:
                    loc = f"{iss.path}:{iss.line}" if iss.path else p_disp
                    print(f"  - [{iss.code}] {loc}: {iss.message}")
        return rc

    return 2


if __name__ == "__main__":
    sys.exit(main())
