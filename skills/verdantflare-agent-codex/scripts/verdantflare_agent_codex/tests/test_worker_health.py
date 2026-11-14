import asyncio
from contextlib import redirect_stderr
import io
from pathlib import Path
import sqlite3
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from verdantflare_agent_codex.engine import Engine
from verdantflare_agent_codex.errors import BridgeError
from verdantflare_agent_codex.store import Store, canonical


class WorkerHealthTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.engine = Engine(dict(database=str(self.root/'state.db'), codex='fake',
                                 node='fake', extension='fake', allowed_roots=[str(self.root)]))
        self.engine.heartbeat_interval = .01

    async def asyncTearDown(self):
        await self.engine.close()
        self.temp.cleanup()

    async def until(self, predicate, timeout=4):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if predicate(): return
            await asyncio.sleep(.01)
        self.fail('condition did not become true')

    async def test_claim_exception_logged_and_original_queue_consumed_once(self):
        store = self.engine.store
        task = store.start(dict(request_id='one', mode='managed', cwd=canonical(self.root),
                                prompt='fixture', timeout_seconds=60))
        original = store.claim
        attempts = []
        executed = []
        def claim(owner):
            attempts.append(owner)
            if len(attempts) == 1: raise sqlite3.OperationalError('database is locked')
            return original(owner)
        async def execute(command):
            executed.append(command['id'])
            store.transition(task['id'], self.engine.owner, 'completed', result={})
            store.command_done(command['id'], self.engine.owner, {'accepted': True})
        logs = io.StringIO()
        with patch.object(store, 'claim', claim), patch.object(self.engine, '_execute', execute), redirect_stderr(logs):
            await self.engine.start()
            await self.until(lambda: store.task(task['id'])['state']=='completed')
            await self.until(lambda: self.engine.worker_health()['healthy'])
        self.assertEqual(1, len(executed))
        self.assertIn('OperationalError', logs.getvalue())
        self.assertIn('worker_loop_failed', logs.getvalue())
        self.assertEqual(1, self.engine.loop_restarts['work'])

    async def test_recovery_loop_exception_is_supervised(self):
        original = self.engine.store.mark_lost_owners
        calls = []
        def lost(cutoff):
            calls.append(cutoff)
            if len(calls)==1: raise sqlite3.OperationalError('fixture')
            return original(cutoff)
        with patch.object(self.engine.store, 'mark_lost_owners', lost), redirect_stderr(io.StringIO()):
            await self.engine.start()
            await self.until(lambda: self.engine.worker_health()['healthy'])
        self.assertEqual(1, self.engine.loop_restarts['recovery'])

    async def test_unexpected_return_and_supervisor_cancellation_restart(self):
        original = self.engine._work
        calls = []
        async def work():
            calls.append(1)
            if len(calls)==1: return
            await original()
        with patch.object(self.engine, '_work', work), redirect_stderr(io.StringIO()):
            await self.engine.start()
            await self.until(lambda: self.engine.worker_health()['healthy'])
            old = self.engine.worker
            old.cancel()
            await self.until(lambda: self.engine.worker is not old and self.engine.worker_health()['healthy'])
        self.assertGreaterEqual(self.engine.loop_restarts['work'],2)

    async def test_event_loop_block_stops_renewal_and_allows_lost_owner_detection(self):
        self.engine.health_timeout = .06
        await self.engine.start()
        await self.until(lambda: self.engine.worker_health()['healthy'])
        store = self.engine.store
        task = store.start(dict(request_id='owned',mode='managed',cwd=canonical(self.root),prompt='fixture',timeout_seconds=60))
        store.claim(self.engine.owner)
        # Block this event loop while the pulse thread and separate Store remain alive.
        time.sleep(.20)
        with store.transaction() as db:
            stamp = db.execute('SELECT heartbeat FROM owners WHERE id=?',(self.engine.owner,)).fetchone()[0]
        self.assertLess(stamp,time.time()-.10)
        self.assertFalse(self.engine.worker_health()['healthy'])
        self.assertEqual(1,Store(store.path).mark_lost_owners(time.time()-.10))
        self.assertEqual('interrupted_unknown',store.task(task['id'])['state'])

    async def test_blocked_claim_does_not_block_event_loop(self):
        entered, release = threading.Event(), threading.Event()
        original = self.engine.store.claim
        def claim(owner):
            entered.set()
            release.wait(2)
            return original(owner)
        with patch.object(self.engine.store,'claim',claim):
            await self.engine.start()
            await self.until(entered.is_set)
            # A runnable coroutine can release the blocked storage worker.
            release.set()
            await self.until(lambda: self.engine.worker_health()['healthy'])

    async def test_health_guard_is_checked_after_acquiring_write_lock(self):
        store=self.engine.store
        store.heartbeat('fixture')
        with store.transaction() as db:
            before=db.execute("SELECT heartbeat FROM owners WHERE id='fixture'").fetchone()[0]
        store.heartbeat('fixture',lambda:False)
        with store.transaction() as db:
            self.assertEqual(before,db.execute("SELECT heartbeat FROM owners WHERE id='fixture'").fetchone()[0])

    async def test_withdraw_exact_unclaimed_command_preserves_history(self):
        store=self.engine.store
        task=store.start(dict(request_id='withdraw',mode='managed',cwd=canonical(self.root),prompt='fixture',timeout_seconds=60))
        with store.transaction() as db:
            command=db.execute('SELECT id FROM commands WHERE task_id=?',(task['id'],)).fetchone()[0]
        with self.assertRaises(BridgeError):store.cancel_unclaimed(command,'wrong','fixture')
        self.assertEqual('cancelled',store.cancel_unclaimed(command,'withdraw','superseded_schedule')['state'])
        self.assertIsNone(store.claim('other'))
        self.assertEqual('queued_submission_withdrawn',store.events(task['id'])['events'][-1]['kind'])
        with self.assertRaises(BridgeError):store.cancel_unclaimed(command,'withdraw','fixture')

    async def test_withdraw_cannot_cancel_claimed_submission(self):
        store=self.engine.store
        task=store.start(dict(request_id='claimed',mode='managed',cwd=canonical(self.root),prompt='fixture',timeout_seconds=60))
        c=store.claim('worker')
        with self.assertRaises(BridgeError):store.cancel_unclaimed(c['id'],'claimed','fixture')
        self.assertEqual('queued',store.task(task['id'])['state'])

    async def test_cancel_during_committed_claim_does_not_replay(self):
        store=self.engine.store
        task=store.start(dict(request_id='claim-cancel',mode='managed',cwd=canonical(self.root),prompt='fixture',timeout_seconds=60))
        original=store.claim
        entered,release=threading.Event(),threading.Event()
        def claim(owner):
            result=original(owner)
            if result:
                entered.set()
                release.wait(2)
            return result
        async def recover(task):
            store.transition(task['id'],self.engine.owner,'interrupted_unknown')
        with patch.object(store,'claim',claim), patch.object(self.engine,'_recover',recover), redirect_stderr(io.StringIO()):
            await self.engine.start()
            await self.until(entered.is_set)
            self.engine.worker.cancel()
            release.set()
            await self.until(lambda: any(e['kind']=='state' and e['payload']['state']=='interrupted_unknown'
                                        for e in store.events(task['id'])['events']))
        with store.transaction() as db:
            self.assertEqual(1,db.execute('SELECT COUNT(*) FROM commands').fetchone()[0])
            self.assertEqual('unknown',db.execute('SELECT state FROM commands').fetchone()[0])
