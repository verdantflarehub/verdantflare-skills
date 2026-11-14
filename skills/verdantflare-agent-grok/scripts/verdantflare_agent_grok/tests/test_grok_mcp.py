"""Unit tests for verdantflare_agent_grok store, schema, ACP backend, AFK Pilot session-meta sync, and engine."""
from __future__ import annotations

import asyncio
import concurrent.futures
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import urllib.parse

from verdantflare_agent_grok.backend import (
    list_grok_sessions,
    sync_afk_pilot_session_meta,
)
from verdantflare_agent_grok.engine import Engine
from verdantflare_agent_grok.errors import BridgeError
from verdantflare_agent_grok.schema import validate
from verdantflare_agent_grok.store import Store, allowed_cwd, canonical


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
            "mode": "acp",
            "cwd": canonical(self.work),
            "prompt": "Run Grok test",
            "session_id": "gsess-a",
            "thread_id": "gsess-a",
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
        self.assert_code("SESSION_BUSY", lambda: self.store.start(self.payload("r2", cwd=canonical(nested), session_id="gsess-b", thread_id="gsess-b")))
        self.assert_code("SESSION_BUSY", lambda: self.store.start(self.payload("r3", cwd=canonical(self.root), session_id="gsess-c", thread_id="gsess-c")))
        self.assert_code("SESSION_BUSY", lambda: self.store.start(self.payload("r4", cwd=canonical(sibling), session_id="gsess-a", thread_id="gsess-a")))
        self.store.start(self.payload("r5", cwd=canonical(sibling), session_id="gsess-b", thread_id="gsess-b"))
        self.assertEqual("queued", self.store.task(task["id"])["state"])

    def test_allowed_cwd_wildcard_and_restrictions(self):
        self.assertEqual(canonical(self.work), allowed_cwd(str(self.work), [self.root]))
        self.assertEqual(canonical(self.work), allowed_cwd(str(self.work), ["*"]))
        self.assertEqual(canonical(self.work), allowed_cwd(str(self.work), []))
        with self.assertRaises(BridgeError) as denied:
            allowed_cwd(str(self.root), [self.work])
        self.assertEqual("WORKSPACE_DENIED", denied.exception.code)

    def test_schema_validation_and_modes(self):
        res = validate("grok_task_start", {"request_id": "r1", "mode": "acp", "cwd": "C:/test", "prompt": "hi"})
        self.assertEqual("acp", res["mode"])
        with self.assertRaises(BridgeError):
            validate("grok_task_start", {"request_id": "r1", "cwd": "C:/test", "prompt": "hi"})
        with self.assertRaises(BridgeError):
            validate("grok_task_start", {"request_id": "r1", "cwd": "C:/test", "prompt": "hi", "mode": "invalid"})
        with self.assertRaises(BridgeError):
            validate("grok_task_start", {"request_id": "r1", "mode": "acp", "cwd": "C:/test", "prompt": "中" * 30000})

    def test_lost_owner_retains_lease_and_rejects_unproven_cancel(self):
        task = self.store.start(self.payload("g-lost"))
        claimed = self.store.claim("dead-owner")
        self.assertIsNotNone(claimed)
        self.store.transition(task["id"], "dead-owner", "running", thread_id="gsess-a", turn_id="gturn-lost")
        self.assertEqual(1, self.store.mark_lost_owners(10**12))
        self.assertEqual("interrupted_unknown", self.store.task(task["id"])["state"])
        self.assertFalse(self.store.result(task["id"])["ready"])
        self.assert_code("SESSION_BUSY", lambda: self.store.start(self.payload("g-new", session_id="gsess-b", thread_id="gsess-b")))
        self.assert_code("SESSION_BUSY", lambda: self.store.enqueue("cancel", {"task_id": task["id"], "expected_turn_id": "gturn-lost"}))
        self.assertIsNone(self.store.claim("replacement"))

    def test_submitter_pinning_and_unclaimed_owner_recovery(self):
        task=self.store.start(self.payload(),owner="first")
        self.assertIsNone(self.store.claim("second"))
        self.store.mark_lost_owners(10**12)
        claimed=self.store.claim("second")
        self.assertEqual(claimed['task_id'],task['id'])
        self.assertIsNone(self.store.claim("third"))
        self.store.mark_lost_owners(10**12)
        self.assertEqual(self.store.task(task['id'])['state'],'interrupted_unknown')
        self.assertIsNone(self.store.claim("third"))



class BackendAndEngineTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.work = self.root / "workspace"
        self.work.mkdir()
        self.grok_home = self.root / ".grok"
        self.grok_home.mkdir()

        encoded = urllib.parse.quote(canonical(self.work), safe="")
        self.session_id = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
        sdir = self.grok_home / "sessions" / encoded / self.session_id
        sdir.mkdir(parents=True)
        (sdir / "summary.json").write_text(
            json.dumps(
                {
                    "id": self.session_id,
                    "title": "Existing Grok Session",
                    "working_dir": canonical(self.work),
                    "created_at": "2026-11-10T10:00:00Z",
                    "updated_at": "2026-11-10T11:00:00Z",
                    "session_kind": None,
                }
            ),
            encoding="utf-8",
        )
        sync_afk_pilot_session_meta(self.grok_home, self.session_id, canonical(self.work), "Existing Grok Session")

        self.engine = Engine(
            {
                "database": str(self.root / "state.db"),
                "grok": sys.executable,
                "grok_home": str(self.grok_home),
                "extension": str(self.root),
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

    async def test_capabilities_lists_sessions_and_afk_pilot_flags(self):
        caps = await self.engine.call("grok_capabilities", {"cwd": str(self.work)})
        self.assertEqual("read_verified", caps["acp"])
        self.assertEqual("read_verified", caps["afk_pilot_ui"])
        self.assertEqual(1, len(caps["sessions"]))
        sess = caps["sessions"][0]
        self.assertEqual(self.session_id, sess["session_id"])
        self.assertTrue(sess["afk_pilot_visible"])
        self.assertTrue(sess["afk_pilot_registered"])

    async def test_acp_turn_and_continue_updates_afk_pilot_meta(self):
        async def fake_acp_turn(**kwargs):
            sid = kwargs["session_id"] or "new-session-999"
            kwargs["on_session_bound"](sid)
            sync_afk_pilot_session_meta(kwargs["grok_home"], sid, kwargs["cwd"], kwargs["title_hint"])
            kwargs["on_event"]("assistant_text", {"text": "Generated C:/tmp/out.png"})
            return {
                "cancelled": False,
                "session_id": sid,
                "stop_reason": "end_turn",
                "answer": f"Grok ACP done (resume={kwargs['is_resume']})",
                "commands": [],
                "file_changes": ["src/art.py"],
                "media": ["C:/tmp/out.png"],
                "afk_pilot_synced": True,
                "unresolved": [],
            }

        with patch("verdantflare_agent_grok.engine.run_acp_turn", side_effect=fake_acp_turn):
            started = await self.engine.call(
                "grok_task_start",
                {
                    "request_id": "g-req-1",
                    "mode": "acp",
                    "cwd": str(self.work),
                    "prompt": "Upscale UI asset",
                },
            )
            await self.until(started["task_id"], "completed")
            st = await self.engine.call("grok_task_status", {"task_id": started["task_id"]})
            self.assertEqual("new-session-999", st["session_id"])

            meta = json.loads((self.grok_home / "client-state" / "session-meta.json").read_text(encoding="utf-8"))
            self.assertIn("new-session-999", meta)
            self.assertEqual("grok", meta["new-session-999"]["provider"])

            res1 = await self.engine.call("grok_task_result", {"task_id": started["task_id"]})
            c1 = json.loads(res1["content_json"])
            self.assertEqual("Grok ACP done (resume=False)", c1["answer"])
            self.assertEqual(["C:/tmp/out.png"], c1["media"])

            await self.engine.call(
                "grok_task_continue",
                {
                    "task_id": started["task_id"],
                    "request_id": "g-req-2",
                    "expected_turn_id": st["turn_id"],
                    "prompt": "Refine edges",
                },
            )
            await self.until(started["task_id"], "completed")
            res2 = await self.engine.call("grok_task_result", {"task_id": started["task_id"]})
            c2 = json.loads(res2["content_json"])
            self.assertEqual("Grok ACP done (resume=True)", c2["answer"])

    async def test_recursion_guard_blocks_nested_call(self):
        with patch.dict(os.environ, {"VERDANTFLARE_AGENT_GROK_DEPTH": "1"}):
            with self.assertRaises(BridgeError) as caught:
                await self.engine.call(
                    "grok_task_start",
                    {"request_id": "g-rec-1", "mode": "acp", "cwd": str(self.work), "prompt": "nested"},
                )
            self.assertEqual("WORKSPACE_DENIED", caught.exception.code)


if __name__ == "__main__":
    unittest.main()
