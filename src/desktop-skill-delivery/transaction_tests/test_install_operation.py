"""Cross-resource installation and child-process failure tests, isolated only."""
from pathlib import Path
import json
import os
import subprocess
import sys
import tempfile
import unittest

TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))
import install_operation as op
import install_transaction as io
from host_config import ConfigConflict

BASE = Path(__file__).resolve().parents[4] / '.tmp/install-operation-tests'


class Crash(BaseException):
    pass


class OperationTests(unittest.TestCase):
    def setUp(self):
        BASE.mkdir(parents=True, exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(dir=BASE)
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).absolute()
        for name in ('state','skills','config'):
            (self.root / name).mkdir()
        self.engine = op.InstallOperation(*(self.root / name for name in ('state','skills','config')), 'mcp.json')

    def apply(self, version='1', checkpoint=lambda _: None):
        e = self.engine
        observation = e.config.observe()
        return e.apply(version, {'a.md': version.encode()}, {'url': 'https://v'+version+'.invalid'},
                       expected_skill_revision=e.skills.snapshot()[1],
                       expected_config_revision=observation['ledger_revision'],
                       expected_config_file=observation['file'], checkpoint=checkpoint)

    def state(self):
        e = self.engine
        raw = io.read(e.skills.root / 'a.md')
        return raw, e.skills.snapshot(), e.config._file(), e.config.snapshot()

    def fail(self, target):
        def callback(phase):
            if phase == target:
                raise Crash()
        return callback

    def test_success_cleanup_update_and_noop(self):
        e = self.engine
        self.assertEqual(self.apply()['status'], 'resources_applied')
        self.assertEqual(e.recover()['status'], 'operation_cleaned')
        self.assertEqual(e.recover()['status'], 'nothing_pending')
        self.apply('2')
        e.recover()
        before = self.state()
        self.apply('2')
        e.recover()
        self.assertEqual(before, self.state())
        self.assertEqual(e._active()['state'], 'cleaned')

    def test_second_step_error_compensates_first(self):
        e = self.engine
        # Existing unowned vf-studio must never be adopted by the config engine.
        e.config.path.write_bytes(b'{"mcpServers":{"vf-studio":{"url":"user-owned"}}}')
        before = self.state()
        with self.assertRaises(ConfigConflict):
            self.apply()
        self.assertEqual(before, self.state())
        self.assertEqual(e._active()['state'], 'aborted')
        self.assertEqual(e.recover()['status'], 'aborted_operation_cleaned')

    def test_external_edit_is_preserved_and_recovery_remains_pending(self):
        e = self.engine
        def change(phase):
            if phase == 'skills':
                (e.skills.root / 'a.md').write_bytes(b'user edit')
                raise ValueError('injected')
        with self.assertRaises(io.Conflict):
            self.apply(checkpoint=change)
        self.assertEqual((e.skills.root / 'a.md').read_bytes(), b'user edit')
        self.assertEqual(e._active()['state'], 'aborting')
        with self.assertRaises(io.Conflict):
            e.recover()

    def test_root_binding_overlap_and_private_journal(self):
        with self.assertRaisesRegex(io.Conflict, 'ROOT_OVERLAP'):
            op.InstallOperation(self.root/'state', self.root/'skills', self.root/'skills', 'mcp.json')
        self.apply()
        e = self.engine
        journal = e.meta / (e._active()['id'] + '.json')
        raw = journal.read_bytes()
        self.assertNotIn(b'https://', raw)
        self.assertNotIn(os.fsencode(self.root), raw)
        self.assertEqual(journal.stat().st_mode & 0o777, 0o600)
        data = json.loads(raw)
        data['bindings'][0] = '0'*64
        journal.write_bytes(io.encode(data))
        with self.assertRaisesRegex(io.Conflict, 'INVALID_OPERATION'):
            e.recover()

    def test_recovery_rejects_other_child_transaction(self):
        e = self.engine
        with self.assertRaises(Crash):
            self.apply(checkpoint=self.fail('skills'))
        e.skills.apply('other', {'a.md': b'other'}, e.skills.snapshot()[1])
        with self.assertRaisesRegex(io.Conflict, 'CHILD_TRANSACTION_CHANGED'):
            e.recover()
        self.assertEqual((e.skills.root / 'a.md').read_bytes(), b'other')

    def test_committed_observer_failure_never_rolls_back(self):
        def fail(phase):
            if phase == 'committed':
                raise ValueError('observer')
        with self.assertRaises(ValueError):
            self.apply(checkpoint=fail)
        self.assertEqual(self.engine._active()['state'], 'committed')
        state = self.state()
        self.engine.recover()
        self.assertEqual(state, self.state())

    def test_cleanup_reentry(self):
        for outcome in ('commit','abort'):
            if outcome == 'commit':
                self.apply()
            else:
                with self.assertRaises(Crash):
                    self.apply('2', checkpoint=self.fail('config'))
                self.engine.recover()
            state = self.state()
            with self.assertRaises(Crash):
                self.engine.recover(checkpoint=self.fail('config:cleaning'))
            self.engine.recover()
            self.assertEqual(state, self.state())

    def test_noop_child_does_not_undo_previous_transaction(self):
        e = self.engine
        self.apply()
        e.recover()
        for unchanged in ('skills', 'config'):
            before = self.state()
            obs = e.config.observe()
            with self.assertRaises(Crash):
                e.apply('1' if unchanged == 'skills' else '2',
                        {'a.md': b'1' if unchanged == 'skills' else b'2'},
                        {'url': 'https://v1.invalid' if unchanged == 'config' else 'https://v2.invalid'},
                        expected_skill_revision=e.skills.snapshot()[1],
                        expected_config_revision=obs['ledger_revision'], expected_config_file=obs['file'],
                        checkpoint=self.fail('config'))
            e.recover()
            self.assertEqual(before, self.state())
            e.recover()

    def test_process_exit_before_and_after_global_commit(self):
        phases = ['preparing','skills:staged','skills:file:a.md','skills:ledger','skills:committed','skills',
                  'config:preparing','config:before_backup','config:config','config:ledger',
                  'config:committed','config','committed']
        e = self.engine
        for i, phase in enumerate(phases):
            with self.subTest(phase=phase):
                before = self.state()
                script = '''import os,sys
sys.path.insert(0,sys.argv[1])
from install_operation import InstallOperation
from pathlib import Path
root=Path(sys.argv[2]);e=InstallOperation(*(root/n for n in ('state','skills','config')),'mcp.json')
def stop(p):
    if p==sys.argv[3]: os._exit(77)
o=e.config.observe()
e.apply(sys.argv[4],{'a.md':sys.argv[4].encode()},{'url':'https://v'+sys.argv[4]+'.invalid'},expected_skill_revision=e.skills.snapshot()[1],expected_config_revision=o['ledger_revision'],expected_config_file=o['file'],checkpoint=stop)
'''
                result = subprocess.run([sys.executable,'-c',script,str(TOOL),str(self.root),phase,str(i+1)],capture_output=True)
                self.assertEqual(result.returncode, 77, result.stderr)
                after = self.state()
                if phase == 'committed':
                    e.recover()
                    self.assertEqual(after, self.state())
                    self.assertNotEqual(before, self.state())
                else:
                    # A second interruption while rolling back must be recoverable.
                    with self.assertRaises(Crash):
                        e.recover(checkpoint=self.fail('restored:config'))
                    self.assertEqual(e.recover()['status'], 'resources_restored')
                    self.assertEqual(before, self.state())
                    e.recover()


if __name__ == '__main__':
    unittest.main()
