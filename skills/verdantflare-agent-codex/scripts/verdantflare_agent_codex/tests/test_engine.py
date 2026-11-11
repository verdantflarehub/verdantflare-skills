import asyncio
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from verdantflare_agent_codex.engine import Engine
from verdantflare_agent_codex.errors import BridgeError
from verdantflare_agent_codex.plugin import Plugin, detect_codex_plugin_version, resolve_codex_binary


class FakeApp:
    instances = []
    def __init__(self, binary):
        self.events = asyncio.Queue()
        self.sent = []
        self.instances.append(self)
    async def start(self): return self
    async def close(self): pass
    async def send(self, message): self.sent.append(message)
    async def call(self, method, params):
        if method == 'thread/start': return {'thread': {'id': 'thread'}}
        if method == 'turn/start': return {'turn': {'id': 'turn'}}
        if method == 'thread/list': return {'data': getattr(self, 'threads', []), 'nextCursor': None}
        if method == 'turn/interrupt':
            await self.events.put({'method': 'turn/completed', 'params': {'threadId': 'thread',
                'turn': {'id': 'turn', 'status': 'interrupted', 'items': []}}})
            return {}
        raise AssertionError(method)


class EngineTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.patch = patch('verdantflare_agent_codex.engine.AppServer', FakeApp)
        self.patch.start()
        self.engine = Engine(dict(database=str(self.root/'state.db'), codex='fake',
                                  allowed_roots=[str(self.root)], node='fake', extension='fake'))
        await self.engine.start()

    async def asyncTearDown(self):
        await self.engine.close()
        self.patch.stop()
        self.temp.cleanup()

    async def until(self, task_id, state):
        for _ in range(100):
            if self.engine.store.task(task_id)['state'] == state: return
            await asyncio.sleep(.02)
        self.fail(self.engine.store.task(task_id)['state'])

    async def start_task(self):
        reply = await self.engine.call('codex_task_start', dict(mode='managed', cwd=str(self.root), prompt='test', request_id='test'))
        await self.until(reply['task_id'], 'running')
        return reply['task_id'], FakeApp.instances[-1]

    async def test_approval_cannot_be_granted_by_mcp_agent(self):
        task_id, app = await self.start_task()
        await app.events.put({'id': 90, 'method': 'item/commandExecution/requestApproval',
                              'params': {'threadId': 'thread', 'turnId': 'turn', 'command': 'test'}})
        await self.until(task_id, 'waiting_approval')
        pending = self.engine.store.pending_list(task_id)[0]
        with self.assertRaises(BridgeError) as caught:
            await self.engine.call('codex_task_respond', dict(task_id=task_id, request_id='approve',
                expected_turn_id='turn', pending_request_id=pending['id'], response={'decision': 'accept'}))
        self.assertEqual('APPROVAL_REQUIRES_UI', caught.exception.code)
        self.assertEqual([], app.sent)
        await self.engine.call('codex_task_cancel', dict(task_id=task_id, expected_turn_id='turn'))
        await self.until(task_id, 'cancelled')
        self.assertEqual([], self.engine.store.pending_list(task_id))

    async def test_user_input_validated_then_forwarded_once(self):
        task_id, app = await self.start_task()
        await app.events.put({'id': 91, 'method': 'item/tool/requestUserInput',
            'params': {'threadId': 'thread', 'turnId': 'turn', 'questions': [{'id': 'choice'}]}})
        await self.until(task_id, 'waiting_input')
        pending = self.engine.store.pending_list(task_id)[0]
        args=dict(task_id=task_id, request_id='answer',expected_turn_id='turn',
                  pending_request_id=pending['id'], response={'answers': {'choice': {'answers': ['yes']}}})
        with self.assertRaises(BridgeError):
            await self.engine.call('codex_task_respond', args | {'response': {'answers': {'other': {'answers': ['yes']}}}})
        await self.engine.call('codex_task_respond', args)
        await self.engine.call('codex_task_respond', args)
        await self.until(task_id, 'running')
        self.assertEqual(1, len(app.sent))
        self.assertEqual(91, app.sent[0]['id'])

    async def test_failure_is_terminal_and_releases_lease(self):
        task_id, app = await self.start_task()
        await app.events.put({'method': 'turn/completed', 'params': {'threadId': 'thread',
             'turn': {'id': 'turn', 'status': 'failed', 'error': {'message': 'provider failed'}}}})
        await self.until(task_id, 'failed')
        result = self.engine.store.result(task_id)
        self.assertTrue(result['ready'])
        self.assertTrue(result['result']['unresolved'])

    async def test_backend_disconnect_retains_unknown_lease(self):
        task_id, app = await self.start_task()
        await app.events.put({'method': 'bridge/disconnected'})
        await self.until(task_id, 'interrupted_unknown')
        with self.assertRaises(BridgeError) as caught:
            await self.engine.call('codex_task_start',dict(mode='managed',cwd=str(self.root),prompt='repeat',request_id='new'))
        self.assertEqual('SESSION_BUSY', caught.exception.code)

    async def test_second_frontend_routes_cancel_to_live_owner(self):
        task_id, app = await self.start_task()
        second = Engine(self.engine.config)
        await second.start()
        try:
            reply = await second.call('codex_task_cancel', dict(task_id=task_id, expected_turn_id='turn'))
            self.assertEqual('cancelling', reply['state'])
            await self.until(task_id, 'cancelled')
            self.assertNotIn(task_id, second.connections)
            self.assertEqual('cancelled', second.store.result(task_id)['state'])
        finally:
            await second.close()

    async def test_deadline_interrupt_is_confirmed_before_timed_out(self):
        task_id, app = await self.start_task()
        with self.engine.store.transaction() as db:
            db.execute('UPDATE tasks SET deadline=? WHERE id=?', (time.time() - 1, task_id))
        # Wake the outstanding event read so the next read notices the deadline.
        await app.events.put({'method': 'heartbeat', 'params': {}})
        await self.until(task_id, 'timed_out')
        events = self.engine.store.events(task_id)['events']
        self.assertTrue(any(e['kind'] == 'cancel_queued' for e in events))
        self.assertTrue(self.engine.store.result(task_id)['ready'])
        self.assertEqual([], self.engine.store.pending_list(task_id))

    async def test_capabilities_reports_filtered_out_count_and_supports_wildcard_roots(self):
        outside = tempfile.TemporaryDirectory()
        try:
            def init_with_threads(self_app, binary):
                self_app.events = asyncio.Queue()
                self_app.sent = []
                self_app.threads = [
                    {'id': 't-in', 'name': 'inside', 'cwd': str(self.root)},
                    {'id': 't-out', 'name': 'outside', 'cwd': outside.name},
                ]
                FakeApp.instances.append(self_app)

            with patch.object(FakeApp, '__init__', init_with_threads):
                restricted = await self.engine.call('codex_capabilities', {})
                self.assertEqual(1, len(restricted['threads']))
                self.assertEqual(1, restricted['filtered_out_count'])
                self.engine.config['allowed_roots'] = ['*']
                unrestricted = await self.engine.call('codex_capabilities', {})
                self.assertEqual(2, len(unrestricted['threads']))
                self.assertEqual(0, unrestricted['filtered_out_count'])
        finally:
            outside.cleanup()

    async def test_plugin_accepts_upgraded_extension_versions_without_lock(self):
        ext_dir = self.root / 'openai.chatgpt-99.9999.1-win32-x64'
        (ext_dir / 'out').mkdir(parents=True)
        (ext_dir / 'package.json').write_text('{"version": "99.9999.1"}', 'utf-8')
        (ext_dir / 'out/extension.js').write_text('"thread-owner-discovery":1,"thread-follower-start-turn":2', 'utf-8')
        self.assertEqual('99.9999.1', detect_codex_plugin_version(ext_dir))
        self.assertEqual('fake', resolve_codex_binary('fake', ext_dir))
        with patch('verdantflare_agent_codex.rpc.JsonlProcess.start', return_value='started'):
            plugin = Plugin('fake-node', ext_dir)
            started = await plugin.start()
            self.assertEqual('started', started)
