#!/usr/bin/env python3
"""Check a time-aligned performance duet against its approved source mix."""

from __future__ import annotations

import argparse
from math import gcd
import json
from pathlib import Path

import numpy as np
from scipy.signal import resample_poly
import soundfile as sf


RATE_TOLERANCE = .025


def read(path: Path, target_rate: int | None = None) -> tuple[np.ndarray, int]:
    audio, rate = sf.read(path, dtype="float32", always_2d=True)
    if target_rate is not None and rate != target_rate:
        factor = gcd(rate, target_rate)
        audio = resample_poly(audio, target_rate // factor, rate // factor, axis=0)
        rate = target_rate
    return audio, rate


def db(audio: np.ndarray) -> float:
    return float(20 * np.log10(max(float(np.sqrt(np.mean(audio * audio))), 1e-9)))


def window(audio: np.ndarray, rate: int, start: float, end: float) -> np.ndarray:
    return audio[round(start * rate):round(end * rate)]


def mix_reconstruction(preview: np.ndarray, backing: np.ndarray,
                       female: np.ndarray, male: np.ndarray) -> dict:
    if any(track.shape != preview.shape for track in (backing, female, male)):
        return {"passed": False, "error": "all mix tracks must be aligned stereo"}
    residual = preview - (backing + female + male)
    reference_rms = max(float(np.sqrt(np.mean(preview * preview))), 1e-9)
    residual_rms = float(np.sqrt(np.mean(residual * residual)))
    residual_db = float(20 * np.log10(max(residual_rms, 1e-9) / reference_rms))
    return {"passed": residual_db <= -35,
            "residual_relative_db": round(residual_db, 2),
            "residual_peak": round(float(np.max(np.abs(residual))), 4)}


def level_events(source: np.ndarray, preview: np.ndarray, rate: int) -> list[dict]:
    hop = round(.1 * rate)
    size = min(len(source), len(preview)) // hop * hop
    def frame_db(audio: np.ndarray) -> np.ndarray:
        frames = audio[:size].reshape(-1, hop, audio.shape[1])
        return 10 * np.log10(np.maximum(np.mean(frames * frames, axis=(1, 2)), 1e-12))
    source_db, preview_db = frame_db(source), frame_db(preview)
    active = source_db > -36
    offset = float(np.median((preview_db - source_db)[active])) if np.any(active) else 0.
    anomaly = (np.abs(preview_db - source_db - offset) > 3) & (source_db > -30)
    edges = np.flatnonzero(np.diff(np.r_[False, anomaly, False].astype(np.int8)))
    return [{"start_seconds": round(a * .1, 2), "end_seconds": round(b * .1, 2),
             "maximum_deviation_db": round(float(np.max(np.abs(
                 preview_db[a:b] - source_db[a:b] - offset))), 2)}
            for a, b in zip(edges[::2], edges[1::2]) if b - a >= 1]


def validate(plan: dict, female: np.ndarray, male: np.ndarray,
             preview: np.ndarray, source: np.ndarray, rate: int,
             backing: np.ndarray | None = None) -> dict:
    errors: list[str] = []
    lines = plan.get("lines", [])
    if plan.get("schema_version") != "duet-performance-v1":
        errors.append("schema_version must be duet-performance-v1")
    duration = len(preview) / rate
    if abs(float(plan.get("duration_seconds", 0)) - duration) > RATE_TOLERANCE:
        errors.append("plan duration differs from preview")
    if any(len(audio) != len(preview) for audio in (female, male, source)):
        errors.append("audio durations differ")
    if source.shape == preview.shape and np.array_equal(source, preview):
        errors.append("source and preview are identical; use the prior approved mix")
    if any(audio.shape[1] != 2 for audio in (female, male, preview)):
        errors.append("female, male and preview must be stereo")
    if not isinstance(lines, list) or not lines:
        errors.append("lines must contain the timed singer roles")
        lines = []
    previous_end = 0.
    roles = {"female": 0, "male": 0, "both": 0}
    for number, line in enumerate(lines, 1):
        try:
            start, end = float(line["start_seconds"]), float(line["end_seconds"])
            role = line["voice"]
        except (KeyError, TypeError, ValueError):
            errors.append(f"line {number}: invalid time or role")
            continue
        if role not in roles or not str(line.get("text", "")).strip():
            errors.append(f"line {number}: invalid role or empty lyric")
        if start < previous_end - .005 or end <= start or end > duration + RATE_TOLERANCE:
            errors.append(f"line {number}: overlapping or invalid interval")
        previous_end = end
        if role not in roles or end - start < .25:
            continue
        roles[role] += 1
        inner_start, inner_end = start + .1, end - .1
        female_db = db(window(female, rate, inner_start, inner_end))
        male_db = db(window(male, rate, inner_start, inner_end))
        if role == "female" and (female_db < -55 or male_db > -65):
            errors.append(f"line {number}: female solo leakage or silence")
        elif role == "male" and (male_db < -55 or female_db > -65):
            errors.append(f"line {number}: male solo leakage or silence")
        elif role == "both" and (min(female_db, male_db) < -55 or
                                 abs(female_db - male_db) > 6):
            errors.append(f"line {number}: unbalanced or missing duet singer")
    if any(count == 0 for count in roles.values()):
        errors.append("female, male and both lines are all required")
    if float(np.max(np.abs(preview))) >= .98:
        errors.append("preview peak is too high")
    mismatches = level_events(source, preview, rate)
    if mismatches:
        errors.append(f"{len(mismatches)} sustained level mismatches against source")
    boundaries = sorted({float(line["start_seconds"]) for line in lines if "start_seconds" in line}
                        | {float(line["end_seconds"]) for line in lines if "end_seconds" in line})
    handoffs = []
    for boundary in boundaries:
        if boundary < .4 or boundary > duration - .4:
            continue
        before, after = (boundary - .35, boundary - .08), (boundary + .08, boundary + .35)
        source_jump = db(window(source, rate, *after)) - db(window(source, rate, *before))
        preview_jump = db(window(preview, rate, *after)) - db(window(preview, rate, *before))
        if abs(preview_jump - source_jump) > 3:
            handoffs.append({"seconds": round(boundary, 2),
                             "excess_jump_db": round(preview_jump - source_jump, 2)})
    if handoffs:
        errors.append(f"{len(handoffs)} handoffs have excess level jumps")
    reconstruction = (mix_reconstruction(preview, backing, female, male)
                      if backing is not None else None)
    return {"qualified": not errors,
            "qualification_scope": "role_windows_and_mix_level_machine_only",
            "stems_reconstruct_preview": (reconstruction["passed"]
                                          if reconstruction is not None else None),
            "mix_reconstruction": reconstruction,
            "human_review_required": True, "duration_seconds": round(duration, 3),
            "role_counts": roles, "level_mismatches": mismatches,
            "handoff_mismatches": handoffs, "errors": errors}


def main() -> None:
    parser = argparse.ArgumentParser()
    for name in ("plan", "female", "male", "preview", "source", "report"):
        parser.add_argument(f"--{name}", type=Path, required=True)
    parser.add_argument("--backing", type=Path)
    parser.add_argument("--require-reconstructible", action="store_true")
    args = parser.parse_args()
    if args.require_reconstructible and not args.backing:
        parser.error("--require-reconstructible requires --backing")
    plan = json.loads(args.plan.read_text(encoding="utf-8"))
    preview, rate = read(args.preview)
    female, female_rate = read(args.female)
    male, male_rate = read(args.male)
    if rate != female_rate or rate != male_rate:
        raise SystemExit("stem sample rates differ")
    source, _ = read(args.source, rate)
    backing = read(args.backing, rate)[0] if args.backing else None
    report = validate(plan, female, male, preview, source, rate, backing)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                           encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))
    passed = report["qualified"] and (not args.require_reconstructible or
                                      report["stems_reconstruct_preview"])
    raise SystemExit(0 if passed else 1)


if __name__ == "__main__":
    main()
