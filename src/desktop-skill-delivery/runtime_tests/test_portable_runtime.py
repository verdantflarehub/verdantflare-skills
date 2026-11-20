"""Wheel integrity/projection and portable entry behavior, no network or host edits."""
from pathlib import Path
import base64
import csv
import hashlib
import io
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
import zipfile

TOOLS=Path(__file__).resolve().parents[1]/'runtime_tools';sys.path.insert(0,str(TOOLS))
import portable_runtime as p

BASE=Path(__file__).resolve().parents[4]/'.tmp/portable-runtime-tests'


def wheel(files,unlisted=(),changes=None):
    files=dict(files);record='sample-1.dist-info/RECORD';rows=[]
    for name,raw in files.items():
        h=base64.urlsafe_b64encode(hashlib.sha256(raw).digest()).rstrip(b'=').decode()
        rows.append([name,'sha256='+h,str(len(raw))])
    rows.append([record,'','']);stream=io.StringIO();csv.writer(stream,lineterminator='\n').writerows(rows)
    files[record]=stream.getvalue().encode()
    if changes:files.update(changes)
    files.update(dict(unlisted));out=io.BytesIO()
    with zipfile.ZipFile(out,'w') as archive:
        for name,raw in files.items():
            info=zipfile.ZipInfo(name);info.create_system=3;info.external_attr=(stat.S_IFREG|0o644)<<16
            archive.writestr(info,raw)
    return out.getvalue()


class PortableRuntimeTests(unittest.TestCase):
    def setUp(self):
        BASE.mkdir(parents=True,exist_ok=True);self.temp=tempfile.TemporaryDirectory(dir=BASE);self.addCleanup(self.temp.cleanup)
        self.base=Path(self.temp.name).absolute()

    def test_original_wheel_members_preserved_and_records_verified(self):
        source={'sample/module.py':b'VALUE = 42\n','sample-1.dist-info/licenses/LICENSE':b'License',
                'sample-1.data/scripts/sample':b'#!python\nprint("sample")\n'}
        result=p.wheel_files(wheel(source))
        for name,raw in source.items():self.assertEqual(result[name],raw)
        self.assertIn('sample-1.dist-info/RECORD',result)

    def test_record_corruption_and_unlisted_file_rejected(self):
        raw=wheel({'sample/module.py':b'original'},changes={'sample/module.py':b'changed'})
        with self.assertRaises(ValueError):p.wheel_files(raw)
        with self.assertRaisesRegex(ValueError,'WHEEL_RECORD_SET'):
            p.wheel_files(wheel({'sample/a':b'a'},unlisted=[('sample/hidden',b'hidden')]))

    def test_pth_data_install_schemes_and_traversal_rejected(self):
        for name in ('sample.pth','sample-1.data/data/file','../outside','/absolute','sample/CON','sample/x:stream'):
            with self.subTest(name=name),self.assertRaises(ValueError):p.wheel_files(wheel({name:b'content'}))

    def test_runner_relocation_script_module_and_console_modes(self):
        tree=self.base/'original';tree.mkdir();(tree/'vendor').mkdir()
        shutil.copyfile(TOOLS/'portable_runner.py',tree/'vf_runner.py')
        (tree/'vendor/sample.py').write_text('def main():\n print("portable-console")\nif __name__ == "__main__":\n print("portable-module")\n')
        registry={'schema':'vf.runtime-console/v1','commands':{'sample':{'type':'callable','target':'sample:main'}}}
        (tree/'console-commands.json').write_text(json.dumps(registry))
        moved=self.base/'moved parent with spaces'/'tree';moved.parent.mkdir();tree.rename(moved)
        script=self.base/'check.py';script.write_text('import sample\nsample.main()\n')
        cache=self.base/'cache';env={**os.environ,'VF_RUNTIME_CACHE':str(cache)}
        for mode,target,expected in [('module','sample','portable-module'),('command','sample','portable-console'),('script',str(script),'portable-console')]:
            cp=subprocess.run([sys.executable,'-I','-B',str(moved/'vf_runner.py'),mode,target],env=env,capture_output=True,text=True)
            self.assertEqual(cp.returncode,0,cp.stderr);self.assertEqual(cp.stdout.strip(),expected)
        self.assertFalse(any(moved.rglob('__pycache__')))
        env['VF_RUNTIME_CACHE']=str(moved/'cache')
        cp=subprocess.run([sys.executable,'-I','-B',str(moved/'vf_runner.py'),'info'],env=env,capture_output=True,text=True)
        self.assertNotEqual(cp.returncode,0);self.assertIn('CACHE_INSIDE_IMMUTABLE_RUNTIME',cp.stderr)

    def test_archive_receiver_and_corrupted_object_rejection(self):
        folder=self.base/'candidate';folder.mkdir();(folder/'objects').mkdir()
        raw=b'payload';h=p.digest(raw);(folder/'objects'/h).write_bytes(raw)
        manifest={'schema':p.store.SCHEMA,'release':'fixture.1','source_sha256':'a'*64,
                  'entries':{'vendor/sample.py':{'type':'file','sha256':h,'size':len(raw),'mode':0o644}}}
        tree_raw=p.store.tx.encode(manifest);tree_sha=p.digest(tree_raw)
        provenance={'schema':'vf.portable-runtime-provenance/v1','source_component_sha256':'a'*64,
                    'entries':{'vendor/sample.py':{'output_sha256':h}}}
        prov_raw=p.bundle.encoded(provenance)
        descriptor={'schema':p.SCHEMA,'release':'fixture.1','platform':'macos-arm64','python_version':'3.12.16',
                    'tree_sha256':tree_sha,'authentication':'unsigned','installable':False,'public_distribution':False,
                    'windows_execution':False,'desktop_acceptance':False,'layout':'original-python-and-vendor-wheels-with-vf-runner',
                    'files':{'tree-manifest.json':p.digest(tree_raw),'provenance.json':p.digest(prov_raw)}}
        for name,value in [('tree-manifest.json',tree_raw),('provenance.json',prov_raw),('portable-runtime.json',p.bundle.encoded(descriptor))]:
            (folder/name).write_bytes(value)
        archive=self.base/'runtime.zip';r=p.archive(folder,archive)
        self.assertEqual(p.verify(archive,r['sha256'])['status'],'portable_archive_verified')
        output=self.base/'recipient';p.unpack(archive,r['sha256'],output)
        self.assertEqual((output/'objects'/h).read_bytes(),raw)
        with self.assertRaisesRegex(ValueError,'NEW_OUTPUT_REQUIRED'):p.unpack(archive,r['sha256'],output)
        altered=self.base/'altered.zip'
        with zipfile.ZipFile(archive) as source,zipfile.ZipFile(altered,'w') as target:
            for item in source.infolist():target.writestr(item,b'changed' if item.filename=='objects/'+h else source.read(item))
        bad_sha=p.digest(altered.read_bytes());rejected=self.base/'must-not-exist'
        with self.assertRaises(ValueError):p.unpack(altered,bad_sha,rejected)
        self.assertFalse(rejected.exists())
        with self.assertRaisesRegex(ValueError,'PORTABLE_ARCHIVE_HASH_MISMATCH'):p.verify(archive,'0'*64)

    def test_platform_wrappers_reference_only_adjacent_runtime(self):
        unix=p.wrappers('macos-arm64')['bin/vf-python'][0]
        windows=p.wrappers('windows-amd64')['bin/vf-python.cmd'][0]
        self.assertIn(b'python/bin/python3.12',unix);self.assertIn(b'%~dp0..\\python\\python.exe',windows)
        self.assertIn(b'-I -B',unix);self.assertIn(b'-I -B',windows)
        self.assertNotIn(os.fsencode(Path.cwd()),unix+windows)

    @unittest.skipUnless(os.name=='posix','POSIX shell execution only')
    def test_posix_wrapper_empty_path_and_managed_command_after_parent_move(self):
        original=self.base/'original parent';(original/'bin').mkdir(parents=True)
        (original/'python/bin').mkdir(parents=True)
        entry=original/'bin/vf-python';raw,mode=p.wrappers('macos-arm64')['bin/vf-python']
        entry.write_bytes(raw);entry.chmod(mode)
        # A shell fixture exposes actual argv/PATH; it never impersonates Python acceptance.
        stub=original/'python/bin/python3.12'
        stub.write_text('#!/bin/sh\n[ "$1" = -I ] && [ "$2" = -B ] || exit 3\nshift 3\nprintf "%s\\n" "$PATH" "$@"\nvf-test-media\n')
        stub.chmod(0o755)
        tool=original/'bin/vf-test-media';tool.write_text('#!/bin/sh\nprintf "managed-media-found\\n"\n');tool.chmod(0o755)
        moved=self.base/'relocated parent with spaces';original.rename(moved)
        cp=subprocess.run([str(moved/'bin/vf-python'),'argument with spaces'],env={'PATH':''},capture_output=True,text=True)
        self.assertEqual(cp.returncode,0,cp.stderr)
        self.assertEqual(cp.stdout.splitlines(),[str(moved/'bin'),'argument with spaces','managed-media-found'])
        self.assertFalse(original.exists());self.assertNotIn(b'dirname',raw)


if __name__=='__main__':unittest.main()
