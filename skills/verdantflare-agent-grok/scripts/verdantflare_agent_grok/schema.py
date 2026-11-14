"""Strict input validation and MCP tool schemas for verdantflare-agent-grok."""
from __future__ import annotations

from .errors import BridgeError

MAX_PROMPT_BYTES = 65536


def _require_keys(payload: dict, allowed: set[str], required: set[str] = frozenset()) -> dict:
    if not isinstance(payload, dict):
        raise BridgeError("INVALID_ARGUMENT", "Arguments must be a JSON object")
    extra = set(payload) - allowed
    missing = required - set(payload)
    if extra or missing:
        raise BridgeError("INVALID_ARGUMENT", f"Invalid argument keys: missing={sorted(missing)} extra={sorted(extra)}")
    return dict(payload)


def _text(value, name: str, *, limit: int | None = None) -> str:
    if not isinstance(value, str) or not value.strip():
        raise BridgeError("INVALID_ARGUMENT", f"{name} must be a non-empty string")
    if limit and len(value.encode("utf-8")) > limit:
        raise BridgeError("INVALID_ARGUMENT", f"{name} exceeds {limit} UTF-8 bytes")
    return value


def _number(value, name: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not (minimum <= value <= maximum):
        raise BridgeError("INVALID_ARGUMENT", f"{name} must be an integer in [{minimum}, {maximum}]")
    return value


def _resolve_session_alias(data: dict, *, required: bool = False) -> str | None:
    sid = data.get("session_id")
    tid = data.get("thread_id")
    if sid is not None and tid is not None and sid != tid:
        raise BridgeError("INVALID_ARGUMENT", "session_id and thread_id must match when both are provided")
    chosen = sid if sid is not None else tid
    if chosen is not None:
        return _text(chosen, "session_id")
    if required:
        raise BridgeError("INVALID_ARGUMENT", "session_id (or thread_id) is required")
    return None


def validate(tool: str, payload: dict) -> dict:
    if tool == "grok_capabilities":
        data = _require_keys(payload, {"cwd", "cursor", "limit"})
        if data.get("cwd") is not None:
            data["cwd"] = _text(data["cwd"], "cwd")
        if data.get("cursor") is not None:
            data["cursor"] = _text(data["cursor"], "cursor")
        data["limit"] = _number(data.get("limit", 20), "limit", 1, 50)
        return data

    if tool == "grok_task_start":
        data = _require_keys(
            payload,
            {"request_id", "mode", "cwd", "prompt", "session_id", "thread_id", "timeout_seconds"},
            {"request_id", "mode", "cwd", "prompt"},
        )
        data["request_id"] = _text(data["request_id"], "request_id")
        data["cwd"] = _text(data["cwd"], "cwd")
        data["prompt"] = _text(data["prompt"], "prompt", limit=MAX_PROMPT_BYTES)
        if data["mode"] not in ("acp", "headless"):
            raise BridgeError("UNSUPPORTED_CAPABILITY", "mode must be acp or headless")
        sid = _resolve_session_alias(data, required=False)
        data["session_id"] = sid
        data["thread_id"] = sid
        data["timeout_seconds"] = _number(data.get("timeout_seconds", 3600), "timeout_seconds", 30, 86400)
        return data

    if tool == "grok_task_status":
        data = _require_keys(payload, {"task_id", "after_event", "limit"}, {"task_id"})
        data["task_id"] = _text(data["task_id"], "task_id")
        data["after_event"] = _number(data.get("after_event", 0), "after_event", 0, 10**9)
        data["limit"] = _number(data.get("limit", 50), "limit", 1, 200)
        return data

    if tool == "grok_task_result":
        data = _require_keys(payload, {"task_id", "turn_id", "cursor"}, {"task_id"})
        data["task_id"] = _text(data["task_id"], "task_id")
        if data.get("turn_id") is not None:
            data["turn_id"] = _text(data["turn_id"], "turn_id")
        if data.get("cursor") is not None and not (isinstance(data["cursor"], str) and data["cursor"].isdigit()):
            raise BridgeError("INVALID_ARGUMENT", "cursor must be a decimal offset string")
        return data

    if tool == "grok_task_continue":
        data = _require_keys(
            payload,
            {"task_id", "request_id", "prompt", "expected_turn_id", "timeout_seconds"},
            {"task_id", "request_id", "prompt", "expected_turn_id"},
        )
        for key in ("task_id", "request_id", "expected_turn_id"):
            data[key] = _text(data[key], key)
        data["prompt"] = _text(data["prompt"], "prompt", limit=MAX_PROMPT_BYTES)
        data["timeout_seconds"] = _number(data.get("timeout_seconds", 3600), "timeout_seconds", 30, 86400)
        return data

    if tool == "grok_task_cancel":
        data = _require_keys(payload, {"task_id", "expected_turn_id"}, {"task_id", "expected_turn_id"})
        return {k: _text(v, k) for k, v in data.items()}

    if tool == "grok_task_respond":
        data = _require_keys(
            payload,
            {"task_id", "request_id", "expected_turn_id", "pending_request_id", "response"},
            {"task_id", "request_id", "expected_turn_id", "pending_request_id", "response"},
        )
        for key in ("task_id", "request_id", "expected_turn_id", "pending_request_id"):
            data[key] = _text(data[key], key)
        if not isinstance(data["response"], dict):
            raise BridgeError("INVALID_ARGUMENT", "response must be an object")
        return data

    raise BridgeError("UNSUPPORTED_CAPABILITY", f"Unknown tool {tool}")


_NON_EMPTY_STR = {"type": "string", "minLength": 1, "pattern": r"\S"}
_PROMPT_STR = {"type": "string", "minLength": 1, "maxLength": MAX_PROMPT_BYTES, "pattern": r"\S"}

SCHEMAS = {
    "grok_capabilities": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "cwd": _NON_EMPTY_STR,
            "cursor": _NON_EMPTY_STR,
            "limit": {"type": "integer", "minimum": 1, "maximum": 50},
        },
    },
    "grok_task_start": {
        "type": "object",
        "additionalProperties": False,
        "required": ["request_id", "mode", "cwd", "prompt"],
        "properties": {
            "request_id": _NON_EMPTY_STR,
            "mode": {"type": "string", "enum": ["acp", "headless"]},
            "cwd": _NON_EMPTY_STR,
            "prompt": _PROMPT_STR,
            "session_id": _NON_EMPTY_STR,
            "thread_id": _NON_EMPTY_STR,
            "timeout_seconds": {"type": "integer", "minimum": 30, "maximum": 86400},
        },
    },
    "grok_task_status": {
        "type": "object",
        "additionalProperties": False,
        "required": ["task_id"],
        "properties": {
            "task_id": _NON_EMPTY_STR,
            "after_event": {"type": "integer", "minimum": 0},
            "limit": {"type": "integer", "minimum": 1, "maximum": 200},
        },
    },
    "grok_task_result": {
        "type": "object",
        "additionalProperties": False,
        "required": ["task_id"],
        "properties": {
            "task_id": _NON_EMPTY_STR,
            "turn_id": _NON_EMPTY_STR,
            "cursor": {"type": "string", "pattern": "^[0-9]+$"},
        },
    },
    "grok_task_continue": {
        "type": "object",
        "additionalProperties": False,
        "required": ["task_id", "request_id", "prompt", "expected_turn_id"],
        "properties": {
            "task_id": _NON_EMPTY_STR,
            "request_id": _NON_EMPTY_STR,
            "prompt": _PROMPT_STR,
            "expected_turn_id": _NON_EMPTY_STR,
            "timeout_seconds": {"type": "integer", "minimum": 30, "maximum": 86400},
        },
    },
    "grok_task_cancel": {
        "type": "object",
        "additionalProperties": False,
        "required": ["task_id", "expected_turn_id"],
        "properties": {
            "task_id": _NON_EMPTY_STR,
            "expected_turn_id": _NON_EMPTY_STR,
        },
    },
    "grok_task_respond": {
        "type": "object",
        "additionalProperties": False,
        "required": ["task_id", "request_id", "expected_turn_id", "pending_request_id", "response"],
        "properties": {
            "task_id": _NON_EMPTY_STR,
            "request_id": _NON_EMPTY_STR,
            "expected_turn_id": _NON_EMPTY_STR,
            "pending_request_id": _NON_EMPTY_STR,
            "response": {"type": "object"},
        },
    },
}
