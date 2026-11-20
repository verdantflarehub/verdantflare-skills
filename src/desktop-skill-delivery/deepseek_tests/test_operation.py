"""Execute origin write-ahead BEFORE real official native mutations.

The retained backend is an isolated fixture, not an Electron desktop. Child
coordinators are real processes; killing them does not restart that backend.
"""
from __future__ import annotations

from dataclasses import asdict, replace
import json
import os
from pathlib import Path
import re
import selectors
import subprocess
import sys
import tempfile
import unittest

TOOLS = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(TOOLS), str(TOOLS / 'validation')]
import host_deepseek as h
from host_deepseek_journal import OriginJournal
from host_deepseek_operation import NativeOperation, Observation
from deepseek_management_backend import NativeBackend

TARGET = h.desired_connection('https://studio.invalid/mcp', 'VF_STUDIO')
UPDATED = h.desired_connection('https://studio.invalid/updated-mcp', 'VF_STUDIO')


def worker(path):
    request = h.read(Path(path).read_bytes())
    identity = h.Identity(**request['identity'])

    class Forward:
        def call(self, action, **values):
            sys.stdout.buffer.write(h.encode({'action': action, **values}) + b'\n')
            sys.stdout.buffer.flush()
            reply = h.read(sys.stdin.buffer.readline())
            h.require(reply.get('ok') is True, 'DEEPSEEK_TEST_PARENT_FAILED')
            return reply

        def observe(self):
            reply = self.call('observe')
            return Observation(h.Identity(**reply['identity']), h.encode(reply['descriptor']),
                               reply['writable'], tuple(reply['registered']))

        def mutate(self, mutation):
            self.call('mutate', mutation=mutation)

    def checkpoint(stage):
        if stage == request['exit_at']:
            os._exit(73)

    ledger = OriginJournal(request['root'], identity)
    operation = NativeOperation(ledger, identity, Forward())
    if request['exit_at'] == 'parent_prepared':
        os._exit(73)
    if request['mode'] == 'apply':
        result = operation.apply(request['expected'], request['desired'], checkpoint=checkpoint,
                                 request_id=request.get('request_id'))
    elif request['mode'] == 'recover_request':
        result = operation.recover_request(request['expected'], request['request_id'],
            request['target_sha256'], cancel=request['cancel'], checkpoint=checkpoint)
    else:
        result = operation.recover(request['expected'], request['operation_id'],
                                   cancel=request['cancel'], checkpoint=checkpoint)
    sys.stdout.buffer.write(h.encode({'done': True, 'result': result}) + b'\n')


class NativeOperationTests(unittest.TestCase):
    crash_cases = []

    def setUp(self):
        modules = os.environ.get('VF_DEEPSEEK_NATIVE_MODULES')
        if not modules:
            raise AssertionError('Explicit pinned VF_DEEPSEEK_NATIVE_MODULES required')
        self.modules = modules
        self.repo = TOOLS.parents[2]
        self.temp = tempfile.TemporaryDirectory(prefix='deepseek-operation-',
            dir=self.repo / '.tmp/agent-skill-delivery')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.backend = NativeBackend(modules, self.root / 'native')
        self.addCleanup(self.backend.close)
        self.ledger_root = self.root / 'ledger'; self.ledger_root.mkdir(mode=0o700)
        self.ledger = OriginJournal(self.ledger_root, self.backend.identity)
        self.op = NativeOperation(self.ledger, self.backend.identity, self.backend)

    def apply(self, desired):
        return self.op.apply(self.ledger.snapshot()[1], desired)

    def pending(self):
        state, revision = self.ledger.snapshot()
        if state['pending']:
            return revision, state['pending']['operation_id']
        return state['last']['pending_revision'], state['last']['operation_id']

    def run_worker(self, *, mode='apply', desired=None, exit_at='', cancel=False, handle=None, intent=None,
                   worker_script=None):
        expected, operation_id = ((intent['expected'], None) if intent is not None else
                                  (handle or (self.ledger.snapshot()[1], None)))
        request = {'identity': asdict(self.backend.identity), 'root': str(self.ledger_root.relative_to(self.repo)),
                   'mode': mode, 'desired': desired, 'expected': expected, 'operation_id': operation_id,
                   'exit_at': exit_at, 'cancel': cancel}
        if intent is not None:
            request.update(intent)
        path = self.root / 'worker-request.json'; path.write_bytes(h.encode(request))
        process = subprocess.Popen([sys.executable, '-B', str(worker_script or Path(__file__).resolve()), 'worker',
            str(path.relative_to(self.repo))], cwd=self.repo, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE)
        result = None
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ)
                while True:
                    self.assertTrue(selector.select(25), 'Coordinator worker timed out')
                    raw = process.stdout.readline()
                    if not raw:
                        break
                    message = h.read(raw)
                    if message.get('failed'):
                        result = {'error': message['failed']}; continue
                    if message.get('done'):
                        result = message['result']; continue
                    if message['action'] == 'observe':
                        observed = self.backend.observe()
                        reply = {'ok': True, 'identity': asdict(observed.identity),
                                 'descriptor': h.read(observed.descriptor), 'writable': observed.writable,
                                 'registered': list(observed.registered)}
                    else:
                        self.assertEqual(message['action'], 'mutate')
                        self.backend.mutate(tuple(message['mutation'])); reply = {'ok': True}
                    process.stdin.write(h.encode(reply) + b'\n'); process.stdin.flush()
            code = process.wait(timeout=10)
            if code not in (0, 73, 74):
                self.fail('Coordinator worker failed; no native exception text exported')
            return code, result
        finally:
            if process.poll() is None:
                process.kill(); process.wait(timeout=10)
            for stream in (process.stdin, process.stdout, process.stderr):
                stream.close()

    def test_actual_native_calls_are_bracketed_by_pending_and_verified_origin(self):
        writes = []
        def inspect_pending(mutation):
            state, _ = self.ledger.snapshot()
            self.assertIsNotNone(state['pending'])
            self.assertEqual(state['pending']['expected_revision'], mutation[2])
            writes.append(state['pending']['operation_id'])
        self.backend.before_mutate = inspect_pending
        for target in (TARGET, UPDATED, None):
            result = self.apply(target)
            self.assertTrue(result['mutation_submitted'])
            self.assertIsNone(self.ledger.snapshot()[0]['pending'])
            self.assertFalse(result['desktop_acceptance'])
        self.assertEqual(len(set(writes)), 3)
        self.assertIsNone(self.ledger.snapshot()[0]['origin'])
        self.assertEqual(h.read(self.backend.observe().descriptor)['user'], {})

    def test_noop_and_repeat_remove_do_not_submit_or_change_ledger(self):
        self.apply(None); self.assertEqual(self.backend.mutations, 0)
        self.apply(TARGET); original = self.ledger.path.read_bytes()
        self.assertFalse(self.apply(TARGET)['mutation_submitted'])
        self.assertEqual(self.ledger.path.read_bytes(), original)
        self.apply(None); original = self.ledger.path.read_bytes(); count = self.backend.mutations
        self.assertFalse(self.apply(None)['mutation_submitted'])
        self.assertEqual(self.ledger.path.read_bytes(), original)
        self.assertEqual(self.backend.mutations, count)

    def test_real_process_exit_recovery_keeps_same_native_backend_and_never_replays(self):
        for action, target in (('install', TARGET), ('update', UPDATED), ('remove', None)):
            for boundary in ('prepared', 'validated', 'submitted', 'committed'):
                with self.subTest(action=action, boundary=boundary):
                    if action != 'install':
                        self.apply(TARGET)
                    code, _ = self.run_worker(desired=target, exit_at=boundary)
                    self.assertEqual(code, 73)
                    generation = self.backend.identity.backend_generation
                    count = self.backend.mutations
                    handle = self.pending()
                    code, result = self.run_worker(mode='recover', handle=handle,
                                                  cancel=boundary in ('prepared', 'validated'))
                    self.assertEqual(code, 0); self.assertFalse(result['mutation_submitted'])
                    self.assertEqual(self.backend.mutations, count)
                    self.assertEqual(self.backend.view()['generation'], generation)
                    self.assertIsNone(self.ledger.snapshot()[0]['pending'])
                    self.crash_cases.append({'action': action, 'boundary': boundary,
                        'coordinator_exit': 73, 'same_native_backend': True, 'recovery_writes': 0})
                    # Return this retained backend to absence for the next case.
                    self.apply(None)
        self.assertEqual(len(self.crash_cases), 12)

    def test_transport_failure_before_send_requires_explicit_unchanged_cancel(self):
        self.backend.before_mutate = lambda _: (_ for _ in ()).throw(OSError('TEST_TRANSPORT'))
        with self.assertRaisesRegex(h.Conflict, '^DEEPSEEK_NATIVE_MUTATION_UNCERTAIN$'): self.apply(TARGET)
        self.assertEqual(self.backend.mutations, 0)
        handle = self.pending(); self.backend.before_mutate = None
        self.op.recover(*handle, cancel=True)
        self.assertIsNone(self.ledger.snapshot()[0]['origin'])
        self.assertEqual(self.backend.mutations, 0)

    def test_lost_response_after_real_write_is_recovered_without_second_mutation(self):
        native = self.backend.mutate
        def lost_reply(mutation):
            native(mutation); raise OSError('TEST_LOST_REPLY')
        self.backend.mutate = lost_reply
        with self.assertRaisesRegex(h.Conflict, '^DEEPSEEK_NATIVE_MUTATION_UNCERTAIN$'): self.apply(TARGET)
        original = self.ledger.path.read_bytes(); handle = self.pending()
        with self.assertRaisesRegex(h.Conflict, 'DEEPSEEK_JOURNAL_CANCEL_CHANGED'):
            self.op.recover(*handle, cancel=True)
        self.assertEqual(self.ledger.path.read_bytes(), original)
        self.op.recover(*handle)
        self.assertEqual(self.backend.mutations, 1)
        self.assertIsNotNone(self.ledger.snapshot()[0]['origin'])

    def test_bad_readback_preserves_pending_until_authentic_observation(self):
        def bad(observation):
            if self.backend.mutations:
                desc = h.read(observation.descriptor); desc['user']['servers']['vf-studio']['url'] = 'https://foreign.invalid/mcp'
                return replace(observation, descriptor=h.encode(desc))
            return observation
        self.backend.observer = bad
        with self.assertRaisesRegex(h.Conflict, 'DEEPSEEK_READBACK_VALUE'): self.apply(TARGET)
        original = self.ledger.path.read_bytes(); handle = self.pending()
        with self.assertRaises(h.Conflict): self.op.recover(*handle)
        self.assertEqual(self.ledger.path.read_bytes(), original)
        self.backend.observer = None; self.op.recover(*handle)
        self.assertEqual(self.backend.mutations, 1)

    def test_pre_submit_external_change_is_preserved_and_not_replayed(self):
        count = 0
        def interfere(observation):
            nonlocal count
            count += 1
            if count == 2:
                desc = h.read(observation.descriptor)
                self.backend.mutate(('probe', [{'op': 'set', 'path': ['servers', 'test-other'],
                    'value': {'transport': 'stdio', 'command': 'INERT_NEVER_EXECUTED'}}], desc['revision']))
                return self.backend.observe()
            return observation
        self.backend.observer = interfere
        with self.assertRaisesRegex(h.Conflict, 'DEEPSEEK_PRECONDITION_CHANGED'): self.apply(TARGET)
        original = self.ledger.path.read_bytes(); config = self.backend.view()['file_sha256']; handle = self.pending()
        for cancel in (False, True):
            with self.assertRaises(h.Conflict): self.op.recover(*handle, cancel=cancel)
        self.assertEqual(self.ledger.path.read_bytes(), original)
        self.assertEqual(self.backend.view()['file_sha256'], config)
        self.assertEqual(self.backend.mutations, 1)

    def test_execute_lock_refuses_live_recovery_before_native_observation(self):
        # A second operation object still binds the same private execution lock.
        other = NativeOperation(OriginJournal(self.ledger_root, self.backend.identity), self.backend.identity, self.backend)
        count = self.backend.observations
        with self.op.locked(), self.assertRaisesRegex(h.Conflict, 'DEEPSEEK_OPERATION_BUSY'):
            other.recover('0' * 64, '0' * 32)
        with self.op.locked():
            code, result = self.run_worker(mode='recover', handle=('0' * 64, '0' * 32))
            self.assertEqual(code, 74)
            self.assertEqual(result['error'], 'DEEPSEEK_OPERATION_BUSY')
        self.assertEqual(self.backend.observations, count)

    def test_identity_change_is_refused_and_cannot_start_a_new_operation(self):
        self.backend.observer = lambda value: replace(value, identity=replace(value.identity, backend_generation='different'))
        with self.assertRaisesRegex(h.Conflict, 'DEEPSEEK_OPERATION_IDENTITY_CHANGED'): self.apply(TARGET)
        self.assertEqual(self.backend.mutations, 0)
        self.assertFalse(self.ledger.path.exists())

    def test_observation_failure_after_native_write_retains_pending_and_redacts_error(self):
        def unavailable(value):
            if self.backend.mutations:
                raise OSError('SYNTHETIC_PRIVATE_DIAGNOSTIC')
            return value
        self.backend.observer = unavailable
        with self.assertRaisesRegex(h.Conflict, '^DEEPSEEK_NATIVE_OBSERVATION_FAILED$'): self.apply(TARGET)
        original = self.ledger.path.read_bytes(); handle = self.pending()
        with self.assertRaisesRegex(h.Conflict, '^DEEPSEEK_NATIVE_OBSERVATION_FAILED$'): self.op.recover(*handle)
        self.assertEqual(self.ledger.path.read_bytes(), original)
        self.backend.observer = None; self.op.recover(*handle)
        self.assertEqual(self.backend.mutations, 1)

    def test_stale_cas_and_second_apply_cannot_replace_a_pending_operation(self):
        config = self.backend.view()['file_sha256']
        with self.assertRaisesRegex(h.Conflict, '^DEEPSEEK_JOURNAL_STALE$'):
            self.op.apply('0' * 64, TARGET)
        self.assertFalse(self.ledger.path.exists())
        self.backend.before_mutate = lambda _: (_ for _ in ()).throw(OSError('TEST_TRANSPORT'))
        with self.assertRaisesRegex(h.Conflict, '^DEEPSEEK_NATIVE_MUTATION_UNCERTAIN$'): self.apply(TARGET)
        original = self.ledger.path.read_bytes(); handle = self.pending()
        with self.assertRaisesRegex(h.Conflict, '^DEEPSEEK_JOURNAL_PENDING$'): self.apply(None)
        with self.assertRaisesRegex(h.Conflict, '^DEEPSEEK_JOURNAL_STALE$'):
            self.op.recover('0' * 64, handle[1], cancel=True)
        self.assertEqual(self.ledger.path.read_bytes(), original)
        self.assertEqual(self.backend.view()['file_sha256'], config)
        self.assertEqual(self.backend.mutations, 0)
        self.op.recover(*handle, cancel=True)

    @classmethod
    def tearDownClass(cls):
        target = os.environ.get('VF_DEEPSEEK_OPERATION_RECEIPT')
        if target:
            path = Path(target).resolve()
            path.relative_to(TOOLS.parents[2] / '.tmp/agent-skill-delivery')
            path.write_bytes(h.encode({'crash_cases': cls.crash_cases, 'desktop_acceptance': False,
                'same_native_fixture_preserved': True, 'transport': 'private-test-pipe-to-memory-http-carrier'}) + b'\n')


if __name__ == '__main__':
    if len(sys.argv) == 3 and sys.argv[1] == 'worker':
        try:
            worker(sys.argv[2])
        except h.Conflict as error:
            code = str(error) if re.fullmatch('[A-Z0-9_]{1,80}', str(error)) else 'DEEPSEEK_TEST_WORKER_FAILED'
            sys.stdout.buffer.write(h.encode({'failed': code}) + b'\n')
            sys.exit(74)
    else:
        unittest.main()
