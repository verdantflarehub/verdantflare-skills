"""Synthetic installed-tool declarations shared by CLI behavior tests."""
from copy import deepcopy

CREATE_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["project_id", "idempotency_key", "model", "route", "prompt", "references"], "properties": {
    **{key: {"type": "string"} for key in ("project_id", "idempotency_key", "model", "route", "prompt", "aspect_ratio", "resolution", "upscale", "mode", "budget_limit", "channel_account_id", "title", "quality_profile")},
    "duration_seconds": {"type": "integer", "default": 5, "minimum": 1, "maximum": 30},
    "price_ceiling": {"type": "number", "minimum": 0},
    "references": {"type": "object", "properties": {kind: {"type": "array", "items": {
        "type": "object", "required": ["artifact_id", "purpose"], "properties": {
            "artifact_id": {"type": "string"}, "purpose": {"type": "string"}}}}
        for kind in ("images", "videos", "audios")}},
    "postprocess": {"type": "object"}}}

CHARACTER_SWAP_SCHEMA = {'type': 'object',
 'additionalProperties': False,
 'required': ['project_id',
              'idempotency_key',
              'source_artifact_id',
              'character_image_artifact_id',
              'prompt'],
 'properties': {'project_id': {'type': 'string'},
                'idempotency_key': {'type': 'string'},
                'source_artifact_id': {'type': 'string'},
                'character_image_artifact_id': {'type': 'string'},
                'prompt': {'type': 'string',
                           'minLength': 1,
                           'maxLength': 16384},
                'seed': {'type': 'integer',
                         'minimum': 0,
                         'maximum': 4294967295,
                         'default': 7},
                'seed_mode': {'enum': ['fixed']},
                'character_lora_version': {'enum': ['v1.5', 'v1']},
                'turbo_variant': {'enum': ['fl2v_turbo_8step',
                                           'pika_h3_flex_turbo_8step_hd_ref2va']},
                'resolution_preset': {'enum': ['768p', '480p']},
                'lora_strength': {'type': 'number',
                                  'minimum': 0.5,
                                  'maximum': 1.2}}}

REGENERATE_2K_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["source_artifact_id", "project_id", "idempotency_key"],
    "properties": {
        "source_artifact_id": {"type": "string"},
        "project_id": {"type": "string"},
        "idempotency_key": {"type": "string"},
        "profile": {"type": "string", "enum": ["open_h3_2k_ic"], "default": "open_h3_2k_ic"},
        "grid_mode": {"type": "string", "enum": ["budget_1440p", "exact_2x"], "default": "budget_1440p"},
        "prompt": {"type": "string"},
        "seed": {"type": "integer", "minimum": 0, "maximum": 4294967295, "default": 7},
        "title": {"type": "string"},
        "postprocess": {"type": "object"},
    },
}


def schemas():
    create = deepcopy(CREATE_SCHEMA)
    preflight = deepcopy(create)
    preflight["properties"].pop("idempotency_key")
    preflight["properties"].pop("title")
    preflight["required"].remove("idempotency_key")
    preflight["properties"].update({
        "source_artifact_id": {"type": "string"},
        "profile": {"type": "string", "enum": ["open_h3_2k_ic"]},
        "grid_mode": {"type": "string", "enum": ["budget_1440p", "exact_2x"]},
        "seed": {"type": "integer", "minimum": 0, "maximum": 4294967295},
    })
    preflight["properties"].update({key: value for key,value in CHARACTER_SWAP_SCHEMA["properties"].items()
                                     if key != "idempotency_key"})
    preflight["required"] = ["project_id"]
    return {
        "video.create": create,
        "video.generate": deepcopy(create),
        "video.preflight": preflight,
        "video.character_swap": deepcopy(CHARACTER_SWAP_SCHEMA),
        "video.regenerate_2k": deepcopy(REGENERATE_2K_SCHEMA),
    }


def fal_inventory():
    return {"tools": ["video.generate", "video.status", "video.result"], "create_tool": "video.generate", "schemas": schemas(),
        "capabilities": {"default_route": "fal", "default_model": "minimax-h3-ref2va", "routes": [{
            "route": "fal", "model": "minimax-h3-ref2va", "configured": True,
            "defaults": {"duration_seconds": 5, "aspect_ratio": "adaptive"},
            "duration_seconds": {"minimum": 4, "maximum": 15}, "aspect_ratios": ["adaptive", "16:9", "9:16"],
            "reference_limits": {"images": 9, "videos": 3, "audios": 3, "minimum": 1, "total": 12}}]}}
