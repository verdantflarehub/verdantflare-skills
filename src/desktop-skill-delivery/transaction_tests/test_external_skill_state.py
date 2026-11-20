"""External recovery records must never become discoverable Skill copies."""
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))
import install_transaction as tx


@unittest.skipUnless(os.name == 'posix', 'Windows storage has a separate plan')
class ExternalSkillStateTests(unittest.TestCase):
    def setUp(self):
        base = TOOLS.parents[2] / '.tmp/external-skill-state-tests'
        base.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=base)
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / 'skills'
        self.root.mkdir(mode=0o700)
        self.state = self.base / 'state'
        self.engine = tx.ManagedFiles(self.root, metadata_root=self.state)

    def test_backups_never_enter_discovery_root_and_reopen_keeps_ownership(self):
        payload = {'example/SKILL.md': b'first', 'example/skill.json': b'{}'}
        self.engine.apply('release.1', payload, self.engine.snapshot()[1])
        reopened = tx.ManagedFiles(self.root, metadata_root=self.state)
        self.assertEqual(reopened.snapshot(), self.engine.snapshot())
        self.assertEqual({p.relative_to(self.root).as_posix() for p in self.root.rglob('*') if p.is_file()},
                         set(payload))
        self.assertFalse((self.root / tx.META).exists())
        self.assertTrue(list(self.state.glob('*/after/example/SKILL.md')))
        reopened.remove(reopened.snapshot()[1])
        self.assertFalse((self.root / 'example/SKILL.md').exists())

    def test_persistent_interruption_recovers_original_payload_in_same_external_state(self):
        first = {'example/SKILL.md': b'first'}
        self.engine.apply('release.1', first, self.engine.snapshot()[1])
        class Stop(BaseException):
            pass
        # The committing boundary is persistent and has staged original bytes.
        def stop_at_committing(phase):
            if phase == 'committing':
                raise Stop()
        with self.assertRaises(Stop):
            self.engine.apply('release.2', {'example/SKILL.md': b'second'},
                              self.engine.snapshot()[1], checkpoint=stop_at_committing)
        restored = tx.ManagedFiles(self.root, metadata_root=self.state)
        restored.recover()
        self.assertEqual((self.root / 'example/SKILL.md').read_bytes(), first['example/SKILL.md'])
        self.assertEqual(restored.snapshot()[0]['release'], 'release.1')

    def test_different_skill_root_cannot_reuse_external_records(self):
        other = self.base / 'other'
        other.mkdir(mode=0o700)
        with self.assertRaisesRegex(tx.Conflict, 'TRANSACTION_STATE_BINDING_CHANGED'):
            tx.ManagedFiles(other, metadata_root=self.state)

    def test_copied_state_cannot_be_rebound_to_new_location(self):
        other = self.base / 'other-state'
        shutil.copytree(self.state, other)
        other.chmod(0o700)
        with self.assertRaisesRegex(tx.Conflict, 'TRANSACTION_STATE_BINDING_CHANGED'):
            tx.ManagedFiles(self.root, metadata_root=other)

    def test_deleted_binding_does_not_adopt_existing_journal(self):
        self.engine.apply('release.1', {'example/SKILL.md': b'first'}, self.engine.snapshot()[1])
        (self.state / 'binding.json').unlink()
        with self.assertRaisesRegex(tx.Conflict, 'TRANSACTION_STATE_BINDING_CHANGED'):
            self.engine.snapshot()
        with self.assertRaisesRegex(tx.Conflict, 'UNBOUND_TRANSACTION_STATE'):
            tx.ManagedFiles(self.root, metadata_root=self.state)
        self.assertFalse((self.state / 'binding.json').exists())

    def test_binding_change_blocks_snapshot_and_pending(self):
        marker = self.state / 'binding.json'
        marker.write_bytes(b'{}')
        for operation in (self.engine.snapshot, self.engine._pending):
            with self.assertRaisesRegex(tx.Conflict, 'TRANSACTION_STATE_BINDING_CHANGED'):
                operation()

    def test_overlap_links_and_non_private_state_are_rejected(self):
        for bad in (self.root, self.root / 'nested', self.base):
            with self.subTest(kind=bad.name), self.assertRaisesRegex(tx.Conflict, 'TRANSACTION_STATE_OVERLAP'):
                tx.ManagedFiles(self.root, metadata_root=bad)
        linked = self.base / 'linked'
        linked.symlink_to(self.state, target_is_directory=True)
        with self.assertRaisesRegex(tx.Conflict, 'SYMLINK'):
            tx.ManagedFiles(self.root, metadata_root=linked)
        self.state.chmod(0o755)
        with self.assertRaisesRegex(tx.Conflict, 'PRIVATE_DIRECTORY_REQUIRED'):
            self.engine.snapshot()
