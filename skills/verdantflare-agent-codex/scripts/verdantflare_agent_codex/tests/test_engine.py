import asyncio
import json
import os
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
        if method in ('thread/start', 'thread/resume'):
            return {'thread': {'id': 'thread'}, 'approvalPolicy': 'on-request',
                    'approvalsReviewer': 'user', 'sandbox': {'type': 'workspaceWrite'},
                    'activePermissionProfile': {'id': ':workspace', 'extends': None}}
        if method == 'turn/start': return {'turn': {'id': 'turn'}}
        if method == 'thread/list': return {'data': getattr(self, 'threads', []), 'nextCursor': None}
        if method == 'turn/interrupt':
            await self.events.put({'method': 'turn/completed', 'params': {'threadId': 'thread',
                'turn': {'id': 'turn', 'status': 'interrupted', 'items': []}}})
            return {}
        raise AssertionError(method)


class EngineTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        # These tests use fake backends and temporary databases only. Do not let
        # the parent delegated shell's depth flag block the isolated test engine.
        depth = patch.dict(os.environ, {'VERDANTFLARE_AGENT_CODEX_DEPTH': ''})
        depth.start()
        self.addCleanup(depth.stop)
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

    async def test_standalone_dispatch_rejected(self):
        with self.assertRaises(BridgeError) as error:
            await self.engine.call('codex_task_start', dict(mode='managed', cwd=str(self.root),
                request_id='retired', prompt='fixture'))
        self.assertEqual('INVALID_ARGUMENT', error.exception.code)

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
