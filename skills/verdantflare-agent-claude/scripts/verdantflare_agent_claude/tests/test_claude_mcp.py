"""Unit tests for verdantflare_agent_claude store, schema, backend, and engine."""
from __future__ import annotations

import asyncio
import concurrent.futures
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from verdantflare_agent_claude.backend import (
    claude_project_dir_name,
    find_live_panel_sessions,
    list_claude_sessions,
    locate_transcript_path,
    read_peer_token,
    run_panel_turn,
)
from verdantflare_agent_claude.engine import Engine
from verdantflare_agent_claude.errors import BridgeError
from verdantflare_agent_claude.schema import validate
from verdantflare_agent_claude.store import Store, allowed_cwd, canonical


class StoreAndSchemaTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.work = self.root / "work"
        self.work.mkdir()
        self.store = Store(self.root / "state.sqlite3")

    def tearDown(self):
        self.temp.cleanup()

    def payload(self, request="req-1", **overrides):
        base = {
            "request_id": request,
            "mode": "panel",
            "cwd": canonical(self.work),
            "prompt": "Run unit test",
            "session_id": "sess-a",
            "thread_id": "sess-a",
            "timeout_seconds": 3600,
        }
        base.update(overrides)
        return base

    def assert_code(self, code, operation):
        with self.assertRaises(BridgeError) as caught:
            operation()
        self.assertEqual(code, caught.exception.code)

    def test_cross_connection_idempotency_and_conflict(self):
        def run(_):
            return Store(self.store.path).start(self.payload())["id"]

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as workers:
            ids = list(workers.map(run, range(16)))
        self.assertEqual(1, len(set(ids)))
        self.assertIsNotNone(self.store.claim("worker"))
        self.assertIsNone(self.store.claim("worker"))
        self.assert_code("REQUEST_CONFLICT", lambda: self.store.start(self.payload(prompt="different")))

    def test_parent_child_and_session_leases(self):
        task = self.store.start(self.payload())
        nested = self.work / "nested"
        nested.mkdir()
        sibling = self.root / "sibling"
        sibling.mkdir()
        self.assert_code("SESSION_BUSY", lambda: self.store.start(self.payload("r2", cwd=canonical(nested), session_id="sess-b", thread_id="sess-b")))
        self.assert_code("SESSION_BUSY", lambda: self.store.start(self.payload("r3", cwd=canonical(self.root), session_id="sess-c", thread_id="sess-c")))
        self.assert_code("SESSION_BUSY", lambda: self.store.start(self.payload("r4", cwd=canonical(sibling), session_id="sess-a", thread_id="sess-a")))
        self.store.start(self.payload("r5", cwd=canonical(sibling), session_id="sess-b", thread_id="sess-b"))
        self.assertEqual("queued", self.store.task(task["id"])["state"])

    def test_allowed_cwd_wildcard_and_restrictions(self):
        self.assertEqual(canonical(self.work), allowed_cwd(str(self.work), [self.root]))
        self.assertEqual(canonical(self.work), allowed_cwd(str(self.work), ["*"]))
        self.assertEqual(canonical(self.work), allowed_cwd(str(self.work), []))
        with self.assertRaises(BridgeError) as denied:
            allowed_cwd(str(self.root), [self.work])
        self.assertEqual("WORKSPACE_DENIED", denied.exception.code)
        self.assert_code("WORKSPACE_DENIED", lambda: allowed_cwd(".", [self.root]))

    def test_schema_validation_and_session_aliases(self):
        res = validate(
            "claude_task_start",
            {"request_id": "r1", "cwd": "C:/test", "prompt": "hi", "mode": "panel", "thread_id": "sid-1"},
        )
        self.assertEqual("sid-1", res["session_id"])
        self.assertEqual("sid-1", res["thread_id"])
        with self.assertRaises(BridgeError):
            validate("claude_task_start", {"request_id": "r1", "cwd": "C:/test", "prompt": "hi", "mode": "panel"})
        with self.assertRaises(BridgeError):
            validate("claude_task_start", {"request_id": "r1", "cwd": "C:/test", "prompt": "中" * 30000, "mode": "managed"})

    def test_lost_owner_releases_lease_and_allows_cancel(self):
        task = self.store.start(self.payload("r-lost"))
        claimed = self.store.claim("dead-owner")
        self.assertIsNotNone(claimed)
        self.store.transition(task["id"], "dead-owner", "running", thread_id="sess-a", turn_id="turn-lost")
        self.assertEqual(1, self.store.mark_lost_owners(time.time() + 10))
        self.assertEqual("interrupted_unknown", self.store.task(task["id"])["state"])
        self.assertFalse(self.store.result(task["id"])["ready"])
        # New task in same workspace must not be blocked by interrupted_unknown
        t2 = self.store.start(self.payload("r-new", session_id="sess-b", thread_id="sess-b"))
        self.assertEqual("queued", t2["state"])
        # Cancelling interrupted_unknown reconciles it to cancelled
        reconciled = self.store.enqueue("cancel", {"task_id": task["id"], "expected_turn_id": "turn-lost"})
        self.assertEqual("cancelled", reconciled["state"])


class BackendAndEngineTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.work = self.root / "workspace"
        self.work.mkdir()
        self.claude_dir = self.root / ".claude"
        (self.claude_dir / "sessions").mkdir(parents=True)
        (self.claude_dir / "projects").mkdir(parents=True)

        self.fake_pipe = self.root / "fake_pipe.bin"
        self.fake_pipe.write_bytes(b"")
        self.session_id = "11111111-2222-3333-4444-555555555555"
        self.peer_token = "a" * 32
        pid = os.getpid()
        meta_path = self.claude_dir / "sessions" / f"{pid}.json"
        meta_path.write_text(
            json.dumps(
                {
                    "pid": pid,
                    "sessionId": self.session_id,
                    "cwd": canonical(self.work),
                    "name": "Test Panel Session",
                    "status": "idle",
                    "entrypoint": "claude-vscode",
                    "messagingSocketPath": str(self.fake_pipe),
                    "updatedAt": int(time.time() * 1000),
                }
            ),
            encoding="utf-8",
        )
        digest = hashlib.sha256(str(self.fake_pipe).lower().encode("utf-8")).hexdigest()
        (self.claude_dir / "sessions" / f"{pid}.{digest}.key").write_text(
            json.dumps({"peerToken": self.peer_token}),
            encoding="utf-8",
        )
        bucket = self.claude_dir / "projects" / claude_project_dir_name(canonical(self.work))
        bucket.mkdir(parents=True, exist_ok=True)
        self.transcript = bucket / f"{self.session_id}.jsonl"
        self.transcript.write_text(
            json.dumps({"type": "user", "cwd": canonical(self.work), "message": {"role": "user", "content": "init"}}) + "\n",
            encoding="utf-8",
        )

        self.fake_cli = self.root / "fake_claude.py"
        self.fake_cli.write_text("# placeholder\n", encoding="utf-8")

        self.engine = Engine(
            {
                "database": str(self.root / "state.db"),
                "claude": sys.executable,
                "claude_dir": str(self.claude_dir),
                "allowed_roots": ["*"],
            }
        )
        await self.engine.start()

    async def asyncTearDown(self):
        await self.engine.close()
        self.temp.cleanup()

    async def until(self, task_id: str, state: str):
        for _ in range(100):
            if self.engine.store.task(task_id)["state"] == state:
                return
            await asyncio.sleep(0.03)
        self.fail(f"Expected {state}, got {self.engine.store.task(task_id)['state']}")

    async def test_capabilities_lists_panel_and_transcript_sessions(self):
        caps = await self.engine.call("claude_capabilities", {"cwd": str(self.work)})
        self.assertEqual("read_verified", caps["panel"])
        self.assertEqual(1, len(caps["sessions"]))
        self.assertEqual(self.session_id, caps["sessions"][0]["session_id"])
        self.assertTrue(caps["sessions"][0]["panel_reachable"])
        self.assertEqual("idle", caps["sessions"][0]["panel_status"])

    async def test_panel_turn_injection_and_transcript_tailing(self):
        async def simulate_extension_writer():
            for _ in range(50):
                raw = self.fake_pipe.read_bytes()
                if b'"type": "user"' in raw or b'"type":"user"' in raw:
                    break
                await asyncio.sleep(0.02)
            with self.transcript.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps({"type": "user", "message": {"role": "user", "content": "hello"}}) + "\n")
                fh.write(
                    json.dumps(
                        {
                            "type": "assistant",
                            "message": {
                                "role": "assistant",
                                "stop_reason": "end_turn",
                                "content": [
                                    {"type": "thinking", "thinking": "analyzing"},
                                    {"type": "tool_use", "id": "tu-1", "name": "Edit", "input": {"file_path": "src/demo.py"}},
                                    {"type": "text", "text": "Panel task finished."},
                                ],
                            },
                        }
                    )
                    + "\n"
                )
                fh.write(json.dumps({"type": "last-prompt", "lastPrompt": "hello"}) + "\n")

        writer_task = asyncio.create_task(simulate_extension_writer())
        started = await self.engine.call(
            "claude_task_start",
            {
                "request_id": "panel-req-1",
                "mode": "panel",
                "cwd": str(self.work),
                "session_id": self.session_id,
                "prompt": "Please inspect demo.py",
            },
        )
        await self.until(started["task_id"], "completed")
        await writer_task

        pipe_frames = [json.loads(line) for line in self.fake_pipe.read_text(encoding="utf-8").splitlines() if line.strip()]
        self.assertEqual(2, len(pipe_frames))
        self.assertEqual("auth", pipe_frames[0]["type"])
        self.assertEqual(self.peer_token, pipe_frames[0]["token"])
        self.assertEqual("user", pipe_frames[1]["type"])
        self.assertEqual(self.session_id, pipe_frames[1]["session_id"])

        res = await self.engine.call("claude_task_result", {"task_id": started["task_id"]})
        self.assertTrue(res["ready"])
        content = json.loads(res["content_json"])
        self.assertEqual("Panel task finished.", content["answer"])
        self.assertEqual(["src/demo.py"], content["file_changes"])

    async def test_managed_turn_and_continue_execution(self):
        async def fake_managed_turn(**kwargs):
            kwargs["on_event"]("assistant_text", {"text": "Managed reply"})
            return {
                "answer": f"Managed ok (resume={kwargs['is_resume']})",
                "commands": [{"id": "c1", "command": "pytest", "exit_code": None}],
                "file_changes": [],
                "unresolved": [],
            }

        with patch("verdantflare_agent_claude.engine.run_managed_turn", side_effect=fake_managed_turn):
            started = await self.engine.call(
                "claude_task_start",
                {
                    "request_id": "m-req-1",
                    "mode": "managed",
                    "cwd": str(self.work),
                    "prompt": "Run managed check",
                },
            )
            await self.until(started["task_id"], "completed")
            st = await self.engine.call("claude_task_status", {"task_id": started["task_id"]})
            res1 = await self.engine.call("claude_task_result", {"task_id": started["task_id"]})
            self.assertEqual("Managed ok (resume=False)", json.loads(res1["content_json"])["answer"])

            await self.engine.call(
                "claude_task_continue",
                {
                    "task_id": started["task_id"],
                    "request_id": "m-req-2",
                    "expected_turn_id": st["turn_id"],
                    "prompt": "Continue managed check",
                },
            )
            await self.until(started["task_id"], "completed")
            res2 = await self.engine.call("claude_task_result", {"task_id": started["task_id"]})
            self.assertEqual("Managed ok (resume=True)", json.loads(res2["content_json"])["answer"])

    async def test_recursion_guard_blocks_nested_call(self):
        with patch.dict(os.environ, {"VERDANTFLARE_AGENT_CLAUDE_DEPTH": "1"}):
            with self.assertRaises(BridgeError) as caught:
                await self.engine.call(
                    "claude_task_start",
                    {"request_id": "rec-1", "mode": "managed", "cwd": str(self.work), "prompt": "nested"},
                )
            self.assertEqual("WORKSPACE_DENIED", caught.exception.code)

    async def test_panel_cancel_returns_approval_requires_ui_without_changing_state(self):
        release = asyncio.Event()

        async def slow_panel_turn(**kwargs):
            await release.wait()
            return {"answer": "done", "commands": [], "file_changes": [], "unresolved": []}

        with patch("verdantflare_agent_claude.engine.run_panel_turn", side_effect=slow_panel_turn):
            started = await self.engine.call(
                "claude_task_start",
                {
                    "request_id": "panel-cancel-1",
                    "mode": "panel",
                    "cwd": str(self.work),
                    "session_id": self.session_id,
                    "prompt": "Hold panel turn",
                },
            )
            await self.until(started["task_id"], "running")
            st = await self.engine.call("claude_task_status", {"task_id": started["task_id"]})
            with self.assertRaises(BridgeError) as caught:
                await self.engine.call(
                    "claude_task_cancel",
                    {"task_id": started["task_id"], "expected_turn_id": st["turn_id"]},
                )
            self.assertEqual("APPROVAL_REQUIRES_UI", caught.exception.code)
            self.assertEqual("running", self.engine.store.task(started["task_id"])["state"])
            release.set()
            await self.until(started["task_id"], "completed")


if __name__ == "__main__":
    unittest.main()
