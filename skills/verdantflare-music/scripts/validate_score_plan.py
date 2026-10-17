#!/usr/bin/env python3
"""Check measured cue bounds and timeline coverage, without rendering or claiming listening approval."""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import re

EPSILON = 1 / 48000


def number(value):
    return type(value) in (int, float) and math.isfinite(value)


def validate(plan):
    errors, pending, vocal_issues, spans = [], [], [], []
    if not isinstance(plan, dict) or plan.get("schema_version") != "background-score-v1":
        return {"technical_pass": False, "errors": ["invalid_schema"], "review_pending": [], "vocal_issues": []}
    duration = plan.get("duration_seconds")
    if not number(duration) or duration <= 0:
        errors.append("invalid_duration")
    cues, silences = plan.get("cues"), plan.get("silences")
    if not isinstance(cues, list) or not cues or not isinstance(silences, list):
        errors.append("cues_and_silences_required")
        return {"technical_pass": False, "errors": errors, "review_pending": [], "vocal_issues": []}
    ids = set()
    for index, cue in enumerate(cues):
        if not isinstance(cue, dict):
            errors.append(f"cue_{index}:invalid_cue")
            continue
        cid = cue.get("cue_id")
        if not isinstance(cid, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", cid):
            errors.append(f"cue_{index}:invalid_id")
            cid = f"cue_{index}"
        if cid in ids:
            errors.append(f"{cid}:duplicate_id")
        ids.add(cid)
        digest = cue.get("source_sha256")
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            errors.append(f"{cid}:source_digest_required")
        keys = ("timeline_start", "timeline_end", "source_start", "source_end", "source_duration_seconds")
        if not all(number(cue.get(k)) for k in keys):
            errors.append(f"{cid}:invalid_times")
        else:
            start, end, source_start, source_end, source_duration = (cue[k] for k in keys)
            if not (0 <= source_start < source_end <= source_duration + EPSILON):
                errors.append(f"{cid}:source_out_of_bounds")
            if not (number(duration) and 0 <= start < end <= duration + EPSILON):
                errors.append(f"{cid}:timeline_out_of_bounds")
            else:
                spans.append((start, end, cid))
            if abs((end - start) - (source_end - source_start)) > EPSILON:
                errors.append(f"{cid}:implicit_time_stretch_or_padding")
        review = cue.get("vocal_review")
        if review not in ("pending", "clear", "present"):
            errors.append(f"{cid}:invalid_vocal_review")
        elif review == "present":
            vocal_issues.append(cid)
        elif review == "pending":
            pending.append(cid)
        elif not isinstance(cue.get("vocal_review_evidence"), str) or not cue["vocal_review_evidence"].strip():
            pending.append(cid)
        if "required_bpm" in cue:
            expected, measured, tolerance = cue["required_bpm"], cue.get("measured_bpm"), cue.get("bpm_tolerance", 0)
            if not all(number(v) for v in (expected, measured, tolerance)) or expected <= 0 or measured <= 0 or tolerance < 0:
                errors.append(f"{cid}:bpm_measurement_required")
            elif abs(expected - measured) > tolerance:
                errors.append(f"{cid}:bpm_mismatch")
    for index, silence in enumerate(silences):
        sid = f"silence_{index}"
        if not isinstance(silence, dict):
            errors.append(f"{sid}:invalid_silence")
            continue
        start, end, reason = silence.get("start"), silence.get("end"), silence.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            errors.append(f"{sid}:reason_required")
        if not all(number(v) for v in (start, end, duration)) or not (0 <= start < end <= duration + EPSILON):
            errors.append(f"{sid}:invalid_times")
        else:
            spans.append((start, end, sid))
    covered = 0
    for start, end, label in sorted(spans):
        if start > covered + EPSILON:
            errors.append(f"{label}:uncovered_interval:{covered:.6f}-{start:.6f}")
        elif start < covered - EPSILON:
            errors.append(f"{label}:overlap_or_silence_conflict")
        covered = max(covered, end)
    if number(duration) and covered < duration - EPSILON:
        errors.append(f"uncovered_tail:{covered:.6f}-{duration:.6f}")
    return {"technical_pass": not errors, "errors": errors, "review_pending": pending,
            "vocal_issues": vocal_issues, "duration_seconds": duration if number(duration) else None,
            "scope": "declared_source_metadata_and_linear_timeline_only"}


def read_plan(path):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result

    return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=pairs)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("plan", type=Path)
    args = parser.parse_args()
    try:
        report = validate(read_plan(args.plan))
    except (OSError, ValueError) as error:
        report = {"technical_pass": False, "errors": [type(error).__name__]}
    print(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False))
    if not report["technical_pass"] or report.get("vocal_issues"):
        return 1
    return 2 if report.get("review_pending") else 0


if __name__ == "__main__":
    raise SystemExit(main())
