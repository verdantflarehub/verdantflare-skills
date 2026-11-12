import asyncio
import tempfile
import time
import unittest
import re
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from urllib.parse import urlencode, urlsplit
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
            self.engine.store.start(dict(mode='managed',cwd=str(self.root),prompt='repeat',request_id='new', thread_id='thread'))
        self.assertEqual('SESSION_BUSY', caught.exception.code)
        self.assertEqual(task_id, caught.exception.task_id)

    async def approval(self, task_id, app, wire_id=90):
        await app.events.put({'id': wire_id, 'method': 'item/commandExecution/requestApproval',
            'params': {'threadId': 'thread', 'turnId': 'turn', 'command': '<script>bad()</script>',
                       'availableDecisions': ['accept', 'decline']}})
        for _ in range(100):
            pending = self.engine.store.pending_list(task_id)
            match = next((p for p in pending if self.engine.pending_wire.get(p['id']) == wire_id), None)
            if match and match.get('approval_url'):
                return match
            await asyncio.sleep(.02)
        self.fail('No approval URL')

    async def http(self, url, data=None, origin=None):
        def request():
            req = Request(url, data=urlencode(data).encode() if data is not None else None,
                          headers={'Origin': origin} if origin else {})
            try:
                with urlopen(req, timeout=5) as reply:
                    self.assertEqual('same-origin', reply.headers['Referrer-Policy'])
                    self.assertIsNone(reply.headers.get('Access-Control-Allow-Origin'))
                    return reply.status, reply.read().decode()
            except HTTPError as error:
                return error.code, error.read().decode()
        return await asyncio.to_thread(request)

    async def test_human_page_routes_once_and_keeps_other_approval_waiting(self):
        task_id, app = await self.start_task()
        first = await self.approval(task_id, app)
        second = await self.approval(task_id, app, 91)
        url = first['approval_url']
        origin = 'http://' + urlsplit(url).netloc
        status, page = await self.http(url)
        self.assertEqual(200, status)
        self.assertEqual(200, (await self.http(url, origin='null'))[0])
        self.assertEqual(200, (await self.http(url, origin='https://external.example'))[0])
        self.assertEqual([], app.sent)  # Navigation never grants a request.
        self.assertIn('&lt;script&gt;', page)
        self.assertNotIn('<script>', page)
        csrf = re.search('name="csrf" value="([^"]+)"', page)[1]
        data = {'csrf': csrf, 'decision': 'accept'}
        self.assertEqual(403, (await self.http(url, data, 'http://evil.example'))[0])
        self.assertEqual(403, (await self.http(url, data, 'null'))[0])
        self.assertEqual(403, (await self.http(url, data))[0])
        self.assertEqual([], app.sent)
        self.assertEqual(200, (await self.http(url, data, origin))[0])
        self.assertEqual([{'id': 90, 'result': {'decision': 'accept'}}], app.sent)
        self.assertEqual('waiting_approval', self.engine.store.task(task_id)['state'])
        self.assertEqual(409, (await self.http(url, data, origin))[0])
        await app.events.put({'method': 'serverRequest/resolved', 'params': {'threadId': 'thread', 'requestId': 91}})
        await self.until(task_id, 'running')
        self.assertEqual(409, (await self.http(second['approval_url']))[0])

    async def test_cancelled_approval_cannot_be_sent(self):
        task_id, app = await self.start_task()
        pending = await self.approval(task_id, app)
        await self.engine.call('codex_task_cancel', dict(task_id=task_id, expected_turn_id='turn'))
        await self.until(task_id, 'cancelled')
        self.assertEqual(409, (await self.http(pending['approval_url']))[0])
        self.assertEqual([], app.sent)

    async def test_managed_same_workspace_runs_two_connections(self):
        original = FakeApp.call
        async def unique_call(app, method, params):
            if method == 'thread/start':
                return {'thread': {'id': 'thread-' + str(id(app))}}
            return await original(app, method, params)
        with patch.object(FakeApp, 'call', unique_call):
            first, second = await asyncio.gather(*[
                self.engine.call('codex_task_start', dict(mode='managed', cwd=str(self.root), prompt='test', request_id=str(i)))
                for i in range(2)])
            await self.until(first['task_id'], 'running')
            await self.until(second['task_id'], 'running')
            self.assertEqual(2, len(self.engine.connections))
            self.assertNotEqual(self.engine.store.task(first['task_id'])['thread_id'],
                                self.engine.store.task(second['task_id'])['thread_id'])

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
