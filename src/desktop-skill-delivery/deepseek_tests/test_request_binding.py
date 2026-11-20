"""Recover native children using only a durably prewritten parent intent.

The native backend is the same retained official isolated fixture. Parent
intents here are test records, not a completed multi-resource installer.
"""
from __future__ import annotations

import os
from pathlib import Path
import unittest
import uuid

import test_operation as fixture
import host_deepseek as h
import install_transaction as io

TARGET, UPDATED = fixture.TARGET, fixture.UPDATED


class RequestBindingTests(unittest.TestCase):
    # Reuse fixture/pipe worker helpers without inheriting its test methods.
    setUp = fixture.NativeOperationTests.setUp
    apply = fixture.NativeOperationTests.apply
    run_worker = fixture.NativeOperationTests.run_worker
    crash_cases = []

    def intent(self, desired):
        intent = {'expected': self.ledger.snapshot()[1], 'request_id': uuid.uuid4().hex,
                  'target_sha256': None if desired is None else h.digest(desired)}
        io.atomic(self.root / 'parent-intent.json', h.encode(intent), mode=0o600)
        return intent

    def recover(self, intent, **kw):
        return self.op.recover_request(intent['expected'], intent['request_id'],
                                       intent['target_sha256'], **kw)

    def test_real_process_exit_recovers_without_a_returned_child_handle(self):
        for action, desired in (('install', TARGET), ('update', UPDATED), ('remove', None)):
            for boundary in ('parent_prepared', 'prepared', 'validated', 'submitted', 'committed'):
                with self.subTest(action=action, boundary=boundary):
                    if action != 'install':
                        self.apply(TARGET)
                    self.intent(desired)
                    # The request file is synchronized BEFORE the first child.
                    intent_path = self.root / 'parent-intent.json'
                    intent_bytes = intent_path.read_bytes()
                    self.assertEqual(intent_path.stat().st_mode & 0o777, 0o600)
                    code, result = self.run_worker(desired=desired, exit_at=boundary,
                                                  intent=h.read(intent_bytes))
                    self.assertEqual(code, 73); self.assertIsNone(result)
                    count = self.backend.mutations
                    generation = self.backend.identity.backend_generation
                    # No ledger/pending handle read: the only recovery input is
                    # the original parent intent plus this retained transport.
                    code, result = self.run_worker(mode='recover_request',
                        intent=h.read(intent_path.read_bytes()),
                        cancel=boundary in ('parent_prepared', 'prepared', 'validated'))
                    self.assertEqual(code, 0); self.assertFalse(result['mutation_submitted'])
                    self.assertEqual(self.backend.mutations, count)
                    self.assertEqual(self.backend.view()['generation'], generation)
                    self.assertEqual(intent_path.read_bytes(), intent_bytes)
                    self.assertEqual(result['status'], 'native_request_not_prepared'
                        if boundary == 'parent_prepared' else 'native_recovery_verified')
                    self.assertIsNone(self.ledger.snapshot()[0]['pending'])
                    self.crash_cases.append({'action': action, 'boundary': boundary,
                        'coordinator_exit': 73, 'returned_child_handle': False,
                        'same_native_backend': True, 'recovery_writes': 0})
                    self.apply(None)
        self.assertEqual(len(self.crash_cases), 15)

    def test_wrong_parent_cas_request_or_target_cannot_adopt_pending_or_completion(self):
        intent = self.intent(TARGET)
        self.run_worker(desired=TARGET, exit_at='prepared', intent=intent)
        original = self.ledger.path.read_bytes(); count = self.backend.mutations
        wrongs = ({**intent, 'expected': '0' * 64},
                  {**intent, 'request_id': '0' * 32},
                  {**intent, 'target_sha256': h.digest(UPDATED)},
                  {**intent, 'target_sha256': None})
        for wrong in wrongs:
            with self.assertRaisesRegex(h.Conflict, '^DEEPSEEK_REQUEST_MISMATCH$'):
                self.recover(wrong, cancel=True)
            self.assertEqual(self.ledger.path.read_bytes(), original)
        self.recover(intent, cancel=True)
        original = self.ledger.path.read_bytes()
        for wrong in wrongs:
            with self.assertRaisesRegex(h.Conflict, '^DEEPSEEK_REQUEST_MISMATCH$'):
                self.recover(wrong, cancel=True)
        self.assertEqual(self.ledger.path.read_bytes(), original)
        self.assertEqual(self.backend.mutations, count)

    def test_lost_reply_is_completed_from_parent_intent_and_cancel_is_refused(self):
        intent = self.intent(TARGET)
        native = self.backend.mutate
        def lost(mutation):
            native(mutation); raise OSError('TEST_LOST_REPLY')
        self.backend.mutate = lost
        with self.assertRaisesRegex(h.Conflict, '^DEEPSEEK_NATIVE_MUTATION_UNCERTAIN$'):
            self.op.apply(intent['expected'], TARGET, request_id=intent['request_id'])
        original = self.ledger.path.read_bytes()
        with self.assertRaisesRegex(h.Conflict, '^DEEPSEEK_JOURNAL_CANCEL_CHANGED$'):
            self.recover(intent, cancel=True)
        self.assertEqual(self.ledger.path.read_bytes(), original)
        self.recover(intent)
        original = self.ledger.path.read_bytes()
        self.recover(intent)
        self.assertEqual(self.ledger.path.read_bytes(), original)
        self.assertEqual(self.backend.mutations, 1)

    def test_unstarted_or_noop_request_does_not_claim_native_state_or_write_ledger(self):
        for desired in (None, TARGET):
            intent = self.intent(desired)
            count = self.backend.observations
            result = self.recover(intent)
            self.assertEqual(result['status'], 'native_request_not_prepared')
            self.assertFalse(result['native_configuration_verified'])
            self.assertEqual(self.backend.observations, count)
            self.assertFalse(self.ledger.path.exists())
        intent = self.intent(None)
        self.op.apply(intent['expected'], None, request_id=intent['request_id'])
        self.assertEqual(self.recover(intent)['status'], 'native_request_not_prepared')
        self.assertEqual(self.backend.mutations, 0)
        # Host changes without our preparation do not turn ledger absence into
        # a claim about the host (nor trigger any recovery mutation).
        observed = self.backend.observe()
        self.backend.mutate(('probe', [{'op': 'set', 'path': ['servers', 'test-other'],
            'value': {'transport': 'stdio', 'command': 'INERT_NEVER_EXECUTED'}}],
            h.read(observed.descriptor)['revision']))
        self.assertFalse(self.recover(intent)['native_configuration_verified'])
        self.assertEqual(self.backend.mutations, 1)

    def test_newer_pending_or_completion_cannot_be_relabelled_as_old_request(self):
        old = self.intent(TARGET)
        self.op.apply(old['expected'], TARGET, request_id=old['request_id'])
        newer = self.intent(UPDATED)
        self.run_worker(desired=UPDATED, exit_at='prepared', intent=newer)
        original = self.ledger.path.read_bytes(); count = self.backend.observations
        with self.assertRaisesRegex(h.Conflict, '^DEEPSEEK_REQUEST_MISMATCH$'):
            self.recover(old)
        self.assertEqual(self.ledger.path.read_bytes(), original)
        self.assertEqual(self.backend.observations, count)
        self.recover(newer, cancel=True)
        with self.assertRaisesRegex(h.Conflict, '^DEEPSEEK_REQUEST_MISMATCH$'):
            self.recover(old)

    def test_invalid_inputs_and_live_execution_lock_refuse_before_observation(self):
        intent = self.intent(TARGET)
        count = self.backend.observations
        for invalid in (True, '', 'A' * 32, '0' * 31):
            with self.assertRaisesRegex(h.Conflict, '^DEEPSEEK_REQUEST_INVALID$'):
                self.op.apply(intent['expected'], TARGET, request_id=invalid)
            with self.assertRaisesRegex(h.Conflict, '^DEEPSEEK_REQUEST_INVALID$'):
                self.recover({**intent, 'request_id': invalid})
        for field in ('expected', 'target_sha256'):
            for invalid in (False, '', 'G' * 64, '0' * 63):
                with self.assertRaisesRegex(h.Conflict, '^DEEPSEEK_REQUEST_INVALID$'):
                    self.recover({**intent, field: invalid})
        with self.op.locked():
            code, result = self.run_worker(mode='recover_request', intent=intent)
            self.assertEqual(code, 74)
            self.assertEqual(result['error'], 'DEEPSEEK_OPERATION_BUSY')
        self.assertEqual(self.backend.observations, count)
        self.assertFalse(self.ledger.path.exists())

    def test_pending_parent_state_or_target_corruption_cannot_be_adopted(self):
        intent = self.intent(TARGET)
        self.run_worker(desired=TARGET, exit_at='prepared', intent=intent)
        original = self.ledger.path.read_bytes(); count = self.backend.observations
        for change in ('sequence', 'target'):
            state = h.read(original)
            if change == 'sequence':
                state['sequence'] += 1
            else:
                state['pending']['target_sha256'] = h.digest(UPDATED)
            self.ledger._write(state)
            changed = self.ledger.path.read_bytes()
            with self.assertRaisesRegex(h.Conflict, '^DEEPSEEK_REQUEST_MISMATCH$'):
                self.recover(intent, cancel=True)
            self.assertEqual(self.ledger.path.read_bytes(), changed)
        io.atomic(self.ledger.path, original, mode=0o600)
        self.assertEqual(self.backend.observations, count)
        self.recover(intent, cancel=True)
        self.assertEqual(self.backend.mutations, 0)

    def test_legacy_unbound_handles_and_schema_continue_without_migration(self):
        _, revision = self.ledger.snapshot()
        code, _ = self.run_worker(desired=TARGET, exit_at='prepared')
        self.assertEqual(code, 73)
        state, pending_cas = self.ledger.snapshot()
        self.assertEqual(state['schema'], 'vf.deepseek-origin.transaction/v1')
        operation_id = state['pending']['operation_id']
        self.op.recover(pending_cas, operation_id, cancel=True)
        original = self.ledger.path.read_bytes()
        self.op.recover(pending_cas, operation_id, cancel=True)
        self.assertEqual(self.ledger.path.read_bytes(), original)
        intent = self.intent(TARGET)
        result = self.op.apply(intent['expected'], TARGET, request_id=intent['request_id'])
        self.assertNotEqual(result['operation_id'], operation_id)
        self.assertEqual(self.ledger.snapshot()[0]['schema'], state['schema'])
        # The old CAS cannot replay an old logical intent into the new state.
        with self.assertRaisesRegex(h.Conflict, '^DEEPSEEK_JOURNAL_STALE$'):
            self.op.apply(revision, TARGET, request_id=intent['request_id'])
        self.assertEqual(self.backend.mutations, 1)

    @classmethod
    def tearDownClass(cls):
        target = os.environ.get('VF_DEEPSEEK_REQUEST_RECEIPT')
        if target:
            path = Path(target).resolve()
            path.relative_to(Path(__file__).resolve().parents[4] / '.tmp/agent-skill-delivery')
            path.write_bytes(h.encode({'crash_cases': cls.crash_cases,
                'desktop_acceptance': False, 'three_resource_acceptance': False,
                'same_native_fixture_preserved': True}) + b'\n')


if __name__ == '__main__':
    unittest.main()
