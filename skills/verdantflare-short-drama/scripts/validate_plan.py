#!/usr/bin/env python3
"""Validate a VerdantFlare Director local planning handoff."""

import argparse
import json
import re
from pathlib import Path


def _text(value):
    return isinstance(value, str) and bool(value.strip())


def _integer(value):
    return type(value) is int


def validate(plan):
    errors = []
    if not isinstance(plan, dict):
        return ["root must be an object"]

    if not _integer(plan.get("schema_version")) or plan["schema_version"] != 1:
        errors.append("schema_version must be 1")
    if not _text(plan.get("project_ref")):
        errors.append("project_ref must be non-empty text")
    if plan.get("kind") not in ("film", "advertisement", "music_video"):
        errors.append("kind must be film, advertisement, or music_video")
    duration = plan.get("duration_ms")
    if not _integer(duration) or duration <= 0:
        errors.append("duration_ms must be a positive integer")
        duration = None
    ratio = plan.get("aspect_ratio")
    if not _text(ratio) or not re.fullmatch(r"[1-9]\d*:[1-9]\d*", ratio):
        errors.append("aspect_ratio must be a ratio such as 16:9")

    source = plan.get("source")
    if not isinstance(source, dict) or not all(_text(source.get(key)) for key in ("type", "reference")):
        errors.append("source must contain non-empty type and reference")

    assets = plan.get("assets")
    assets = assets if isinstance(assets, dict) else {}
    if not isinstance(plan.get("assets"), dict):
        errors.append("assets must be an object")
    catalog = {}
    for group in ("characters", "looks", "locations", "props"):
        entries = assets.get(group)
        if not isinstance(entries, list):
            errors.append(f"assets.{group} must be an array")
            continue
        for index, entry in enumerate(entries):
            path = f"assets.{group}[{index}]"
            if not isinstance(entry, dict) or not _text(entry.get("id")) or not _text(entry.get("description")):
                errors.append(f"{path} needs non-empty id and description")
                continue
            asset_id = entry["id"]
            if asset_id in catalog:
                errors.append(f"{path}.id duplicates {catalog[asset_id]}")
            catalog[asset_id] = group
            if "asset_version_id" in entry and not _text(entry["asset_version_id"]):
                errors.append(f"{path}.asset_version_id must be non-empty when present")
            if group == "looks" and not _text(entry.get("character_id")):
                errors.append(f"{path}.character_id must be non-empty")
    looks = assets.get("looks") if isinstance(assets.get("looks"), list) else []
    for index, look in enumerate(looks):
        if isinstance(look, dict) and _text(look.get("character_id")) and catalog.get(look["character_id"]) != "characters":
            errors.append(f"assets.looks[{index}].character_id references no character")

    if plan.get("kind") == "advertisement":
        brief = plan.get("brief")
        if not isinstance(brief, dict) or not all(_text(brief.get(key)) for key in ("benefit", "evidence", "cta")):
            errors.append("advertisement brief needs non-empty benefit, evidence, and cta")
        if not isinstance(brief, dict) or not isinstance(brief.get("prohibited_claims"), list) or not all(
            _text(item) for item in brief["prohibited_claims"]
        ):
            errors.append("brief.prohibited_claims must be an array of text")

    for field in ("assumptions", "open_questions"):
        if field in plan and (not isinstance(plan[field], list) or not all(_text(item) for item in plan[field])):
            errors.append(f"{field} must be an array of non-empty text")

    shots = plan.get("shots")
    if not isinstance(shots, list) or not shots:
        errors.append("shots must be a non-empty array")
        shots = []
    shot_map = {}
    previous_end = 0
    valid_timeline = True
    for index, shot in enumerate(shots):
        path = f"shots[{index}]"
        if not isinstance(shot, dict):
            errors.append(f"{path} must be an object")
            valid_timeline = False
            continue
        shot_id = shot.get("id")
        if not _text(shot_id):
            errors.append(f"{path}.id must be non-empty")
        elif shot_id in shot_map:
            errors.append(f"{path}.id duplicates another shot")
        else:
            shot_map[shot_id] = shot
        start, end = shot.get("start_ms"), shot.get("end_ms")
        if not _integer(start) or not _integer(end) or start < 0 or end <= start:
            errors.append(f"{path} needs integer start_ms < end_ms")
            valid_timeline = False
        else:
            if start != previous_end:
                errors.append(f"{path}.start_ms must equal previous end_ms ({previous_end})")
            previous_end = end
        for field in ("purpose", "action", "start_state", "end_state", "audio", "continuity_group"):
            if not _text(shot.get(field)):
                errors.append(f"{path}.{field} must be non-empty text")
        camera = shot.get("camera")
        if not isinstance(camera, dict) or not all(
            _text(camera.get(field)) for field in ("framing", "position", "movement")
        ):
            errors.append(f"{path}.camera needs framing, position, and movement")
        location_id = shot.get("location_id")
        if not _text(location_id) or catalog.get(location_id) != "locations":
            errors.append(f"{path}.location_id references no location")
        for field, group in (("character_ids", "characters"), ("look_ids", "looks"), ("prop_ids", "props")):
            ids = shot.get(field)
            if not isinstance(ids, list) or any(not _text(value) for value in ids):
                errors.append(f"{path}.{field} must be an array of IDs")
                continue
            if len(ids) != len(set(ids)):
                errors.append(f"{path}.{field} contains duplicate IDs")
            for asset_id in ids:
                if catalog.get(asset_id) != group:
                    errors.append(f"{path}.{field} references unknown {group} ID {asset_id}")
            if field == "look_ids":
                characters = shot.get("character_ids")
                characters = characters if isinstance(characters, list) else []
                for look in looks:
                    if isinstance(look, dict) and look.get("id") in ids and look.get("character_id") not in characters:
                        errors.append(f"{path}.look_ids includes a look without its character")
    if shots and valid_timeline and duration is not None and previous_end != duration:
        errors.append(f"shots end at {previous_end}, expected duration_ms {duration}")

    units = plan.get("generation_units")
    if units is not None:
        if not isinstance(units, list):
            errors.append("generation_units must be an array")
        elif units:
            seen_units = set()
            assigned = []
            previous_unit_end = 0
            for index, unit in enumerate(units):
                path = f"generation_units[{index}]"
                if not isinstance(unit, dict):
                    errors.append(f"{path} must be an object")
                    continue
                unit_id = unit.get("id")
                if not _text(unit_id) or unit_id in seen_units:
                    errors.append(f"{path}.id must be unique and non-empty")
                if _text(unit_id):
                    seen_units.add(unit_id)
                ids = unit.get("shot_ids")
                if not isinstance(ids, list) or not ids or any(not _text(value) for value in ids):
                    errors.append(f"{path}.shot_ids must be a non-empty array of IDs")
                    continue
                assigned.extend(ids)
                matched = [shot_map.get(shot_id) for shot_id in ids]
                if any(shot is None for shot in matched):
                    errors.append(f"{path}.shot_ids references an unknown shot")
                    continue
                start, end = unit.get("start_ms"), unit.get("end_ms")
                if not _integer(start) or not _integer(end) or start < 0 or end <= start:
                    errors.append(f"{path} needs integer start_ms < end_ms")
                    continue
                if start != previous_unit_end or start != matched[0].get("start_ms") or end != matched[-1].get("end_ms"):
                    errors.append(f"{path} must exactly cover consecutive shots and follow the previous unit")
                previous_unit_end = end
                for left, right in zip(matched, matched[1:]):
                    if left.get("end_ms") != right.get("start_ms"):
                        errors.append(f"{path}.shot_ids are not consecutive")
                if len({shot.get("continuity_group") for shot in matched if _text(shot.get("continuity_group"))}) > 1:
                    errors.append(f"{path} spans multiple continuity groups")
                if len({shot.get("location_id") for shot in matched if _text(shot.get("location_id"))}) > 1:
                    errors.append(f"{path} spans multiple locations")
                looks_by_character = {}
                for shot in matched:
                    shot_looks = shot.get("look_ids") if isinstance(shot.get("look_ids"), list) else []
                    for look in looks:
                        if isinstance(look, dict) and _text(look.get("id")) and look["id"] in shot_looks:
                            character_id = look.get("character_id")
                            if _text(character_id):
                                looks_by_character.setdefault(character_id, set()).add(look["id"])
                if any(len(ids) > 1 for ids in looks_by_character.values()):
                    errors.append(f"{path} changes a character's look within one unit")
                form = unit.get("form")
                if form not in ("continuous_single_shot", "internal_multi_shot"):
                    errors.append(f"{path}.form is invalid")
                if not _text(unit.get("model")):
                    errors.append(f"{path}.model must be non-empty")
                if unit.get("model") in ("h3", "minimax-h3-ref2va"):
                    if not 4000 <= end - start <= 15000:
                        errors.append(f"{path} exceeds H3's 4-15 second duration")
                    if form == "continuous_single_shot" and len(ids) != 1:
                        errors.append(f"{path} continuous_single_shot must contain one Shot")
                    if form == "internal_multi_shot" and not 2 <= len(ids) <= 3:
                        errors.append(f"{path} H3 internal_multi_shot needs 2-3 Shots")
            expected = [shot.get("id") for shot in shots if isinstance(shot, dict)]
            if assigned != expected:
                errors.append("generation_units must cover every Shot once in timeline order")
            if duration is not None and previous_unit_end != duration:
                errors.append("generation_units must cover duration_ms")
    return errors


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("plan", type=Path, help="Local plan.json to validate")
    args = parser.parse_args()
    try:
        plan = json.loads(args.plan.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        parser.exit(2, f"Cannot read plan: {error}\n")
    errors = validate(plan)
    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        return 1
    print("Plan structure valid; creative and approval review still required.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
