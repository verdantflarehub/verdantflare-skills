#!/usr/bin/env python3
"""Validate a scored two-singer production probe before mixing or full-song work.

The validator checks the machine-verifiable contract only. Human listening is
still required for diction, naturalness, singer identity, and musicality.
"""

from __future__ import annotations

import argparse
from array import array
import json
import math
import struct
import sys
import wave
from pathlib import Path
from typing import Any, Iterable


ROLES = {"female", "male", "both", "instrumental"}
ACTIVE_DBFS = -55.0
SILENT_DBFS = -58.0
MAX_DURATION_ERROR = 0.025
FRAME_SECONDS = 0.05
SCHEMA_VERSION = "duet-score-v1"
MAX_BOTH_IMBALANCE_DB = 18.0
DEFAULT_BOTH_RATIO = 0.18
DEFAULT_FIRST_BOTH_RATIO = 0.65


def fail(errors: list[str], message: str) -> None:
    errors.append(message)


def clean_text(value: str) -> str:
    return "".join(value.split())


def note_interval(note: Any) -> tuple[float, float] | None:
    try:
        return float(note["start_seconds"]), float(note["end_seconds"])
    except (KeyError, TypeError, ValueError):
        return None


def read_wav(path: Path) -> tuple[int, int, list[float]]:
    try:
        with wave.open(str(path), "rb") as source:
            channels = source.getnchannels()
            rate = source.getframerate()
            width = source.getsampwidth()
            frames = source.getnframes()
            raw = source.readframes(frames)
        format_tag = 1
    except wave.Error:
        blob = path.read_bytes()
        if blob[:4] != b"RIFF" or blob[8:12] != b"WAVE":
            raise
        cursor = 12
        fmt = data = None
        while cursor + 8 <= len(blob):
            chunk_id, size = struct.unpack_from("<4sI", blob, cursor)
            cursor += 8
            chunk = blob[cursor:cursor + size]
            cursor += size + (size & 1)
            if chunk_id == b"fmt ":
                fmt = chunk
            elif chunk_id == b"data":
                data = chunk
        if fmt is None or data is None or len(fmt) < 16:
            raise ValueError(f"invalid WAV chunks in {path}")
        format_tag, channels, rate, _, _, width_bits = struct.unpack_from("<HHIIHH", fmt)
        if format_tag != 3 or width_bits != 32:
            raise ValueError(f"unsupported WAV format tag {format_tag} in {path}")
        width = 4
        raw = data
        frames = len(raw) // (width * channels)
    if width not in (1, 2, 3, 4):
        raise ValueError(f"unsupported PCM width {width} in {path}")
    if format_tag == 3:
        values = array("f")
        values.frombytes(raw)
        samples = [sum(values[offset + channel] for channel in range(channels)) / max(channels, 1)
                   for offset in range(0, len(values), channels)]
        return rate, channels, samples
    scale = float(1 << (width * 8 - 1))
    samples: list[float] = []
    step = width * channels
    for offset in range(0, len(raw), step):
        total = 0
        for channel in range(channels):
            start = offset + channel * width
            chunk = raw[start : start + width]
            if len(chunk) != width:
                continue
            if width == 1:
                value = chunk[0] - 128
            else:
                value = int.from_bytes(chunk, "little", signed=True)
            total += value / scale
        samples.append(total / max(channels, 1))
    return rate, channels, samples


def dbfs(samples: Iterable[float]) -> float:
    values = list(samples)
    if not values:
        return -120.0
    rms = math.sqrt(sum(sample * sample for sample in values) / len(values))
    return 20.0 * math.log10(max(rms, 1e-9))


def window_dbfs(samples: list[float], rate: int, start: float, end: float) -> float:
    left = max(0, round(start * rate))
    right = min(len(samples), round(end * rate))
    return dbfs(samples[left:right])


def active_frame_ratio(samples: list[float], rate: int, start: float, end: float) -> float:
    """Return the fraction of short frames that contain audible vocal energy."""
    left = max(0, round(start * rate))
    right = min(len(samples), round(end * rate))
    if right <= left:
        return 0.0
    frame_size = max(1, round(FRAME_SECONDS * rate))
    frames = [samples[pos : min(pos + frame_size, right)]
              for pos in range(left, right, frame_size)]
    if not frames:
        return 0.0
    return sum(dbfs(frame) >= ACTIVE_DBFS for frame in frames) / len(frames)


def normalize_line(line: dict[str, Any], index: int, errors: list[str]) -> tuple[float, float, str, str]:
    try:
        start = float(line["start_seconds"])
        end = float(line["end_seconds"])
        voice = str(line["voice"])
        text = str(line["text"])
    except (KeyError, TypeError, ValueError) as exc:
        fail(errors, f"line {index}: missing start_seconds/end_seconds/voice/text ({exc})")
        return 0.0, 0.0, "", ""
    if voice not in ROLES:
        fail(errors, f"line {index}: invalid voice role {voice!r}")
    if end <= start:
        fail(errors, f"line {index}: end_seconds must be greater than start_seconds")
    if voice != "instrumental" and not clean_text(text):
        fail(errors, f"line {index}: empty text")
    return start, end, voice, text


def chord_pcs(root: str, quality: str) -> set[int]:
    names = {"C": 0, "C#": 1, "Db": 1, "D": 2, "D#": 3, "Eb": 3,
             "E": 4, "F": 5, "F#": 6, "Gb": 6, "G": 7, "G#": 8,
             "Ab": 8, "A": 9, "A#": 10, "Bb": 10, "B": 11}
    root_pc = names.get(root.rstrip("0123456789"))
    if root_pc is None:
        return set()
    intervals = {"": (0, 4, 7), "maj": (0, 4, 7), "major": (0, 4, 7),
                 "min": (0, 3, 7), "minor": (0, 3, 7), "m": (0, 3, 7),
                 "dim": (0, 3, 6), "sus2": (0, 2, 7),
                 "sus4": (0, 5, 7), "7": (0, 4, 7, 10),
                 "maj7": (0, 4, 7, 11), "min7": (0, 3, 7, 10),
                 "min11": (0, 2, 3, 5, 7, 10)}
    return {(root_pc + interval) % 12 for interval in intervals.get(quality, ())}


def validate_schedule(plan: dict[str, Any], lines: list[dict[str, Any]], errors: list[str]) -> None:
    duration = plan.get("duration_seconds")
    if not isinstance(duration, (int, float)) or not math.isfinite(float(duration)) or duration <= 0:
        fail(errors, "duration_seconds must be a positive number")
        return
    if not lines:
        return
    if abs(lines[0]["start"] - 0.0) > MAX_DURATION_ERROR:
        fail(errors, "line schedule must start at 0s; use an instrumental line for an intro")
    if abs(lines[-1]["end"] - float(duration)) > MAX_DURATION_ERROR:
        fail(errors, f"line schedule ends at {lines[-1]['end']:.3f}s but plan lasts {float(duration):.3f}s")
    for index, line in enumerate(lines):
        if line["start"] < -MAX_DURATION_ERROR or line["end"] > float(duration) + MAX_DURATION_ERROR:
            fail(errors, f"line {index}: window falls outside the plan duration")
        if index and line["start"] < lines[index - 1]["end"] - MAX_DURATION_ERROR:
            fail(errors, f"line {index}: overlapping role windows; use one 'both' line for intentional unison")
        if index and line["start"] > lines[index - 1]["end"] + MAX_DURATION_ERROR:
            fail(errors, f"line {index}: unplanned gap; add an explicit instrumental line")
    roles = {line["voice"] for line in lines}
    for role in ("female", "male", "both"):
        if role not in roles:
            fail(errors, f"line schedule must contain a {role} section")


def validate_score(plan: dict[str, Any], lines: list[dict[str, Any]], errors: list[str]) -> None:
    if plan.get("schema_version") != SCHEMA_VERSION:
        fail(errors, f"plan schema_version must be {SCHEMA_VERSION!r}")
    chords = plan.get("chords")
    melody = plan.get("melody")
    if not isinstance(chords, list) or not chords:
        fail(errors, "score missing chords: accompaniment harmony must be explicit")
    if not isinstance(melody, dict) or not melody.get("female") or not melody.get("male"):
        fail(errors, "score missing independent female and male melody note lines")
    if not isinstance(chords, list) or not isinstance(melody, dict):
        return

    try:
        duration = float(plan.get("duration_seconds", 0.0))
    except (TypeError, ValueError):
        return
    cursor = 0.0
    chord_map: list[tuple[float, float, set[int]]] = []
    previous_chord_end = 0.0
    for index, chord in enumerate(chords):
        try:
            start = float(chord["start_seconds"])
            end = float(chord["end_seconds"])
            tones = chord_pcs(str(chord["root"]), str(chord.get("quality", "")))
        except (KeyError, TypeError, ValueError) as exc:
            fail(errors, f"chord {index}: invalid fields ({exc})")
            continue
        if start < -MAX_DURATION_ERROR or end > duration + MAX_DURATION_ERROR:
            fail(errors, f"chord {index}: range falls outside plan duration")
        if end <= start or not tones:
            fail(errors, f"chord {index}: invalid range or unknown chord")
        if start > cursor + MAX_DURATION_ERROR:
            fail(errors, f"chords have a gap before {start:.3f}s")
        cursor = max(cursor, end)
        chord_map.append((start, end, tones))
        if index and start < previous_chord_end - MAX_DURATION_ERROR:
            fail(errors, f"chord {index}: overlaps the previous chord")
        previous_chord_end = max(previous_chord_end, end)
    if chord_map and chord_map[0][0] > MAX_DURATION_ERROR:
        fail(errors, "chords must start at 0s")
    if duration and cursor < duration - MAX_DURATION_ERROR:
        fail(errors, f"chords end at {cursor:.3f}s but plan lasts {duration:.3f}s")

    for role in ("female", "male"):
        notes = melody.get(role, [])
        if not isinstance(notes, list) or not notes:
            fail(errors, f"{role} melody must contain at least one note")
            continue
        lyric_parts: list[str] = []
        previous_end = 0.0
        for index, note in enumerate(notes):
            if not isinstance(note, dict):
                fail(errors, f"{role} note {index}: expected object")
                continue
            lyric_value = str(note.get("lyric", ""))
            if not lyric_value and not note.get("rest", False):
                fail(errors, f"{role} note {index}: lyric is required (use rest=true for a rest)")
            if not note.get("rest", False):
                lyric_parts.append(lyric_value)
            try:
                note_start = float(note["start_seconds"])
                note_end = float(note["end_seconds"])
            except (KeyError, TypeError, ValueError):
                continue
            if note_start < previous_end - MAX_DURATION_ERROR:
                fail(errors, f"{role} note {index}: notes overlap or are out of order")
            if note_start < -MAX_DURATION_ERROR or note_end > duration + MAX_DURATION_ERROR:
                fail(errors, f"{role} note {index}: range falls outside plan duration")
            previous_end = max(previous_end, note_end)
        lyric = clean_text("".join(lyric_parts))
        expected = clean_text("".join(line["text"] for line in lines
                                      if line["voice"] in (role, "both")))
        if lyric != expected:
            fail(errors, f"{role} melody lyrics do not exactly match role plan")
        role_lines = [(index, line) for index, line in enumerate(lines)
                      if line["voice"] in (role, "both")]
        for line_index, line in role_lines:
            if not any(
                isinstance(note, dict)
                and not note.get("rest", False)
                and (interval := note_interval(note)) is not None
                and interval[0] < line["end"]
                and interval[1] > line["start"]
                for note in notes
                if isinstance(note, dict)
            ):
                fail(errors, f"{role}: no melody note lands in line {line_index}")
        for index, note in enumerate(notes):
            if not isinstance(note, dict):
                continue
            try:
                start = float(note["start_seconds"])
                end = float(note["end_seconds"])
            except (KeyError, TypeError, ValueError) as exc:
                fail(errors, f"{role} note {index}: invalid fields ({exc})")
                continue
            if note.get("rest", False):
                midi = 0.0
            else:
                try:
                    midi = float(note["midi"])
                except (KeyError, TypeError, ValueError) as exc:
                    fail(errors, f"{role} note {index}: invalid MIDI value ({exc})")
                    continue
            if end <= start or not 0 <= midi <= 127:
                fail(errors, f"{role} note {index}: invalid range or MIDI value")
            if midi <= 0:
                continue
            containing_lines = [line for line in lines
                                if line["voice"] in (role, "both")
                                and start >= line["start"] - MAX_DURATION_ERROR
                                and end <= line["end"] + MAX_DURATION_ERROR]
            if not containing_lines:
                fail(errors, f"{role} note {index}: note is outside its singer's line windows")
            overlaps = [tones for left, right, tones in chord_map
                        if start < right and end > left]
            is_passing = bool(note.get("passing", False))
            if overlaps and not is_passing and not any(int(round(midi)) % 12 in tones for tones in overlaps):
                fail(errors, f"{role} note {index}: no chord-tone support at {start:.3f}s")


def validate_duet_template(plan: dict[str, Any], lines: list[dict[str, Any]], errors: list[str]) -> None:
    """Apply optional vocal-architecture constraints beyond the base score contract."""
    template = plan.get("duet_template")
    if template is None:
        return
    if template != "call_response_final_merge":
        fail(errors, f"unknown duet_template {template!r}")
        return
    duration = float(plan.get("duration_seconds", 0.0) or 0.0)
    voiced = [line for line in lines if line["voice"] != "instrumental"]
    both = [line for line in voiced if line["voice"] == "both"]
    if not both:
        fail(errors, "call_response_final_merge requires at least one both line")
        return
    voiced_duration = sum(max(0.0, line["end"] - line["start"]) for line in voiced)
    both_duration = sum(max(0.0, line["end"] - line["start"]) for line in both)
    policy = plan.get("both_policy")
    if not isinstance(policy, dict):
        policy = {}
    try:
        max_ratio = float(policy.get("max_voiced_ratio", DEFAULT_BOTH_RATIO))
        first_ratio = float(policy.get("first_both_min_ratio", DEFAULT_FIRST_BOTH_RATIO))
    except (TypeError, ValueError):
        fail(errors, "both_policy ratios must be numbers")
        return
    if not 0.0 < max_ratio <= 1.0:
        fail(errors, "both_policy.max_voiced_ratio must be between 0 and 1")
    if not 0.0 <= first_ratio <= 1.0:
        fail(errors, "both_policy.first_both_min_ratio must be between 0 and 1")
    if voiced_duration and both_duration / voiced_duration > max_ratio + 1e-9:
        fail(errors, f"both coverage is {both_duration / voiced_duration:.3f}, above the {max_ratio:.3f} limit")
    first_both = min(line["start"] for line in both)
    if duration and first_both / duration < first_ratio - 1e-9:
        fail(errors, f"first both line starts at {first_both / duration:.3f} of the song, before the {first_ratio:.3f} merge point")
    allowed_modes = policy.get("allowed_modes", ["unison", "third", "sixth", "lead_with_tail_harmony"])
    if not isinstance(allowed_modes, list):
        fail(errors, "both_policy.allowed_modes must be a list")
        allowed_modes = []
    for index, line in enumerate(both):
        mode = line.get("harmony_mode")
        if mode not in allowed_modes:
            fail(errors, f"both line {index} must declare an allowed harmony_mode")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", required=True, type=Path)
    parser.add_argument("--female", required=True, type=Path)
    parser.add_argument("--male", required=True, type=Path)
    parser.add_argument("--instrumental", type=Path)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()

    errors: list[str] = []
    try:
        plan = json.loads(args.plan.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        fail(errors, f"cannot read plan: {exc}")
        plan = {}
    if not isinstance(plan, dict):
        fail(errors, "plan root must be an object")
        plan = {}
    lines_raw = plan.get("lines")
    if not isinstance(lines_raw, list) or not lines_raw:
        fail(errors, "plan requires a non-empty lines array")
        lines_raw = []
    lines: list[dict[str, Any]] = []
    for index, raw in enumerate(lines_raw):
        if not isinstance(raw, dict):
            fail(errors, f"line {index}: expected object")
            continue
        start, end, voice, text = normalize_line(raw, index, errors)
        normalized = {"start": start, "end": end, "voice": voice, "text": text}
        for field in ("harmony_mode", "harmony_interval_semitones"):
            if field in raw:
                normalized[field] = raw[field]
        lines.append(normalized)
    validate_schedule(plan, lines, errors)
    validate_duet_template(plan, lines, errors)
    validate_score(plan, lines, errors)

    try:
        duration = float(plan.get("duration_seconds", 0.0) or 0.0)
    except (TypeError, ValueError):
        duration = 0.0
    measurements: dict[str, Any] = {}
    audio: dict[str, tuple[int, int, list[float]]] = {}
    for role, path in (("female", args.female), ("male", args.male)):
        try:
            rate, channels, samples = read_wav(path)
        except (OSError, ValueError, wave.Error) as exc:
            fail(errors, f"{role}: cannot read WAV: {exc}")
            continue
        if rate <= 0 or channels <= 0:
            fail(errors, f"{role}: invalid sample rate or channel count")
        expected_rate = plan.get("sample_rate")
        if expected_rate is not None and rate != expected_rate:
            fail(errors, f"{role}: sample rate {rate} differs from plan {expected_rate}")
        audio[role] = (rate, channels, samples)
        actual_duration = len(samples) / rate if rate else 0.0
        if duration and abs(actual_duration - duration) > MAX_DURATION_ERROR:
            fail(errors, f"{role}: duration {actual_duration:.3f}s differs from plan {duration:.3f}s")
        if max((abs(sample) for sample in samples), default=0.0) >= 0.999:
            fail(errors, f"{role}: clipped sample")
        expected_windows = [(line["start"], line["end"])
                            for line in lines if line["voice"] in (role, "both")]
        expected_indexes = [index for index, line in enumerate(lines)
                            if line["voice"] in (role, "both")]
        forbidden_windows = [(line["start"], line["end"])
                             for line in lines if line["voice"] not in (role, "both", "instrumental")]
        expected_levels = [window_dbfs(samples, rate, left, right)
                           for left, right in expected_windows]
        expected_activity = [active_frame_ratio(samples, rate, left, right)
                             for left, right in expected_windows]
        forbidden_levels = [window_dbfs(samples, rate, left, right)
                             for left, right in forbidden_windows]
        instrumental_windows = [(line["start"], line["end"])
                                for line in lines if line["voice"] == "instrumental"]
        instrumental_levels = [window_dbfs(samples, rate, left, right)
                               for left, right in instrumental_windows]
        silent_indexes = [index for index, level in zip(expected_indexes, expected_levels)
                          if level < ACTIVE_DBFS]
        if silent_indexes:
            fail(errors, f"{role}: silent assigned line windows {silent_indexes}")
        sparse_indexes = [index for index, ratio in zip(expected_indexes, expected_activity)
                          if ratio < (0.2 if lines[index]["end"] - lines[index]["start"] >= 0.4 else 0.01)]
        if sparse_indexes:
            fail(errors, f"{role}: assigned line windows have too little active coverage {sparse_indexes}")
        if forbidden_levels and max(forbidden_levels) > SILENT_DBFS:
            fail(errors, f"{role}: vocal energy leaks into another singer's windows")
        if instrumental_levels and max(instrumental_levels) > SILENT_DBFS:
            fail(errors, f"{role}: vocal energy leaks into instrumental windows")
        measurements[role] = {"sample_rate": rate, "channels": channels,
                              "duration_seconds": actual_duration,
                              "assigned_window_dbfs": expected_levels,
                              "assigned_active_frame_ratio": expected_activity,
                              "forbidden_window_dbfs": forbidden_levels,
                              "instrumental_window_dbfs": instrumental_levels}

    if "female" in audio and "male" in audio:
        female_rate, female_channels, female_samples = audio["female"]
        male_rate, male_channels, male_samples = audio["male"]
        if female_rate != male_rate:
            fail(errors, f"female/male sample rates differ ({female_rate} vs {male_rate})")
        if abs(len(female_samples) / female_rate - len(male_samples) / male_rate) > MAX_DURATION_ERROR:
            fail(errors, "female and male tracks are not aligned to the same duration")
        both_windows = [(line["start"], line["end"])
                        for line in lines if line["voice"] == "both"]
        for start, end in both_windows:
            female_level = window_dbfs(female_samples, female_rate, start, end)
            male_level = window_dbfs(male_samples, male_rate, start, end)
            if abs(female_level - male_level) > MAX_BOTH_IMBALANCE_DB:
                fail(errors, f"both window {start:.3f}-{end:.3f}s is imbalanced by {abs(female_level - male_level):.1f} dB")

    if args.instrumental:
        try:
            rate, channels, samples = read_wav(args.instrumental)
            actual_duration = len(samples) / rate if rate else 0.0
            if duration and abs(actual_duration - duration) > MAX_DURATION_ERROR:
                fail(errors, f"instrumental: duration {actual_duration:.3f}s differs from plan {duration:.3f}s")
            expected_rate = plan.get("sample_rate")
            if expected_rate is not None and rate != expected_rate:
                fail(errors, f"instrumental: sample rate {rate} differs from plan {expected_rate}")
            measurements["instrumental"] = {"sample_rate": rate, "channels": channels,
                                             "duration_seconds": actual_duration}
        except (OSError, ValueError, wave.Error) as exc:
            fail(errors, f"instrumental: cannot read WAV: {exc}")

    result = {
        "qualified": not errors,
        "qualification_scope": "machine_only",
        "schema_version": plan.get("schema_version"),
        "errors": errors,
        "measurements": measurements,
        "human_review_required": [
            "lyrics_completeness",
            "diction",
            "naturalness",
            "singer_identity",
            "pitch_and_harmony",
            "duet_timing_and_balance",
            "musicality",
        ],
    }
    output = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(output, encoding="utf-8")
    print(output, end="")
    return 0 if not errors else 1


if __name__ == "__main__":
    sys.exit(main())
