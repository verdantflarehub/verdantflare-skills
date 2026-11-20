"""Go module hash changes and unsafe members must reject, without extraction."""
from pathlib import Path
import stat
import sys
import tempfile
import unittest
import warnings
import zipfile

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'workspace_tools'))
import workspace_modules as m

BASE=Path(__file__).resolve().parents[4]/'.tmp/workspace-source-tests'
ROOT='example.invalid/module@v1.0.0'


class ModuleTests(unittest.TestCase):
    def setUp(self):
        BASE.mkdir(parents=True,exist_ok=True);self.temp=tempfile.TemporaryDirectory(dir=BASE)
        self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name).absolute()

    def archive(self,extra=()):
        p=self.root/'module.zip'
        with zipfile.ZipFile(p,'w') as z:
            for name,content,mode in [(ROOT+'/go.mod',b'module example.invalid/module\n',stat.S_IFREG|0o644),
                                      (ROOT+'/LICENSE',b'original license',stat.S_IFREG|0o644)]+list(extra):
                i=zipfile.ZipInfo(name);i.create_system=3;i.external_attr=mode<<16
                with warnings.catch_warnings():
                    warnings.filterwarnings('ignore',message='Duplicate name:.*',category=UserWarning)
                    z.writestr(i,content)
        return p

    def test_go_sum_change_rejects_and_matching_bytes_reported(self):
        p=self.archive()
        with self.assertRaisesRegex(ValueError,'MODULE_GO_SUM_MISMATCH'):
            m.verify_module_zip(p,'example.invalid/module','v1.0.0','h1:'+'A'*43+'=')
        # Independent Go 1.26.11 `go mod download` against a local fixture proxy.
        expected='h1:H4fjdzUmr9zEXELDAQKtnUJ1TBvVtFYVGHTHRAXKIKc='
        self.assertEqual(m.mod_sum(b'module example.invalid/module\n'),
                         'h1:H3dH2PiuZgWDO5S/2tMIuPnky5EjpQd28ez/79NqLmQ=')
        r=m.verify_module_zip(p,'example.invalid/module','v1.0.0',expected)
        self.assertEqual(r['license_files'],['LICENSE']);self.assertEqual(set(r['files']),{'go.mod','LICENSE'})
        self.assertEqual(sorted(f.name for f in self.root.iterdir()),['module.zip'])

    def test_paths_links_aliases_and_duplicates_reject_before_hash_acceptance(self):
        groups=[ [(ROOT+'/../outside',b'a',stat.S_IFREG|0o644)],
                 [(ROOT+'/link',b'outside',stat.S_IFLNK|0o777)],
                 [(ROOT+'/LICENSE',b'duplicate',stat.S_IFREG|0o644)],
                 [(ROOT+'/Foo/a',b'a',stat.S_IFREG|0o644),(ROOT+'/foo/b',b'b',stat.S_IFREG|0o644)],
                 [('other@v1.0.0/file',b'a',stat.S_IFREG|0o644)],
                 [(ROOT+'/a',b'a',stat.S_IFREG|0o644),(ROOT+'/a/b',b'b',stat.S_IFREG|0o644)]]
        for extra in groups:
            with self.subTest(extra=extra):
                p=self.archive(extra)
                with self.assertRaises(ValueError) as result:m.verify_module_zip(p,'example.invalid/module','v1.0.0','h1:'+'A'*43+'=')
                self.assertNotEqual(str(result.exception),'MODULE_GO_SUM_MISMATCH')


if __name__=='__main__':unittest.main()
