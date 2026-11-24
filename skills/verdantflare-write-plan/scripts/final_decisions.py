"""Bind a native human decision to one exact final request and delivery.

This validates local evidence, not a provider signature. Callers still validate
current policy, artifacts and technical gates; no message is sent by this module.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import re
import uuid

from delegated_policy import PolicyError, hash_shape, keys, need, path_name, ref_shape
from goal_evidence import (MAX_CAPTURE_BYTES, GoalEvidence, _automatic, _timestamp,
                           _user_text, validate_goal_evidence)
from native_receipts import ReceiptError, _json, digest, identifier
from process_receipts import canonical, content_ref

CODE = "PL-DELEGATE-H2"
ACCEPT = frozenset({"通过", "审核通过", "同意", "批准"})
CANCEL = frozenset({"取消计划", "确认取消计划", "同意取消计划"})


def target_shape(target: dict, *, repository_id: str, plan_id: str, plan_path: str) -> None:
    keys(target, {"schema_version", "kind", "repository_id", "plan_id", "plan_path", "request_id",
                  "release_id", "manifest", "cp1", "authorization_sha256", "reviewed_history_sha256",
                  "preflight_capture_sha256", "preflight_capture_bytes"})
    need(type(target["schema_version"]) is int and target["schema_version"] == 1
         and target["kind"] in {"final-delivery", "plan-cancellation"}, CODE, "Unsupported final review object")
    need(target["repository_id"] == repository_id and target["plan_id"] == plan_id
         and target["plan_path"] == plan_path, CODE, "Final review belongs to another repository or plan")
    path_name(plan_path)
    for value in (repository_id, plan_id, plan_path):
        need(isinstance(value, str) and bool(value) and not any(c in value for c in "\r\n`<>"),
             CODE, "Final review identity cannot be displayed safely")
    try:
        need(str(uuid.UUID(target["request_id"])) == target["request_id"], CODE, "Invalid final review request ID")
    except (TypeError, ValueError, AttributeError):
        raise PolicyError(CODE, "Invalid final review request ID") from None
    for key in ("authorization_sha256", "reviewed_history_sha256", "preflight_capture_sha256"):
        hash_shape(target[key])
    need(type(target["preflight_capture_bytes"]) is int and 0 < target["preflight_capture_bytes"] <= MAX_CAPTURE_BYTES,
         CODE, "Invalid final review source prefix length")
    if target["kind"] == "final-delivery":
        need(isinstance(target["release_id"], str) and
             re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", target["release_id"]), CODE, "Invalid delivery release ID")
        ref_shape(target["manifest"]); ref_shape(target["cp1"])
        need(not any(c in target["manifest"]["path"] for c in "\r\n`<>"), CODE, "Manifest path cannot be displayed safely")
        need(content_ref(target["cp1"]["path"]) == target["cp1"], CODE, "Final review requires a content-addressed CP1 record")
    else:
        need(target["release_id"] is None and target["manifest"] is None and target["cp1"] is None,
             CODE, "Cancellation cannot carry a successful delivery")


def request_text(target: dict) -> str:
    """Deterministic final request. Caller presents it; this function never sends."""
    target_shape(target, repository_id=target.get("repository_id"), plan_id=target.get("plan_id"),
                 plan_path=target.get("plan_path"))
    if target["kind"] == "final-delivery":
        body = (f"请审核计划 {target['plan_id']} 的最终交付版本 {target['release_id']}。\n\n"
                f"交付清单：`{target['manifest']['path']}`\n"
                f"清单 SHA-256：`{target['manifest']['sha256']}`\n"
                f"技术验收收据：`{target['cp1']['path']}`\n\n"
                "通过请回复“通过”；需要修改时请直接说明。")
    else:
        body = (f"请确认取消计划 {target['plan_id']}。取消后仅作废弃归档，不记为成功交付。\n\n"
                "确认取消请回复“确认取消计划”；保留计划或调整范围时请直接说明。")
    return body + f"\n\n审核编号：{target['request_id']}\n审核对象 SHA-256：{digest(canonical(target))}"


def _rows(raw: bytes) -> tuple[list[bytes], list[dict]]:
    need(isinstance(raw, bytes) and 0 < len(raw) <= MAX_CAPTURE_BYTES and raw.endswith(b"\n")
         and b"\r" not in raw, CODE, "Final decision capture is missing, incomplete or oversized")
    lines = raw.splitlines(keepends=True)
    rows = [_json(line) for line in lines]
    need(all(isinstance(row, dict) and isinstance(row.get("payload"), dict) for row in rows),
         CODE, "Final decision source has malformed records")
    return lines, rows


def goal_projection(conversation_capture: bytes) -> bytes:
    lines, rows = _rows(conversation_capture)
    return b"".join(line for line, row in zip(lines, rows)
                    if not (row.get("type") == "response_item" and row["payload"].get("role") == "assistant"))


def _history_hash(lines: list[bytes], rows: list[dict]) -> str:
    chosen = []
    for index, (line, row) in enumerate(zip(lines, rows)):
        if row.get("type") != "response_item":
            continue
        try:
            text = _user_text(row)
        except ReceiptError:
            text = None
        next_row = rows[index + 1] if index + 1 < len(rows) else {}
        companion = next_row.get("type") == "event_msg" and next_row.get("payload", {}).get("type") == "user_message"
        if not (text and _automatic(text) and not companion):
            chosen.append(line)
    return digest(b"".join(chosen))


@dataclass(frozen=True)
class FinalDecision:
    kind: str
    request_id: str
    request_sha256: str
    decision_sha256: str
    target_sha256: str
    current_goal: GoalEvidence
    reviewed_prefix: bytes = field(repr=False)


def validate_final_decision(target: dict, conversation_capture: bytes, current_capture: bytes,
                            observation: bytes, *, session_id: str, repository_id: str,
                            plan_id: str, plan_path: str) -> FinalDecision:
    """Keep full current history and prove its only new human event is this reply."""
    try:
        identifier(session_id)
        target_shape(target, repository_id=repository_id, plan_id=plan_id, plan_path=plan_path)
        lines, rows = _rows(conversation_capture)
        need(goal_projection(conversation_capture) == current_capture,
             CODE, "Conversation and current user capture differ")
        user_lines, user_rows = _rows(current_capture)
        n = target["preflight_capture_bytes"]
        need(n <= len(current_capture) and current_capture[:n].endswith(b"\n")
             and digest(current_capture[:n]) == target["preflight_capture_sha256"],
             CODE, "Prepared final review source prefix changed")
        current_goal = validate_goal_evidence(
            current_capture, observation, session_id=session_id,
            authorization_event_sha256=target["authorization_sha256"],
            user_history_sha256=_history_hash(user_lines, user_rows), observation_sha256=digest(observation))
        text = request_text(target)
        requests = []
        previous_time = float('-inf')
        for index, row in enumerate(rows[1:], 1):
            stamp = _timestamp(row)
            need(stamp >= previous_time, CODE, "Final review chronology is reversed")
            previous_time = stamp
            p = row["payload"]
            for obj in (row, p):
                for key in ("session_id", "sessionId", "thread_id", "threadId"):
                    need(key not in obj or obj[key] == session_id, CODE, "Final review source session differs")
            if row.get("type") != "response_item" or p.get("role") != "assistant":
                continue
            blocks = p.get("content")
            if blocks == [{"type": "output_text", "text": text}]:
                need(p.get("type") == "message" and p.get("phase") == "final_answer"
                     and p.get("status") == "completed", CODE, "Review request is not a completed final answer")
                requests.append(index)
        need(len(requests) == 1, CODE, "Expected one exact native final review request")
        start = requests[0]
        before_request = goal_projection(b"".join(lines[:start]))
        need(len(before_request) >= n, CODE, "Final review request predates its prepared source")
        reply_index = None
        for index in range(start + 1, len(rows)):
            row = rows[index]; p = row["payload"]
            if row.get("type") == "response_item" and p.get("role") == "assistant":
                need(p.get("phase") != "final_answer", CODE, "A later final answer replaced the review request")
            if row.get("type") != "response_item" or p.get("role") != "user":
                continue
            reply = _user_text(row)
            next_row = rows[index + 1] if index + 1 < len(rows) else {}
            human = (next_row.get("type") == "event_msg" and next_row.get("payload", {}).get("type") == "user_message")
            if _automatic(reply) and not human:
                continue
            normalized = reply.strip().rstrip("。.!！")
            need(normalized in (ACCEPT if target["kind"] == "final-delivery" else CANCEL),
                 CODE, "User did not explicitly accept this final review")
            need(human and next_row["payload"].get("message") == reply,
                 CODE, "Final reply lacks its matching native human event")
            reply_index = index
            break
        need(reply_index is not None, CODE, "Final review has no human reply")
        prefix = goal_projection(b"".join(lines[:reply_index]))
        validate_goal_evidence(prefix, observation, session_id=session_id,
            authorization_event_sha256=target["authorization_sha256"],
            user_history_sha256=target["reviewed_history_sha256"], observation_sha256=digest(observation))
        # Prove every later user record is a recognized automatic continuation.
        for index in range(reply_index + 2, len(rows)):
            row = rows[index]
            if row.get("type") == "response_item" and row["payload"].get("role") == "user":
                next_row = rows[index + 1] if index + 1 < len(rows) else {}
                need(_automatic(_user_text(row)) and not (next_row.get("type") == "event_msg"
                     and next_row.get("payload", {}).get("type") == "user_message"),
                     CODE, "New human input invalidates this final review")
        return FinalDecision(target["kind"], target["request_id"], digest(lines[start]),
                             digest(lines[reply_index]), digest(canonical(target)), current_goal, prefix)
    except PolicyError:
        raise
    except (ReceiptError, KeyError, TypeError, ValueError, AttributeError, OverflowError):
        raise PolicyError(CODE, "Final review native evidence is invalid") from None
