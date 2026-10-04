#!/usr/bin/env python3
"""Match a new separated backing to an approved duet preview's level automation."""

from __future__ import annotations

import argparse
import json
from math import gcd
from pathlib import Path

import numpy as np
from scipy.ndimage import gaussian_filter1d
from scipy.signal import resample_poly
import soundfile as sf


def read(path: Path, rate: int | None = None) -> tuple[np.ndarray, int]:
    audio, actual_rate = sf.read(path, dtype="float32", always_2d=True)
    if rate is not None and actual_rate != rate:
        divisor = gcd(actual_rate, rate)
        audio = resample_poly(audio, rate // divisor, actual_rate // divisor, axis=0)
        actual_rate = rate
    return audio, actual_rate


def fit(audio: np.ndarray, length: int, rate: int) -> np.ndarray:
    if abs(len(audio) - length) > round(.03 * rate):
        raise ValueError("backing durations differ by more than 30 ms")
    result = np.zeros((length, audio.shape[1]), dtype="float32")
    count = min(length, len(audio))
    result[:count] = audio[:count]
    return result


def prepare(source: np.ndarray, approved: np.ndarray, replacement: np.ndarray,
            rate: int, start: float = 0., end: float | None = None
            ) -> tuple[np.ndarray, dict]:
    if source.shape != approved.shape or replacement.shape != source.shape:
        raise ValueError("all backings must be time-aligned stereo")
    if source.ndim != 2 or source.shape[1] != 2:
        raise ValueError("all backings must be stereo")
    if not all(np.isfinite(track).all() for track in (source, approved, replacement)):
        raise ValueError("backings must contain only finite samples")

    hop = max(1, round(.01 * rate))
    count = len(source) // hop
    if count < 100:
        raise ValueError("at least one second of backing is required")
    src = source[:count * hop].reshape(count, hop, 2)
    ref = approved[:count * hop].reshape(count, hop, 2)
    energy = np.sum(src * src, axis=(1, 2))
    active = energy > hop * 2 * 1e-7
    if not active.any():
        raise ValueError("source backing is silent")
    gain = np.sum(src * ref, axis=(1, 2)) / np.maximum(energy, 1e-9)
    gain[~active] = np.interp(np.flatnonzero(~active), np.flatnonzero(active), gain[active])
    gain = gaussian_filter1d(gain, 1.)
    centers = (np.arange(count) + .5) * hop
    gain = np.interp(np.arange(len(source)), centers, gain,
                     left=gain[0], right=gain[-1]).astype("float32")
    error = approved - source * gain[:, None]
    relative_error = float(20 * np.log10(max(float(np.sqrt(np.mean(error * error))), 1e-9)
                                       / max(float(np.sqrt(np.mean(approved * approved))), 1e-9)))
    if relative_error > -35:
        raise ValueError(f"approved backing is not a level-automated source ({relative_error:.1f} dB)")

    duration = len(source) / rate
    stop = duration if end is None else end
    if start < 0 or stop > duration + .001 or stop - start < 1.:
        raise ValueError("calibration interval must cover at least one second within the song")
    ratios = []
    for index in range(round(start * rate), min(round(stop * rate), len(source)) - rate + 1, rate):
        original = source[index:index + rate]
        new = replacement[index:index + rate]
        original_power = float(np.sum(original * original))
        new_power = float(np.sum(new * new))
        if original_power / original.size < 1e-5 or new_power <= 0:
            continue
        correlation = float(np.sum(original * new) / np.sqrt(original_power * new_power))
        if correlation > .98:
            ratios.append(np.sqrt(original_power / new_power))
    if not ratios:
        raise ValueError("no active, highly correlated passage for separator calibration")
    scale = float(np.median(ratios))
    result = replacement * (gain[:, None] * scale)
    report = {
        "sample_rate": rate,
        "duration_seconds": round(duration, 3),
        "calibration_windows": len(ratios),
        "replacement_scale": round(scale, 6),
        "approved_gain_reconstruction_db": round(relative_error, 2),
        "qualification_scope": "backing_alignment_and_level_only",
        "human_review_required": True,
    }
    return result, report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("source-backing", "approved-backing", "replacement-backing", "output"):
        parser.add_argument(f"--{name}", type=Path, required=True)
    parser.add_argument("--calibration-start", type=float, default=0.)
    parser.add_argument("--calibration-end", type=float)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    if args.output.exists() or (args.report and args.report.exists()):
        raise FileExistsError("output or report already exists")
    approved, rate = read(args.approved_backing)
    source = fit(read(args.source_backing, rate)[0], len(approved), rate)
    replacement = fit(read(args.replacement_backing, rate)[0], len(approved), rate)
    result, report = prepare(source, approved, replacement, rate,
                             args.calibration_start, args.calibration_end)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    sf.write(args.output, result, rate, subtype="PCM_24")
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                               encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
