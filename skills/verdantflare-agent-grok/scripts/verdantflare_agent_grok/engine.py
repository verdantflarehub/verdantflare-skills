"""Task execution engine for verdantflare-agent-grok (Grok CLI + AFK Pilot)."""
from __future__ import annotations

import asyncio
from contextlib import suppress
import json
import os
from pathlib import Path
import time
import uuid

from .backend import (
    detect_grok_versions,
    list_grok_sessions,
    run_acp_turn,
    run_headless_turn,
)
from .errors import BridgeError
from .schema import validate
from .store import Store, TERMINAL, allowed_cwd


def public_task(task: dict) -> dict:
    return {
        "task_id": task["id"],
        "state": task["state"],
        "mode": task["mode"],
        "session_id": task["thread_id"],
        "thread_id": task["thread_id"],
        "turn_id": task["turn_id"],
        "created_at": task["created"],
        "deadline_at": task["deadline"],
    }


class Engine:
    def __init__(self, config: dict):
        self.config = config
        self.store = Store(config["database"])
        self.grok_home = Path(config.get("grok_home") or (Path.home() / ".grok"))
        self.owner = f"{os.getpid()}:{uuid.uuid4()}"
        self.runs: dict[str, asyncio.Task] = {}
        self.handles: dict[str, object] = {}
        self.worker: asyncio.Task | None = None
        self.heartbeat_worker: asyncio.Task | None = None
        self.stopping = False

    async def start(self) -> None:
        self.store.heartbeat(self.owner)
        self.store.mark_lost_owners(time.time() - 30)
        self.worker = asyncio.create_task(self._work())
        self.heartbeat_worker = asyncio.create_task(self._heartbeat())

    async def close(self) -> None:
        self.stopping = True
        if self.heartbeat_worker:
            self.heartbeat_worker.cancel()
            with suppress(asyncio.CancelledError):
                await self.heartbeat_worker
        if self.worker:
            self.worker.cancel()
            with suppress(asyncio.CancelledError):
                await self.worker
        for run in list(self.runs.values()):
            run.cancel()
        for run in list(self.runs.values()):
            with suppress(asyncio.CancelledError):
                await run

    async def call(self, name: str, arguments: dict) -> dict:
        args = validate(name, arguments or {})
        if name == "grok_capabilities":
            return await self.capabilities(args)
        if name == "grok_task_start":
            if os.environ.get("VERDANTFLARE_AGENT_GROK_DEPTH"):
                raise BridgeError("WORKSPACE_DENIED", "Recursive delegation to this service is disabled")
            args["cwd"] = allowed_cwd(args["cwd"], self.config["allowed_roots"])
            replay = self.store.replay("start", args)
            if replay:
                return public_task(replay)
            task = self.store.start(args)
            return public_task(task)
        if name == "grok_task_status":
            task = self.store.task(args["task_id"])
            return {
                **public_task(task),
                **self.store.events(task["id"], args["after_event"], args["limit"]),
                "pending": self.store.pending_list(task["id"]),
                "last_event_at": task["updated"],
            }
        if name == "grok_task_result":
            result = self.store.result(args["task_id"], args.get("turn_id"))
            payload = json.dumps(result.pop("result"), ensure_ascii=False)
            offset = int(args.get("cursor", "0"))
            result.update(
                content_json=payload[offset : offset + 32768],
                next_cursor=str(offset + 32768) if offset + 32768 < len(payload) else None,
            )
            return result
        operation = name.removeprefix("grok_task_")
        if operation == "respond":
            raise BridgeError("APPROVAL_REQUIRES_UI", "Interactive approvals must be handled in the AFK Pilot UI")
        return public_task(self.store.enqueue(operation, args))

    async def capabilities(self, args: dict) -> dict:
        cwd = allowed_cwd(args["cwd"], self.config["allowed_roots"]) if args.get("cwd") else None
        all_sessions = list_grok_sessions(self.grok_home, cwd)
        rows = []
        filtered_out_count = 0
        for sess in all_sessions:
            if not sess.get("cwd"):
                continue
            try:
                allowed_cwd(sess["cwd"], self.config["allowed_roots"])
            except BridgeError:
                filtered_out_count += 1
                continue
            rows.append(sess)
        offset = int(args["cursor"]) if args.get("cursor") and str(args["cursor"]).isdigit() else 0
        limit = args["limit"]
        page = rows[offset : offset + limit]
        next_cursor = str(offset + limit) if offset + limit < len(rows) else None
        grok_exists = Path(self.config["grok"]).exists()
        ext_exists = bool(self.config.get("extension") and Path(self.config["extension"]).exists())
        ext_ver, cli_ver = detect_grok_versions(self.config["grok"], self.config.get("extension"))
        return {
            "version": "0.1.0",
            "afk_pilot_version": ext_ver,
            "cli_version": cli_ver,
            "acp": "read_verified" if grok_exists else "unavailable",
            "afk_pilot_ui": "read_verified" if ext_exists else "discovered",
            "headless": "read_verified" if grok_exists else "unavailable",
            "sessions": page,
            "threads": page,
            "filtered_out_count": filtered_out_count,
            "next_cursor": next_cursor,
            "operations": ["capabilities", "start", "status", "result", "continue", "cancel", "respond"],
            "display_Views": ["grok.chat", "grok.projects"],
            "permission_modes": {
                "acp": "always_approve",
                "headless": "noninteractive",
            },
        }

    async def _work(self) -> None:
        while True:
            command = self.store.claim(self.owner)
            if command:
                task_id = command["task_id"]
                try:
                    if command["operation"] in ("start", "continue"):
                        previous = self.runs.get(task_id)
                        if previous:
                            await previous
                        self.runs[task_id] = asyncio.create_task(self._execute(command))
                    else:
                        await self._control(command)
                except BridgeError as error:
                    self.store.command_done(command["id"], self.owner, error.as_dict())
                    self.store.append_event(task_id, "control_error", error.as_dict())
            else:
                await asyncio.sleep(0.1)

    async def _heartbeat(self) -> None:
        while True:
            self.store.heartbeat(self.owner)
            self.store.mark_lost_owners(time.time() - 30)
            await asyncio.sleep(5)

    async def _execute(self, command: dict) -> None:
        task_id, payload = command["task_id"], command["payload"]
        task = self.store.task(task_id)
        submitted = False
        try:
            is_continue = command["operation"] == "continue"
            session_id = task["thread_id"]
            turn_id = str(uuid.uuid4())
            self.store.transition(task_id, self.owner, "starting", thread_id=session_id, turn_id=turn_id)
            raw_prompt = payload["prompt"]
            prompt = "[Delegated by verdantflare-agent-grok; do not call verdantflare-agent-grok recursively.]\n" + raw_prompt
            self.store.transition(task_id, self.owner, "running", thread_id=session_id, turn_id=turn_id)
            self.store.command_done(command["id"], self.owner, {"accepted": True})
            submitted = True

            def on_event(kind: str, data: dict) -> None:
                self.store.append_event(task_id, kind, data)

            def on_session_bound(bound_sid: str) -> None:
                self.store.transition(task_id, self.owner, "running", thread_id=bound_sid, turn_id=turn_id)

            def is_cancelling() -> bool:
                return self.store.task(task_id)["state"] == "cancelling"

            if task["mode"] == "acp":
                outcome = await run_acp_turn(
                    grok_binary=self.config["grok"],
                    grok_home=self.grok_home,
                    cwd=task["cwd"],
                    session_id=session_id,
                    is_resume=is_continue or bool(session_id),
                    prompt=prompt,
                    title_hint=raw_prompt.splitlines()[0] if raw_prompt.splitlines() else raw_prompt,
                    deadline=task["deadline"],
                    on_event=on_event,
                    on_session_bound=on_session_bound,
                    register_handle=lambda h: self.handles.__setitem__(task_id, h),
                    is_cancelling=is_cancelling,
                )
            else:
                outcome = await run_headless_turn(
                    grok_binary=self.config["grok"],
                    cwd=task["cwd"],
                    session_id=session_id,
                    prompt=prompt,
                    deadline=task["deadline"],
                    register_handle=lambda h: self.handles.__setitem__(task_id, h),
                    is_cancelling=is_cancelling,
                )

            final_sid = outcome.get("session_id") or session_id
            if outcome.get("cancelled") or is_cancelling():
                self.store.transition(task_id, self.owner, "cancelled", thread_id=final_sid, result=outcome)
            else:
                self.store.transition(task_id, self.owner, "completed", thread_id=final_sid, result=outcome)
        except asyncio.CancelledError:
            current = self.store.task(task_id)
            if current["state"] not in TERMINAL:
                self.store.transition(task_id, self.owner, "interrupted_unknown", error={"code": "WORKER_STOPPED"})
            raise
        except Exception as error:
            current = self.store.task(task_id)
            if current["state"] not in TERMINAL and current["state"] != "interrupted_unknown":
                detail = error.as_dict() if isinstance(error, BridgeError) else {"code": "INTERNAL_ERROR", "message": type(error).__name__}
                target_state = "timed_out" if isinstance(error, BridgeError) and error.code == "BACKEND_TIMEOUT" else ("failed" if not submitted or isinstance(error, BridgeError) else "interrupted_unknown")
                self.store.transition(task_id, self.owner, target_state, error=detail)
                self.store.command_done(command["id"], self.owner, detail)
        finally:
            self.handles.pop(task_id, None)

    async def _control(self, command: dict) -> None:
        task = self.store.task(command["task_id"])
        if task["turn_id"] != command["payload"]["expected_turn_id"] or task["state"] in TERMINAL:
            raise BridgeError("STALE_TURN", "Control no longer matches an active turn")
        if command["operation"] == "cancel":
            handle = self.handles.get(task["id"])
            if handle is not None:
                await handle.cancel()
        self.store.command_done(command["id"], self.owner, {"accepted": True})
