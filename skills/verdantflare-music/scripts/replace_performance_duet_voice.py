#!/usr/bin/env python3
"""Replace timed phrases in a duet preview; output role-window diagnostic tracks."""

from __future__ import annotations

import argparse
from math import gcd
import json
from pathlib import Path

import numpy as np
from scipy.ndimage import gaussian_filter1d, uniform_filter1d
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
        raise ValueError("audio durations differ by more than 30 ms")
    result = np.zeros((length, audio.shape[1]), dtype="float32")
    size = min(length, len(audio))
    result[:size] = audio[:size]
    return result


def envelope(audio: np.ndarray, hop: int) -> np.ndarray:
    mono = audio.mean(axis=1)
    frames = mono[:len(mono) // hop * hop].reshape(-1, hop)
    return np.sqrt(uniform_filter1d(np.mean(frames * frames, axis=1), size=12) + 1e-10)


def samples(gain: np.ndarray, length: int, hop: int) -> np.ndarray:
    centers = (np.arange(len(gain)) + .5) * hop
    return np.interp(np.arange(length), centers, gain,
                     left=gain[0], right=gain[-1]).astype("float32")


def match_vocal(reference: np.ndarray, candidate: np.ndarray, hop: int) -> np.ndarray:
    ref, cur = envelope(reference, hop), envelope(candidate, hop)
    gain = np.clip(ref / cur, .08, 2.5)
    gain[(ref < 10 ** (-42 / 20)) & (cur < 10 ** (-55 / 20))] = 1.
    return samples(gaussian_filter1d(gain, sigma=4.), len(candidate), hop)


def match_mix(reference: np.ndarray, replacement: np.ndarray, hop: int) -> np.ndarray:
    ref, cur = envelope(reference, hop), envelope(replacement, hop)
    gain_db = np.clip(20 * np.log10(ref / cur), -6., 2.5)
    gain_db[ref < 10 ** (-36 / 20)] = 0.
    return samples(10 ** (gaussian_filter1d(gain_db, sigma=5.) / 20),
                   len(reference), hop)


def mask(length: int, fade: int) -> np.ndarray:
    result = np.ones(length, dtype="float32")
    fade = min(fade, length // 3)
    if fade:
        edge = np.sin(np.linspace(0, np.pi / 2, fade)) ** 2
        result[:fade] = edge
        result[-fade:] = edge[::-1]
    return result


def assemble(plan: dict, baseline: np.ndarray, backing: np.ndarray,
             other: np.ndarray, converted: np.ndarray, role: str,
             rate: int) -> dict[str, np.ndarray]:
    if plan.get("schema_version") != "duet-performance-v1":
        raise ValueError("plan must use duet-performance-v1")
    if role not in ("female", "male"):
        raise ValueError("role must be female or male")
    if abs(len(baseline) / rate - plan.get("duration_seconds", 0)) > .03:
        raise ValueError("plan duration differs from audio")
    if baseline.shape[1] != 2:
        raise ValueError("baseline must be stereo")
    if any(track.shape != baseline.shape for track in (backing, other)):
        raise ValueError("baseline, backing and other voice must be aligned stereo")
    converted = fit(converted, len(baseline), rate)
    if converted.shape[1] not in (1, 2):
        raise ValueError("converted voice must be mono or stereo")
    converted = converted.mean(axis=1)[:, None]

    preview = baseline.copy()
    target = np.zeros_like(baseline)
    hop = round(.01 * rate)
    previous_end = 0.
    count = 0
    for line in plan["lines"]:
        start, end = float(line["start_seconds"]), float(line["end_seconds"])
        if start < previous_end - .005 or end - start < .25:
            raise ValueError("plan lines overlap or have invalid times")
        if line["voice"] not in ("female", "male", "both") or not str(line.get("text", "")).strip():
            raise ValueError("plan line has invalid role or empty lyric")
        previous_end = end
        if line["voice"] not in (role, "both"):
            continue
        a, b = round(start * rate), round(end * rate)
        if a < 0 or b > len(preview):
            raise ValueError("plan line extends beyond audio")
        original = preview[a:b].copy()
        wet = original - backing[a:b]
        if line["voice"] == "both":
            wet -= other[a:b]
        voice = converted[a:b, 0].copy()
        voice *= match_vocal(wet, voice[:, None], hop)
        if role == "male":
            proposed = np.column_stack((voice * .91, voice * .97))
        else:
            proposed = np.column_stack((voice * .97, voice * .91))
        replacement = backing[a:b] + proposed
        if line["voice"] == "both":
            replacement += other[a:b]
        correction = match_mix(original, replacement, hop)
        proposed *= correction[:, None]
        replacement *= correction[:, None]
        blend = mask(b - a, round(.085 * rate))[:, None]
        preview[a:b] = original * (1 - blend) + replacement * blend
        target[a:b] = proposed * blend
        count += 1
    if count == 0:
        raise ValueError(f"plan has no {role} phrases")

    peak = float(np.max(np.abs(preview)))
    safety = min(1., .95 / max(peak, 1e-6))
    result = {"Preview": preview * safety,
              "Instrumental": backing * safety,
              role.title(): target * safety,
              ("Female" if role == "male" else "Male"): other * safety}
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("plan", "baseline", "backing", "other-voice",
                 "converted", "output-prefix"):
        parser.add_argument(f"--{name}", type=Path, required=True)
    parser.add_argument("--role", choices=("female", "male"), required=True)
    args = parser.parse_args()
    plan = json.loads(args.plan.read_text(encoding="utf-8"))
    baseline, rate = read(args.baseline)
    backing = fit(read(args.backing, rate)[0], len(baseline), rate)
    other = fit(read(args.other_voice, rate)[0], len(baseline), rate)
    converted = read(args.converted, rate)[0]
    result = assemble(plan, baseline, backing, other, converted, args.role, rate)
    outputs = {name: Path(f"{args.output_prefix}_{name}.wav") for name in result}
    if any(path.exists() for path in outputs.values()):
        raise FileExistsError("one or more output files already exist")
    for name, path in outputs.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        sf.write(path, result[name], rate, subtype="PCM_24")
        print(path, f"{len(result[name]) / rate:.3f}s")


if __name__ == "__main__":
    main()
