"""Synthetic installed-tool declarations shared by CLI behavior tests."""
from copy import deepcopy

CREATE_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["project_id", "idempotency_key", "model", "route", "prompt", "references"], "properties": {
    **{key: {"type": "string"} for key in ("project_id", "idempotency_key", "model", "route", "prompt", "aspect_ratio", "resolution", "mode", "budget_limit", "channel_account_id", "title")},
    "duration_seconds": {"type": "integer", "default": 5, "minimum": 1, "maximum": 30},
    "price_ceiling": {"type": "number", "minimum": 0},
    "references": {"type": "object", "properties": {kind: {"type": "array", "items": {
        "type": "object", "required": ["artifact_id", "purpose"], "properties": {
            "artifact_id": {"type": "string"}, "purpose": {"type": "string"}}}}
        for kind in ("images", "videos", "audios")}},
    "postprocess": {"type": "object"}}}


def schemas():
    create = deepcopy(CREATE_SCHEMA)
    preflight = deepcopy(create)
    preflight["properties"].pop("idempotency_key")
    preflight["properties"].pop("title")
    preflight["required"].remove("idempotency_key")
    return {"video.create": create, "video.generate": deepcopy(create), "video.preflight": preflight}


def fal_inventory():
    return {"tools": ["video.generate", "video.status", "video.result"], "create_tool": "video.generate", "schemas": schemas(),
        "capabilities": {"default_route": "fal", "default_model": "minimax-h3-ref2va", "routes": [{
            "route": "fal", "model": "minimax-h3-ref2va", "configured": True,
            "defaults": {"duration_seconds": 5, "aspect_ratio": "adaptive"},
            "duration_seconds": {"minimum": 4, "maximum": 15}, "aspect_ratios": ["adaptive", "16:9", "9:16"],
            "reference_limits": {"images": 9, "videos": 3, "audios": 3, "minimum": 1, "total": 12}}]}}
