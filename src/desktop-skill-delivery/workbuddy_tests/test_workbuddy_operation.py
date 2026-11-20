"""WorkBuddy profile through the original coordinator and lifecycle locks."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import uuid

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))
from install_operation import InstallOperation
import install_transaction as tx
import host_workbuddy as w
from host_config import ConfigConflict
from runtime_tools.runtime_lifecycle import RuntimeLifecycle
from runtime_tools.runtime_store import SCHEMA

URL = 'https://studio.example.invalid/mcp/desktop/connection_1'
NEXT_URL = 'https://studio.example.invalid/mcp/desktop/connection_2'
BASE = TOOLS.parents[2] / '.tmp/workbuddy-operation-tests'


class Crash(BaseException):
    pass


@unittest.skipUnless(os.name == 'posix', 'Windows storage is a separate plan')
class WorkBuddyOperationTests(unittest.TestCase):
    def setUp(self):
        BASE.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=BASE)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).absolute()
        for name in ('state', 'skills', 'config', 'runtime', 'sources'):
            (self.root / name).mkdir(mode=0o700)
        self.engine = self.create()
        self.original = b'{ "theme":"dark", "mcpServers":{"foreign":{"command":"keep"}} }\r\n'
        self.engine.config.path.write_bytes(self.original)
        self.engine.config.path.chmod(0o640)
        self.manager = RuntimeLifecycle(self.engine.runtime_store, [self.engine])
        self.manager.initialize()
        self.key = next(iter(self.manager.scopes))
        self.trees = {}
        for version in ('1', '2'):
            raw = ('DATA_ONLY_NEVER_EXECUTE_' + version).encode()
            source = self.root / 'sources' / version
            source.write_bytes(raw)
            manifest = {'schema': SCHEMA, 'release': 'fixture.' + version,
                        'source_sha256': tx.sha(raw), 'entries': {'fixture.dat':
                        {'type': 'file', 'sha256': tx.sha(raw), 'size': len(raw), 'mode': 0o644}}}
            digest = tx.sha(tx.encode(manifest))
            self.manager.stage(manifest, {'fixture.dat': source}, expected_digest=digest,
                               transaction_id=uuid.uuid4().hex)
            self.trees[version] = digest

    def create(self, url=URL, **options):
        return InstallOperation(*(self.root / n for n in ('state', 'skills', 'config')),
                                 'mcp.json', runtime_root=self.root / 'runtime',
                                 config_profile='workbuddy', admitted_url=url, **options)

    def state(self):
        e = self.engine
        return (e.runtime.snapshot(), e.skills.snapshot(), e.config.snapshot(), e.config._file(),
                tx.read(e.runtime.root / 'runtime.json'), tx.read(e.skills.root / 'example/SKILL.md'))

    def apply(self, version='1', value=None, **options):
        e = self.engine
        observed = e.config.observe()
        return self.manager.apply(self.key, 'skills.' + version,
            {'example/SKILL.md': ('fixture-' + version).encode()},
            w.desired_connection(URL, admitted_url=URL) if value is None else value,
            expected_skill_revision=e.skills.snapshot()[1],
            expected_config_revision=observed['ledger_revision'], expected_config_file=observed['file'],
            runtime_digest=self.trees[version], expected_runtime_revision=e.runtime.snapshot()[1], **options)

    def settle(self):
        for _ in range(3):
            self.manager.recover_operation(self.key)

    @staticmethod
    def fail(point):
        def interrupt(phase):
            if phase == point:
                raise Crash()
        return interrupt

    def test_original_exact_type_lifecycle_install_update_and_uninstall(self):
        self.assertIs(type(self.engine), InstallOperation)
        phases = []
        self.assertEqual(self.apply(checkpoint=phases.append)['status'], 'resources_applied')
        self.assertLess(phases.index('runtime'), phases.index('skills'))
        self.assertLess(phases.index('skills'), phases.index('config'))
        self.settle()
        self.apply('2')
        self.settle()
        self.assertEqual(json.loads(self.engine.config.path.read_bytes())['mcpServers']['vf-studio'],
                         w.desired_connection(URL, admitted_url=URL))
        observed = self.engine.config.observe()
        removed = self.manager.uninstall(self.key,
            expected_skill_revision=self.engine.skills.snapshot()[1],
            expected_config_revision=observed['ledger_revision'], expected_config_file=observed['file'],
            expected_runtime_revision=self.engine.runtime.snapshot()[1])
        self.settle()
        self.assertEqual(removed['status'], 'resources_removed')
        self.assertEqual(self.engine.config.path.read_bytes(), self.original)
        self.assertEqual(self.engine.config.path.stat().st_mode & 0o777, 0o640)
        self.assertIsNone(tx.read(self.engine.runtime.root / 'runtime.json'))
        self.assertIsNone(tx.read(self.engine.skills.root / 'example/SKILL.md'))

    def test_recovery_state_is_outside_discovery_tree(self):
        self.apply()
        self.assertEqual(self.engine.skills.meta, self.root / 'state/.vf-workbuddy-skill-files')
        self.assertFalse((self.root / 'skills' / tx.META).exists())
        discovered_files = {p.relative_to(self.root / 'skills').as_posix()
                            for p in (self.root / 'skills').rglob('*') if p.is_file()}
        self.assertEqual(discovered_files, {'example/SKILL.md'})
        self.assertTrue(list(self.engine.skills.meta.glob('*/after/example/SKILL.md')))

    def test_legacy_internal_state_is_preserved_and_never_silently_migrated(self):
        legacy = self.root / 'skills' / tx.META
        legacy.mkdir(mode=0o700)
        marker = legacy / 'keep.json'
        marker.write_bytes(b'{"engineering_state":"keep"}')
        with self.assertRaisesRegex(tx.Conflict, 'WORKBUDDY_LEGACY_SKILL_STATE_PRESENT'):
            self.create()
        self.assertEqual(marker.read_bytes(), b'{"engineering_state":"keep"}')

    def test_wrong_connection_and_extra_fields_refused_before_any_child_activation(self):
        before = self.state()
        for value in [w.desired_connection(NEXT_URL, admitted_url=NEXT_URL),
                      {**w.desired_connection(URL, admitted_url=URL), 'command': 'fixture'},
                      {**w.desired_connection(URL, admitted_url=URL), 'headers': {}}]:
            with self.assertRaises(w.Conflict):
                self.apply(value=value)
            self.assertEqual(self.state(), before)
            self.assertIsNone(self.engine._active())

    def test_profile_binding_rejects_generic_recovery(self):
        with self.assertRaises(Crash):
            self.apply(checkpoint=self.fail('config'))
        generic = InstallOperation(*(self.root / n for n in ('state', 'skills', 'config')),
                                    'mcp.json', runtime_root=self.root / 'runtime')
        before = self.state()
        with self.assertRaisesRegex(tx.Conflict, '^INVALID_OPERATION$'):
            generic.recover()
        self.assertEqual(self.state(), before)
        self.settle()
        self.assertEqual(self.engine.config.path.read_bytes(), self.original)

    def test_failure_at_every_resource_boundary_restores_before_state(self):
        self.apply()
        self.settle()
        before = self.state()
        for point in ('preparing', 'runtime', 'skills', 'config'):
            with self.subTest(point=point), self.assertRaises(Crash):
                self.apply('2', checkpoint=self.fail(point))
            self.settle()
            self.assertEqual(self.state(), before)

    def test_owned_node_conflict_compensates_runtime_and_skills(self):
        self.engine.config.path.write_bytes(b'{"mcpServers":{"vf-studio":{"url":"user"}}}')
        before = self.state()
        with self.assertRaises(ConfigConflict):
            self.apply()
        self.settle()
        self.assertEqual(self.state(), before)

    def test_activity_lease_prevents_apply_and_preserves_before_state(self):
        self.apply()
        self.settle()
        before = self.state()
        with self.manager.lease(self.trees['1']):
            with self.assertRaisesRegex(tx.Conflict, '^RUNTIME_ACTIVITY_BUSY$'):
                self.apply('2')
        self.assertEqual(self.state(), before)

    def test_recovery_preserves_external_edit(self):
        with self.assertRaises(Crash):
            self.apply(checkpoint=self.fail('config'))
        edited = self.engine.config.path.read_bytes().replace(b'dark', b'light')
        self.engine.config.path.write_bytes(edited)
        with self.assertRaises(tx.Conflict):
            self.manager.recover_operation(self.key)
        self.assertEqual(self.engine.config.path.read_bytes(), edited)
        self.assertEqual(self.engine._active()['state'], 'aborting')

    def test_actual_process_exit_releases_lifecycle_lock_and_recovers_three_resources(self):
        before = self.state()
        script = '''import os,sys
sys.path.insert(0,sys.argv[1]);from install_operation import InstallOperation
from runtime_tools.runtime_lifecycle import RuntimeLifecycle
import host_workbuddy as w
from pathlib import Path
r=Path(sys.argv[2]);url=sys.argv[3]
e=InstallOperation(*(r/n for n in ('state','skills','config')),'mcp.json',runtime_root=r/'runtime',config_profile='workbuddy',admitted_url=url)
m=RuntimeLifecycle(e.runtime_store,[e]);m.initialize();key=next(iter(m.scopes));o=e.config.observe()
def stop(point):
 if point=='config':os._exit(77)
m.apply(key,'skills.1',{'example/SKILL.md':b'fixture-1'},w.desired_connection(url,admitted_url=url),expected_skill_revision=e.skills.snapshot()[1],expected_config_revision=o['ledger_revision'],expected_config_file=o['file'],runtime_digest=sys.argv[4],expected_runtime_revision=e.runtime.snapshot()[1],checkpoint=stop)
'''
        result = subprocess.run([sys.executable, '-I', '-B', '-c', script, str(TOOLS),
                                 str(self.root), URL, self.trees['1']], capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 77, 'WorkBuddy lifecycle did not reach crash checkpoint')
        identifier = self.engine._active()['id']
        self.assertNotEqual(self.state(), before)
        resumed = self.create()
        manager = RuntimeLifecycle(resumed.runtime_store, [resumed])
        manager.initialize()
        for _ in range(3):
            manager.recover_operation(self.key)
        self.assertEqual(resumed._active()['id'], identifier)
        self.assertEqual(resumed._active()['state'], 'cleaned_aborted')
        self.assertEqual(self.state(), before)

    def test_constructor_rejects_unexpected_profile_url_and_wrong_format(self):
        for values in [dict(config_profile='generic', admitted_url=URL),
                       dict(config_profile='unknown'),
                       dict(config_profile='workbuddy', admitted_url=URL, config_format='toml')]:
            with self.assertRaises(tx.Conflict):
                InstallOperation(*(self.root / n for n in ('state', 'skills', 'config')),
                                 'mcp.json', **values)
        with self.assertRaises(w.Conflict):
            self.create('https://example.invalid/mcp')


if __name__ == '__main__':
    unittest.main()
