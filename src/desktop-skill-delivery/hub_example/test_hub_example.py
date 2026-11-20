"""Hub example recipient behavior; uses the fixed accepted r39 handoff."""
from pathlib import Path
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile

TOOL=Path(__file__).with_name('hub_example.py')
ROOT=Path(__file__).resolve().parents[4]
SOURCE=ROOT/'.tmp/agent-skill-delivery/20261119-handoff-8/vf-skills-0.2.0-integration.6.zip'
BASE=ROOT/'.tmp/hub-example-tests'


class HubExampleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not SOURCE.is_file():raise RuntimeError('Fixed r39 handoff required; no synthetic substitute')
        BASE.mkdir(parents=True,exist_ok=True)
        cls.temp=tempfile.TemporaryDirectory(dir=BASE);cls.base=Path(cls.temp.name).absolute()
        cls.archive=cls.base/'example.zip'
        result=cls.run_cli('build','--source',str(SOURCE),'--output',str(cls.archive),cwd=ROOT)
        cls.receipt=json.loads(result.stdout);cls.recipient=cls.base/'recipient'
        with zipfile.ZipFile(cls.archive) as z:z.extractall(cls.recipient)

    @classmethod
    def tearDownClass(cls):cls.temp.cleanup()

    @staticmethod
    def run_cli(*args,cwd,expected=0,tool=TOOL):
        result=subprocess.run([sys.executable,'-B',str(tool),*args],cwd=cwd,capture_output=True,text=True)
        if result.returncode!=expected:raise AssertionError((result.returncode,result.stdout,result.stderr))
        return result

    def test_deterministic_build_preserves_original_members(self):
        second=self.base/'second.zip'
        self.run_cli('build','--source',str(SOURCE),'--output',str(second),cwd=ROOT)
        self.assertEqual(self.archive.read_bytes(),second.read_bytes())
        with zipfile.ZipFile(SOURCE) as old,zipfile.ZipFile(self.archive) as new:
            for name in old.namelist():self.assertEqual(old.read(name),new.read(name),name)
        self.assertEqual(hashlib.sha256(self.archive.read_bytes()).hexdigest(),self.receipt['sha256'])

    def test_standalone_both_verifiers_and_project_dependency_download(self):
        root=self.recipient;script=root/'hub_example.py'
        value=json.loads(self.run_cli('verify','--root','.',cwd=root,tool=script).stdout)
        self.assertEqual(value['skills'],11)
        original=self.run_cli('verify','candidate',cwd=root,tool=root/'tooling/delivery.py')
        self.assertEqual(json.loads(original.stdout)['status'],'integrity_verified')
        p=json.loads(self.run_cli('plan','--skill','verdantflare-project',cwd=root,tool=script).stdout)
        self.assertEqual([x['id'] for x in p['orderedPackages']],['verdantflare-artifact','verdantflare-project'])
        self.assertEqual(p['downloadBytes'],37509);self.assertFalse(p['installAllowed'])
        target=self.base/'download'
        result=json.loads(self.run_cli('download','--skill','verdantflare-project','--output',str(target),cwd=root,tool=script).stdout)
        self.assertEqual(result['status'],'engineering_bytes_copied')
        self.assertFalse(result['installationPerformed'])
        for package in p['orderedPackages']:
            raw=(target/(package['id']+'.zip')).read_bytes()
            self.assertEqual(raw,(root/package['path']).read_bytes())
            self.assertEqual(hashlib.sha256(raw).hexdigest(),package['sha256'])
        self.run_cli('verify','--root','.',cwd=root,tool=script)
        self.run_cli('download','--skill','verdantflare-project','--output',str(target),cwd=root,tool=script,expected=2)

    def test_all_skills_closure_shared_deps_and_no_unsupported_claims(self):
        fixture=json.loads((self.recipient/'hub-example.json').read_bytes())
        for skill in fixture['skills']:
            result=json.loads(self.run_cli('plan','--root',str(self.recipient),'--skill',skill['id'],cwd=self.base).stdout)
            ids=[p['id'] for p in result['orderedPackages']]
            self.assertEqual(len(ids),len(set(ids)));self.assertEqual(ids[-1],skill['id'])
            self.assertFalse(result['installAllowed'])
        self.assertEqual(len(fixture['supportMatrix']),10)
        self.assertEqual({r['status'] for r in fixture['supportMatrix']},{'not_run'})
        self.assertIsNone(fixture['publishedReleaseId']);self.assertTrue(fixture['sample'])
        self.assertEqual(fixture['distribution'],'unavailable')

    def test_scenarios_unknown_ids_and_no_output_on_rejection(self):
        root=self.recipient
        for scenario,reason in [('withdrawn','SIMULATED_RELEASE_WITHDRAWN'),('checksum-mismatch','SIMULATED_CHECKSUM_MISMATCH')]:
            output=self.base/('reject-'+scenario)
            result=self.run_cli('download','--root',str(root),'--skill','verdantflare-project','--scenario',scenario,'--output',str(output),cwd=self.base,expected=2)
            self.assertEqual(json.loads(result.stderr)['error'],reason);self.assertFalse(output.exists())
        result=json.loads(self.run_cli('plan','--root',str(root),'--skill','verdantflare-project','--scenario','missing-installer',cwd=self.base).stdout)
        self.assertEqual(result['reason'],'INSTALLER_NOT_DELIVERED');self.assertFalse(result['installAllowed'])
        self.run_cli('plan','--root',str(root),'--skill','unknown',cwd=self.base,expected=2)

    def test_real_corruption_links_and_unknown_files_are_rejected(self):
        root=self.base/'corrupted';shutil.copytree(self.recipient,root)
        asset=root/'candidate/packages/verdantflare-project.zip';raw=asset.read_bytes();asset.write_bytes(raw+b'changed')
        result=self.run_cli('verify','--root',str(root),cwd=self.base,expected=2)
        self.assertEqual(json.loads(result.stderr)['error'],'EXAMPLE_FILE_HASH_MISMATCH')
        output=self.base/'corrupt-download'
        self.run_cli('download','--root',str(root),'--skill','verdantflare-project','--output',str(output),cwd=self.base,expected=2)
        self.assertFalse(output.exists());asset.write_bytes(raw)
        unknown=root/'unknown';unknown.write_bytes(b'preserve')
        self.run_cli('verify','--root',str(root),cwd=self.base,expected=2);self.assertEqual(unknown.read_bytes(),b'preserve')
        unknown.unlink();asset.unlink();asset.symlink_to(self.recipient/'candidate/packages/verdantflare-project.zip')
        self.run_cli('verify','--root',str(root),cwd=self.base,expected=2)


if __name__=='__main__':unittest.main()
