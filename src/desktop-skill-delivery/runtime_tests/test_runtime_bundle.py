"""Tiny synthetic bundles exercise integrity failures, not executable runtimes."""
import copy
import io
import json
from pathlib import Path
import stat
import sys
import tempfile
import unittest
import zipfile

TOOLS=Path(__file__).resolve().parents[1]/'runtime_tools'
sys.path.insert(0,str(TOOLS))
import runtime_bundle as b


class RuntimeBundleTests(unittest.TestCase):
    def setUp(self):
        root=Path(__file__).resolve().parents[4]/'.tmp/runtime-bundle-tests'
        root.mkdir(parents=True,exist_ok=True)
        self.temp=tempfile.TemporaryDirectory(dir=root);self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.source=self.root/'source'
        self.folder=self.source/'macos-arm64';self.folder.mkdir(parents=True)
        self.archive=self.root/'runtime.zip'
        raw=b'synthetic non-executable interpreter archive'
        archive_name='cpython-3.12.16+fixture-install_only.tar.gz'
        (self.folder/archive_name).write_bytes(raw)
        download={'platform':'macos-arm64','filename':archive_name,'sha256':b.sha(raw),'size':len(raw),
                  'source_release':'fixture','source_url':'https://github.com/astral-sh/python-build-standalone/releases/download/fixture/'+archive_name,'source_metadata_sha256':'a'*64}
        self.put(self.folder/'download-receipt.json',download)
        self.put(self.source/'release.json',{'tag_name':'fixture','assets':[{'name':archive_name,'digest':'sha256:'+b.sha(raw),'size':len(raw),'browser_download_url':download['source_url']}]})
        download['source_metadata_sha256']=b.sha((self.source/'release.json').read_bytes())
        self.put(self.folder/'download-receipt.json',download)
        build={'target_triple':'aarch64-apple-darwin','python_major_minor_version':'3.12','python_version':'3.12.16','license_path':'licenses/LICENSE.cpython.txt'}
        self.put(self.folder/'build-metadata/selected/python/PYTHON.json',build)
        (self.folder/'build-metadata/selected/python/licenses').mkdir()
        (self.folder/'build-metadata/selected/python/licenses/LICENSE.cpython.txt').write_text('Synthetic license')
        self.filename='sample-1.0-py3-none-any.whl'
        payload=io.BytesIO()
        with zipfile.ZipFile(payload,'w') as archive:
            archive.writestr('sample-1.0.dist-info/METADATA','Name: sample\nVersion: 1.0\n')
            archive.writestr('sample-1.0.dist-info/WHEEL','Wheel-Version: 1.0\nTag: py3-none-any\n')
            archive.writestr('sample-1.0.dist-info/licenses/LICENSE','Synthetic wheel license')
        wheel=payload.getvalue();(self.folder/'wheels').mkdir();(self.folder/'wheels'/self.filename).write_bytes(wheel)
        self.record={'package':'sample','version':'1.0','filename':self.filename,'size':len(wheel),'sha256':b.sha(wheel),
                     'url':'https://files.pythonhosted.org/packages/fixture/'+self.filename,'tags':['py3-none-any'],
                     'requires_python':'>=3.10','requires_dist':[],
                     'license_files':[{'path':'sample-1.0.dist-info/licenses/LICENSE','sha256':b.sha(b'Synthetic wheel license')}]}
        self.put(self.folder/'verified-wheel-lock.json',{'platform':'macos-arm64','wheels':[self.record]})
        self.put(self.source/'pypi/sample-1.0.json',{'urls':[{'filename':self.filename,'url':self.record['url'],'size':len(wheel),'digests':{'sha256':b.sha(wheel)}}]})

    @staticmethod
    def put(path,value):
        path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(b.encoded(value))

    def build(self):
        return b.build(self.source,'macos-arm64','fixture.1',self.archive)

    def mutate(self,change):
        with zipfile.ZipFile(self.archive) as archive:files={n:archive.read(n) for n in archive.namelist()}
        change(files)
        manifest=b.decode(files['runtime-manifest.json'])
        manifest['files']={n:{'size':len(raw),'sha256':b.sha(raw)} for n,raw in files.items() if n!='runtime-manifest.json'}
        files['runtime-manifest.json']=b.encoded(manifest)
        with zipfile.ZipFile(self.archive,'w') as archive:
            for n,raw in files.items():
                info=zipfile.ZipInfo(n);info.create_system=3;info.external_attr=(stat.S_IFREG|0o644)<<16
                archive.writestr(info,raw)
        return b.sha(self.archive.read_bytes())

    def test_roundtrip_determinism_and_no_installation_claims(self):
        result=self.build()
        other=self.root/'second.zip';b.build(self.source,'macos-arm64','fixture.1',other)
        self.assertEqual(self.archive.read_bytes(),other.read_bytes())
        self.assertEqual(result['wheels'],1)
        self.assertFalse(result['installable']);self.assertFalse(result['desktop_acceptance'])
        self.assertEqual(result['authentication'],'unsigned')

    def test_changed_wheel_and_wrong_expected_archive_hash_reject(self):
        self.build()
        with self.assertRaisesRegex(ValueError,'ARCHIVE_HASH_MISMATCH'):b.verify(self.archive,'0'*64)
        (self.folder/'wheels'/self.filename).write_bytes(b'corrupt')
        with self.assertRaisesRegex(ValueError,'SOURCE_HASH_OR_SIZE_MISMATCH'):
            b.assemble(self.source,'macos-arm64','fixture.1')

    def test_existing_output_is_never_overwritten(self):
        self.archive.write_bytes(b'original')
        with self.assertRaisesRegex(ValueError,'NEW_OUTPUT_REQUIRED'):self.build()
        self.assertEqual(self.archive.read_bytes(),b'original')

    def test_paths_and_symlink_inputs_reject(self):
        for path in ('../outside','/absolute','a//b','CON.txt','a/NUL','a\\b'):
            with self.subTest(path=path),self.assertRaises(ValueError):b.relative(path)
        original=self.folder/'download-receipt.json';hidden=self.folder/'original.json'
        original.rename(hidden);original.symlink_to(hidden)
        with self.assertRaisesRegex(ValueError,'LINKED_PATH'):self.build()

    def test_claimed_installability_rejects_even_with_updated_archive_hash(self):
        self.build()
        def change(files):
            m=b.decode(files['runtime-manifest.json']);m['installable']=True;files['runtime-manifest.json']=b.encoded(m)
        digest=self.mutate(change)
        with self.assertRaisesRegex(ValueError,'UNSUPPORTED_ACCEPTANCE_CLAIM'):b.verify(self.archive,digest)

    def test_removed_licensing_reference_rejects_against_build_metadata(self):
        self.build()
        def change(files):
            sources=b.decode(files['sources.json']);sources['interpreter_license_paths']=[];files['sources.json']=b.encoded(sources)
            del files['licenses/python/LICENSE.cpython.txt']
        digest=self.mutate(change)
        with self.assertRaisesRegex(ValueError,'INTERPRETER_LICENSE_SET_MISMATCH'):b.verify(self.archive,digest)

    def test_requirements_omission_rejects(self):
        self.build();digest=self.mutate(lambda files:files.update({'requirements.lock':b'\n'}))
        with self.assertRaisesRegex(ValueError,'REQUIREMENTS_MISMATCH'):b.verify(self.archive,digest)

    def test_wrong_wheel_platform_metadata_and_license_reject(self):
        raw=(self.folder/'wheels'/self.filename).read_bytes()
        for key,value,code in (('tags',['cp312-cp312-win_amd64'],'WHEEL_TAG_MISMATCH'),
                               ('package','different','WHEEL_DISTRIBUTION_MISMATCH'),
                               ('license_files',[{'path':'sample-1.0.dist-info/licenses/LICENSE','sha256':'0'*64}],'WHEEL_LICENSE_HASH_MISMATCH')):
            record=copy.deepcopy(self.record);record[key]=value
            with self.subTest(code=code),self.assertRaisesRegex(ValueError,code):b.inspect_wheel(raw,record,'macos-arm64')

    def test_manifest_duplicate_keys_and_duplicate_zip_members_reject(self):
        with self.assertRaisesRegex(ValueError,'DUPLICATE_JSON_KEY'):b.decode(b'{"a":1,"a":2}')
        self.build()
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter('ignore')
            with zipfile.ZipFile(self.archive,'a') as archive:archive.writestr('runtime-manifest.json',b'{}')
        with self.assertRaisesRegex(ValueError,'INVALID_MEMBER_SET'):b.verify(self.archive,b.sha(self.archive.read_bytes()))


if __name__=='__main__':unittest.main()
