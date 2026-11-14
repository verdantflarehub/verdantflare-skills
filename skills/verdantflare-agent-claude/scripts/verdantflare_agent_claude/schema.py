"""Strict public tool schemas for verdantflare-agent-claude."""
from copy import deepcopy
from jsonschema import Draft202012Validator
from .errors import BridgeError

TEXT = {"type": "string", "minLength": 1, "maxLength": 65536}
ID = {"type": "string", "minLength": 1, "maxLength": 256}
CURSOR = {"type": "string", "pattern": r"^[0-9]+$", "maxLength": 20}


def obj(properties, required=()):
    return {
        "type": "object",
        "properties": deepcopy(properties),
        "required": list(required),
        "additionalProperties": False,
    }


SCHEMAS = {
    "claude_session_rename": obj({
        "session_id": {"type": "string", "pattern": "^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"},
        "cwd": TEXT, "name": {"type": "string", "minLength": 1, "maxLength": 256}},
        ("session_id", "cwd", "name")),
    "claude_capabilities": obj(
        {
            "cwd": TEXT,
            "cursor": {"type": "string", "minLength": 1, "maxLength": 8192},
            "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 20},
        }
    ),
    "claude_task_start": obj(
        {
            "request_id": ID,
            "mode": {"type": "string", "enum": ["panel", "managed"]},
            "cwd": TEXT,
            "prompt": TEXT,
            "session_id": ID,
            "thread_id": ID,
            "timeout_seconds": {"type": "integer", "minimum": 30, "maximum": 86400, "default": 3600},
        },
        ("request_id", "mode", "cwd", "prompt"),
    ),
    "claude_task_status": obj(
        {
            "task_id": ID,
            "after_event": {"type": "integer", "minimum": 0, "default": 0},
            "limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 50},
        },
        ("task_id",),
    ),
    "claude_task_result": obj(
        {"task_id": ID, "turn_id": ID, "cursor": CURSOR},
        ("task_id",),
    ),
    "claude_task_continue": obj(
        {
            "task_id": ID,
            "request_id": ID,
            "prompt": TEXT,
            "expected_turn_id": ID,
        },
        ("task_id", "request_id", "prompt", "expected_turn_id"),
    ),
    "claude_task_cancel": obj(
        {"task_id": ID, "expected_turn_id": ID},
        ("task_id", "expected_turn_id"),
    ),
    "claude_task_respond": obj(
        {
            "task_id": ID,
            "request_id": ID,
            "pending_request_id": ID,
            "expected_turn_id": ID,
            "response": {"type": "object"},
        },
        ("task_id", "request_id", "pending_request_id", "expected_turn_id", "response"),
    ),
}

SCHEMAS["claude_task_start"]["properties"]["request_id"]["description"] = (
    "Required for both panel and managed modes. Client-generated idempotency key unique within this service."
)
SCHEMAS["claude_task_start"]["properties"]["mode"]["description"] = (
    "Execution mode: 'panel' (inject into an active idle VS Code Claude Code panel session_id) or 'managed' (spawn claude -p stream-json)."
)
SCHEMAS["claude_task_start"]["properties"]["cwd"]["description"] = (
    "Required absolute workspace directory path."
)
SCHEMAS["claude_task_start"]["properties"]["prompt"]["description"] = (
    "Required non-empty task instruction (max 64 KiB UTF-8)."
)
SCHEMAS["claude_task_start"]["properties"]["session_id"]["description"] = (
    "Target conversation session_id: required when mode='panel'; optional when mode='managed' to resume an existing session (do not confuse with request_id)."
)
SCHEMAS["claude_task_start"]["properties"]["thread_id"]["description"] = (
    "Optional compatibility alias for session_id."
)


def validate(name, arguments):
    if name not in SCHEMAS:
        raise BridgeError("INVALID_ARGUMENT", "Unknown tool")
    schema = SCHEMAS[name]
    errors = list(Draft202012Validator(schema).iter_errors(arguments))
    if errors:
        raise BridgeError("INVALID_ARGUMENT", "Arguments do not match the tool schema")
    result = deepcopy(arguments)
    for key, value in result.items():
        if isinstance(value, str) and (not value.strip() or "\x00" in value):
            raise BridgeError("INVALID_ARGUMENT", f"Invalid {key}")
    if "prompt" in result and len(result["prompt"].encode("utf-8")) > 65536:
        raise BridgeError("INVALID_ARGUMENT", "Prompt exceeds 64 KiB")
    if name == "claude_task_start":
        sid = result.get("session_id") or result.get("thread_id")
        if result.get("session_id") and result.get("thread_id") and result["session_id"] != result["thread_id"]:
            raise BridgeError("INVALID_ARGUMENT", "session_id and thread_id must match when both are provided")
        if result["mode"] == "panel" and not sid:
            raise BridgeError("INVALID_ARGUMENT", "session_id is required in panel mode")
        result.pop("thread_id", None)
        if sid:
            result["session_id"] = sid
            result["thread_id"] = sid
    for key, spec in schema["properties"].items():
        if key not in result and "default" in spec:
            result[key] = spec["default"]
    return result
