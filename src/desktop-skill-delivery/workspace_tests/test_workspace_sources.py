"""SDK source archives support Unicode while rejecting aliases before writes."""
import hashlib
import io
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'workspace_tools'))
import workspace_sources as s

BASE=Path(__file__).resolve().parents[4]/'.tmp/workspace-source-tests'


class SDKTests(unittest.TestCase):
    def setUp(self):
        BASE.mkdir(parents=True,exist_ok=True);self.temp=tempfile.TemporaryDirectory(dir=BASE)
        self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name).absolute()

    def archive(self,extra):
        path=self.root/'sdk.tar.gz'
        with tarfile.open(path,'w:gz') as archive:
            for name,kind,data in [('go',tarfile.DIRTYPE,b'')]+extra:
                m=tarfile.TarInfo(name);m.type=kind;m.mode=0o755 if name=='go/bin/go' else 0o644
                if kind==tarfile.REGTYPE:m.size=len(data);archive.addfile(m,io.BytesIO(data))
                else:
                    if kind in (tarfile.SYMTYPE,tarfile.LNKTYPE):m.linkname=data.decode()
                    archive.addfile(m)
        lock={'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'bytes':path.stat().st_size,'version':'go1.26.11'}
        return path,lock

    def test_original_unicode_bytes_and_executable_modes_preserved(self):
        p,lock=self.archive([('go/bin/go',tarfile.REGTYPE,b'compiler'),('go/test/\u00defoo.go',tarfile.REGTYPE,b'original Unicode source')])
        r=s.extract_sdk(p,self.root/'out',lock)
        self.assertEqual(r['unicode_entries'],1)
        self.assertEqual((self.root/'out/test/\u00defoo.go').read_bytes(),b'original Unicode source')
        self.assertEqual((self.root/'out/bin/go').stat().st_mode&0o777,0o755)
        with self.assertRaisesRegex(ValueError,'NEW_SDK_OUTPUT_REQUIRED'):s.extract_sdk(p,self.root/'out',lock)

    def test_graph_paths_types_case_and_unicode_aliases_reject_before_output(self):
        groups=[ [('go/../outside',tarfile.REGTYPE,b'a')],
                 [('go/a',tarfile.SYMTYPE,b'../../outside')],
                 [('go/a',tarfile.LNKTYPE,b'go/bin/go')],
                 [('go/a',tarfile.FIFOTYPE,b'')],
                 [('go/a',tarfile.REGTYPE,b'a'),('go/a',tarfile.REGTYPE,b'b')],
                 [('go/A',tarfile.REGTYPE,b'a'),('go/a/child',tarfile.REGTYPE,b'b')],
                 [('go/include/Foo/a',tarfile.REGTYPE,b'a'),('go/include/foo/b',tarfile.REGTYPE,b'b')],
                 [('go/test/\u00e9.go',tarfile.REGTYPE,b'a'),('go/test/e\u0301.go',tarfile.REGTYPE,b'b')],
                 [('go/test/\u00defoo.go',tarfile.REGTYPE,b'a'),('go/test/\u00fefoo.go',tarfile.REGTYPE,b'b')],
                 [('elsewhere/a',tarfile.REGTYPE,b'a')]]
        for extra in groups:
            p,lock=self.archive(extra)
            with self.subTest(extra=extra),self.assertRaises(ValueError):s.extract_sdk(p,self.root/'out',lock)
            self.assertFalse((self.root/'out').exists())

    def test_hash_and_symlink_inputs_reject_before_output(self):
        p,lock=self.archive([('go/file',tarfile.REGTYPE,b'a')]);lock['sha256']='0'*64
        with self.assertRaisesRegex(ValueError,'SDK_SOURCE_HASH_MISMATCH'):s.extract_sdk(p,self.root/'out',lock)
        link=self.root/'alias';link.symlink_to(p)
        with self.assertRaisesRegex(ValueError,'WORKSPACE_INPUT_LINK'):s.extract_sdk(link,self.root/'out',lock)
        self.assertFalse((self.root/'out').exists())


if __name__=='__main__':unittest.main()
