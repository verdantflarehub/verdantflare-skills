"""Offline, deterministic Python runtime evidence bundles; no host installation.

These unsigned engineering archives carry the original upstream Python archive,
platform wheel closure, interpreter licenses and locked provenance. The future
installer must independently admit a signed release before consuming them.
"""
from __future__ import annotations

import argparse
import hashlib
import email
import io
import json
from pathlib import Path
import re
import stat
import sys
import zipfile

SCHEMA = 'vf.python-runtime-candidate/v1'
VERSION = '0.1.1'
PLATFORMS = {'macos-arm64': 'aarch64-apple-darwin', 'windows-amd64': 'x86_64-pc-windows-msvc'}
MAX_FILE = 128 * 1024 * 1024
MAX_TOTAL = 512 * 1024 * 1024
MAX_FILES = 128
MAX_JSON = 4 * 1024 * 1024


def require(condition, code):
    if not condition:
        raise ValueError(code)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)+'\n').encode()


def decode(raw):
    require(len(raw) <= MAX_JSON, 'JSON_TOO_LARGE')
    def unique(items):
        result = {}
        for key, value in items:
            require(key not in result, 'DUPLICATE_JSON_KEY')
            result[key] = value
        return result
    return json.loads(raw, object_pairs_hook=unique, parse_constant=lambda _: (_ for _ in ()).throw(ValueError('NONFINITE_JSON')))


def relative(name):
    require(isinstance(name,str) and name and name.isascii(), 'INVALID_PATH')
    require(all(re.fullmatch(r'[A-Za-z0-9_.-]+',p) and p not in ('.','..') and not p.endswith(('.', ' '))
                for p in name.split('/')), 'INVALID_PATH')
    reserved={'con','prn','aux','nul'}|{f'{prefix}{n}' for prefix in ('com','lpt') for n in range(1,10)}
    require(all(p.split('.')[0].lower() not in reserved for p in name.split('/')), 'INVALID_PATH')
    return name


def no_links(path):
    require(not any(p.is_symlink() for p in (path.absolute(), *path.absolute().parents)), 'LINKED_PATH')


def read(path, limit=MAX_FILE):
    no_links(path)
    info = path.stat()
    require(stat.S_ISREG(info.st_mode) and info.st_size <= limit, 'INVALID_FILE')
    with path.open('rb') as stream:
        raw = stream.read(limit+1)
    require(len(raw)==info.st_size and len(raw)<=limit, 'FILE_CHANGED_OR_TOO_LARGE')
    return raw


def checked(payload, digest, size):
    require(isinstance(digest,str) and re.fullmatch('[a-f0-9]{64}',digest) and type(size) is int and size>0,
            'INVALID_SOURCE_RECORD')
    require(len(payload)==size and sha(payload)==digest, 'SOURCE_HASH_OR_SIZE_MISMATCH')
    return payload


def licenses_from_build(build):
    paths=set()
    def collect(value):
        if isinstance(value,dict):
            for key,item in value.items():
                if key=='license_path':
                    require(isinstance(item,str),'INVALID_LICENSE_PATH');paths.add(item)
                elif key=='license_paths':
                    require(isinstance(item,list) and all(isinstance(n,str) for n in item),'INVALID_LICENSE_PATH');paths.update(item)
                collect(item)
        elif isinstance(value,list):
            for item in value:collect(item)
    collect(build)
    require(bool(paths),'MISSING_INTERPRETER_LICENSES')
    for name in paths:
        relative(name);require(name.startswith('licenses/'),'INVALID_LICENSE_PATH')
    return paths


def inspect_wheel(raw, record, platform):
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        members=archive.infolist();names=[i.filename for i in members]
        require(len(names)<=20000 and len(set(names))==len(names)
                and sum(i.file_size for i in members)<=512*1024*1024,'INVALID_WHEEL_CONTENTS')
        metadata_names=[n for n in names if n.endswith('.dist-info/METADATA')]
        wheel_names=[n for n in names if n.endswith('.dist-info/WHEEL')]
        require(len(metadata_names)==len(wheel_names)==1,'INVALID_WHEEL_METADATA')
        def small(name):
            require(archive.getinfo(name).file_size<=MAX_JSON,'WHEEL_METADATA_TOO_LARGE')
            return archive.read(name)
        metadata=email.message_from_bytes(small(metadata_names[0]))
        normalize=lambda text: re.sub(r'[-_.]+','-',text).lower()
        require(normalize(metadata.get('Name',''))==record['package'] and metadata.get('Version')==record['version'],
                'WHEEL_DISTRIBUTION_MISMATCH')
        tags=email.message_from_bytes(small(wheel_names[0])).get_all('Tag',[])
        require(set(tags)==set(record['tags']), 'WHEEL_TAG_MISMATCH')
        def compatible(tag):
            parts=tag.split('-')
            if len(parts)!=3:return False
            interpreter,abi,target=parts
            python_ok=(interpreter=='py3' and abi=='none') or (interpreter=='cp312' and abi in ('none','cp312','abi3')) or (abi=='abi3' and re.fullmatch(r'cp3\d{1,2}',interpreter) and 2<=int(interpreter[3:])<=12)
            os_ok=target=='any' or (platform=='windows-amd64' and target=='win_amd64') or (platform=='macos-arm64' and re.fullmatch(r'macosx_\d+_\d+_(arm64|universal2)',target))
            return bool(python_ok and os_ok and (abi=='none' or target!='any'))
        require(any(compatible(tag) for tag in tags),'WHEEL_PLATFORM_INCOMPATIBLE')
        require(record['license_files'],'MISSING_WHEEL_LICENSE')
        for item in record['license_files']:
            name=item['path']
            require(isinstance(name,str) and not name.startswith('/') and '\\' not in name and '..' not in name.split('/'),'INVALID_LICENSE_PATH')
            require(sha(small(name))==item['sha256'],'WHEEL_LICENSE_HASH_MISMATCH')


def assemble(source, platform, release_id):
    require(platform in PLATFORMS, 'UNKNOWN_PLATFORM')
    require(isinstance(release_id,str) and re.fullmatch(r'[a-z0-9][a-z0-9.-]{0,95}',release_id), 'INVALID_RELEASE_ID')
    folder = source/platform
    download=decode(read(folder/'download-receipt.json',MAX_JSON))
    wheel_lock=decode(read(folder/'verified-wheel-lock.json',MAX_JSON))
    build=decode(read(folder/'build-metadata/selected/python/PYTHON.json',MAX_JSON))
    upstream_raw=read(source/'release.json',MAX_JSON)
    upstream=decode(upstream_raw)
    require(download['source_metadata_sha256']==sha(upstream_raw), 'UPSTREAM_METADATA_HASH_MISMATCH')
    require(download['platform']==wheel_lock['platform']==platform and build['target_triple']==PLATFORMS[platform], 'PLATFORM_MISMATCH')
    require(build['python_major_minor_version']=='3.12' and re.fullmatch(r'3\.12\.\d+',build['python_version']), 'PYTHON_VERSION_MISMATCH')
    require(download['source_release']==upstream['tag_name'], 'UPSTREAM_RELEASE_MISMATCH')
    match=[a for a in upstream['assets'] if a['name']==download['filename']]
    require(len(match)==1 and match[0]['digest']=='sha256:'+download['sha256']
            and match[0]['size']==download['size'] and match[0]['browser_download_url']==download['source_url'], 'UPSTREAM_SOURCE_MISMATCH')
    # Upstream archive basename contains '+', so validate it as a basename rather
    # than using the narrower cross-platform destination path grammar.
    require(Path(download['filename']).name==download['filename'] and '\\' not in download['filename'], 'INVALID_SOURCE_NAME')
    files={'python.tar.gz':checked(read(folder/download['filename']),download['sha256'],download['size']),
           'python-build.json':encoded(build)}
    license_paths=licenses_from_build(build)
    for name in sorted(license_paths):
        relative(name)
        require(name.startswith('licenses/'),'INVALID_LICENSE_PATH')
        files['licenses/python/'+name[len('licenses/'):]]=read(folder/'build-metadata/selected/python'/name,MAX_JSON)
    wheels=[];seen=set()
    require(isinstance(wheel_lock['wheels'],list) and 0<len(wheel_lock['wheels'])<=64, 'INVALID_WHEEL_SET')
    for item in wheel_lock['wheels']:
        name=relative(item['filename'])
        require('/' not in name and name.endswith('.whl') and name not in seen,'INVALID_WHEEL_NAME');seen.add(name)
        require(isinstance(item['package'],str) and re.fullmatch('[a-z0-9][a-z0-9-]*',item['package'])
                and isinstance(item['version'],str) and re.fullmatch('[A-Za-z0-9.!+-]+',item['version']), 'INVALID_DISTRIBUTION')
        metadata=decode(read(source/'pypi'/f"{item['package']}-{item['version']}.json",MAX_JSON))
        found=[a for a in metadata['urls'] if a['filename']==name]
        require(len(found)==1 and found[0]['url']==item['url'] and found[0]['digests']['sha256']==item['sha256']
                and found[0]['size']==item['size'], 'WHEEL_METADATA_MISMATCH')
        files['wheels/'+name]=checked(read(folder/'wheels'/name),item['sha256'],item['size'])
        inspect_wheel(files['wheels/'+name],item,platform)
        wheels.append({key:item[key] for key in ('package','version','filename','sha256','size','url','tags','requires_python','requires_dist','license_files')})
    require(len({w['package'] for w in wheels})==len(wheels), 'DUPLICATE_DISTRIBUTION')
    wheels.sort(key=lambda w:w['package'])
    files['requirements.lock']=''.join(f"{w['package']}=={w['version']} --hash=sha256:{w['sha256']}\n" for w in wheels).encode()
    provenance={'python':{key:download[key] for key in ('source_url','source_release','sha256','size','source_metadata_sha256')},
                'wheels':wheels,'interpreter_license_paths':sorted(license_paths)}
    files['sources.json']=encoded(provenance)
    files['tooling/runtime_bundle.py']=read(Path(__file__))
    files['README.txt']=(
        'VF Python runtime engineering candidate\n\n'
        'Unsigned component; not a user installer or a published release.\n'
        'An engineer can verify bytes using a trusted Python 3.12+ environment:\n'
        'python tooling/runtime_bundle.py verify ARCHIVE.zip --expected-sha256 EXPECTED_SHA256\n'
        'Obtain EXPECTED_SHA256 through the controlled handoff, not this archive itself.\n'
        'Integrity does not authenticate the publisher. Do not run Python or install wheels before release admission.\n'
        'Python licenses are in licenses/python/. Wheel licenses remain inside the original wheels, indexed in sources.json.\n'
        'Native installer, signed admission, host configuration, media CLI and desktop acceptance remain separate.\n'
    ).encode()
    manifest={'schema':SCHEMA,'builder_version':VERSION,'release_id':release_id,'platform':platform,
              'python_version':build['python_version'],'target_triple':build['target_triple'],
              'archive_layout':'original-python-archive-and-locked-wheels','authentication':'unsigned',
              'installable':False,'public_distribution':False,'desktop_acceptance':False,'windows_execution':False,
              'limitations':['no_native_installer','no_signed_release_admission','no_desktop_acceptance','media_cli_not_included'],
              'files':{name:{'size':len(raw),'sha256':sha(raw)} for name,raw in sorted(files.items())}}
    files['runtime-manifest.json']=encoded(manifest)
    require(len(files)<=MAX_FILES and sum(map(len,files.values()))<=MAX_TOTAL,'BUNDLE_LIMIT_EXCEEDED')
    return files


def build(source, platform, release_id, output):
    files=assemble(source,platform,release_id)
    no_links(output)
    require(output.parent.is_dir() and not output.exists(),'NEW_OUTPUT_REQUIRED')
    with output.open('xb') as stream,zipfile.ZipFile(stream,'w',compression=zipfile.ZIP_STORED,allowZip64=False) as archive:
        for name,raw in sorted(files.items()):
            info=zipfile.ZipInfo(relative(name),(1980,1,1,0,0,0))
            info.create_system=3;info.external_attr=(stat.S_IFREG|0o644)<<16
            archive.writestr(info,raw)
    digest=sha(read(output,MAX_TOTAL))
    result=verify(output,digest)
    return {**result,'archive_sha256':digest,'archive_size':output.stat().st_size}


def verify(path, expected_sha256):
    raw=read(path,MAX_TOTAL)
    require(sha(raw)==expected_sha256,'ARCHIVE_HASH_MISMATCH')
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        members=archive.infolist();names=[i.filename for i in members]
        require(len(members)<=MAX_FILES and len(set(names))==len(names),'INVALID_MEMBER_SET')
        require(sum(i.file_size for i in members)<=MAX_TOTAL,'BUNDLE_LIMIT_EXCEEDED')
        for item in members:
            relative(item.filename)
            require(not item.is_dir() and item.file_size<=MAX_FILE and not item.flag_bits&1
                    and stat.S_IFMT(item.external_attr>>16)==stat.S_IFREG,'INVALID_MEMBER')
        manifest=decode(archive.read('runtime-manifest.json'))
        require(manifest['schema']==SCHEMA and manifest['platform'] in PLATFORMS,'INVALID_MANIFEST')
        require(manifest['target_triple']==PLATFORMS[manifest['platform']],'PLATFORM_MISMATCH')
        require(manifest['authentication']=='unsigned' and all(manifest[k] is False for k in
                ('installable','public_distribution','desktop_acceptance','windows_execution')),'UNSUPPORTED_ACCEPTANCE_CLAIM')
        require(isinstance(manifest['files'],dict) and set(manifest['files'])==set(names)-{'runtime-manifest.json'},'MANIFEST_FILE_SET_MISMATCH')
        for name,record in manifest['files'].items():
            checked(archive.read(name),record['sha256'],record['size'])
        sources=decode(archive.read('sources.json'))
        build_info=decode(archive.read('python-build.json'))
        require(build_info['python_version']==manifest['python_version'] and build_info['target_triple']==manifest['target_triple'],'PYTHON_VERSION_MISMATCH')
        require('python.tar.gz' in manifest['files'] and manifest['files']['python.tar.gz']['sha256']==sources['python']['sha256'],'PYTHON_BINDING_MISMATCH')
        wheels=sources['wheels'];require(isinstance(wheels,list) and bool(wheels),'INVALID_WHEEL_SET')
        require(len({w['package'] for w in wheels})==len(wheels),'DUPLICATE_DISTRIBUTION')
        require({'wheels/'+w['filename'] for w in wheels}=={n for n in names if n.startswith('wheels/')},'WHEEL_SET_MISMATCH')
        for item in wheels:
            name='wheels/'+relative(item['filename'])
            require(manifest['files'][name]=={'sha256':item['sha256'],'size':item['size']} and item['license_files'],'WHEEL_BINDING_MISMATCH')
            inspect_wheel(archive.read(name),item,manifest['platform'])
        requirements=''.join(f"{w['package']}=={w['version']} --hash=sha256:{w['sha256']}\n" for w in sorted(wheels,key=lambda w:w['package'])).encode()
        require(archive.read('requirements.lock')==requirements,'REQUIREMENTS_MISMATCH')
        require(set(sources['interpreter_license_paths'])==licenses_from_build(build_info),'INTERPRETER_LICENSE_SET_MISMATCH')
        for name in sources['interpreter_license_paths']:
            relative(name);require(name.startswith('licenses/') and 'licenses/python/'+name[len('licenses/'):] in names,'MISSING_INTERPRETER_LICENSE')
    return {'status':'runtime_bytes_verified','release_id':manifest['release_id'],'platform':manifest['platform'],
            'python_version':manifest['python_version'],'wheels':len(wheels),'authentication':'unsigned','installable':False,
            'desktop_acceptance':False,'windows_execution':False}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest='command',required=True)
    creation=sub.add_parser('build');creation.add_argument('--source',type=Path,required=True)
    creation.add_argument('--platform',choices=PLATFORMS,required=True);creation.add_argument('--release-id',required=True)
    creation.add_argument('--output',type=Path,required=True)
    verification=sub.add_parser('verify');verification.add_argument('archive',type=Path)
    verification.add_argument('--expected-sha256',required=True)
    args=parser.parse_args()
    result=build(args.source,args.platform,args.release_id,args.output) if args.command=='build' else verify(args.archive,args.expected_sha256)
    print(json.dumps(result))


if __name__=='__main__':
    try:main()
    except (ValueError,OSError,KeyError,TypeError,RecursionError,zipfile.BadZipFile) as exc:
        code=str(exc) if type(exc) is ValueError and re.fullmatch('[A-Z_]+',str(exc)) else 'INVALID_RUNTIME_BUNDLE'
        print(json.dumps({'error':code}),file=sys.stderr);raise SystemExit(2)
