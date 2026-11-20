"""Three-resource order, compensation and real process exits in isolated roots."""
from pathlib import Path
import json
import os
import subprocess
import sys
import tempfile
import unittest
import uuid

TOOL=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(TOOL))
from install_operation import InstallOperation
import install_transaction as io
from runtime_tools.runtime_store import RuntimeStore,SCHEMA
from host_config import ConfigConflict

BASE=Path(__file__).resolve().parents[4]/'.tmp/runtime-operation-tests'


class Crash(BaseException):pass


class RuntimeOperationTests(unittest.TestCase):
    def setUp(self):
        BASE.mkdir(parents=True,exist_ok=True)
        self.temp=tempfile.TemporaryDirectory(dir=BASE);self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name).absolute()
        for n in ('state','skills','config','runtime','sources'):(self.root/n).mkdir(mode=0o700)
        self.engine=self.engine_at(self.root)
        self.trees={}
        for version in ('1','2'):
            raw=('python-fixture-'+version).encode();source=self.root/'sources'/version;source.write_bytes(raw)
            manifest={'schema':SCHEMA,'release':'python.'+version,'source_sha256':io.sha(raw),
                      'entries':{'python/bin/python':{'type':'file','sha256':io.sha(raw),'size':len(raw),'mode':0o755}}}
            digest=io.sha(io.encode(manifest))
            self.engine.runtime_store.stage(manifest,{'python/bin/python':source},
                expected_digest=digest,transaction_id=uuid.uuid4().hex)
            self.trees[version]=digest

    @staticmethod
    def engine_at(root):
        return InstallOperation(*(root/n for n in ('state','skills','config')),'mcp.json',runtime_root=root/'runtime')

    def apply(self,version='1',runtime_version=None,checkpoint=lambda _:None):
        e=self.engine;observed=e.config.observe()
        return e.apply('skill.'+version,{'a.md':version.encode()},{'url':'https://v'+version+'.invalid'},
            expected_skill_revision=e.skills.snapshot()[1],expected_config_revision=observed['ledger_revision'],
            expected_config_file=observed['file'],runtime_digest=self.trees[runtime_version or version],
            expected_runtime_revision=e.runtime.snapshot()[1],checkpoint=checkpoint)

    def state(self):
        e=self.engine
        return (io.read(e.runtime.root/'runtime.json'),e.runtime.snapshot(),
                io.read(e.skills.root/'a.md'),e.skills.snapshot(),e.config._file(),e.config.snapshot())

    @staticmethod
    def fail(target):
        def f(phase):
            if phase==target:raise Crash()
        return f

    def settle(self):
        for _ in range(3):self.engine.recover()

    def test_activation_precedes_references_and_cleanup_retains_trees(self):
        phases=[];e=self.engine
        self.apply(checkpoint=phases.append)
        self.assertLess(phases.index('runtime'),phases.index('skills'))
        self.assertLess(phases.index('skills'),phases.index('config'))
        self.assertEqual(json.loads((e.runtime.root/'runtime.json').read_bytes())['tree_sha256'],self.trees['1'])
        e.recover();self.apply('2');e.recover()
        for digest in self.trees.values():self.assertEqual(e.runtime_store.verify(digest)['status'],'tree_verified')
        self.assertEqual(e._active()['state'],'cleaned')
        self.assertFalse((e.runtime.meta/e.runtime._pending()['id']/'before/runtime.json').exists())

    def test_all_three_noop_and_runtime_only_noop_preserve_history(self):
        e=self.engine;self.apply();self.settle();before=self.state();previous=e.runtime._pending()['id']
        self.apply();self.settle();self.assertEqual(self.state(),before)
        self.assertEqual(e.runtime._pending()['id'],previous)
        with self.assertRaises(Crash):self.apply('2',runtime_version='1',checkpoint=self.fail('config'))
        self.settle();self.assertEqual(self.state(),before)
        self.assertEqual(e.runtime._pending()['id'],previous)

    def test_config_conflict_restores_runtime_and_skills(self):
        e=self.engine;e.config.path.write_bytes(b'{"mcpServers":{"vf-studio":{"url":"user-owned"}}}')
        before=self.state()
        with self.assertRaises(ConfigConflict):self.apply()
        self.assertEqual(self.state(),before);self.settle()
        self.assertEqual(e._active()['state'],'cleaned_aborted')

    def test_missing_tree_stale_preflight_and_mode_reject_before_writes(self):
        e=self.engine;before=self.state();observed=e.config.observe()
        for digest,revision in [('a'*64,e.runtime.snapshot()[1]),(self.trees['1'],'b'*64)]:
            with self.assertRaises(io.Conflict):
                e.apply('skill.1',{'a.md':b'1'},{'url':'https://v1.invalid'},
                    expected_skill_revision=e.skills.snapshot()[1],expected_config_revision=observed['ledger_revision'],
                    expected_config_file=observed['file'],runtime_digest=digest,expected_runtime_revision=revision)
            self.assertEqual(self.state(),before);self.assertIsNone(e._active())
        with self.assertRaisesRegex(io.Conflict,'RUNTIME_MODE_MISMATCH'):
            e.apply('v1',{},None,expected_skill_revision='',expected_config_revision='',expected_config_file=None)

    def test_new_tree_damage_before_commit_aborts_and_preserves_damage(self):
        e=self.engine;self.apply();self.settle();before=self.state()
        target=e.runtime_store.versions/self.trees['2']/'tree/python/bin/python'
        def damage(phase):
            if phase=='config':target.write_bytes(b'user changed new runtime')
        with self.assertRaises(io.Conflict):self.apply('2',checkpoint=damage)
        self.assertEqual(self.state(),before);self.settle()
        self.assertEqual(target.read_bytes(),b'user changed new runtime')

    def test_old_tree_damage_blocks_rollback_until_repaired(self):
        e=self.engine;self.apply();self.settle();before=self.state()
        with self.assertRaises(Crash):self.apply('2',checkpoint=self.fail('config'))
        target=e.runtime_store.versions/self.trees['1']/'tree/python/bin/python';raw=target.read_bytes()
        target.write_bytes(b'damaged old runtime')
        with self.assertRaises(io.Conflict):e.recover()
        self.assertEqual(json.loads((e.runtime.root/'runtime.json').read_bytes())['tree_sha256'],self.trees['2'])
        self.assertEqual(target.read_bytes(),b'damaged old runtime')
        target.write_bytes(raw);self.settle();self.assertEqual(self.state(),before)

    def test_external_activation_edit_is_never_overwritten(self):
        e=self.engine;self.apply();self.settle()
        with self.assertRaises(Crash):self.apply('2',checkpoint=self.fail('config'))
        target=e.runtime.root/'runtime.json';target.write_bytes(b'user edit')
        with self.assertRaises(io.Conflict):e.recover()
        self.assertEqual(target.read_bytes(),b'user edit');self.assertEqual(e._active()['state'],'aborting')

    def test_root_binding_privacy_and_overlap(self):
        e=self.engine
        with self.assertRaisesRegex(io.Conflict,'ROOT_OVERLAP'):
            InstallOperation(*(self.root/n for n in ('state','skills','config')),'mcp.json',runtime_root=self.root/'skills')
        self.apply();journal=e.meta/(e._active()['id']+'.json');raw=journal.read_bytes()
        self.assertNotIn(os.fsencode(self.root),raw);self.assertNotIn(b'https://',raw)
        self.assertEqual(journal.stat().st_mode&0o777,0o600)
        changed=io.decode(raw);changed['runtime']['id']='0'*32;journal.write_bytes(io.encode(changed))
        with self.assertRaisesRegex(io.Conflict,'CHILD_TRANSACTION_CHANGED'):e.recover()

    def test_cleanup_reentry_after_commit_and_abort(self):
        e=self.engine
        for outcome in ('committed','aborted'):
            if outcome=='committed':self.apply()
            else:
                with self.assertRaises(Crash):self.apply('2',checkpoint=self.fail('config'))
                e.recover()
            before=self.state()
            with self.assertRaises(Crash):e.recover(checkpoint=self.fail('cleaned:runtime'))
            self.settle();self.assertEqual(before,self.state())

    def test_real_process_exit_and_second_recovery_exit(self):
        phases=['preparing','runtime:staged','runtime:file:runtime.json','runtime:ledger','runtime:committed',
                'runtime','skills:committed','skills','config:config','config:committed','config','committed']
        script='''from pathlib import Path
import os,sys
sys.path.insert(0,sys.argv[1])
from install_operation import InstallOperation
root=Path(sys.argv[2]);e=InstallOperation(*(root/n for n in ('state','skills','config')),'mcp.json',runtime_root=root/'runtime')
def stop(p):
 if p==sys.argv[3]:os._exit(77)
o=e.config.observe()
e.apply('skill.'+sys.argv[4],{'a.md':sys.argv[4].encode()},{'url':'https://v'+sys.argv[4]+'.invalid'},
 expected_skill_revision=e.skills.snapshot()[1],expected_config_revision=o['ledger_revision'],expected_config_file=o['file'],
 runtime_digest=sys.argv[5],expected_runtime_revision=e.runtime.snapshot()[1],checkpoint=stop)
'''
        for baseline in ('empty','existing'):
            if baseline=='existing':self.apply();self.settle()
            for i,phase in enumerate(phases):
                with self.subTest(baseline=baseline,phase=phase):
                    before=self.state()
                    version='2' if baseline=='existing' else '1'
                    cp=subprocess.run([sys.executable,'-B','-c',script,str(TOOL),str(self.root),phase,version,self.trees[version]],capture_output=True)
                    self.assertEqual(cp.returncode,77,cp.stderr.decode().replace(str(self.root),'%TEST_ROOT%'))
                    if phase=='committed':
                        after=self.state();self.settle();self.assertEqual(self.state(),after)
                        self.assertNotEqual(before,after)
                    else:
                        with self.assertRaises(Crash):self.engine.recover(checkpoint=self.fail('restored:runtime'))
                        self.settle();self.assertEqual(self.state(),before)


if __name__=='__main__':unittest.main()
