import asyncio
from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from verdantflare_agent_codex.engine import Engine
from verdantflare_agent_codex.errors import BridgeError
from verdantflare_agent_codex.permissions import (
    PermissionSelection, permission_comparison, assert_no_pending_permission_conflict)
from verdantflare_agent_codex.rpc import JsonlProcess


def state(mode='auto'):
    fields = PermissionSelection(mode, '').request_fields()
    return {key: deepcopy(fields) for key in ('currentPermissions', 'latestThreadSettings')}


class PermissionDiagnosticsTests(unittest.TestCase):
    def test_conflict_exposes_both_sources_without_mutation(self):
        original = state()
        before = deepcopy(original)
        with self.assertRaises(BridgeError) as caught:
            assert_no_pending_permission_conflict(original, [], PermissionSelection('full-access', ''))
        error = caught.exception.as_dict()
        self.assertEqual('PERMISSIONS_CONFLICT', error['code'])
        self.assertEqual(':workspace', error['details']['thread']['currentPermissions']['permissions'])
        self.assertEqual('never', error['details']['host']['approvalPolicy'])
        self.assertEqual(['auto'], error['details']['remediation']['matching_thread_modes'])
        self.assertIn(':workspace', error['message'])
        self.assertEqual(before, original)

    def test_each_policy_field_and_each_source_is_compared(self):
        for source in ('currentPermissions', 'latestThreadSettings'):
            for field, value in [('permissions', ':read-only'), ('approvalPolicy', 'never'),
                                 ('approvalsReviewer', 'guardian_subagent')]:
                with self.subTest(source=source, field=field):
                    current = state()
                    current[source][field] = value
                    with self.assertRaises(BridgeError) as caught:
                        assert_no_pending_permission_conflict(current, [], PermissionSelection('auto', ''))
                    self.assertIn({'source': source, 'field': field}, caught.exception.details['mismatches'])

    def test_missing_values_are_unknown_not_match(self):
        current = state()
        del current['currentPermissions']['approvalsReviewer']
        self.assertEqual('unknown', permission_comparison(current, PermissionSelection('auto', ''))['status'])
        self.assertEqual('match', permission_comparison(state(), PermissionSelection('auto', ''))['status'])


class RpcDiagnosticsTests(unittest.IsolatedAsyncioTestCase):
    async def test_wire_rejections_are_method_specific_and_do_not_leak_text(self):
        script = "import sys,json\nfor line in sys.stdin:\n r=json.loads(line); print(json.dumps({'id':r['id'],'error':r['params']}),flush=True)"
        rpc = await JsonlProcess([sys.executable, '-u', '-c', script]).start()
        try:
            for method, message, expected in [
                ('thread/resume', 'Thread is owned by plugin', 'THREAD_OWNED_BY_PLUGIN'),
                ('thread/resume', 'Thread not found', 'THREAD_RESUME_REJECTED'),
                ('thread/resume', 'database locked secret=do-not-expose', 'THREAD_RESUME_REJECTED'),
                ('thread/resume', 'thread already loaded', 'THREAD_RESUME_REJECTED'),
                ('turn/start', 'Thread is owned by plugin', 'BACKEND_ERROR')]:
                with self.subTest(method=method, message=message):
                    with self.assertRaises(BridgeError) as caught:
                        await rpc.call(method, {'code': -32000, 'message': message})
                    error = caught.exception.as_dict()
                    self.assertEqual(expected, error['code'])
                    self.assertEqual(method, error['details']['method'])
                    self.assertEqual(-32000, error['details']['backend_error_code'])
                    self.assertNotIn('do-not-expose', json.dumps(error))
                    self.assertFalse(error['retryable'])
            self.assertEqual(5, rpc.sequence)
            self.assertEqual({}, rpc.pending_methods)
        finally:
            await rpc.close()


class CapabilityDiagnosticsTests(unittest.IsolatedAsyncioTestCase):
    async def test_read_failure_does_not_abort_rows_and_no_write_is_sent(self):
        with tempfile.TemporaryDirectory() as directory:
            calls = []
            class App:
                def __init__(self, *args): pass
                async def start(self): return self
                async def close(self): pass
                async def call(self, method, params):
                    calls.append(method)
                    return {'data': [{'id': key, 'cwd': directory} for key in ('match', 'conflict', 'broken', 'after')], 'nextCursor': None}
            class Plugin:
                def __init__(self, *args): self.events = asyncio.Queue()
                async def start(self): return self
                async def close(self): pass
                async def settings(self, thread): return {'model': 'test'}
                async def follow(self, thread):
                    calls.append('observe')
                    if thread == 'broken': raise OSError('read failure')
                    await self.events.put({'method': 'plugin/state', 'params': {'conversationId': thread,
                        'change': {'type': 'snapshot', 'revision': 1, 'conversationState': state()}}})
                    return 'owner'
                async def permission_selection(self, thread, owner):
                    return 'store', PermissionSelection('full-access' if thread == 'conflict' else 'auto', '')
            engine = Engine({'database': str(Path(directory)/'tasks.db'), 'codex': 'fake', 'node': 'fake',
                             'extension': 'fake', 'allowed_roots': [directory]})
            with patch('verdantflare_agent_codex.engine.AppServer', App), patch('verdantflare_agent_codex.engine.Plugin', Plugin):
                reply = await engine.capabilities({'limit': 10})
            self.assertEqual(['match', 'conflict', 'unknown', 'match'],
                             [row['permission_alignment']['status'] for row in reply['threads']])
            self.assertTrue(all(row['owner_reachable'] for row in reply['threads']))
            self.assertEqual(['thread/list', 'observe', 'observe', 'observe', 'observe'], calls)
