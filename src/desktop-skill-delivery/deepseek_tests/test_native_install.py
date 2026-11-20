"""Three-resource coordination against the retained official isolated backend.

Runtime trees below are executable local test fixtures, not product runtimes.
No real desktop, user credentials or lifecycle admission is exercised here.
"""
from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
import re
import subprocess
import sys
import unittest
import uuid

import test_operation as fixture
import host_deepseek as h
import install_transaction as io
from native_install_operation import NativeInstallOperation
from host_deepseek_journal import OriginJournal
from host_deepseek_operation import NativeOperation, Observation
from runtime_tools.runtime_store import SCHEMA


class Crash(BaseException):
    pass


def worker(path):
    request = h.read(Path(path).read_bytes())
    identity = h.Identity(**request['identity'])
    root = Path(request['root'])

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

    native = NativeOperation(OriginJournal(root / 'ledger', identity), identity, Forward())
    engine = NativeInstallOperation(root / 'ledger', root / 'skills', root / 'runtime', native)
    def checkpoint(stage):
        if stage == request['exit_at']:
            os._exit(73)
    if request['mode'] == 'native_recover':
        for _ in range(3):
            result = engine.recover(cancel_native=request['cancel'], checkpoint=checkpoint)
    elif request['mode'] == 'native_remove':
        result = engine.uninstall(**request['preflight'], checkpoint=checkpoint)
    else:
        version = request['version']
        result = engine.apply('fixture-skills.' + version, {'example/SKILL.md': version.encode()},
            fixture.TARGET if request.get('native_version', version) == '1' else fixture.UPDATED,
            runtime_digest=request['tree'], checkpoint=checkpoint, **request['preflight'])
    sys.stdout.buffer.write(h.encode({'done': True, 'result': result}) + b'\n')


class NativeInstallTests(unittest.TestCase):
    setUp_backend = fixture.NativeOperationTests.setUp
    pipe_worker = fixture.NativeOperationTests.run_worker
    crash_cases = []

    def setUp(self):
        self.setUp_backend()
        for name in ('skills', 'runtime', 'sources'):
            (self.root / name).mkdir(mode=0o700)
        self.engine = NativeInstallOperation(self.ledger_root, self.root / 'skills',
                                              self.root / 'runtime', self.op)
        self.trees = {}
        for version in ('1', '2'):
            raw = ('#!/bin/sh\nprintf "%s\\n" "fixture-' + version + '"\n').encode()
            source = self.root / 'sources' / version; source.write_bytes(raw)
            manifest = {'schema': SCHEMA, 'release': 'fixture-runtime.' + version,
                'source_sha256': io.sha(raw), 'entries': {'bin/vf-fixture': {
                    'type': 'file', 'sha256': io.sha(raw), 'size': len(raw), 'mode': 0o755}}}
            digest = io.sha(io.encode(manifest))
            self.engine.runtime_store.stage(manifest, {'bin/vf-fixture': source},
                expected_digest=digest, transaction_id=uuid.uuid4().hex)
            self.trees[version] = digest

    def apply(self, version='1', checkpoint=lambda _: None, native_version=None):
        return self.engine.apply('fixture-skills.' + version, {'example/SKILL.md': version.encode()},
            fixture.TARGET if (native_version or version) == '1' else fixture.UPDATED, runtime_digest=self.trees[version],
            checkpoint=checkpoint, **self.engine.preflight())

    def run_worker(self, mode, *, version='1', exit_at='', cancel=False, native_version=None):
        intent = {'root': str(self.root.relative_to(self.repo)), 'expected': '0' * 64,
                  'version': version, 'tree': self.trees[version]}
        if mode != 'native_recover':
            intent['preflight'] = self.engine.preflight()
        if native_version is not None:
            intent['native_version'] = native_version
        return self.pipe_worker(mode=mode, exit_at=exit_at, cancel=cancel, intent=intent,
                                worker_script=Path(__file__).resolve())

    def settle(self, **kw):
        for _ in range(3):
            self.engine.recover(**kw)

    def state(self):
        return (io.read(self.engine.runtime.root / 'runtime.json'), self.engine.runtime.snapshot(),
                io.read(self.engine.skills.root / 'example/SKILL.md'), self.engine.skills.snapshot(),
                self.backend.view()['file_sha256'])

    def test_install_update_uninstall_repeat_remove_and_reinstall(self):
        self.apply(); self.settle()
        self.assertEqual(self.engine._active()['state'], 'cleaned')
        self.assertEqual(h.read(self.backend.observe().descriptor)['user']['servers'][h.KEY], fixture.TARGET)
        before = self.state(); count = self.backend.mutations
        self.apply(); self.settle()
        self.assertEqual(self.state(), before); self.assertEqual(self.backend.mutations, count)
        self.apply('2'); self.settle()
        executable = self.engine.runtime_store.versions / self.trees['2'] / 'tree/bin/vf-fixture'
        self.assertEqual(executable.stat().st_mode & 0o777, 0o755)
        self.assertEqual(subprocess.check_output([str(executable)]).strip(), b'fixture-2')
        for _ in range(2):
            self.engine.uninstall(**self.engine.preflight()); self.settle()
            self.assertIsNone(io.read(self.engine.runtime.root / 'runtime.json'))
            self.assertIsNone(io.read(self.engine.skills.root / 'example/SKILL.md'))
            self.assertEqual(h.read(self.backend.observe().descriptor)['user'], {})
        self.apply(); self.settle()
        self.assertEqual(self.engine.skills.snapshot()[0]['release'], 'fixture-skills.1')
        self.assertEqual(self.backend.mutations, 4)

    def test_real_process_exits_recover_all_resources_without_native_replay(self):
        boundaries = ('preparing', 'runtime:file:runtime.json', 'runtime:committed', 'runtime',
            'skills:file:example/SKILL.md', 'skills:committed', 'skills', 'native:prepared',
            'native:validated', 'native:submitted', 'native:committed', 'native', 'committed')
        for action in ('install', 'update', 'remove'):
            for boundary in boundaries:
                with self.subTest(action=action, boundary=boundary):
                    if action != 'install':
                        self.apply(); self.settle()
                    before = self.state()
                    code, result = self.run_worker('native_remove' if action == 'remove' else 'native_apply',
                        version='2' if action == 'update' else '1', exit_at=boundary)
                    self.assertEqual(code, 73); self.assertIsNone(result)
                    count = self.backend.mutations
                    generation = self.backend.identity.backend_generation
                    cancel = boundary in ('native:prepared', 'native:validated')
                    if cancel:
                        original = self.engine.active_path.read_bytes()
                        code, result = self.run_worker('native_recover')
                        self.assertEqual(code, 74)
                        self.assertEqual(result['error'], 'DEEPSEEK_READBACK_REVISION')
                        self.assertEqual(self.engine.active_path.read_bytes(), original)
                    code, result = self.run_worker('native_recover', cancel=cancel)
                    self.assertEqual(code, 0)
                    self.assertEqual(self.backend.mutations, count)
                    self.assertEqual(self.backend.view()['generation'], generation)
                    committed = boundary in ('native:submitted', 'native:committed', 'native', 'committed')
                    self.assertEqual(self.engine._active()['state'], 'cleaned' if committed else 'cleaned_aborted')
                    if not committed:
                        self.assertEqual(self.state(), before)
                    elif action == 'remove':
                        self.assertIsNone(io.read(self.engine.runtime.root / 'runtime.json'))
                        self.assertIsNone(io.read(self.engine.skills.root / 'example/SKILL.md'))
                        self.assertEqual(h.read(self.backend.observe().descriptor)['user'], {})
                    else:
                        expected = '2' if action == 'update' else '1'
                        self.assertEqual(io.read(self.engine.skills.root / 'example/SKILL.md'), expected.encode())
                        self.assertEqual(io.decode(io.read(self.engine.runtime.root / 'runtime.json'))['tree_sha256'],
                                         self.trees[expected])
                        self.assertEqual(h.read(self.backend.observe().descriptor)['user']['servers'][h.KEY],
                                         fixture.UPDATED if expected == '2' else fixture.TARGET)
                    self.crash_cases.append({'action': action, 'boundary': boundary, 'coordinator_exit': 73,
                        'same_native_backend': True, 'recovery_native_writes': 0,
                        'result': 'committed' if committed else 'restored'})
                    self.engine.uninstall(**self.engine.preflight()); self.settle()
        self.assertEqual(len(self.crash_cases), 39)

    def test_lost_native_reply_finishes_local_commit_without_second_mutation(self):
        native = self.backend.mutate
        def lost(mutation):
            native(mutation); raise OSError('TEST_LOST_REPLY')
        self.backend.mutate = lost
        with self.assertRaisesRegex(h.Conflict, '^DEEPSEEK_NATIVE_MUTATION_UNCERTAIN$'):
            self.apply()
        self.assertEqual(io.read(self.engine.skills.root / 'example/SKILL.md'), b'1')
        self.assertEqual(self.engine._active()['state'], 'skills')
        self.settle()
        self.assertEqual(self.engine._active()['state'], 'cleaned')
        self.assertEqual(self.backend.mutations, 1)

    def test_uncertain_before_send_is_preserved_until_explicit_unchanged_cancel(self):
        before = self.state()
        self.backend.before_mutate = lambda _: (_ for _ in ()).throw(OSError('TEST_UNCERTAIN'))
        with self.assertRaisesRegex(h.Conflict, '^DEEPSEEK_NATIVE_MUTATION_UNCERTAIN$'):
            self.apply()
        preserved = self.state(); origin_bytes = self.ledger.path.read_bytes()
        with self.assertRaisesRegex(h.Conflict, '^DEEPSEEK_READBACK_REVISION$'):
            self.engine.recover()
        self.assertEqual(self.state(), preserved)
        self.assertEqual(self.ledger.path.read_bytes(), origin_bytes)
        self.settle(cancel_native=True)
        self.assertEqual(self.state(), before)
        self.assertEqual(self.backend.mutations, 0)

    def test_external_native_change_before_prepare_preserves_all_three_resources(self):
        def external(phase):
            if phase == 'skills':
                desc = h.read(self.backend.observe().descriptor)
                self.backend.mutate(('probe', [{'op': 'set', 'path': ['servers', 'test-other'],
                    'value': {'transport': 'stdio', 'command': 'INERT_NEVER_EXECUTED'}}], desc['revision']))
        with self.assertRaisesRegex(h.Conflict, '^DEEPSEEK_PRECONDITION_CHANGED$'):
            self.apply(checkpoint=external)
        preserved = self.state()
        with self.assertRaisesRegex(h.Conflict, '^DEEPSEEK_PRECONDITION_CHANGED$'):
            self.engine.recover()
        self.assertEqual(self.state(), preserved)
        self.assertFalse(self.ledger.path.exists())
        self.assertIn('test-other', h.read(self.backend.observe().descriptor)['user']['servers'])
        self.assertEqual(self.backend.mutations, 1)

    def test_user_file_after_native_commit_is_preserved_and_never_rolls_back_native(self):
        target = self.engine.skills.root / 'example/SKILL.md'
        def change(phase):
            if phase == 'native:submitted':
                target.write_bytes(b'USER_EDIT')
        with self.assertRaisesRegex(io.Conflict, '^LOCAL_CHANGE_CONFLICT$'):
            self.apply(checkpoint=change)
        preserved = self.backend.view()['file_sha256']
        with self.assertRaisesRegex(io.Conflict, '^LOCAL_CHANGE_CONFLICT$'):
            self.engine.recover()
        self.assertEqual(target.read_bytes(), b'USER_EDIT')
        self.assertEqual(self.backend.view()['file_sha256'], preserved)
        self.assertEqual(self.backend.mutations, 1)
        child = self.engine._active()['skills']
        self.assertTrue((self.engine.skills.meta / child['id'] / 'after/example/SKILL.md').exists())

    def test_native_noop_with_changed_files_recovers_before_or_after_local_completion(self):
        self.apply(); self.settle(); before = self.state()
        for boundary in ('preparing', 'runtime', 'skills', 'committed'):
            with self.subTest(boundary=boundary):
                code, _ = self.run_worker('native_apply', version='2', native_version='1', exit_at=boundary)
                self.assertEqual(code, 73)
                count = self.backend.mutations
                code, _ = self.run_worker('native_recover')
                self.assertEqual(code, 0)
                self.assertEqual(self.backend.mutations, count)
                if boundary in ('preparing', 'runtime'):
                    self.assertEqual(self.state(), before)
                else:
                    self.assertEqual(io.read(self.engine.skills.root / 'example/SKILL.md'), b'2')
                    self.assertEqual(h.read(self.backend.observe().descriptor)['user']['servers'][h.KEY], fixture.TARGET)
                self.apply(); self.settle()

    def test_internal_native_entrypoints_require_same_thread_execution_lock(self):
        count = self.backend.observations
        with self.assertRaisesRegex(h.Conflict, '^DEEPSEEK_OPERATION_LOCK_REQUIRED$'):
            self.op._apply_locked(self.ledger.snapshot()[1], fixture.TARGET)
        with self.op.locked(), ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(self.op._recover_request_locked, '0' * 64, '0' * 32, None)
            with self.assertRaisesRegex(h.Conflict, '^DEEPSEEK_OPERATION_LOCK_REQUIRED$'):
                future.result(timeout=5)
        self.assertEqual(self.backend.observations, count)
        self.assertEqual(self.backend.mutations, 0)

    def test_stale_preflight_and_parent_corruption_fail_closed(self):
        preflight = self.engine.preflight()
        for key in preflight:
            values = {**preflight, key: '0' * 64}
            with self.assertRaises((io.Conflict, h.Conflict)):
                self.engine.apply('fixture-skills.1', {'example/SKILL.md': b'1'}, fixture.TARGET,
                                  runtime_digest=self.trees['1'], **values)
            self.assertIsNone(self.engine._active())
            self.assertIsNone(io.read(self.engine.runtime.root / 'runtime.json'))
        self.apply()
        journal = self.engine._active(); path = self.engine.meta / (journal['id'] + '.json')
        original = path.read_bytes(); resources = self.state()
        for field, value in (('bindings', []), ('state', []), ('native', {})):
            modified = {**journal, field: value}; io.atomic(path, io.encode(modified), mode=0o600)
            with self.assertRaises(io.Conflict):
                self.engine.recover()
            self.assertEqual(self.state(), resources)
        io.atomic(path, original, mode=0o600)
        self.settle()

    def test_parent_and_native_locks_refuse_competing_execution_and_recovery(self):
        count = self.backend.observations
        for lock in (self.engine.locked, self.op.locked):
            with lock():
                code, result = self.run_worker('native_recover')
                self.assertEqual(code, 74)
                self.assertIn(result['error'], ('SCOPE_BUSY', 'DEEPSEEK_OPERATION_BUSY'))
        self.assertEqual(self.backend.observations, count)
        self.assertEqual(self.backend.mutations, 0)

    def test_forged_noop_flag_cannot_commit_an_unconfigured_native_target(self):
        code, _ = self.run_worker('native_apply', exit_at='skills')
        self.assertEqual(code, 73)
        journal = self.engine._active()
        journal['native']['changed'] = False
        path = self.engine.meta / (journal['id'] + '.json')
        io.atomic(path, io.encode(journal), mode=0o600)
        original = path.read_bytes(); resources = self.state()
        with self.assertRaisesRegex(h.Conflict, '^DEEPSEEK_NOOP_TARGET_MISMATCH$'):
            self.engine.recover()
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(self.state(), resources)
        self.assertEqual(self.backend.mutations, 0)

    def test_forged_noop_removal_cannot_leave_native_connection_installed(self):
        self.apply(); self.settle()
        code, _ = self.run_worker('native_remove', exit_at='skills')
        self.assertEqual(code, 73)
        journal = self.engine._active(); journal['native']['changed'] = False
        path = self.engine.meta / (journal['id'] + '.json')
        io.atomic(path, io.encode(journal), mode=0o600)
        original = path.read_bytes(); resources = self.state(); count = self.backend.mutations
        with self.assertRaisesRegex(h.Conflict, '^DEEPSEEK_NOOP_TARGET_MISMATCH$'):
            self.engine.recover()
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(self.state(), resources)
        self.assertEqual(self.backend.mutations, count)

    def test_identity_changed_after_native_write_retains_resources_until_authentic_readback(self):
        def change(phase):
            if phase == 'native:submitted':
                self.backend.observer = lambda o: replace(o,
                    identity=replace(o.identity, backend_generation='DIFFERENT_TEST_GENERATION'))
        with self.assertRaisesRegex(h.Conflict, '^DEEPSEEK_OPERATION_IDENTITY_CHANGED$'):
            self.apply(checkpoint=change)
        resources = self.state(); origin_bytes = self.ledger.path.read_bytes()
        with self.assertRaisesRegex(h.Conflict, '^DEEPSEEK_OPERATION_IDENTITY_CHANGED$'):
            self.engine.recover()
        self.assertEqual(self.state(), resources)
        self.assertEqual(self.ledger.path.read_bytes(), origin_bytes)
        self.backend.observer = None; self.settle()
        self.assertEqual(self.engine._active()['state'], 'cleaned')
        self.assertEqual(self.backend.mutations, 1)

    def test_user_mode_change_after_native_commit_is_preserved_with_backups(self):
        target = self.engine.skills.root / 'example/SKILL.md'
        def change(phase):
            if phase == 'native:submitted':
                target.chmod(0o755)
        with self.assertRaisesRegex(io.Conflict, '^LOCAL_MODE_CONFLICT$'):
            self.apply(checkpoint=change)
        with self.assertRaisesRegex(io.Conflict, '^LOCAL_MODE_CONFLICT$'):
            self.engine.recover()
        self.assertEqual(target.stat().st_mode & 0o777, 0o755)
        self.assertEqual(self.backend.mutations, 1)
        self.assertIsNotNone(self.ledger.snapshot()[0]['origin'])

    def test_parent_metadata_modes_links_and_scope_mismatch_refuse_before_native_observation(self):
        count = self.backend.observations
        path = self.engine.meta / 'scope.lock'
        with self.engine.locked():
            pass
        path.chmod(0o644)
        with self.assertRaisesRegex(io.Conflict, '^PRIVATE_FILE_REQUIRED$'):
            self.engine.recover()
        path.chmod(0o600)
        link = self.engine.meta / 'extra-lock-link'; os.link(path, link)
        with self.assertRaisesRegex(io.Conflict, '^PRIVATE_FILE_REQUIRED$'):
            self.engine.recover()
        link.unlink()
        path.unlink(); path.symlink_to(self.root / 'unused-lock')
        with self.assertRaisesRegex(io.Conflict, '^SYMLINK_REJECTED$'):
            self.engine.recover()
        path.unlink()
        other = NativeOperation(self.ledger, replace(self.backend.identity, profile='OTHER_TEST_PROFILE'), self.backend)
        with self.assertRaisesRegex(h.Conflict, '^DEEPSEEK_JOURNAL_SCOPE_MISMATCH$'):
            NativeInstallOperation(self.ledger_root, self.root / 'skills', self.root / 'runtime', other)
        self.assertEqual(self.backend.observations, count)
        self.assertEqual(self.backend.mutations, 0)

    @classmethod
    def tearDownClass(cls):
        target = os.environ.get('VF_DEEPSEEK_INSTALL_RECEIPT')
        if target:
            path = Path(target).resolve()
            path.relative_to(Path(__file__).resolve().parents[4] / '.tmp/agent-skill-delivery')
            path.write_bytes(h.encode({'crash_cases': cls.crash_cases, 'desktop_acceptance': False,
                'installer_acceptance': False, 'runtime_fixture': True,
                'same_native_fixture_preserved': True}) + b'\n')


if __name__ == '__main__':
    if len(sys.argv) == 3 and sys.argv[1] == 'worker':
        try:
            worker(sys.argv[2])
        except (io.Conflict, h.Conflict) as error:
            code = str(error) if re.fullmatch('[A-Z0-9_]{1,80}', str(error)) else 'DEEPSEEK_TEST_WORKER_FAILED'
            sys.stdout.buffer.write(h.encode({'failed': code}) + b'\n')
            sys.exit(74)
    else:
        unittest.main()
