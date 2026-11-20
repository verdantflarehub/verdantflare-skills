#!/usr/bin/env python3
"""Check the documented H3 Ref2VA prompt layout and actual preflight tags.

This validates request compilation only, never media quality or model obedience.
"""
import argparse
import json
import re
from pathlib import Path

HEADINGS = (
    "subject_definitions", "summary", "retention_analysis",
    "detailed_description", "overall_soundscape", "non_diegetic_music",
)
TAG = re.compile(r"<(?:Picture|Video|Audio) [1-9][0-9]*>")


def validate(request, preflight):
    errors = []
    request = request.get("arguments", request)
    if request.get("model") != "minimax-h3-ref2va":
        return {"passed": False, "errors": ["This check applies only to H3 Ref2VA."]}
    prompt = request.get("prompt", "")
    matches = list(re.finditer(r"(?m)^(" + "|".join(HEADINGS) + r"):[ \t]*", prompt))
    if [m.group(1) for m in matches] != list(HEADINGS):
        errors.append("Required six sections are missing, duplicated or out of order.")
    else:
        for i, match in enumerate(matches):
            end = matches[i + 1].start() if i + 1 < len(matches) else len(prompt)
            if not prompt[match.end():end].strip():
                errors.append("Empty section: " + match.group(1))
        sections = {
            m.group(1): prompt[m.end():matches[i + 1].start() if i + 1 < len(matches) else len(prompt)]
            for i, m in enumerate(matches)
        }
        for section in ("subject_definitions", "retention_analysis"):
            for item in preflight.get("reference_mapping", []):
                if item.get("tag") not in set(TAG.findall(sections[section])):
                    errors.append(section + " lacks actual reference tag " + str(item.get("tag")))
    expected = {item.get("tag") for item in preflight.get("reference_mapping", [])}
    if not expected or any(not isinstance(t, str) or not TAG.fullmatch(t) for t in expected):
        errors.append("Actual preflight reference mapping is missing or invalid.")
    used = set(TAG.findall(prompt))
    for tag in sorted(used - {x for x in expected if isinstance(x, str)}):
        errors.append("Unbound media tag: " + tag)
    if re.search(r"\b(?:Picture|Video|Audio)\d+\b", prompt):
        errors.append("Uncompiled media label such as Picture1; use exact preflight tags.")
    actual_ids = [r.get("artifact_id") for group in ("images", "videos", "audios")
                  for r in request.get("references", {}).get(group, [])]
    mapped_ids = [r.get("artifact_id") for r in preflight.get("reference_mapping", [])]
    if set(actual_ids) != set(mapped_ids):
        errors.append("Preflight mapping does not match actual request references.")
    # Studio trims outer whitespace; changes within the prompt remain failures.
    if not isinstance(preflight.get("prompt"), str) or preflight["prompt"].strip() != prompt.strip():
        errors.append("Actual request prompt differs from saved preflight prompt.")
    return {"passed": not errors, "errors": errors,
            "scope": "Prompt format and reference binding only; no creative approval."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("request", type=Path)
    parser.add_argument("preflight", type=Path)
    args = parser.parse_args()
    try:
        result = validate(json.loads(args.request.read_text()), json.loads(args.preflight.read_text()))
    except (OSError, ValueError, TypeError, AttributeError) as exc:
        result = {"passed": False, "errors": ["Invalid input: " + type(exc).__name__]}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
