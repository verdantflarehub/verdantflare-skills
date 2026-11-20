"""Isolated reference/rollback protection and actual POSIX inherited leases."""
from pathlib import Path
import os
import select
import subprocess
import sys
import tempfile
import unittest
import uuid

TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))
from runtime_tools import runtime_lifecycle as lc

BASE = TOOL.parents[2] / '.tmp/runtime-lifecycle-tests'


class Crash(BaseException):
    pass


class RuntimeLifecycleTests(unittest.TestCase):
    def setUp(self):
        BASE.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=BASE)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).absolute()
        (self.root / 'runtime').mkdir(mode=0o700)
        self.store = lc.rt.RuntimeStore(self.root / 'runtime')
        self.scopes = []
        for i in range(2):
            paths = [self.root / (name + str(i)) for name in ('state', 'skills', 'config')]
            for path in paths:
                path.mkdir(mode=0o700)
            self.scopes.append(lc.operation.InstallOperation(*paths, 'mcp.json', runtime_root=self.store.root))
        self.manager = lc.RuntimeLifecycle(self.store, self.scopes)
        self.manager.initialize()
        self.trees = {}
        for version in ('1', '2', '3'):
            raw = ('#!/bin/sh\nprintf "version-' + version + ':%s" "$1"\n').encode()
            source = self.root / ('source' + version)
            source.write_bytes(raw)
            manifest = {'schema': lc.rt.SCHEMA, 'release': 'test.' + version,
                        'source_sha256': lc.tx.sha(raw), 'entries': {
                            'bin/tool': {'type': 'file', 'sha256': lc.tx.sha(raw), 'size': len(raw), 'mode': 0o755},
                            'bin/alias': {'type': 'symlink', 'target': 'tool'}}}
            digest = lc.tx.sha(lc.tx.encode(manifest))
            self.manager.stage(manifest, {'bin/tool': source}, expected_digest=digest,
                               transaction_id=uuid.uuid4().hex)
            self.trees[version] = digest

    def key(self, index=0):
        return lc.tx.sha(lc.tx.encode(self.scopes[index].bindings))

    def apply(self, version, index=0, settle=True, checkpoint=lambda _: None):
        scope = self.scopes[index]
        observed = scope.config.observe()
        result = self.manager.apply(self.key(index), 'skill.' + version, {'a.md': version.encode()},
            {'url': 'https://v' + version + '.invalid'},
            expected_skill_revision=scope.skills.snapshot()[1],
            expected_config_revision=observed['ledger_revision'], expected_config_file=observed['file'],
            runtime_digest=self.trees[version], expected_runtime_revision=scope.runtime.snapshot()[1],
            checkpoint=checkpoint)
        if settle:
            self.manager.recover_operation(self.key(index))
        return result

    def collect(self, version, checkpoint=lambda _: None):
        inventory = self.manager.inventory()
        return self.manager.collect(self.trees[version], expected_revision=inventory['revision'], checkpoint=checkpoint)

    def test_quiescent_snapshot_holds_activity_and_every_resource_lock_until_cas_exits(self):
        self.apply('1')
        before = self.manager.inventory()
        with self.manager.quiescent_snapshot(expected_revision=before['revision']) as observed:
            self.assertEqual(observed, before)
            with self.assertRaisesRegex(lc.tx.Conflict, 'RUNTIME_ACTIVITY_BUSY'):
                with self.manager.lease(self.trees['1']):
                    self.fail('activity entered an exclusive maintenance snapshot')
            for resource in [*self.scopes, *[s.skills for s in self.scopes],
                             *[s.config for s in self.scopes], self.store, self.store.pointer]:
                with self.subTest(resource=type(resource).__name__), self.assertRaisesRegex(lc.tx.Conflict, 'SCOPE_BUSY'):
                    with resource.locked():
                        self.fail('resource changed during binding CAS')
        self.assertEqual(self.manager.inventory(), before)
        self.apply('2')
        with self.assertRaisesRegex(lc.tx.Conflict, 'LIFECYCLE_REVISION_CHANGED'):
            with self.manager.quiescent_snapshot(expected_revision=before['revision']):
                self.fail('stale snapshot was accepted')

    @staticmethod
    def crash(phase):
        def stop(actual):
            if actual == phase:
                raise Crash()
        return stop

    def test_fresh_scope_seal_reopen_and_no_adoption_or_changed_scopes(self):
        self.assertEqual(lc.RuntimeLifecycle(self.store, list(reversed(self.scopes))).initialize()['status'], 'scope_verified')
        altered = lc.RuntimeLifecycle(self.store, self.scopes[:1])
        with self.assertRaisesRegex(lc.tx.Conflict, 'LIFECYCLE_SCOPE_CHANGED'):
            altered.initialize()
        with self.assertRaisesRegex(lc.tx.Conflict, 'DUPLICATE_LIFECYCLE_SCOPE'):
            lc.RuntimeLifecycle(self.store, [self.scopes[0], self.scopes[0]])
        seal = self.manager.meta / 'seal.json'
        original = seal.read_bytes()
        seal.unlink()
        with self.assertRaisesRegex(lc.tx.Conflict, 'FRESH_RUNTIME_SCOPE_REQUIRED'):
            self.manager.initialize()
        self.assertFalse(seal.exists())
        seal.write_bytes(original); seal.chmod(0o600)

    def test_multiple_scope_references_and_old_version_collection(self):
        self.apply('1'); self.apply('1', index=1); self.apply('2')
        inventory = self.manager.inventory()
        self.assertTrue(inventory['versions'][self.trees['1']]['references'])
        with self.assertRaisesRegex(lc.tx.Conflict, 'RUNTIME_VERSION_PROTECTED'):
            self.collect('1')
        self.apply('2', index=1)
        result = self.collect('1')
        self.assertEqual(result['status'], 'runtime_collected')
        self.assertFalse((self.store.versions / self.trees['1']).exists())
        self.assertEqual(self.store.verify(self.trees['2'])['status'], 'tree_verified')
        self.assertFalse(result['host_verified'])
        self.assertNotIn(os.fsdecode(self.root), str(result))

    def test_unclean_operation_and_rollback_storage_block_gc(self):
        self.apply('1'); self.apply('2', settle=False)
        inventory = self.manager.inventory()
        self.assertTrue(inventory['blockers'])
        self.assertTrue(inventory['references'][self.trees['1']])
        with self.assertRaisesRegex(lc.tx.Conflict, 'RUNTIME_RECOVERY_REQUIRED'):
            self.collect('3')
        self.manager.recover_operation(self.key())
        self.assertFalse(self.manager.inventory()['blockers'])
        self.assertEqual(self.collect('1')['status'], 'runtime_collected')

    def test_nonterminal_operation_recovers_original_reference(self):
        self.apply('1')
        with self.assertRaises(Crash):
            self.apply('2', settle=False, checkpoint=self.crash('config'))
        with self.assertRaises(lc.tx.Conflict):
            self.collect('3')
        for _ in range(3):
            self.manager.recover_operation(self.key())
        self.assertIn(self.trees['1'], self.manager.inventory()['references'])
        self.assertEqual(self.collect('3')['status'], 'runtime_collected')

    def test_stale_snapshot_unready_version_and_unknown_storage_preserved(self):
        old = self.manager.inventory()['revision']
        self.apply('1')
        with self.assertRaisesRegex(lc.tx.Conflict, 'LIFECYCLE_REVISION_CHANGED'):
            self.manager.collect(self.trees['3'], expected_revision=old)
        digest = self.trees['3']
        path = self.store.versions / digest / 'journal.json'
        journal = lc.tx.decode(path.read_bytes())
        journal['state'] = 'staging'
        path.write_bytes(lc.tx.encode(journal))
        with self.assertRaisesRegex(lc.tx.Conflict, 'RUNTIME_VERSION_PROTECTED'):
            self.collect('3')
        journal['state'] = 'ready'; path.write_bytes(lc.tx.encode(journal))
        orphan = self.store.pointer.meta / ('f' * 32)
        orphan.mkdir(mode=0o700)
        (orphan / 'keep').write_bytes(b'unknown')
        with self.assertRaisesRegex(lc.tx.Conflict, 'ORPHAN_ACTIVATION_STORAGE'):
            self.collect('3')
        self.assertEqual((orphan / 'keep').read_bytes(), b'unknown')

    def test_unregistered_activation_and_modified_version_are_preserved(self):
        self.store.activate(self.trees['1'], expected_revision=self.store.pointer.snapshot()[1])
        with self.assertRaisesRegex(lc.tx.Conflict, 'UNREGISTERED_ACTIVATION_TRANSACTION'):
            self.manager.inventory()
        self.assertTrue((self.store.versions / self.trees['1']).exists())

    def test_config_skill_and_quarantine_path_drift_block_deletion(self):
        self.apply('1'); self.apply('2')
        config = self.scopes[0].config.path
        original = config.read_bytes()
        config.write_bytes(b'{"mcpServers":{"foreign":{"url":"user-change"}}}')
        with self.assertRaisesRegex(lc.tx.Conflict, 'LIFECYCLE_RESOURCE_CHANGED'):
            self.collect('1')
        self.assertTrue((self.store.versions / self.trees['1']).exists())
        config.write_bytes(original)
        skill = self.scopes[0].skills.root / 'a.md'
        original_skill = skill.read_bytes(); skill.write_bytes(b'user-change')
        with self.assertRaisesRegex(lc.tx.Conflict, 'LOCAL_CHANGE_CONFLICT'):
            self.collect('1')
        skill.write_bytes(original_skill)
        quarantine = self.manager.trash
        saved = quarantine.with_name('saved-quarantine')
        quarantine.rename(saved)
        external = self.root / 'external'; external.mkdir(mode=0o700)
        (external / 'keep').write_bytes(b'external-content')
        quarantine.symlink_to(external, target_is_directory=True)
        with self.assertRaisesRegex(lc.tx.Conflict, 'SYMLINK_REJECTED'):
            self.collect('1')
        self.assertEqual((external / 'keep').read_bytes(), b'external-content')
        quarantine.unlink(); saved.rename(quarantine)
        self.assertEqual(self.collect('1')['status'], 'runtime_collected')

    def test_before_first_operation_config_drift_cannot_hide_a_runtime_reference(self):
        path = self.scopes[0].config.path
        target = self.store.versions / self.trees['3'] / 'tree/bin/tool'
        import json
        raw = json.dumps({'mcpServers': {'user-added': {'command': str(target)}}}).encode()
        path.write_bytes(raw)
        with self.assertRaisesRegex(lc.tx.Conflict, 'INITIAL_SCOPE_CHANGED'):
            self.collect('3')
        self.assertEqual(path.read_bytes(), raw)
        self.assertTrue(target.exists())
        path.unlink()
        self.assertEqual(self.collect('3')['status'], 'runtime_collected')

    def test_gc_restart_every_delete_boundary_and_history(self):
        self.apply('1')
        phases = ['recorded', 'quarantined', 'removed:journal.json', 'progress:journal.json',
                  'removed:manifest.json', 'removed:tree/bin/alias', 'removed:tree/bin/tool',
                  'removed:tree/bin', 'removed:tree', 'removed:']
        # Re-stage this same unreferenced immutable version after each completed GC.
        manifest = self.store._manifest(self.trees['3'])
        source = self.root / 'source3'
        for phase in phases:
            with self.subTest(phase=phase):
                with self.assertRaises(Crash):
                    self.collect('3', checkpoint=self.crash(phase))
                with self.assertRaisesRegex(lc.tx.Conflict, 'GC_RECOVERY_REQUIRED'):
                    self.manager.inventory()
                self.assertEqual(self.manager.recover_gc()['status'], 'runtime_collected')
                self.assertEqual(self.manager.recover_gc()['status'], 'nothing_pending')
                self.manager.stage(manifest, {'bin/tool': source}, expected_digest=self.trees['3'],
                                   transaction_id=uuid.uuid4().hex)
        self.assertEqual(len(list((self.manager.meta / 'history').iterdir())), len(phases) - 1)

    def test_gc_extra_content_future_missing_mode_and_hardlink_refuse(self):
        with self.assertRaises(Crash):
            self.collect('3', checkpoint=self.crash('quarantined'))
        record = self.manager._pending_gc()
        target = self.manager.trash / record['id']
        extra = target / 'keep'
        extra.write_bytes(b'user data')
        with self.assertRaisesRegex(lc.tx.Conflict, 'UNKNOWN_GC_CONTENT'):
            self.manager.recover_gc()
        self.assertEqual(extra.read_bytes(), b'user data'); extra.unlink()
        file = target / 'tree/bin/tool'
        raw = file.read_bytes()
        file.unlink()
        with self.assertRaisesRegex(lc.tx.Conflict, 'GC_PROGRESS_CONFLICT'):
            self.manager.recover_gc()
        file.write_bytes(raw); file.chmod(0o755)
        file.chmod(0o644)
        with self.assertRaisesRegex(lc.tx.Conflict, 'RUNTIME_FILE_CONFLICT'):
            self.manager.recover_gc()
        file.chmod(0o755)
        link = self.root / 'external-hardlink'
        os.link(file, link)
        with self.assertRaisesRegex(lc.tx.Conflict, 'RUNTIME_FILE_CONFLICT'):
            self.manager.recover_gc()
        link.unlink()
        self.assertEqual(self.manager.recover_gc()['status'], 'runtime_collected')

    def test_gc_record_forgery_and_damaged_original_refuse(self):
        with self.assertRaises(Crash):
            self.collect('3', checkpoint=self.crash('recorded'))
        path = self.manager.meta / 'gc.json'
        raw = path.read_bytes(); record = lc.tx.decode(raw)
        for changed in ({**record, 'next': True}, {**record, 'digest': self.trees['2']},
                        {**record, 'state': 'done'}, {**record, 'extra': 1}):
            path.write_bytes(lc.tx.encode(changed))
            with self.assertRaises(lc.tx.Conflict):
                self.manager.recover_gc()
        path.write_bytes(raw)
        file = self.store.versions / self.trees['3'] / 'tree/bin/tool'
        original = file.read_bytes(); file.write_bytes(b'changed')
        with self.assertRaises(lc.tx.Conflict):
            self.manager.recover_gc()
        self.assertEqual(file.read_bytes(), b'changed')
        file.write_bytes(original)
        self.assertEqual(self.manager.recover_gc()['status'], 'runtime_collected')

    def test_real_command_and_shared_lease_exclude_maintenance_and_install(self):
        self.apply('1')
        cp = self.manager.run(self.trees['1'], 'bin/tool', ['a b'], capture_output=True, check=True)
        self.assertEqual(cp.stdout, b'version-1:a b')
        with self.assertRaisesRegex(lc.tx.Conflict, 'UNREFERENCED_RUNTIME_EXECUTION'):
            self.manager.run(self.trees['3'], 'bin/tool')
        with self.manager.lease(self.trees['1']):
            with self.assertRaisesRegex(lc.tx.Conflict, 'RUNTIME_ACTIVITY_BUSY'):
                self.collect('3')
            with self.assertRaisesRegex(lc.tx.Conflict, 'RUNTIME_ACTIVITY_BUSY'):
                self.apply('2')
            with self.manager.lease(self.trees['1']):
                pass
        self.assertEqual(self.collect('3')['status'], 'runtime_collected')

    def test_inherited_descriptor_keeps_activity_after_parent_context_closes(self):
        self.apply('1')
        control_read, control_write = os.pipe()
        try:
            with self.manager.lease(self.trees['1']) as fd:
                child = subprocess.Popen([sys.executable, '-B', '-c',
                    'import os,sys;os.read(int(sys.argv[1]),1)', str(control_read)],
                    pass_fds=(fd, control_read), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            try:
                with self.assertRaisesRegex(lc.tx.Conflict, 'RUNTIME_ACTIVITY_BUSY'):
                    self.manager.inventory()
                os.write(control_write, b'x')
                child.communicate(timeout=10)
                self.assertEqual(child.returncode, 0)
            finally:
                if child.poll() is None:
                    child.kill(); child.communicate()
        finally:
            os.close(control_read); os.close(control_write)
        self.assertEqual(self.collect('3')['status'], 'runtime_collected')

    def test_real_parent_exit_retains_child_lease_and_gc_process_exit_recovers(self):
        self.apply('1')
        setup = '''from pathlib import Path
import os,sys,subprocess
sys.path.insert(0,sys.argv[1])
from runtime_tools import runtime_lifecycle as lc
root=Path(sys.argv[2]);store=lc.rt.RuntimeStore(root/'runtime');scopes=[]
for i in range(2):
 scopes.append(lc.operation.InstallOperation(*(root/(n+str(i)) for n in ('state','skills','config')),'mcp.json',runtime_root=store.root))
manager=lc.RuntimeLifecycle(store,scopes);manager.initialize()
'''
        read_fd, write_fd = os.pipe()
        notify_read, notify_write = os.pipe()
        child_code = 'import os,sys;os.read(int(sys.argv[1]),1);os.close(int(sys.argv[2]));os.write(int(sys.argv[3]),b"x")'
        script = setup + '''with manager.lease(sys.argv[3]) as fd:
 subprocess.Popen([sys.executable,'-B','-c',sys.argv[6],sys.argv[4],str(fd),sys.argv[5]],
  pass_fds=(fd,int(sys.argv[4]),int(sys.argv[5])),stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
 os._exit(73)
'''
        try:
            parent = subprocess.run([sys.executable, '-B', '-c', script, str(TOOL), str(self.root),
                self.trees['1'], str(read_fd), str(notify_write), child_code],
                pass_fds=(read_fd, notify_write), capture_output=True, timeout=15)
            self.assertEqual(parent.returncode, 73, parent.stderr)
            with self.assertRaisesRegex(lc.tx.Conflict, 'RUNTIME_ACTIVITY_BUSY'):
                self.manager.inventory()
            os.write(write_fd, b'x')
            self.assertTrue(select.select([notify_read], [], [], 10)[0])
            self.assertEqual(os.read(notify_read, 1), b'x')
        finally:
            for fd in (read_fd, write_fd, notify_read, notify_write):
                os.close(fd)
        script = setup + '''def crash(phase):
 if phase=='removed:tree/bin/alias':os._exit(77)
manager.collect(sys.argv[3],expected_revision=manager.inventory()['revision'],checkpoint=crash)
'''
        result = subprocess.run([sys.executable, '-B', '-c', script, str(TOOL), str(self.root),
                                 self.trees['3']], capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 77, result.stderr)
        self.assertEqual(self.manager.recover_gc()['status'], 'runtime_collected')


if __name__ == '__main__':
    unittest.main()
