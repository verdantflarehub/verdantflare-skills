#!/usr/bin/env python3
"""Extract source-anchored scene facts with a locally running Ollama model."""

import argparse
import hashlib
import ipaddress
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, Request, build_opener


DEFAULT_ENDPOINT = "http://127.0.0.1:11434"
DEFAULT_MODEL = "gemma3:12b"
MAX_SOURCE_CHARS = 6000
KINDS = {"character", "location", "time", "prop", "dialogue", "action"}
LOCAL_OPENER = build_opener(ProxyHandler({}))

RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "facts": {"type": "array", "items": {
            "type": "object", "properties": {
                "kind": {"type": "string", "enum": sorted(KINDS)},
                "value": {"type": "string"},
                "evidence": {"type": "string"},
            }, "required": ["kind", "value", "evidence"]}},
        "beats": {"type": "array", "items": {
            "type": "object", "properties": {
                "action": {"type": "string"},
                "evidence": {"type": "string"},
            }, "required": ["action", "evidence"]}},
        "inferences": {"type": "array", "items": {
            "type": "object", "properties": {
                "idea": {"type": "string"},
                "basis": {"type": "string"},
            }, "required": ["idea", "basis"]}},
        "questions": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["facts", "beats", "inferences", "questions"],
}

SYSTEM_PROMPT = (
    "你只拆解用户提供的单场剧本文本。facts 的 value 必须逐字出现在 evidence 中；"
    "beats 的 action 必须逐字出现在 evidence 中；facts、beats 和 inferences 的 evidence/basis "
    "必须逐字复制输入中的连续原文片段。"
    "人物心理、动机和未写明的情节只能放入 inferences，不能写成 facts。"
    "不要把原文已经明确的事实放入推断。只输出符合 JSON Schema 的对象。"
)


def local_endpoint(value):
    parsed = urlsplit(value)
    if parsed.scheme != "http" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Ollama endpoint must be an unauthenticated local HTTP URL")
    if parsed.path not in ("", "/") or parsed.query or parsed.fragment:
        raise ValueError("Ollama endpoint must not contain a path, query, or fragment")
    if parsed.hostname != "localhost":
        try:
            if not ipaddress.ip_address(parsed.hostname).is_loopback:
                raise ValueError("Ollama endpoint must resolve to a loopback address")
        except ValueError as error:
            if "does not appear to be an IPv4 or IPv6 address" in str(error):
                raise ValueError("Ollama endpoint must be localhost or a loopback IP") from error
            raise
    return value.rstrip("/")


def _json_request(url, payload=None, timeout=180):
    data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = Request(url, data=data, headers={"Content-Type": "application/json"} if data else {})
    try:
        with LOCAL_OPENER.open(request, timeout=timeout) as response:
            return json.load(response)
    except (HTTPError, URLError, TimeoutError) as error:
        raise RuntimeError(f"Local Ollama request failed: {error}") from error


def available_models(endpoint, timeout=10):
    body = _json_request(f"{endpoint}/api/tags", timeout=timeout)
    if not isinstance(body, dict) or not isinstance(body.get("models"), list):
        raise RuntimeError("Ollama model inventory has an invalid response")
    return {item.get("name") for item in body["models"] if isinstance(item, dict)}


def generate_candidate(endpoint, model, source, timeout):
    payload = {
        "model": model,
        "stream": False,
        "format": RESPONSE_SCHEMA,
        "options": {"temperature": 0.1, "num_ctx": 8192, "num_predict": 2048},
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": source},
        ],
    }
    body = _json_request(f"{endpoint}/api/chat", payload, timeout)
    if not isinstance(body, dict) or not isinstance(body.get("message"), dict):
        raise RuntimeError("Ollama chat has an invalid response")
    content = body["message"].get("content")
    if not isinstance(content, str):
        raise RuntimeError("Ollama response has no text content")
    try:
        return json.loads(content), {
            "total_duration_ns": body.get("total_duration"),
            "prompt_eval_count": body.get("prompt_eval_count"),
            "eval_count": body.get("eval_count"),
        }
    except json.JSONDecodeError as error:
        raise RuntimeError(f"Ollama returned invalid JSON: {error}") from error


def _nonempty(value):
    return isinstance(value, str) and bool(value.strip())


def anchor_candidate(candidate, source):
    if not isinstance(candidate, dict):
        raise ValueError("Model output must be a JSON object")
    for section in ("facts", "beats", "inferences", "questions"):
        if not isinstance(candidate.get(section), list):
            raise ValueError(f"Model output is missing the {section} array")

    result = {"facts": [], "beats": [], "inferences": [], "questions": [], "rejected": []}
    for section in ("facts", "beats", "inferences"):
        for index, item in enumerate(candidate[section]):
            reason = None
            if not isinstance(item, dict):
                reason = "item is not an object"
            elif section == "facts":
                if not isinstance(item.get("kind"), str) or item["kind"] not in KINDS or not _nonempty(item.get("value")) or not _nonempty(item.get("evidence")):
                    reason = "invalid fact fields"
                elif item["evidence"] not in source:
                    reason = "evidence is not an exact source excerpt"
                elif item["value"] not in item["evidence"]:
                    reason = "fact value does not occur in its evidence"
            elif section == "beats":
                if not _nonempty(item.get("action")) or not _nonempty(item.get("evidence")):
                    reason = "invalid beat fields"
                elif item["evidence"] not in source:
                    reason = "evidence is not an exact source excerpt"
                elif item["action"] not in item["evidence"]:
                    reason = "beat action does not occur in its evidence"
            else:
                if not _nonempty(item.get("idea")) or not _nonempty(item.get("basis")):
                    reason = "invalid inference fields"
                elif item["basis"] not in source:
                    reason = "basis is not an exact source excerpt"
            if reason:
                result["rejected"].append({"section": section, "index": index, "reason": reason, "item": item})
            elif section == "inferences":
                result[section].append({**item, "needs_review": True})
            else:
                result[section].append(item)

    for index, question in enumerate(candidate["questions"]):
        if _nonempty(question):
            result["questions"].append(question)
        else:
            result["rejected"].append({"section": "questions", "index": index, "reason": "empty question", "item": question})
    return result


def write_new_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=f".{path.name}.", delete=False) as handle:
        temporary = Path(handle.name)
        handle.write(encoded)
    try:
        os.link(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path, help="UTF-8 single-scene text")
    parser.add_argument("--output", required=True, type=Path, help="New JSON output path")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--endpoint", default=DEFAULT_ENDPOINT)
    parser.add_argument("--timeout", type=int, default=180)
    args = parser.parse_args()
    try:
        endpoint = local_endpoint(args.endpoint)
        if args.timeout <= 0:
            raise ValueError("timeout must be positive")
        if args.output.exists():
            raise FileExistsError(f"Output already exists: {args.output}")
        source = args.input.read_text(encoding="utf-8")
        if not source.strip() or len(source) > MAX_SOURCE_CHARS:
            raise ValueError(f"Input must contain 1-{MAX_SOURCE_CHARS} characters from one scene")
        if args.model not in available_models(endpoint):
            raise ValueError(f"Local Ollama model is not installed: {args.model}")
        candidate, usage = generate_candidate(endpoint, args.model, source, args.timeout)
        anchored = anchor_candidate(candidate, source)
        if not anchored["facts"] and not anchored["beats"]:
            raise ValueError("Model returned no source-anchored facts or beats")
        report = {
            "schema_version": 1,
            "source": {"path": str(args.input.resolve()), "sha256": hashlib.sha256(source.encode("utf-8")).hexdigest()},
            "model": args.model,
            "created_at": datetime.now(timezone.utc).isoformat(),
            **anchored,
            "usage": usage,
        }
        write_new_json(args.output, report)
    except (OSError, UnicodeError, ValueError, RuntimeError) as error:
        parser.exit(1, f"Scene breakdown failed: {error}\n")
    print(f"Saved {args.output}: {len(anchored['facts'])} facts, {len(anchored['beats'])} beats, "
          f"{len(anchored['rejected'])} rejected; human review required")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
