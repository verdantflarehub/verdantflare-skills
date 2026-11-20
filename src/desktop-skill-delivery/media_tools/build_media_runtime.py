"""Build isolated FFmpeg/x264 engineering components from fixed source bytes.

No downloads, package managers, global installs, media services or host changes.
Build tools and intermediate files stay under the explicit new work directory.
This is not signed admission or a native user installer.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import shutil
import stat
import subprocess
import tarfile

SCHEMA='vf.media-build-sources/v1'
MAX_SOURCE=256*1024*1024
MAX_EXPANDED=512*1024*1024
MAX_ENTRIES=20000


def require(value,code):
    if not value:raise ValueError(code)


def sha(path):
    h=hashlib.sha256()
    with path.open('rb') as stream:
        while chunk:=stream.read(1024*1024):h.update(chunk)
    return h.hexdigest()


def no_links(path):
    require(not any(p.is_symlink() for p in (path,*path.parents)),'LINK_REJECTED')


def relative(name):
    require(type(name) is str and name and name.isascii() and len(name)<=1024
            and all(re.fullmatch('[A-Za-z0-9_.+ ~-]+',p) and p not in ('.','..')
                    and p==p.strip() for p in name.split('/')),'INVALID_SOURCE_PATH')


def unique_path_spelling(names,code):
    """Include implicit directories in case-collision validation before writes."""
    prefixes={}
    for name in names:
        parts=name.split('/')
        for i in range(1,len(parts)+1):
            prefix='/'.join(parts[:i]);key=prefix.casefold()
            require(key not in prefixes or prefixes[key]==prefix,code)
            prefixes[key]=prefix


def source(record,folder):
    require(type(record) is dict and set(record)=={'filename','sha256','root','url'}
            and type(record['sha256']) is str and re.fullmatch('[0-9a-f]{64}',record['sha256'])
            and type(record['url']) is str and record['url'].startswith('https://'),'INVALID_SOURCE_RECORD')
    relative(record['filename']);relative(record['root'])
    require('/' not in record['filename'] and '/' not in record['root'],'INVALID_SOURCE_RECORD')
    path=folder/record['filename'];no_links(path)
    info=path.stat()
    require(stat.S_ISREG(info.st_mode) and info.st_nlink==1 and info.st_size<=MAX_SOURCE
            and sha(path)==record['sha256'],'SOURCE_HASH_MISMATCH')
    return path


def extract_source(path,record,output):
    """Validate whole source tar before creating its new output directory."""
    no_links(path);no_links(output)
    require(not output.exists() and output.parent.is_dir(),'NEW_OUTPUT_REQUIRED')
    require(sha(path)==record['sha256'],'SOURCE_HASH_MISMATCH')
    with tarfile.open(path,'r:*') as archive:
        members=archive.getmembers();seen=set();total=0;files=set()
        require(0<len(members)<=MAX_ENTRIES,'SOURCE_ENTRY_LIMIT')
        for member in members:
            name=member.name.rstrip('/');relative(name)
            require(name==record['root'] or name.startswith(record['root']+'/'),'SOURCE_ROOT_MISMATCH')
            require(name not in seen and (member.isdir() or member.isfile()),'SOURCE_MEMBER_CONFLICT')
            seen.add(name)
            if member.isfile():
                total+=member.size;files.add(name)
                require(0<=member.size<=MAX_SOURCE and total<=MAX_EXPANDED,'SOURCE_SIZE_LIMIT')
        unique_path_spelling(seen,'SOURCE_CASE_ALIAS')
        require(not any('/'.join(n.split('/')[:i]) in files
                        for n in seen for i in range(1,len(n.split('/')))),'SOURCE_PATH_PREFIX')
        output.mkdir(mode=0o700)
        for member in members:
            tail=PurePosixPath(member.name).parts[1:]
            if not tail:continue
            target=output.joinpath(*tail)
            if member.isdir():target.mkdir(parents=True,exist_ok=True,mode=0o700)
            else:
                target.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
                with archive.extractfile(member) as src,target.open('xb') as dst:shutil.copyfileobj(src,dst)
                target.chmod(0o755 if member.mode&0o111 else 0o644)
    require(sha(path)==record['sha256'],'SOURCE_CHANGED')


def build(sources,work,*,jobs=4,target='macos-arm64'):
    require(platform.system()=='Darwin' and platform.machine()=='arm64','NATIVE_MACOS_ARM64_REQUIRED')
    require(target in ('macos-arm64','windows-amd64'),'INVALID_MEDIA_TARGET')
    require(type(jobs) is int and 1<=jobs<=4,'INVALID_JOB_LIMIT')
    sources=sources.absolute();work=work.absolute();no_links(sources);no_links(work)
    require(not work.exists() and work.parent.is_dir(),'NEW_OUTPUT_REQUIRED')
    lock_path=sources/'media-source-lock.json';no_links(lock_path)
    lock_bytes=lock_path.read_bytes()
    builder_bytes=Path(__file__).read_bytes()
    lock=json.loads(lock_bytes)
    require(type(lock) is dict and set(lock)=={'schema','sources'} and lock['schema']==SCHEMA
            and type(lock['sources']) is dict and set(lock['sources'])=={'ffmpeg','x264','pkgconf','zlib','nasm'},'INVALID_SOURCE_LOCK')
    inputs={name:source(record,sources) for name,record in lock['sources'].items()}
    cross_bytes=None;cross=None;extractor_bytes=None
    if target=='windows-amd64':
        cross_path=sources/'cross-toolchain-lock.json';no_links(cross_path)
        cross_bytes=cross_path.read_bytes();cross=json.loads(cross_bytes)
        require(type(cross) is dict and set(cross)=={'schema','source'}
                and cross['schema']=='vf.media-cross-toolchain-source/v1','INVALID_CROSS_TOOLCHAIN_LOCK')
        cross_archive=source(cross['source'],sources)
        extractor_bytes=Path(__file__).with_name('prepare_cross_toolchain.py').read_bytes()
    work.mkdir(mode=0o700);(work/'logs').mkdir();(work/'temp').mkdir()
    for name,record in lock['sources'].items():extract_source(inputs[name],record,work/name)
    env=dict(os.environ)
    for name in ('CC','CXX','AR','ARFLAGS','RANLIB','LD','STRIP','CHOST','CROSS_PREFIX','CFLAGS','CPPFLAGS','CXXFLAGS','LDFLAGS','PKG_CONFIG_PATH','PKG_CONFIG_LIBDIR','PKG_CONFIG_SYSROOT_DIR','DESTDIR','MAKEFLAGS','MFLAGS','BASH_ENV','ENV'):
        env.pop(name,None)
    env.update({'LC_ALL':'C','LANG':'C','TMPDIR':str(work/'temp'),'SOURCE_DATE_EPOCH':'1789689600',
                'GIT_CEILING_DIRECTORIES':str(work),
                'CC':'cc','CFLAGS':'-O2 -ffile-prefix-map='+str(work)+'=/vf-build'})
    commands=[]
    def run(name,args,cwd):
        cp=subprocess.run(args,cwd=cwd,env=env,stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
        log=cp.stdout.replace(os.fsencode(work),b'%BUILD%').replace(os.fsencode(Path.home()),b'%USERPROFILE%')
        (work/'logs'/(name+'.log')).write_bytes(log)
        # Receipts use build-relative working directories and sanitized flags.
        commands.append({'step':name,'cwd':cwd.relative_to(work).as_posix(),
                         'argv':[str(x).replace(str(work),'%BUILD%') for x in args],'exit_code':cp.returncode})
        (work/'build-commands.json').write_text(json.dumps(commands,indent=2)+'\n')
        require(cp.returncode==0,'BUILD_STEP_FAILED_'+name.upper().replace('-','_'))
    run('native-compiler',['cc','--version'],work)
    run('pkgconf-configure',['./configure','--prefix=/vf-tools','--disable-shared','--enable-static'],work/'pkgconf')
    run('pkgconf-make',['make','-j'+str(jobs)],work/'pkgconf')
    run('pkgconf-install',['make','install','DESTDIR='+str(work/'tool-stage')],work/'pkgconf')
    if cross:
        from prepare_cross_toolchain import prepare
        prepare(cross_archive,work/'cross-toolchain',expected_sha256=cross['source']['sha256'],root=cross['source']['root'])
        run('nasm-configure',['./configure','--prefix=/vf-tools'],work/'nasm')
        run('nasm-make',['make','-j'+str(jobs),'nasm'],work/'nasm')
        env['PATH']=str(work/'nasm')+os.pathsep+str(work/'cross-toolchain/bin')+os.pathsep+env['PATH']
        env.update({'CC':'x86_64-w64-mingw32-clang','AR':'x86_64-w64-mingw32-ar',
                    'RANLIB':'x86_64-w64-mingw32-ranlib','LDFLAGS':'-static',
                    'CHOST':'x86_64-w64-mingw32','ARFLAGS':'rc'})
        run('cross-compiler',['x86_64-w64-mingw32-clang','--version'],work)
        run('nasm-version',['nasm','-v'],work)
    run('zlib-configure',['./configure','--static','--prefix=/vf-deps'],work/'zlib')
    run('zlib-make',['make','-j'+str(jobs)],work/'zlib')
    run('zlib-install',['make','install','DESTDIR='+str(work/'deps-stage')],work/'zlib')
    x264=['./configure','--prefix=/vf-deps','--enable-static','--enable-pic','--disable-cli','--disable-opencl']
    if cross:x264+=['--host=x86_64-w64-mingw32','--cross-prefix=../cross-toolchain/bin/x86_64-w64-mingw32-']
    run('x264-configure',x264,work/'x264')
    run('x264-make',['make','-j'+str(jobs)],work/'x264')
    run('x264-install',['make','install-lib-static','install-lib-dev','DESTDIR='+str(work/'deps-stage')],work/'x264')
    env['PKG_CONFIG_LIBDIR']=str(work/'deps-stage/vf-deps/lib/pkgconfig')
    env['PKG_CONFIG_SYSROOT_DIR']=str(work/'deps-stage')
    configure=['./configure','--prefix=/vf-media','--pkg-config=../tool-stage/vf-tools/bin/pkgconf',
               '--disable-autodetect','--enable-gpl','--enable-version3','--enable-libx264','--enable-zlib',
               '--disable-ffplay','--disable-doc','--disable-debug','--disable-shared','--enable-static',
               '--disable-network','--disable-avdevice','--extra-version=vf-engineering']
    if cross:configure+=['--enable-cross-compile','--target-os=mingw32','--arch=x86_64',
                         '--cross-prefix=../cross-toolchain/bin/x86_64-w64-mingw32-',
                         '--x86asmexe=../nasm/nasm']
    run('ffmpeg-configure',configure,work/'ffmpeg')
    run('ffmpeg-make',['make','-j'+str(jobs)],work/'ffmpeg')
    output=work/'candidate';output.mkdir();(output/'bin').mkdir();(output/'licenses').mkdir();(output/'sources').mkdir()
    binaries={}
    for name in ('ffmpeg','ffprobe'):
        filename=name+('.exe' if cross else '')
        path=work/'ffmpeg'/filename
        raw=path.read_bytes()
        require(os.fsencode(Path.home()) not in raw and os.fsencode(work) not in raw,'PRIVATE_PATH_IN_BINARY')
        if cross:
            require(raw[:2]==b'MZ' and len(raw)>64,'PE_BINARY_REQUIRED')
            pe=int.from_bytes(raw[60:64],'little')
            require(raw[pe:pe+6]==b'PE\0\0\x64\x86','AMD64_PE_BINARY_REQUIRED')
        else:require(raw[:4]==b'\xcf\xfa\xed\xfe','MACHO_BINARY_REQUIRED')
        shutil.copyfile(path,output/'bin'/filename);(output/'bin'/filename).chmod(0o755)
        if not cross:run(name+'-version',[str(output/'bin'/filename),'-version'],work)
        if cross:
            run(name+'-libraries',[str(work/'cross-toolchain/bin/llvm-readobj'),'--coff-imports',str(output/'bin'/filename)],work)
            imports=re.findall(r'^\s*Name: (\S+)\s*$',(work/'logs'/(name+'-libraries.log')).read_text(),re.M)
            allowed={'kernel32.dll','advapi32.dll','bcrypt.dll','user32.dll','shell32.dll','ole32.dll','ws2_32.dll','secur32.dll','ntdll.dll','ucrtbase.dll'}
            require(bool(imports) and all(n.lower() in allowed or re.fullmatch('api-ms-win-crt-[a-z0-9-]+\\.dll',n.lower())
                                         for n in imports),'EXTERNAL_WINDOWS_DLL_DEPENDENCY')
        else:
            run(name+'-libraries',['otool','-L',str(output/'bin'/filename)],work)
            libraries=(work/'logs'/(name+'-libraries.log')).read_text()
            require(all(line.startswith(('/usr/lib/','/System/Library/'))
                        for line in [line.strip().split(' (')[0] for line in libraries.splitlines()[1:]]),'EXTERNAL_DYLIB_DEPENDENCY')
        binaries[filename]={'sha256':sha(output/'bin'/filename),'size':len(raw),'mode':0o755}
    for name,record in lock['sources'].items():
        shutil.copyfile(inputs[name],output/'sources'/record['filename'])
        require(sha(output/'sources'/record['filename'])==record['sha256'],'SOURCE_CHANGED')
        licenses=[p for p in (work/name).iterdir() if p.is_file() and (p.name.startswith('COPYING') or p.name.startswith('LICENSE') or p.name in ('LICENCE','License.txt'))]
        require(bool(licenses),'SOURCE_LICENSE_MISSING')
        folder=output/'licenses'/name;folder.mkdir()
        for path in licenses:shutil.copyfile(path,folder/path.name)
    require(lock_path.read_bytes()==lock_bytes and Path(__file__).read_bytes()==builder_bytes,
            'BUILD_INPUT_CHANGED')
    (output/'sources/media-source-lock.json').write_bytes(lock_bytes)
    (output/'sources/build_media_runtime.py').write_bytes(builder_bytes)
    if cross:
        require(cross_path.read_bytes()==cross_bytes and Path(__file__).with_name('prepare_cross_toolchain.py').read_bytes()==extractor_bytes,
                'BUILD_INPUT_CHANGED')
        (output/'sources/cross-toolchain-lock.json').write_bytes(cross_bytes)
        (output/'sources/prepare_cross_toolchain.py').write_bytes(extractor_bytes)
        shutil.copyfile(work/'cross-toolchain/LICENSE.TXT',output/'licenses/llvm-mingw-LICENSE.TXT')
        shutil.copyfile(work/'cross-toolchain/vf-toolchain-receipt.json',output/'sources/cross-toolchain-receipt.json')
    receipt={'schema':'vf.media-runtime-build/v1','platform':target,'status':'engineering_binaries_built',
             'ffmpeg_source_sha256':lock['sources']['ffmpeg']['sha256'],'source_lock_sha256':sha(lock_path),
             'builder_sha256':hashlib.sha256(builder_bytes).hexdigest(),'binaries':binaries,'license':'GPL-3.0-or-later',
             'public_distribution':False,'installable':False,'desktop_acceptance':False,'windows_execution':False,
             'commands':commands,'cflags':'-O2 -ffile-prefix-map=%BUILD%=/vf-build','ldflags':'-static' if cross else 'system-default',
             'cross_toolchain_sha256':cross['source']['sha256'] if cross else None,
             'source_date_epoch':env['SOURCE_DATE_EPOCH'],'independent_review':'pending'}
    (output/'build-receipt.json').write_text(json.dumps(receipt,indent=2)+'\n')
    return receipt


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sources',type=Path,required=True);parser.add_argument('--work',type=Path,required=True)
    parser.add_argument('--jobs',type=int,default=4)
    parser.add_argument('--target',choices=('macos-arm64','windows-amd64'),default='macos-arm64')
    args=parser.parse_args()
    print(json.dumps(build(args.sources,args.work,jobs=args.jobs,target=args.target)))


if __name__=='__main__':
    try:main()
    except (ValueError,OSError,KeyError,tarfile.TarError) as exc:
        code=str(exc) if isinstance(exc,ValueError) and re.fullmatch('[A-Z_]+',str(exc)) else 'MEDIA_BUILD_FAILED'
        print(json.dumps({'error':code}),file=__import__('sys').stderr);raise SystemExit(2)
