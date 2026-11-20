"""Validate and unpack a pinned llvm-mingw build toolchain in a new private root.

This toolchain is a build input, not a runtime shipped to VF users. Internal
relative links are preserved only after the complete member graph is checked.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import posixpath
import shutil
import stat
import tarfile

from build_media_runtime import no_links, relative, require, sha, unique_path_spelling


def prepare(archive_path, output, *, expected_sha256, root):
    archive_path=archive_path.absolute();output=output.absolute()
    no_links(archive_path);no_links(output);relative(root)
    require('/' not in root,'INVALID_TOOLCHAIN_ROOT')
    require(not output.exists() and output.parent.is_dir(),'NEW_OUTPUT_REQUIRED')
    info=archive_path.stat()
    require(stat.S_ISREG(info.st_mode) and info.st_nlink==1 and info.st_size<=512*1024*1024,
            'INVALID_TOOLCHAIN_FILE')
    require(sha(archive_path)==expected_sha256,'TOOLCHAIN_HASH_MISMATCH')
    with tarfile.open(archive_path,'r:*') as archive:
        members=archive.getmembers();entries={};folded={};total=0
        require(0<len(members)<=20000,'TOOLCHAIN_ENTRY_LIMIT')
        for member in members:
            name=member.name.rstrip('/');relative(name)
            require(name==root or name.startswith(root+'/'),'TOOLCHAIN_ROOT_MISMATCH')
            require(name not in entries and name.casefold() not in folded,'TOOLCHAIN_PATH_COLLISION')
            require(member.isdir() or member.isfile() or member.issym(),'TOOLCHAIN_MEMBER_TYPE')
            entries[name]=member;folded[name.casefold()]=name
            if member.isfile():
                total+=member.size
                require(0<=member.size<=512*1024*1024 and total<=2*1024*1024*1024,'TOOLCHAIN_SIZE_LIMIT')
        unique_path_spelling(entries,'TOOLCHAIN_CASE_ALIAS')
        require(root in entries and entries[root].isdir(),'TOOLCHAIN_ROOT_MISSING')
        for name,member in entries.items():
            for i in range(1,len(name.split('/'))):
                parent='/'.join(name.split('/')[:i]);key=parent.casefold()
                require(key not in folded or (folded[key]==parent and entries[parent].isdir()),
                        'TOOLCHAIN_PARENT_NOT_DIRECTORY')
            if not member.issym():continue
            target=member.linkname
            require(type(target) is str and target and not target.startswith('/') and '\\' not in target,
                    'INVALID_TOOLCHAIN_LINK')
            relative('/'.join(p for p in target.split('/') if p not in ('.','..')))
            current=name;visited={name}
            while entries[current].issym():
                parts=posixpath.dirname(current).split('/')
                target_parts=entries[current].linkname.split('/')
                for index,part in enumerate(target_parts):
                    if part=='..':
                        require(len(parts)>1,'TOOLCHAIN_LINK_ESCAPE');parts.pop()
                    elif part!='.':parts.append(part)
                    intermediate='/'.join(parts)
                    if index<len(target_parts)-1:
                        require(intermediate not in entries or entries[intermediate].isdir(),
                                'TOOLCHAIN_LINK_PARENT')
                current='/'.join(parts)
                require(current in entries and current not in visited,'TOOLCHAIN_LINK_CYCLE_OR_MISSING')
                visited.add(current)
        # Write regular files first. No member has a symlink parent.
        output.mkdir(mode=0o700)
        for name,member in entries.items():
            tail=PurePosixPath(name).parts[1:]
            if not tail:continue
            target=output.joinpath(*tail)
            if member.isdir():target.mkdir(mode=0o700,parents=True,exist_ok=True)
            elif member.isfile():
                target.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
                with archive.extractfile(member) as src,target.open('xb') as dst:shutil.copyfileobj(src,dst)
                target.chmod(0o755 if member.mode&0o111 else 0o644)
        for name,member in entries.items():
            if member.issym():
                target=output.joinpath(*PurePosixPath(name).parts[1:])
                target.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
                os.symlink(member.linkname,target)
    require(sha(archive_path)==expected_sha256,'TOOLCHAIN_CHANGED')
    receipt={'schema':'vf.media-cross-toolchain/v1','source_filename':archive_path.name,
             'source_sha256':expected_sha256,'source_bytes':info.st_size,'entries':len(entries),
             'expanded_bytes':total,'internal_symlinks':sum(m.issym() for m in members),
             'extractor_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
             'status':'build_input_unpacked','user_runtime':False,'windows_execution':False}
    (output/'vf-toolchain-receipt.json').write_text(json.dumps(receipt,indent=2)+'\n',encoding='utf-8')
    return receipt


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--sha256',required=True);parser.add_argument('--root',required=True)
    args=parser.parse_args()
    print(json.dumps(prepare(args.archive,args.output,expected_sha256=args.sha256,root=args.root)))


if __name__=='__main__':main()
