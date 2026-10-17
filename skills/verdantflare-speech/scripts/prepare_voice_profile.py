#!/usr/bin/env python3
"""Prepare a candidate profile from immutable reference bytes; no upload/inference."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import re
import struct

UUID7 = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}")
HASH = re.compile(r"[0-9a-f]{64}")
PCM_GUID = bytes.fromhex("0100000000001000800000aa00389b71")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def read_json(path):
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, "duplicate JSON key")
            result[key] = value
        return result

    def constant(_):
        raise ValueError("non-finite JSON number")

    return json.loads(Path(path).read_text(encoding="utf-8"), object_pairs_hook=pairs, parse_constant=constant)


def inspect_reference(data):
    """Strict PCM RIFF/WAV and WAVEX inspection, including truncated data chunks."""
    require(12 <= len(data) <= 2 << 20, "reference must be at most 2 MiB")
    require(data[:4] == b"RIFF" and data[8:12] == b"WAVE", "reference must be a PCM WAV; prepare a separate derivative first")
    require(struct.unpack_from("<I", data, 4)[0] + 8 == len(data), "RIFF size mismatch")
    cursor, chunks = 12, {}
    while cursor < len(data):
        require(cursor + 8 <= len(data), "truncated WAV chunk header")
        name, size = struct.unpack_from("<4sI", data, cursor)
        cursor += 8
        end = cursor + size
        require(end + (size & 1) <= len(data), "truncated WAV chunk")
        if name in (b"fmt ", b"data"):
            require(name not in chunks, "duplicate WAV audio chunk")
            chunks[name] = data[cursor:end]
        cursor = end + (size & 1)
    fmt, pcm = chunks.get(b"fmt ", b""), chunks.get(b"data", b"")
    require(len(fmt) >= 16 and len(pcm) > 0, "missing WAV format or audio")
    tag, channels, rate, byte_rate, block, bits = struct.unpack_from("<HHIIHH", fmt)
    if tag == 0xFFFE:
        require(len(fmt) >= 40, "invalid WAVEX format")
        extension, valid_bits, mask = struct.unpack_from("<HHI", fmt, 16)
        require(extension >= 22 and len(fmt) >= 18 + extension and valid_bits == bits and fmt[24:40] == PCM_GUID,
                "WAVEX must contain full-width integer PCM")
        require(mask in (0, 4), "WAVEX reference must use mono or unspecified channel layout")
    else:
        require(tag == 1, "reference must contain integer PCM")
    require(channels == 1 and bits in (16, 24), "reference must be mono PCM16 or PCM24")
    require(rate in (16000, 22050, 24000, 44100, 48000), "unsupported reference sample rate")
    width = bits // 8
    require(block == width and byte_rate == rate * block and len(pcm) % block == 0, "invalid PCM frame layout")
    frames = len(pcm) // block
    duration = frames / rate
    require(1 <= duration <= 15, "reference must be 1–15 seconds; do not silently crop")
    peak, clipped = 0, 0
    full_scale = 1 << (bits - 1)
    for offset in range(0, len(pcm), width):
        amplitude = abs(int.from_bytes(pcm[offset:offset + width], "little", signed=True))
        peak = max(peak, amplitude)
        clipped += amplitude >= full_scale - 1
    require(peak > 0, "reference is silent")
    return {"sha256": hashlib.sha256(data).hexdigest(), "size": len(data),
            "sample_rate_hz": rate, "channels": channels, "bit_depth": bits,
            "frames": frames, "duration_seconds": duration,
            "peak_dbfs": 20 * math.log10(peak / full_scale), "clipped_samples": clipped}


def prepare(data, content_ref, capabilities, voice_id, label):
    require(isinstance(voice_id, str) and re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", voice_id), "invalid voice_id")
    require(isinstance(label, str) and 1 <= len(label) <= 128 and label.strip(), "label must contain 1–128 characters")
    require(isinstance(content_ref, dict) and set(content_ref) == {"store_id", "artifact_id", "version_id"}, "expected a complete Artifact ContentRef")
    require(all(isinstance(v, str) and UUID7.fullmatch(v) for v in content_ref.values()), "ContentRef requires UUIDv7 values")
    require(isinstance(capabilities, dict) and capabilities.get("engine") == "indextts2", "expected actual speech.capabilities for indextts2")
    model_hash = capabilities.get("configured_model_manifest_sha256", "")
    require(isinstance(model_hash, str) and HASH.fullmatch(model_hash), "missing actual model manifest SHA-256")
    limits = capabilities.get("reference_seconds", {})
    require(isinstance(limits, dict), "invalid reference limits")
    bounds = [limits.get("min"), limits.get("max"), capabilities.get("reference_max_bytes")]
    require(all(type(v) in (int, float) and math.isfinite(v) and v > 0 for v in bounds), "missing finite reference limits")
    require(bounds[0] <= bounds[1], "invalid reference duration bounds")
    info = inspect_reference(data)
    require(bounds[0] <= info["duration_seconds"] <= bounds[1] and info["size"] <= bounds[2], "reference exceeds current capability limits")
    profile = {"schema_version": 1, "voice_id": voice_id, "label": label, "engine": "indextts2",
               "model_manifest_sha256": model_hash, "reference": {"content_ref": content_ref, "sha256": info["sha256"]},
               "candidate_state": "candidate", "listening_review": "pending"}
    intake = {"schema_version": 1, "project_path": f"speech/voices/{voice_id}/profile.json",
              "reference": info, "service_version": capabilities.get("service_version"),
              "ready_at_discovery": capabilities.get("ready") is True,
              "model_manifest_sha256": model_hash, "transcript": None,
              "processing": "none", "remote_membership_verified": False,
              "registration": "pending_project_commit_and_speech_voices_readback",
              "listening_review": "pending",
              "warnings": ["reference_contains_full_scale_samples"] if info["clipped_samples"] else []}
    return profile, intake


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference", required=True, type=Path)
    parser.add_argument("--content-ref", required=True, type=Path, help="JSON containing only store_id/artifact_id/version_id")
    parser.add_argument("--capabilities", required=True, type=Path, help="Actual unwrapped speech.capabilities result")
    parser.add_argument("--voice-id", required=True)
    parser.add_argument("--label", required=True)
    parser.add_argument("--out", required=True, type=Path, help="New output directory; existing directories are never overwritten")
    args = parser.parse_args()
    try:
        require(args.reference.stat().st_size <= 2 << 20, "reference exceeds 2 MiB")
        profile, intake = prepare(args.reference.read_bytes(), read_json(args.content_ref), read_json(args.capabilities), args.voice_id, args.label)
        args.out.mkdir(parents=True, exist_ok=False)
        for name, value in (("profile.json", profile), ("intake.json", intake)):
            with (args.out / name).open("x", encoding="utf-8") as stream:
                stream.write(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
        print(json.dumps({"prepared": True, "project_path": intake["project_path"], "registration": intake["registration"]}))
    except (OSError, ValueError, struct.error) as error:
        # OSError may contain personal paths. Do not echo it into task logs.
        print(json.dumps({"prepared": False, "error": str(error) if isinstance(error, ValueError) else type(error).__name__}))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
