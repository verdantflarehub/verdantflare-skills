"""Cross-toolchain links must be internal and validated before writes."""
import hashlib
import io
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'media_tools'))
import prepare_cross_toolchain as t

BASE=Path(__file__).resolve().parents[4]/'.tmp/media-source-tests'


class ToolchainTests(unittest.TestCase):
    def setUp(self):
        BASE.mkdir(parents=True,exist_ok=True);self.temp=tempfile.TemporaryDirectory(dir=BASE)
        self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name).absolute()

    def archive(self,extra):
        path=self.root/'toolchain.tar.gz'
        with tarfile.open(path,'w:gz') as archive:
            for name,kind,data in [('toolchain',tarfile.DIRTYPE,b''),
                                   ('toolchain/bin',tarfile.DIRTYPE,b''),
                                   ('toolchain/lib',tarfile.DIRTYPE,b''),
                                   ('toolchain/bin/clang',tarfile.REGTYPE,b'original compiler')]+extra:
                member=tarfile.TarInfo(name);member.type=kind;member.mode=0o755
                if kind==tarfile.REGTYPE:
                    member.size=len(data);archive.addfile(member,io.BytesIO(data))
                else:
                    if kind in (tarfile.SYMTYPE,tarfile.LNKTYPE):member.linkname=data.decode()
                    archive.addfile(member)
        return path,hashlib.sha256(path.read_bytes()).hexdigest()

    def unpack(self,p,h):
        return t.prepare(p,self.root/'out',expected_sha256=h,root='toolchain')

    def test_internal_file_directory_and_chain_links_preserve_bytes(self):
        p,h=self.archive([('toolchain/bin/cc',tarfile.SYMTYPE,b'clang'),
                          ('toolchain/bin/gcc',tarfile.SYMTYPE,b'cc'),
                          ('toolchain/lib/tools',tarfile.SYMTYPE,b'../bin')])
        result=self.unpack(p,h)
        self.assertEqual(result['internal_symlinks'],3)
        self.assertEqual((self.root/'out/lib/tools/gcc').read_bytes(),b'original compiler')
        self.assertTrue((self.root/'out/bin/cc').is_symlink())
        self.assertEqual((self.root/'out/bin/clang').stat().st_mode&0o777,0o755)

    def test_hash_rejects_before_output(self):
        p,h=self.archive([])
        with self.assertRaisesRegex(ValueError,'TOOLCHAIN_HASH_MISMATCH'):self.unpack(p,'0'*64)
        self.assertFalse((self.root/'out').exists())

    def test_invalid_graph_and_members_reject_before_output(self):
        cases=[ [('toolchain/bin/out',tarfile.SYMTYPE,b'../../outside')],
                [('toolchain/bin/out',tarfile.SYMTYPE,b'/outside')],
                [('toolchain/bin/out',tarfile.SYMTYPE,b'missing')],
                [('toolchain/bin/a',tarfile.SYMTYPE,b'b'),('toolchain/bin/b',tarfile.SYMTYPE,b'a')],
                [('toolchain/bin/out',tarfile.LNKTYPE,b'toolchain/bin/clang')],
                [('toolchain/../outside',tarfile.REGTYPE,b'a')],
                [('toolchain/BIN/other',tarfile.REGTYPE,b'a')],
                [('toolchain/bin/clang',tarfile.REGTYPE,b'duplicate')],
                [('toolchain/bin/cc',tarfile.SYMTYPE,b'clang'),('toolchain/bin/cc/file',tarfile.REGTYPE,b'a')],
                [('toolchain/bin/cc',tarfile.SYMTYPE,b'clang'),('toolchain/bin/bad',tarfile.SYMTYPE,b'cc/../clang')]]
        for extra in cases:
            with self.subTest(extra=extra):
                p,h=self.archive(extra)
                with self.assertRaises(ValueError):self.unpack(p,h)
                self.assertFalse((self.root/'out').exists())

    def test_implicit_directory_case_aliases_reject_before_output(self):
        groups=[ [('toolchain/include/Foo/a',tarfile.REGTYPE,b'a'),('toolchain/include/foo/b',tarfile.REGTYPE,b'b')],
                 [('toolchain/Include/a',tarfile.REGTYPE,b'a'),('toolchain/include/b',tarfile.REGTYPE,b'b')],
                 [('toolchain/A',tarfile.REGTYPE,b'a'),('toolchain/a/child',tarfile.REGTYPE,b'b')]]
        for extra in groups:
            p,h=self.archive(extra)
            with self.subTest(extra=extra),self.assertRaisesRegex(ValueError,'TOOLCHAIN_CASE_ALIAS'):
                self.unpack(p,h)
            self.assertFalse((self.root/'out').exists())


if __name__=='__main__':unittest.main()
