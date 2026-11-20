"""Exercise offline scope admission and real subprocess interruption/recovery."""
from pathlib import Path
import io
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
import uuid
import zipfile

TOOLS = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(TOOLS), str(TOOLS / 'installer_tools')]
import offline_operation as op

BASE = TOOLS.parents[2] / '.tmp/offline-operation-tests'


def zip_bytes(files):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w') as package:
        for name, raw in files.items():
            info = zipfile.ZipInfo(name);info.create_system = 3
            info.external_attr = (stat.S_IFREG | 0o644) << 16
            package.writestr(info, raw)
    return stream.getvalue()


def sample_bytes(*, mutate=lambda _: None, release='engineering.1', changed=False):
    files, packages = {}, []
    for key in sorted(op.USER_SKILLS):
        name = key + '/SKILL.md';data = ('# ' + key + (' updated' if changed else '') + '\n').encode()
        content = zip_bytes({name: data})
        listing = op.tx.encode({'skill_id': key, 'files': [{'path': name, 'size': len(data), 'sha256': op.tx.sha(data)}]})
        artifact = {'path': 'packages/' + key + '.zip', 'size': len(content), 'sha256': op.tx.sha(content),
                    'file_manifest': 'manifests/' + key + '.json', 'file_manifest_sha256': op.tx.sha(listing)}
        files['candidate/' + artifact['path']] = content
        files['candidate/' + artifact['file_manifest']] = listing
        packages.append({'id': key, 'requires': [], 'artifact': artifact})
    manifest = {'schema_version': 'vf.skills.handoff/v2', 'release_id': release,
                'installable': False, 'public_distribution': False, 'packages': packages}
    mutate(manifest)
    files['candidate/release-manifest.json'] = op.tx.encode(manifest)
    return zip_bytes(files)


class OfflineOperationTests(unittest.TestCase):
    def setUp(self):
        BASE.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=BASE);self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).absolute();self.root.chmod(0o700)
        self.bundle = self.root / 'bundle';self.bundle.mkdir(mode=0o700)
        data = b'DATA_ONLY_RUNTIME_NEVER_EXECUTED\n'
        entry = {'type': 'file', 'size': len(data), 'sha256': op.tx.sha(data), 'mode': 0o644}
        tree = {'schema': op.store.SCHEMA, 'release': 'runtime.fixture', 'source_sha256': '0' * 64,
                'entries': {'fixture.dat': entry}}
        self.tree_sha = op.tx.sha(op.tx.encode(tree))
        values = {'resources/runtime/fixture.dat': data,
                  'resources/provenance/runtime-tree-manifest.json': op.tx.encode(tree),
                  'resources/provenance/source-lock.json': op.tx.encode({'runtime_tree_sha256': self.tree_sha, 'platform': 'macos-arm64'}),
                  'bootstrap-manifest.json': op.tx.encode({'entries': {'resources/runtime/fixture.dat': entry}})}
        for name, raw in values.items():
            p = self.bundle / name;p.parent.mkdir(parents=True, mode=0o700, exist_ok=True);p.write_bytes(raw);p.chmod(0o644)
        self.sample = self.root / 'sample.zip';self.sample.write_bytes(sample_bytes());self.sample.chmod(0o644)
        self.scope = self.root / 'isolated';self.request_file = self.root / 'request.json'
        self.value = {'schema': op.SCHEMA, 'action': 'apply', 'scope': str(self.scope),
                      'scope_id': uuid.uuid4().hex, 'config_format': 'json', 'sample': str(self.sample),
                      'sample_sha256': op.tx.sha(self.sample.read_bytes()), 'interrupt_after': None}

    def write(self, value=None):
        self.request_file.write_bytes(op.tx.encode(value or self.value));self.request_file.chmod(0o600)

    def run_action(self, action):
        value = {k: v for k, v in self.value.items() if k in op.COMMON}
        value['action'] = action;self.write(value)
        return op.run(self.bundle, self.request_file)

    def collect(self, inventory, digest=None):
        value = {k: v for k, v in self.value.items() if k in op.COMMON}
        value.update(action='collect', runtime_tree_sha256=digest or self.tree_sha,
                     inventory_revision=inventory['revision'])
        self.write(value)
        return op.run(self.bundle, self.request_file)

    def lifecycle(self):
        engine = self.engine()
        return op.RuntimeLifecycle(engine.runtime_store, [engine])

    def test_live_runtime_lease_blocks_native_apply_recover_inventory_and_gc(self):
        self.process(0)
        manager = self.lifecycle()
        inventory = self.run_action('inventory')['inventory']
        with manager.lease(self.tree_sha):
            self.write()
            with self.assertRaisesRegex(op.tx.Conflict, 'RUNTIME_ACTIVITY_BUSY'):
                op.run(self.bundle, self.request_file)
            for action in ('recover', 'inventory', 'recover-gc'):
                with self.subTest(action=action), self.assertRaisesRegex(op.tx.Conflict, 'RUNTIME_ACTIVITY_BUSY'):
                    self.run_action(action)
            with self.assertRaisesRegex(op.tx.Conflict, 'RUNTIME_ACTIVITY_BUSY'):
                self.collect(inventory)
            self.assertEqual(self.run_action('status')['operation_state'], 'cleaned')
        self.assertEqual(self.run_action('inventory')['inventory']['revision'], inventory['revision'])
        with self.assertRaisesRegex(op.tx.Conflict, 'RUNTIME_VERSION_PROTECTED'):
            self.collect(inventory)

    def test_aborted_target_is_protected_but_unused_staged_tree_can_be_collected(self):
        self.value['interrupt_after'] = 'config';self.process(73)
        self.run_action('recover')
        inventory = self.run_action('inventory')['inventory']
        self.assertTrue(inventory['versions'][self.tree_sha]['references'])
        with self.assertRaisesRegex(op.tx.Conflict, 'RUNTIME_VERSION_PROTECTED'):
            self.collect(inventory)
        data = b'UNUSED_DATA_ONLY_TREE_NEVER_EXECUTE\n'
        unused = {'schema': op.store.SCHEMA, 'release': 'unused.fixture', 'source_sha256': '0' * 64,
                  'entries': {'unused.dat': {'type': 'file', 'mode': 0o644, 'size': len(data),
                                             'sha256': op.tx.sha(data)}}}
        source = self.root / 'unused.dat';source.write_bytes(data);source.chmod(0o644)
        digest = op.tx.sha(op.tx.encode(unused))
        self.lifecycle().stage(unused, {'unused.dat': source}, expected_digest=digest,
                               transaction_id=uuid.uuid4().hex)
        inventory = self.run_action('inventory')['inventory']
        stale = dict(inventory, revision='0' * 64)
        with self.assertRaisesRegex(op.tx.Conflict, 'LIFECYCLE_REVISION_CHANGED'):
            self.collect(stale, digest)
        result = self.collect(inventory, digest)
        self.assertEqual(result['maintenance']['status'], 'runtime_collected')
        self.assertEqual(set(self.run_action('inventory')['inventory']['versions']), {self.tree_sha})
        self.assertEqual(self.run_action('recover-gc')['maintenance']['status'], 'nothing_pending')
        self.value['interrupt_after'] = None;self.process(0)
        self.assertTrue(self.run_action('inventory')['inventory']['versions'][self.tree_sha]['references'])

    def test_legacy_scope_and_invalid_maintenance_requests_are_preserved(self):
        self.process(0)
        binding = self.scope / 'scope.json';before = binding.read_bytes()
        legacy = op.tx.decode(before);legacy['schema'] = 'vf.bootstrap-offline-scope/v1'
        legacy_raw = op.tx.encode(legacy);binding.write_bytes(legacy_raw)
        with self.assertRaisesRegex(op.tx.Conflict, 'OFFLINE_SCOPE_BINDING_MISMATCH'):
            self.run_action('inventory')
        self.assertEqual(binding.read_bytes(), legacy_raw)
        binding.write_bytes(before)
        for action, extra in [('collect', {}), ('inventory', {'runtime_tree_sha256': self.tree_sha}),
                              ('collect', {'runtime_tree_sha256': self.tree_sha, 'inventory_revision': True})]:
            value = {k: v for k, v in self.value.items() if k in op.COMMON}
            value.update(action=action, **extra);self.write(value)
            with self.subTest(action=action), self.assertRaises(op.tx.Conflict):
                op.run(self.bundle, self.request_file)

    def test_interrupted_gc_request_resumes_original_record_and_preserves_live_tree(self):
        self.process(0)
        manager = self.lifecycle()
        raw = b'UNUSED_FOR_INTERRUPTED_GC\n'
        source = self.root / 'unused.dat';source.write_bytes(raw);source.chmod(0o644)
        tree = {'schema': op.store.SCHEMA, 'release': 'unused.fixture', 'source_sha256': '0' * 64,
                'entries': {'unused.dat': {'type': 'file', 'mode': 0o644, 'size': len(raw),
                                           'sha256': op.tx.sha(raw)}}}
        digest = op.tx.sha(op.tx.encode(tree))
        manager.stage(tree, {'unused.dat': source}, expected_digest=digest, transaction_id=uuid.uuid4().hex)
        inventory = manager.inventory()
        class Interrupted(BaseException):
            pass
        def stop(phase):
            if phase == 'quarantined':
                raise Interrupted()
        with self.assertRaises(Interrupted):
            manager.collect(digest, expected_revision=inventory['revision'], checkpoint=stop)
        with self.assertRaisesRegex(op.tx.Conflict, 'GC_RECOVERY_REQUIRED'):
            self.run_action('inventory')
        result = self.run_action('recover-gc')
        self.assertEqual(result['maintenance']['tree_sha256'], digest)
        self.assertEqual(result['maintenance']['status'], 'runtime_collected')
        self.assertEqual(set(self.run_action('inventory')['inventory']['versions']), {self.tree_sha})
        self.assertEqual(self.run_action('recover-gc')['maintenance']['status'], 'nothing_pending')

    def process(self, expected):
        self.write()
        code = ('import sys,json;sys.path[:0]=' + repr([str(TOOLS), str(TOOLS / 'installer_tools'), *[p for p in sys.path if 'host-config-dependencies/site' in p]])
                + ';import offline_operation as o;from pathlib import Path;print(json.dumps(o.run(Path(sys.argv[1]),Path(sys.argv[2]))))')
        cp = subprocess.run([sys.executable, '-B', '-c', code, str(self.bundle), str(self.request_file)],
                            capture_output=True, text=True, timeout=30)
        self.assertEqual(cp.returncode, expected, cp.stderr.replace(str(Path.home()), '%USERPROFILE%'))
        return json.loads(cp.stdout) if expected == 0 else None

    def engine(self):
        fmt = self.value['config_format']
        return op.InstallOperation(*(self.scope / n for n in ('state','skills','config')),
                                   'config.toml' if fmt == 'toml' else 'mcp.json',
                                   runtime_root=self.scope / 'runtime', config_format=fmt)

    def test_apply_new_candidate_and_same_version_reinstall(self):
        result = self.process(0)
        self.assertEqual((result['skill_packages'], result['skill_files']), (11, 11))
        self.assertFalse(result['desktop_acceptance']);self.assertFalse(result['installable'])
        for key in op.USER_SKILLS:
            self.assertEqual((self.scope / 'skills' / key / 'SKILL.md').read_bytes(), ('# ' + key + '\n').encode())
        self.process(0)
        engine = self.engine();self.assertEqual(engine._active()['state'], 'cleaned')
        self.assertEqual(op.tx.decode((engine.runtime.root / 'runtime.json').read_bytes())['tree_sha256'], self.tree_sha)

    def test_actual_process_exit_restores_all_three_resources_or_keeps_commit(self):
        for fmt in ('json', 'toml'):
            for phase in ('runtime', 'skills', 'config', 'committed'):
                with self.subTest(format=fmt, phase=phase):
                    self.scope = self.root / (fmt + '-' + phase)
                    self.value.update(scope=str(self.scope), scope_id=uuid.uuid4().hex, config_format=fmt, interrupt_after=phase)
                    self.process(73)
                    self.assertEqual(self.run_action('status')['operation_state'], phase)
                    self.run_action('recover');self.run_action('recover')
                    engine = self.engine()
                    if phase == 'committed':
                        self.assertEqual(engine._active()['state'], 'cleaned')
                        self.assertTrue(engine.skills.snapshot()[0]['files'])
                    else:
                        self.assertEqual(engine._active()['state'], 'cleaned_aborted')
                        self.assertEqual(engine.skills.snapshot()[0]['files'], {})
                        self.assertFalse(engine.config.path.exists())
                        self.assertFalse((engine.runtime.root / 'runtime.json').exists())
                    self.value['interrupt_after'] = None;self.process(0)

    def test_bad_pins_dependencies_or_metadata_refuse_before_new_scope(self):
        self.value['sample_sha256'] = '0' * 64;self.write()
        with self.assertRaises(op.tx.Conflict):op.run(self.bundle, self.request_file)
        self.assertFalse(self.scope.exists())
        for mutate in (lambda m: m['packages'][0].update(requires=['unknown']),
                       lambda m: m['packages'][0].update(requires=[m['packages'][0]['id']]),
                       lambda m: m.update(installable=True)):
            self.sample.write_bytes(sample_bytes(mutate=mutate));self.value['sample_sha256'] = op.tx.sha(self.sample.read_bytes());self.write()
            with self.assertRaises(op.tx.Conflict):op.run(self.bundle, self.request_file)
            self.assertFalse(self.scope.exists())
        self.sample.chmod(0o666)
        with self.assertRaises(op.tx.Conflict):op.run(self.bundle, self.request_file)
        self.assertFalse(self.scope.exists())

    def test_existing_unmanaged_scope_and_unknown_contents_are_preserved(self):
        self.scope.mkdir();sentinel = self.scope / 'keep';sentinel.write_bytes(b'keep')
        self.write()
        with self.assertRaisesRegex(op.tx.Conflict, 'OFFLINE_SCOPE_NOT_MANAGED'):op.run(self.bundle, self.request_file)
        self.assertEqual(sentinel.read_bytes(), b'keep');self.assertEqual(list(self.scope.iterdir()), [sentinel])

    def test_binding_mismatch_and_foreign_file_drift_refuse(self):
        self.process(0)
        self.value['config_format'] = 'toml';self.write()
        with self.assertRaisesRegex(op.tx.Conflict, 'OFFLINE_SCOPE_BINDING_MISMATCH'):op.run(self.bundle, self.request_file)
        self.value['config_format'] = 'json'
        target = self.scope / 'skills/verdantflare-project/SKILL.md';target.write_bytes(b'USER CHANGE\n')
        self.write()
        with self.assertRaises(op.tx.Conflict):op.run(self.bundle, self.request_file)
        self.assertEqual(target.read_bytes(), b'USER CHANGE\n')

    def test_different_candidate_update_preserves_nonvf_config_and_modes(self):
        self.process(0)
        path = self.scope / 'config/mcp.json'
        path.write_bytes(path.read_bytes().replace(b'{', b'{\"theme\":\"dark\",', 1));path.chmod(0o640)
        self.sample.write_bytes(sample_bytes(release='engineering.2', changed=True))
        self.value['sample_sha256'] = op.tx.sha(self.sample.read_bytes());self.write()
        result = op.run(self.bundle, self.request_file)
        self.assertEqual(result['release'], 'engineering.2')
        self.assertEqual(json.loads(path.read_bytes())['theme'], 'dark')
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o640)
        self.assertIn(b'updated', (self.scope / 'skills/verdantflare-project/SKILL.md').read_bytes())

    def test_busy_scope_and_unknown_owned_root_contents_refuse_without_overwrite(self):
        self.process(0)
        import fcntl
        lock = self.scope / 'scope.lock'
        with lock.open('r+b') as stream:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.write()
            with self.assertRaisesRegex(op.tx.Conflict, 'OFFLINE_SCOPE_BUSY'):
                op.run(self.bundle, self.request_file)
        sentinel = self.scope / 'unknown-user-file';sentinel.write_bytes(b'keep')
        with self.assertRaisesRegex(op.tx.Conflict, 'OFFLINE_SCOPE_CONTENT_CHANGED'):
            self.run_action('status')
        self.assertEqual(sentinel.read_bytes(), b'keep')

    def test_duplicate_request_key_and_unknown_fields_refuse_before_scope(self):
        self.write()
        original = self.request_file.read_bytes()
        self.request_file.write_bytes(original.replace(b'{', b'{"action":"status",', 1))
        with self.assertRaisesRegex(op.tx.Conflict, 'DUPLICATE_JSON_KEY'):
            op.run(self.bundle, self.request_file)
        self.assertFalse(self.scope.exists())
        self.value['program'] = '/never/execute';self.write()
        with self.assertRaisesRegex(op.tx.Conflict, 'OFFLINE_REQUEST_FIELDS'):
            op.run(self.bundle, self.request_file)
        self.assertFalse(self.scope.exists())

    def test_status_and_request_validation_are_readonly(self):
        with self.assertRaisesRegex(op.tx.Conflict, 'OFFLINE_SCOPE_NOT_FOUND'):self.run_action('status')
        self.assertFalse(self.scope.exists())
        for field, value in [('scope','relative'),('scope_id','0'),('config_format',None),('interrupt_after','unknown')]:
            original = dict(self.value);self.value[field] = value;self.write()
            with self.assertRaises(op.tx.Conflict):op.run(self.bundle, self.request_file)
            self.assertFalse(self.scope.exists());self.value = original
        self.process(0)
        def snapshot():
            return {p.relative_to(self.scope).as_posix(): (p.read_bytes(),p.stat().st_mode) for p in self.scope.rglob('*') if p.is_file()}
        before = snapshot();self.assertEqual(self.run_action('status')['operation_state'], 'cleaned')
        self.assertEqual(before, snapshot())


if __name__ == '__main__':
    unittest.main()
