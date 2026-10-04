#!/usr/bin/env python3
"""Lift two low-rate singer estimates to complementary source-rate vocal stems."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import librosa
import numpy as np
from scipy.interpolate import RegularGridInterpolator
import soundfile as sf


def fit_estimate(audio: np.ndarray, length: int, rate: int) -> np.ndarray:
    if abs(len(audio) - length) > round(.03 * rate):
        raise ValueError("singer estimate differs from source interval by more than 30 ms")
    return np.pad(audio[:length], (0, max(0, length - len(audio))))


def lift(source: np.ndarray, source_rate: int, target_estimate: np.ndarray,
         other_estimate: np.ndarray, estimate_rate: int,
         power: float = 1.) -> tuple[np.ndarray, np.ndarray]:
    if source.ndim != 2 or source.shape[1] not in (1, 2):
        raise ValueError("source vocals must have one or two channels")
    if source_rate <= estimate_rate or estimate_rate <= 0:
        raise ValueError("source rate must exceed estimate rate")
    if power <= 0 or not np.isfinite(power):
        raise ValueError("mask power must be positive and finite")
    expected = round(len(source) * estimate_rate / source_rate)
    target_estimate = fit_estimate(target_estimate, expected, estimate_rate)
    other_estimate = fit_estimate(other_estimate, expected, estimate_rate)
    if not np.isfinite(source).all() or not np.isfinite(target_estimate).all() or not np.isfinite(other_estimate).all():
        raise ValueError("audio contains non-finite samples")

    low_fft, low_hop = 1024, 256
    target_mag = np.abs(librosa.stft(target_estimate, n_fft=low_fft,
                                      hop_length=low_hop)) ** power
    other_mag = np.abs(librosa.stft(other_estimate, n_fft=low_fft,
                                     hop_length=low_hop)) ** power
    mask = target_mag / (target_mag + other_mag + 1e-8)
    low_freq = librosa.fft_frequencies(sr=estimate_rate, n_fft=low_fft)
    low_time = librosa.frames_to_time(np.arange(mask.shape[1]),
                                       sr=estimate_rate, hop_length=low_hop)
    interpolate = RegularGridInterpolator((low_freq, low_time), mask,
                                           bounds_error=False, fill_value=None)

    full_fft, full_hop = 2048, 512
    full_freq = librosa.fft_frequencies(sr=source_rate, n_fft=full_fft)
    frame_count = 1 + len(source) // full_hop
    full_time = librosa.frames_to_time(np.arange(frame_count),
                                        sr=source_rate, hop_length=full_hop)
    grid = np.stack(np.meshgrid(np.clip(full_freq, 0, low_freq[-1]),
                                np.clip(full_time, 0, low_time[-1]),
                                indexing="ij"), axis=-1)
    full_mask = np.clip(interpolate(grid), .01, .99).astype("float32")
    target = np.empty_like(source)
    other = np.empty_like(source)
    for channel in range(source.shape[1]):
        spectrum = librosa.stft(source[:, channel], n_fft=full_fft,
                                 hop_length=full_hop)
        if spectrum.shape != full_mask.shape:
            raise ValueError("source STFT frame count does not match interpolated mask")
        target[:, channel] = librosa.istft(spectrum * full_mask,
                                             hop_length=full_hop, length=len(source))
        other[:, channel] = librosa.istft(spectrum * (1 - full_mask),
                                            hop_length=full_hop, length=len(source))
    return target, other


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("source-vocals", "target-estimate", "other-estimate",
                 "target-out", "other-out", "report"):
        parser.add_argument(f"--{name}", type=Path, required=True)
    parser.add_argument("--start-seconds", type=float, required=True)
    parser.add_argument("--end-seconds", type=float, required=True)
    parser.add_argument("--mask-power", type=float, default=1.)
    args = parser.parse_args()
    outputs = (args.target_out, args.other_out, args.report)
    if len(set(outputs)) != len(outputs) or any(path.exists() for path in outputs):
        raise FileExistsError("output paths must be distinct and must not exist")
    with sf.SoundFile(args.source_vocals) as source_file:
        rate = source_file.samplerate
        start, end = round(args.start_seconds * rate), round(args.end_seconds * rate)
        if start < 0 or end <= start or end > len(source_file):
            raise ValueError("invalid source interval")
        source_file.seek(start)
        source = source_file.read(end - start, dtype="float32", always_2d=True)
    target_estimate, target_rate = sf.read(args.target_estimate, dtype="float32")
    other_estimate, other_rate = sf.read(args.other_estimate, dtype="float32")
    if target_estimate.ndim != 1 or other_estimate.ndim != 1 or target_rate != other_rate:
        raise ValueError("estimates must be mono and share a sample rate")
    target, other = lift(source, rate, target_estimate, other_estimate,
                         target_rate, args.mask_power)
    report = {"source_interval_seconds": [start / rate, end / rate],
              "source_rate": rate, "estimate_rate": target_rate,
              "mask_power": args.mask_power,
              "reconstruction_rms": float(np.sqrt(np.mean((source - target - other) ** 2))),
              "target_out": str(args.target_out), "other_out": str(args.other_out),
              "identity_requires_listening": True}
    for path in outputs:
        path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(args.target_out, target, rate, subtype="PCM_24")
    sf.write(args.other_out, other, rate, subtype="PCM_24")
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2),
                           encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
