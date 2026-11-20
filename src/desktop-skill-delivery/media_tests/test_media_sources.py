"""Source tar/hash boundaries for local media builds; no compiler invocation."""
from pathlib import Path
import hashlib
import io
import sys
import tarfile
import tempfile
import unittest

TOOLS=Path(__file__).resolve().parents[1]/'media_tools';sys.path.insert(0,str(TOOLS))
import build_media_runtime as m
BASE=Path(__file__).resolve().parents[4]/'.tmp/media-source-tests'


class SourceTests(unittest.TestCase):
    def setUp(self):
        BASE.mkdir(parents=True,exist_ok=True);self.temp=tempfile.TemporaryDirectory(dir=BASE)
        self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name).absolute()

    def archive(self,members):
        p=self.root/'source.tar.gz'
        with tarfile.open(p,'w:gz') as archive:
            for name,content,kind in members:
                info=tarfile.TarInfo(name);info.type=kind
                if kind==tarfile.REGTYPE:
                    info.size=len(content);info.mode=0o755 if name.endswith('configure') else 0o644
                    archive.addfile(info,io.BytesIO(content))
                else:
                    info.linkname=content.decode();archive.addfile(info)
        return p,{'filename':p.name,'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'root':'source','url':'https://example.invalid/source'}

    def test_original_source_bytes_and_executable_bit(self):
        p,r=self.archive([('source/configure',b'#!sh\n',tarfile.REGTYPE),('source/LICENSE',b'license',tarfile.REGTYPE),
                          ('source/m4/lt~obsolete.m4',b'original m4',tarfile.REGTYPE)])
        self.assertEqual(m.source(r,self.root),p)
        out=self.root/'out';m.extract_source(p,r,out)
        self.assertEqual((out/'configure').read_bytes(),b'#!sh\n');self.assertEqual((out/'configure').stat().st_mode&0o777,0o755)
        self.assertEqual((out/'LICENSE').read_bytes(),b'license')
        self.assertEqual((out/'m4/lt~obsolete.m4').read_bytes(),b'original m4')
        with self.assertRaisesRegex(ValueError,'NEW_OUTPUT_REQUIRED'):m.extract_source(p,r,out)

    def test_hash_change_rejected_before_output(self):
        p,r=self.archive([('source/file',b'a',tarfile.REGTYPE)]);p.write_bytes(p.read_bytes()+b'changed')
        with self.assertRaisesRegex(ValueError,'SOURCE_HASH_MISMATCH'):m.extract_source(p,r,self.root/'out')
        self.assertFalse((self.root/'out').exists())

    def test_links_escapes_duplicate_and_prefix_rejected_before_output(self):
        groups=[ [('source/link',b'../../outside',tarfile.SYMTYPE)],
                 [('source/link',b'source/file',tarfile.LNKTYPE)],
                 [('source/../outside',b'a',tarfile.REGTYPE)],
                 [('other/file',b'a',tarfile.REGTYPE)],
                 [('source/a',b'a',tarfile.REGTYPE),('source/a',b'b',tarfile.REGTYPE)],
                 [('source/a',b'a',tarfile.REGTYPE),('source/a/child',b'b',tarfile.REGTYPE)]]
        for members in groups:
            p,r=self.archive(members)
            with self.subTest(members=members),self.assertRaises(ValueError):m.extract_source(p,r,self.root/'out')
            self.assertFalse((self.root/'out').exists())

    def test_symlink_source_and_parent_rejected(self):
        p,r=self.archive([('source/file',b'a',tarfile.REGTYPE)])
        link=self.root/'alias';link.symlink_to(p)
        with self.assertRaisesRegex(ValueError,'LINK_REJECTED'):m.extract_source(link,r,self.root/'out')
        folder=self.root/'folder';folder.mkdir();alias=self.root/'alias-dir';alias.symlink_to(folder,target_is_directory=True)
        with self.assertRaisesRegex(ValueError,'LINK_REJECTED'):m.extract_source(p,r,alias/'out')

    def test_case_aliases_include_implicit_directories_and_file_parents(self):
        groups=[ [('source/include/Foo/a',b'a',tarfile.REGTYPE),('source/include/foo/b',b'b',tarfile.REGTYPE)],
                 [('source/Include/a',b'a',tarfile.REGTYPE),('source/include/b',b'b',tarfile.REGTYPE)],
                 [('source/A',b'a',tarfile.REGTYPE),('source/a/b',b'b',tarfile.REGTYPE)],
                 [('source/DIR',b'',tarfile.DIRTYPE),('source/dir/file',b'a',tarfile.REGTYPE)]]
        for members in groups:
            p,r=self.archive(members)
            with self.subTest(members=members),self.assertRaisesRegex(ValueError,'SOURCE_CASE_ALIAS'):
                m.extract_source(p,r,self.root/'out')
            self.assertFalse((self.root/'out').exists())


if __name__=='__main__':unittest.main()
