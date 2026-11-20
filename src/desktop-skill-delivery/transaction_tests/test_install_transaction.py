"""Isolated file-transaction tests, never a host/configuration acceptance test."""
from pathlib import Path
import os
import subprocess
import sys
import tempfile
import unittest

TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))
import install_transaction as t


class SimulatedCrash(BaseException):
    pass


class TransactionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.engine = t.ManagedFiles(self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def apply(self, files, version='1.0.0', checkpoint=lambda _: None):
        return self.engine.apply(version, files, self.engine.snapshot()[1], checkpoint)

    def test_install_update_delete_and_repeat_preserve_unmanaged(self):
        (self.root / 'user.md').write_bytes(b'personal')
        first = self.apply({'one/SKILL.md': b'old', 'two/SKILL.md': b'two'})
        self.assertFalse(first['host_verified'])
        self.assertEqual(self.apply({'one/SKILL.md': b'old', 'two/SKILL.md': b'two'})['status'], 'unchanged')
        self.apply({'one/SKILL.md': b'new', 'three/SKILL.md': b'three'}, '1.1.0')
        self.assertEqual((self.root / 'one/SKILL.md').read_bytes(), b'new')
        self.assertFalse((self.root / 'two/SKILL.md').exists())
        self.assertEqual((self.root / 'user.md').read_bytes(), b'personal')

    def test_local_change_and_unmanaged_claim_rejected(self):
        self.apply({'one.md': b'old'})
        (self.root / 'one.md').write_bytes(b'user edit')
        with self.assertRaisesRegex(t.Conflict, 'LOCAL_CHANGE_CONFLICT'):
            self.apply({'one.md': b'new'})
        self.assertEqual((self.root / 'one.md').read_bytes(), b'user edit')
        (self.root / 'one.md').write_bytes(b'old')
        (self.root / 'new.md').write_bytes(b'unmanaged')
        with self.assertRaisesRegex(t.Conflict, 'LOCAL_CHANGE_CONFLICT'):
            self.apply({'one.md': b'new', 'new.md': b'claimed'})
        self.assertEqual((self.root / 'one.md').read_bytes(), b'old')

    def test_revision_conflict_and_lock_contention(self):
        stale = self.engine.snapshot()[1]
        self.apply({'one.md': b'old'})
        with self.assertRaisesRegex(t.Conflict, 'REVISION_CHANGED'):
            self.engine.apply('2', {'one.md': b'new'}, stale)
        with self.engine.locked():
            command = "import sys;sys.path.insert(0,sys.argv[1]);import install_transaction as t;e=t.ManagedFiles(sys.argv[2]);e.apply('2',{},e.snapshot()[1])"
            r = subprocess.run([sys.executable, '-c', command, str(TOOL), str(self.root)], capture_output=True)
            self.assertNotEqual(r.returncode, 0)
            self.assertIn(b'SCOPE_BUSY', r.stderr)

    @unittest.skipIf(os.name == 'nt', 'POSIX executable bit is not a Windows assertion')
    def test_local_permission_change_is_not_overwritten(self):
        self.apply({'a.md': b'old'})
        (self.root / 'a.md').chmod(0o755)
        with self.assertRaisesRegex(t.Conflict, 'LOCAL_MODE_CONFLICT'):
            self.apply({'a.md': b'new'}, '2')
        self.assertEqual((self.root / 'a.md').read_bytes(), b'old')
        self.assertEqual((self.root / 'a.md').stat().st_mode & 0o777, 0o755)

    def test_crash_at_each_boundary_restores_whole_set(self):
        for point in ('staged', 'committing', 'file:a.md', 'file:b.md', 'file:c.md', 'ledger'):
            with self.subTest(point=point), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp).resolve()
                engine = t.ManagedFiles(root)
                engine.apply('1', {'a.md': b'old', 'b.md': b'delete'}, engine.snapshot()[1])
                old = engine.snapshot()
                def crash(where):
                    if where == point:
                        raise SimulatedCrash()
                with self.assertRaises(SimulatedCrash):
                    engine.apply('2', {'a.md': b'new', 'c.md': b'added'}, old[1], crash)
                with self.assertRaisesRegex(t.Conflict, 'RECOVERY_REQUIRED'):
                    engine.apply('2', {}, engine.snapshot()[1])
                recovered = t.ManagedFiles(root).recover()
                self.assertEqual(recovered['status'], 'bytes_restored')
                self.assertEqual(engine.snapshot(), old)
                self.assertEqual((root / 'a.md').read_bytes(), b'old')
                self.assertEqual((root / 'b.md').read_bytes(), b'delete')
                self.assertFalse((root / 'c.md').exists())
                self.assertEqual(engine.recover()['status'], 'nothing_pending')

    def test_user_edit_after_crash_is_preserved(self):
        self.apply({'a.md': b'old', 'b.md': b'old'})
        def crash(point):
            if point == 'file:b.md':
                raise SimulatedCrash()
        with self.assertRaises(SimulatedCrash):
            self.apply({'a.md': b'new', 'b.md': b'new'}, '2', crash)
        (self.root / 'b.md').write_bytes(b'user edit')
        with self.assertRaisesRegex(t.Conflict, 'RECOVERY_FILE_CONFLICT'):
            self.engine.recover()
        self.assertEqual((self.root / 'a.md').read_bytes(), b'new')
        self.assertEqual((self.root / 'b.md').read_bytes(), b'user edit')

    def test_symlink_and_portable_path_rejections(self):
        (self.root / 'outside').mkdir()
        (self.root / 'linked').symlink_to(self.root / 'outside', target_is_directory=True)
        with self.assertRaisesRegex(t.Conflict, 'SYMLINK_REJECTED'):
            self.apply({'linked/file.md': b'x'})
        for files in ({'../escape': b'x'}, {'NUL.txt': b'x'}, {'A': b'x', 'a': b'y'},
                      {'a': b'x', 'A/file': b'y'}, {t.META + '/ledger.json': b'x'}):
            with self.subTest(paths=list(files)), self.assertRaises(t.Conflict):
                self.apply(files)
        self.assertFalse((self.root / 'outside/file.md').exists())

    def test_staging_corruption_blocks_commit_and_recovers(self):
        self.apply({'a.md': b'old'})
        def corrupt(point):
            if point == 'staged':
                journal = self.engine._pending()
                (self.engine.meta / journal['id'] / 'after/a.md').write_bytes(b'corrupted')
        with self.assertRaisesRegex(t.Conflict, 'STAGED_BYTES_CHANGED'):
            self.apply({'a.md': b'new'}, '2', corrupt)
        self.assertEqual((self.root / 'a.md').read_bytes(), b'old')
        self.assertEqual(self.engine.recover()['status'], 'bytes_restored')

    def test_process_exit_releases_lock_and_restores_original(self):
        self.apply({'a.md': b'old', 'b.md': b'delete'})
        before = self.engine.snapshot()
        code = """import os,sys
sys.path.insert(0,sys.argv[1])
import install_transaction as t
e=t.ManagedFiles(sys.argv[2])
def stop(point):
    if point == 'file:a.md': os._exit(77)
e.apply('2',{'a.md':b'new','c.md':b'added'},e.snapshot()[1],stop)
"""
        r = subprocess.run([sys.executable, '-c', code, str(TOOL), str(self.root)], capture_output=True)
        self.assertEqual(r.returncode, 77, r.stderr)
        self.assertEqual(self.engine.recover()['status'], 'bytes_restored')
        self.assertEqual(self.engine.snapshot(), before)
        self.assertEqual((self.root / 'a.md').read_bytes(), b'old')
        self.assertEqual((self.root / 'b.md').read_bytes(), b'delete')
        self.assertFalse((self.root / 'c.md').exists())

    def test_interrupted_recovery_can_resume(self):
        self.apply({'a.md': b'old', 'b.md': b'old'})
        before = self.engine.snapshot()
        def stop_apply(point):
            if point == 'ledger': raise SimulatedCrash()
        with self.assertRaises(SimulatedCrash):
            self.apply({'a.md': b'new', 'b.md': b'new'}, '2', stop_apply)
        def stop_recover(point):
            if point == 'file:a.md': raise SimulatedCrash()
        with self.assertRaises(SimulatedCrash):
            self.engine.recover(stop_recover)
        self.assertEqual((self.root / 'a.md').read_bytes(), b'old')
        self.assertEqual((self.root / 'b.md').read_bytes(), b'new')
        self.assertEqual(self.engine.recover()['status'], 'bytes_restored')
        self.assertEqual(self.engine.snapshot(), before)
        self.assertEqual((self.root / 'b.md').read_bytes(), b'old')

    def test_bad_backup_refuses_before_any_recovery_write(self):
        self.apply({'a.md': b'old', 'b.md': b'old'})
        def stop(point):
            if point == 'ledger': raise SimulatedCrash()
        with self.assertRaises(SimulatedCrash):
            self.apply({'a.md': b'new', 'b.md': b'new'}, '2', stop)
        journal = self.engine._pending()
        (self.engine.meta / journal['id'] / 'before/b.md').write_bytes(b'corrupted')
        with self.assertRaisesRegex(t.Conflict, 'BACKUP_CHANGED'):
            self.engine.recover()
        self.assertEqual((self.root / 'a.md').read_bytes(), b'new')
        self.assertEqual((self.root / 'b.md').read_bytes(), b'new')

    def test_edit_during_commit_is_not_reported_as_applied(self):
        self.apply({'a.md': b'old', 'b.md': b'old'})
        before = self.engine.snapshot()
        def edit(point):
            if point == 'file:b.md':
                (self.root / 'a.md').write_bytes(b'user edit')
        with self.assertRaisesRegex(t.Conflict, 'LOCAL_CHANGE_CONFLICT'):
            self.apply({'a.md': b'new', 'b.md': b'new'}, '2', edit)
        self.assertEqual(self.engine.snapshot(), before)
        self.assertEqual((self.root / 'a.md').read_bytes(), b'user edit')
        with self.assertRaisesRegex(t.Conflict, 'RECOVERY_FILE_CONFLICT'):
            self.engine.recover()

    @unittest.skipIf(os.name == 'nt', 'POSIX executable bit is not a Windows assertion')
    def test_executable_install_and_mode_rollback_really_execute(self):
        script = b'#!/bin/sh\nexit 0\n'
        first = self.engine.apply('1', {'bin/helper': script}, self.engine.snapshot()[1],
                                  modes={'bin/helper': 0o755})
        self.assertFalse(first['host_verified'])
        self.assertEqual(subprocess.run([str(self.root / 'bin/helper')]).returncode, 0)
        before = self.engine.snapshot()
        def crash(point):
            if point == 'ledger': raise SimulatedCrash()
        with self.assertRaises(SimulatedCrash):
            self.engine.apply('2', {'bin/helper': script}, before[1], crash, modes={'bin/helper': 0o644})
        self.assertEqual((self.root / 'bin/helper').stat().st_mode & 0o777, 0o644)
        self.engine.recover()
        self.assertEqual(self.engine.snapshot(), before)
        self.assertEqual((self.root / 'bin/helper').stat().st_mode & 0o777, 0o755)
        self.assertEqual(subprocess.run([str(self.root / 'bin/helper')]).returncode, 0)

    def test_release_path_is_immutable_but_selection_can_grow(self):
        self.apply({'a.md': b'a'}, '1')
        before = self.engine.snapshot()
        with self.assertRaisesRegex(t.Conflict, 'IMMUTABLE_RELEASE_CONFLICT'):
            self.apply({'a.md': b'changed'}, '1')
        self.assertEqual(self.engine.snapshot(), before)
        self.apply({'a.md': b'a', 'b.md': b'b'}, '1')
        self.apply({'b.md': b'b'}, '1')
        self.apply({'b.md': b'new'}, '2')
        with self.assertRaisesRegex(t.Conflict, 'IMMUTABLE_RELEASE_CONFLICT'):
            self.apply({'a.md': b'changed'}, '1')
        self.assertEqual((self.root / 'b.md').read_bytes(), b'new')

    def test_modes_reject_privilege_bits_and_incomplete_maps(self):
        for modes in ({}, {'a.md': 0o777}, {'a.md': 0o4755}, {'a.md': True}, {'other': 0o644}):
            with self.subTest(modes=modes), self.assertRaises(t.Conflict):
                self.engine.apply('1', {'a.md': b'a'}, self.engine.snapshot()[1], modes=modes)
        self.assertFalse((self.root / 'a.md').exists())
        self.apply({'a.md': b'a'}, '1')
        with self.assertRaisesRegex(t.Conflict, 'IMMUTABLE_RELEASE_CONFLICT'):
            self.engine.apply('1', {'a.md': b'a'}, self.engine.snapshot()[1], modes={'a.md': 0o755})

    def test_explicit_cleanup_old_transaction_preserves_current_and_unknown(self):
        first = self.apply({'a.md': b'a'}, '1')['transaction']
        self.apply({'a.md': b'b'}, '2')
        (self.root / 'user.md').write_bytes(b'personal')
        revision = self.engine.snapshot()[1]
        self.assertEqual(self.engine.cleanup(first, revision)['status'], 'staging_removed')
        self.assertFalse((self.engine.meta / first).exists())
        self.assertEqual((self.root / 'a.md').read_bytes(), b'b')
        self.assertEqual((self.root / 'user.md').read_bytes(), b'personal')
        self.assertEqual(self.engine.cleanup(first, revision)['status'], 'already_cleaned')

    def test_cleanup_unknown_staging_file_and_symlink_preserved(self):
        txn = self.apply({'a.md': b'a'}, '1')['transaction']
        folder = self.engine.meta / txn
        extra = folder / 'user.txt'
        extra.write_bytes(b'personal')
        with self.assertRaisesRegex(t.Conflict, 'UNKNOWN_STAGING_FILE'):
            self.engine.cleanup(txn, self.engine.snapshot()[1])
        self.assertTrue((folder / 'after/a.md').is_file())
        self.assertEqual(extra.read_bytes(), b'personal')
        extra.unlink()
        extra.symlink_to(self.root / 'a.md')
        with self.assertRaisesRegex(t.Conflict, 'SYMLINK_REJECTED'):
            self.engine.cleanup(txn, self.engine.snapshot()[1])
        self.assertTrue(extra.is_symlink())

    def test_cleanup_refuses_pending_transaction(self):
        def crash(point):
            if point == 'file:a.md': raise SimulatedCrash()
        with self.assertRaises(SimulatedCrash):
            self.apply({'a.md': b'a'}, '1', crash)
        txn = self.engine._pending()['id']
        with self.assertRaisesRegex(t.Conflict, 'RECOVERY_REQUIRED'):
            self.engine.cleanup(txn, self.engine.snapshot()[1])
        self.assertTrue((self.engine.meta / txn / 'after/a.md').is_file())

    def test_real_exit_during_cleanup_is_resumable(self):
        txn = self.apply({'a.md': b'a', 'b.md': b'b'}, '1')['transaction']
        revision = self.engine.snapshot()[1]
        code = """import os,sys
sys.path.insert(0,sys.argv[1])
import install_transaction as t
e=t.ManagedFiles(sys.argv[2])
def stop(point):
    if point == 'file:after/a.md': os._exit(78)
e.cleanup(sys.argv[3],e.snapshot()[1],stop)
"""
        r = subprocess.run([sys.executable, '-c', code, str(TOOL), str(self.root), txn], capture_output=True)
        self.assertEqual(r.returncode, 78, r.stderr)
        self.assertFalse((self.engine.meta / txn / 'after/a.md').exists())
        self.assertEqual(self.engine.cleanup(txn, revision)['status'], 'staging_removed')
        self.assertEqual((self.root / 'a.md').read_bytes(), b'a')
        self.assertEqual((self.root / 'b.md').read_bytes(), b'b')

    def test_edit_during_recovery_keeps_failure_and_user_bytes(self):
        self.apply({'a.md': b'a', 'b.md': b'b'}, '1')
        def crash(point):
            if point == 'ledger': raise SimulatedCrash()
        with self.assertRaises(SimulatedCrash):
            self.apply({'a.md': b'new a', 'b.md': b'new b'}, '2', crash)
        def edit(point):
            if point == 'file:b.md': (self.root / 'a.md').write_bytes(b'user edit')
        with self.assertRaisesRegex(t.Conflict, 'LOCAL_CHANGE_CONFLICT'):
            self.engine.recover(edit)
        self.assertEqual(self.engine._pending()['state'], 'committing')
        self.assertEqual((self.root / 'a.md').read_bytes(), b'user edit')

    def test_empty_files_are_owned_and_distinct_from_absent(self):
        self.apply({'empty.py': b''}, '1')
        self.assertTrue((self.root / 'empty.py').is_file())
        self.assertEqual(self.apply({'empty.py': b''}, '1')['status'], 'unchanged')
        self.apply({}, '2')
        self.assertFalse((self.root / 'empty.py').exists())

    def test_new_directory_during_cleanup_is_not_deleted(self):
        txn = self.apply({'a.md': b'a'}, '1')['transaction']
        extra = self.engine.meta / txn / 'user-directory'
        def create(point):
            if point == 'cleaning': extra.mkdir()
        with self.assertRaisesRegex(t.Conflict, 'UNKNOWN_STAGING_DIRECTORY'):
            self.engine.cleanup(txn, self.engine.snapshot()[1], create)
        self.assertTrue(extra.is_dir())
        self.assertEqual((self.root / 'a.md').read_bytes(), b'a')

    def test_final_ledger_boundary_edit_does_not_report_success(self):
        self.apply({'a.md': b'a'}, '1')
        def edit(point):
            if point == 'ledger': (self.root / 'a.md').write_bytes(b'user edit')
        with self.assertRaisesRegex(t.Conflict, 'LOCAL_CHANGE_CONFLICT'):
            self.apply({'a.md': b'b'}, '2', edit)
        self.assertEqual(self.engine._pending()['state'], 'committing')
        self.assertEqual((self.root / 'a.md').read_bytes(), b'user edit')

    def test_foreign_platform_and_old_schema_are_not_reinterpreted(self):
        for field, value in (('platform', 'foreign'), ('schema', 'vf.skill-files.transaction/v1')):
            ledger = t.empty_ledger()
            ledger[field] = value
            self.engine.ledger.write_bytes(t.encode(ledger))
            with self.subTest(field=field), self.assertRaisesRegex(t.Conflict, 'INVALID_LEDGER'):
                self.engine.snapshot()


if __name__ == '__main__':
    unittest.main()
