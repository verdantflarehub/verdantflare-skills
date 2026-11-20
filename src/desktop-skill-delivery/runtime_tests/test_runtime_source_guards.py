"""Source lock and interpreter ABI mismatch rejection, using tiny fixtures."""
import copy
import io
import json
import sys
from pathlib import Path
import unittest
import zipfile

sys.path.insert(0,str(Path(__file__).parent))
import test_runtime_bundle as fixtures
import runtime_bundle as b


class RuntimeSourceGuardTests(unittest.TestCase):
    def setUp(self):
        self.fixture=fixtures.RuntimeBundleTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def test_download_metadata_digest_must_bind_actual_release_bytes(self):
        f=self.fixture
        path=f.folder/'download-receipt.json'
        receipt=json.loads(path.read_text());receipt['source_metadata_sha256']='0'*64
        f.put(path,receipt)
        with self.assertRaisesRegex(ValueError,'UPSTREAM_METADATA_HASH_MISMATCH'):
            b.assemble(f.source,'macos-arm64','fixture.1')

    def test_metadata_change_requires_new_source_lock_even_same_assets(self):
        f=self.fixture;path=f.source/'release.json'
        data=json.loads(path.read_text());data['new_metadata']='changed'
        f.put(path,data)
        with self.assertRaisesRegex(ValueError,'UPSTREAM_METADATA_HASH_MISMATCH'):
            b.assemble(f.source,'macos-arm64','fixture.1')

    def wheel(self,tag):
        data=io.BytesIO()
        with zipfile.ZipFile(data,'w') as archive:
            archive.writestr('sample-1.0.dist-info/METADATA','Name: sample\nVersion: 1.0\n')
            archive.writestr('sample-1.0.dist-info/WHEEL','Wheel-Version: 1.0\nTag: '+tag+'\n')
            archive.writestr('sample-1.0.dist-info/licenses/LICENSE','Synthetic wheel license')
        record=copy.deepcopy(self.fixture.record);record['tags']=[tag]
        return data.getvalue(),record

    def test_incompatible_abi_is_rejected_even_matching_os_and_python(self):
        for tag in ('cp312-cp311-macosx_11_0_arm64','py3-cp311-macosx_11_0_arm64',
                    'cp313-abi3-macosx_11_0_arm64','cp31-abi3-macosx_11_0_arm64',
                    'cp312-cp312-any','cp312-cp312t-macosx_11_0_arm64'):
            with self.subTest(tag=tag),self.assertRaisesRegex(ValueError,'WHEEL_PLATFORM_INCOMPATIBLE'):
                b.inspect_wheel(*self.wheel(tag),'macos-arm64')

    def test_native_and_older_stable_abi_remain_accepted(self):
        for tag in ('cp312-cp312-macosx_11_0_arm64','cp39-abi3-macosx_11_0_arm64',
                    'cp312-none-any','py3-none-any'):
            with self.subTest(tag=tag):
                b.inspect_wheel(*self.wheel(tag),'macos-arm64')


if __name__=='__main__':unittest.main()
