"""Verify original Go module inputs against go.sum before private-cache use.

Implements Go's documented dirhash.Hash1 over original ZIP member names and
contents, in addition to a raw-archive SHA-256. No extraction or code execution.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
from pathlib import Path
import re
import stat
import unicodedata
import zipfile

from workspace_sources import no_links, parts, require, sha

MAX_ZIP=128*1024*1024
MAX_EXPANDED=512*1024*1024
MAX_FILES=20000


def h1_from_hashes(files):
    result=hashlib.sha256()
    for name in sorted(files):
        require('\n' not in name,'MODULE_HASH_FILENAME')
        result.update((files[name]+'  '+name+'\n').encode('utf-8'))
    return 'h1:'+base64.b64encode(result.digest()).decode('ascii')


def mod_sum(raw):
    return h1_from_hashes({'go.mod':hashlib.sha256(raw).hexdigest()})


def read_go_sum(path):
    path=path.absolute();no_links(path);info=path.stat()
    require(stat.S_ISREG(info.st_mode) and info.st_nlink==1 and info.st_size<=2*1024*1024,'INVALID_GO_SUM_FILE')
    result={}
    for line in path.read_text(encoding='utf-8').splitlines():
        row=line.split()
        require(len(row)==3 and re.fullmatch('h1:[A-Za-z0-9+/]{43}=',row[2]),'INVALID_GO_SUM_RECORD')
        key=(row[0],row[1])
        require(key not in result,'DUPLICATE_GO_SUM_RECORD');result[key]=row[2]
    require(bool(result),'EMPTY_GO_SUM')
    return result


def verify_module_zip(path,module,version,expected_sum):
    path=path.absolute();no_links(path);info=path.stat()
    require(stat.S_ISREG(info.st_mode) and info.st_nlink==1 and info.st_size<=MAX_ZIP,'INVALID_MODULE_ZIP')
    require(type(module) is str and module and '@' not in module and type(version) is str
            and re.fullmatch('v[0-9][A-Za-z0-9.+-]*',version)
            and type(expected_sum) is str and re.fullmatch('h1:[A-Za-z0-9+/]{43}=',expected_sum),'INVALID_MODULE_IDENTITY')
    root=module+'@'+version;parts(root);archive_sha=sha(path)
    with zipfile.ZipFile(path) as archive:
        members=archive.infolist();seen={};prefixes={};total=0;hashes={};files={}
        require(0<len(members)<=MAX_FILES,'MODULE_ENTRY_LIMIT')
        for member in members:
            name=member.filename.rstrip('/');components=parts(name)
            require(name==root or name.startswith(root+'/'),'MODULE_ROOT_MISMATCH')
            mode=stat.S_IFMT(member.external_attr>>16)
            require(not member.flag_bits&1 and mode in (0,stat.S_IFREG,stat.S_IFDIR)
                    and (mode!=stat.S_IFDIR or member.is_dir())
                    and (not member.is_dir() or (member.file_size==0 and mode in (0,stat.S_IFDIR))),
                    'MODULE_MEMBER_TYPE')
            require(name not in seen,'MODULE_MEMBER_COLLISION');seen[name]=member
            for i in range(1,len(components)+1):
                prefix='/'.join(components[:i]);key=unicodedata.normalize('NFD',prefix).casefold()
                require(key not in prefixes or prefixes[key]==prefix,'MODULE_PATH_ALIAS');prefixes[key]=prefix
            total+=member.file_size
            require(0<=member.file_size<=64*1024*1024 and total<=MAX_EXPANDED,'MODULE_SIZE_LIMIT')
        for name in seen:
            components=name.split('/')
            require(all('/'.join(components[:i]) not in seen or seen['/'.join(components[:i])].is_dir()
                        for i in range(1,len(components))),'MODULE_FILE_PARENT_CONFLICT')
        for name,member in seen.items():
            digest=hashlib.sha256();count=0
            with archive.open(member) as stream:
                while raw:=stream.read(1024*1024):
                    count+=len(raw);require(count<=member.file_size,'MODULE_FILE_SIZE');digest.update(raw)
            require(count==member.file_size,'MODULE_FILE_SIZE')
            hashes[member.filename]=digest.hexdigest()
            if not member.is_dir():
                files[name[len(root)+1:]]={'sha256':digest.hexdigest(),'bytes':count}
        actual=h1_from_hashes(hashes)
        require(actual==expected_sum,'MODULE_GO_SUM_MISMATCH')
    require(sha(path)==archive_sha,'MODULE_ZIP_CHANGED')
    licenses=[n for n in files if '/' not in n and n.upper().startswith(('LICENSE','LICENCE','COPYING','NOTICE'))]
    return {'schema':'vf.workspace-module-input/v1','module':module,'version':version,
            'go_sum':actual,'zip_sha256':archive_sha,'zip_bytes':info.st_size,'files':files,
            'license_files':sorted(licenses),'status':'module_original_bytes_verified'}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive',type=Path,required=True);parser.add_argument('--module',required=True)
    parser.add_argument('--version',required=True);parser.add_argument('--sum',required=True)
    args=parser.parse_args();result=verify_module_zip(args.archive,args.module,args.version,args.sum)
    print(json.dumps({k:v for k,v in result.items() if k!='files'}))


if __name__=='__main__':
    try:main()
    except (ValueError,OSError,KeyError,zipfile.BadZipFile) as exc:
        code=str(exc) if isinstance(exc,ValueError) and re.fullmatch('[A-Z_]+',str(exc)) else 'MODULE_INPUT_FAILED'
        print(json.dumps({'error':code}),file=__import__('sys').stderr);raise SystemExit(2)
