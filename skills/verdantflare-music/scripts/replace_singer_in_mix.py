#!/usr/bin/env python3
"""Replace one singer on an aligned original mix without rebuilding its backing."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import soundfile as sf

from replace_performance_duet_voice import match_vocal


def rms(audio: np.ndarray) -> float:
    return float(np.sqrt(np.mean(np.square(audio, dtype="float64"))))


def phrases_from_plan(plan: dict, role: str, length: int, rate: int) -> list[dict]:
    if role not in ("female", "male"):
        raise ValueError("role must be female or male")
    if abs(float(plan.get("duration_seconds", 0)) - length / rate) > .03:
        raise ValueError("plan duration differs from source mix")
    phrases = plan.get(f"{role}_phrases")
    if not isinstance(phrases, list) or not phrases:
        raise ValueError(f"plan needs nonempty {role}_phrases")
    previous_end = 0
    result = []
    for phrase in phrases:
        start, end = float(phrase["start"]), float(phrase["end"])
        if not np.isfinite([start, end]).all():
            raise ValueError("phrase times must be finite")
        a, b = round(start * rate), round(end * rate)
        if a < previous_end or b <= a or b > length:
            raise ValueError("phrases overlap or extend beyond source mix")
        max_gain = float(phrase.get("max_gain", 1.))
        if not 1 <= max_gain <= 1.5:
            raise ValueError("max_gain must be between 1 and 1.5")
        previous_end = b
        result.append({**phrase, "start_sample": a, "end_sample": b,
                       "max_gain": max_gain})
    return result


def edge_mask(length: int, fade: int) -> np.ndarray:
    window = np.ones(length, dtype="float32")
    fade = min(fade, length // 3)
    if fade:
        edge = np.sin(np.linspace(0, np.pi / 2, fade)) ** 2
        window[:fade] = edge
        window[-fade:] = edge[::-1]
    return window


def level_gain(retained: np.ndarray, replacement: np.ndarray,
               original: np.ndarray, cap: float) -> float:
    if cap == 1:
        return 1.
    quadratic = float(np.mean(replacement.astype("float64") ** 2))
    linear = float(2 * np.mean(retained.astype("float64") * replacement))
    constant = float(np.mean(retained.astype("float64") ** 2)
                     - np.mean(original.astype("float64") ** 2))
    discriminant = linear * linear - 4 * quadratic * constant
    if quadratic < 1e-10 or discriminant < 0:
        return 1.
    desired = (-linear + np.sqrt(discriminant)) / (2 * quadratic)
    return float(np.clip(desired, 1., cap))


def assemble(source: np.ndarray, target: np.ndarray, converted: np.ndarray,
             rate: int, phrases: list[dict], wet: np.ndarray | None = None,
             wet_gain: float = 0., fade_seconds: float = .1,
             pan: tuple[float, float] = (.95, 1.)) -> tuple[np.ndarray, dict]:
    if source.ndim != 2 or source.shape[1] != 2 or target.shape != source.shape:
        raise ValueError("source mix and target singer must be aligned stereo")
    if converted.ndim != 2 or len(converted) != len(source) or converted.shape[1] not in (1, 2):
        raise ValueError("converted singer must align exactly and be mono or stereo")
    if wet is not None and (wet.ndim != 2 or len(wet) != len(source)
                            or wet.shape[1] not in (1, 2)):
        raise ValueError("wet singer must align exactly and be mono or stereo")
    if not np.isfinite([wet_gain, fade_seconds, *pan]).all() or wet_gain < 0 or fade_seconds < 0:
        raise ValueError("invalid gain or fade")
    if any(not np.isfinite(audio).all() for audio in (source, target, converted)
           if audio is not None) or (wet is not None and not np.isfinite(wet).all()):
        raise ValueError("audio contains non-finite samples")

    mono = converted.mean(axis=1)
    envelope_gain = match_vocal(target.mean(axis=1)[:, None], mono[:, None],
                                max(1, round(.01 * rate)))
    if converted.shape[1] == 1:
        replacement = np.column_stack((mono * pan[0], mono * pan[1]))
    else:
        replacement = converted.copy()
    replacement *= envelope_gain[:, None]
    if wet is not None and wet_gain:
        replacement += wet_gain * (np.repeat(wet, 2, axis=1) if wet.shape[1] == 1 else wet)

    result = source.copy()
    mask = np.zeros(len(source), dtype="float32")
    rows = []
    for phrase in phrases:
        a, b = phrase["start_sample"], phrase["end_sample"]
        if a < 0 or b > len(source) or b <= a or np.any(mask[a:b]):
            raise ValueError("invalid or overlapping replacement phrase")
        window = edge_mask(b - a, round(fade_seconds * rate))
        mask[a:b] = window
        retained = source[a:b] - target[a:b] * window[:, None]
        new_voice = replacement[a:b] * window[:, None]
        gain = level_gain(retained, new_voice, source[a:b], phrase["max_gain"])
        result[a:b] = retained + gain * new_voice
        rows.append({"start": a / rate, "end": b / rate,
                     "source": phrase.get("source"),
                     "applied_gain": gain,
                     "mix_rms_shift_db": float(20 * np.log10(
                         max(rms(result[a:b]), 1e-8) / max(rms(source[a:b]), 1e-8)))})

    untouched = mask == 0
    deviation = []
    span = round(.15 * rate)
    for phrase in phrases:
        for at in (phrase["start_sample"], phrase["end_sample"]):
            if at < span or at + span > len(source):
                continue
            before_original = rms(source[at - span:at])
            after_original = rms(source[at:at + span])
            before_new = rms(result[at - span:at])
            after_new = rms(result[at:at + span])
            old_change = 20 * np.log10(max(after_original, 1e-8) / max(before_original, 1e-8))
            new_change = 20 * np.log10(max(after_new, 1e-8) / max(before_new, 1e-8))
            deviation.append({"time": at / rate,
                              "change_deviation_db": float(new_change - old_change)})
    report = {"sample_rate": rate, "duration_seconds": len(result) / rate,
              "peak": float(np.max(np.abs(result))),
              "untouched_max_difference": float(np.max(np.abs(result[untouched] - source[untouched])))
              if np.any(untouched) else 0.,
              "phrases": rows, "boundaries": deviation,
              "human_review_required": True}
    if report["peak"] >= 1:
        raise ValueError("replacement mix reaches digital clipping")
    return result, report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("plan", "source-mix", "target-singer", "converted", "output", "report"):
        parser.add_argument(f"--{name}", type=Path, required=True)
    parser.add_argument("--role", choices=("female", "male"), required=True)
    parser.add_argument("--wet", type=Path)
    parser.add_argument("--wet-gain", type=float, default=0.)
    parser.add_argument("--fade-seconds", type=float, default=.1)
    args = parser.parse_args()
    if args.output == args.report or args.output.exists() or args.report.exists():
        raise FileExistsError("output and report must be distinct new paths")
    plan = json.loads(args.plan.read_text(encoding="utf-8"))
    source, rate = sf.read(args.source_mix, dtype="float32", always_2d=True)
    target, target_rate = sf.read(args.target_singer, dtype="float32", always_2d=True)
    converted, converted_rate = sf.read(args.converted, dtype="float32", always_2d=True)
    if target_rate != rate or converted_rate != rate:
        raise ValueError("all tracks must have the same sample rate")
    wet = None
    if args.wet:
        wet, wet_rate = sf.read(args.wet, dtype="float32", always_2d=True)
        if wet_rate != rate:
            raise ValueError("wet track sample rate differs")
    phrases = phrases_from_plan(plan, args.role, len(source), rate)
    mixed, report = assemble(source, target, converted, rate, phrases, wet,
                              args.wet_gain, args.fade_seconds)
    for path in (args.output, args.report):
        path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(args.output, mixed, rate, subtype="PCM_24")
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
