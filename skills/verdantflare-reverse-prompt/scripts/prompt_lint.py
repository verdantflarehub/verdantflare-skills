#!/usr/bin/env python3
"""Deterministic linter and parser for verdantflare-reverse-prompt outputs."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

CATEGORY_SECTIONS: dict[str, list[str]] = {
    "c1": [
        "人物形象",
        "发型与妆容",
        "完整服装搭配",
        "场景环境",
        "人物姿态",
        "摄影构图",
        "画面质感",
        "严格要求",
    ],
    "c2": [
        "空间定位",
        "主体建筑",
        "陈设与材质",
        "空间层次",
        "光影氛围",
        "摄影构图",
        "严格要求",
    ],
    "c3": [
        "人物形象",
        "发型与妆容",
        "完整服装搭配",
        "场景环境",
        "人物关系与姿态",
        "摄影构图",
        "画面质感",
        "严格要求",
    ],
}

SECTION_HEADER_RE = re.compile(r"^【([^【】\r\n]+)】\s*$")
ASPECT_RATIO_RE = re.compile(r"(?<![0-9])(?:9:16|16:9|3:4|4:3|2:3|3:2|1:1|21:9)(?![0-9])")
TEMPLATE_VAR_RE = re.compile(r"\{\{([^{}\r\n]+)\}\}")
PLACEHOLDER_RE = re.compile(r"(?:TODO|TBD|待补充|待填写|<[^>\r\n]{2,}>)")
WATERMARK_POLLUTION_TERMS = [
    "抖音",
    "小红书",
    "快手",
    "哔哩哔哩",
    "bilibili",
    "tiktok",
    "shutterstock",
    "getty images",
    "视觉中国",
    "台标",
]
EMPTY_BUZZWORDS = [
    "氛围感拉满",
    "高级感拉满",
    "绝美",
    "神仙颜值",
    "大师级杰作",
]


def parse_cn_sections(text: str) -> tuple[list[tuple[str, str]], str]:
    """Parse 【Title】 sections and optional trailing supplement notes."""
    sections: list[tuple[str, str]] = []
    current_title: str | None = None
    current_lines: list[str] = []
    supplement_lines: list[str] = []
    in_supplement = False

    for raw_line in text.splitlines():
        stripped = raw_line.strip()
        if not in_supplement and stripped.startswith(("补全说明", "【补全说明】", "变量说明")):
            if current_title is not None:
                sections.append((current_title, "\n".join(current_lines).strip()))
                current_title = None
                current_lines = []
            in_supplement = True
            supplement_lines.append(raw_line)
            continue

        if in_supplement:
            supplement_lines.append(raw_line)
            continue

        match = SECTION_HEADER_RE.match(stripped)
        if match:
            if current_title is not None:
                sections.append((current_title, "\n".join(current_lines).strip()))
            current_title = match.group(1).strip()
            current_lines = []
        elif current_title is not None:
            current_lines.append(raw_line)

    if current_title is not None:
        sections.append((current_title, "\n".join(current_lines).strip()))

    return sections, "\n".join(supplement_lines).strip()


def _check_common_pollution(
    text: str,
    variant: str,
    errors: list[str],
    warnings: list[str],
) -> list[str]:
    variables = sorted(set(m.group(1).strip() for m in TEMPLATE_VAR_RE.finditer(text)))
    if variant != "template" and variables:
        errors.append(
            f"RP-PLACEHOLDER: non-template prompt contains unresolved template variables: {', '.join(variables)}"
        )
    if variant == "template" and not variables:
        errors.append("RP-TEMPLATE-EMPTY: template variant must declare at least one {{variable}} placeholder")

    if PLACEHOLDER_RE.search(text):
        errors.append("RP-PLACEHOLDER: prompt contains unresolved placeholder markers (TODO/TBD/待补充/<...>)")

    lowered = text.lower()
    for term in WATERMARK_POLLUTION_TERMS:
        if term in lowered:
            # Allow explicit negative mentions like "不要抖音水印" only if preceded by negative word.
            neg_pattern = re.compile(rf"(?:不要|无|不含|去除|no\s+){re.escape(term)}", re.IGNORECASE)
            if not neg_pattern.search(text):
                errors.append(f"RP-POLLUTION: possible platform watermark/logo term found in prompt: {term}")

    found_buzzwords = [word for word in EMPTY_BUZZWORDS if word in text]
    if found_buzzwords:
        warnings.append(
            f"RP-BUZZWORD: replace vague adjectives with physical visual details: {', '.join(found_buzzwords)}"
        )

    return variables


def lint_english_prompt(text: str, category: str) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    variables = _check_common_pollution(text, "en", errors, warnings)

    stripped = text.strip()
    if len(stripped) < 120:
        errors.append("RP-EN-SHORT: English natural-language prompt is too short (< 120 chars)")

    aspect_match = ASPECT_RATIO_RE.search(stripped)
    if not aspect_match:
        errors.append("RP-ASPECT-REQ: missing explicit aspect ratio (e.g. 9:16, 16:9, 3:4, 1:1)")

    lowered = stripped.lower()
    if "strict requirements:" not in lowered:
        errors.append("RP-EN-STRICT: missing 'Strict requirements:' closing constraint section")

    for req_token, label in [("collage", "no collage"), ("text", "no text"), ("watermark", "no watermark")]:
        if req_token not in lowered:
            errors.append(f"RP-NEG-BASE: missing base negative constraint covering '{label}'")

    if category == "c2":
        if not any(tok in lowered for tok in ("no people", "no person", "uninhabited", "empty", "no human")):
            errors.append("RP-NEG-EMPTY-SET: c2 empty-scene prompt must explicitly forbid people/pedestrians")
    else:
        if "adult" not in lowered and not re.search(r"\b(?:1[89]|[2-7][0-9])[- ]year[- ]old\b", lowered):
            errors.append("RP-ADULT-REQ: c1/c3 prompt must explicitly state adult age")
        if "full-body" in lowered or "full body" in lowered:
            if not any(tok in lowered for tok in ("no cropping", "not cropped", "head to", "full body")):
                warnings.append("RP-FULLBODY-PAIR: verify head-to-shoe framing constraint is explicit")

    return {
        "status": "PASS" if not errors else "FAIL",
        "category": category,
        "variant": "en",
        "aspect_ratio": aspect_match.group(0) if aspect_match else None,
        "sections": {},
        "variables": variables,
        "errors": errors,
        "warnings": warnings,
    }


def lint_cn_prompt(text: str, category: str, variant: str) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    expected = CATEGORY_SECTIONS[category]

    sections_list, supplement = parse_cn_sections(text)
    found_titles = [title for title, _ in sections_list]
    sections_map = {title: body for title, body in sections_list}

    missing = [title for title in expected if title not in sections_map]
    if missing:
        errors.append(f"RP-SECTION-MISSING: missing required sections: {', '.join(missing)}")

    present_expected_order = [title for title in found_titles if title in expected]
    expected_filtered = [title for title in expected if title in sections_map]
    if present_expected_order != expected_filtered:
        errors.append(
            f"RP-SECTION-ORDER: sections out of order; expected {expected_filtered}, got {present_expected_order}"
        )

    for title in expected:
        if title in sections_map and len(sections_map[title].strip()) < 10:
            errors.append(f"RP-SECTION-EMPTY: section 【{title}】 is empty or too short (< 10 chars)")

    variables = _check_common_pollution(text, variant, errors, warnings)

    comp_text = sections_map.get("摄影构图", "")
    aspect_match = ASPECT_RATIO_RE.search(comp_text) or ASPECT_RATIO_RE.search(text)
    if not aspect_match:
        errors.append("RP-ASPECT-REQ: 【摄影构图】 must declare an explicit aspect ratio (e.g. 9:16, 16:9, 3:4)")

    strict_text = sections_map.get("严格要求", "")
    for token in ("拼图", "文字", "水印"):
        if token not in strict_text:
            errors.append(f"RP-NEG-BASE: 【严格要求】 must include negative constraint against '{token}'")

    if category in ("c1", "c3"):
        subject_text = sections_map.get("人物形象", "")
        if not any(tok in subject_text for tok in ("成年", "岁")) and variant != "template":
            errors.append("RP-ADULT-REQ: 【人物形象】 must explicitly specify an adult age description")
        if "全身" in comp_text or "全身" in strict_text:
            if not any(tok in strict_text for tok in ("裁切", "完整全身", "不要半身")):
                errors.append(
                    "RP-FULLBODY-PAIR: full-body framing requires anti-cropping / no-half-body rule in 【严格要求】"
                )
        if any(tok in text for tok in ("真实皮肤", "摄影", "写实", "毛孔")):
            if not any(tok in strict_text for tok in ("塑料皮肤", "假面", "过度磨皮")):
                warnings.append(
                    "RP-SKIN-PAIR: realistic portrait should pair '不要塑料皮肤' or '不要假面感' in 【严格要求】"
                )

    if category == "c2":
        if not any(tok in strict_text for tok in ("无人", "不要出现人物", "无人物", "空景")):
            errors.append("RP-NEG-EMPTY-SET: c2 【严格要求】 must explicitly require an empty/uninhabited scene")

    if any(tok in text for tok in ("民国", "古装", "古镇", "古代", "汉服", "旗袍")):
        if "现代" not in strict_text:
            warnings.append("RP-ERA-PAIR: period/historical scene should pair '不要现代元素' in 【严格要求】")

    return {
        "status": "PASS" if not errors else "FAIL",
        "category": category,
        "variant": variant,
        "aspect_ratio": aspect_match.group(0) if aspect_match else None,
        "sections": sections_map,
        "supplements": supplement,
        "variables": variables,
        "errors": errors,
        "warnings": warnings,
    }


def evaluate_prompt(text: str, category: str, variant: str = "default") -> dict[str, Any]:
    if category not in CATEGORY_SECTIONS:
        raise ValueError(f"Unsupported category: {category}")
    if variant not in ("default", "template", "en"):
        raise ValueError(f"Unsupported variant: {variant}")
    if variant == "en":
        return lint_english_prompt(text, category)
    return lint_cn_prompt(text, category, variant)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Lint and parse verdantflare-reverse-prompt outputs.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    for cmd in ("lint", "parse"):
        sub = subparsers.add_parser(cmd, help=f"{cmd.title()} a reverse-engineered prompt text file.")
        sub.add_argument("path", type=Path, help="Path to the UTF-8 prompt text file.")
        sub.add_argument(
            "--category",
            choices=sorted(CATEGORY_SECTIONS.keys()),
            default="c1",
            help="Scene category: c1 (single person), c2 (empty scene), c3 (multi-person).",
        )
        sub.add_argument(
            "--variant",
            choices=("default", "template", "en"),
            default="default",
            help="Prompt output variant.",
        )
        sub.add_argument("--json", action="store_true", dest="as_json", help="Emit JSON output.")

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.path.is_file():
        print(f"ERROR: file not found: {args.path}", file=sys.stderr)
        return 2

    text = args.path.read_text(encoding="utf-8")
    result = evaluate_prompt(text, category=args.category, variant=args.variant)

    if args.command == "parse":
        payload = {
            "category": result["category"],
            "variant": result["variant"],
            "aspect_ratio": result["aspect_ratio"],
            "sections": result["sections"],
            "variables": result["variables"],
            "supplements": result.get("supplements", ""),
            "status": result["status"],
            "errors": result["errors"],
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0 if result["status"] == "PASS" else 2

    if args.as_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"{result['status']} [{result['category']}/{result['variant']}]: {args.path}")
        for err in result["errors"]:
            print(f"  ERROR: {err}")
        for warn in result["warnings"]:
            print(f"  WARN:  {warn}")

    return 0 if result["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
