"""Compose locked Python, media and workspace components into one engineering tree.

No build scripts or component programs execute. Original workspace paths are
preserved in metadata and restored explicitly, never relaxed in the tree policy.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
from pathlib import Path
import re
import shutil
import stat
import sys
import zipfile

import portable_runtime as portable
import runtime_store as store

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'workspace_tools'))
import workspace_delivery as workspace

SCHEMA='vf.composed-runtime-candidate/v1'
LOCK_SCHEMA='vf.runtime-composition-lock/v1'
PROVENANCE='vf.runtime-composition-provenance/v1'
META=('composed-runtime.json','composition-lock.json','tree-manifest.json','provenance.json')
PY_META=('portable-runtime.json','tree-manifest.json','provenance.json')
FLAGS=('installable','public_distribution','windows_execution','desktop_acceptance','mcp_acceptance','main_source_license_grant_verified')
need=portable.need
encode=store.tx.encode
decode=store.tx.decode
digest=portable.digest


def entry(raw,mode=0o644):
    return {'type':'file','sha256':digest(raw),'size':len(raw),'mode':mode}


def file_record(record):
    need(type(record) is dict and set(record)=={'sha256','bytes','mode'}
         and store.digest_ok(record['sha256']) and type(record['bytes']) is int
         and 0<=record['bytes']<=store.MAX_FILE and type(record['mode']) is int
         and record['mode'] in (0o644,0o755),'INVALID_COMPONENT_FILE')
    return {'type':'file','sha256':record['sha256'],'size':record['bytes'],'mode':record['mode']}


def records(value):
    need(type(value) is dict and 0<len(value)<=4096,'INVALID_COMPONENT_FILES')
    workspace.validate_names(value)
    result={n:file_record(r) for n,r in value.items()}
    need(sum(r['size'] for r in result.values())<=workspace.MAX_TOTAL,'COMPONENT_TOO_LARGE')
    return result


def original_metadata(lock,materials):
    need(type(lock) is dict and set(lock)=={'schema','release','platform','builder_sha256','wrapper_generator_sha256','components'}
         and lock['schema']==LOCK_SCHEMA and lock['platform'] in portable.bundle.PLATFORMS
         and type(lock['release']) is str and re.fullmatch('[a-z0-9][a-z0-9.-]{0,63}',lock['release'])
         and store.digest_ok(lock['builder_sha256']) and store.digest_ok(lock['wrapper_generator_sha256']),
         'INVALID_COMPOSITION_LOCK')
    refs=lock['components']
    need(type(refs) is dict and set(refs)=={'python','media','workspace'}
         and set(refs['python'])=={'archive_sha256','metadata_sha256'}
         and store.digest_ok(refs['python']['archive_sha256'])
         and set(refs['python']['metadata_sha256'])==set(PY_META)
         and all(store.digest_ok(h) for h in refs['python']['metadata_sha256'].values())
         and set(refs['media'])=={'manifest_sha256'} and store.digest_ok(refs['media']['manifest_sha256'])
         and set(refs['workspace'])=={'descriptor_sha256'} and store.digest_ok(refs['workspace']['descriptor_sha256']),
         'INVALID_COMPONENT_REFERENCES')
    expected={f'components/python/{n}' for n in PY_META}|{
        'components/media/component-integrity.json','components/workspace/workspace-runtime.json',
        'components/composer/runtime_composition.py','components/composer/portable_runtime.py'}
    need(set(materials)==expected,'COMPOSITION_MATERIAL_SET')
    for name in PY_META:
        need(digest(materials['components/python/'+name])==refs['python']['metadata_sha256'][name],'PYTHON_METADATA_HASH')
    need(digest(materials['components/media/component-integrity.json'])==refs['media']['manifest_sha256']
         and digest(materials['components/workspace/workspace-runtime.json'])==refs['workspace']['descriptor_sha256'],
         'COMPONENT_METADATA_HASH')
    need(digest(materials['components/composer/runtime_composition.py'])==lock['builder_sha256']
         and digest(materials['components/composer/portable_runtime.py'])==lock['wrapper_generator_sha256'],
         'COMPOSITION_TOOL_BINDING')
    # The versioned receiver must know this wrapper projection; input code is never executed.
    need(digest(Path(__file__).read_bytes())==lock['builder_sha256']
         and digest(Path(portable.__file__).read_bytes())==lock['wrapper_generator_sha256'],'COMPOSITION_TOOL_VERSION')
    py={n:decode(materials['components/python/'+n]) for n in PY_META}
    pd=py['portable-runtime.json'];pt=py['tree-manifest.json'];pp=py['provenance.json']
    store.validate_manifest(pt)
    need(pd['schema']==portable.SCHEMA and pd['platform']==lock['platform'] and pd['release']==pt['release']
         and pd['tree_sha256']==digest(materials['components/python/tree-manifest.json'])
         and pd['files']=={n:digest(materials['components/python/'+n]) for n in PY_META[1:]}
         and pd['authentication']=='unsigned' and all(pd[k] is False for k in
             ('installable','public_distribution','windows_execution','desktop_acceptance'))
         and pp['schema']=='vf.portable-runtime-provenance/v1'
         and pp['source_component_sha256']==pt['source_sha256'] and set(pp['entries'])==set(pt['entries']),
         'PYTHON_COMPONENT_BINDING')
    for name,r in pt['entries'].items():
        if r['type']=='file':need(pp['entries'][name].get('output_sha256')==r['sha256'],'PYTHON_COMPONENT_BINDING')
    media=decode(materials['components/media/component-integrity.json'])
    need(type(media) is dict and set(media)=={'schema','platform','candidate_files','source_closure_verified',
         'public_distribution','installable','windows_execution','independent_review'}
         and media['schema']=='vf.media-component-integrity/v1' and media['platform']==lock['platform']
         and media['source_closure_verified'] is True and media['independent_review']=='pending'
         and all(media[k] is False for k in ('public_distribution','installable','windows_execution')),
         'INVALID_MEDIA_COMPONENT')
    mr=records(media['candidate_files'])
    wd=decode(materials['components/workspace/workspace-runtime.json'])
    need(wd['schema']=='vf.workspace-cli-candidate/v1' and wd['authentication']=='unsigned'
         and wd['independent_review']=='pending' and all(wd[k] is False for k in FLAGS)
         and set(wd['binaries'])==set(workspace.TARGETS),'INVALID_WORKSPACE_COMPONENT')
    wr=records(wd['files'])
    for target,(_,_,name) in workspace.TARGETS.items():
        record=wd['binaries'][target];path='bin/'+target+'/'+name
        need(set(record)=={'path','sha256','bytes'} and record['path']==path and path in wr
             and record['sha256']==wr[path]['sha256'] and record['bytes']==wr[path]['size']
             and wr[path]['mode']==0o755,'WORKSPACE_BINARY_BINDING')
    return py,mr,wd,wr


def projection(lock,materials):
    py,mr,wd,wr=original_metadata(lock,materials)
    entries={};origins={};generated={}
    def add(name,record,origin):
        store.relative(name)
        need(name not in entries or entries[name]==record,'COMPOSITION_PATH_COLLISION')
        need(name not in origins or origins[name]==origin,'COMPOSITION_ORIGIN_COLLISION')
        entries[name]=record;origins[name]=origin
    for name,record in py['tree-manifest.json']['entries'].items():add(name,record,{'component':'python','member':name})
    for name,(raw,mode) in portable.wrappers(lock['platform']).items():
        need(name in entries and entries[name]['type']=='file','PYTHON_WRAPPER_MISSING')
        before=entries[name];material='components/python/original-entries/'+before['sha256']
        add(material,{**before,'mode':0o644},{'component':'python-original-wrapper','member':name})
        entries[name]=entry(raw,mode);generated[name]=raw
        origins[name]={'component':'wrapper-projection','input_sha256':before['sha256'],'transform':'scoped-bin-and-shell-builtins-v1'}
    for name,record in mr.items():add('components/media/'+name,record,{'component':'media','member':name})
    for name,record in wr.items():
        add('components/workspace/files/'+record['sha256'],{**record,'mode':0o644},
            {'component':'workspace-object','content_sha256':record['sha256']})
    for name,raw in materials.items():add(name,entry(raw),{'component':'original-material','member':name})
    suffix='.exe' if lock['platform']=='windows-amd64' else ''
    for name in ('ffmpeg','ffprobe'):
        path='bin/'+name+suffix;need(path in mr and mr[path]['mode']==0o755,'MEDIA_BINARY_MISSING')
        add(path,mr[path],{'component':'media-entry','member':path})
    path=wd['binaries'][lock['platform']]['path']
    add('bin/studio-workspace'+suffix,wr[path],{'component':'workspace-entry','member':path})
    manifest={'schema':store.SCHEMA,'release':lock['release'],'source_sha256':digest(encode(lock)),'entries':entries}
    store.validate_manifest(manifest)
    return manifest,{'schema':PROVENANCE,'entries':origins},generated


def media_bindings(manifest,read_entry,platform):
    receipt=decode(read_entry('components/media/build-receipt.json'))
    source_raw=read_entry('components/media/sources/media-source-lock.json');sources=decode(source_raw)
    need(receipt['schema']=='vf.media-runtime-build/v1' and receipt['platform']==platform
         and receipt['license']=='GPL-3.0-or-later' and receipt['source_lock_sha256']==digest(source_raw)
         and receipt['builder_sha256']==digest(read_entry('components/media/sources/build_media_runtime.py'))
         and all(receipt[k] is False for k in ('public_distribution','installable','desktop_acceptance','windows_execution'))
         and sources['schema']=='vf.media-build-sources/v1'
         and set(sources['sources'])=={'ffmpeg','x264','zlib','pkgconf','nasm'},'MEDIA_SOURCE_BINDING')
    for name,value in sources['sources'].items():
        store.relative(value['filename']);path='components/media/sources/'+value['filename']
        need(path in manifest['entries'] and manifest['entries'][path]['sha256']==value['sha256'],'MEDIA_SOURCE_BINDING')
        if name=='ffmpeg':need(receipt['ffmpeg_source_sha256']==value['sha256'],'MEDIA_SOURCE_BINDING')
    suffix='.exe' if platform=='windows-amd64' else ''
    need(set(receipt['binaries'])=={'ffmpeg'+suffix,'ffprobe'+suffix},'MEDIA_BINARY_SET')
    for name,value in receipt['binaries'].items():
        record=manifest['entries']['bin/'+name]
        need(value=={'sha256':record['sha256'],'size':record['size'],'mode':record['mode']},'MEDIA_BINARY_BINDING')


def assemble(python_archive,python_sha,media_folder,media_integrity,media_sha,workspace_folder,workspace_sha,release,output):
    python_archive=python_archive.absolute();media_folder=media_folder.absolute();workspace_folder=workspace_folder.absolute()
    output=output.absolute();portable.bundle.no_links(output)
    need(not output.exists() and output.parent.is_dir(),'NEW_OUTPUT_REQUIRED')
    need(all(p!=output and p not in output.parents for p in (media_folder,workspace_folder)),'COMPOSITION_ROOT_OVERLAP')
    raw=portable.bundle.read(python_archive,store.MAX_TOTAL)
    z,pd,pt=portable._validated(raw,python_sha)
    try:
        media_raw=workspace.read(media_integrity);need(digest(media_raw)==media_sha,'MEDIA_MANIFEST_HASH')
        mv=decode(media_raw);workspace.check_files(media_folder,mv['candidate_files'],metadata=())
        wd=workspace.verify_runtime(workspace_folder,workspace_sha)
        materials={'components/python/'+n:z.read(n) for n in PY_META}
        materials.update({'components/media/component-integrity.json':media_raw,
                          'components/workspace/workspace-runtime.json':workspace.read(workspace_folder/'workspace-runtime.json'),
                          'components/composer/runtime_composition.py':Path(__file__).read_bytes(),
                          'components/composer/portable_runtime.py':Path(portable.__file__).read_bytes()})
        lock={'schema':LOCK_SCHEMA,'release':release,'platform':pd['platform'],
              'builder_sha256':digest(materials['components/composer/runtime_composition.py']),
              'wrapper_generator_sha256':digest(materials['components/composer/portable_runtime.py']),
              'components':{'python':{'archive_sha256':python_sha,'metadata_sha256':{n:digest(z.read(n)) for n in PY_META}},
                            'media':{'manifest_sha256':media_sha},'workspace':{'descriptor_sha256':workspace_sha}}}
        manifest,provenance,generated=projection(lock,materials)
        def payload(name):
            origin=provenance['entries'][name];kind=origin['component']
            if name in generated:return generated[name]
            if name in materials:return materials[name]
            if kind=='python':return z.read('objects/'+pt['entries'][name]['sha256'])
            if kind=='python-original-wrapper':return z.read('objects/'+pt['entries'][origin['member']]['sha256'])
            if kind in ('media','media-entry'):return workspace.read(media_folder/origin['member'])
            if kind=='workspace-entry':return workspace.read(workspace_folder/origin['member'])
            # Multiple original workspace members can have identical bytes; verify before choosing.
            original=next(n for n,r in wd['files'].items() if r['sha256']==origin['content_sha256'])
            return workspace.read(workspace_folder/original)
        media_bindings(manifest,payload,pd['platform'])
        output.mkdir(mode=0o700);(output/'objects').mkdir(mode=0o700)
        written=set()
        for name,record in manifest['entries'].items():
            if record['type']!='file' or record['sha256'] in written:continue
            content=payload(name)
            need(len(content)==record['size'] and digest(content)==record['sha256'],'COMPOSITION_INPUT_CHANGED')
            (output/'objects'/record['sha256']).write_bytes(content);written.add(record['sha256'])
        metadata={'composition-lock.json':encode(lock),'tree-manifest.json':encode(manifest),'provenance.json':encode(provenance)}
        descriptor={'schema':SCHEMA,'release':release,'platform':pd['platform'],
                    'files':{n:digest(v) for n,v in metadata.items()},'authentication':'unsigned','independent_review':'pending',
                    **{k:False for k in FLAGS}}
        metadata['composed-runtime.json']=encode(descriptor)
        for n,value in metadata.items():(output/n).write_bytes(value)
        need(digest(Path(__file__).read_bytes())==lock['builder_sha256']
             and digest(Path(portable.__file__).read_bytes())==lock['wrapper_generator_sha256'],'COMPOSITION_TOOL_CHANGED')
        workspace.verify_runtime(workspace_folder,workspace_sha)
        workspace.check_files(media_folder,mv['candidate_files'],metadata=())
        return verify_folder(output,digest(metadata['composed-runtime.json']))
    finally:z.close()


def validate(metadata,read_object):
    need(set(metadata)==set(META),'COMPOSITION_METADATA_SET')
    d=decode(metadata['composed-runtime.json'])
    need(type(d) is dict and set(d)=={'schema','release','platform','files','authentication','independent_review',*FLAGS}
         and d['schema']==SCHEMA and d['authentication']=='unsigned' and d['independent_review']=='pending'
         and all(d[k] is False for k in FLAGS)
         and d['files']=={n:digest(v) for n,v in metadata.items() if n!='composed-runtime.json'},'INVALID_COMPOSED_DESCRIPTOR')
    lock=decode(metadata['composition-lock.json']);manifest=decode(metadata['tree-manifest.json']);provenance=decode(metadata['provenance.json'])
    need(all(metadata[n]==encode(v) for n,v in [('composition-lock.json',lock),('tree-manifest.json',manifest),
                                               ('provenance.json',provenance)]),'NONCANONICAL_COMPOSITION_METADATA')
    store.validate_manifest(manifest)
    objects={}
    for record in manifest['entries'].values():
        if record['type']=='file':
            need(record['sha256'] not in objects or objects[record['sha256']]==record['size'],'COMPOSED_OBJECT_SIZE_CONFLICT')
            objects[record['sha256']]=record['size']
    def get(name):
        record=manifest['entries'][name];need(record['type']=='file','COMPONENT_FILE_REQUIRED')
        value=read_object(record['sha256'],record['size'])
        need(type(value) is bytes and len(value)==record['size'] and digest(value)==record['sha256'],'COMPOSED_OBJECT_CHANGED')
        return value
    names={f'components/python/{n}' for n in PY_META}|{'components/media/component-integrity.json',
           'components/workspace/workspace-runtime.json','components/composer/runtime_composition.py','components/composer/portable_runtime.py'}
    need(names<=set(manifest['entries']),'COMPOSED_MATERIAL_MISSING')
    expected,origins,generated=projection(lock,{n:get(n) for n in names})
    need(manifest==expected and provenance==origins and d['release']==lock['release']
         and d['platform']==lock['platform'],'COMPOSITION_PROJECTION_CHANGED')
    for name,raw in generated.items():need(get(name)==raw,'COMPOSITION_WRAPPER_CHANGED')
    media_bindings(manifest,get,lock['platform'])
    suffix='.exe' if lock['platform']=='windows-amd64' else ''
    for name in ('ffmpeg','ffprobe','studio-workspace'):workspace.binary_format(get('bin/'+name+suffix),lock['platform'])
    # Rehash every payload, including source materials not needed during a normal invocation.
    for h,size in objects.items():
        value=read_object(h,size);need(type(value) is bytes and len(value)==size and digest(value)==h,'COMPOSED_OBJECT_CHANGED')
    return d,manifest,objects


def verify_folder(folder,expected_sha):
    portable.bundle.no_links(folder)
    metadata={n:workspace.read(folder/n,store.tx.MAX_FILE) for n in META}
    need(store.digest_ok(expected_sha) and digest(metadata['composed-runtime.json'])==expected_sha,'COMPOSED_DESCRIPTOR_HASH')
    need(workspace.file_set(folder)==set(META)|{'objects/'+p.name for p in (folder/'objects').iterdir()},'COMPOSED_FILE_SET')
    d,manifest,objects=validate(metadata,lambda h,size:workspace.read(folder/'objects'/h,store.MAX_FILE))
    need({p.name for p in (folder/'objects').iterdir()}==set(objects),'COMPOSED_OBJECT_SET')
    return {'status':'composed_runtime_verified','descriptor_sha256':expected_sha,'tree_sha256':digest(metadata['tree-manifest.json']),
            'platform':d['platform'],'entries':len(manifest['entries']),'expanded_bytes':sum(r['size'] for r in manifest['entries'].values() if r['type']=='file'),
            'objects':len(objects),'installable':False,'windows_execution':False}


def archive(folder,expected_sha,output):
    result=verify_folder(folder,expected_sha);portable.bundle.no_links(output)
    need(not output.exists() and output.parent.is_dir(),'NEW_OUTPUT_REQUIRED')
    with output.open('xb') as stream,zipfile.ZipFile(stream,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=6) as z:
        for name in sorted(workspace.file_set(folder)):
            info=zipfile.ZipInfo(name,(1980,1,1,0,0,0));info.create_system=3
            info.external_attr=(stat.S_IFREG|0o644)<<16;info.compress_type=zipfile.ZIP_DEFLATED
            with (folder/name).open('rb') as source,z.open(info,'w') as destination:shutil.copyfileobj(source,destination,store.CHUNK)
    verify_folder(folder,expected_sha)
    return {**result,'status':'composed_archive_built','archive_sha256':store.file_hash(output,store.MAX_TOTAL)[0],'archive_bytes':output.stat().st_size}


def checked_archive(path,expected_sha):
    raw=portable.bundle.read(path,store.MAX_TOTAL)
    need(store.digest_ok(expected_sha) and digest(raw)==expected_sha,'COMPOSED_ARCHIVE_HASH')
    z=zipfile.ZipFile(io.BytesIO(raw))
    try:
        items=z.infolist();names=[i.filename for i in items]
        need(0<len(items)<=store.MAX_ENTRIES+len(META) and len(set(names))==len(names)
             and sum(i.file_size for i in items)<=store.MAX_TOTAL,'COMPOSED_ARCHIVE_LIMIT')
        for i in items:
            need(i.filename in META or re.fullmatch('objects/[0-9a-f]{64}',i.filename),'COMPOSED_ARCHIVE_PATH')
            need(not i.is_dir() and not i.flag_bits&1 and stat.S_IFMT(i.external_attr>>16)==stat.S_IFREG
                 and i.file_size<=store.MAX_FILE and (i.filename not in META or i.file_size<=store.tx.MAX_FILE),
                 'COMPOSED_ARCHIVE_MEMBER')
        metadata={n:z.read(n) for n in META}
        d,manifest,objects=validate(metadata,lambda h,size:z.read('objects/'+h))
        need(set(names)==set(META)|{'objects/'+h for h in objects},'COMPOSED_ARCHIVE_SET')
        return z,d,manifest
    except BaseException:z.close();raise


def receive(path,expected_sha,output=None):
    z,d,manifest=checked_archive(path,expected_sha)
    try:
        if output is not None:
            portable.bundle.no_links(output);need(not output.exists() and output.parent.is_dir(),'NEW_OUTPUT_REQUIRED')
            output.mkdir(mode=0o700);(output/'objects').mkdir(mode=0o700)
            for item in z.infolist():
                with z.open(item) as source,(output/item.filename).open('xb') as target:shutil.copyfileobj(source,target,store.CHUNK)
        return {'status':'composed_archive_received' if output is not None else 'composed_archive_verified',
                'platform':d['platform'],'entries':len(manifest['entries']),'tree_sha256':digest(encode(manifest)),
                'installable':False,'windows_execution':False}
    finally:z.close()


def restore(folder,expected_sha,output):
    verify_folder(folder,expected_sha);portable.bundle.no_links(output)
    need(not output.exists() and output.parent.is_dir(),'NEW_OUTPUT_REQUIRED')
    manifest=decode(workspace.read(folder/'tree-manifest.json',store.tx.MAX_FILE))
    def get(name):return workspace.read(folder/'objects'/manifest['entries'][name]['sha256'],store.MAX_FILE)
    wd=decode(get('components/workspace/workspace-runtime.json'))
    # Validate all original names/modes and set bounds before materializing any paths.
    wr=records(wd['files']);wr['workspace-runtime.json']=entry(get('components/workspace/workspace-runtime.json'))
    media_raw=get('components/media/component-integrity.json');mv=decode(media_raw);mr=records(mv['candidate_files'])
    output.mkdir(mode=0o700)
    for name,record in wr.items():
        raw=get('components/workspace/workspace-runtime.json') if name=='workspace-runtime.json' else get('components/workspace/files/'+record['sha256'])
        workspace.add(output/'workspace',name,raw,record['mode'])
    for name,record in mr.items():workspace.add(output/'media',name,get('components/media/'+name),record['mode'])
    workspace.add(output,'component-integrity.json',media_raw)
    lock=decode(workspace.read(folder/'composition-lock.json',store.tx.MAX_FILE))
    workspace.verify_runtime(output/'workspace',lock['components']['workspace']['descriptor_sha256'])
    workspace.check_files(output/'media',mv['candidate_files'],metadata=())
    python=output/'python';python.mkdir(mode=0o700);(python/'objects').mkdir(mode=0o700)
    original_tree=decode(get('components/python/tree-manifest.json'))
    for name in PY_META:workspace.add(python,name,get('components/python/'+name))
    hashes={r['sha256'] for r in original_tree['entries'].values() if r['type']=='file'}
    for h in sorted(hashes):shutil.copyfile(folder/'objects'/h,python/'objects'/h)
    # Reconstitute the original deterministic archive, independently proving the
    # recorded Python input ID rather than assuming a metadata-to-archive relation.
    python_result=portable.archive(python,output/'python-original.zip')
    need(python_result['sha256']==lock['components']['python']['archive_sha256'],'RESTORED_PYTHON_ARCHIVE_CHANGED')
    verify_folder(folder,expected_sha)
    return {'status':'original_components_restored_and_verified','workspace_files':len(wr),'media_files':len(mr),
            'python_archive_sha256':python_result['sha256'],'installable':False,'windows_execution':False}


def main():
    p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='command',required=True)
    s=sub.add_parser('assemble')
    for name in ('python-archive','media-folder','media-integrity','workspace-folder','output'):s.add_argument('--'+name,type=Path,required=True)
    for name in ('python-sha256','media-sha256','workspace-sha256','release'):s.add_argument('--'+name,required=True)
    for name in ('verify-folder','archive','restore'):
        s=sub.add_parser(name);s.add_argument('--folder',type=Path,required=True);s.add_argument('--sha256',required=True)
        if name!='verify-folder':s.add_argument('--output',type=Path,required=True)
    for name in ('verify','receive'):
        s=sub.add_parser(name);s.add_argument('--archive',type=Path,required=True);s.add_argument('--sha256',required=True)
        if name=='receive':s.add_argument('--output',type=Path,required=True)
    a=p.parse_args()
    if a.command=='assemble':r=assemble(a.python_archive,a.python_sha256,a.media_folder,a.media_integrity,a.media_sha256,
                                      a.workspace_folder,a.workspace_sha256,a.release,a.output)
    elif a.command=='verify-folder':r=verify_folder(a.folder,a.sha256)
    elif a.command=='archive':r=archive(a.folder,a.sha256,a.output)
    elif a.command=='restore':r=restore(a.folder,a.sha256,a.output)
    else:r=receive(a.archive,a.sha256,a.output if a.command=='receive' else None)
    print(json.dumps(r))


if __name__=='__main__':
    try:main()
    except (ValueError,OSError,KeyError,TypeError,zipfile.BadZipFile,RuntimeError) as exc:
        code=str(exc) if isinstance(exc,ValueError) and re.fullmatch('[A-Z_]+',str(exc)) else 'COMPOSED_RUNTIME_FAILED'
        print(json.dumps({'error':code}),file=sys.stderr);raise SystemExit(2)
