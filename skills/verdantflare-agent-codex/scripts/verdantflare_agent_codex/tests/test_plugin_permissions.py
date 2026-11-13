import asyncio
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from verdantflare_agent_codex.engine import Engine
from verdantflare_agent_codex.errors import BridgeError
from verdantflare_agent_codex.permissions import PermissionSelection
from verdantflare_agent_codex.store import canonical


class PluginPermissionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.mode = 'full-access'
        self.native_mode = None
        self.reject_selection = False
        self.change_before_submit = False
        self.submissions = []
        self.interruptions = []
        self.sequence = 0
        self.break_patch = False
        self.recovery_fails = False
        self.recoveries = 0
        self.pending_tightening = False
        test = self

        class FakePlugin:
            def __init__(self, *args):
                self.events = asyncio.Queue()
            async def start(self): return self
            async def close(self): pass
            async def follow(self, thread):
                self.state = {'cwd': canonical(str(test.root)), 'threadRuntimeStatus': {'type': 'idle'},
                              'turns': [], 'requests': [],
                              'latestThreadSettings': {'permissions': ':workspace'},
                              'currentPermissions': {'activePermissionProfile': {'id': ':workspace'}}}
                if test.pending_tightening:
                    self.state['latestThreadSettings'] = {'permissions': ':read-only', 'approvalPolicy': 'on-request'}
                    self.state['turns'] = [{'turnId': 'old', 'status': 'completed',
                        'params': {'permissions': ':danger-full-access', 'approvalPolicy': 'never'}}]
                await self.emit()
                return 'owner'
            async def emit(self):
                await self.events.put({'method': 'plugin/state', 'params': {'change': {
                    'type': 'snapshot', 'revision': 1, 'conversationState': deepcopy(self.state)}}})
            async def resnapshot(self, thread, owner):
                test.recoveries += 1
                test.assertEqual('test-thread', thread)
                test.assertEqual('owner', owner)
                if test.recovery_fails:
                    raise BridgeError('OWNER_UNAVAILABLE', 'owner changed')
                return {'type': 'snapshot', 'revision': 2, 'conversationState': deepcopy(self.state)}
            async def permission_selection(self, *args):
                if test.reject_selection:
                    raise BridgeError('PERMISSIONS_UNAVAILABLE', 'unknown source')
                return 'bound-store', PermissionSelection(test.mode, 'fingerprint')
            async def check_permission_selection(self, *args):
                if test.change_before_submit:
                    raise BridgeError('PERMISSIONS_CHANGED', 'changed')
            async def effective_turn_permissions(self, thread, turn, cwd):
                return self.state['turns'][0]['params']
            async def follower(self, method, thread, payload, **kwargs):
                test.assertEqual('thread-follower-start-turn', method)
                request = payload['turnStart']['request']
                test.submissions.append(deepcopy(request))
                test.sequence += 1
                mode = test.native_mode or test.mode
                fields = PermissionSelection(mode, '').request_fields()
                sandbox = 'dangerFullAccess' if mode == 'full-access' else 'readOnly' if mode == 'read-only' else 'workspaceWrite'
                self.state['turns'] = [{'turnId': 'turn-' + str(test.sequence), 'status': 'inProgress',
                    'params': request | fields | {'sandboxPolicy': {'type': sandbox}}, 'items': []}]
                if test.break_patch:
                    await self.events.put({'method': 'plugin/state', 'params': {'change': {
                        'type': 'patches', 'baseRevision': 1, 'revision': 2,
                        'patches': [{'op': 'replace', 'path': ['missing', 'value'], 'value': 1}]}}})
                else:
                    await self.emit()
                if test.native_mode is None:
                    self.state['turns'][0]['status'] = 'completed'
                    await self.emit()
                return {}
            async def interrupt(self, thread, turn, owner):
                test.interruptions.append(turn)
                self.state['turns'][0]['status'] = 'interrupted'
                await self.emit()

        self.patch = patch('verdantflare_agent_codex.engine.Plugin', FakePlugin)
        self.patch.start()
        self.engine = Engine({'database': str(self.root/'tasks.db'), 'node': 'fake',
                              'extension': 'fake', 'codex': 'fake', 'allowed_roots': [str(self.root)]})
        await self.engine.start()

    async def asyncTearDown(self):
        await self.engine.close()
        self.patch.stop()
        self.temp.cleanup()

    async def terminal(self, task_id):
        for _ in range(100):
            task = self.engine.store.task(task_id)
            if task['state'] in ('completed', 'failed', 'interrupted_unknown'):
                return task
            await asyncio.sleep(.02)
        self.fail('No terminal state')

    async def start_task(self):
        return await self.engine.call('codex_task_start', {'request_id': 'initial', 'mode': 'plugin',
            'cwd': str(self.root), 'thread_id': 'test-thread', 'prompt': 'test'})

    async def test_start_continue_reloads_tightened_selection_and_keeps_model_inheritance(self):
        first = await self.start_task()
        task = await self.terminal(first['task_id'])
        self.assertEqual('completed', task['state'])
        self.assertEqual(':danger-full-access', self.submissions[0]['permissions'])
        self.assertNotIn('model', self.submissions[0])
        self.mode = 'read-only'
        await self.engine.call('codex_task_continue', {'task_id': task['id'], 'request_id': 'next',
            'expected_turn_id': task['turn_id'], 'prompt': 'next'})
        task = await self.terminal(task['id'])
        self.assertEqual('completed', task['state'])
        self.assertEqual(':read-only', self.submissions[1]['permissions'])
        self.assertEqual('read-only', self.engine.store.submission(task['id'])['permission_mode'])

    async def test_unknown_selection_submits_no_model_turn(self):
        self.reject_selection = True
        task = await self.terminal((await self.start_task())['task_id'])
        self.assertEqual('failed', task['state'])
        self.assertEqual([], self.submissions)

    async def test_pending_tightening_cannot_be_overwritten_by_stale_full_access_preference(self):
        self.pending_tightening = True
        task = await self.terminal((await self.start_task())['task_id'])
        self.assertEqual('failed', task['state'])
        self.assertEqual([], self.submissions)

    async def test_persisted_selection_matching_pending_tightening_can_run(self):
        self.pending_tightening = True
        self.mode = 'read-only'
        task = await self.terminal((await self.start_task())['task_id'])
        self.assertEqual('completed', task['state'])
        self.assertEqual(':read-only', self.submissions[0]['permissions'])

    async def test_selection_changed_before_submit_submits_nothing(self):
        self.change_before_submit = True
        task = await self.terminal((await self.start_task())['task_id'])
        self.assertEqual('failed', task['state'])
        self.assertEqual([], self.submissions)

    async def test_mismatch_interrupts_exact_turn_and_preserves_failure(self):
        self.native_mode = 'auto'
        task = await self.terminal((await self.start_task())['task_id'])
        self.assertEqual('failed', task['state'])
        self.assertEqual([task['turn_id']], self.interruptions)
        result = self.engine.store.result(task['id'])
        self.assertEqual('PERMISSIONS_MISMATCH', result['result']['unresolved'][0]['code'])

    async def test_idempotent_replay_does_not_resubmit(self):
        first = await self.start_task()
        await self.terminal(first['task_id'])
        second = await self.start_task()
        self.assertEqual(first['task_id'], second['task_id'])
        self.assertEqual(1, len(self.submissions))

    async def test_bad_patch_refreshes_exact_snapshot_without_replaying_turn(self):
        self.break_patch = True
        task = await self.terminal((await self.start_task())['task_id'])
        self.assertEqual('completed', task['state'])
        self.assertEqual(1, self.recoveries)
        self.assertEqual(1, len(self.submissions))
        self.assertEqual([], self.interruptions)
        kinds = [e['kind'] for e in self.engine.store.events(task['id'])['events']]
        self.assertIn('snapshot_recovered', kinds)
        self.assertIn('effective_permissions', kinds)

    async def test_failed_snapshot_recovery_keeps_outcome_unknown_without_replay(self):
        self.break_patch = True
        self.recovery_fails = True
        task = await self.terminal((await self.start_task())['task_id'])
        self.assertEqual('interrupted_unknown', task['state'])
        self.assertEqual(1, self.recoveries)
        self.assertEqual(1, len(self.submissions))


if __name__ == '__main__':
    unittest.main()
