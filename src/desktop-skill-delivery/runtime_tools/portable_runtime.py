"""Build relocatable engineering vendor runtimes from locked source components.

No package code is executed. Candidates remain unsigned and not user installers.
"""
from __future__ import annotations

import argparse
import base64
import configparser
import csv
import hashlib
import io
import json
from pathlib import Path
import re
import stat
import tarfile
import zipfile

import runtime_bundle as bundle
import runtime_store as store

VERSION='0.1.0'
SCHEMA='vf.portable-runtime-candidate/v1'


def need(value,code):
    if not value:raise ValueError(code)


def digest(raw):return hashlib.sha256(raw).hexdigest()


def wheel_files(raw):
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        items=archive.infolist();files={}
        need(len(items)<=20000 and sum(i.file_size for i in items)<=512*1024*1024,'WHEEL_LIMIT')
        for item in items:
            if item.is_dir():continue
            store.relative(item.filename)
            need(item.filename not in files and not item.flag_bits&1
                 and stat.S_IFMT(item.external_attr>>16) in (0,stat.S_IFREG)
                 and item.file_size<=store.MAX_FILE,'INVALID_WHEEL_MEMBER')
            need(not item.filename.endswith('.pth'),'PTH_NOT_SUPPORTED')
            if '.data/' in item.filename:
                need(re.fullmatch(r'[^/]+\.data/scripts/[^/]+',item.filename),'WHEEL_DATA_SCHEME_NOT_SUPPORTED')
            files[item.filename]=archive.read(item)
        records=[n for n in files if n.endswith('.dist-info/RECORD')]
        need(len(records)==1,'WHEEL_RECORD_MISSING')
        seen=set()
        for row in csv.reader(io.StringIO(files[records[0]].decode('utf-8'))):
            need(len(row)==3 and row[0] in files and row[0] not in seen,'WHEEL_RECORD_SET')
            name,hash_value,size=row;seen.add(name)
            if name==records[0]:need(hash_value==size=='','WHEEL_SELF_RECORD')
            else:
                need(hash_value.startswith('sha256=') and size==str(len(files[name])),'WHEEL_RECORD_SIZE_OR_ALGORITHM')
                wanted=base64.urlsafe_b64encode(hashlib.sha256(files[name]).digest()).rstrip(b'=').decode('ascii')
                need(hash_value=='sha256='+wanted,'WHEEL_RECORD_HASH')
        need(seen==set(files),'WHEEL_RECORD_SET')
        return files


def wrappers(platform):
    if platform=='macos-arm64':
        return {'bin/vf-python':(b'#!/bin/sh\nset -eu\ncase "$0" in\n  */*) VF_RUNTIME_BIN=${0%/*} ;;\n  *) echo "EXPLICIT_RUNTIME_ENTRY_REQUIRED" >&2; exit 2 ;;\nesac\nVF_RUNTIME_DIR=$(CDPATH= cd -P "$VF_RUNTIME_BIN/.." && pwd)\nPATH="$VF_RUNTIME_DIR/bin${PATH:+:$PATH}"\nexport PATH\nexec "$VF_RUNTIME_DIR/python/bin/python3.12" -I -B "$VF_RUNTIME_DIR/vf_runner.py" "$@"\n',0o755)}
    return {'bin/vf-python.cmd':(b'@echo off\r\nsetlocal\r\nset "PATH=%~dp0;%PATH%"\r\n"%~dp0..\\python\\python.exe" -I -B "%~dp0..\\vf_runner.py" %*\r\nexit /b %errorlevel%\r\n',0o644)}


def assemble(source,expected_sha,release,output):
    verified=bundle.verify(source,expected_sha)
    need(not output.exists() and output.parent.is_dir(),'NEW_OUTPUT_REQUIRED')
    bundle.no_links(output)
    raw=bundle.read(source,bundle.MAX_TOTAL);need(digest(raw)==expected_sha,'SOURCE_CHANGED')
    entries={};origins={};commands={};objects=set();total=0
    output.mkdir();(output/'objects').mkdir()
    def add(name,payload,mode,origin):
        nonlocal total
        store.relative(name);need(name not in entries,'PORTABLE_PATH_COLLISION')
        need(type(payload) is bytes and len(payload)<=store.MAX_FILE,'PORTABLE_FILE_LIMIT')
        total+=len(payload);need(total<=store.MAX_TOTAL and len(entries)<store.MAX_ENTRIES,'PORTABLE_TREE_LIMIT')
        h=digest(payload)
        if h not in objects:
            with (output/'objects'/h).open('xb') as stream:stream.write(payload)
            objects.add(h)
        entries[name]={'type':'file','size':len(payload),'sha256':h,'mode':mode}
        origins[name]={**origin,'output_sha256':h}
    def command(name,value):
        need(re.fullmatch('[A-Za-z0-9_.-]+',name) and name not in commands,'CONSOLE_COMMAND_COLLISION')
        commands[name]=value
    with zipfile.ZipFile(io.BytesIO(raw)) as outer:
        source_manifest=bundle.decode(outer.read('runtime-manifest.json'))
        sources=bundle.decode(outer.read('sources.json'))
        with tarfile.open(fileobj=io.BytesIO(outer.read('python.tar.gz')),mode='r:gz') as archive:
            members=archive.getmembers()
            need(len(members)<=10000 and sum(m.size for m in members)<=512*1024*1024,'PYTHON_ARCHIVE_LIMIT')
            seen=set()
            for member in members:
                name=member.name.rstrip('/');store.relative(name)
                need(name=='python' or name.startswith('python/'),'PYTHON_MEMBER_ROOT')
                need(name not in seen,'PYTHON_MEMBER_COLLISION');seen.add(name)
                if member.isdir():continue
                origin={'kind':'python-archive','archive_sha256':sources['python']['sha256'],'member':member.name}
                if member.issym():
                    need(name not in entries,'PORTABLE_PATH_COLLISION')
                    entries[name]={'type':'symlink','target':member.linkname};origins[name]=origin
                else:
                    need(member.isfile() and member.size<=store.MAX_FILE,'PYTHON_MEMBER_TYPE')
                    add(name,archive.extractfile(member).read(),0o755 if member.mode&0o111 else 0o644,origin)
        for wheel in sources['wheels']:
            wheel_raw=outer.read('wheels/'+wheel['filename'])
            need(digest(wheel_raw)==wheel['sha256'],'WHEEL_SOURCE_CHANGED')
            contents=wheel_files(wheel_raw)
            for name,payload in contents.items():
                add('vendor/'+name,payload,0o644,{'kind':'wheel','archive_sha256':wheel['sha256'],'member':name})
            points=[n for n in contents if n.endswith('.dist-info/entry_points.txt')]
            need(len(points)<=1,'WHEEL_ENTRY_POINTS_AMBIGUOUS')
            if points:
                config=configparser.ConfigParser(interpolation=None);config.optionxform=str
                config.read_string(contents[points[0]].decode('utf-8'))
                if config.has_section('console_scripts'):
                    for name,target in config['console_scripts'].items():
                        need(re.fullmatch(r'[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*:[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*',target),'INVALID_CONSOLE_TARGET')
                        command(name,{'type':'callable','target':target})
            for name in contents:
                if '.data/scripts/' in name:
                    command(name.rsplit('/',1)[1],{'type':'script','path':'vendor/'+name})
        for name in outer.namelist():
            if name.startswith('licenses/python/'):
                add(name,outer.read(name),0o644,{'kind':'source-component','member':name,'archive_sha256':expected_sha})
        add('licenses/sources.json',outer.read('sources.json'),0o644,{'kind':'source-component','member':'sources.json','archive_sha256':expected_sha})
    runner=Path(__file__).with_name('portable_runner.py').read_bytes()
    builder=Path(__file__).read_bytes()
    add('vf_runner.py',runner,0o644,{'kind':'generated-runner','tool_sha256':digest(runner)})
    add('console-commands.json',bundle.encoded({'schema':'vf.runtime-console/v1','commands':commands}),0o644,
        {'kind':'entry-point-projection','tool_sha256':digest(builder)})
    for name,(payload,mode) in wrappers(verified['platform']).items():
        add(name,payload,mode,{'kind':'platform-wrapper','tool_sha256':digest(builder)})
    manifest={'schema':store.SCHEMA,'release':release,'source_sha256':expected_sha,'entries':entries}
    store.validate_manifest(manifest)
    tree_raw=store.tx.encode(manifest);tree_sha=digest(tree_raw)
    provenance={'schema':'vf.portable-runtime-provenance/v1','builder_version':VERSION,
                'source_component_sha256':expected_sha,'builder_sha256':digest(builder),'runner_sha256':digest(runner),
                'entries':origins,'mode_projection':'0644-or-0755-from-upstream-executable-bit'}
    descriptor={'schema':SCHEMA,'release':release,'platform':verified['platform'],'python_version':source_manifest['python_version'],
                'tree_sha256':tree_sha,'authentication':'unsigned','installable':False,'public_distribution':False,
                'windows_execution':False,'desktop_acceptance':False,'layout':'original-python-and-vendor-wheels-with-vf-runner',
                'files':{'tree-manifest.json':digest(tree_raw),'provenance.json':digest(bundle.encoded(provenance))}}
    for name,payload in [('tree-manifest.json',tree_raw),('provenance.json',bundle.encoded(provenance)),('portable-runtime.json',bundle.encoded(descriptor))]:
        with (output/name).open('xb') as stream:stream.write(payload)
    return {'status':'portable_tree_assembled','tree_sha256':tree_sha,'files':len(entries),'objects':len(objects),
            'expanded_bytes':total,'commands':sorted(commands),'platform':verified['platform'],'installable':False}


def archive(folder,output):
    bundle.no_links(output);need(not output.exists() and output.parent.is_dir(),'NEW_OUTPUT_REQUIRED')
    # File set is constrained by the generated manifest and metadata before export.
    descriptor=bundle.decode(bundle.read(folder/'portable-runtime.json',bundle.MAX_JSON))
    manifest=bundle.decode(bundle.read(folder/'tree-manifest.json',store.tx.MAX_FILE))
    store.validate_manifest(manifest);need(digest(store.tx.encode(manifest))==descriptor['tree_sha256'],'TREE_BINDING_CHANGED')
    names=['portable-runtime.json','tree-manifest.json','provenance.json']
    objects={v['sha256']:v['size'] for v in manifest['entries'].values() if v['type']=='file'}
    need({p.name for p in folder.iterdir()}==set(names)|{'objects'},'PORTABLE_FILE_SET')
    need({p.name for p in (folder/'objects').iterdir()}==set(objects),'PORTABLE_OBJECT_SET')
    for name in names[1:]:need(digest(bundle.read(folder/name,store.tx.MAX_FILE))==descriptor['files'][name],'PORTABLE_METADATA_CHANGED')
    with output.open('xb') as stream,zipfile.ZipFile(stream,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=6) as z:
        for name in sorted(names+['objects/'+h for h in objects]):
            path=folder/name;bundle.no_links(path)
            if name.startswith('objects/'):
                h=name.split('/')[1];need(store.file_hash(path)==(h,objects[h]),'PORTABLE_OBJECT_CHANGED')
            info=zipfile.ZipInfo(name,(1980,1,1,0,0,0));info.create_system=3;info.external_attr=(stat.S_IFREG|0o644)<<16
            info.compress_type=zipfile.ZIP_DEFLATED
            with path.open('rb') as source,z.open(info,'w') as destination:
                while chunk:=source.read(store.CHUNK):destination.write(chunk)
    return {'status':'portable_archive_built','bytes':output.stat().st_size,'sha256':store.file_hash(output,store.MAX_TOTAL)[0],
            'installable':False,'platform':descriptor['platform']}


def _validated(raw,expected_sha):
    need(store.digest_ok(expected_sha) and digest(raw)==expected_sha,'PORTABLE_ARCHIVE_HASH_MISMATCH')
    archive=zipfile.ZipFile(io.BytesIO(raw))
    try:
        items=archive.infolist();names=[i.filename for i in items]
        need(len(items)<=store.MAX_ENTRIES+3 and len(set(names))==len(names)
             and sum(i.file_size for i in items)<=store.MAX_TOTAL+32*1024*1024,'PORTABLE_ARCHIVE_LIMIT')
        for item in items:
            need(not item.is_dir() and not item.flag_bits&1
                 and stat.S_IFMT(item.external_attr>>16)==stat.S_IFREG
                 and item.file_size<=store.MAX_FILE,'PORTABLE_ARCHIVE_MEMBER')
        def metadata(name):
            need(archive.getinfo(name).file_size<=store.tx.MAX_FILE,'PORTABLE_METADATA_LIMIT')
            return archive.read(name)
        descriptor=bundle.decode(metadata('portable-runtime.json'))
        need(type(descriptor) is dict and set(descriptor)=={'schema','release','platform','python_version','tree_sha256',
             'authentication','installable','public_distribution','windows_execution','desktop_acceptance','layout','files'}
             and descriptor['schema']==SCHEMA and descriptor['platform'] in bundle.PLATFORMS
             and descriptor['authentication']=='unsigned' and descriptor['layout']=='original-python-and-vendor-wheels-with-vf-runner'
             and all(descriptor[k] is False for k in ('installable','public_distribution','windows_execution','desktop_acceptance')),
             'INVALID_PORTABLE_DESCRIPTOR')
        tree_raw=metadata('tree-manifest.json');manifest=store.tx.decode(tree_raw);store.validate_manifest(manifest)
        provenance_raw=metadata('provenance.json');provenance=store.tx.decode(provenance_raw)
        need(digest(tree_raw)==descriptor['tree_sha256'] and descriptor['release']==manifest['release']
             and descriptor['files']=={'tree-manifest.json':digest(tree_raw),'provenance.json':digest(provenance_raw)},'PORTABLE_METADATA_BINDING')
        need(provenance['schema']=='vf.portable-runtime-provenance/v1'
             and provenance['source_component_sha256']==manifest['source_sha256']
             and set(provenance['entries'])==set(manifest['entries']),'PORTABLE_PROVENANCE_BINDING')
        objects={}
        for name,entry in manifest['entries'].items():
            if entry['type']=='file':
                need(provenance['entries'][name].get('output_sha256')==entry['sha256'],'PORTABLE_PROVENANCE_BINDING')
                need(entry['sha256'] not in objects or objects[entry['sha256']]==entry['size'],'OBJECT_SIZE_CONFLICT')
                objects[entry['sha256']]=entry['size']
        expected={'portable-runtime.json','tree-manifest.json','provenance.json'}|{'objects/'+h for h in objects}
        need(set(names)==expected,'PORTABLE_ARCHIVE_MEMBER_SET')
        for h,size in objects.items():
            info=archive.getinfo('objects/'+h);need(info.file_size==size,'PORTABLE_OBJECT_SIZE')
            digestor=hashlib.sha256();count=0
            with archive.open(info) as stream:
                while chunk:=stream.read(store.CHUNK):
                    count+=len(chunk);need(count<=size,'PORTABLE_OBJECT_SIZE');digestor.update(chunk)
            need(count==size and digestor.hexdigest()==h,'PORTABLE_OBJECT_HASH')
        return archive,descriptor,manifest
    except BaseException:
        archive.close();raise


def verify(path,expected_sha):
    raw=bundle.read(path,store.MAX_TOTAL)
    archive,descriptor,manifest=_validated(raw,expected_sha);archive.close()
    return {'status':'portable_archive_verified','platform':descriptor['platform'],'tree_sha256':descriptor['tree_sha256'],
            'entries':len(manifest['entries']),'installable':False,'desktop_acceptance':False}


def unpack(path,expected_sha,output):
    raw=bundle.read(path,store.MAX_TOTAL)
    archive,descriptor,manifest=_validated(raw,expected_sha)
    try:
        bundle.no_links(output);need(not output.exists() and output.parent.is_dir(),'NEW_OUTPUT_REQUIRED')
        output.mkdir();(output/'objects').mkdir()
        for info in archive.infolist():
            # _validated restricts paths to exact metadata or objects/<sha256>.
            with archive.open(info) as source,(output/info.filename).open('xb') as target:
                while chunk:=source.read(store.CHUNK):target.write(chunk)
        return {'status':'portable_objects_unpacked','tree_sha256':descriptor['tree_sha256'],
                'entries':len(manifest['entries']),'installable':False}
    finally:archive.close()


def main():
    p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='command',required=True)
    a=sub.add_parser('assemble');a.add_argument('--source',type=Path,required=True);a.add_argument('--sha256',required=True)
    a.add_argument('--release',required=True);a.add_argument('--output',type=Path,required=True)
    b=sub.add_parser('archive');b.add_argument('--folder',type=Path,required=True);b.add_argument('--output',type=Path,required=True)
    for command in ('verify','unpack'):
        c=sub.add_parser(command);c.add_argument('--archive',type=Path,required=True);c.add_argument('--sha256',required=True)
        if command=='unpack':c.add_argument('--output',type=Path,required=True)
    args=p.parse_args()
    if args.command=='assemble':result=assemble(args.source,args.sha256,args.release,args.output)
    elif args.command=='archive':result=archive(args.folder,args.output)
    elif args.command=='verify':result=verify(args.archive,args.sha256)
    else:result=unpack(args.archive,args.sha256,args.output)
    print(json.dumps(result))


if __name__=='__main__':
    try:main()
    except (ValueError,OSError,KeyError,TypeError,RecursionError,zipfile.BadZipFile,tarfile.TarError,configparser.Error,csv.Error) as exc:
        code=str(exc) if isinstance(exc,ValueError) and re.fullmatch('[A-Z_]+',str(exc)) else 'INVALID_PORTABLE_RUNTIME'
        print(json.dumps({'error':code}),file=sys.stderr);raise SystemExit(2)
