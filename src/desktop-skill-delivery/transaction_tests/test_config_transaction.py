"""Actual isolated config files and process exits; no real host configuration."""
from pathlib import Path
import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))
import config_transaction as t

BASE = Path(__file__).resolve().parents[4] / '.tmp/config-transaction-tests'


class SimulatedCrash(BaseException):
    pass


@unittest.skipUnless(os.name == 'posix', 'Windows ACL implementation is not yet available')
class ConfigTransactionTests(unittest.TestCase):
    def setUp(self):
        BASE.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=BASE)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).absolute()
        self.path = self.root / 'mcp.json'
        self.engine = t.ConfigFile(self.root, 'mcp.json')

    def seed(self, raw=b'{ "mcpServers": { "other": {"url":"keep"} }, "theme":"dark" }\r\n', mode=0o640):
        self.path.write_bytes(raw)
        self.path.chmod(mode)
        return raw

    def apply(self, value, checkpoint=lambda _: None):
        return self.engine.apply(value, expected_revision=self.engine.snapshot()[1],
                                 expected_file=self.engine._file()[1], checkpoint=checkpoint)

    def fail_at(self, target):
        def crash(point):
            if point == target:
                raise SimulatedCrash()
        return crash

    def test_install_update_delete_preserve_user_edits_and_original_mode(self):
        before = self.seed()
        first = self.apply({'url':'https://first.invalid'})
        self.assertEqual(first['status'], 'config_bytes_applied')
        self.assertFalse(first['host_verified'])
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o640)
        # User edits outside the owned VF node are included in the next preflight.
        raw = self.path.read_bytes().replace(b'"theme":"dark"', b'"theme":"light"')
        self.path.write_bytes(raw)
        self.apply({'url':'https://second.invalid'})
        self.assertEqual(json.loads(self.path.read_bytes())['theme'], 'light')
        self.apply(None)
        self.assertEqual(self.path.read_bytes(), before.replace(b'"theme":"dark"', b'"theme":"light"'))
        self.assertIsNone(self.engine.snapshot()[0]['owned_sha256'])

    def test_absent_noop_new_file_private_mode_and_owned_noop(self):
        self.assertEqual(self.apply(None)['status'], 'unchanged')
        self.assertFalse(self.path.exists())
        self.apply({'url':'https://example.invalid'})
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        snapshot = self.engine.snapshot()
        self.assertEqual(self.apply({'url':'https://example.invalid'})['status'], 'unchanged')
        self.assertEqual(snapshot, self.engine.snapshot())

    def test_unowned_and_user_modified_vf_entries_are_never_adopted(self):
        raw = self.seed(b'{"mcpServers":{"vf-studio":{"url":"user"}}}')
        with self.assertRaisesRegex(t.config.ConfigConflict, 'OWNERSHIP_CONFLICT'):
            self.apply({'url':'user'})
        self.assertEqual(self.path.read_bytes(), raw)
        self.path.unlink()
        self.apply({'url':'owned'})
        edited = self.path.read_bytes().replace(b'owned', b'edited')
        self.path.write_bytes(edited)
        with self.assertRaisesRegex(t.config.ConfigConflict, 'OWNERSHIP_CONFLICT'):
            self.apply(None)
        self.assertEqual(self.path.read_bytes(), edited)

    def test_stale_file_and_ledger_preflight_rejected(self):
        self.seed()
        revision = self.engine.snapshot()[1]
        observed = self.engine.observe()['file']
        self.path.write_bytes(self.path.read_bytes()+b'\n')
        with self.assertRaisesRegex(t.io.Conflict, 'CONFIG_CHANGED'):
            self.engine.apply({}, expected_revision=revision, expected_file=observed)
        self.apply({})
        with self.assertRaisesRegex(t.io.Conflict, 'REVISION_CHANGED'):
            self.engine.apply({}, expected_revision=revision, expected_file=self.engine._file()[1])

    def test_recover_each_persistent_boundary_existing_and_absent_original(self):
        for existing in (False, True):
            for point in ('preparing', 'before_backup', 'after_backup', 'staged', 'writing', 'config', 'ledger'):
                with self.subTest(existing=existing, point=point), tempfile.TemporaryDirectory(dir=BASE) as tmp:
                    root = Path(tmp).absolute(); path = root/'mcp.json'
                    original = b'{ "other": "original" }\r\n' if existing else None
                    if original:
                        path.write_bytes(original); path.chmod(0o644)
                    engine = t.ConfigFile(root, 'mcp.json'); ledger = engine.snapshot()
                    with self.assertRaises(SimulatedCrash):
                        engine.apply({'url':'new'}, expected_revision=ledger[1],
                                     expected_file=engine._file()[1], checkpoint=self.fail_at(point))
                    with self.assertRaisesRegex(t.io.Conflict, 'RECOVERY_OR_CLEANUP_REQUIRED'):
                        engine.apply({}, expected_revision=engine.snapshot()[1], expected_file={'sha256':None,'mode':None,'gid':None,'provenance_sha256':None})
                    result = t.ConfigFile(root, 'mcp.json').recover()
                    self.assertEqual(result['status'], 'config_bytes_restored')
                    self.assertEqual(path.read_bytes() if path.exists() else None, original)
                    self.assertEqual(engine.snapshot(), ledger)
                    if original:
                        self.assertEqual(path.stat().st_mode & 0o777, 0o644)
                    self.assertEqual(engine.recover()['status'], 'nothing_pending')
                    self.assertEqual(engine.cleanup(result['transaction'],expected_revision=engine.snapshot()[1])['status'],'private_backups_removed')

    def test_real_process_exit_releases_lock_and_recovers_same_transaction(self):
        before = self.seed(); ledger = self.engine.snapshot()
        code = '''import sys,os
sys.path.insert(0,sys.argv[1]);import config_transaction as t
e=t.ConfigFile(sys.argv[2],'mcp.json')
def stop(point):
 if point=='config': os._exit(77)
e.apply({'url':'new'},expected_revision=e.snapshot()[1],expected_file=e._file()[1],checkpoint=stop)
'''
        proc = subprocess.run([sys.executable,'-c',code,str(TOOL),str(self.root)], capture_output=True)
        self.assertEqual(proc.returncode, 77, proc.stderr)
        identifier = self.engine._active()['id']
        recovered = t.ConfigFile(self.root, 'mcp.json').recover()
        self.assertEqual(recovered['transaction'], identifier)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(self.engine.snapshot(), ledger)

    def test_recovery_can_be_interrupted_and_resumed(self):
        for point in ('config','ledger'):
            with self.subTest(point=point), tempfile.TemporaryDirectory(dir=BASE) as tmp:
                root=Path(tmp).absolute();path=root/'mcp.json';path.write_bytes(b'{"user":1}');path.chmod(0o600)
                engine=t.ConfigFile(root,'mcp.json');before=engine.snapshot()
                with self.assertRaises(SimulatedCrash):
                    engine.apply({},expected_revision=before[1],expected_file=engine._file()[1],checkpoint=self.fail_at('ledger'))
                with self.assertRaises(SimulatedCrash):
                    engine.recover(self.fail_at(point))
                self.assertEqual(engine.recover()['status'],'config_bytes_restored')
                self.assertEqual(path.read_bytes(),b'{"user":1}')
                self.assertEqual(engine.snapshot(),before)

    def test_user_edit_after_staging_or_crash_is_preserved(self):
        before=self.seed()
        def edit(point):
            if point=='writing': self.path.write_bytes(b'{"user":"changed"}')
        with self.assertRaisesRegex(t.io.Conflict,'CONFIG_CHANGED'):
            self.apply({},edit)
        with self.assertRaisesRegex(t.io.Conflict,'RECOVERY_CONFIG_CONFLICT'):
            self.engine.recover()
        self.assertEqual(self.path.read_bytes(),b'{"user":"changed"}')
        # Explicit test-fixture restoration allows recovery; the engine never does it.
        self.path.write_bytes(before)
        self.engine.recover()
        with self.assertRaises(SimulatedCrash): self.apply({},self.fail_at('config'))
        self.path.write_bytes(b'{"user":"after-crash"}')
        with self.assertRaisesRegex(t.io.Conflict,'RECOVERY_CONFIG_CONFLICT'): self.engine.recover()
        self.assertEqual(self.path.read_bytes(),b'{"user":"after-crash"}')

    def test_corrupt_backups_block_restore_before_any_live_write(self):
        self.seed()
        with self.assertRaises(SimulatedCrash): self.apply({},self.fail_at('config'))
        current=self.path.read_bytes();journal=self.engine._active()
        (self.engine._folder(journal['id'])/'before.bin').write_bytes(b'corrupt')
        with self.assertRaisesRegex(t.io.Conflict,'BACKUP_CHANGED'): self.engine.recover()
        self.assertEqual(self.path.read_bytes(),current)

    def test_staging_corruption_and_mode_changes_block_commit(self):
        before=self.seed()
        def corrupt(point):
            if point=='staged':
                folder=self.engine._folder(self.engine._active()['id'])
                (folder/'after.bin').write_bytes(b'corrupt')
        with self.assertRaisesRegex(t.io.Conflict,'STAGED_BYTES_CHANGED'): self.apply({},corrupt)
        self.assertEqual(self.path.read_bytes(),before)
        journal=self.engine._active(); folder=self.engine._folder(journal['id'])
        # Separate fresh fixture for a mode-only external edit.
        with tempfile.TemporaryDirectory(dir=BASE) as tmp:
            root=Path(tmp).absolute();path=root/'mcp.json';path.write_bytes(b'{}');path.chmod(0o600)
            engine=t.ConfigFile(root,'mcp.json')
            def change_mode(point):
                if point=='writing': path.chmod(0o644)
            with self.assertRaisesRegex(t.io.Conflict,'CONFIG_CHANGED'):
                engine.apply({},expected_revision=engine.snapshot()[1],expected_file=engine._file()[1],checkpoint=change_mode)
            self.assertEqual(path.read_bytes(),b'{}')
            self.assertEqual(path.stat().st_mode&0o777,0o644)

    def test_private_state_and_backups_never_emit_secret_content(self):
        marker=b'SYNTHETIC_SECRET_NOT_FOR_LOGS'
        self.seed(b'{"other":"'+marker+b'"}')
        result=self.apply({'credential_ref':'reference-only'})
        folder=self.engine._folder(result['transaction'])
        self.assertIn(marker,(folder/'before.bin').read_bytes())
        self.assertNotIn(marker,json.dumps(result).encode())
        self.assertNotIn(marker,(folder/'journal.json').read_bytes())
        self.assertNotIn(marker,self.engine.ledger_path.read_bytes())
        for path in self.engine.meta.rglob('*'):
            self.assertEqual(path.stat().st_mode&0o777,0o700 if path.is_dir() else 0o600)
        self.engine.cleanup(result['transaction'],expected_revision=self.engine.snapshot()[1])
        self.assertFalse((folder/'before.bin').exists())
        self.assertFalse((folder/'after.bin').exists())

    def test_unsafe_modes_links_and_private_state_are_refused_without_chmod(self):
        self.seed(mode=0o664)
        with self.assertRaisesRegex(t.io.Conflict,'CONFIG_PERMISSIONS_UNSUPPORTED'): self.apply({})
        self.assertEqual(self.path.stat().st_mode&0o777,0o664)
        self.path.chmod(0o600)
        os.link(self.path,self.root/'hardlink')
        with self.assertRaisesRegex(t.io.Conflict,'CONFIG_PERMISSIONS_UNSUPPORTED'): self.apply({})
        (self.root/'hardlink').unlink()
        self.path.unlink(); (self.root/'real.json').write_bytes(b'{}');self.path.symlink_to(self.root/'real.json')
        with self.assertRaisesRegex(t.io.Conflict,'SYMLINK_REJECTED'): self.apply({})
        self.path.unlink()
        self.engine.meta.chmod(0o755)
        with self.assertRaisesRegex(t.io.Conflict,'PRIVATE_DIRECTORY_REQUIRED'): self.engine.snapshot()
        self.assertEqual(self.engine.meta.stat().st_mode&0o777,0o755)

    def test_cleanup_unknown_content_and_interruption_are_safe(self):
        self.seed();result=self.apply({});identifier=result['transaction'];folder=self.engine._folder(identifier)
        extra=folder/'unknown';extra.write_bytes(b'user')
        with self.assertRaisesRegex(t.io.Conflict,'UNKNOWN_TRANSACTION_CONTENT'):
            self.engine.cleanup(identifier,expected_revision=self.engine.snapshot()[1])
        self.assertEqual(extra.read_bytes(),b'user');extra.unlink()
        with self.assertRaises(SimulatedCrash):
            self.engine.cleanup(identifier,expected_revision=self.engine.snapshot()[1],checkpoint=self.fail_at('before'))
        with self.assertRaisesRegex(t.io.Conflict,'RECOVERY_OR_CLEANUP_REQUIRED'): self.apply({'x':1})
        self.assertEqual(self.engine.cleanup(identifier,expected_revision=self.engine.snapshot()[1])['status'],'private_backups_removed')
        self.assertEqual(self.engine.cleanup(identifier,expected_revision=self.engine.snapshot()[1])['status'],'already_cleaned')
        self.apply({'x':1})

    def test_lock_contention_and_bad_ledger_are_refused(self):
        with self.engine.locked():
            code="import sys;sys.path.insert(0,sys.argv[1]);import config_transaction as t;e=t.ConfigFile(sys.argv[2],'mcp.json');e.apply({},expected_revision=e.snapshot()[1],expected_file={'sha256':None,'mode':None,'gid':None,'provenance_sha256':None})"
            proc=subprocess.run([sys.executable,'-c',code,str(TOOL),str(self.root)],capture_output=True)
            self.assertNotEqual(proc.returncode,0);self.assertIn(b'SCOPE_BUSY',proc.stderr)
        self.engine._write_private(self.engine.ledger_path,b'{"schema":"wrong"}')
        with self.assertRaisesRegex(t.io.Conflict,'INVALID_LEDGER'): self.engine.snapshot()
        self.assertFalse(self.path.exists())

    def test_windows_unverified_private_storage_is_refused_before_creation(self):
        target=self.root/'not-created'
        with patch.object(t.os,'name','nt'),self.assertRaisesRegex(t.io.Conflict,'WINDOWS_PRIVATE_STORAGE_UNVERIFIED'):
            t.ConfigFile(target,'mcp.json')
        self.assertFalse(target.exists())

    @unittest.skipUnless(hasattr(os,'setxattr') or sys.platform=='darwin','extended attributes unavailable')
    def test_extended_attributes_on_config_are_not_silently_dropped(self):
        self.seed()
        name='user.vf-test'
        if sys.platform=='darwin':
            proc=subprocess.run(['/usr/bin/xattr','-w',name,'fixture',str(self.path)],capture_output=True)
            self.assertEqual(proc.returncode,0)
        else:
            try: os.setxattr(self.path,name,b'fixture')
            except OSError as error: self.skipTest('test filesystem does not support xattrs: '+str(error.errno))
        with self.assertRaisesRegex(t.io.Conflict,'EXTENDED_METADATA_UNSUPPORTED'): self.apply({})
        if sys.platform=='darwin':
            proc=subprocess.run(['/usr/bin/xattr','-p',name,str(self.path)],capture_output=True)
            self.assertEqual(proc.stdout.strip(),b'fixture')
        else: self.assertEqual(os.getxattr(self.path,name),b'fixture')

    @unittest.skipUnless(sys.platform=='darwin','macOS ACL check')
    def test_extended_acl_refuses_replacement(self):
        self.seed()
        proc=subprocess.run(['/bin/chmod','+a','everyone allow read',str(self.path)],capture_output=True)
        self.assertEqual(proc.returncode,0)
        with self.assertRaisesRegex(t.io.Conflict,'EXTENDED_ACL_UNSUPPORTED'): self.apply({})

    def test_crash_after_commit_is_not_rolled_back(self):
        self.seed()
        with self.assertRaises(SimulatedCrash): self.apply({'url':'committed'},self.fail_at('committed'))
        committed=self.path.read_bytes();ledger=self.engine.snapshot()
        self.assertEqual(self.engine.recover()['status'],'nothing_pending')
        self.assertEqual(self.path.read_bytes(),committed)
        self.assertEqual(self.engine.snapshot(),ledger)
        self.assertEqual(self.apply({'url':'committed'})['status'],'unchanged')

    def test_existing_group_is_not_silently_changed(self):
        before=self.seed()
        original=self.path.stat().st_gid
        alternatives=[group for group in os.getgroups() if group!=original]
        if not alternatives: self.skipTest('No supplementary group available for native test')
        try: os.chown(self.path,-1,alternatives[0])
        except PermissionError: self.skipTest('Native fixture group change not allowed')
        with self.assertRaisesRegex(t.io.Conflict,'CONFIG_GROUP_CANNOT_PRESERVE'): self.apply({})
        self.assertEqual(self.path.read_bytes(),before)
        self.assertEqual(self.path.stat().st_gid,alternatives[0])

    def test_stale_permissions_and_case_alias_scope_are_rejected(self):
        self.seed(mode=0o600)
        observation=self.engine.observe()
        self.path.chmod(0o644)
        with self.assertRaisesRegex(t.io.Conflict,'CONFIG_CHANGED'):
            self.engine.apply({},expected_revision=observation['ledger_revision'],expected_file=observation['file'])
        self.assertEqual(self.path.stat().st_mode&0o777,0o644)
        alias=t.ConfigFile(self.root,'MCP.json')
        self.assertEqual(alias.meta,self.engine.meta)
        self.apply({})
        with self.assertRaisesRegex(t.io.Conflict,'INVALID_LEDGER'): alias.snapshot()
        with self.assertRaisesRegex(t.io.Conflict,'INVALID_TARGET'):
            t.ConfigFile(self.root,'.VF-CONFIG-TRANSACTION/ledger.json')

    def test_malformed_journal_is_rejected_without_live_changes(self):
        self.seed()
        with self.assertRaises(SimulatedCrash): self.apply({},self.fail_at('staged'))
        journal=self.engine._active();path=self.engine._folder(journal['id'])/'journal.json'
        before=self.path.read_bytes()
        for bad_state in (None,True,[],{},7,'unknown'):
            changed={**journal,'state':bad_state}
            self.engine._write_private(path,t.io.encode(changed))
            with self.subTest(kind=type(bad_state).__name__),self.assertRaisesRegex(t.io.Conflict,'INVALID_JOURNAL'):
                self.engine.recover()
            self.assertEqual(self.path.read_bytes(),before)


if __name__=='__main__':unittest.main()
