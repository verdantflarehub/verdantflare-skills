"""Large runtime files and version activation in isolated POSIX fixtures."""
from pathlib import Path
import copy
import os
import subprocess
import sys
import tempfile
import unittest
import uuid

TOOLS=Path(__file__).resolve().parents[1]/'runtime_tools';sys.path.insert(0,str(TOOLS))
import runtime_store as r

BASE=Path(__file__).resolve().parents[4]/'.tmp/runtime-store-tests'


class Crash(BaseException):pass


class RuntimeStoreTests(unittest.TestCase):
    def setUp(self):
        BASE.mkdir(parents=True,exist_ok=True)
        self.temp=tempfile.TemporaryDirectory(dir=BASE);self.addCleanup(self.temp.cleanup)
        self.base=Path(self.temp.name).absolute();root=self.base/'store';root.mkdir(mode=0o700)
        self.store=r.RuntimeStore(root);self.sources=self.base/'sources';self.sources.mkdir()
        self.source=self.sources/'source';self.source.write_bytes(b'a'*(r.CHUNK+17))
        self.manifest={'schema':r.SCHEMA,'release':'test.1','source_sha256':'a'*64,
                       'entries':{'python/bin/python3':{'type':'file','sha256':r.file_hash(self.source)[0],
                           'size':self.source.stat().st_size,'mode':0o755},
                           'python/bin/python':{'type':'symlink','target':'python3'}}}
        self.mapping={'python/bin/python3':self.source};self.identifier=uuid.uuid4().hex

    def digest(self,manifest=None):return r.tx.sha(r.tx.encode(manifest or self.manifest))

    def stage(self,checkpoint=lambda _:None):
        return self.store.stage(self.manifest,self.mapping,expected_digest=self.digest(),
                                transaction_id=self.identifier,checkpoint=checkpoint)

    def target(self):return self.store.versions/self.digest()/'tree/python/bin/python3'

    def crash(self,target):
        def f(phase):
            if phase==target:raise Crash()
        return f

    def test_stage_verify_link_modes_and_idempotent_ready(self):
        receipt=self.stage();self.assertEqual(receipt['status'],'tree_ready')
        self.assertEqual(self.target().read_bytes(),self.source.read_bytes())
        self.assertEqual(self.target().stat().st_mode&0o777,0o755)
        self.assertEqual(os.readlink(self.target().with_name('python')),'python3')
        self.assertEqual(self.store.verify(self.digest())['status'],'tree_verified')
        self.assertEqual(self.stage()['status'],'already_ready')
        self.assertNotIn(str(self.base),str(receipt))

    def test_manifest_rejects_escapes_aliases_cycles_and_sizes(self):
        for name in ('../outside','/absolute','a//b','CON','a\\b'):
            manifest=copy.deepcopy(self.manifest);manifest['entries'][name]=manifest['entries'].pop('python/bin/python3')
            with self.assertRaises(r.tx.Conflict):r.validate_manifest(manifest)
        for target in ('/etc/passwd','../../../outside','missing','python','python/../python3'):
            manifest=copy.deepcopy(self.manifest);manifest['entries']['python/bin/python']['target']=target
            with self.assertRaises(r.tx.Conflict):r.validate_manifest(manifest)
        for name in ('Python/other','python/bin/python3/child'):
            manifest=copy.deepcopy(self.manifest);manifest['entries'][name]=manifest['entries']['python/bin/python3']
            with self.assertRaises(r.tx.Conflict):r.validate_manifest(manifest)
        manifest=copy.deepcopy(self.manifest);manifest['entries']['python/bin/python3']['size']=r.MAX_FILE+1
        with self.assertRaisesRegex(r.tx.Conflict,'INVALID_TREE_FILE'):r.validate_manifest(manifest)

    def test_real_dependency_filenames_spaces_and_plus(self):
        self.manifest['entries']={name:copy.deepcopy(self.manifest['entries']['python/bin/python3'])
                                 for name in ('vendor/Transparent Busy.ani','vendor/GLB.Ts+dSST.csv')}
        self.mapping={name:self.source for name in self.manifest['entries']}
        self.stage();self.assertEqual(self.store.verify(self.digest())['status'],'tree_verified')
        for name in ('a/ trailing','a/trailing ','a/x.','a/x:stream','a/PRN.txt','a/x\x01'):
            with self.assertRaises(r.tx.Conflict):r.relative(name)

    def test_partial_reentry_and_original_source_binding(self):
        with self.assertRaises(Crash):self.stage(self.crash('chunk:python/bin/python3'))
        self.assertEqual(self.target().stat().st_size,r.CHUNK)
        self.stage();self.assertEqual(self.target().read_bytes(),self.source.read_bytes())
        self.source.write_bytes(b'changed')
        # Ready reuse verifies its own bytes, not an unnecessary vanished source.
        self.assertEqual(self.stage()['status'],'already_ready')

    def test_partial_user_modification_is_preserved(self):
        with self.assertRaises(Crash):self.stage(self.crash('chunk:python/bin/python3'))
        self.target().write_bytes(b'user changed')
        with self.assertRaisesRegex(r.tx.Conflict,'RUNTIME_PARTIAL_CONFLICT'):self.stage()
        self.assertEqual(self.target().read_bytes(),b'user changed')

    def test_completed_missing_file_unknown_file_and_mode_conflicts(self):
        self.stage();self.target().chmod(0o644)
        with self.assertRaisesRegex(r.tx.Conflict,'RUNTIME_FILE_CONFLICT'):self.store.verify(self.digest())
        self.target().chmod(0o755);self.target().unlink()
        with self.assertRaisesRegex(r.tx.Conflict,'RUNTIME_FILE_MISSING'):self.store.verify(self.digest())
        # A completed file is not recreated silently after user deletion.
        with self.assertRaisesRegex(r.tx.Conflict,'RUNTIME_FILE_MISSING'):self.stage()

    def test_wrong_id_source_and_unready_activation(self):
        with self.assertRaises(Crash):self.stage(self.crash('prepared'))
        self.identifier=uuid.uuid4().hex
        with self.assertRaisesRegex(r.tx.Conflict,'STAGING_OWNED_BY_OTHER_TRANSACTION'):self.stage()
        with self.assertRaisesRegex(r.tx.Conflict,'RUNTIME_NOT_READY'):
            self.store.activate(self.digest(),expected_revision=self.store.pointer.snapshot()[1])
        journal=r.tx.decode((self.store.versions/self.digest()/'journal.json').read_bytes())
        self.identifier=journal['id'];self.source.write_bytes(b'changed')
        with self.assertRaisesRegex(r.tx.Conflict,'RUNTIME_SOURCE_CHANGED'):self.stage()

    def test_unknown_content_symlink_root_and_hardlink_preserved(self):
        self.stage();extra=self.target().parent/'unknown';extra.write_bytes(b'keep')
        with self.assertRaisesRegex(r.tx.Conflict,'UNKNOWN_RUNTIME_FILE'):self.store.verify(self.digest())
        self.assertEqual(extra.read_bytes(),b'keep');extra.unlink()
        link=self.sources/'extra-link';os.link(self.target(),link)
        with self.assertRaisesRegex(r.tx.Conflict,'RUNTIME_FILE_CONFLICT'):self.store.verify(self.digest())
        link.unlink()
        alias=self.base/'alias';alias.symlink_to(self.store.root,target_is_directory=True)
        with self.assertRaises(r.tx.Conflict):r.RuntimeStore(alias)

    def test_real_process_exit_and_resume_same_transaction(self):
        manifest_path=self.base/'manifest.json';manifest_path.write_bytes(r.tx.encode(self.manifest))
        script='''import sys,os,json
sys.path.insert(0,sys.argv[1]);import runtime_store as r
s=r.RuntimeStore(sys.argv[2]);manifest=json.load(open(sys.argv[3]))
def stop(phase):
 if phase==sys.argv[6]:os._exit(77)
s.stage(manifest,{'python/bin/python3':sys.argv[4]},expected_digest=r.tx.sha(r.tx.encode(manifest)),transaction_id=sys.argv[5],checkpoint=stop)
'''
        for phase in ('prepared','writing:python/bin/python3','chunk:python/bin/python3','written:python/bin/python3','ready'):
            # Each subprocess continues the same transaction and exits at a later point.
            cp=subprocess.run([sys.executable,'-c',script,str(TOOLS),str(self.store.root),str(manifest_path),
                               str(self.source),self.identifier,phase],capture_output=True)
            self.assertEqual(cp.returncode,77,cp.stderr)
        self.assertEqual(self.stage()['status'],'already_ready')

    def test_activation_update_compensation_and_bad_old_tree(self):
        self.stage();first=self.digest()
        first_receipt=self.store.activate(first,expected_revision=self.store.pointer.snapshot()[1])
        self.manifest=copy.deepcopy(self.manifest);self.manifest['release']='test.2';self.identifier=uuid.uuid4().hex
        self.stage();second=self.digest()
        receipt=self.store.activate(second,expected_revision=self.store.pointer.snapshot()[1])
        self.store.rollback_activation(receipt['transaction'],expected_revision=receipt['revision'])
        active=r.tx.decode((self.store.activation/'runtime.json').read_bytes())
        self.assertEqual(active['tree_sha256'],first)
        self.assertEqual(self.store.verify(second)['status'],'tree_verified')
        self.assertEqual(self.store.pointer._pending()['state'],'committed')
        self.assertNotEqual(first_receipt['transaction'],receipt['transaction'])

    def test_large_file_and_lost_preparation_progress(self):
        self.source.write_bytes(b'L'*(12*1024*1024+31))
        entry=self.manifest['entries']['python/bin/python3']
        entry['size']=self.source.stat().st_size;entry['sha256']=r.file_hash(self.source)[0]
        with self.assertRaises(Crash):self.stage(self.crash('prepared'))
        folder=self.store.versions/self.digest();(folder/'journal.json').unlink()
        # Immutable intent retains the original ID before any content was written.
        self.stage();self.assertEqual(self.target().stat().st_size,12*1024*1024+31)
        self.assertEqual(r.file_hash(self.target()),r.file_hash(self.source))

    def test_link_tampering_and_unready_reads_do_not_create_versions(self):
        unknown=self.store.versions/('b'*64)
        with self.assertRaisesRegex(r.tx.Conflict,'RUNTIME_VERSION_MISSING'):self.store.verify('b'*64)
        self.assertFalse(unknown.exists())
        self.stage();link=self.target().with_name('python');link.unlink();link.symlink_to(self.source)
        with self.assertRaisesRegex(r.tx.Conflict,'RUNTIME_LINK_CONFLICT'):self.store.verify(self.digest())
        self.assertEqual(os.readlink(link),str(self.source))

    def test_activation_interrupted_before_commit_restores_absence(self):
        self.stage();identifier=uuid.uuid4().hex
        with self.assertRaises(Crash):
            self.store.activate(self.digest(),expected_revision=self.store.pointer.snapshot()[1],
                                transaction_id=identifier,checkpoint=self.crash('file:runtime.json'))
        self.store.recover_activation(identifier)
        self.assertFalse((self.store.activation/'runtime.json').exists())
        self.assertEqual(self.store.verify(self.digest())['status'],'tree_verified')


if __name__=='__main__':unittest.main()
