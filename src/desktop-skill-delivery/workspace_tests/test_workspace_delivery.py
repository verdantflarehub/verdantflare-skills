"""Receive source/runtime bytes without trusting a candidate's own declarations."""
from pathlib import Path
import os
import shutil
import stat
import sys
import tempfile
import unittest
import zipfile

TOOLS=Path(__file__).resolve().parents[1]/'workspace_tools'
sys.path.insert(0,str(TOOLS))
import workspace_delivery as d

BASE=Path(__file__).resolve().parents[4]/'.tmp/workspace-source-tests'
MODULE='example.invalid/module'
MOD=b'module example.invalid/module\n'
MODSUM='h1:H3dH2PiuZgWDO5S/2tMIuPnky5EjpQd28ez/79NqLmQ='
ZIPSUM='h1:H4fjdzUmr9zEXELDAQKtnUJ1TBvVtFYVGHTHRAXKIKc='


class DeliveryTests(unittest.TestCase):
    def setUp(self):
        BASE.mkdir(parents=True,exist_ok=True);self.temp=tempfile.TemporaryDirectory(dir=BASE)
        self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name).absolute()
        self.source=self.root/'source-candidate';self.source.mkdir()
        s=self.source
        for n,raw in [('go.mod',b'module '+d.MAIN.encode()+b'\n'),
                      ('go.sum',(MODULE+' v1.0.0 '+ZIPSUM+'\n'+MODULE+' v1.0.0/go.mod '+MODSUM+'\n').encode()),
                      ('internal/buildinfo/version.go',b'package buildinfo\nconst Version = "0.5.228"\n')]:
            d.add(s,'source/'+n,raw)
        for name in d.TOOL_FILES:d.add(s,'tooling/'+name,(TOOLS/name).read_bytes())
        prefix='modules/download-cache/'+MODULE+'/@v/v1.0.0'
        archive=s/(prefix+'.zip');archive.parent.mkdir(parents=True)
        with zipfile.ZipFile(archive,'w') as z:
            for name,raw in [('go.mod',MOD),('LICENSE',b'original license')]:
                i=zipfile.ZipInfo(MODULE+'@v1.0.0/'+name);i.create_system=3;i.external_attr=(stat.S_IFREG|0o644)<<16
                z.writestr(i,raw)
        archive.chmod(0o644)
        result=d.modules.verify_module_zip(archive,MODULE,'v1.0.0',ZIPSUM)
        info=d.encode({'Version':'v1.0.0'})
        for suffix,raw in [('.mod',MOD),('.ziphash',ZIPSUM.encode()),('.info',info)]:d.add(s,prefix+suffix,raw)
        index={'schema':'vf.workspace-offline-module-inputs/v1',
               'root_go_mod_sha256':d.sdk.sha(s/'source/go.mod'),'root_go_sum_sha256':d.sdk.sha(s/'source/go.sum'),
               'metadata_modules':[{'module':MODULE,'version':'v1.0.0','go_mod_sum':MODSUM,'mod_sha256':d.digest(MOD),'mod_bytes':len(MOD)}],
               'code_modules':[{**result,'info_sha256':d.digest(info),'info_bytes':len(info)}]}
        d.add(s,'modules/module-inputs.json',d.encode(index));d.add(s,'licenses/go-sdk/LICENSE',b'upstream license fixture')
        status={'source_kind':'explicit_working_tree_snapshot','git_commit':'a'*40,'worktree_dirty':False,
                'selected_source_dirty':False,'original_license_files':[],'main_source_license_grant_verified':False,
                'public_distribution':False,'installable':False}
        d.add(s,'source-status.json',d.encode(status))
        self.lock={'schema':d.SCHEMA,'version':1,'main_module':d.MAIN,'cli_version':'0.5.228',
                   'sdk_lock':d.sdk.read_sdk_lock(TOOLS/'go-toolchain-lock.json'),'source':status,
                   'source_files':sorted(n[7:] for n in d.file_set(s) if n.startswith('source/')),
                   'files':d.inventory(s),'sdk_archive_included':False,'public_distribution':False,'installable':False,
                   'desktop_acceptance':False,'windows_execution':False,'authentication':'unsigned'}
        d.add(s,'source-lock.json',d.encode(self.lock));self.source_sha=d.sdk.sha(s/'source-lock.json')

    def relock(self,folder,lock,metadata):
        lock['files']=d.inventory(folder,exclude=(metadata,));(folder/metadata).write_bytes(d.encode(lock))
        return d.sdk.sha(folder/metadata)

    def runtime(self):
        out=self.root/'runtime';out.mkdir();shutil.copytree(self.source,out/'build-source')
        shutil.copytree(self.source/'licenses',out/'licenses')
        binaries={}
        for target,(_,_,name) in d.TARGETS.items():
            # Deliberate format fixtures, never executable programs or acceptance evidence.
            raw=bytearray(256)
            if target=='macos-arm64':raw[:8]=b'\xcf\xfa\xed\xfe\x0c\x00\x00\x01'
            else:raw[:2]=b'MZ';raw[60:64]=(128).to_bytes(4,'little');raw[128:134]=b'PE\0\0\x64\x86'
            path='bin/'+target+'/'+name;d.add(out,path,bytes(raw),0o755)
            binaries[target]={'path':path,'sha256':d.digest(raw),'bytes':len(raw)}
        value={'schema':'vf.workspace-cli-candidate/v1','version':'0.5.228','source_lock_sha256':self.source_sha,
               'sdk_version':self.lock['sdk_lock']['version'],'sdk_source_sha256':self.lock['sdk_lock']['sha256'],
               'binaries':binaries,'tests_passed':39,'tests_failed':0,'tests_skipped':0,'files':d.inventory(out),
               'authentication':'unsigned','installable':False,'public_distribution':False,
               'main_source_license_grant_verified':False,'windows_execution':False,'mcp_acceptance':False,
               'desktop_acceptance':False,'independent_review':'pending'}
        d.add(out,'workspace-runtime.json',d.encode(value))
        return out,value,d.sdk.sha(out/'workspace-runtime.json')

    def test_source_and_runtime_receive_do_not_execute_or_change_bytes(self):
        before=d.inventory(self.source)
        self.assertEqual(d.verify_source(self.source,self.source_sha)['cli_version'],'0.5.228')
        self.assertEqual(d.inventory(self.source),before)
        folder,value,h=self.runtime();before=d.inventory(folder)
        self.assertEqual(d.verify_runtime(folder,h)['binaries'],value['binaries'])
        self.assertEqual(d.inventory(folder),before)

    def test_external_hash_is_required_even_for_valid_json(self):
        with self.assertRaisesRegex(ValueError,'WORKSPACE_SOURCE_LOCK_HASH'):d.verify_source(self.source,'0'*64)
        out,value,h=self.runtime();(out/'workspace-runtime.json').write_bytes(d.encode(value)+b' ')
        with self.assertRaisesRegex(ValueError,'WORKSPACE_RUNTIME_HASH'):d.verify_runtime(out,h)

    def test_extra_missing_changed_mode_links_and_bytes_reject(self):
        for case in ('extra','missing','bytes','mode','symlink','hardlink'):
            out=self.root/case;shutil.copytree(self.source,out);p=out/'source/go.mod'
            if case=='extra':(out/'unlisted').write_bytes(b'payload')
            elif case=='missing':p.unlink()
            elif case=='bytes':p.write_bytes(b'changed')
            elif case=='mode':p.chmod(0o755)
            elif case=='symlink':p.unlink();p.symlink_to(self.source/'source/go.mod')
            else:p.unlink();os.link(self.source/'source/go.mod',p)
            with self.subTest(case=case),self.assertRaises(ValueError):d.verify_source(out,self.source_sha)
            if case=='hardlink':p.unlink()

    def test_rehashed_root_module_metadata_still_bound_to_source(self):
        (self.source/'source/go.mod').write_bytes(b'module changed.invalid\n')
        h=self.relock(self.source,self.lock,'source-lock.json')
        with self.assertRaisesRegex(ValueError,'WORKSPACE_MODULE_ROOT_BINDING'):d.verify_source(self.source,h)

    def test_rehashed_raw_module_zip_still_bound_to_original_go_sum(self):
        path=self.source/('modules/download-cache/'+MODULE+'/@v/v1.0.0.zip')
        with zipfile.ZipFile(path,'a') as z:z.writestr(MODULE+'@v1.0.0/extra.go',b'package unexpected')
        h=self.relock(self.source,self.lock,'source-lock.json')
        with self.assertRaisesRegex(ValueError,'MODULE_GO_SUM_MISMATCH'):d.verify_source(self.source,h)

    def test_rehashed_source_version_or_tool_set_is_not_accepted(self):
        self.lock['cli_version']='0.5.229'
        (self.source/'source-lock.json').write_bytes(d.encode(self.lock))
        with self.assertRaisesRegex(ValueError,'WORKSPACE_SOURCE_VERSION_BINDING'):
            d.verify_source(self.source,d.sdk.sha(self.source/'source-lock.json'))
        self.lock['cli_version']='0.5.228';(self.source/'tooling/unlisted.py').write_bytes(b'pass\n')
        h=self.relock(self.source,self.lock,'source-lock.json')
        with self.assertRaisesRegex(ValueError,'WORKSPACE_SOURCE_LAYOUT'):d.verify_source(self.source,h)

    def test_runtime_rejects_misbound_binary_and_changed_architecture(self):
        out,value,h=self.runtime()
        value['binaries']['windows-amd64']['path']=value['binaries']['macos-arm64']['path']
        h=self.relock(out,value,'workspace-runtime.json')
        with self.assertRaisesRegex(ValueError,'WORKSPACE_BINARY_BINDING'):d.verify_runtime(out,h)
        path='bin/windows-amd64/studio-workspace.exe';value['binaries']['windows-amd64']['path']=path
        raw=bytearray((out/path).read_bytes());raw[132:134]=b'\x4c\x01';(out/path).write_bytes(raw)
        value['binaries']['windows-amd64']['sha256']=d.digest(raw);h=self.relock(out,value,'workspace-runtime.json')
        with self.assertRaisesRegex(ValueError,'WORKSPACE_AMD64_REQUIRED'):d.verify_runtime(out,h)

    def test_runtime_rejects_rehashed_license_drift_and_unrelated_payload(self):
        out,value,h=self.runtime();p=out/'licenses/go-sdk/LICENSE';p.write_bytes(b'different grant')
        h=self.relock(out,value,'workspace-runtime.json')
        with self.assertRaisesRegex(ValueError,'WORKSPACE_LICENSE_COPY_CHANGED'):d.verify_runtime(out,h)
        p.write_bytes((out/'build-source/licenses/go-sdk/LICENSE').read_bytes())
        d.add(out,'extra/program',b'unrelated');h=self.relock(out,value,'workspace-runtime.json')
        with self.assertRaisesRegex(ValueError,'WORKSPACE_RUNTIME_LAYOUT'):d.verify_runtime(out,h)

    def test_runtime_cannot_assert_distribution_windows_or_acceptance_passed(self):
        out,value,h=self.runtime()
        for name in ('installable','public_distribution','main_source_license_grant_verified','windows_execution','mcp_acceptance','desktop_acceptance'):
            value[name]=True;h=self.relock(out,value,'workspace-runtime.json')
            with self.subTest(name=name),self.assertRaisesRegex(ValueError,'INVALID_WORKSPACE_RUNTIME'):d.verify_runtime(out,h)
            value[name]=False

    def test_manifest_rejects_implicit_case_and_unicode_aliases(self):
        for names in (('include/Foo/a','include/foo/b'),('include/\u00e9/a','include/e\u0301/b'),('a','a/b')):
            with self.subTest(names=names),self.assertRaises(ValueError):d.validate_names(names)


if __name__=='__main__':unittest.main()
