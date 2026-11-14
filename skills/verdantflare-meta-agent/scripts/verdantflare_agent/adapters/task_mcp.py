"""Shared helpers for task-oriented MCP adapters; no provider routing guesses."""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import re

from ..store import Conflict, digest, encode
from ..transport import MCPClient
from ..worker import NotAccepted, Observation, Reconciliation

TERMINAL = {"completed", "failed", "cancelled", "timed_out"}


class ServiceError(RuntimeError):
    def __init__(self, code):
        self.code = code if isinstance(code, str) else "MCP_SERVICE_ERROR"


def unwrap(result):
    value = result.get("structuredContent")
    if value is None:
        blocks = [block.get("text", "") for block in result.get("content", []) if block.get("type") == "text"]
        try:
            value = json.loads("".join(blocks))
        except (ValueError, TypeError):
            raise ServiceError("INVALID_MCP_RESULT") from None
    if not isinstance(value, dict):
        raise ServiceError("INVALID_MCP_RESULT")
    if result.get("isError") or ("code" in value and "task_id" not in value):
        raise ServiceError(value.get("code"))
    return value


def acknowledgment(message, result, turn_id):
    answer = result.get("answer")
    if not isinstance(answer, str):
        return None
    body = message["body"]
    expected = {"message_id": message["id"], "recipient": body["recipient"], "generation": message["route_generation"],
                "assignment_id": body["assignment_id"], "revision": body["revision"]}
    found = []
    for line in answer.splitlines():
        if not line.startswith("VF_AGENT_ACK "):
            continue
        try:
            ack = json.loads(line[len("VF_AGENT_ACK "):])
        except ValueError:
            continue
        if (not isinstance(ack, dict) or set(ack) != set(expected) | {"action"}
                or any(ack[key] != value for key, value in expected.items())
                or not isinstance(ack["action"], str) or not ack["action"].strip()):
            continue
        found.append({key: value for key, value in ack.items() if key != "message_id"} | {"turn_id": turn_id})
    if not found or any(value != found[0] for value in found):
        return None
    return found[0]


def public_context(value, workspace):
    """Keep environment-owned locations out of inter-agent prompts."""
    if isinstance(value, dict):
        return {key: public_context(item, workspace) for key, item in value.items()}
    if isinstance(value, list):
        return [public_context(item, workspace) for item in value]
    if not isinstance(value, str):
        return value
    replacements = [(str(workspace), "."), (str(Path.home()), "%USERPROFILE%")]
    hostname = os.environ.get("COMPUTERNAME")
    if hostname:
        replacements.append((hostname, "%COMPUTERNAME%"))
    for source, target in replacements:
        for spelling in {source, source.replace("\\", "/"), source.replace("/", "\\")}:
            value = re.sub(re.escape(spelling), lambda _: target, value, flags=re.IGNORECASE if os.name == "nt" else 0)
    return value


def prompt_for(message, endpoint, handoff):
    body = message["body"]
    header = {key: body[key] for key in ("message_id", "kind", "plan_id", "task_key", "assignment_id", "revision", "causation_id")}
    header.update(recipient=body["recipient"], generation=message["route_generation"], logical_session_name=endpoint["display_name"])
    ack = {"message_id": message["id"], "recipient": body["recipient"], "generation": message["route_generation"],
           "assignment_id": body["assignment_id"], "revision": body["revision"], "action": "Describe the action actually taken."}
    if body["kind"] in {"assignment", "review_request"}:
        operation = "Execute or review only this registered task, following its plan, change record and existing authorization."
    else:
        operation = "Process this result/blocked notification as coordinator. Check evidence and record the appropriate next action; do not re-execute the original assignment merely because its handoff is attached."
    prompt = ("[VerdantFlare plan-bound collaboration]\n" + encode(header) + "\n" + operation
              + "\nTask context (paths are workspace-relative):\n" + encode(public_context(handoff, endpoint["workspace"]))
              + "\nMessage payload:\n" + encode(public_context(body["payload"], endpoint["workspace"]))
              + "\nAfter identifying this message, include exactly one standalone acknowledgment line in your final answer. "
              "Preserve identity fields and replace action with what you actually did. A receipt is not task acceptance or user approval.\n"
              + "VF_AGENT_ACK " + encode(ack)
              + "\nDo not call this same Agent service recursively. The persistent coordinator worker collects your result and sends it to the registered return Session.")
    if len(prompt.encode("utf-8")) > 65536:
        raise NotAccepted("UNSUPPORTED_CAPABILITY")
    return prompt


class TaskMCPAdapter:
    prefix = ""
    agent = ""
    native_key = ""

    def __init__(self, argv, store, *, client=None):
        self.client = client or MCPClient(argv)
        self.store = store

    async def call(self, suffix, arguments):
        return unwrap(await self.client.call(self.prefix + suffix, arguments))

    def request_id(self, message):
        return "vf-agent-" + digest([str(self.store.path), message["id"]])

    def check_target(self, value, endpoint, *, require_turn=False):
        if value.get(self.native_key) != endpoint["native_session_id"] or value.get("mode", endpoint["mode"]) != endpoint["mode"]:
            raise Conflict("BACKEND_TARGET_MISMATCH")
        if require_turn and not value.get("turn_id"):
            raise Conflict("BACKEND_TURN_NOT_BOUND")

    async def prepare(self, message, endpoint, handoff):
        raise NotImplementedError

    async def _receipt(self, message, endpoint, accepted, request, *, wait):
        task_id = accepted.get("task_id")
        if not isinstance(task_id, str) or not task_id:
            raise Conflict("BACKEND_TASK_NOT_BOUND")
        while True:
            status = await self.call("task_status", {"task_id": task_id, "limit": 1})
            if status.get("task_id") != task_id:
                raise Conflict("BACKEND_TASK_MISMATCH")
            if status.get(self.native_key) is not None:
                self.check_target(status, endpoint)
            turn_id = status.get("turn_id")
            old_turn = request["arguments"].get("expected_turn_id") if request["tool"].endswith("continue") else None
            if turn_id and turn_id != old_turn:
                self.check_target(status, endpoint, require_turn=True)
                return {"session_id": endpoint["native_session_id"], "task_id": task_id, "turn_id": turn_id}
            if status.get("state") in TERMINAL or status.get("state") == "interrupted_unknown":
                raise Conflict("BACKEND_SUBMISSION_UNRESOLVED")
            if not wait:
                return None
            await asyncio.sleep(.25)

    async def deliver(self, message, endpoint, handoff):
        if endpoint["agent"] != self.agent:
            raise NotAccepted("UNSUPPORTED_CAPABILITY")
        journal = self.store.prepared_request(message["id"])
        if journal is None:
            try:
                request = await self.prepare(message, endpoint, handoff)
            except ServiceError as error:
                if error.code in {"SESSION_BUSY", "PLUGIN_UNAVAILABLE", "OWNER_NOT_FOUND", "OWNER_UNAVAILABLE", "TASK_NOT_FOUND"}:
                    raise NotAccepted("SESSION_BUSY" if error.code == "SESSION_BUSY" else "OFFLINE") from None
                raise
            self.store.prepare_request(message["id"], request)
            journal = self.store.prepared_request(message["id"])
        request = journal["request"]
        accepted = journal["accepted"]
        if accepted is None:
            try:
                accepted = unwrap(await self.client.call(request["tool"], request["arguments"]))
            except ServiceError as error:
                # These tool errors occur before the backend accepts a command.
                if error.code in {"SESSION_BUSY", "PLUGIN_UNAVAILABLE", "OWNER_NOT_FOUND", "OWNER_UNAVAILABLE"}:
                    raise NotAccepted("SESSION_BUSY" if error.code == "SESSION_BUSY" else "OFFLINE") from None
                raise
            self.store.accepted_request(message["id"], accepted)
        return await self._receipt(message, endpoint, accepted, request, wait=True)

    async def reconcile(self, message, endpoint):
        journal = self.store.prepared_request(message["id"])
        if not journal:
            return Reconciliation(retry_safe_evidence="No external request was prepared by this adapter.")
        if journal["accepted"]:
            receipt = await self._receipt(message, endpoint, journal["accepted"], journal["request"], wait=False)
            return Reconciliation(receipt=receipt)
        request = journal["request"]
        if "request_id" in request["arguments"] and request["tool"] in {self.prefix + "task_start", self.prefix + "task_continue"}:
            return Reconciliation(retry_safe_evidence="Frozen request_id and arguments; this service contract returns the original task on identical replay.")
        return Reconciliation()

    async def observe(self, message, endpoint):
        receipt = message["receipt"]
        after = message["cursor"] if type(message["cursor"]) is int else 0
        status = await self.call("task_status", {"task_id": receipt["task_id"], "after_event": after, "limit": 200})
        self.check_target(status, endpoint)
        if status.get("task_id") != receipt["task_id"]:
            raise Conflict("BACKEND_TASK_MISMATCH")
        # The task may already be on another turn; result is always read by our exact turn.
        if status.get("turn_id") == receipt["turn_id"] and status.get("state") not in TERMINAL:
            return Observation(cursor=status.get("next_event", after))
        pieces = []
        cursor, seen, terminal = None, set(), None
        while True:
            args = {"task_id": receipt["task_id"], "turn_id": receipt["turn_id"]}
            if cursor is not None:
                args["cursor"] = cursor
            page = await self.call("task_result", args)
            self.check_target(page, endpoint, require_turn=True)
            if page.get("task_id") != receipt["task_id"] or page.get("turn_id") != receipt["turn_id"]:
                raise Conflict("RESULT_IDENTITY_MISMATCH")
            if not page.get("ready"):
                return Observation(cursor=after)
            if page.get("state") not in TERMINAL or (terminal and terminal != page["state"]):
                raise Conflict("RESULT_STATE_MISMATCH")
            terminal = page["state"]
            chunk = page.get("content_json")
            if not isinstance(chunk, str):
                raise Conflict("INVALID_RESULT_PAGE")
            pieces.append(chunk)
            cursor = page.get("next_cursor")
            if cursor is None:
                break
            if not isinstance(cursor, str) or not cursor.isdigit() or cursor in seen or (seen and int(cursor) <= max(map(int, seen))):
                raise Conflict("RESULT_CURSOR_NOT_ADVANCING")
            seen.add(cursor)
        result = json.loads("".join(pieces))
        if not isinstance(result, dict):
            raise Conflict("INVALID_RESULT_OBJECT")
        ack = acknowledgment(message, result, receipt["turn_id"])
        return Observation(cursor=status.get("next_event", after), terminal=terminal, result=result, acknowledgment=ack)

    async def close(self):
        await self.client.close()
