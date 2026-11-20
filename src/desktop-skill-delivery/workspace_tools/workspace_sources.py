"""Validate fixed Go SDK build inputs without installing global tools.

The SDK contains upstream Unicode source tests. This build-input parser does
not alter the stricter ASCII paths used for public VF skill packages.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import stat
import tarfile
import unicodedata

MAX_ARCHIVE=128*1024*1024
MAX_EXPANDED=512*1024*1024
MAX_ENTRIES=30000


def require(value,code):
    if not value:raise ValueError(code)


def no_links(path):
    require(not any(p.is_symlink() for p in (path,*path.parents)),'WORKSPACE_INPUT_LINK')


def sha(path):
    result=hashlib.sha256()
    with path.open('rb') as stream:
        while raw:=stream.read(1024*1024):result.update(raw)
    return result.hexdigest()


def pairs(items):
    result={}
    for key,value in items:
        require(key not in result,'DUPLICATE_INPUT_KEY');result[key]=value
    return result


def read_sdk_lock(path):
    path=path.absolute();no_links(path);info=path.stat()
    require(stat.S_ISREG(info.st_mode) and info.st_nlink==1 and info.st_size<=8192,'INVALID_SDK_LOCK')
    result=json.loads(path.read_bytes(),object_pairs_hook=pairs)
    require(type(result) is dict and set(result)=={'schema','version','filename','sha256','bytes','url','metadata_url','install_global','user_runtime'}
            and result['schema']=='vf.workspace-build-toolchain-source/v1'
            and type(result['version']) is str and re.fullmatch('go1\\.[0-9]+\\.[0-9]+',result['version'])
            and result['filename']==result['version']+'.darwin-arm64.tar.gz'
            and type(result['sha256']) is str and re.fullmatch('[0-9a-f]{64}',result['sha256'])
            and type(result['bytes']) is int and 0<result['bytes']<=MAX_ARCHIVE
            and result['url']=='https://go.dev/dl/'+result['filename']
            and result['metadata_url']=='https://go.dev/dl/?mode=json'
            and result['install_global'] is False and result['user_runtime'] is False,'INVALID_SDK_LOCK')
    return result


def parts(name):
    require(type(name) is str and name and len(name.encode('utf-8'))<=1024
            and not name.startswith('/') and '\\' not in name and ':' not in name
            and not any(unicodedata.category(c).startswith('C') for c in name),'INVALID_SDK_PATH')
    result=name.split('/')
    require(all(p and p not in ('.','..') and p==p.strip() and not p.endswith('.')
                and len(p.encode('utf-8'))<=255 for p in result),'INVALID_SDK_PATH')
    return result


def extract_sdk(archive_path,output,lock):
    archive_path=archive_path.absolute();output=output.absolute()
    no_links(archive_path);no_links(output)
    require(not output.exists() and output.parent.is_dir(),'NEW_SDK_OUTPUT_REQUIRED')
    info=archive_path.stat()
    require(stat.S_ISREG(info.st_mode) and info.st_nlink==1 and info.st_size==lock['bytes']
            and info.st_size<=MAX_ARCHIVE and sha(archive_path)==lock['sha256'],'SDK_SOURCE_HASH_MISMATCH')
    extractor_bytes=Path(__file__).read_bytes()
    with tarfile.open(archive_path,'r:*') as archive:
        members=archive.getmembers();seen={};prefixes={};total=0
        require(0<len(members)<=MAX_ENTRIES,'SDK_ENTRY_LIMIT')
        for member in members:
            name=member.name.rstrip('/');components=parts(name)
            require(components[0]=='go','SDK_ROOT_MISMATCH')
            require(name not in seen and (member.isfile() or member.isdir()),'SDK_MEMBER_CONFLICT')
            seen[name]=member
            for i in range(1,len(components)+1):
                prefix='/'.join(components[:i]);key=unicodedata.normalize('NFD',prefix).casefold()
                require(key not in prefixes or prefixes[key]==prefix,'SDK_PATH_ALIAS')
                prefixes[key]=prefix
            if member.isfile():
                total+=member.size
                require(0<=member.size<=256*1024*1024 and total<=MAX_EXPANDED,'SDK_SIZE_LIMIT')
        require('go' in seen and seen['go'].isdir(),'SDK_ROOT_MISSING')
        for name in seen:
            components=name.split('/')
            require(all('/'.join(components[:i]) not in seen or seen['/'.join(components[:i])].isdir()
                        for i in range(1,len(components))),'SDK_FILE_PARENT_CONFLICT')
        require('go/vf-build-source.json' not in seen,'SDK_RESERVED_METADATA')
        output.mkdir(mode=0o700)
        for name,member in seen.items():
            components=name.split('/')[1:]
            if not components:continue
            target=output.joinpath(*components)
            if member.isdir():target.mkdir(mode=0o700,parents=True,exist_ok=True)
            else:
                target.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
                with archive.extractfile(member) as src,target.open('xb') as dst:shutil.copyfileobj(src,dst)
                target.chmod(0o755 if member.mode&0o111 else 0o644)
    require(sha(archive_path)==lock['sha256'] and Path(__file__).read_bytes()==extractor_bytes,'SDK_INPUT_CHANGED')
    result={'schema':'vf.workspace-sdk-extraction/v1','source_sha256':lock['sha256'],
            'source_filename':archive_path.name,'source_bytes':info.st_size,'version':lock['version'],
            'entries':len(seen),'expanded_bytes':total,'unicode_entries':sum(not n.isascii() for n in seen),
            'extractor_sha256':hashlib.sha256(extractor_bytes).hexdigest(),
            'status':'sdk_build_input_unpacked','user_runtime':False,'installed_globally':False}
    (output/'vf-build-source.json').write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--lock',type=Path,required=True)
    args=parser.parse_args();lock=read_sdk_lock(args.lock)
    require(args.archive.name==lock['filename'],'SDK_FILENAME_MISMATCH')
    print(json.dumps(extract_sdk(args.archive,args.output,lock)))


if __name__=='__main__':
    try:main()
    except (ValueError,OSError,KeyError,tarfile.TarError) as exc:
        code=str(exc) if isinstance(exc,ValueError) and re.fullmatch('[A-Z_]+',str(exc)) else 'SDK_INPUT_FAILED'
        print(json.dumps({'error':code}),file=__import__('sys').stderr);raise SystemExit(2)
