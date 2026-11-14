"""Read-only, dependency-free handoff preflight; no dispatch or authorization inference."""
from __future__ import annotations

import argparse
import json
from pathlib import Path, PureWindowsPath
import sys

MODES = {
    "codex": {"plugin", "managed"},
    "claude": {"panel", "managed"},
    "antigravity": {"panel", "cli"},
    "grok": {"acp", "headless"},
}
FIELDS = {
    "plan_id", "plan_name", "plan_path", "task_key", "task_label", "change_path",
    "assignment_id", "revision", "sender", "recipient", "reply_to", "goal",
    "inputs", "allowed_write_paths", "constraints", "deliverables", "verification",
    "completion_criteria", "authorization", "closeout_owner",
}
ENDPOINT_FIELDS = {"session_key", "service", "agent", "mode", "native_session_id", "generation"}


class InvalidHandoff(ValueError):
    pass


def text(value, label):
    if not isinstance(value, str) or not value.strip() or "\x00" in value or len(value) > 65536:
        raise InvalidHandoff(f"{label}: non-empty text required")
    return value


def exact(value, fields, label):
    if not isinstance(value, dict) or set(value) != fields:
        raise InvalidHandoff(f"{label}: incorrect field set")


def positive(value, label):
    if type(value) is not int or value < 1:
        raise InvalidHandoff(f"{label}: positive integer required")


def endpoint(value, label):
    exact(value, ENDPOINT_FIELDS, label)
    for key in ENDPOINT_FIELDS - {"generation"}:
        text(value[key], f"{label}.{key}")
    positive(value["generation"], f"{label}.generation")
    if value["agent"] not in MODES or value["mode"] not in MODES[value["agent"]]:
        raise InvalidHandoff(f"{label}: unsupported agent/mode")


def relative_file(root, value, label, *, exists=False):
    value = text(value, label)
    win = PureWindowsPath(value)
    if "\\" in value or win.drive or win.root or Path(value).is_absolute() or ":" in value:
        raise InvalidHandoff(f"{label}: workspace-relative forward-slash path required")
    if any(part in {"..", ""} for part in value.split("/")):
        raise InvalidHandoff(f"{label}: traversal or empty path component")
    try:
        target = (root / value).resolve()
        target.relative_to(root)
    except (OSError, ValueError, RuntimeError):
        raise InvalidHandoff(f"{label}: path escapes workspace or cannot resolve") from None
    if exists and not target.is_file():
        raise InvalidHandoff(f"{label}: referenced file missing")
    return target


def validate(payload, workspace):
    root = Path(workspace).resolve()
    if not root.is_dir():
        raise InvalidHandoff("workspace: directory required")
    exact(payload, FIELDS, "handoff")
    scalar = FIELDS - {"revision", "sender", "recipient", "reply_to", "inputs",
                      "allowed_write_paths", "constraints", "deliverables", "verification", "completion_criteria"}
    for key in scalar:
        text(payload[key], key)
    positive(payload["revision"], "revision")
    for key in ("sender", "recipient", "reply_to"):
        endpoint(payload[key], key)
    seen = {}
    for key in ("sender", "recipient", "reply_to"):
        ep = payload[key]
        if ep["session_key"] in seen and seen[ep["session_key"]] != ep:
            raise InvalidHandoff("endpoints: conflicting identity for same session_key")
        seen[ep["session_key"]] = ep
    if payload["sender"]["session_key"] == payload["recipient"]["session_key"]:
        raise InvalidHandoff("recipient: delegation must target another session")
    for key in ("plan_path", "change_path"):
        relative_file(root, payload[key], key, exists=True)
    for key in ("inputs", "allowed_write_paths", "constraints", "deliverables", "verification", "completion_criteria"):
        values = payload[key]
        if not isinstance(values, list) or (not values and key not in {"inputs", "allowed_write_paths"}):
            raise InvalidHandoff(f"{key}: list required; outcome and constraints lists cannot be empty")
        for index, value in enumerate(values):
            label = f"{key}[{index}]"
            if key == "inputs":
                relative_file(root, value, label, exists=True)
            elif key in {"allowed_write_paths", "deliverables"}:
                relative_file(root, value, label)
            else:
                text(value, label)
    return {"valid": True, "plan_id": payload["plan_id"], "task_key": payload["task_key"],
            "assignment_id": payload["assignment_id"], "revision": payload["revision"],
            "checks": "fields-and-local-paths-only"}


def unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise InvalidHandoff("JSON: duplicate key")
        result[key] = value
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--input", required=True)
    args = parser.parse_args()
    try:
        raw = Path(args.input).read_text(encoding="utf-8")
        if len(raw.encode("utf-8")) > 262144:
            raise InvalidHandoff("JSON: handoff exceeds 256 KiB")
        result = validate(json.loads(raw, object_pairs_hook=unique_pairs), args.workspace)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (InvalidHandoff, OSError, UnicodeError, json.JSONDecodeError) as error:
        # Never reflect JSON content, absolute paths, or arbitrary exception strings.
        message = str(error) if isinstance(error, InvalidHandoff) else "input: cannot read valid UTF-8 JSON"
        print(json.dumps({"valid": False, "error": message}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
