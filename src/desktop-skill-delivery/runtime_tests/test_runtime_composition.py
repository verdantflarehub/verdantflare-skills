"""Original components must survive composition; tampering rejects before receive."""
from pathlib import Path
import json
import os
import shutil
import stat
import sys
import tempfile
import unittest
import zipfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'runtime_tools'))
sys.path.insert(0,str(ROOT/'workspace_tests'))
import runtime_composition as c
import test_workspace_delivery as workspace_fixtures

BASE=Path(__file__).resolve().parents[4]/'.tmp/composed-runtime-tests'


class CompositionTests(unittest.TestCase):
    def setUp(self):
        BASE.mkdir(parents=True,exist_ok=True);self.temp=tempfile.TemporaryDirectory(dir=BASE)
        self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name).absolute()
        fixture=workspace_fixtures.DeliveryTests();fixture.setUp();self.addCleanup(fixture.doCleanups)
        self.workspace,self.wd,self.wh=fixture.runtime()
        self.python=self.root/'python';self.python.mkdir();(self.python/'objects').mkdir()
        entries={};origins={}
        for name,raw,mode in [('python/bin/python3.12',b'not an interpreter',0o755),
                              ('bin/vf-python',b'original wrapper fixture',0o755),('vendor/sample.py',b'VALUE=42\n',0o644)]:
            r=c.entry(raw,mode);entries[name]=r;origins[name]={'output_sha256':r['sha256']}
            (self.python/'objects'/r['sha256']).write_bytes(raw)
        tree={'schema':c.store.SCHEMA,'release':'fixture.python','source_sha256':'a'*64,'entries':entries}
        provenance={'schema':'vf.portable-runtime-provenance/v1','source_component_sha256':'a'*64,'entries':origins}
        pd={'schema':c.portable.SCHEMA,'release':tree['release'],'platform':'macos-arm64','python_version':'3.12.16',
            'tree_sha256':c.digest(c.encode(tree)),'authentication':'unsigned','installable':False,'public_distribution':False,
            'windows_execution':False,'desktop_acceptance':False,'layout':'original-python-and-vendor-wheels-with-vf-runner',
            'files':{'tree-manifest.json':c.digest(c.encode(tree)),'provenance.json':c.digest(c.encode(provenance))}}
        for n,v in [('portable-runtime.json',pd),('tree-manifest.json',tree),('provenance.json',provenance)]:
            (self.python/n).write_bytes(c.encode(v))
        self.pa=self.root/'python.zip';self.ph=c.portable.archive(self.python,self.pa)['sha256']
        self.media=self.root/'media';self.media.mkdir();sources={}
        def add(name,raw,mode=0o644):c.workspace.add(self.media,name,raw,mode)
        raw=b'\xcf\xfa\xed\xfe\x0c\x00\x00\x01'+b'\0'*248
        for name in ('ffmpeg','ffprobe'):add('bin/'+name,raw,0o755)
        add('sources/build_media_runtime.py',b'# original builder fixture\n');add('licenses/ffmpeg/COPYING.GPLv3',b'original license')
        for name in ('ffmpeg','x264','zlib','pkgconf','nasm'):
            content=('original '+name+' source').encode();filename=name+'.tar.gz';add('sources/'+filename,content)
            sources[name]={'filename':filename,'root':name,'sha256':c.digest(content),'url':'https://example.invalid/'+filename}
        source_raw=c.encode({'schema':'vf.media-build-sources/v1','sources':sources});add('sources/media-source-lock.json',source_raw)
        receipt={'schema':'vf.media-runtime-build/v1','platform':'macos-arm64','license':'GPL-3.0-or-later',
                 'source_lock_sha256':c.digest(source_raw),'ffmpeg_source_sha256':sources['ffmpeg']['sha256'],
                 'builder_sha256':c.digest((self.media/'sources/build_media_runtime.py').read_bytes()),
                 'binaries':{n:{'sha256':c.digest(raw),'size':len(raw),'mode':0o755} for n in ('ffmpeg','ffprobe')},
                 'public_distribution':False,'installable':False,'desktop_acceptance':False,'windows_execution':False}
        add('build-receipt.json',c.encode(receipt));self.mi=self.root/'component-integrity.json'
        self.mv={'schema':'vf.media-component-integrity/v1','platform':'macos-arm64','candidate_files':c.workspace.inventory(self.media),
                 'source_closure_verified':True,'public_distribution':False,'installable':False,'windows_execution':False,'independent_review':'pending'}
        self.mi.write_bytes(c.encode(self.mv));self.mh=c.digest(self.mi.read_bytes())

    def build(self,name='candidate',**kw):
        values={'python_archive':self.pa,'python_sha':self.ph,'media_folder':self.media,'media_integrity':self.mi,
                'media_sha':self.mh,'workspace_folder':self.workspace,'workspace_sha':self.wh,
                'release':'fixture.composed','output':self.root/name};values.update(kw)
        return c.assemble(**values)

    def alter_archive(self,source,target,changes=None,extra=None):
        with zipfile.ZipFile(source) as z,zipfile.ZipFile(target,'w') as out:
            for item in z.infolist():out.writestr(item,(changes or {}).get(item.filename,z.read(item)))
            if extra:
                info=zipfile.ZipInfo(extra[0]);info.create_system=3;info.external_attr=(stat.S_IFREG|0o644)<<16
                out.writestr(info,extra[1])
        return c.digest(target.read_bytes())

    def test_all_original_components_restore_and_archive_reproduces(self):
        r=self.build();folder=self.root/'candidate';archive=self.root/'composed.zip'
        ah=c.archive(folder,r['descriptor_sha256'],archive)['archive_sha256']
        receiver=self.root/'receiver';c.receive(archive,ah,receiver)
        restored=self.root/'restored';rr=c.restore(receiver,r['descriptor_sha256'],restored)
        self.assertEqual(rr['python_archive_sha256'],self.ph)
        self.assertEqual((restored/'python-original.zip').read_bytes(),self.pa.read_bytes())
        for original,other in [(self.workspace,restored/'workspace'),(self.media,restored/'media')]:
            self.assertEqual(c.workspace.file_set(original),c.workspace.file_set(other))
            for n in c.workspace.file_set(original):
                self.assertEqual((original/n).read_bytes(),(other/n).read_bytes())
                self.assertEqual((original/n).stat().st_mode&0o777,(other/n).stat().st_mode&0o777)
        # @v paths survive only inside original metadata; the actual runtime path rule remains strict.
        manifest=c.decode((folder/'tree-manifest.json').read_bytes())
        self.assertTrue(any('@v' in n for n in self.wd['files']))
        self.assertFalse(any('@v' in n for n in manifest['entries']))
        self.build('rebuilt');archive2=self.root/'composed2.zip'
        c.archive(self.root/'rebuilt',r['descriptor_sha256'],archive2)
        self.assertEqual(archive.read_bytes(),archive2.read_bytes())

    def test_wrong_external_component_ids_and_platform_reject_before_output(self):
        for key in ('python_sha','media_sha','workspace_sha'):
            with self.subTest(key=key),self.assertRaises(ValueError):self.build(**{key:'0'*64})
            self.assertFalse((self.root/'candidate').exists())
        self.mv['platform']='windows-amd64';self.mi.write_bytes(c.encode(self.mv))
        with self.assertRaisesRegex(ValueError,'INVALID_MEDIA_COMPONENT'):self.build(media_sha=c.digest(self.mi.read_bytes()))
        self.assertFalse((self.root/'candidate').exists())

    def test_corrupt_rehashed_payload_and_extra_archive_paths_reject_before_receive(self):
        r=self.build();original=self.root/'original.zip';c.archive(self.root/'candidate',r['descriptor_sha256'],original)
        with zipfile.ZipFile(original) as z:object_name=next(n for n in z.namelist() if n.startswith('objects/'))
        for index,kwargs in enumerate([{'changes':{object_name:b'changed'}},{'extra':('../outside',b'escape')},
                                       {'extra':('objects/'+'f'*64,b'unlisted')} ]):
            bad=self.root/('bad'+str(index)+'.zip');h=self.alter_archive(original,bad,**kwargs)
            out=self.root/('receiver'+str(index))
            with self.subTest(index=index),self.assertRaises((ValueError,KeyError)):c.receive(bad,h,out)
            self.assertFalse(out.exists())
        with self.assertRaisesRegex(ValueError,'COMPOSED_ARCHIVE_HASH'):c.receive(original,'0'*64,self.root/'wrong-hash')
        self.assertFalse((self.root/'wrong-hash').exists())

    def test_rehashed_tree_projection_or_provenance_drift_rejected(self):
        r=self.build();folder=self.root/'candidate'
        for name in ('tree-manifest.json','provenance.json'):
            raw=(folder/name).read_bytes();v=c.decode(raw)
            if name=='tree-manifest.json':v['entries']['bin/ffmpeg']['mode']=0o644
            else:v['entries']['bin/ffmpeg']['component']='python'
            (folder/name).write_bytes(c.encode(v));d=c.decode((folder/'composed-runtime.json').read_bytes())
            d['files'][name]=c.digest(c.encode(v));(folder/'composed-runtime.json').write_bytes(c.encode(d))
            with self.subTest(name=name),self.assertRaisesRegex(ValueError,'COMPOSITION_PROJECTION_CHANGED'):
                c.verify_folder(folder,c.digest(c.encode(d)))
            (folder/name).write_bytes(raw)
            d['files'][name]=c.digest(raw);(folder/'composed-runtime.json').write_bytes(c.encode(d))

    def test_linked_or_unlisted_objects_cannot_be_imported(self):
        r=self.build();folder=self.root/'candidate';obj=next((folder/'objects').iterdir());raw=obj.read_bytes()
        outside=self.root/'outside';outside.write_bytes(raw);obj.unlink();obj.symlink_to(outside)
        with self.assertRaises(ValueError):c.verify_folder(folder,r['descriptor_sha256'])
        obj.unlink();os.link(outside,obj)
        with self.assertRaises(ValueError):c.verify_folder(folder,r['descriptor_sha256'])
        obj.unlink();obj.write_bytes(raw);(folder/'objects'/('f'*64)).write_bytes(b'extra')
        with self.assertRaisesRegex(ValueError,'COMPOSED_OBJECT_SET'):c.verify_folder(folder,r['descriptor_sha256'])

    def test_source_archive_binding_rejects_even_with_valid_outer_manifest(self):
        receipt=self.media/'build-receipt.json';v=c.decode(receipt.read_bytes());v['ffmpeg_source_sha256']='0'*64
        receipt.write_bytes(c.encode(v));self.mv['candidate_files']=c.workspace.inventory(self.media)
        self.mi.write_bytes(c.encode(self.mv));h=c.digest(self.mi.read_bytes())
        with self.assertRaisesRegex(ValueError,'MEDIA_SOURCE_BINDING'):self.build(media_sha=h)
        self.assertFalse((self.root/'candidate').exists())


if __name__=='__main__':unittest.main()
