"""Explicit cross-bundle CAS; fixtures are data, never runtime upgrade evidence."""
from contextlib import contextmanager
import shutil
from unittest import mock
import unittest
import uuid

import test_offline_operation as fixture

op = fixture.op


class BindingMigrationTests(unittest.TestCase):
    setUp = fixture.OfflineOperationTests.setUp
    write = fixture.OfflineOperationTests.write
    process = fixture.OfflineOperationTests.process
    engine = fixture.OfflineOperationTests.engine
    lifecycle = fixture.OfflineOperationTests.lifecycle
    run_action = fixture.OfflineOperationTests.run_action

    def migrate_request(self, revision=None, phase=None):
        value = {k: v for k, v in self.value.items() if k in op.COMMON}
        value.update(action='migrate-binding', previous_bootstrap_manifest_sha256=self.previous_pin,
                     inventory_revision=revision or self.inventory['revision'], interrupt_after=phase)
        return value

    def run_migration(self, value=None):
        self.write(value or self.migrate_request())
        return op.run(self.bundle, self.request_file)

    def prepare(self, *, fmt='json', interrupt=None):
        self.value.update(config_format=fmt, interrupt_after=interrupt)
        self.process(73 if interrupt else 0)
        self.value['interrupt_after'] = None
        self.inventory = self.run_action('inventory')['inventory']
        self.old_bundle = self.bundle
        self.previous_pin = op.tx.sha((self.bundle / 'bootstrap-manifest.json').read_bytes())
        self.bundle = self.root / 'next-bundle'
        shutil.copytree(self.old_bundle, self.bundle)
        raw = b'SECOND_DATA_ONLY_RUNTIME_NEVER_EXECUTED\n'
        entry = {'type': 'file', 'size': len(raw), 'sha256': op.tx.sha(raw), 'mode': 0o644}
        tree = {'schema': op.store.SCHEMA, 'release': 'runtime.fixture.2', 'source_sha256': '1' * 64,
                'entries': {'fixture.dat': entry}}
        self.next_tree_sha = op.tx.sha(op.tx.encode(tree))
        for name, data in {
            'resources/runtime/fixture.dat': raw,
            'resources/provenance/runtime-tree-manifest.json': op.tx.encode(tree),
            'resources/provenance/source-lock.json': op.tx.encode({
                'runtime_tree_sha256': self.next_tree_sha, 'platform': 'macos-arm64'}),
            'bootstrap-manifest.json': op.tx.encode({'entries': {'resources/runtime/fixture.dat': entry}})
        }.items():
            (self.bundle / name).write_bytes(data)
        self.next_pin = op.tx.sha((self.bundle / 'bootstrap-manifest.json').read_bytes())

    def snapshot(self):
        return {p.relative_to(self.scope).as_posix(): (p.read_bytes(), p.stat().st_mode & 0o777)
                for p in self.scope.rglob('*') if p.is_file()}

    def record_path(self):
        binding, previous = op.bindings(self.bundle, self.migrate_request())
        intent = {'schema': 'vf.bootstrap-binding-migration/v1', 'before': previous,
                  'after': binding, 'inventory_revision': self.inventory['revision']}
        return self.scope / 'state/.vf-bootstrap-binding' / (op.tx.sha(op.tx.encode(intent)) + '.json')

    def test_migration_is_separate_from_subsequent_different_tree_application(self):
        for fmt in ('json', 'toml'):
            with self.subTest(format=fmt):
                if fmt == 'toml':
                    self.setUp()
                self.prepare(fmt=fmt)
                before = self.snapshot()
                self.write()
                with self.assertRaisesRegex(op.tx.Conflict, 'OFFLINE_SCOPE_BINDING_MISMATCH'):
                    op.run(self.bundle, self.request_file)
                result = self.run_migration()
                self.assertEqual(result['status'], 'offline_binding_migrated')
                self.assertFalse(result['runtime_upgrade_acceptance'])
                self.assertEqual(self.run_action('inventory')['inventory'], self.inventory)
                after = self.snapshot()
                self.assertEqual(set(after) - set(before), {self.record_path().relative_to(self.scope).as_posix()})
                self.assertEqual({k: v for k, v in before.items() if k != 'scope.json'},
                                 {k: v for k, v in after.items() if k in before and k != 'scope.json'})
                self.assertEqual(self.run_migration()['status'], 'offline_binding_already_migrated')
                self.assertEqual(after, self.snapshot())
                applied = self.process(0)
                self.assertEqual(applied['runtime_tree_sha256'], self.next_tree_sha)
                self.assertNotEqual(self.tree_sha, self.next_tree_sha)
                pointer = op.tx.decode((self.engine().runtime.root / 'runtime.json').read_bytes())
                self.assertEqual(pointer['tree_sha256'], self.next_tree_sha)
                self.write()
                with self.assertRaisesRegex(op.tx.Conflict, 'OFFLINE_SCOPE_BINDING_MISMATCH'):
                    op.run(self.old_bundle, self.request_file)

    def test_real_process_exit_before_and_after_binding_commit_retries_original_intent(self):
        for fmt in ('json', 'toml'):
            for phase in ('binding-prepared', 'binding-committed'):
                with self.subTest(format=fmt, phase=phase):
                    self.setUp();self.prepare(fmt=fmt)
                    before = self.snapshot()
                    apply_value = self.value
                    self.value = self.migrate_request(phase=phase)
                    self.process(73)
                    self.value = apply_value
                    current = op.tx.decode((self.scope / 'scope.json').read_bytes())
                    self.assertEqual(current['bootstrap_manifest_sha256'],
                                     self.previous_pin if phase == 'binding-prepared' else self.next_pin)
                    self.assertTrue(self.record_path().exists())
                    result = self.run_migration()
                    self.assertEqual(result['status'], 'offline_binding_migrated' if phase == 'binding-prepared'
                                     else 'offline_binding_already_migrated')
                    self.assertEqual(self.run_action('inventory')['inventory'], self.inventory)
                    self.assertEqual({k: v for k, v in before.items() if k != 'scope.json'},
                                     {k: v for k, v in self.snapshot().items() if k in before and k != 'scope.json'})

    def test_failed_different_tree_update_recovers_previous_three_resources(self):
        for fmt in ('json', 'toml'):
            for phase in ('runtime', 'skills', 'config'):
                with self.subTest(format=fmt, phase=phase):
                    self.setUp();self.prepare(fmt=fmt);self.run_migration()
                    engine = self.engine()
                    config_before = engine.config.path.read_bytes()
                    pointer_before = (engine.runtime.root / 'runtime.json').read_bytes()
                    skill_before = (self.scope / 'skills/verdantflare-project/SKILL.md').read_bytes()
                    self.sample.write_bytes(fixture.sample_bytes(release='engineering.2', changed=True))
                    self.value.update(sample_sha256=op.tx.sha(self.sample.read_bytes()), interrupt_after=phase)
                    self.process(73)
                    self.run_action('recover')
                    self.assertEqual(engine.config.path.read_bytes(), config_before)
                    self.assertEqual((engine.runtime.root / 'runtime.json').read_bytes(), pointer_before)
                    self.assertEqual((self.scope / 'skills/verdantflare-project/SKILL.md').read_bytes(), skill_before)
                    self.value['interrupt_after'] = None
                    self.assertEqual(self.process(0)['runtime_tree_sha256'], self.next_tree_sha)

    def test_stale_snapshot_and_live_activity_refuse_without_changing_binding(self):
        self.prepare();before = self.snapshot()
        with self.assertRaisesRegex(op.tx.Conflict, 'LIFECYCLE_REVISION_CHANGED'):
            self.run_migration(self.migrate_request(revision='0' * 64))
        with self.lifecycle().lease(self.tree_sha):
            with self.assertRaisesRegex(op.tx.Conflict, 'RUNTIME_ACTIVITY_BUSY'):
                self.run_migration()
        self.assertEqual(self.snapshot(), before)

    def test_pending_operation_must_be_recovered_with_previous_bundle_first(self):
        self.prepare(interrupt='config');before = self.snapshot()
        with self.assertRaisesRegex(op.tx.Conflict, 'RUNTIME_RECOVERY_REQUIRED'):
            self.run_migration()
        self.assertEqual(before, self.snapshot())
        request = {k: v for k, v in self.value.items() if k in op.COMMON};request['action'] = 'recover'
        self.write(request);op.run(self.old_bundle, self.request_file)
        request['action'] = 'inventory';self.write(request)
        self.inventory = op.run(self.old_bundle, self.request_file)['inventory']
        self.assertEqual(self.run_migration()['status'], 'offline_binding_migrated')

    def test_partial_staging_blocks_binding_migration(self):
        self.prepare()
        raw = b'INCOMPLETE_DATA_TREE\n'
        source = self.root / 'unfinished.dat';source.write_bytes(raw);source.chmod(0o644)
        tree = {'schema': op.store.SCHEMA, 'release': 'unfinished.fixture', 'source_sha256': '2' * 64,
                'entries': {'unfinished.dat': {'type': 'file', 'size': len(raw),
                                               'sha256': op.tx.sha(raw), 'mode': 0o644}}}
        class Interrupted(BaseException):
            pass
        def stop(phase):
            if phase == 'prepared':
                raise Interrupted()
        with self.assertRaises(Interrupted):
            self.lifecycle().stage(tree, {'unfinished.dat': source}, expected_digest=op.tx.sha(op.tx.encode(tree)),
                                   transaction_id=uuid.uuid4().hex, checkpoint=stop)
        self.inventory = self.lifecycle().inventory();before = self.snapshot()
        with self.assertRaisesRegex(op.tx.Conflict, 'RUNTIME_STAGING_INCOMPLETE'):
            self.run_migration()
        self.assertEqual(before, self.snapshot())

    def test_missing_seal_legacy_schema_and_resource_drift_are_preserved(self):
        for kind in ('seal', 'legacy', 'skill', 'config', 'runtime'):
            with self.subTest(kind=kind):
                self.setUp();self.prepare()
                if kind == 'seal':
                    (self.scope / 'runtime/lifecycle/seal.json').unlink()
                elif kind == 'legacy':
                    path = self.scope / 'scope.json';value = op.tx.decode(path.read_bytes())
                    value['schema'] = 'vf.bootstrap-offline-scope/v1';path.write_bytes(op.tx.encode(value))
                else:
                    path = {'skill': self.scope / 'skills/verdantflare-project/SKILL.md',
                            'config': self.engine().config.path,
                            'runtime': self.scope / 'runtime/versions' / self.tree_sha / 'tree/fixture.dat'}[kind]
                    path.write_bytes(path.read_bytes() + b'USER_CHANGE')
                before = self.snapshot()
                with self.assertRaises(op.tx.Conflict):
                    self.run_migration()
                self.assertEqual(before, self.snapshot())

    def test_target_binding_without_matching_original_intent_is_not_success(self):
        self.prepare()
        binding, _ = op.bindings(self.bundle, self.migrate_request())
        (self.scope / 'scope.json').write_bytes(op.tx.encode(binding));before = self.snapshot()
        with self.assertRaisesRegex(op.tx.Conflict, 'OFFLINE_MIGRATION_RECORD_REQUIRED'):
            self.run_migration()
        self.assertEqual(before, self.snapshot())

    def test_missing_registration_directories_are_not_recreated_by_engine_constructors(self):
        for relative in ('runtime/lifecycle/quarantine', 'config/{meta}', 'runtime/activation/{files}'):
            with self.subTest(relative=relative):
                self.setUp();self.prepare()
                path = self.scope / relative.format(meta=op.cfg.META, files=op.tx.META)
                shutil.rmtree(path)
                before = self.snapshot()
                dirs = {p.relative_to(self.scope).as_posix() for p in self.scope.rglob('*') if p.is_dir()}
                with self.assertRaisesRegex(op.tx.Conflict, 'OFFLINE_REGISTERED_SCOPE_REQUIRED'):
                    self.run_migration()
                self.assertEqual(before, self.snapshot())
                self.assertEqual(dirs, {p.relative_to(self.scope).as_posix() for p in self.scope.rglob('*') if p.is_dir()})

    def test_changed_or_symlinked_migration_record_is_not_overwritten(self):
        for kind in ('changed', 'symlink'):
            with self.subTest(kind=kind):
                self.setUp();self.prepare();self.run_migration()
                path = self.record_path()
                if kind == 'changed':
                    path.write_bytes(b'{}')
                else:
                    path.unlink();path.symlink_to(self.root / 'unrelated')
                binding_before = (self.scope / 'scope.json').read_bytes()
                with self.assertRaises(op.tx.Conflict):
                    self.run_migration()
                self.assertEqual((self.scope / 'scope.json').read_bytes(), binding_before)
                if kind == 'changed':
                    self.assertEqual(path.read_bytes(), b'{}')
                else:
                    self.assertTrue(path.is_symlink())

    def test_scope_descriptor_is_rechecked_after_all_resource_locks_are_acquired(self):
        self.prepare();original = op.RuntimeLifecycle.quiescent_snapshot
        changed = b'{"user":"changed"}'
        @contextmanager
        def drift(manager, **kwargs):
            with original(manager, **kwargs) as observed:
                (self.scope / 'scope.json').write_bytes(changed)
                yield observed
        with mock.patch.object(op.RuntimeLifecycle, 'quiescent_snapshot', drift):
            with self.assertRaisesRegex(op.tx.Conflict, 'OFFLINE_SCOPE_BINDING_CHANGED'):
                self.run_migration()
        self.assertEqual((self.scope / 'scope.json').read_bytes(), changed)
        self.assertFalse(self.record_path().exists())

    def test_no_implicit_creation_same_bundle_or_invalid_request_fields(self):
        self.previous_pin = 'f' * 64;self.inventory = {'revision': 'a' * 64}
        with self.assertRaisesRegex(op.tx.Conflict, 'OFFLINE_SCOPE_NOT_FOUND'):
            self.run_migration()
        self.assertFalse(self.scope.exists())
        self.prepare();before = self.snapshot()
        for field, value in [('previous_bootstrap_manifest_sha256', self.next_pin),
                             ('previous_bootstrap_manifest_sha256', 'wrong'),
                             ('inventory_revision', True), ('interrupt_after', 'runtime'), ('program', 'anything')]:
            request = self.migrate_request();request[field] = value
            with self.subTest(field=field), self.assertRaises(op.tx.Conflict):
                self.run_migration(request)
        self.assertEqual(before, self.snapshot())

    def test_binding_can_be_explicitly_migrated_back_without_changing_resources(self):
        self.prepare();self.run_migration();before = self.snapshot()
        value = self.migrate_request();value['previous_bootstrap_manifest_sha256'] = self.next_pin
        self.write(value);result = op.run(self.old_bundle, self.request_file)
        self.assertEqual(result['bootstrap_manifest_sha256'], self.previous_pin)
        after = self.snapshot()
        self.assertEqual({k: v for k, v in before.items() if k != 'scope.json'},
                         {k: v for k, v in after.items() if k in before and k != 'scope.json'})
        self.assertEqual((self.scope / 'scope.json').read_bytes(), op.tx.encode(op.bindings(self.old_bundle, self.value)[0]))


if __name__ == '__main__':
    unittest.main()
