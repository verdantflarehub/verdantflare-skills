"""Real WorkBuddy config/skills placement through the unchanged transactions."""
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
from runtime_tools.runtime_lifecycle import RuntimeLifecycle
from runtime_tools.runtime_store import SCHEMA

URL = 'https://native-layout.invalid/mcp/desktop/fixture'


@unittest.skipUnless(os.name == 'posix', 'Windows storage is a separate plan')
class WorkBuddyNativeLayoutTests(unittest.TestCase):
    def setUp(self):
        base = TOOLS.parents[2] / '.tmp/workbuddy-native-layout-tests'
        base.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=base)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).absolute()
        for name in ('state', 'workbuddy', 'workbuddy/skills', 'runtime'):
            (self.root / name).mkdir(mode=0o700)
        self.engine = self.create()
        self.original = b'{ "theme":"dark", "mcpServers":{"foreign":{"command":"keep"}} }\r\n'
        self.engine.config.path.write_bytes(self.original)
        self.engine.config.path.chmod(0o640)
        foreign = self.engine.skills.root / 'user-skill/SKILL.md'
        foreign.parent.mkdir()
        foreign.write_bytes(b'user-owned instructions')
        self.manager = RuntimeLifecycle(self.engine.runtime_store, [self.engine])
        self.manager.initialize()
        self.key = next(iter(self.manager.scopes))
        source = self.root / 'runtime-fixture.dat'
        source.write_bytes(b'DATA_ONLY_NEVER_EXECUTE')
        manifest = {'schema': SCHEMA, 'release': 'runtime.fixture',
                    'source_sha256': tx.sha(source.read_bytes()),
                    'entries': {'fixture.dat': {'type': 'file', 'sha256': tx.sha(source.read_bytes()),
                    'size': source.stat().st_size, 'mode': 0o644}}}
        self.digest = tx.sha(tx.encode(manifest))
        self.manager.stage(manifest, {'fixture.dat': source}, expected_digest=self.digest,
                           transaction_id=uuid.uuid4().hex)

    def create(self):
        return InstallOperation(*(self.root / name for name in ('state', 'workbuddy/skills', 'workbuddy')),
                                'mcp.json', runtime_root=self.root / 'runtime',
                                config_profile='workbuddy', admitted_url=URL)

    def apply(self, release):
        e = self.engine
        observed = e.config.observe()
        return self.manager.apply(self.key, release, {'vf-example/SKILL.md': release.encode()},
            w.desired_connection(URL, admitted_url=URL), expected_skill_revision=e.skills.snapshot()[1],
            expected_config_revision=observed['ledger_revision'], expected_config_file=observed['file'],
            runtime_digest=self.digest, expected_runtime_revision=e.runtime.snapshot()[1])

    def settle(self):
        for _ in range(3):
            self.manager.recover_operation(self.key)

    def test_native_layout_install_update_uninstall_preserves_user_files(self):
        self.apply('skills.1')
        self.settle()
        self.apply('skills.2')
        self.settle()
        e = self.engine
        self.assertEqual((e.skills.root / 'vf-example/SKILL.md').read_bytes(), b'skills.2')
        self.assertEqual(json.loads(e.config.path.read_bytes())['mcpServers']['vf-studio'],
                         w.desired_connection(URL, admitted_url=URL))
        self.assertEqual(e.skills.meta, self.root / 'state/.vf-workbuddy-skill-files')
        self.assertFalse((e.skills.root / tx.META).exists())
        self.assertEqual({p.relative_to(e.skills.root).as_posix()
                          for p in e.skills.root.rglob('SKILL.md')},
                         {'vf-example/SKILL.md', 'user-skill/SKILL.md'})
        observed = e.config.observe()
        self.manager.uninstall(self.key, expected_skill_revision=e.skills.snapshot()[1],
            expected_config_revision=observed['ledger_revision'], expected_config_file=observed['file'],
            expected_runtime_revision=e.runtime.snapshot()[1])
        self.settle()
        self.assertEqual(e.config.path.read_bytes(), self.original)
        self.assertEqual(e.config.path.stat().st_mode & 0o777, 0o640)
        self.assertEqual((e.skills.root / 'user-skill/SKILL.md').read_bytes(), b'user-owned instructions')
        self.assertFalse((e.skills.root / 'vf-example/SKILL.md').exists())
        self.assertIsNone(tx.read(e.runtime.root / 'runtime.json'))

    def test_native_layout_actual_process_exit_and_reopen_restore_all_resources(self):
        before = (self.engine.runtime.snapshot(), self.engine.skills.snapshot(), self.engine.config._file())
        script = '''import os,sys
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from install_operation import InstallOperation
from runtime_tools.runtime_lifecycle import RuntimeLifecycle
import host_workbuddy as w
r=Path(sys.argv[2]);url=sys.argv[3]
e=InstallOperation(*(r/n for n in ('state','workbuddy/skills','workbuddy')),'mcp.json',runtime_root=r/'runtime',config_profile='workbuddy',admitted_url=url)
m=RuntimeLifecycle(e.runtime_store,[e]);m.initialize();key=next(iter(m.scopes));o=e.config.observe()
def stop(point):
 if point=='config':os._exit(77)
m.apply(key,'skills.crash',{'vf-example/SKILL.md':b'crash'},w.desired_connection(url,admitted_url=url),expected_skill_revision=e.skills.snapshot()[1],expected_config_revision=o['ledger_revision'],expected_config_file=o['file'],runtime_digest=sys.argv[4],expected_runtime_revision=e.runtime.snapshot()[1],checkpoint=stop)
'''
        result = subprocess.run([sys.executable, '-I', '-B', '-c', script, str(TOOLS), str(self.root), URL,
                                 self.digest], capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 77, 'Native-layout process missed its crash checkpoint')
        operation_id = self.engine._active()['id']
        resumed = self.create()
        manager = RuntimeLifecycle(resumed.runtime_store, [resumed])
        manager.initialize()
        for _ in range(3):
            manager.recover_operation(self.key)
        self.assertEqual(resumed._active()['id'], operation_id)
        self.assertEqual(resumed._active()['state'], 'cleaned_aborted')
        self.assertEqual((resumed.runtime.snapshot(), resumed.skills.snapshot(), resumed.config._file()), before)
        self.assertEqual((resumed.skills.root / 'user-skill/SKILL.md').read_bytes(), b'user-owned instructions')

    def test_only_exact_workbuddy_skills_child_is_allowed(self):
        e = self.engine
        for name in ('workbuddy/other', 'workbuddy/nested', 'workbuddy/nested/skills',
                     'workbuddy/state', 'workbuddy/runtime'):
            (self.root / name).mkdir(mode=0o700)
        invalid = [
            (self.root / 'state', e.config.root, e.config.root, self.root / 'runtime'),
            (self.root / 'state', e.config.root / 'other', e.config.root, self.root / 'runtime'),
            (self.root / 'state', e.config.root / 'nested/skills', e.config.root, self.root / 'runtime'),
            (self.root / 'workbuddy/state', e.skills.root, e.config.root, self.root / 'runtime'),
            (self.root / 'state', e.skills.root, e.config.root, self.root / 'workbuddy/runtime'),
            (self.root / 'state', e.config.root, e.skills.root, self.root / 'runtime'),
        ]
        before = {p.relative_to(self.root).as_posix(): tx.sha(p.read_bytes())
                  for p in self.root.rglob('*') if p.is_file()}
        for state, skills, config, runtime in invalid:
            with self.subTest(skills=skills.name, state=state.name), \
                 self.assertRaisesRegex(tx.Conflict, '^ROOT_OVERLAP$'):
                InstallOperation(state, skills, config, 'mcp.json', runtime_root=runtime,
                                 config_profile='workbuddy', admitted_url=URL)
        with self.assertRaisesRegex(tx.Conflict, '^ROOT_OVERLAP$'):
            InstallOperation(self.root / 'state', e.skills.root, e.config.root, 'mcp.json',
                             runtime_root=self.root / 'runtime')
        self.assertEqual({p.relative_to(self.root).as_posix(): tx.sha(p.read_bytes())
                          for p in self.root.rglob('*') if p.is_file()}, before)
