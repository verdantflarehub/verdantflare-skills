"""Strict public tool schemas. Backend RPC names are never caller supplied."""
from copy import deepcopy
from jsonschema import Draft202012Validator
from .errors import BridgeError

TEXT = {"type": "string", "minLength": 1, "maxLength": 65536}
ID = {"type": "string", "minLength": 1, "maxLength": 256}
CURSOR = {"type": "string", "pattern": r"^[0-9]+$", "maxLength": 20}


def obj(properties, required=()):
    return {"type": "object", "properties": deepcopy(properties),
            "required": list(required), "additionalProperties": False}


SCHEMAS = {
    "codex_session_create": obj({"cwd": TEXT, "request_id": ID,
        "name": {"type": "string", "minLength": 1, "maxLength": 256}}, ("cwd", "request_id")),
    "codex_session_rename": obj({"thread_id": ID, "cwd": TEXT,
        "name": {"type": "string", "minLength": 1, "maxLength": 256}},
        ("thread_id", "cwd", "name")),
    "codex_capabilities": obj({"cwd": TEXT, "thread_id": ID, "cursor": {"type": "string", "minLength": 1, "maxLength": 8192},
        "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 20}}),
    "codex_task_start": obj({"request_id": ID, "mode": {"type": "string", "enum": ["plugin"]},
        "cwd": TEXT, "prompt": TEXT, "thread_id": ID,
        "timeout_seconds": {"type": "integer", "minimum": 30, "maximum": 86400, "default": 3600}},
        ("request_id", "mode", "cwd", "prompt", "thread_id")),
    "codex_task_status": obj({"task_id": ID,
        "after_event": {"type": "integer", "minimum": 0, "default": 0},
        "limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 50}}, ("task_id",)),
    "codex_task_result": obj({"task_id": ID, "turn_id": ID, "cursor": CURSOR}, ("task_id",)),
    "codex_task_continue": obj({"task_id": ID, "request_id": ID, "prompt": TEXT,
        "expected_turn_id": ID}, ("task_id", "request_id", "prompt", "expected_turn_id")),
    "codex_task_cancel": obj({"task_id": ID, "expected_turn_id": {**ID, "type": ["string", "null"]}}, ("task_id", "expected_turn_id")),
    "codex_task_respond": obj({"task_id": ID, "request_id": ID, "pending_request_id": ID,
        "expected_turn_id": ID, "response": {"type": "object"}},
        ("task_id", "request_id", "pending_request_id", "expected_turn_id", "response")),
}
# Keep advertised schemas within common model tool-schema subsets. Conditional
# requirements are enforced below, even when a host drops JSON Schema conditionals.
SCHEMAS["codex_task_start"]["properties"]["request_id"]["description"] = (
    "Client-generated idempotency key unique within this service."
)
SCHEMAS["codex_task_start"]["properties"]["mode"]["description"] = (
    "Attach to the specified VS Code plugin Session."
)
SCHEMAS["codex_task_start"]["properties"]["cwd"]["description"] = (
    "Required absolute workspace directory path."
)
SCHEMAS["codex_task_start"]["properties"]["prompt"]["description"] = (
    "Required non-empty task instruction (max 64 KiB UTF-8)."
)
SCHEMAS["codex_task_start"]["properties"]["thread_id"]["description"] = (
    "ID of the target plugin conversation."
)


def validate(name, arguments):
    if name not in SCHEMAS:
        raise BridgeError("INVALID_ARGUMENT", "Unknown tool")
    schema = SCHEMAS[name]
    errors = list(Draft202012Validator(schema).iter_errors(arguments))
    if errors:
        # Do not reflect prompt text, secrets, or arbitrary caller values.
        raise BridgeError("INVALID_ARGUMENT", "Arguments do not match the tool schema")
    if name == "codex_capabilities" and "thread_id" in arguments and "cursor" in arguments:
        raise BridgeError("INVALID_ARGUMENT", "Use thread_id or a pagination cursor")
    result = deepcopy(arguments)
    for key, value in result.items():
        if isinstance(value, str) and (not value.strip() or "\x00" in value):
            raise BridgeError("INVALID_ARGUMENT", f"Invalid {key}")
    if "prompt" in result and len(result["prompt"].encode("utf-8")) > 65536:
        raise BridgeError("INVALID_ARGUMENT", "Prompt exceeds 64 KiB")
    for key, spec in schema["properties"].items():
        if key not in result and "default" in spec:
            result[key] = spec["default"]
    return result
