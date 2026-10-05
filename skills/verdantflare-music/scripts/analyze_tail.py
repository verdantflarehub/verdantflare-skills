#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import struct
import wave


def decode_samples(payload: bytes, width: int) -> list[float]:
    if width == 1:
        return [(value - 128) / 128.0 for value in payload]
    if width == 2:
        count = len(payload) // 2
        return [value / 32768.0 for value in struct.unpack(f"<{count}h", payload)]
    if width == 3:
        values: list[float] = []
        for offset in range(0, len(payload), 3):
            raw = int.from_bytes(payload[offset : offset + 3], "little", signed=False)
            if raw & 0x800000:
                raw -= 1 << 24
            values.append(raw / 8388608.0)
        return values
    if width == 4:
        count = len(payload) // 4
        return [value / 2147483648.0 for value in struct.unpack(f"<{count}i", payload)]
    raise ValueError("only 8, 16, 24, and 32-bit PCM WAV are supported")


def db(value: float) -> float:
    return round(20.0 * math.log10(max(value, 1e-12)), 2)


def window_metrics(samples: list[float], count: int) -> dict[str, float]:
    selected = samples[-count:] if count < len(samples) else samples
    peak = max((abs(value) for value in selected), default=0.0)
    rms = math.sqrt(sum(value * value for value in selected) / max(len(selected), 1))
    return {"peak_dbfs": db(peak), "rms_dbfs": db(rms)}


parser = argparse.ArgumentParser(description="Detect short duration, hard cuts, and rapid tail collapse in PCM WAV candidates.")
parser.add_argument("audio", type=Path)
parser.add_argument("--planned-duration", type=float)
args = parser.parse_args()

with wave.open(str(args.audio), "rb") as source:
    if source.getcomptype() != "NONE":
        raise SystemExit("compressed WAV is not supported")
    channels = source.getnchannels()
    sample_rate = source.getframerate()
    width = source.getsampwidth()
    frames = source.getnframes()
    duration = frames / sample_rate
    tail_frames = min(frames, round(12 * sample_rate))
    source.setpos(frames - tail_frames)
    samples = decode_samples(source.readframes(tail_frames), width)

windows: dict[str, dict[str, float]] = {}
for seconds in (12.0, 4.0, 1.0, 0.25):
    sample_count = round(seconds * sample_rate * channels)
    windows[str(seconds)] = window_metrics(samples, sample_count)

warnings: list[str] = []
duration_ratio = None
if args.planned_duration:
    duration_ratio = duration / args.planned_duration
    if duration_ratio < 0.9:
        warnings.append("actual duration is below 90% of the approved duration budget")

tail_4_rms = windows["4.0"]["rms_dbfs"]
tail_1_rms = windows["1.0"]["rms_dbfs"]
if tail_4_rms > -24.0 and tail_4_rms - tail_1_rms >= 18.0:
    warnings.append("strong final four seconds collapse by at least 18 dB in the final second")
if windows["0.25"]["peak_dbfs"] > -12.0:
    warnings.append("final 250 ms retains high-level audio and may be hard-cut")

print(
    json.dumps(
        {
            "status": "warning" if warnings else "passed",
            "file": args.audio.name,
            "duration_seconds": round(duration, 6),
            "planned_duration_seconds": args.planned_duration,
            "duration_ratio": round(duration_ratio, 4) if duration_ratio is not None else None,
            "format": {
                "channels": channels,
                "sample_rate_hz": sample_rate,
                "bit_depth": width * 8,
            },
            "tail_windows": windows,
            "warnings": warnings,
            "human_review_required": True,
        },
        ensure_ascii=False,
        indent=2,
    )
)
