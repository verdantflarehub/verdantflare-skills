"""Compensate committed resources in isolated fixtures and real child processes."""
from pathlib import Path
import json
import os
import subprocess
import sys
import tempfile
import unittest
import uuid

TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))
import config_transaction as config
import install_transaction as files

BASE = Path(__file__).resolve().parents[4] / '.tmp/compensation-tests'


class Crash(BaseException):
    pass


class CompensationTests(unittest.TestCase):
    def setUp(self):
        BASE.mkdir(parents=True, exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(dir=BASE)
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name).absolute()

    def engine(self, kind, label='case'):
        root = self.base / (kind + label)
        root.mkdir()
        return files.ManagedFiles(root) if kind == 'files' else config.ConfigFile(root, 'mcp.json')

    def apply(self, engine, version, identifier=None):
        if isinstance(engine, files.ManagedFiles):
            payload = ({'a.md': b'old', 'removed.md': b'keep'} if version == '1' else
                       {'a.md': b'new', 'added.md': b'added'})
            return engine.apply(version, payload, engine.snapshot()[1],
                                modes={name: 0o755 if name == 'a.md' else 0o644 for name in payload},
                                transaction_id=identifier)
        return engine.apply({'url': 'https://v' + version + '.invalid'}, expected_revision=engine.snapshot()[1],
                            expected_file=engine.observe()['file'], transaction_id=identifier)

    def rollback(self, engine, receipt, checkpoint=lambda _: None):
        return engine.rollback(receipt['transaction'], expected_revision=receipt['revision'], checkpoint=checkpoint)

    def journal(self, engine):
        return engine._pending() if isinstance(engine, files.ManagedFiles) else engine._active()

    def public_state(self, engine):
        if isinstance(engine, files.ManagedFiles):
            return {p.name: (p.read_bytes(), p.stat().st_mode & 0o777) for p in engine.root.glob('*.md')}
        return engine._file()

    def fail_at(self, phase):
        def fail(actual):
            if actual == phase:
                raise Crash()
        return fail

    def test_original_state_commit_history_and_reapply(self):
        for kind in ('files', 'config'):
            for existing in (False, True):
                with self.subTest(kind=kind, existing=existing):
                    e = self.engine(kind, str(existing))
                    if existing:
                        self.apply(e, '1')
                    before, revision = e.snapshot()
                    old = self.public_state(e)
                    r = self.apply(e, '2')
                    journal = self.journal(e)
                    self.rollback(e, r)
                    self.assertEqual(e.snapshot(), (before, revision))
                    self.assertEqual(self.public_state(e), old)
                    self.assertEqual(self.journal(e), journal)
                    self.assertEqual(self.rollback(e, r)['status'], 'already_compensated')
                    self.assertEqual(e.recover()['status'], 'nothing_pending')
                    self.apply(e, '2')

    def test_fresh_supplied_ids_and_noop(self):
        for kind in ('files', 'config'):
            e = self.engine(kind)
            for bad in ('../bad', 'A'*32, '', 123):
                with self.assertRaisesRegex(files.Conflict, 'INVALID_TRANSACTION'):
                    self.apply(e, '1', bad)
            identifier = uuid.uuid4().hex
            r = self.apply(e, '1', identifier)
            self.assertEqual(r['transaction'], identifier)
            with self.assertRaisesRegex(files.Conflict, 'TRANSACTION_ALREADY_EXISTS'):
                self.apply(e, '2', identifier)
            unused = uuid.uuid4().hex
            self.assertEqual(self.apply(e, '1', unused)['status'], 'unchanged')
            self.assertEqual(self.journal(e)['id'], identifier)
            self.assertEqual(self.apply(e, '2', unused)['transaction'], unused)

    def test_user_edit_and_wrong_revision_preserved(self):
        for kind in ('files', 'config'):
            e = self.engine(kind)
            r = self.apply(e, '1')
            with self.assertRaisesRegex(files.Conflict, 'NOT_LATEST_COMMIT'):
                e.rollback('f'*32, expected_revision=r['revision'])
            with self.assertRaisesRegex(files.Conflict, 'REVISION_CHANGED'):
                e.rollback(r['transaction'], expected_revision='0'*64)
            path = e.root / ('a.md' if kind == 'files' else 'mcp.json')
            path.write_bytes(b'user edits')
            with self.assertRaises(files.Conflict):
                self.rollback(e, r)
            self.assertEqual(path.read_bytes(), b'user edits')
            self.assertIsNone(e._compensation(self.journal(e)).state())

    def test_newer_transaction_is_not_rolled_back(self):
        for kind in ('files', 'config'):
            e = self.engine(kind)
            first = self.apply(e, '1')
            self.apply(e, '2')
            state = self.public_state(e)
            with self.assertRaisesRegex(files.Conflict, 'NOT_LATEST_COMMIT'):
                self.rollback(e, first)
            self.assertEqual(state, self.public_state(e))

    def test_cleaned_and_corrupt_backups_rejected(self):
        for kind in ('files', 'config'):
            for clean in (False, True):
                e = self.engine(kind, str(clean))
                self.apply(e, '1')
                r = self.apply(e, '2')
                state = self.public_state(e)
                if clean:
                    e.cleanup(r['transaction'], expected_revision=r['revision'])
                else:
                    path = (e.meta / r['transaction'] / 'before/a.md' if kind == 'files' else
                            e.transactions / r['transaction'] / 'before.bin')
                    path.write_bytes(b'changed')
                with self.assertRaises(files.Conflict):
                    self.rollback(e, r)
                self.assertEqual(state, self.public_state(e))

    def test_pending_blocks_apply_cleanup_and_recovery_preserves_edits(self):
        for kind in ('files', 'config'):
            e = self.engine(kind)
            r = self.apply(e, '1')
            with self.assertRaises(Crash):
                self.rollback(e, r, self.fail_at('compensation_requested'))
            with self.assertRaisesRegex(files.Conflict, 'COMPENSATION_REQUIRED'):
                self.apply(e, '2')
            with self.assertRaisesRegex(files.Conflict, 'COMPENSATION_REQUIRED'):
                e.cleanup(r['transaction'], expected_revision=r['revision'])
            path = e.root / ('a.md' if kind == 'files' else 'mcp.json')
            path.write_bytes(b'user edits')
            with self.assertRaises(files.Conflict):
                e.recover()
            self.assertEqual(path.read_bytes(), b'user edits')

    def test_completed_cleanup_and_retry(self):
        for kind in ('files', 'config'):
            e = self.engine(kind)
            r = self.apply(e, '1')
            self.rollback(e, r)
            e.cleanup(r['transaction'], expected_revision=e.snapshot()[1])
            self.assertEqual(self.rollback(e, r)['status'], 'already_compensated')
            self.assertEqual(e.recover()['status'], 'nothing_pending')
            self.apply(e, '2')

    def test_tampered_compensation_record_rejected(self):
        for kind in ('files', 'config'):
            e = self.engine(kind)
            r = self.apply(e, '1')
            with self.assertRaises(Crash):
                self.rollback(e, r, self.fail_at('compensation_requested'))
            record = e._compensation(self.journal(e))
            value = json.loads(record.path.read_bytes())
            value['journal_sha256'] = '0'*64
            record.path.write_bytes(files.encode(value))
            state = self.public_state(e)
            with self.assertRaisesRegex(files.Conflict, 'INVALID_COMPENSATION'):
                e.recover()
            self.assertEqual(state, self.public_state(e))

    def test_real_process_exit_and_recovery_reentry(self):
        for kind in ('files', 'config'):
            phases = ['compensation_requested', 'ledger', 'compensation_completed',
                      'file:a.md' if kind == 'files' else 'config']
            for index, phase in enumerate(phases):
                for existing in (False, True):
                    with self.subTest(kind=kind, phase=phase, existing=existing):
                        e = self.engine(kind, str(index)+str(existing))
                        if existing:
                            self.apply(e, '1')
                        old, ledger = self.public_state(e), e.snapshot()
                        r = self.apply(e, '2')
                        script = '''import os,sys
sys.path.insert(0,sys.argv[1])
from install_transaction import ManagedFiles
from config_transaction import ConfigFile
e=ManagedFiles(sys.argv[3]) if sys.argv[2]=='files' else ConfigFile(sys.argv[3],'mcp.json')
def stop(point):
    if point==sys.argv[6]: os._exit(77)
e.rollback(sys.argv[4],expected_revision=sys.argv[5],checkpoint=stop)
'''
                        child = subprocess.run([sys.executable, '-c', script, str(TOOL), kind, str(e.root),
                                                r['transaction'], r['revision'], phase], capture_output=True)
                        self.assertEqual(child.returncode, 77, child.stderr)
                        if phase != 'compensation_completed':
                            with self.assertRaises(Crash):
                                e.recover(checkpoint=self.fail_at('ledger'))
                        e.recover()
                        self.assertEqual(old, self.public_state(e))
                        self.assertEqual(ledger, e.snapshot())
                        self.assertEqual(self.rollback(e, r)['status'], 'already_compensated')


if __name__ == '__main__':
    unittest.main()
