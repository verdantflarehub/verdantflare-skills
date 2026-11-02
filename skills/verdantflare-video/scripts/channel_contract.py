"""Validate the installed Video tool and route declarations without a model catalog."""
from __future__ import annotations

from typing import Any


def validate_schema(value: Any, schema: dict, path: str = "request", root: dict | None = None) -> None:
    root = schema if root is None else root
    if "$ref" in schema:
        reference = schema["$ref"]
        if not isinstance(reference, str) or not reference.startswith("#/"):
            raise ValueError(f"{path}: unsupported host schema reference")
        target = root
        for token in reference[2:].split("/"):
            target = target.get(token.replace("~1", "/").replace("~0", "~"), {})
        if not target:
            raise ValueError(f"{path}: unresolved host schema reference")
        validate_schema(value, target, path, root)
        return
    for keyword in ("anyOf", "oneOf"):
        if keyword in schema:
            matches = 0
            for variant in schema[keyword]:
                try:
                    validate_schema(value, variant, path, root)
                    matches += 1
                except ValueError:
                    pass
            if not matches or keyword == "oneOf" and matches != 1:
                raise ValueError(f"{path}: value conflicts with host tool schema")
    for variant in schema.get("allOf", []):
        validate_schema(value, variant, path, root)
    expected = schema.get("type")
    types = {"object": isinstance(value, dict), "array": isinstance(value, list),
             "string": isinstance(value, str), "integer": type(value) is int,
             "number": type(value) in {int, float}, "boolean": type(value) is bool, "null": value is None}
    if expected and not any(types.get(kind, False) for kind in ([expected] if isinstance(expected, str) else expected)):
        raise ValueError(f"{path}: invalid type for host tool schema")
    if "enum" in schema and value not in schema["enum"] or "const" in schema and value != schema["const"]:
        raise ValueError(f"{path}: value conflicts with host tool schema")
    if isinstance(value, dict):
        properties = schema.get("properties", {})
        for field in schema.get("required", []):
            if field not in value:
                raise ValueError(f"{path}.{field}: required by host tool schema")
        for field, item in value.items():
            if field not in properties and schema.get("additionalProperties") is False:
                raise ValueError(f"{path}.{field}: not registered by host tool schema")
            if field in properties:
                validate_schema(item, properties[field], f"{path}.{field}", root)
    if isinstance(value, list):
        if len(value) < schema.get("minItems", 0) or len(value) > schema.get("maxItems", float("inf")):
            raise ValueError(f"{path}: item count exceeds host tool schema")
        if isinstance(schema.get("items"), dict):
            for item in value:
                validate_schema(item, schema["items"], f"{path}[]", root)
    if type(value) in {int, float} and not schema.get("minimum", float("-inf")) <= value <= schema.get("maximum", float("inf")):
        raise ValueError(f"{path}: number exceeds host tool schema")
    if isinstance(value, str) and not schema.get("minLength", 0) <= len(value) <= schema.get("maxLength", float("inf")):
        raise ValueError(f"{path}: length exceeds host tool schema")


def tool_schema(inventory: dict, name: str) -> dict:
    schema = inventory.get("schemas", {}).get(name)
    if not isinstance(schema, dict) or not isinstance(schema.get("properties"), dict):
        raise ValueError(f"{name}: host input schema is unavailable; no generation submitted")
    return schema


def validate_tool(inventory: dict, name: str, request: dict) -> None:
    schema = tool_schema(inventory, name)
    # Do not invent fields even when an older tool permits additional properties.
    for key in request:
        if key not in schema["properties"]:
            raise ValueError(f"{name}.{key}: parameter is not registered; no generation submitted")
    validate_schema(request, schema)


def select_route(capabilities: dict, route: str | None, model: str | None) -> tuple[str, str, dict]:
    if not capabilities or not isinstance(capabilities.get("routes"), list):
        raise ValueError("Host video.capabilities is unavailable; no generation submitted")
    route = route or capabilities.get("default_route")
    if not isinstance(route, str) or not route:
        raise ValueError("Host default route is unavailable; explicitly select --route")
    rows = [row for row in capabilities["routes"] if isinstance(row, dict) and row.get("route") == route]
    def aliases(row):
        return {row.get("model"), *[item for item in row.get("models", []) if isinstance(item, str)]}
    if not model:
        defaults = {row.get("default_model") for row in rows if row.get("default_model")}
        host_default = capabilities.get("default_model")
        model = next(iter(defaults)) if len(defaults) == 1 else host_default
    matching = [row for row in rows if model and model in aliases(row)]
    if len(matching) != 1:
        raise ValueError("Host must declare exactly one matching route/model; explicitly select --model; no generation submitted")
    selected = matching[0]
    if selected.get("configured") is not True or selected.get("enabled") is False or selected.get('can_submit') is False:
        raise ValueError("Selected route/model is not configured or enabled; no generation submitted")
    return route, model, selected


def declared_default(selected: dict, schema: dict, field: str, limit: str | None = None, mode: str | None = None):
    defaults = selected.get("defaults", {})
    mode_defaults = defaults.get('by_mode', {}).get(mode, {})
    if field in mode_defaults:
        return mode_defaults[field]
    if field in defaults:
        return defaults[field]
    declaration = selected.get(limit or field)
    if isinstance(declaration, dict) and "default" in declaration:
        return declaration["default"]
    if isinstance(declaration, list) and len(declaration) == 1:
        return declaration[0]
    return schema.get("properties", {}).get(field, {}).get("default")


def validate_capability(request: dict, selected: dict) -> None:
    limits = selected.get("duration_seconds", {})
    duration = request.get("duration_seconds")
    allowed = limits.get("values", limits.get("enum")) if isinstance(limits, dict) else limits
    if duration is not None and (allowed and duration not in allowed or isinstance(limits, dict) and
            not limits.get("minimum", float("-inf")) <= duration <= limits.get("maximum", float("inf"))):
        raise ValueError("Selected route/model duration exceeds its declared adapter limits")
    for field, declaration in (("aspect_ratio", "aspect_ratios"), ("resolution", "resolutions"), ("mode", "modes")):
        if field in request and (not selected.get(declaration) or request[field] not in selected[declaration]):
            raise ValueError(f"Selected route/model {field} is not supported by host capabilities")
    references = request["references"]
    ref_limits = selected.get("reference_limits") or {}
    ref_limits = {**ref_limits, **ref_limits.get('by_mode', {}).get(request.get('mode'), {})}
    for kind, items in references.items():
        limit = ref_limits.get(kind)
        if type(limit) is int and len(items) > limit:
            raise ValueError(f"Selected route/model accepts at most {limit} {kind}")
        if isinstance(limit, dict) and not limit.get("minimum", 0) <= len(items) <= limit.get("maximum", float("inf")):
            raise ValueError(f"Selected route/model {kind} count exceeds host capabilities")
    total = sum(map(len, references.values()))
    minimum = ref_limits.get("minimum_total", ref_limits.get("minimum", ref_limits.get("min_total", 0)))
    maximum = ref_limits.get("total", ref_limits.get("max_total", float("inf")))
    if not minimum <= total <= maximum:
        raise ValueError("Selected route/model total reference count exceeds host capabilities")
    required = ref_limits.get('required_media_any_of', [])
    if required and not any(references.get(kind) for kind in required):
        raise ValueError("Selected route/model requires one of its declared media kinds")
    mode = request.get("mode")
    if mode == "text_to_video" and total:
        raise ValueError("text_to_video does not accept references")
    if mode == "image_to_video" and (references["videos"] or references["audios"] or len(references["images"]) != 1 or
            references["images"][0]["purpose"] != "first_frame"):
        raise ValueError("image_to_video requires exactly one first_frame image")
    if mode == "reference_to_video" and (not total or any(item["purpose"] in {"first_frame", "last_frame"} for item in references["images"])):
        raise ValueError("reference_to_video requires reference inputs without first_frame/last_frame semantics")
    if isinstance(selected.get('parameter_schema'), dict):
        validate_schema(request, selected['parameter_schema'], 'capability')
