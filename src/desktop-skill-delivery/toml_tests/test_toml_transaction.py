"""Real isolated TOML files, process crashes and cross-resource compensation."""
from pathlib import Path
import json
import os
import subprocess
import sys
import tempfile
import tomllib
import unittest
import uuid

TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))
import config_transaction as cfg
import host_toml
import install_transaction as io
from install_operation import InstallOperation
from toml_transaction import TomlConfigFile
from runtime_tools.runtime_store import SCHEMA

BASE = Path(__file__).resolve().parents[4] / '.tmp/toml-transaction-tests'
ORIGINAL = b'# keep this comment\r\nmodel = "fixture"\r\n[mcp_servers.other]\r\nurl = "https://other.invalid"\r\n'
DESIRED = {'url': 'https://vf.invalid/mcp', 'bearer_token_env_var': 'VF_TEST_TOKEN_REFERENCE'}


class Crash(BaseException):
    pass


def fail_at(target):
    def stop(point):
        if point == target:
            raise Crash()
    return stop


class TomlTransactionTests(unittest.TestCase):
    def setUp(self):
        BASE.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=BASE)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).absolute()
        self.path = self.root / 'config.toml'
        self.engine = TomlConfigFile(self.root, 'config.toml')

    def seed(self, raw=ORIGINAL):
        self.path.write_bytes(raw)
        self.path.chmod(0o640)

    @staticmethod
    def apply_to(engine, desired=DESIRED, checkpoint=lambda _: None):
        observed = engine.observe()
        return engine.apply(desired, expected_revision=observed['ledger_revision'],
                            expected_file=observed['file'], checkpoint=checkpoint)

    def test_install_update_remove_preserves_foreign_comments_and_modes(self):
        self.seed()
        first = self.apply_to(self.engine)
        self.assertFalse(first['host_verified'])
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o640)
        raw = self.path.read_bytes().replace(b'model = "fixture"', b'model = "user edit"')
        self.path.write_bytes(raw)
        self.apply_to(self.engine, {**DESIRED, 'url': 'https://next.invalid'})
        result = tomllib.loads(self.path.read_text())
        self.assertEqual(result['model'], 'user edit')
        self.assertEqual(result['mcp_servers']['other']['url'], 'https://other.invalid')
        self.assertIn(b'# keep this comment\r\n', self.path.read_bytes())
        self.apply_to(self.engine, None)
        self.assertNotIn('vf-studio', tomllib.loads(self.path.read_text())['mcp_servers'])
        self.assertIsNone(self.engine.snapshot()[0]['owned_sha256'])
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o640)

    def test_missing_file_and_noop_do_not_mutate_history(self):
        self.assertEqual(self.apply_to(self.engine, None)['status'], 'unchanged')
        self.assertFalse(self.path.exists())
        self.apply_to(self.engine)
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        state = self.engine.snapshot(), self.path.read_bytes(), self.engine._active()
        self.assertEqual(self.apply_to(self.engine)['status'], 'unchanged')
        self.assertEqual(state, (self.engine.snapshot(), self.path.read_bytes(), self.engine._active()))

    def test_all_persistent_apply_boundaries_recover_exact_original(self):
        for existing in (False, True):
            for phase in ('preparing', 'before_backup', 'after_backup', 'staged', 'writing', 'config', 'ledger'):
                with self.subTest(existing=existing, phase=phase), tempfile.TemporaryDirectory(dir=BASE) as temp:
                    root = Path(temp); path = root / 'config.toml'
                    if existing:
                        path.write_bytes(ORIGINAL); path.chmod(0o640)
                    engine = TomlConfigFile(root, 'config.toml'); before = engine.snapshot()
                    with self.assertRaises(Crash):
                        self.apply_to(engine, checkpoint=fail_at(phase))
                    recovered = TomlConfigFile(root, 'config.toml')
                    self.assertEqual(recovered.recover()['status'], 'config_bytes_restored')
                    self.assertEqual(io.read(path), ORIGINAL if existing else None)
                    self.assertEqual(recovered.snapshot(), before)
                    if existing:
                        self.assertEqual(path.stat().st_mode & 0o777, 0o640)
                    journal = recovered._active()
                    recovered.cleanup(journal['id'], expected_revision=before[1])
                    self.assertFalse((recovered._folder(journal['id']) / 'after.bin').exists())

    def test_real_process_exit_reopens_and_restores(self):
        self.seed(); before = self.engine.snapshot()
        script = '''import os,sys
sys.path.insert(0,sys.argv[1])
from toml_transaction import TomlConfigFile
e=TomlConfigFile(sys.argv[2],'config.toml');o=e.observe()
def stop(p):
 if p=='ledger':os._exit(77)
e.apply({'url':'https://fixture.invalid'},expected_revision=o['ledger_revision'],expected_file=o['file'],checkpoint=stop)
'''
        child = subprocess.run([sys.executable, '-B', '-c', script, str(TOOL), str(self.root)], capture_output=True)
        self.assertEqual(child.returncode, 77)
        reopened = TomlConfigFile(self.root, 'config.toml')
        reopened.recover()
        self.assertEqual(self.path.read_bytes(), ORIGINAL)
        self.assertEqual(reopened.snapshot(), before)

    def test_compensation_and_cleanup_each_resume_after_interruption(self):
        for phase in ('compensation_requested', 'config', 'ledger', 'compensation_completed'):
            with self.subTest(phase=phase), tempfile.TemporaryDirectory(dir=BASE) as temp:
                root = Path(temp); path = root / 'config.toml'; path.write_bytes(ORIGINAL); path.chmod(0o640)
                engine = TomlConfigFile(root, 'config.toml'); before = engine.snapshot()
                result = self.apply_to(engine)
                with self.assertRaises(Crash):
                    engine.rollback(result['transaction'], expected_revision=result['revision'], checkpoint=fail_at(phase))
                # Explicit retry handles already-completed compensation as well.
                engine.rollback(result['transaction'], expected_revision=result['revision'])
                self.assertEqual(path.read_bytes(), ORIGINAL); self.assertEqual(engine.snapshot(), before)
                for cleanup_phase in ('cleaning', 'before', 'after'):
                    with self.assertRaises(Crash):
                        engine.cleanup(result['transaction'], expected_revision=before[1], checkpoint=fail_at(cleanup_phase))
                engine.cleanup(result['transaction'], expected_revision=before[1])
                self.assertFalse((engine._folder(result['transaction']) / 'before.bin').exists())

    def test_stale_preflight_and_external_edits_are_preserved(self):
        self.seed(); observed = self.engine.observe(); self.path.write_bytes(ORIGINAL + b'\n')
        with self.assertRaisesRegex(io.Conflict, 'CONFIG_CHANGED'):
            self.engine.apply(DESIRED, expected_revision=observed['ledger_revision'], expected_file=observed['file'])
        self.path.write_bytes(ORIGINAL)
        with self.assertRaises(Crash):
            self.apply_to(self.engine, checkpoint=fail_at('config'))
        changed = self.path.read_bytes() + b'# user after crash\n'; self.path.write_bytes(changed)
        with self.assertRaisesRegex(io.Conflict, 'RECOVERY_CONFIG_CONFLICT'):
            self.engine.recover()
        self.assertEqual(self.path.read_bytes(), changed)

    def test_mode_drift_is_preserved(self):
        self.seed()
        with self.assertRaises(Crash):
            self.apply_to(self.engine, checkpoint=fail_at('config'))
        self.path.chmod(0o644); current = self.path.read_bytes()
        with self.assertRaisesRegex(io.Conflict, 'RECOVERY_CONFIG_CONFLICT'):
            self.engine.recover()
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o644)
        self.assertEqual(self.path.read_bytes(), current)

    def test_backups_cannot_forge_ownership_by_rehashing_file(self):
        self.seed()
        first = self.apply_to(self.engine); self.engine.cleanup(first['transaction'], expected_revision=first['revision'])
        with self.assertRaises(Crash):
            self.apply_to(self.engine, {**DESIRED, 'url': 'https://next.invalid'}, fail_at('config'))
        journal = self.engine._active(); folder = self.engine._folder(journal['id'])
        backup = folder / 'before.bin'; raw = backup.read_bytes().replace(b'https://vf.invalid', b'https://forged.invalid')
        backup.write_bytes(raw); journal['before_file']['sha256'] = io.sha(raw); self.engine._save(journal)
        current = self.path.read_bytes()
        with self.assertRaisesRegex(io.Conflict, 'BACKUP_OWNERSHIP_MISMATCH'):
            self.engine.recover()
        self.assertEqual(self.path.read_bytes(), current)

    def test_unowned_node_and_owned_external_change_are_never_adopted(self):
        self.seed(b'[mcp_servers.vf-studio]\nurl="user"\n')
        with self.assertRaisesRegex(host_toml.ConfigConflict, 'OWNERSHIP_CONFLICT'):
            self.apply_to(self.engine)
        self.path.unlink(); self.apply_to(self.engine)
        self.path.write_bytes(self.path.read_bytes().replace(b'https://vf.invalid', b'https://user.invalid'))
        with self.assertRaisesRegex(host_toml.ConfigConflict, 'OWNERSHIP_CONFLICT'):
            self.apply_to(self.engine, None)

    def test_json_toml_schema_cannot_reinterpret_same_target_records(self):
        self.apply_to(self.engine)
        wrong = cfg.ConfigFile(self.root, 'config.toml')
        self.assertEqual(wrong.meta, self.engine.meta)
        with self.assertRaisesRegex(io.Conflict, 'INVALID_LEDGER'):
            wrong.snapshot()
        with self.assertRaisesRegex(io.Conflict, 'INVALID_JOURNAL'):
            wrong.recover()

    def test_private_backups_and_receipts_keep_synthetic_secret_out_of_logs(self):
        marker = b'SYNTHETIC_SECRET_ONLY_IN_PRIVATE_BACKUP'
        self.seed(b'# ' + marker + b'\nmodel="fixture"\n')
        result = self.apply_to(self.engine); folder = self.engine._folder(result['transaction'])
        self.assertIn(marker, (folder / 'before.bin').read_bytes())
        self.assertNotIn(marker, json.dumps(result).encode())
        self.assertNotIn(marker, (folder / 'journal.json').read_bytes())
        self.assertNotIn(marker, self.engine.ledger_path.read_bytes())
        for path in self.engine.meta.rglob('*'):
            self.assertEqual(path.stat().st_mode & 0o777, 0o700 if path.is_dir() else 0o600)


class TomlOperationTests(unittest.TestCase):
    def setUp(self):
        BASE.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=BASE); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).absolute()
        for name in ('state', 'skills', 'config', 'runtime', 'sources'):
            (self.root / name).mkdir(mode=0o700)
        self.engine = self.at('toml')
        self.engine.config.path.write_bytes(ORIGINAL); self.engine.config.path.chmod(0o640)
        raw = b'non-executable-runtime-fixture'; source = self.root / 'sources/payload'; source.write_bytes(raw)
        manifest = {'schema': SCHEMA, 'release': 'toml-fixture', 'source_sha256': io.sha(raw),
                    'entries': {'python/payload': {'type': 'file', 'size': len(raw), 'sha256': io.sha(raw), 'mode': 0o644}}}
        self.tree = io.sha(io.encode(manifest))
        self.engine.runtime_store.stage(manifest, {'python/payload': source}, expected_digest=self.tree, transaction_id=uuid.uuid4().hex)

    def at(self, fmt):
        return InstallOperation(*(self.root / n for n in ('state', 'skills', 'config')), 'config.toml',
                                runtime_root=self.root / 'runtime', config_format=fmt)

    def apply(self, checkpoint=lambda _: None):
        e = self.engine; o = e.config.observe()
        return e.apply('skill.fixture', {'a.md': b'skill fixture'}, DESIRED,
                       expected_skill_revision=e.skills.snapshot()[1], expected_config_revision=o['ledger_revision'],
                       expected_config_file=o['file'], expected_runtime_revision=e.runtime.snapshot()[1],
                       runtime_digest=self.tree, checkpoint=checkpoint)

    def state(self):
        e = self.engine
        return (io.read(e.runtime.root / 'runtime.json'), e.runtime.snapshot(), io.read(e.skills.root / 'a.md'),
                e.skills.snapshot(), e.config._file(), e.config.snapshot())

    def test_three_resources_commit_and_cleanup_use_toml(self):
        self.apply(); after = self.state(); self.engine.recover()
        self.assertEqual(self.state(), after)
        self.assertEqual(tomllib.loads(self.engine.config.path.read_text())['mcp_servers']['vf-studio'], DESIRED)
        self.assertEqual(self.engine._active()['state'], 'cleaned')

    def test_three_resource_interruption_restores_all_resources(self):
        for phase in ('preparing', 'runtime', 'skills', 'config:config', 'config:ledger', 'config:committed', 'config'):
            with self.subTest(phase=phase):
                before = self.state()
                with self.assertRaises(Crash):
                    self.apply(fail_at(phase))
                reopened = self.at('toml'); reopened.recover(); reopened.recover()
                self.assertEqual(self.state(), before)
                self.assertEqual(self.engine.config.path.stat().st_mode & 0o777, 0o640)

    def test_format_binding_blocks_wrong_recovery_before_config_child_exists(self):
        before = self.state()
        with self.assertRaises(Crash):
            self.apply(fail_at('skills'))
        partial = self.state(); wrong = self.at('json')
        with self.assertRaisesRegex(io.Conflict, 'INVALID_OPERATION'):
            wrong.recover()
        self.assertEqual(self.state(), partial)
        self.engine.recover(); self.assertEqual(self.state(), before)

    def test_real_three_resource_process_exit_recovers_same_operation(self):
        script = """import os,sys
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from install_operation import InstallOperation
root=Path(sys.argv[2]);e=InstallOperation(*(root/n for n in ('state','skills','config')),'config.toml',runtime_root=root/'runtime',config_format='toml')
def stop(p):
 if p==sys.argv[4]:os._exit(79)
o=e.config.observe()
e.apply('skill.fixture',{'a.md':b'skill fixture'},{'url':'https://vf.invalid/mcp','bearer_token_env_var':'VF_TEST_TOKEN_REFERENCE'},expected_skill_revision=e.skills.snapshot()[1],expected_config_revision=o['ledger_revision'],expected_config_file=o['file'],expected_runtime_revision=e.runtime.snapshot()[1],runtime_digest=sys.argv[3],checkpoint=stop)
"""
        for phase in ('config:config', 'committed'):
            with self.subTest(phase=phase):
                before = self.state()
                child = subprocess.run([sys.executable, '-B', '-c', script, str(TOOL), str(self.root), self.tree, phase], capture_output=True)
                self.assertEqual(child.returncode, 79)
                partial = self.state(); reopened = self.at('toml'); reopened.recover(); reopened.recover()
                self.assertEqual(self.state(), partial if phase == 'committed' else before)

    def test_lifecycle_inventory_accepts_toml_and_seals_format_binding(self):
        from runtime_tools.runtime_lifecycle import RuntimeLifecycle
        # A separate fresh store is needed: initialize cannot adopt staged trees.
        with tempfile.TemporaryDirectory(dir=BASE) as temp:
            root = Path(temp).absolute()
            for name in ('state', 'skills', 'config', 'runtime'):
                (root / name).mkdir(mode=0o700)
            engine = InstallOperation(*(root / n for n in ('state', 'skills', 'config')), 'config.toml', runtime_root=root / 'runtime', config_format='toml')
            manager = RuntimeLifecycle(engine.runtime_store, [engine]); manager.initialize()
            inventory = manager.inventory()
            self.assertFalse(inventory['blockers'])
            wrong = InstallOperation(*(root / n for n in ('state', 'skills', 'config')), 'config.toml', runtime_root=root / 'runtime')
            with self.assertRaisesRegex(io.Conflict, 'LIFECYCLE_SCOPE_CHANGED'):
                RuntimeLifecycle(wrong.runtime_store, [wrong]).initialize()
            engine.config.path.write_bytes(b'# outside edit\n')
            engine.config.path.chmod(0o600)
            with self.assertRaisesRegex(io.Conflict, 'INITIAL_SCOPE_CHANGED'):
                manager.inventory()
            self.assertEqual(engine.config.path.read_bytes(), b'# outside edit\n')

    def test_unknown_format_rejected_before_metadata_write(self):
        for value in ('yaml', None, [], True):
            with self.subTest(value=value), self.assertRaisesRegex(io.Conflict, 'CONFIG_FORMAT_UNSUPPORTED'):
                InstallOperation(self.root / 'state', self.root / 'skills', self.root / 'config', 'config.toml', config_format=value)
        self.assertIsNone(self.engine._active())


if __name__ == '__main__':
    unittest.main()
