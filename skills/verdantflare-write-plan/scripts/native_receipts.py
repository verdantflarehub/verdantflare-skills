#!/usr/bin/env python3
"""Read-only integrity and request/result binding for native review receipts.

This validates captured evidence, not the authenticity of locally authored files.
The independent reviewer must still compare captures with the native provider.
No commands in evidence are executed and no prompt or response is printed.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import evidence_view as ev

HEX = re.compile(r"^[0-9a-f]{64}$")
IDENT = re.compile(r"^[A-Za-z0-9._-]{1,180}$")
PLACEHOLDERS = {"unavailable", "unknown", "none", "null", "pending", "n/a", "todo"}
MAX_SOURCE_BYTES = 16 * 1024 * 1024


class ReceiptError(ValueError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ReceiptError(message)


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def text_digest(text: str) -> str:
    require(isinstance(text, str) and bool(text.strip()), "Missing non-empty event text")
    return digest(text.encode("utf-8"))


def identifier(value) -> str:
    require(isinstance(value, str) and bool(IDENT.fullmatch(value))
            and value.lower() not in PLACEHOLDERS, "Invalid or placeholder native identifier")
    return value


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, "Duplicate JSON field")
        result[key] = value
    return result


def _json(data: bytes):
    try:
        return json.loads(data.decode("utf-8"), object_pairs_hook=_pairs,
                          parse_constant=lambda _: (_ for _ in ()).throw(ReceiptError("Non-finite JSON number")))
    except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ReceiptError("Invalid UTF-8 JSON evidence") from exc


def _keys(obj, required: set[str]) -> None:
    require(isinstance(obj, dict) and set(obj) == required, "Missing or unexpected receipt fields")


def relative_file(root: Path, value: str) -> Path:
    require(isinstance(value, str) and bool(value) and "\\" not in value
            and ":" not in value and "\x00" not in value, "Invalid relative evidence path")
    pure = PurePosixPath(value)
    require(not pure.is_absolute() and ".." not in pure.parts, "Evidence path escapes workspace")
    target = (root / value).resolve()
    base = root.resolve()
    # The repository's explicit managed-drive mount is the only external alias.
    if pure.parts[:2] == (".gdrive", "managed"):
        base = (root / ".gdrive/managed").resolve()
    require(target.is_relative_to(base) and ev.is_file(target), "Evidence missing or escaping its allowed root")
    return target


def _ref_path(root: Path, ref: dict) -> Path:
    _keys(ref, {"path", "sha256"})
    require(isinstance(ref["sha256"], str) and bool(HEX.fullmatch(ref["sha256"])), "Invalid source hash")
    return relative_file(root, ref["path"])


def _read_ref(root: Path, ref: dict) -> bytes:
    path = _ref_path(root, ref)
    # Bound the read itself, including files that grow after stat().
    data = ev.read_bytes(path, MAX_SOURCE_BYTES + 1)
    require(len(data) <= MAX_SOURCE_BYTES, "Evidence exceeds size limit")
    require(digest(data) == ref["sha256"], "Source bytes do not match recorded hash")
    return data


def _verify_input(root: Path, ref: dict) -> None:
    path = _ref_path(root, ref)
    hasher = hashlib.sha256()
    preserved = ev.original(path)
    if preserved is not None:
        hasher.update(preserved)
    else:
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                hasher.update(chunk)
    require(hasher.hexdigest() == ref["sha256"], "Reviewed input bytes do not match recorded hash")


def _jsonl(data: bytes) -> list[dict]:
    rows = [_json(line) for line in data.splitlines() if line.strip()]
    require(bool(rows) and all(isinstance(row, dict) for row in rows), "Invalid JSONL event stream")
    return rows


def _message_text(message) -> str:
    require(isinstance(message, dict), "Missing native message")
    content = message.get("content")
    if isinstance(content, str):
        return content
    require(isinstance(content, list) and all(isinstance(v, dict) for v in content), "Invalid message content")
    return "\n".join(v["text"] for v in content if v.get("type") == "text" and isinstance(v.get("text"), str))


@dataclass(frozen=True)
class NativeReceipt:
    provider: str
    agent: str
    session_id: str
    role: str
    stage: str
    sha256: str
    identifiers: frozenset[str]
    input_count: int
    scope: tuple[tuple[str, str], ...]


def _hub(receipt: dict, sources: dict) -> set[str]:
    native = receipt["native"]
    _keys(native, {"start_step", "end_step"})
    start, end = native["start_step"], native["end_step"]
    require(type(start) is int and type(end) is int and 0 <= start < end and end - start >= 2,
            "Invalid native step interval")
    transcript, state = _json(sources["transcript"]), _json(sources["observation"])
    require(isinstance(transcript, dict) and isinstance(state, dict), "Invalid Hub snapshots")
    sid = receipt["session_id"]
    require(transcript.get("source") == "hub_rpc" and transcript.get("conversation_id") == sid
            and state.get("conversation_id") == sid, "Hub source or session mismatch")
    require(state.get("status") == "CASCADE_RUN_STATUS_IDLE"
            and type(state.get("num_total_steps")) is int and state["num_total_steps"] == end,
            "Hub observation is not the requested terminal interval")
    require(transcript.get("start_step") == start and transcript.get("end_step") == end
            and type(transcript.get("total_steps")) is int and transcript["total_steps"] >= end,
            "Hub transcript interval mismatch")
    steps = transcript.get("steps")
    require(isinstance(steps, list) and len(steps) == end - start
            and all(isinstance(v, dict) and type(v.get("step_index")) is int for v in steps),
            "Incomplete Hub steps")
    require([v["step_index"] for v in steps] == list(range(start, end)), "Non-contiguous Hub steps")
    users = [v for v in steps if v.get("type") == "CORTEX_STEP_TYPE_USER_INPUT"]
    require(len(users) == 1 and users[0] is steps[0]
            and users[0].get("status") == "CORTEX_STEP_STATUS_DONE", "Mixed or missing Hub request")
    last = steps[-1]
    require(last.get("type") == "CORTEX_STEP_TYPE_PLANNER_RESPONSE"
            and last.get("status") == "CORTEX_STEP_STATUS_DONE", "Missing completed Hub response")
    request, response = users[0].get("content"), last.get("content")
    require(text_digest(request) == receipt["request_sha256"]
            and text_digest(response) == receipt["response_sha256"], "Hub request/response hash mismatch")
    require(state.get("last_user_input") == request and state.get("last_response") == response,
            "Hub terminal snapshot belongs to another request or response")
    return {sid}


def _claude(receipt: dict, sources: dict) -> set[str]:
    native = receipt["native"]
    _keys(native, {"request_id", "message_id", "result_id"})
    for value in native.values():
        identifier(value)
    sid = receipt["session_id"]
    stream = _jsonl(sources["stream"])
    require(all(v.get("session_id") == sid for v in stream), "CLI stream has missing or mixed sessions")
    results = [v for v in stream if v.get("type") == "result"]
    require(len(results) == 1 and results[0] is stream[-1], "CLI stream lacks one final result")
    result = results[0]
    require(result.get("session_id") == sid and result.get("uuid") == native["result_id"]
            and result.get("subtype") == "success" and result.get("is_error") is False
            and result.get("terminal_reason") == "completed", "CLI result did not complete successfully")
    observation = _json(sources["observation"])
    _keys(observation, {"session_id", "exit_code", "completed", "stdout_sha256"})
    require(observation["session_id"] == sid and type(observation["exit_code"]) is int
            and observation["exit_code"] == 0 and observation["completed"] is True
            and observation["stdout_sha256"] == digest(sources["stream"]), "CLI process observation mismatch or failure")
    journal = _jsonl(sources["journal"])
    events = {}
    for row in journal:
        require(row.get("sessionId") == sid, "Journal has missing or mixed sessions")
        if row.get("uuid") is None:
            require(row.get("type") not in {"user", "assistant", "progress", "attachment"},
                    "Journal event lacks its native UUID")
            continue
        uid = identifier(row["uuid"])
        require(uid not in events, "Duplicate journal event ID")
        require(row.get("sessionId") == sid, "Journal mixes sessions")
        events[uid] = row
    request = events.get(native["request_id"])
    require(request is not None and request.get("type") == "user"
            and request.get("message", {}).get("role") == "user", "Missing native user event")
    require(text_digest(_message_text(request["message"])) == receipt["request_sha256"], "CLI input hash mismatch")
    response = result.get("result")
    require(text_digest(response) == receipt["response_sha256"], "CLI response hash mismatch")
    stream_assistants = [v for v in stream if v.get("type") == "assistant"]
    require(bool(stream_assistants), "CLI stream lacks the final assistant message")
    final_message = stream_assistants[-1].get("message")
    require(isinstance(final_message, dict) and final_message.get("id") == native["message_id"]
            and final_message.get("role") == "assistant" and _message_text(final_message) == response,
            "CLI final assistant message differs from journal/result binding")
    finals = [v for v in events.values() if v.get("type") == "assistant"
              and isinstance(v.get("message"), dict) and v["message"].get("role") == "assistant"
              and v["message"].get("id") == native["message_id"] and _message_text(v["message"]) == response]
    require(len(finals) == 1, "Native assistant message does not match final result")
    # The selected assistant event must descend from this exact user request.
    cursor, visited = finals[0], set()
    pending_tools, seen_results = set(), set()
    while cursor["uuid"] != native["request_id"]:
        require(cursor["uuid"] not in visited, "Journal parent cycle")
        visited.add(cursor["uuid"])
        if cursor.get("type") == "user":
            content = cursor.get("message", {}).get("content")
            require(isinstance(content, list) and bool(content) and all(isinstance(b, dict) for b in content),
                    "Another user request interrupts the result chain")
            results = [b for b in content if b.get("type") == "tool_result"]
            require(bool(results) and all(b.get("type") in {"tool_result", "text"} for b in content),
                    "Non-tool user input interrupts the result chain")
            for block in content:
                if block.get("type") == "text":
                    text = block.get("text")
                    require(isinstance(text, str) and text.strip().startswith("<system-reminder>")
                            and text.strip().endswith("</system-reminder>"),
                            "User text outside a tool system reminder interrupts the result chain")
            for block in results:
                tool_id = identifier(block.get("tool_use_id"))
                require(tool_id not in seen_results, "Duplicate tool result in journal parent chain")
                seen_results.add(tool_id)
                pending_tools.add(tool_id)
        elif cursor.get("type") == "assistant":
            content = cursor.get("message", {}).get("content")
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_use":
                        pending_tools.discard(identifier(block.get("id")))
        cursor = events.get(cursor.get("parentUuid"))
        require(cursor is not None, "Incomplete journal parent chain")
    require(not pending_tools, "Tool result has no preceding matching tool call")
    return {sid, *native.values()}


def validate_receipt(path: Path, root: Path, *, plan_path: str, stage: str,
                     role: str, review_hash: str, agent: str | None = None) -> NativeReceipt:
    """Validate one role/request receipt against live input files and scope."""
    try:
        root = root.resolve()
        require(path.is_file() and path.stat().st_size <= 256 * 1024, "Receipt missing or oversized")
        with path.open("rb") as stream:
            raw = stream.read(256 * 1024 + 1)
        require(len(raw) <= 256 * 1024, "Receipt missing or oversized")
        receipt = _json(raw)
        _keys(receipt, {"schema_version", "provider", "agent", "role", "plan_path", "stage", "scope",
                        "session_id", "review_hash", "inputs", "request_sha256", "response_sha256",
                        "sources", "native"})
        require(type(receipt["schema_version"]) is int and receipt["schema_version"] == 1, "Unknown receipt schema")
        require(stage in {"t1", "t2", "t4"} and role in {"architecture", "detail"}, "Invalid receipt context")
        require(receipt["plan_path"] == plan_path and receipt["stage"] == stage and receipt["role"] == role,
                "Receipt belongs to another plan, stage or role")
        relative_file(root, plan_path)
        require(isinstance(review_hash, str) and review_hash.startswith("sha256:")
                and bool(HEX.fullmatch(review_hash[7:])) and receipt["review_hash"] == review_hash,
                "Review scope hash mismatch")
        scope_keys = {"acceptance", "design"} if stage in {"t1", "t2"} else {"acceptance"}
        _keys(receipt["scope"], scope_keys)
        scope_files = {key: relative_file(root, value) for key, value in receipt["scope"].items()}
        # Reuse the formal canonical slicer, including its signed-tail exclusions.
        # Lazy import avoids a module-initialization cycle with plan_lint.
        from plan_lint import compute_review_hash
        try:
            live_scope = compute_review_hash(stage, scope_files["acceptance"], scope_files.get("design"))
        except (ValueError, OSError) as exc:
            raise ReceiptError("Core review scope cannot be read or canonicalized") from exc
        require(live_scope == review_hash, "Core review scope changed after capture")
        identifier(receipt["session_id"])
        for key in ("request_sha256", "response_sha256"):
            require(isinstance(receipt[key], str) and bool(HEX.fullmatch(receipt[key])), "Invalid event hash")
        provider = receipt["provider"]
        providers = {"antigravity-hub": ("Gemini", "detail", {"transcript", "observation"}),
                     "claude-cli": ("Claude", "architecture", {"stream", "journal", "observation"})}
        require(isinstance(provider, str) and provider in providers, "Unknown native provider")
        owner, allowed_role, source_keys = providers[provider]
        require(receipt["agent"] == owner and (agent is None or owner == agent) and role == allowed_role,
                "Native provider, role or reviewer identity mismatch")
        inputs = receipt["inputs"]
        require(isinstance(inputs, list) and bool(inputs), "Receipt lacks reviewed input files")
        seen = set()
        for ref in inputs:
            _verify_input(root, ref)
            canonical_path = relative_file(root, ref["path"])
            require(canonical_path not in seen, "Duplicate reviewed input file")
            seen.add(canonical_path)
        _keys(receipt["sources"], source_keys)
        sources = {key: _read_ref(root, ref) for key, ref in receipt["sources"].items()}
        ids = _hub(receipt, sources) if provider == "antigravity-hub" else _claude(receipt, sources)
        return NativeReceipt(provider, owner, receipt["session_id"], role, stage, digest(raw), frozenset(ids),
                             len(inputs), tuple(sorted(receipt["scope"].items())))
    except (OSError, TypeError, KeyError, AttributeError, OverflowError) as exc:
        raise ReceiptError("Malformed or unreadable native receipt evidence") from exc


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("receipt", type=Path)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--plan", required=True)
    parser.add_argument("--stage", choices=["t1", "t2", "t4"], required=True)
    parser.add_argument("--role", choices=["architecture", "detail"], required=True)
    parser.add_argument("--review-hash", required=True)
    args = parser.parse_args(argv)
    try:
        result = validate_receipt(args.receipt, args.root, plan_path=args.plan, stage=args.stage,
                                  role=args.role, review_hash=args.review_hash)
        print(json.dumps({"ok": True, "provider": result.provider, "agent": result.agent,
                          "session_id": result.session_id, "receipt_sha256": result.sha256,
                          "input_count": result.input_count, "scope": "captured-evidence-integrity-only"}))
        return 0
    except ReceiptError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
