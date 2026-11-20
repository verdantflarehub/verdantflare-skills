"""Closed native lifecycle tests against the official isolated backend.

No real desktop credentials, MCP business calls or Windows admission.
"""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import os
import subprocess
import sys
import unittest
import uuid

import test_operation as fixture
import host_deepseek as h
import install_transaction as io
from native_install_operation import NativeInstallOperation
from host_deepseek_journal import OriginJournal
from host_deepseek_operation import NativeOperation
from install_operation import InstallOperation
from runtime_tools.runtime_lifecycle import RuntimeLifecycle
from runtime_tools.runtime_store import SCHEMA


class Crash(BaseException):
    pass


class NativeLifecycleTests(unittest.TestCase):
    def setUp(self):
        fixture.NativeOperationTests.setUp(self)
        for name in ('skills', 'runtime', 'sources'):
            (self.root / name).mkdir(mode=0o700)
        self.engine = NativeInstallOperation(self.ledger_root, self.root / 'skills', self.root / 'runtime', self.op)
        self.manager = RuntimeLifecycle(self.engine.runtime_store, [self.engine])
        self.manager.initialize()
        self.key = io.sha(io.encode(self.engine.bindings))
        self.trees = {}
        for version in ('1', '2', '3'):
            raw = ('#!/bin/sh\nprintf "%s\\n" "fixture-' + version + '"\n').encode()
            source = self.root / 'sources' / version
            source.write_bytes(raw)
            manifest = {'schema': SCHEMA, 'release': 'fixture-runtime.' + version,
                'source_sha256': io.sha(raw), 'entries': {'bin/vf-fixture': {
                    'type': 'file', 'sha256': io.sha(raw), 'size': len(raw), 'mode': 0o755}}}
            digest = io.sha(io.encode(manifest))
            self.manager.stage(manifest, {'bin/vf-fixture': source}, expected_digest=digest,
                               transaction_id=uuid.uuid4().hex)
            self.trees[version] = digest

    def apply(self, version='1', *, target=None, checkpoint=lambda _: None, settle=True):
        receipt = self.manager.apply(self.key, 'fixture-skills.' + version,
            {'example/SKILL.md': version.encode()}, target or (fixture.TARGET if version == '1' else fixture.UPDATED),
            runtime_digest=self.trees[version], checkpoint=checkpoint, **self.engine.preflight())
        if settle:
            self.settle()
        return receipt

    def settle(self, **kwargs):
        for _ in range(3):
            self.manager.recover_operation(self.key, **kwargs)

    def collect(self, version):
        inventory = self.manager.inventory()
        return self.manager.collect(self.trees[version], expected_revision=inventory['revision'])

    @staticmethod
    def crash(boundary):
        def stop(stage):
            if stage == boundary:
                raise Crash()
        return stop

    def test_install_update_remove_reinstall_and_reference_gc(self):
        self.apply()
        self.assertEqual(self.manager.run(self.trees['1'], 'bin/vf-fixture', capture_output=True,
                                         check=True).stdout.strip(), b'fixture-1')
        with self.assertRaisesRegex(io.Conflict, '^RUNTIME_VERSION_PROTECTED$'):
            self.collect('1')
        self.apply('2')
        self.assertEqual(self.collect('1')['status'], 'runtime_collected')
        self.manager.uninstall(self.key, **self.engine.preflight())
        self.settle()
        self.assertEqual(self.collect('2')['status'], 'runtime_collected')
        self.assertEqual(self.backend.mutations, 3)
        self.assertFalse(self.manager.inventory()['blockers'])
        self.apply('3')
        self.assertEqual(self.backend.mutations, 4)

    def test_noop_native_update_can_release_previous_runtime(self):
        self.apply(); mutations = self.backend.mutations
        self.apply('2', target=fixture.TARGET)
        self.assertEqual(self.backend.mutations, mutations)
        self.assertEqual(self.collect('1')['status'], 'runtime_collected')

    def test_pending_and_uncertain_native_submission_protect_all_versions(self):
        for boundary in ('skills', 'native:prepared', 'native:submitted', 'committed'):
            with self.subTest(boundary=boundary):
                with self.assertRaises(Crash):
                    self.apply(checkpoint=self.crash(boundary))
                before = self.backend.mutations
                inventory = self.manager.inventory()
                self.assertTrue(inventory['blockers'])
                with self.assertRaisesRegex(io.Conflict, '^RUNTIME_RECOVERY_REQUIRED$'):
                    self.manager.collect(self.trees['3'], expected_revision=inventory['revision'])
                with self.assertRaisesRegex(io.Conflict, '^RUNTIME_RECOVERY_REQUIRED$'):
                    with self.manager.lease(self.trees['1']):
                        self.fail('pending operation admitted execution')
                self.settle(cancel_native=boundary == 'native:prepared')
                self.assertEqual(self.backend.mutations, before)
                self.assertFalse(self.manager.inventory()['blockers'])
                self.manager.uninstall(self.key, **self.engine.preflight()); self.settle()

    def test_actual_native_drift_cannot_be_treated_as_unreferenced(self):
        self.apply()
        before = self.manager.inventory()
        desc = h.read(self.backend.observe().descriptor)
        self.backend.mutate(('probe', [{'op': 'set', 'path': ['servers', 'test-other'],
            'value': {'transport': 'stdio', 'command': 'INERT_NEVER_EXECUTED'}}], desc['revision']))
        original = self.ledger.path.read_bytes(); count = self.backend.mutations
        with self.assertRaisesRegex(io.Conflict, '^LIFECYCLE_NATIVE_CHANGED$'):
            self.manager.collect(self.trees['3'], expected_revision=before['revision'])
        self.assertTrue(self.engine.runtime_store.versions.joinpath(self.trees['3']).exists())
        self.assertEqual(self.ledger.path.read_bytes(), original)
        self.assertEqual(self.backend.mutations, count)

    def test_initial_native_drift_and_changed_identity_fail_closed(self):
        self.backend.observer = lambda value: replace(value,
            identity=replace(value.identity, backend_generation='CHANGED_FIXTURE_GENERATION'))
        with self.assertRaisesRegex(h.Conflict, '^DEEPSEEK_OPERATION_IDENTITY_CHANGED$'):
            self.manager.inventory()
        self.backend.observer = None
        desc = h.read(self.backend.observe().descriptor)
        self.backend.mutate(('probe', [{'op': 'set', 'path': ['servers', 'test-other'],
            'value': {'transport': 'stdio', 'command': 'INERT_NEVER_EXECUTED'}}], desc['revision']))
        with self.assertRaisesRegex(io.Conflict, '^INITIAL_SCOPE_CHANGED$'):
            self.manager.inventory()

    def test_unknown_source_and_orphan_parent_are_preserved(self):
        self.apply()
        for directory, name, expected in (
            (self.ledger.directory, 'unexpected', 'UNKNOWN_NATIVE_SOURCE_CONTENT'),
            (self.ledger.base, 'unknown-scope', 'UNREGISTERED_NATIVE_SOURCE')):
            path = directory / name
            if directory == self.ledger.base:
                path.mkdir(mode=0o700)
            else:
                path.write_bytes(b'KEEP'); path.chmod(0o600)
            with self.assertRaisesRegex(io.Conflict, '^' + expected + '$'):
                self.collect('3')
            self.assertTrue(path.exists())
            path.rmdir() if path.is_dir() else path.unlink()
        active = self.engine._active(); orphan = {**active, 'id': uuid.uuid4().hex, 'state': 'skills'}
        path = self.engine.meta / (orphan['id'] + '.json')
        path.write_bytes(io.encode(orphan)); path.chmod(0o600)
        with self.assertRaisesRegex(io.Conflict, '^ORPHAN_OPERATION_REQUIRES_RECOVERY$'):
            self.collect('3')
        self.assertTrue(path.exists())

    def test_stale_inventory_and_actual_skill_bytes_are_protected(self):
        before = self.manager.inventory(); self.apply()
        with self.assertRaisesRegex(io.Conflict, '^LIFECYCLE_REVISION_CHANGED$'):
            self.manager.collect(self.trees['3'], expected_revision=before['revision'])
        path = self.engine.skills.root / 'example/SKILL.md'; path.write_bytes(b'USER_EDIT')
        with self.assertRaisesRegex(io.Conflict, '^LOCAL_CHANGE_CONFLICT$'):
            self.collect('3')
        self.assertEqual(path.read_bytes(), b'USER_EDIT')

    def test_quiescent_snapshot_holds_native_execution_and_source_locks(self):
        self.apply()
        before = self.manager.inventory()
        with self.manager.quiescent_snapshot(expected_revision=before['revision']):
            for lock, expected in ((self.op.locked, 'DEEPSEEK_OPERATION_BUSY'),
                                   (self.ledger.locked, 'DEEPSEEK_JOURNAL_BUSY')):
                with self.assertRaisesRegex(h.Conflict, '^' + expected + '$'):
                    with lock():
                        self.fail('native lock was released during snapshot')
        self.assertEqual(self.manager.inventory(), before)

    def test_actual_activity_lease_blocks_update_and_gc(self):
        self.apply()
        with self.manager.lease(self.trees['1']) as fd:
            child = subprocess.Popen([sys.executable, '-B', '-c',
                'import sys; print("ready", flush=True); sys.stdin.buffer.read(1)'],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, pass_fds=(fd,))
        try:
            self.assertEqual(child.stdout.readline(), b'ready\n')
            with self.assertRaisesRegex(io.Conflict, '^RUNTIME_ACTIVITY_BUSY$'):
                self.apply('2')
            with self.assertRaisesRegex(io.Conflict, '^RUNTIME_ACTIVITY_BUSY$'):
                self.collect('3')
        finally:
            child.stdin.close(); child.wait(timeout=10); child.stdout.close()
        self.apply('2')

    def fresh(self):
        root = self.root / 'fresh'; root.mkdir(mode=0o700)
        for name in ('state', 'skills', 'runtime', 'file-state', 'file-skills', 'file-config'):
            (root / name).mkdir(mode=0o700)
        native = NativeOperation(OriginJournal(root / 'state', self.backend.identity),
                                 self.backend.identity, self.backend)
        engine = NativeInstallOperation(root / 'state', root / 'skills', root / 'runtime', native)
        file_scope = InstallOperation(root / 'file-state', root / 'file-skills', root / 'file-config',
                                     'mcp.json', runtime_root=root / 'runtime')
        return root, native, engine, file_scope

    def test_initialization_never_adopts_existing_native_origin(self):
        _, native, engine, _ = self.fresh()
        native.apply(native.journal.snapshot()[1], fixture.TARGET)
        manager = RuntimeLifecycle(engine.runtime_store, [engine])
        with self.assertRaisesRegex(io.Conflict, '^FRESH_NATIVE_SCOPE_REQUIRED$'):
            manager.initialize()
        self.assertFalse((manager.meta / 'seal.json').exists())
        self.assertEqual(self.backend.mutations, 1)

    def test_missing_source_directory_is_not_recreated_by_inventory(self):
        directory = self.ledger.directory
        saved = directory.with_name('saved-source')
        directory.rename(saved)
        with self.assertRaisesRegex(io.Conflict, '^NATIVE_SOURCE_DIRECTORY_MISSING$'):
            self.manager.inventory()
        self.assertFalse(directory.exists())
        saved.rename(directory)
        self.assertFalse(self.manager.inventory()['blockers'])

    def test_mixed_scopes_keep_both_targets_until_native_update(self):
        _, native, engine, file_scope = self.fresh()
        manager = RuntimeLifecycle(engine.runtime_store, [file_scope, engine])
        manager.initialize()
        self.assertEqual(RuntimeLifecycle(engine.runtime_store, [engine, file_scope]).initialize()['status'],
                         'scope_verified')
        with self.assertRaisesRegex(io.Conflict, '^LIFECYCLE_SCOPE_CHANGED$'):
            RuntimeLifecycle(engine.runtime_store, [engine]).initialize()
        for version in ('1', '2', '3'):
            digest = self.trees[version]
            manifest = self.engine.runtime_store._manifest(digest)
            source = self.engine.runtime_store.versions / digest / 'tree/bin/vf-fixture'
            manager.stage(manifest, {'bin/vf-fixture': source}, expected_digest=digest,
                          transaction_id=uuid.uuid4().hex)
        native_key = io.sha(io.encode(engine.bindings)); file_key = io.sha(io.encode(file_scope.bindings))
        manager.apply(native_key, 'native.1', {'example/SKILL.md': b'1'}, fixture.TARGET,
                      runtime_digest=self.trees['1'], **engine.preflight())
        manager.recover_operation(native_key)
        observed = file_scope.config.observe()
        manager.apply(file_key, 'file.2', {'other/SKILL.md': b'2'}, {'url': 'https://fixture.invalid'},
            expected_skill_revision=file_scope.skills.snapshot()[1],
            expected_config_revision=observed['ledger_revision'], expected_config_file=observed['file'],
            runtime_digest=self.trees['2'], expected_runtime_revision=file_scope.runtime.snapshot()[1])
        manager.recover_operation(file_key)
        before = manager.inventory()
        for version in ('1', '2'):
            self.assertTrue(before['versions'][self.trees[version]]['references'])
        with self.assertRaisesRegex(io.Conflict, '^RUNTIME_VERSION_PROTECTED$'):
            manager.collect(self.trees['1'], expected_revision=before['revision'])
        manager.apply(native_key, 'native.2', {'example/SKILL.md': b'2'}, fixture.UPDATED,
                      runtime_digest=self.trees['2'], **engine.preflight())
        manager.recover_operation(native_key)
        before = manager.inventory()
        self.assertEqual(manager.collect(self.trees['1'], expected_revision=before['revision'])['status'],
                         'runtime_collected')

    def test_gc_resume_reobserves_native_before_deleting_quarantine(self):
        self.apply(); inventory = self.manager.inventory()
        with self.assertRaises(Crash):
            self.manager.collect(self.trees['3'], expected_revision=inventory['revision'],
                                 checkpoint=self.crash('quarantined'))
        quarantine = list(self.manager.trash.iterdir())
        self.assertEqual(len(quarantine), 1)
        original = {str(p.relative_to(quarantine[0])): p.read_bytes()
                    for p in quarantine[0].rglob('*') if p.is_file()}
        desc = h.read(self.backend.observe().descriptor)
        self.backend.mutate(('probe', [{'op': 'set', 'path': ['servers', 'test-other'],
            'value': {'transport': 'stdio', 'command': 'INERT_NEVER_EXECUTED'}}], desc['revision']))
        count = self.backend.mutations; ledger = self.ledger.path.read_bytes()
        with self.assertRaisesRegex(io.Conflict, '^LIFECYCLE_NATIVE_CHANGED$'):
            self.manager.recover_gc()
        self.assertEqual({str(p.relative_to(quarantine[0])): p.read_bytes()
                         for p in quarantine[0].rglob('*') if p.is_file()}, original)
        self.assertEqual(self.backend.mutations, count)
        self.assertEqual(self.ledger.path.read_bytes(), ledger)


if __name__ == '__main__':
    unittest.main()
