#!/usr/bin/env python3
"""Find source or conversion gaps before replacing a singer in a duet."""

from __future__ import annotations

import argparse
from math import gcd
import json
from pathlib import Path

import numpy as np
from scipy.ndimage import uniform_filter1d
from scipy.signal import resample_poly
import soundfile as sf


HOP_SECONDS = .05


def read(path: Path, rate: int | None = None) -> tuple[np.ndarray, int]:
    audio, actual_rate = sf.read(path, dtype="float32", always_2d=True)
    if rate is not None and actual_rate != rate:
        divisor = gcd(actual_rate, rate)
        audio = resample_poly(audio, rate // divisor, actual_rate // divisor, axis=0)
        actual_rate = rate
    return audio, actual_rate


def frame_db(audio: np.ndarray, hop: int) -> np.ndarray:
    frames = audio[:len(audio) // hop * hop].reshape(-1, hop, audio.shape[1])
    power = uniform_filter1d(np.mean(frames * frames, axis=(1, 2)), size=3)
    return 10 * np.log10(np.maximum(power, 1e-12))


def ranges(indices: np.ndarray) -> list[np.ndarray]:
    if not len(indices):
        return []
    cuts = np.flatnonzero(np.diff(indices) > 1) + 1
    return np.split(indices, cuts)


def audit(plan: dict, source: np.ndarray, backing: np.ndarray,
          candidate: np.ndarray, dry: np.ndarray | None, rate: int,
          role: str) -> dict:
    if plan.get("schema_version") != "duet-performance-v1":
        raise ValueError("plan must use duet-performance-v1")
    if role not in ("female", "male"):
        raise ValueError("role must be female or male")
    def fit(track: np.ndarray) -> np.ndarray:
        if abs(len(track) - len(source)) > round(.03 * rate):
            raise ValueError("audio durations differ by more than 30 ms")
        result = np.zeros((len(source), track.shape[1]), dtype="float32")
        size = min(len(result), len(track))
        result[:size] = track[:size]
        return result

    backing = fit(backing)
    candidate = fit(candidate)
    dry = fit(dry) if dry is not None else None
    if source.shape[1] != 2 or backing.shape[1] != 2:
        raise ValueError("source and backing must be stereo")
    if abs(len(source) / rate - plan.get("duration_seconds", 0)) > .03:
        raise ValueError("plan duration differs from audio")

    hop = round(HOP_SECONDS * rate)
    wet_db = frame_db(source - backing, hop)
    candidate_db = frame_db(candidate, hop)
    dry_db = frame_db(dry, hop) if dry is not None else None
    count = min(len(wet_db), len(candidate_db))
    active = np.zeros(count, dtype=bool)
    for line in plan["lines"]:
        if line["voice"] in (role, "both"):
            start = max(0, round(line["start_seconds"] / HOP_SECONDS))
            end = min(count, round(line["end_seconds"] / HOP_SECONDS))
            active[start:end] = True
    if not np.any(active):
        raise ValueError(f"plan has no {role} lines")

    voiced = active & (wet_db[:count] > -33)
    if voiced.sum() < 20:
        raise ValueError("too little source vocal energy to audit")
    results = {}
    for name, track_db in (("candidate", candidate_db), ("dry", dry_db)):
        if track_db is None:
            continue
        common = voiced & (track_db[:count] > -55)
        if common.sum() < 20:
            offset = 0.
        else:
            offset = float(np.median(track_db[:count][common] - wet_db[:count][common]))
        deficit = wet_db[:count] - (track_db[:count] - offset)
        missing = voiced & (deficit > 9)
        events = []
        for group in ranges(np.flatnonzero(missing)):
            if len(group) < 3:
                continue
            events.append({
                "start_seconds": round(group[0] * HOP_SECONDS, 2),
                "end_seconds": round((group[-1] + 1) * HOP_SECONDS, 2),
                "maximum_deficit_db": round(float(np.max(deficit[group])), 1),
            })
        results[name] = {"median_level_offset_db": round(offset, 1),
                         "coverage_gaps": events}

    return {
        "coverage_passed": not results["candidate"]["coverage_gaps"],
        "qualification_scope": "energy_coverage_only",
        "human_review_required": True,
        "role": role,
        "candidate": results["candidate"],
        "dry_source": results.get("dry"),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("plan", "source", "backing", "candidate", "report"):
        parser.add_argument(f"--{name}", type=Path, required=True)
    parser.add_argument("--dry", type=Path)
    parser.add_argument("--role", choices=("female", "male"), required=True)
    args = parser.parse_args()
    plan = json.loads(args.plan.read_text(encoding="utf-8"))
    source, rate = read(args.source)
    backing, _ = read(args.backing, rate)
    candidate, _ = read(args.candidate, rate)
    dry = read(args.dry, rate)[0] if args.dry else None
    report = audit(plan, source, backing, candidate, dry, rate, args.role)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                           encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))
    raise SystemExit(0 if report["coverage_passed"] else 1)


if __name__ == "__main__":
    main()
