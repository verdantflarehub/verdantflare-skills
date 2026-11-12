import concurrent.futures
import tempfile
import time
import unittest
from pathlib import Path
from verdantflare_agent_codex.store import Store, allowed_cwd, canonical
from verdantflare_agent_codex.errors import BridgeError
from verdantflare_agent_codex.schema import validate


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.work = self.root / 'work'
        self.work.mkdir()
        self.store = Store(self.root / 'state.sqlite3')

    def tearDown(self):
        self.temp.cleanup()

    def payload(self, request='first', **overrides):
        return dict(request_id=request, mode='plugin', cwd=canonical(self.work),
                    prompt='Perform test task', thread_id='thread-a', timeout_seconds=3600) | overrides

    def assert_code(self, code, operation):
        with self.assertRaises(BridgeError) as caught:
            operation()
        self.assertEqual(code, caught.exception.code)

    def test_cross_connection_idempotency(self):
        def run(_):
            return Store(self.store.path).start(self.payload())['id']
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as workers:
            ids = list(workers.map(run, range(24)))
        self.assertEqual(1, len(set(ids)))
        self.assertIsNotNone(self.store.claim('worker'))
        self.assertIsNone(self.store.claim('worker'))
        self.assert_code('REQUEST_CONFLICT', lambda: self.store.start(self.payload(prompt='different')))

    def test_parent_child_leases_and_thread_leases(self):
        task = self.store.start(self.payload())
        nested = self.work / 'nested'
        nested.mkdir()
        sibling = self.root / 'other'
        sibling.mkdir()
        self.assert_code('SESSION_BUSY', lambda: self.store.start(self.payload('second', cwd=canonical(nested))))
        self.assert_code('SESSION_BUSY', lambda: self.store.start(self.payload('third', cwd=canonical(self.root))))
        self.assert_code('SESSION_BUSY', lambda: self.store.start(self.payload('fourth', cwd=canonical(sibling))))
        self.store.start(self.payload('fifth', cwd=canonical(sibling), thread_id='thread-b'))
        self.assertEqual('queued', self.store.task(task['id'])['state'])

    def test_unknown_retains_lease_and_cannot_replay(self):
        task = self.store.start(self.payload())
        self.store.claim('lost')
        self.assertEqual(1, self.store.mark_lost_owners(time.time()))
        self.assertEqual('interrupted_unknown', self.store.task(task['id'])['state'])
        self.assertIsNone(self.store.claim('replacement'))
        self.assert_code('SESSION_BUSY', lambda: self.store.start(self.payload('retry-with-new-id')))

    def test_managed_parallel_leases_are_atomic_and_thread_scoped(self):
        def run(i):
            return Store(self.store.path).start(self.payload(str(i), mode='managed', thread_id=None))
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as workers:
            tasks = list(workers.map(run, range(4)))
        self.assertEqual(4, len({task['id'] for task in tasks}))
        for i in range(4):
            command = self.store.claim('owner')
            self.store.transition(command['task_id'], 'owner', 'running', thread_id='thread-' + str(i), turn_id='turn')
        self.assert_code('SESSION_BUSY', lambda: self.store.start(self.payload('same-thread', mode='managed', thread_id='thread-0')))
        self.assert_code('SESSION_BUSY', lambda: self.store.start(self.payload('plugin')))

    def test_owner_fencing_and_heartbeat(self):
        task = self.store.start(self.payload())
        self.store.claim('one')
        self.store.heartbeat('one')
        self.assertEqual(0, self.store.mark_lost_owners(time.time() - 60))
        self.assert_code('BACKEND_DISCONNECTED', lambda: self.store.transition(task['id'], 'two', 'running'))

    def test_completion_continuation_and_stale_cancel(self):
        task = self.store.start(self.payload())
        self.store.claim('one')
        self.store.transition(task['id'], 'one', 'running', turn_id='turn-1')
        self.assert_code('STALE_TURN', lambda: self.store.enqueue('cancel', dict(task_id=task['id'], expected_turn_id='wrong')))
        self.store.transition(task['id'], 'one', 'completed', result={'answer': 'done'})
        self.store.append_event(task['id'], 'submission', {'client_message_id': 'old-marker'})
        payload = dict(task_id=task['id'], request_id='next', prompt='continue', expected_turn_id='turn-1')
        self.store.enqueue('continue', payload)
        self.store.enqueue('continue', payload)
        self.assertFalse(self.store.result(task['id'])['ready'])
        self.assertIsNone(self.store.submission_marker(task['id']))
        self.assertEqual('continue', self.store.claim('two')['operation'])
        self.assertIsNone(self.store.claim('one'))
        self.store.transition(task['id'], 'two', 'starting')
        self.assertEqual('completed', self.store.result(task['id'], 'turn-1')['state'])
        self.assert_code('STALE_TURN', lambda: self.store.enqueue('cancel', dict(task_id=task['id'], expected_turn_id='turn-1')))
        self.store.transition(task['id'], 'two', 'running', turn_id='turn-2')
        self.assert_code('STALE_TURN', lambda: self.store.enqueue('cancel', dict(task_id=task['id'], expected_turn_id='turn-1')))

    def test_failed_continuation_preserves_prior_result(self):
        task = self.store.start(self.payload())
        self.store.claim('one')
        self.store.transition(task['id'], 'one', 'completed', turn_id='old', result={'answer': 'done'})
        self.store.enqueue('continue', dict(task_id=task['id'], request_id='next', prompt='next', expected_turn_id='old'))
        self.store.claim('two')
        self.store.transition(task['id'], 'two', 'failed', error={'code': 'OWNER_UNAVAILABLE'})
        self.assertEqual({'answer': 'done'}, self.store.result(task['id'], 'old')['result'])
        self.assertEqual('failed', self.store.result(task['id'])['state'])
        self.assertTrue(self.store.result(task['id'])['ready'])
        self.assertEqual('OWNER_UNAVAILABLE', self.store.result(task['id'])['result']['unresolved'][0]['code'])
        self.assert_code('TASK_NOT_FOUND', lambda: self.store.result(task['id'], 'nonexistent'))

    def test_cancel_does_not_revert_to_running(self):
        task = self.store.start(self.payload())
        self.store.claim('one')
        self.store.transition(task['id'], 'one', 'running', turn_id='turn-1')
        self.store.enqueue('cancel', dict(task_id=task['id'], expected_turn_id='turn-1'))
        self.store.transition(task['id'], 'one', 'running')
        self.assertEqual('cancelling', self.store.task(task['id'])['state'])
        self.store.transition(task['id'], 'one', 'waiting_approval')
        self.assertEqual('cancelling', self.store.task(task['id'])['state'])
        self.store.transition(task['id'], 'one', 'cancelled')
        self.store.start(self.payload('new'))

    def test_event_pagination_has_no_loss(self):
        task = self.store.start(self.payload())
        self.store.claim('one')
        self.store.transition(task['id'], 'one', 'running', turn_id='turn-1')
        first = self.store.events(task['id'], limit=1)
        self.assertTrue(first['has_more'])
        second = self.store.events(task['id'], after=first['next_event'], limit=1)
        self.assertFalse(second['has_more'])
        self.assertGreater(second['next_event'], first['next_event'])

    def test_workspace_escape_and_relative_path(self):
        self.assertEqual(canonical(self.work), allowed_cwd(str(self.work), [self.root]))
        self.assertEqual(canonical(self.work), allowed_cwd(str(self.work), ['*']))
        self.assertEqual(canonical(self.work), allowed_cwd(str(self.work), []))
        with self.assertRaises(BridgeError) as denied:
            allowed_cwd(str(self.root), [self.work])
        self.assertEqual('WORKSPACE_DENIED', denied.exception.code)
        self.assertIn(canonical(self.root), str(denied.exception))
        self.assertIn(canonical(self.work), str(denied.exception))
        self.assert_code('WORKSPACE_DENIED', lambda: allowed_cwd('.', [self.root]))


class SchemaTests(unittest.TestCase):
    def test_unknown_fields_and_modes_rejected(self):
        base = dict(request_id='a', cwd='C:/test', prompt='test', mode='plugin', thread_id='a')
        validate('codex_task_start', base)
        for payload in [base | {'rpc': 'arbitrary'}, base | {'mode': 'managed'}, base | {'prompt': ' '},
                        {k: v for k, v in base.items() if k != 'thread_id'}]:
            with self.assertRaises(BridgeError):
                validate('codex_task_start', payload)

    def test_bytes_and_boolean_limits(self):
        with self.assertRaises(BridgeError):
            validate('codex_task_start', dict(request_id='a', cwd='C:/test', prompt='中' * 30000, mode='managed'))
        with self.assertRaises(BridgeError):
            validate('codex_task_status', dict(task_id='a', limit=True))


if __name__ == '__main__':
    unittest.main()
