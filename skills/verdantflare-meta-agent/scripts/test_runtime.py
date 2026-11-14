import asyncio
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest

from verdantflare_agent.store import Store, Conflict
from verdantflare_agent.worker import Worker, WorkerLock, NotAccepted, Observation, Reconciliation


class Fixture:
    def setup_runtime(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.database = self.root / "ledger.sqlite"
        self.now = 1000.0
        self.handoff = json.loads((Path(__file__).resolve().parents[1] / "resources/handoff.example.json").read_text("utf-8"))
        for rel in [self.handoff["plan_path"], self.handoff["change_path"], *self.handoff["inputs"]]:
            target = self.root / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("# Fixture\n", "utf-8")
        self.store = Store(self.database, self.root, clock=lambda: self.now)
        self.addCleanup(lambda: self.store.close())
        self.store.register(self.handoff)
        self.body = {
            "message_id": "message-1", "kind": "assignment", "plan_id": self.handoff["plan_id"],
            "task_key": self.handoff["task_key"], "assignment_id": self.handoff["assignment_id"],
            "revision": 1, "sender": "coordinator", "sender_generation": 1,
            "recipient": "reviewer", "recipient_generation": 1, "causation_id": "user-authorized-task",
            "payload": {"instruction": "Read the registered handoff and complete its scoped task."},
        }

    def claim_delivered(self):
        self.store.enqueue(self.body)
        claim = self.store.claim("worker")
        receipt = {"session_id": self.handoff["recipient"]["native_session_id"], "task_id": "external-task", "turn_id": "turn-1"}
        self.store.delivered(claim["id"], claim["token"], receipt)
        return claim, receipt

    def ack(self):
        return {"recipient": "reviewer", "generation": 1, "assignment_id": self.handoff["assignment_id"],
                "revision": 1, "turn_id": "turn-1", "action": "Read and accepted the scoped assignment."}


class StoreTests(Fixture, unittest.TestCase):
    def setUp(self):
        self.setup_runtime()

    def test_persistence_idempotency_and_conflicting_payload(self):
        first = self.store.enqueue(self.body)
        self.assertEqual(first["id"], self.store.enqueue(self.body)["id"])
        other = Store(self.database, self.root)
        try:
            self.assertEqual(other.get(first["id"])["body"], self.body)
        finally:
            other.close()
        changed = copy.deepcopy(self.body)
        changed["payload"]["instruction"] = "different"
        with self.assertRaisesRegex(Conflict, "MESSAGE_ID_CONFLICT"):
            self.store.enqueue(changed)
        self.assertEqual(len(self.store.pending({"queued"})), 1)

    def test_registration_rollback_and_revision_identity(self):
        handoff = copy.deepcopy(self.handoff)
        handoff["assignment_id"] = "new-assignment"
        handoff["sender"]["session_key"] = "new-sender"
        handoff["sender"]["native_session_id"] = "new-native-sender"
        handoff["recipient"]["native_session_id"] = "wrong-native-id"
        with self.assertRaisesRegex(Conflict, "SESSION_.*_CONFLICT"):
            self.store.register(handoff)

        with self.assertRaisesRegex(Conflict, "SESSION_NOT_REGISTERED"):
            self.store.session("new-sender")
        handoff = copy.deepcopy(self.handoff)
        handoff["revision"] = 2
        handoff["task_key"] = "another-task"
        with self.assertRaisesRegex(Conflict, "ASSIGNMENT_IDENTITY_CONFLICT"):
            self.store.register(handoff)

    def test_native_session_cannot_be_registered_under_another_alias(self):
        handoff = copy.deepcopy(self.handoff)
        handoff["assignment_id"] = "another-assignment"
        handoff["recipient"]["session_key"] = "alias-of-existing-session"
        with self.assertRaisesRegex(Conflict, "SESSION_ALIAS_CONFLICT"):
            self.store.register(handoff)

    def test_two_connections_only_one_claim(self):
        self.store.enqueue(self.body)
        barrier = threading.Barrier(2)
        claims = []
        def claim():
            store = Store(self.database, self.root, clock=lambda: self.now)
            try:
                barrier.wait(timeout=5)
                claims.append(store.claim("racing-worker"))
            finally:
                store.close()
        threads = [threading.Thread(target=claim) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
            self.assertFalse(thread.is_alive())
        self.assertEqual(len(claims), 2)
        self.assertEqual(sum(row is not None for row in claims), 1)

    def test_crash_before_receipt_requires_reconciliation(self):
        self.store.enqueue(self.body)
        claim = self.store.claim("old-worker", lease_seconds=10)
        self.now += 11
        self.assertEqual(self.store.recover(), 1)
        self.assertEqual(self.store.get(claim["id"])["state"], "unknown")
        self.assertIsNone(self.store.claim("new-worker"))
        self.store.reconcile_unsent(claim["id"], "adapter proved request was not submitted")
        new_claim = self.store.claim("new-worker")
        self.assertNotEqual(claim["token"], new_claim["token"])
        with self.assertRaisesRegex(Conflict, "CLAIM_LOST"):
            self.store.delivery_error(claim["id"], claim["token"])

    def test_backoff_and_budget_expiration(self):
        self.store.enqueue(self.body, max_attempts=2)
        claim = self.store.claim("one")
        self.store.delivery_error(claim["id"], claim["token"], known_unsent=True, reason="SESSION_BUSY")
        self.assertIsNone(self.store.claim("two"))
        self.now += 5
        claim = self.store.claim("two")
        self.assertEqual(claim["attempts"], 2)
        self.store.delivery_error(claim["id"], claim["token"], known_unsent=True, reason="OFFLINE")
        self.assertIsNone(self.store.claim("three"))
        self.assertEqual(self.store.get(claim["id"])["state"], "expired")

    def test_ack_identity_duplicate_conflict_and_stale_revision(self):
        self.claim_delivered()
        ack = self.ack()
        wrong = dict(ack, turn_id="wrong")
        with self.assertRaisesRegex(Conflict, "ACK_IDENTITY_MISMATCH"):
            self.store.acknowledge(self.body["message_id"], wrong)
        self.assertTrue(self.store.acknowledge(self.body["message_id"], ack)["acknowledged"])
        self.assertTrue(self.store.acknowledge(self.body["message_id"], ack)["duplicate"])
        with self.assertRaisesRegex(Conflict, "ACK_CONFLICT"):
            self.store.acknowledge(self.body["message_id"], dict(ack, action="another action"))
        revised = dict(self.handoff, revision=2)
        self.store.register(revised)
        self.assertFalse(self.store.acknowledge(self.body["message_id"], ack)["acknowledged"])
        self.assertEqual(len(self.store.pending({"queued"})), 0)  # No ack-of-ack loop.

    def test_takeover_requires_explicit_unsent_migration(self):
        self.store.enqueue(self.body)
        ep = dict(self.handoff["recipient"], generation=2, native_session_id="replacement-session")
        self.store.takeover(ep, 1, "approved replacement route")
        self.assertIsNone(self.store.claim("worker"))
        self.assertEqual(self.store.get(self.body["message_id"])["error"], "MIGRATION_REQUIRED")
        self.store.migrate(self.body["message_id"], 1)
        claim = self.store.claim("worker")
        self.assertEqual(claim["route_generation"], 2)
        self.assertEqual(claim["body"]["recipient_generation"], 1)  # Immutable original envelope.
        with self.assertRaisesRegex(Conflict, "MIGRATION_REQUIRES_UNSENT"):
            self.store.migrate(claim["id"], 2)

    def test_terminal_observation_atomic_unique_return_no_false_acceptance(self):
        claim, receipt = self.claim_delivered()
        result = self.store.observation(claim["id"], terminal="completed", result={"answer": "evidence saved"})
        self.assertEqual(result["body"]["recipient"], "coordinator")
        self.assertFalse(result["body"]["payload"]["business_accepted"])
        self.assertEqual(self.store.get(claim["id"])["state"], "delivered")
        self.assertIsNone(self.store.observation(claim["id"], terminal="completed", result={"answer": "evidence saved"}))
        self.assertEqual(len(self.store.pending({"queued"})), 1)
        changed = copy.deepcopy(result["body"])
        changed["message_id"] = "duplicate-with-new-id"
        with self.assertRaisesRegex(Conflict, "RESULT_ID_MUST_MATCH_SOURCE"):
            self.store.enqueue(changed)

    def test_wrong_workspace_rejected(self):
        other = self.root / "another-workspace"
        other.mkdir()
        with self.assertRaisesRegex(Conflict, "WORKSPACE_MISMATCH"):
            Store(self.database, other)

    def test_relink_keeps_pending_message_revision_and_identity(self):
        self.store.enqueue(self.body)
        plan_path = "docs/archive/plan/example.plan.md"
        change_path = "docs/archive/changes/change_review-contract.md"
        for old, new in ((self.handoff["plan_path"], plan_path), (self.handoff["change_path"], change_path)):
            (self.root / new).parent.mkdir(parents=True, exist_ok=True)
            (self.root / old).rename(self.root / new)
        self.store.relink(self.handoff["plan_id"], "Renamed plan", plan_path,
                          [{"task_key": self.handoff["task_key"], "task_label": "T2.2", "change_path": change_path}])
        current = self.store.handoff(self.handoff["assignment_id"])
        self.assertEqual(current["plan_path"], plan_path)
        self.assertEqual(current["task_label"], "T2.2")
        self.assertEqual(current["revision"], 1)
        claim = self.store.claim("worker")
        self.assertEqual(claim["body"]["task_key"], self.body["task_key"])

    def test_elapsed_ttl_expires_without_call(self):
        self.store.enqueue(self.body, ttl=1)
        self.now += 2
        self.assertIsNone(self.store.claim("worker"))
        self.assertEqual(self.store.get(self.body["message_id"])["state"], "expired")

    def test_coordinator_takeover_preserves_inflight_execution_result(self):
        claim, _ = self.claim_delivered()
        self.store.takeover(dict(self.handoff["sender"], generation=2, native_session_id="new-coordinator"),
                            1, "approved coordinator takeover")
        reply = self.store.observation(claim["id"], terminal="completed", result={"answer": "done"})
        self.assertIsNotNone(reply)
        self.assertEqual(reply["route_generation"], 2)
        self.assertEqual(reply["body"]["recipient"], "coordinator")

    def test_observation_failure_rolls_back_terminal_flag(self):
        claim, _ = self.claim_delivered()
        with self.assertRaises(Conflict):
            self.store.observation(claim["id"], terminal="completed", result={"too_large": "x" * 300000})
        self.assertEqual(self.store.get(claim["id"])["observed"], 0)
        self.assertEqual(len(self.store.pending({"queued"})), 0)

    def test_crash_after_receipt_does_not_requeue_delivery(self):
        claim, _ = self.claim_delivered()
        self.now += 1000
        self.store.recover()
        self.assertIsNone(self.store.claim("restarted-worker"))
        self.assertEqual(self.store.get(claim["id"])["state"], "delivered")

    def test_worker_lock_excludes_other_live_owner_and_releases(self):
        with WorkerLock(self.database):
            with self.assertRaisesRegex(Conflict, "WORKER_ALREADY_RUNNING"):
                with WorkerLock(self.database):
                    self.fail("two workers acquired the lock")
        with WorkerLock(self.database):
            pass

    def test_cli_register_and_read_from_different_cwd(self):
        input_file = self.root / "handoff.json"
        input_file.write_text(json.dumps(self.handoff), "utf-8")
        script = Path(__file__).with_name("agent_runtime.py").resolve()
        args = [sys.executable, str(script), "--workspace", str(self.root), "--database", str(self.database)]
        result = subprocess.run([*args, "register", "--input", str(input_file)], cwd=self.root, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        result = subprocess.run([*args, "sessions"], cwd=self.root, capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(json.loads(result.stdout)), 2)


class FakeAdapter:
    def __init__(self, *, error=None, terminal=None):
        self.calls = []
        self.reads = []
        self.error = error
        self.terminal = terminal
        self.closed = False
        self.recovery = Reconciliation()

    async def deliver(self, message, endpoint, handoff):
        self.calls.append(message["id"])
        if self.error:
            raise self.error
        return {"session_id": endpoint["native_session_id"], "task_id": "task-" + message["id"], "turn_id": "turn-1"}

    async def reconcile(self, message, endpoint):
        return self.recovery

    async def observe(self, message, endpoint):
        self.reads.append(message["id"])
        return Observation(cursor=len(self.reads), terminal=self.terminal,
                           result={"answer": "done"} if self.terminal else None)

    async def close(self):
        self.closed = True


class WorkerTests(Fixture, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.setup_runtime()

    async def test_busy_then_success_and_one_return(self):
        self.store.enqueue(self.body)
        adapter = FakeAdapter(error=NotAccepted("SESSION_BUSY"))
        worker = Worker(self.store, {"local-claude": adapter, "local-codex": adapter})
        await worker.step()
        self.assertEqual(self.store.get(self.body["message_id"])["state"], "retry_wait")
        await worker.step()
        self.assertEqual(len(adapter.calls), 1)
        adapter.error, adapter.terminal = None, "completed"
        self.now += 5
        await worker.step()
        self.assertEqual(len(self.store.pending({"queued"})), 1)
        await worker.step()
        await worker.step()
        self.assertEqual(len(adapter.calls), 3)  # Busy, accepted assignment, one result notice.
        self.assertEqual(len(self.store.pending({"queued"})), 0)

    async def test_unknown_after_external_acceptance_never_replayed(self):
        self.store.enqueue(self.body)
        adapter = FakeAdapter(error=TimeoutError())
        worker = Worker(self.store, {"local-claude": adapter})
        await worker.step()
        self.now += 300
        await worker.step()
        self.assertEqual(len(adapter.calls), 1)
        self.assertEqual(self.store.get(self.body["message_id"])["state"], "unknown")
        adapter.recovery = Reconciliation(receipt={"session_id": self.handoff["recipient"]["native_session_id"],
                                                  "task_id": "accepted-before-timeout", "turn_id": "turn-1"})
        await worker.step()
        self.assertEqual(len(adapter.calls), 1)
        self.assertEqual(self.store.get(self.body["message_id"])["state"], "delivered")

    async def test_persistent_loop_holds_adapter_until_stop(self):
        self.store.enqueue(self.body)
        adapter = FakeAdapter()
        stop = asyncio.Event()
        worker = Worker(self.store, {"local-claude": adapter}, poll_seconds=.01)
        task = asyncio.create_task(worker.run(stop))
        for _ in range(100):
            if adapter.calls:
                break
            await asyncio.sleep(.01)
        self.assertEqual(len(adapter.calls), 1)
        self.assertFalse(adapter.closed)
        stop.set()
        await asyncio.wait_for(task, 2)
        self.assertTrue(adapter.closed)


if __name__ == "__main__":
    unittest.main()
