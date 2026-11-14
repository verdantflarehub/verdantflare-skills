"""Task execution engine for verdantflare-agent-claude."""
from __future__ import annotations

import asyncio
from contextlib import suppress
import json
import os
from pathlib import Path
import time
import uuid

from .backend import (
    detect_claude_versions,
    find_live_panel_sessions,
    list_claude_sessions,
    resolve_claude_binary,
    run_managed_turn,
    run_panel_turn,
    rename_session,
    locate_transcript_path,
    _extract_transcript_summary,
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
        self.claude_dir = Path(config.get("claude_dir") or (Path.home() / ".claude"))
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
        if name == "claude_capabilities":
            return await self.capabilities(args)
        if name == 'claude_session_rename':
            cwd = allowed_cwd(args['cwd'], self.config['allowed_roots'])
            self.store.assert_idle_for_naming(cwd, args['session_id'])
            if args['session_id'] in find_live_panel_sessions(self.claude_dir):
                raise BridgeError('SESSION_BUSY', 'Close the live session before starting a native naming connection')
            summary = _extract_transcript_summary(locate_transcript_path(self.claude_dir, cwd, args['session_id']))
            if not summary or not summary.get('cwd') or allowed_cwd(summary['cwd'], self.config['allowed_roots']) != cwd:
                raise BridgeError('WORKSPACE_DENIED', 'Existing session workspace could not be verified')
            return await rename_session(claude_binary=resolve_claude_binary(self.config.get('claude'), self.config.get('extension')),
                                        claude_dir=self.claude_dir, cwd=cwd, session_id=args['session_id'], name=args['name'])
        if name == "claude_task_start":
            if os.environ.get("VERDANTFLARE_AGENT_CLAUDE_DEPTH"):
                raise BridgeError("WORKSPACE_DENIED", "Recursive delegation to this service is disabled")
            args["cwd"] = allowed_cwd(args["cwd"], self.config["allowed_roots"])
            replay = self.store.replay("start", args)
            if replay:
                return public_task(replay)
            if args["mode"] == "panel":
                live_map = find_live_panel_sessions(self.claude_dir)
                live = live_map.get(args["session_id"])
                if not live or not live["panel_reachable"]:
                    raise BridgeError("OWNER_UNAVAILABLE", "Target VS Code Claude Code panel session is not reachable")
                if live["status"] != "idle":
                    raise BridgeError("SESSION_BUSY", f"Target VS Code Claude Code panel session is {live['status']}", retryable=True)
            task = self.store.start(args, owner=self.owner)
            return public_task(task)
        if name == "claude_task_status":
            task = self.store.task(args["task_id"])
            return {
                **public_task(task),
                **self.store.events(task["id"], args["after_event"], args["limit"]),
                "pending": self.store.pending_list(task["id"]),
                "last_event_at": task["updated"],
            }
        if name == "claude_task_result":
            result = self.store.result(args["task_id"], args.get("turn_id"))
            payload = json.dumps(result.pop("result"), ensure_ascii=False)
            offset = int(args.get("cursor", "0"))
            result.update(
                content_json=payload[offset : offset + 32768],
                next_cursor=str(offset + 32768) if offset + 32768 < len(payload) else None,
            )
            return result
        operation = name.removeprefix("claude_task_")
        if operation == "respond":
            raise BridgeError("APPROVAL_REQUIRES_UI", "Claude Code permissions must be approved in the VS Code panel UI")
        if operation == "cancel":
            task = self.store.task(args["task_id"])
            if task["turn_id"] != args["expected_turn_id"]:
                raise BridgeError("STALE_TURN", "Expected turn does not match current turn", task_id=task["id"])
            if task["mode"] == "panel" and task["state"] not in TERMINAL and task["state"] != "interrupted_unknown":
                raise BridgeError(
                    "APPROVAL_REQUIRES_UI",
                    "Panel mode does not kill the user's VS Code Claude Code session; stop the turn in the panel UI",
                    task_id=task["id"],
                )
        return public_task(self.store.enqueue(operation, args, owner=self.owner))

    async def capabilities(self, args: dict) -> dict:
        cwd = allowed_cwd(args["cwd"], self.config["allowed_roots"]) if args.get("cwd") else None
        all_sessions = list_claude_sessions(self.claude_dir, cwd)
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
        panel_ready = any(r.get("panel_reachable") for r in rows)
        claude_bin = resolve_claude_binary(self.config.get("claude"), self.config.get("extension"))
        ext_ver, cli_ver = detect_claude_versions(claude_bin, self.config.get("extension"))
        return {
            "version": "0.1.0",
            "extension_version": ext_ver,
            "cli_version": cli_ver,
            "panel": "read_verified" if panel_ready else "discovered",
            "managed": "read_verified" if (claude_bin and Path(claude_bin).exists()) else "unavailable",
            "sessions": page,
            "threads": page,
            "filtered_out_count": filtered_out_count,
            "next_cursor": next_cursor,
            "operations": ["capabilities", "session_rename", "start", "status", "result", "continue", "cancel", "respond"],
            "permission_approval": "human_ui_required",
            "permission_modes": {
                "panel": "human_ui_required",
                "managed": "bypassPermissions",
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
            session_id = task["thread_id"] or str(uuid.uuid4())
            turn_id = str(uuid.uuid4())
            self.store.transition(task_id, self.owner, "starting", thread_id=session_id, turn_id=turn_id)
            prompt = "[Delegated by verdantflare-agent-claude; do not call verdantflare-agent-claude recursively.]\n" + payload["prompt"]
            self.store.transition(task_id, self.owner, "running", thread_id=session_id, turn_id=turn_id)
            self.store.command_done(command["id"], self.owner, {"accepted": True})
            submitted = True

            def on_event(kind: str, data: dict) -> None:
                self.store.append_event(task_id, kind, data)

            def is_cancelling() -> bool:
                return self.store.task(task_id)["state"] == "cancelling"

            if task["mode"] == "panel":
                outcome = await run_panel_turn(
                    claude_dir=self.claude_dir,
                    cwd=task["cwd"],
                    session_id=session_id,
                    turn_id=turn_id,
                    prompt=prompt,
                    deadline=task["deadline"],
                    on_event=on_event,
                    is_cancelling=is_cancelling,
                )
            else:
                claude_bin = resolve_claude_binary(self.config.get("claude"), self.config.get("extension"))
                outcome = await run_managed_turn(
                    claude_binary=claude_bin,
                    claude_dir=self.claude_dir,
                    cwd=task["cwd"],
                    session_id=session_id,
                    is_resume=is_continue or bool(task["thread_id"]),
                    prompt=prompt,
                    deadline=task["deadline"],
                    on_event=on_event,
                    register_handle=lambda h: self.handles.__setitem__(task_id, h),
                    is_cancelling=is_cancelling,
                )

            if outcome.get("cancelled") or is_cancelling():
                self.store.transition(task_id, self.owner, "cancelled", result=outcome)
            else:
                self.store.transition(task_id, self.owner, "completed", result=outcome)
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
            if task["mode"] == "panel":
                raise BridgeError("APPROVAL_REQUIRES_UI", "Panel mode does not kill the user's VS Code Claude Code session; stop the turn in the panel UI")
            handle = self.handles.get(task["id"])
            if handle is None:
                raise BridgeError("BACKEND_DISCONNECTED", "Managed task process handle is unavailable")
            await handle.cancel()
        self.store.command_done(command["id"], self.owner, {"accepted": True})
