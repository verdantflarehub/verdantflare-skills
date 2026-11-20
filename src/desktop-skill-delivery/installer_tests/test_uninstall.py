"""Owned-resource removal, process crashes and GC in isolated data scopes."""
from pathlib import Path
import json
import sys
import unittest
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_offline_operation as fixture

op = fixture.op


class UninstallTests(unittest.TestCase):
    # Reuse fixture helpers without re-running the base class's test methods.
    setUp = fixture.OfflineOperationTests.setUp
    write = fixture.OfflineOperationTests.write
    process = fixture.OfflineOperationTests.process
    run_action = fixture.OfflineOperationTests.run_action
    engine = fixture.OfflineOperationTests.engine
    lifecycle = fixture.OfflineOperationTests.lifecycle
    collect = fixture.OfflineOperationTests.collect

    def installed(self, fmt='json', suffix='default'):
        self.scope = self.root / (fmt + '-' + suffix)
        self.value.update(action='apply', scope=str(self.scope), scope_id=uuid.uuid4().hex,
                          config_format=fmt, sample=str(self.sample),
                          sample_sha256=op.tx.sha(self.sample.read_bytes()), interrupt_after=None)
        self.process(0)
        engine = self.engine()
        raw = engine.config.path.read_bytes()
        if fmt == 'json':
            # Preserve the owned node's exact bytes, including its JSON layout.
            import host_config
            parser, _, container, _, owned = host_config.inspect(raw)
            at = container.value.start + 1
            text = parser.text[:at] + '"unrelated":{"url":"https://unrelated.invalid/mcp"},' + parser.text[at:]
            raw = ('{"theme":"dark",' + text[1:]).encode()
            self.assertEqual(host_config.inspect(raw)[-1], owned)
        else:
            import host_toml
            owned = host_toml.inspect(raw)
            raw = b'# user connection comment\ntheme = "dark"\n[mcp_servers.unrelated]\nurl = "https://unrelated.invalid/mcp"\n' + raw
            self.assertEqual(host_toml.inspect(raw), owned)
        engine.config.path.write_bytes(raw)
        engine.config.path.chmod(0o640)
        self.process(0)  # Record this exact configuration as the current prestate.
        self.original_apply = dict(self.value)
        self.value = {k: v for k, v in self.value.items() if k in op.COMMON}
        self.value.update(action='uninstall', interrupt_after=None)
        return self.engine()

    def state(self):
        engine = self.engine()
        ledger = engine.skills.snapshot()[0]
        return (engine.skills.snapshot(),
                {name: (op.tx.read(engine.skills.root / name),
                        (engine.skills.root / name).stat().st_mode & 0o777)
                 for name in ledger['files']},
                engine.config.snapshot(), engine.config._file(),
                op.tx.read(engine.runtime.root / 'runtime.json'), engine.runtime.snapshot())

    def removed(self):
        engine = self.engine()
        self.assertEqual(engine.skills.snapshot()[0], op.tx.empty_ledger())
        self.assertEqual(engine.runtime.snapshot()[0], op.tx.empty_ledger())
        self.assertFalse((engine.runtime.root / 'runtime.json').exists())
        self.assertIsNone(engine.config.snapshot()[0]['owned_sha256'])
        self.assertEqual(engine.config.path.stat().st_mode & 0o777, 0o640)
        raw = engine.config.path.read_bytes()
        self.assertIn(b'https://unrelated.invalid/mcp', raw)
        self.assertNotIn(b'vf-studio', raw)
        if self.value['config_format'] == 'json':
            self.assertEqual(json.loads(raw)['theme'], 'dark')
        else:
            self.assertIn(b'# user connection comment', raw)
        self.assertEqual(engine._active()['state'], 'cleaned')

    def test_json_toml_remove_preserves_unknown_files_and_config(self):
        for fmt in ('json', 'toml'):
            with self.subTest(format=fmt):
                engine = self.installed(fmt)
                names = list(engine.skills.snapshot()[0]['files'])
                extra = engine.skills.root / names[0].split('/')[0] / 'user.txt'
                extra.write_bytes(b'user material');extra.chmod(0o644)
                other = engine.skills.root / 'my-skill';other.mkdir()
                (other / 'SKILL.md').write_bytes(b'personal skill')
                seal = engine.runtime_store.root / 'lifecycle/seal.json'
                sealed = seal.read_bytes()
                result = self.process(0)
                self.assertEqual(result['status'], 'offline_resources_removed')
                self.assertEqual(result['skill_files_removed'], 11)
                self.assertFalse(result['uninstall_acceptance'])
                self.assertFalse(result['runtime_trees_collected'])
                self.removed()
                self.assertTrue(all(not (engine.skills.root / name).exists() for name in names))
                self.assertEqual(extra.read_bytes(), b'user material')
                self.assertEqual((other / 'SKILL.md').read_bytes(), b'personal skill')
                self.assertEqual(seal.read_bytes(), sealed)
                engine.runtime_store.verify(self.tree_sha)

    def test_each_process_boundary_restores_prestate_or_preserves_commit(self):
        for fmt in ('json', 'toml'):
            for phase in ('runtime', 'skills', 'config', 'committed'):
                with self.subTest(format=fmt, phase=phase):
                    self.installed(fmt, phase)
                    before = self.state()
                    self.value['interrupt_after'] = phase
                    self.process(73)
                    self.assertEqual(self.run_action('status')['operation_state'], phase)
                    self.run_action('recover');self.run_action('recover')
                    if phase == 'committed':
                        self.removed()
                    else:
                        self.assertEqual(self.state(), before)
                        self.assertEqual(self.engine()._active()['state'], 'cleaned_aborted')

    def test_repeated_remove_and_original_release_reinstall(self):
        for fmt in ('json', 'toml'):
            with self.subTest(format=fmt):
                self.installed(fmt)
                self.process(0);before = self.state()
                result = self.process(0)
                self.assertEqual(result['skill_files_removed'], 0)
                self.assertEqual(self.state(), before)
                self.removed()
                uninstall = dict(self.value)
                self.value = self.original_apply;self.process(0)
                self.assertEqual(len(self.engine().skills.snapshot()[0]['files']), 11)
                self.assertTrue((self.engine().runtime.root / 'runtime.json').is_file())
                self.value = uninstall;self.process(0);self.removed()

    def test_live_lease_blocks_remove_without_resource_changes(self):
        self.installed();before = self.state()
        with self.lifecycle().lease(self.tree_sha):
            self.write()
            with self.assertRaisesRegex(op.tx.Conflict, 'RUNTIME_ACTIVITY_BUSY'):
                op.run(self.bundle, self.request_file)
            self.assertEqual(self.state(), before)

    def test_user_modified_skill_or_mode_is_preserved(self):
        for change in ('bytes', 'mode'):
            with self.subTest(change=change):
                engine = self.installed(suffix=change)
                name = next(iter(engine.skills.snapshot()[0]['files']))
                path = engine.skills.root / name
                if change == 'bytes':
                    path.write_bytes(b'user revision')
                else:
                    path.chmod(0o755)
                before = self.state();self.write()
                with self.assertRaises(op.tx.Conflict):
                    op.run(self.bundle, self.request_file)
                self.assertEqual(self.state(), before)

    def test_user_changed_vf_connection_preserved_and_other_resources_restored(self):
        engine = self.installed()
        raw = engine.config.path.read_bytes().replace(b'vf-offline-operation.invalid', b'user-edited.invalid')
        engine.config.path.write_bytes(raw)
        before = self.state();self.write()
        with self.assertRaises(Exception) as raised:
            op.run(self.bundle, self.request_file)
        self.assertEqual(type(raised.exception).__name__, 'ConfigConflict')
        self.assertEqual(self.state(), before)
        self.assertEqual(engine._active()['state'], 'aborted')

    def test_stale_cas_refused_before_resource_changes(self):
        engine = self.installed();observed = engine.config.observe();before = self.state()
        for kind in ('skills', 'config', 'runtime'):
            kwargs = dict(expected_skill_revision=engine.skills.snapshot()[1],
                          expected_config_revision=observed['ledger_revision'], expected_config_file=observed['file'],
                          expected_runtime_revision=engine.runtime.snapshot()[1])
            kwargs[{'skills': 'expected_skill_revision', 'config': 'expected_config_revision',
                    'runtime': 'expected_runtime_revision'}[kind]] = '0' * 64
            with self.subTest(kind=kind), self.assertRaisesRegex(op.tx.Conflict, 'PREFLIGHT_CHANGED'):
                self.lifecycle().uninstall(next(iter(self.lifecycle().scopes)), **kwargs)
            self.assertEqual(self.state(), before)

    def test_missing_scope_and_registration_are_not_created_or_repaired(self):
        self.value = {k: v for k, v in self.value.items() if k in op.COMMON}
        self.value.update(action='uninstall', interrupt_after=None);self.write()
        with self.assertRaisesRegex(op.tx.Conflict, 'OFFLINE_SCOPE_NOT_FOUND'):
            op.run(self.bundle, self.request_file)
        self.assertFalse(self.scope.exists())
        engine = self.installed();before = self.state()
        folder = engine.runtime_store.root / 'lifecycle/quarantine';folder.rmdir();self.write()
        with self.assertRaisesRegex(op.tx.Conflict, 'OFFLINE_REGISTERED_SCOPE_REQUIRED'):
            op.run(self.bundle, self.request_file)
        self.assertFalse(folder.exists())
        self.assertEqual(self.state(), before)

    def test_unknown_uninstall_fields_and_invalid_boundary_refused(self):
        self.installed();before = self.state()
        for extra in ({'sample': str(self.sample)}, {'interrupt_after': True}, {'interrupt_after': 'delete-root'}):
            value = {**self.value, **extra};self.write(value)
            with self.subTest(extra=list(extra)), self.assertRaises(op.tx.Conflict):
                op.run(self.bundle, self.request_file)
            self.assertEqual(self.state(), before)

    def test_removal_schema_requires_null_tree_and_apply_still_requires_tree(self):
        engine = self.installed();before = self.state();observed = engine.config.observe()
        with self.assertRaisesRegex(op.tx.Conflict, 'RUNTIME_MODE_MISMATCH'):
            engine.apply('install.1', {}, None, expected_skill_revision=engine.skills.snapshot()[1],
                         expected_config_revision=observed['ledger_revision'], expected_config_file=observed['file'])
        self.assertEqual(self.state(), before)
        self.process(0)
        journal = engine._active();self.assertIsNone(journal['runtime']['tree'])
        path = engine.meta / (journal['id'] + '.json');original = path.read_bytes()
        for schema, tree in ((op.InstallOperation.__module__, None),
                             ('vf.runtime-skill-config.operation/v1', None),
                             ('vf.runtime-skill-config.removal/v1', self.tree_sha)):
            changed = {**journal, 'schema': schema, 'runtime': {**journal['runtime'], 'tree': tree}}
            path.write_bytes(op.tx.encode(changed))
            with self.assertRaises(op.tx.Conflict):
                engine._active()
        path.write_bytes(original)
        self.removed()

    def test_unfinished_remove_protects_tree_and_completed_remove_allows_gc(self):
        self.installed();self.value['interrupt_after'] = 'skills';self.process(73)
        inventory = self.run_action('inventory')['inventory']
        self.assertTrue(inventory['versions'][self.tree_sha]['references'])
        with self.assertRaises(op.tx.Conflict):
            self.collect(inventory)
        self.run_action('recover');self.run_action('recover')
        self.value['interrupt_after'] = None;self.process(0)
        inventory = self.run_action('inventory')['inventory']
        self.assertFalse(inventory['versions'][self.tree_sha]['references'])
        result = self.collect(inventory)
        self.assertEqual(result['maintenance']['status'], 'runtime_collected')
        self.assertEqual(self.run_action('inventory')['inventory']['versions'], {})
        self.removed()
        self.value = self.original_apply;self.process(0)
        self.assertEqual(len(self.engine().skills.snapshot()[0]['files']), 11)

    def test_external_recreation_after_removal_blocks_recovery_without_overwrite(self):
        for fmt in ('json', 'toml'):
            for kind in ('skills', 'runtime'):
                with self.subTest(format=fmt, kind=kind):
                    engine = self.installed(fmt, 'recreated-' + kind)
                    before = self.state()
                    self.value['interrupt_after'] = 'config';self.process(73)
                    if kind == 'runtime':
                        path = engine.runtime.root / 'runtime.json'
                    else:
                        path = engine.skills.root / next(iter(before[0][0]['files']))
                    path.write_bytes(b'new user content after removal');path.chmod(0o644)
                    with self.assertRaises(op.tx.Conflict):
                        self.run_action('recover')
                    self.assertEqual(path.read_bytes(), b'new user content after removal')
                    # Only the test fixture's own added content is removed here.
                    path.unlink()
                    self.run_action('recover');self.run_action('recover')
                    self.assertEqual(self.state(), before)


if __name__ == '__main__':
    unittest.main()
