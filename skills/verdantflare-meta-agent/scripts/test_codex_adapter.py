import copy
import json
from pathlib import Path
import tempfile
import unittest

from verdantflare_agent.store import Store, Conflict
from verdantflare_agent.worker import NotAccepted, Worker
from verdantflare_agent.adapters.codex import CodexAdapter
from verdantflare_agent.adapters.task_mcp import acknowledgment


class MockCodex:
    def __init__(self, *, mode="plugin", state="completed", turn="old-turn"):
        self.calls = []
        self.mode, self.state, self.turn = mode, state, turn
        self.writes = {}
        self.drop_response = False
        self.pages = None
        self.answer = {"answer": "done"}

    async def call(self, name, args):
        self.calls.append((name, copy.deepcopy(args)))
        if name in {"codex_task_start", "codex_task_continue"}:
            request_id = args["request_id"]
            if request_id not in self.writes:
                self.writes[request_id] = copy.deepcopy(args)
                self.turn, self.state = "new-turn", "running"
            elif args != self.writes[request_id]:
                return {"isError": True, "structuredContent": {"code": "REQUEST_CONFLICT"}}
            if self.drop_response:
                self.drop_response = False
                raise TimeoutError()
            value = {"task_id": "backend-task", "state": "queued", "thread_id": "native-worker", "turn_id": None, "mode": self.mode}
        elif name == "codex_task_status":
            value = {"task_id": "backend-task", "state": self.state, "thread_id": "native-worker", "turn_id": self.turn,
                     "mode": self.mode, "next_event": 1, "has_more": False}
        elif name == "codex_task_result":
            if self.pages:
                chunk, cursor = self.pages[int(args.get("cursor", "0"))]
            else:
                chunk, cursor = json.dumps(self.answer), None
            value = {"task_id": "backend-task", "thread_id": "native-worker", "turn_id": args["turn_id"],
                     "state": "completed", "ready": True, "content_json": chunk, "next_cursor": cursor}
        else:
            raise AssertionError(name)
        return {"structuredContent": value, "content": []}

    async def close(self):
        pass


class CodexAdapterTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.h = json.loads((Path(__file__).resolve().parents[1]/"resources/handoff.example.json").read_text("utf-8"))
        self.h["recipient"].update(agent="codex", mode="plugin", service="codex-service", native_session_id="native-worker")
        for path in [self.h["plan_path"], self.h["change_path"], *self.h["inputs"]]:
            target = self.root/path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("# Actual fixture\n", "utf-8")
        self.store = Store(self.root/"runtime.sqlite", self.root)
        self.addCleanup(self.store.close)
        self.store.register(self.h)
        self.body = {"message_id": "codex-test", "kind": "assignment", "plan_id": self.h["plan_id"],
            "task_key": self.h["task_key"], "assignment_id": self.h["assignment_id"], "revision": 1,
            "sender": "coordinator", "sender_generation": 1, "recipient": "reviewer", "recipient_generation": 1,
            "causation_id": "authorized-task", "payload": {"instruction": "Read the task and return evidence."}}
        self.store.enqueue(self.body)
        self.message = self.store.claim("test-worker")
        self.endpoint = dict(self.store.session("reviewer"), workspace=str(self.root))

    async def test_plugin_targets_exact_thread_and_freezes_request(self):
        client = MockCodex()
        adapter = CodexAdapter([], self.store, client=client)
        receipt = await adapter.deliver(self.message, self.endpoint, self.h)
        self.assertEqual(receipt["turn_id"], "new-turn")
        args = client.calls[0][1]
        self.assertEqual(args["thread_id"], "native-worker")
        self.assertEqual(args["mode"], "plugin")
        self.assertNotIn(str(self.root), args["prompt"])
        self.assertIn("VF_AGENT_ACK", args["prompt"])
        frozen = self.store.prepared_request(self.message["id"])
        self.assertEqual(frozen["request"]["arguments"], args)
        self.assertEqual(frozen["accepted"]["task_id"], "backend-task")

    async def test_managed_continues_bound_task_never_starts_replacement(self):
        endpoint = dict(self.endpoint, mode="managed", binding={"session_id": "native-worker", "task_id": "backend-task", "turn_id": "old-turn"})
        client = MockCodex(mode="managed")
        receipt = await CodexAdapter([], self.store, client=client).deliver(self.message, endpoint, self.h)
        names = [x[0] for x in client.calls]
        self.assertNotIn("codex_task_start", names)
        self.assertIn("codex_task_continue", names)
        self.assertEqual(receipt["session_id"], "native-worker")

    async def test_managed_without_binding_and_busy_target_do_not_write(self):
        client = MockCodex(mode="managed", state="running")
        adapter = CodexAdapter([], self.store, client=client)
        endpoint = dict(self.endpoint, mode="managed")
        with self.assertRaises(NotAccepted):
            await adapter.deliver(self.message, endpoint, self.h)
        endpoint["binding"] = {"session_id": "native-worker", "task_id": "backend-task", "turn_id": "old-turn"}
        with self.assertRaises(NotAccepted):
            await adapter.deliver(self.message, endpoint, self.h)
        self.assertEqual(len(client.writes), 0)

    async def test_response_loss_replays_identical_id_only_once_externally(self):
        client = MockCodex()
        client.drop_response = True
        adapter = CodexAdapter([], self.store, client=client)
        with self.assertRaises(TimeoutError):
            await adapter.deliver(self.message, self.endpoint, self.h)
        self.assertTrue((await adapter.reconcile(self.message, self.endpoint)).retry_safe_evidence)
        receipt = await adapter.deliver(self.message, self.endpoint, self.h)
        self.assertEqual(receipt["turn_id"], "new-turn")
        self.assertEqual(len(client.writes), 1)
        starts = [args for name, args in client.calls if name == "codex_task_start"]
        self.assertEqual(starts[0], starts[1])

    async def test_reconcile_saved_task_reads_without_replay(self):
        client = MockCodex()
        adapter = CodexAdapter([], self.store, client=client)
        await adapter.deliver(self.message, self.endpoint, self.h)
        client.calls.clear()
        outcome = await adapter.reconcile(self.message, self.endpoint)
        self.assertEqual(outcome.receipt["task_id"], "backend-task")
        self.assertEqual([name for name, _ in client.calls], ["codex_task_status"])

    async def test_result_pages_exact_turn_and_ack(self):
        client = MockCodex()
        adapter = CodexAdapter([], self.store, client=client)
        receipt = await adapter.deliver(self.message, self.endpoint, self.h)
        self.store.delivered(self.message["id"], self.message["token"], receipt)
        message = self.store.get(self.message["id"])
        ack = {"message_id": message["id"], "recipient": "reviewer", "generation": 1,
               "assignment_id": self.h["assignment_id"], "revision": 1, "action": "Read registered task."}
        raw = json.dumps({"answer": "VF_AGENT_ACK " + json.dumps(ack)})
        client.pages = {0: (raw[:20], "20"), 20: (raw[20:], None)}
        client.state = "completed"
        observation = await adapter.observe(message, self.endpoint)
        self.assertEqual(observation.acknowledgment["turn_id"], "new-turn")
        self.assertEqual(observation.terminal, "completed")
        for name, args in client.calls:
            if name == "codex_task_result":
                self.assertEqual(args["turn_id"], "new-turn")

    async def test_result_cursor_loop_rejected(self):
        client = MockCodex()
        adapter = CodexAdapter([], self.store, client=client)
        receipt = await adapter.deliver(self.message, self.endpoint, self.h)
        self.store.delivered(self.message["id"], self.message["token"], receipt)
        client.state = "completed"
        client.pages = {0: ('{"answer":', "1"), 1: ('"x"}', "1")}
        with self.assertRaisesRegex(Conflict, "RESULT_CURSOR_NOT_ADVANCING"):
            await adapter.observe(self.store.get(self.message["id"]), self.endpoint)

    async def test_wrong_target_rejected_and_plain_done_is_not_ack(self):
        client = MockCodex()
        adapter = CodexAdapter([], self.store, client=client)
        endpoint = dict(self.endpoint, native_session_id="another-thread")
        with self.assertRaisesRegex(Conflict, "BACKEND_TARGET_MISMATCH"):
            await adapter.deliver(self.message, endpoint, self.h)
        self.assertIsNone(acknowledgment(self.message, {"answer": "done"}, "turn-1"))


if __name__ == "__main__":
    unittest.main()
