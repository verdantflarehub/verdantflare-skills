"""Synthetic source/transport tests; real provider probes are recorded separately."""
import asyncio
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import goal_provider as gp
import test_goal_evidence as fixture


class ProviderTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.workspace = self.root / 'workspace'; self.workspace.mkdir()
        self.home = self.root / 'home'; (self.home / 'sessions').mkdir(parents=True)
        self.log = self.home / 'sessions' / 'native.jsonl'
        self.f = fixture.GoalEvidenceTests(); self.f.setUp()
        meta = json.loads(self.f.meta); meta['payload']['cwd'] = str(self.workspace)
        self.base = fixture.line(meta) + self.f.history
        self.log.write_bytes(self.base)
        self.thread = {'id': self.f.sid, 'cwd': str(self.workspace), 'path': str(self.log)}
        self.binary = self.root / 'codex'; self.binary.write_bytes(b'synthetic executable')

    def capture(self, thread=None):
        return gp.capture_session(self.thread if thread is None else thread,
                                  self.f.sid, self.workspace, self.home)

    def test_original_bytes_and_unknown_goal_events_are_preserved(self):
        noise = fixture.line({'type': 'response_item', 'payload': {'role': 'assistant', 'type': 'message'}})
        unknown = fixture.line({'type': 'event_msg', 'payload': {'type': 'thread_goal_cleared'}})
        companion = fixture.line({'type': 'event_msg', 'payload': {'type': 'user_message'}})
        self.log.write_bytes(self.base + noise + unknown + companion)
        self.assertEqual(self.base + unknown + companion, self.capture()[0])

    def test_metadata_workspace_session_and_path_cannot_be_substituted(self):
        for field, value in [('id', 'other-session'), ('cwd', str(self.root)), ('path', ''),
                             ('path', 'relative.jsonl')]:
            with self.subTest(field=field), self.assertRaises(gp.SourceError):
                self.capture(dict(self.thread, **{field: value}))
        self.log.write_bytes(self.base.replace(b'"id": "synthetic-goal-session"', b'"id": "other"', 1))
        with self.assertRaises(gp.SourceError): self.capture()

    def test_native_path_escape_and_symlink_escape_rejected(self):
        outside = self.root / 'outside'; outside.write_bytes(self.base)
        self.thread['path'] = str(outside)
        with self.assertRaises(gp.SourceError): self.capture()
        alias = self.home / 'sessions' / 'alias'; alias.symlink_to(outside)
        self.thread['path'] = str(alias)
        with self.assertRaises(gp.SourceError): self.capture()

    def test_corruption_partial_tail_conflicting_metadata_and_limits_rejected(self):
        other = json.loads(self.base.splitlines()[0]); other['payload']['id'] = 'other'
        for data in [self.base[:-1], self.base + b'{"payload":', self.base + fixture.line(other),
                     self.base + b'{"type":"event_msg","payload":{},"payload":{}}\n']:
            self.log.write_bytes(data)
            with self.subTest(size=len(data)), self.assertRaises(gp.SourceError): self.capture()
        self.log.write_bytes(self.base)
        with patch.object(gp, 'MAX_LOG_BYTES', 1), self.assertRaises(gp.SourceError): self.capture()
        with patch.object(gp, 'MAX_CAPTURE_BYTES', 1), self.assertRaises(gp.SourceError): self.capture()

    def test_resumed_metadata_must_match_identity_but_keeps_original_header(self):
        resumed = json.loads(self.base.splitlines()[0]); resumed['payload']['cli_version'] = 'new'
        self.log.write_bytes(self.base + fixture.line(resumed))
        self.assertEqual(self.base, self.capture()[0])
        resumed['payload']['session_id'] = 'other'
        self.log.write_bytes(self.base + fixture.line(resumed))
        with self.assertRaises(gp.SourceError): self.capture()

    async def invoke(self, callback=None, goal=None, *, include_decisions=False):
        owner = self
        class FakeClient:
            def __init__(self, binary): self.calls = []; self.closed = False
            async def start(self): pass
            async def call(self, method, params):
                self.calls.append((method, params))
                if method == 'thread/read': return {'thread': owner.thread}
                if callback: callback()
                return owner.f.goal if goal is None else goal
            async def close(self): self.closed = True
        client = FakeClient(str(self.binary)); self.client = client
        with patch.object(gp, '_ReadOnlyCodex', return_value=client), \
             patch.object(gp.shutil, 'which', return_value=str(self.binary)), \
             patch.dict(os.environ, {'CODEX_HOME': str(self.home)}):
            return await gp.read_codex_goal(self.f.sid, self.workspace, include_decisions=include_decisions)

    async def test_optional_decision_capture_preserves_assistant_and_original_projection(self):
        from test_final_decisions import assistant
        message = assistant('Synthetic final review')
        self.log.write_bytes(self.base + message)
        default = await self.invoke()
        self.assertEqual(default.capture, self.base)
        self.assertEqual(default.conversation_capture, b'')
        result = await self.invoke(include_decisions=True)
        self.assertEqual(result.capture, self.base)
        self.assertEqual(result.conversation_capture, self.base + message)
        self.assertNotIn('Synthetic final review', repr(result))

    async def test_final_message_drift_is_rejected_only_when_decisions_are_requested(self):
        from test_final_decisions import assistant
        def append():
            with self.log.open('ab') as stream:
                stream.write(assistant('New final request'))
        with self.assertRaises(gp.SourceError):
            await self.invoke(append, include_decisions=True)
        self.assertTrue(self.client.closed)
        self.log.write_bytes(self.base)
        self.assertEqual((await self.invoke(append)).capture, self.base)

    async def test_provider_reads_live_twice_and_keeps_sensitive_bytes_out_of_repr(self):
        result = await self.invoke()
        self.assertEqual(self.base, result.capture)
        self.assertNotIn(self.f.objective, repr(result))
        self.assertTrue(self.client.closed)
        self.assertEqual(['thread/read', 'thread/goal/get'], [m for m, _ in self.client.calls])
        self.assertFalse(self.client.calls[0][1]['includeTurns'])
        self.assertFalse(hasattr(result, 'approved'))

    async def test_new_user_input_during_readback_is_rejected_but_assistant_noise_is_not(self):
        def append(data):
            with self.log.open('ab') as stream: stream.write(data)
        with self.assertRaises(gp.SourceError):
            await self.invoke(lambda: append(fixture.user('New scope')))
        self.assertTrue(self.client.closed)
        self.log.write_bytes(self.base)
        noise = fixture.line({'type': 'event_msg', 'payload': {'type': 'agent_message', 'message': 'noise'}})
        self.assertEqual(self.base, (await self.invoke(lambda: append(noise))).capture)

    async def test_replaced_log_or_executable_and_missing_or_foreign_goal_fail(self):
        def replace_log():
            replacement = self.log.with_suffix('.new'); replacement.write_bytes(self.base)
            replacement.replace(self.log)
        with self.assertRaises(gp.SourceError): await self.invoke(replace_log)
        with self.assertRaises(gp.SourceError): await self.invoke(lambda: self.binary.write_bytes(b'changed'))
        for response in ({}, {'goal': None}, {'goal': dict(self.f.goal['goal'], threadId='other')}):
            with self.assertRaises(gp.SourceError): await self.invoke(goal=response)

    async def test_missing_binary_never_uses_saved_observation(self):
        with patch.object(gp.shutil, 'which', return_value=None), self.assertRaises(gp.SourceError):
            await gp.read_codex_goal(self.f.sid, self.workspace)


class TransportTests(unittest.IsolatedAsyncioTestCase):
    async def call(self, rows, method='thread/goal/get'):
        class Sink:
            def write(self, data): pass
            async def drain(self): pass
        class Process: pass
        p = Process(); p.stdin = Sink(); p.stdout = asyncio.StreamReader()
        for row in rows: p.stdout.feed_data(fixture.line(row))
        p.stdout.feed_eof()
        client = gp._ReadOnlyCodex('synthetic'); client.process = p
        return await client.call(method, {})

    async def test_correlated_read_result_and_notification(self):
        self.assertEqual({'goal': None}, await self.call([
            {'method': 'notice'}, {'id': 1, 'result': {'goal': None}}]))

    async def test_wrong_id_server_approval_error_or_mutation_method_rejected(self):
        for row in [{'id': 2, 'result': {}}, {'id': True, 'result': {}},
                    {'id': 1, 'method': 'approval/request'}, {'id': 1, 'error': {'secret': 'hidden'}}]:
            with self.assertRaises(gp.SourceError) as ctx: await self.call([row])
            self.assertNotIn('hidden', str(ctx.exception))
        with self.assertRaises(gp.SourceError): await self.call([], 'thread/goal/set')

    async def test_notification_flood_and_empty_response_fail_closed(self):
        for rows in ([], [{'method': 'notice'}] * 129):
            with self.assertRaises(gp.SourceError): await self.call(rows)

    async def test_silent_provider_has_a_bounded_timeout(self):
        class Sink:
            def write(self, data): pass
            async def drain(self): pass
        class Process: pass
        p = Process(); p.stdin = Sink(); p.stdout = asyncio.StreamReader()
        client = gp._ReadOnlyCodex('synthetic'); client.process = p
        with patch.object(gp, 'TIMEOUT_SECONDS', 0.01), self.assertRaises(asyncio.TimeoutError):
            await client.call('thread/goal/get', {})

