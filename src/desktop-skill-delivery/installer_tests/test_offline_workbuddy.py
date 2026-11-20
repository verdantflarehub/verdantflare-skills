"""Fixed offline entry uses the real WorkBuddy layout without claiming refresh."""
import json
import shutil
import stat
import unittest

import test_offline_operation as fixtures
import test_binding_migration as migration_fixtures

op = fixtures.op
sample_bytes = fixtures.sample_bytes


class WorkBuddyOfflineTests(unittest.TestCase):
    write = fixtures.OfflineOperationTests.write
    process = fixtures.OfflineOperationTests.process

    def setUp(self):
        fixtures.OfflineOperationTests.setUp(self)
        self.value.update(schema=op.WORKBUDDY_SCHEMA, host_profile='workbuddy')

    def run_action(self, action, **extra):
        value = {k: v for k, v in self.value.items() if k in op.common_fields(self.value)}
        value.update(action=action, **extra)
        self.write(value)
        return op.run(self.bundle, self.request_file)

    def engine(self):
        return op.make_engine(self.scope, self.value, 'mcp.json')

    def assert_pending_refresh(self, receipt):
        self.assertEqual(receipt['schema'], op.WORKBUDDY_SCHEMA)
        self.assertEqual(receipt['host_profile'], 'workbuddy')
        self.assertEqual(receipt['host_refresh'], 'refresh_required')
        for key in ('host_verified', 'desktop_acceptance', 'mcp_acceptance', 'installable',
                    'publisher_signature_verified', 'user_config_changed'):
            self.assertIs(receipt[key], False, key)

    def test_native_layout_update_and_uninstall_preserve_foreign_content(self):
        self.assert_pending_refresh(self.process(0))
        self.assertEqual(set(p.name for p in self.scope.iterdir()),
                         op.WORKBUDDY_CHILDREN | {'scope.json', 'scope.lock'})
        root = self.scope / 'workbuddy'
        self.assertFalse((root / 'skills' / op.tx.META).exists())
        self.assertTrue((self.scope / 'state/.vf-workbuddy-skill-files').is_dir())
        config = root / 'mcp.json'
        desired = json.loads(config.read_bytes())['mcpServers']['vf-studio']
        self.assertEqual(desired, {'url': op.WORKBUDDY_URL, 'timeout': 30000, 'disabled': False})
        # Preserve user changes outside our node through an actual new release.
        raw = config.read_bytes()
        # Our node is byte-owned; don't reserialize it when editing foreign data.
        config.write_bytes(b'{"theme":"dark",' + raw[1:]);config.chmod(0o640)
        canary = root / 'skills/user-skill/SKILL.md'
        canary.parent.mkdir();canary.write_bytes(b'# Foreign skill\n')
        self.sample.write_bytes(sample_bytes(release='engineering.2', changed=True))
        self.value['sample_sha256'] = op.tx.sha(self.sample.read_bytes())
        result = self.process(0)
        self.assert_pending_refresh(result)
        self.assertEqual(result['release'], 'engineering.2')
        for key in op.USER_SKILLS:
            self.assertEqual((root / 'skills' / key / 'SKILL.md').read_bytes(),
                             ('# ' + key + ' updated\n').encode())
        removed = self.run_action('uninstall', interrupt_after=None)
        self.assert_pending_refresh(removed)
        after = json.loads(config.read_bytes())
        self.assertEqual(after, {'theme': 'dark', 'mcpServers': {}})
        self.assertEqual(stat.S_IMODE(config.stat().st_mode), 0o640)
        self.assertEqual(canary.read_bytes(), b'# Foreign skill\n')
        self.assertEqual(removed['skill_files_removed'], 11)
        self.assert_pending_refresh(self.run_action('status'))

    def test_actual_process_exit_restores_three_resources_and_can_reinstall(self):
        self.value['interrupt_after'] = 'config'
        self.process(73)
        self.assertEqual(self.run_action('status')['operation_state'], 'config')
        recovered = self.run_action('recover')
        self.assert_pending_refresh(recovered)
        self.assertEqual(recovered['operation_state'], 'cleaned_aborted')
        self.assertFalse((self.scope / 'workbuddy/mcp.json').exists())
        self.assertFalse((self.scope / 'runtime/activation/runtime.json').exists())
        self.assertFalse((self.scope / 'workbuddy/skills/verdantflare-project/SKILL.md').exists())
        self.assert_pending_refresh(self.run_action('recover'))
        self.value['interrupt_after'] = None
        self.assert_pending_refresh(self.process(0))
        self.assert_pending_refresh(self.run_action('inventory'))

    def test_invalid_profile_format_or_fields_refused_without_scope_creation(self):
        original = dict(self.value)
        for update in ({'host_profile': 'generic'}, {'host_profile': []}, {'config_format': 'toml'},
                       {'url': 'https://real.invalid/mcp/desktop/not-admitted'},
                       {'host_refresh': 'ready'}, {'schema': op.SCHEMA}):
            self.value = {**original, **update}
            self.write()
            with self.subTest(update=update), self.assertRaises(op.tx.Conflict):
                op.run(self.bundle, self.request_file)
            self.assertFalse(self.scope.exists())
        self.value = dict(original);self.value.pop('host_profile');self.write()
        with self.assertRaisesRegex(op.tx.Conflict, 'OFFLINE_REQUEST_FIELDS'):
            op.run(self.bundle, self.request_file)
        self.assertFalse(self.scope.exists())

    def test_v1_and_v2_scopes_cannot_recover_each_other(self):
        self.process(0)
        binding = self.scope / 'scope.json'
        before = binding.read_bytes()
        self.assertEqual(json.loads(before)['schema'], op.WORKBUDDY_SCOPE)
        original = dict(self.value)
        self.value = {k: v for k, v in original.items() if k != 'host_profile'}
        self.value['schema'] = op.SCHEMA
        with self.assertRaisesRegex(op.tx.Conflict, 'OFFLINE_SCOPE_BINDING_MISMATCH'):
            self.run_action('recover')
        self.assertEqual(binding.read_bytes(), before)
        self.scope = self.root / 'generic-isolated';self.value['scope'] = str(self.scope)
        self.process(0)
        binding = self.scope / 'scope.json';before = binding.read_bytes()
        self.value.update(schema=op.WORKBUDDY_SCHEMA, host_profile='workbuddy')
        with self.assertRaisesRegex(op.tx.Conflict, 'OFFLINE_SCOPE_BINDING_MISMATCH'):
            self.run_action('recover')
        self.assertEqual(binding.read_bytes(), before)

    def test_missing_external_registration_is_not_recreated(self):
        self.process(0)
        metadata = self.scope / 'state/.vf-workbuddy-skill-files'
        saved = self.root / 'preserved-metadata';metadata.rename(saved)
        config = self.scope / 'workbuddy/mcp.json';before = config.read_bytes()
        for action in ('recover', 'inventory', 'uninstall', 'apply'):
            if action == 'apply':
                self.write()
                call = lambda: op.run(self.bundle, self.request_file)
            else:
                call = lambda: self.run_action(action, **({'interrupt_after': None} if action == 'uninstall' else {}))
            with self.subTest(action=action), self.assertRaisesRegex(op.tx.Conflict, 'OFFLINE_REGISTERED_SCOPE_REQUIRED'):
                call()
            self.assertFalse(metadata.exists())
            self.assertEqual(config.read_bytes(), before)
        shutil.move(saved, metadata)
        self.assert_pending_refresh(self.run_action('inventory'))

    def test_explicit_bundle_migration_retains_profile_and_does_not_apply_runtime(self):
        migration_fixtures.BindingMigrationTests.prepare(self)
        config = self.scope / 'workbuddy/mcp.json'
        before = config.read_bytes()
        with self.assertRaisesRegex(op.tx.Conflict, 'OFFLINE_SCOPE_BINDING_MISMATCH'):
            self.run_action('inventory')
        args = {'previous_bootstrap_manifest_sha256': self.previous_pin,
                'inventory_revision': self.inventory['revision'], 'interrupt_after': None}
        receipt = self.run_action('migrate-binding', **args)
        self.assert_pending_refresh(receipt)
        self.assertEqual(receipt['status'], 'offline_binding_migrated')
        binding = json.loads((self.scope / 'scope.json').read_bytes())
        self.assertEqual(binding['schema'], op.WORKBUDDY_SCOPE)
        self.assertEqual(binding['host_profile'], 'workbuddy')
        self.assertEqual(binding['bootstrap_manifest_sha256'], self.next_pin)
        self.assertEqual(config.read_bytes(), before)
        self.assertEqual(self.run_action('inventory')['inventory'], self.inventory)
        self.assertEqual(self.run_action('migrate-binding', **args)['status'], 'offline_binding_already_migrated')
        self.assert_pending_refresh(self.process(0))
        pointer = json.loads((self.scope / 'runtime/activation/runtime.json').read_bytes())
        self.assertEqual(pointer['tree_sha256'], self.next_tree_sha)


if __name__ == '__main__':
    unittest.main()
