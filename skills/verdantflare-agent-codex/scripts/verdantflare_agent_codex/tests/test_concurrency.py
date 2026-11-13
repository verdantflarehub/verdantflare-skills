import asyncio
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import AsyncMock, patch

from verdantflare_agent_codex.build_info import capture_build
from verdantflare_agent_codex.engine import Engine
from verdantflare_agent_codex.errors import BridgeError
from verdantflare_agent_codex.permissions import PermissionSelection, assert_no_pending_permission_conflict, read_turn_permissions
from verdantflare_agent_codex.recovery import read_native_result
from verdantflare_agent_codex.store import Store, canonical


class LeaseMatrixTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = Store(self.root / 'state.db')

    def tearDown(self):
        self.temp.cleanup()

    def payload(self, name, mode='plugin', cwd=None, thread=None):
        return dict(request_id=name, mode=mode, cwd=canonical(cwd or self.root),
                    thread_id=thread or name, prompt='fixture', timeout_seconds=60)

    def test_A01_mode_and_directory_matrix(self):
        nested = self.root / 'nested'
        sibling = self.root / 'other'
        nested.mkdir()
        sibling.mkdir()
        for modes in [('plugin', 'plugin'), ('plugin', 'managed'), ('managed', 'managed')]:
            for relation, dirs in [('same', (nested, nested)), ('parent', (self.root, nested)),
                                   ('different', (nested, sibling))]:
                for reverse in (False, True):
                    with self.subTest(modes=modes, relation=relation, reverse=reverse):
                        key = '-'.join(modes) + relation + str(reverse)
                        pair = list(zip(modes, dirs))
                        if reverse:
                            pair.reverse()
                        tasks = [self.store.start(self.payload(key + str(i), mode, cwd))
                                 for i, (mode, cwd) in enumerate(pair)]
                        self.assertNotEqual(tasks[0]['id'], tasks[1]['id'])

    def test_A02_twenty_batches_eight_connections(self):
        with ThreadPoolExecutor(max_workers=8) as pool:
            for batch in range(20):
                barrier = threading.Barrier(8)
                def start(i):
                    barrier.wait(timeout=10)
                    return self.store.start(self.payload(f'{batch}-{i}'))['id']
                self.assertEqual(8, len(set(pool.map(start, range(8)))))
        with self.store.transaction() as db:
            self.assertEqual(160, db.execute('SELECT COUNT(*) FROM tasks').fetchone()[0])
            self.assertEqual(160, db.execute('SELECT COUNT(*) FROM commands').fetchone()[0])

    def test_A02_same_thread_race_and_null_binding_rollback(self):
        barrier = threading.Barrier(8)
        def start(i):
            barrier.wait(timeout=10)
            try:
                return self.store.start(self.payload(str(i), thread='shared'))['id']
            except BridgeError as error:
                self.assertEqual('thread_lease', error.reason)
                return None
        with ThreadPoolExecutor(max_workers=8) as pool:
            self.assertEqual(1, sum(v is not None for v in pool.map(start, range(8))))
        first = self.store.start(self.payload('null-a', 'managed') | {'thread_id': None})
        second = self.store.start(self.payload('null-b', 'managed') | {'thread_id': None})
        while self.store.claim('worker'):
            pass
        self.store.transition(first['id'], 'worker', 'starting', thread_id='bound')
        with self.assertRaises(BridgeError):
            self.store.transition(second['id'], 'worker', 'starting', thread_id='bound')
        self.assertIsNone(self.store.task(second['id'])['thread_id'])

    def test_A04_unknown_only_blocks_its_thread_and_retries_are_persistent(self):
        task = self.store.start(self.payload('lost'))
        self.store.claim('expired-worker')
        self.store.mark_lost_owners(time.time())
        event = self.store.events(task['id'])['events'][-1]
        self.assertEqual('worker_heartbeat_expired', event['payload']['reason'])
        self.store.start(self.payload('other'))
        self.assertTrue(self.store.claim_recovery(task['id'], 'one'))
        self.store.transition(task['id'], 'one', 'interrupted_unknown')
        self.assertFalse(Store(self.store.path).claim_recovery(task['id'], 'two'))
        with patch('verdantflare_agent_codex.store.time.time', return_value=time.time() + 2):
            self.assertTrue(Store(self.store.path).claim_recovery(task['id'], 'two'))
        with self.assertRaises(BridgeError):
            self.store.transition(task['id'], 'one', 'completed', result={})

    def test_A05_current_permissions_cannot_be_overwritten_even_without_history(self):
        state = {'latestThreadSettings': {'permissions': ':workspace'},
                 'currentPermissions': {'activePermissionProfile': {'id': ':workspace'}}}
        with self.assertRaises(BridgeError) as error:
            assert_no_pending_permission_conflict(state, [], PermissionSelection('full-access', ''))
        self.assertEqual('PERMISSIONS_CONFLICT', error.exception.code)
        assert_no_pending_permission_conflict(state, [], PermissionSelection('auto', ''))

    def test_A05_path_aliases_do_not_create_a_directory_lock(self):
        self.store.start(self.payload('one', cwd=self.root))
        self.store.start(self.payload('two', cwd=self.root / '.'))
        self.assertEqual(canonical(self.root), canonical(self.root / '.'))
        if __import__('os').name == 'nt':
            self.assertEqual(canonical(self.root), canonical(str(self.root).upper()))

    def test_A01_junction_alias_resolves_to_same_workspace_without_locking_other_threads(self):
        target, link = self.root / 'target', self.root / 'alias'
        target.mkdir()
        if os.name == 'nt':
            import _winapi
            _winapi.CreateJunction(str(target), str(link))
        else:
            link.symlink_to(target, target_is_directory=True)
        try:
            self.assertEqual(canonical(target), canonical(link))
            self.store.start(self.payload('target', cwd=target))
            self.store.start(self.payload('alias', cwd=link))
        finally:
            if os.name == 'nt':
                link.rmdir()
            else:
                link.unlink()

    def test_A05_history_older_than_eight_megabytes_is_recoverable(self):
        path = self.root / 'history.jsonl'
        context = {'type': 'turn_context', 'payload': {'turn_id': 'old',
            'approval_policy': 'on-request', 'approvals_reviewer': 'user',
            'sandbox_policy': {'type': 'read-only'}}}
        with path.open('w', encoding='utf-8') as stream:
            stream.write(json.dumps(context) + '\n')
            padding = json.dumps({'type': 'padding', 'data': 'x' * 4096}) + '\n'
            for _ in range(2100):
                stream.write(padding)
            stream.write('{"unfinished":')
        self.assertIsNone(read_turn_permissions(path, 'old'))
        self.assertEqual('readOnly', read_turn_permissions(path, 'old', full_history=True)['sandboxPolicy']['type'])
        self.assertIsNone(read_turn_permissions(path, 'absent', full_history=True))

    def test_A06_isolated_install_rollback_and_manifest_tamper(self):
        scripts = Path(__file__).resolve().parents[2]
        def load(name, path):
            spec = importlib.util.spec_from_file_location(name, path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            return module
        installer = load('concurrency_installer', scripts / 'install.py')
        generator = load('concurrency_generator', scripts / 'generate_build.py')
        old, new, target = [self.root / n for n in ('old', 'new', 'installed')]
        for directory, version in [(old, 'old'), (new, 'new')]:
            directory.mkdir()
            (directory / 'example.py').write_text(f'VERSION = {version!r}\n')
            generator.generate(directory)
        old_hash = installer.sync_service_tree(old, target, False, None)
        old_identity = capture_build(target)
        new_hash = installer.sync_service_tree(new, target, True, old_hash)
        new_identity = capture_build(target)
        self.assertTrue(new_identity['verified'])
        self.assertNotEqual(old_identity['sha256'], new_identity['sha256'])
        self.assertTrue(old_identity['verified'])  # Captured identities do not reread disk.
        (target / 'example.py').write_text('tampered')
        self.assertFalse(capture_build(target)['verified'])
        installer.sync_service_tree(old, target, True, new_hash)
        self.assertEqual(old_identity, capture_build(target))
        # A real Python process must retain its imported build after disk replacement.
        module = Path(__file__).resolve().parents[1] / 'build_info.py'
        (target / 'build_info.py').write_bytes(module.read_bytes())
        generator.generate(target)
        code = ("import json; from pathlib import Path; import build_info; "
                "before=dict(build_info.SERVICE_BUILD); "
                "Path('example.py').write_text('changed after import'); "
                "print(json.dumps({'before':before,'same':before==build_info.SERVICE_BUILD,"
                "'disk_verified':build_info.capture_build()['verified']}))")
        output = subprocess.check_output([sys.executable, '-B', '-c', code], cwd=target, text=True)
        result = json.loads(output)
        self.assertTrue(result['before']['verified'])
        self.assertTrue(result['same'])
        self.assertFalse(result['disk_verified'])


class RecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.engine = Engine(dict(database=str(self.root/'tasks.db'), codex='fake',
                                  node='fake', extension='fake', allowed_roots=[str(self.root)]))

    async def asyncTearDown(self):
        await self.engine.close()
        self.temp.cleanup()

    async def test_A04_heartbeat_survives_event_loop_block(self):
        self.engine.heartbeat_interval = .02
        await self.engine.start()
        before = time.time()
        time.sleep(.2)  # Intentional event-loop fault; liveness must be independent.
        with self.engine.store.transaction() as db:
            heartbeat = db.execute('SELECT heartbeat FROM owners WHERE id=?', (self.engine.owner,)).fetchone()[0]
        self.assertGreater(heartbeat, before + .05)

    async def test_A04_engine_retries_readonly_history_and_releases_only_original_lease(self):
        store = self.engine.store
        payload = dict(request_id='original', mode='plugin', cwd=canonical(self.root),
                       thread_id='thread', prompt='fixture', timeout_seconds=60)
        task = store.start(payload)
        store.claim(self.engine.owner)
        store.transition(task['id'], self.engine.owner, 'running', turn_id='original-turn')
        store.append_event(task['id'], 'submission', {'client_message_id': 'marker', 'permission_mode': 'read-only'})
        store.transition(task['id'], self.engine.owner, 'interrupted_unknown')
        state = ['inProgress']
        calls = []
        root = self.root
        class Plugin:
            def __init__(self, *args): pass
            async def start(self): return self
            async def close(self): pass
            async def follow(self, thread): raise BridgeError('OWNER_UNAVAILABLE', 'fixture')
            async def effective_turn_permissions(self, *args):
                return {'sandboxPolicy': {'type': 'readOnly'}, 'approvalPolicy': 'on-request', 'approvalsReviewer': 'user'}
        class App:
            def __init__(self, *args): pass
            async def start(self): return self
            async def close(self): pass
            async def call(self, method, params):
                calls.append(method)
                if method == 'thread/read':
                    return {'thread': {'id': 'thread', 'cwd': str(root)}}
                if method == 'thread/turns/list':
                    return {'data': [{'id': 'original-turn', 'status': state[0], 'items': []}]}
                raise AssertionError('Recovery attempted a write: ' + method)
        with patch('verdantflare_agent_codex.engine.Plugin', Plugin), patch('verdantflare_agent_codex.engine.AppServer', App):
            self.assertTrue(store.claim_recovery(task['id'], self.engine.owner))
            await self.engine._recover(store.task(task['id']))
            self.assertEqual('interrupted_unknown', store.task(task['id'])['state'])
            state[0] = 'completed'
            with patch('verdantflare_agent_codex.store.time.time', return_value=time.time()+2):
                self.assertTrue(store.claim_recovery(task['id'], self.engine.owner))
            await self.engine._recover(store.task(task['id']))
        self.assertEqual('completed', store.task(task['id'])['state'])
        self.assertTrue(store.result(task['id'])['ready'])
        store.start(payload | {'request_id': 'next'})
        self.assertEqual(['thread/read', 'thread/turns/list'] * 2, calls)

    async def test_A04_finished_run_is_removed_and_recovery_can_run_again(self):
        attempts = []
        async def fail():
            attempts.append(1)
        for _ in range(2):
            self.engine._track('task', fail())
            await asyncio.sleep(.01)
            self.assertNotIn('task', self.engine.runs)
        self.assertEqual(2, len(attempts))

    async def test_A03_snapshot_rejects_cross_thread_events(self):
        connection = type('Connection', (), {'events': asyncio.Queue()})()
        for thread in ('other', 'wanted'):
            connection.events.put_nowait({'method': 'plugin/state', 'params': {'conversationId': thread,
                'change': {'type': 'snapshot', 'revision': 1, 'conversationState': {'thread': thread}}}})
        snapshot = await self.engine._snapshot(connection, 'wanted')
        self.assertEqual('wanted', snapshot.state['thread'])

    async def test_A04_native_history_fallback_paginates_without_writes(self):
        task = dict(thread_id='thread', turn_id='original', cwd=canonical(self.root), deadline=time.time()+30)
        calls = []
        async def call(method, params):
            calls.append(method)
            if method == 'thread/read':
                return {'thread': {'id': 'thread', 'cwd': str(self.root)}}
            self.assertEqual('thread/turns/list', method)
            if not params.get('cursor'):
                return {'data': [{'id': 'another', 'status': 'completed', 'items': []}], 'nextCursor': 'page2'}
            return {'data': [{'id': 'original', 'status': 'completed', 'items': [
                {'type': 'agentMessage', 'text': 'original answer'}]}], 'nextCursor': None}
        app = type('App', (), {})()
        app.call = call
        plugin = type('Plugin', (), {})()
        plugin.effective_turn_permissions = AsyncMock(return_value={
            'sandboxPolicy': {'type': 'readOnly'}, 'approvalPolicy': 'on-request', 'approvalsReviewer': 'user'})
        state, result, _ = await read_native_result(app, plugin, task, {'permission_mode': 'read-only'})
        self.assertEqual('completed', state)
        self.assertEqual('original answer', result['answer'])
        self.assertEqual(['thread/read', 'thread/turns/list', 'thread/turns/list'], calls)
        with self.assertRaises(BridgeError):
            await read_native_result(app, plugin, task | {'turn_id': None}, {'permission_mode': 'read-only'})

    async def test_A04_unknown_native_outcomes_never_become_success(self):
        task = dict(thread_id='thread', turn_id='original', cwd=canonical(self.root), deadline=time.time()+30)
        plugin = type('Plugin', (), {})()
        plugin.effective_turn_permissions = AsyncMock()
        for turn in [{'id': 'original', 'status': 'inProgress', 'items': []},
                     {'id': 'original', 'status': 'completed'}]:
            async def call(method, params):
                return {'thread': {'id': 'thread', 'cwd': str(self.root)}} if method == 'thread/read' else {'data': [turn]}
            app = type('App', (), {})()
            app.call = call
            with self.assertRaises(BridgeError):
                await read_native_result(app, plugin, task, {'permission_mode': 'read-only'})
        plugin.effective_turn_permissions.assert_not_called()


if __name__ == '__main__':
    unittest.main()
