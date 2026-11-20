"""Developer-only offline recipient check of an admitted engineering candidate.

Not the user installer: uses the invoking developer Python to inspect ZIP/tar,
then runs the candidate interpreter entirely within a new test directory.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import platform
import shutil
import subprocess
import sys
import tarfile
import zipfile

TOOLS=Path(__file__).resolve().parents[1]/'runtime_tools'
sys.path.insert(0,str(TOOLS))
import runtime_bundle as bundle


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive',type=Path,required=True)
    parser.add_argument('--sha256',required=True)
    parser.add_argument('--music-zip',type=Path,required=True)
    parser.add_argument('--music-sha256',required=True)
    parser.add_argument('--music-tests',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    checked=bundle.verify(args.archive,args.sha256)
    native={'Darwin':'macos-arm64','Windows':'windows-amd64'}.get(platform.system())
    assert checked['platform']==native,'NATIVE_RUNTIME_PLATFORM_REQUIRED'
    assert (native=='macos-arm64' and platform.machine()=='arm64') or (native=='windows-amd64' and platform.machine().lower() in ('amd64','x86_64'))
    args.output.mkdir(parents=True,exist_ok=False);output=args.output.resolve()
    with zipfile.ZipFile(args.archive) as archive:archive.extractall(output/'component')
    component=output/'component'
    # Independently run the verifier actually supplied to the recipient.
    def run(label,command,*,cwd=None,timeout=120):
        env={k:v for k,v in os.environ.items() if not k.startswith(('PYTHON','PIP_'))}
        env['PIP_CONFIG_FILE']=os.devnull;env['TMPDIR']=str(output/'temporary');env['TEMP']=env['TMPDIR'];env['TMP']=env['TMPDIR']
        result=subprocess.run(command,env=env,cwd=cwd,capture_output=True,text=True,timeout=timeout)
        log=(result.stdout+result.stderr).replace(str(Path.cwd()),'%WORKSPACE%').replace(str(Path.home()),'%USERPROFILE%')
        (output/(label+'.log')).write_text(log,encoding='utf-8')
        assert result.returncode==0,label+': see saved log'
        return result.stdout
    (output/'temporary').mkdir()
    verify_output=run('recipient-verify',[sys.executable,'tooling/runtime_bundle.py','verify',str(args.archive.resolve()),'--expected-sha256',args.sha256],cwd=component)
    assert json.loads(verify_output)['status']=='runtime_bytes_verified'
    runtime=output/'runtime';runtime.mkdir()
    with tarfile.open(component/'python.tar.gz') as archive:
        members=archive.getmembers()
        assert len(members)<=10000 and sum(m.size for m in members)<=256*1024*1024
        for member in members:
            p=PurePosixPath(member.name)
            assert not p.is_absolute() and p.parts[0]=='python' and '..' not in p.parts
            assert member.isdir() or member.isfile() or member.issym() or member.islnk()
        archive.extractall(runtime,filter='data')
    python=runtime/('python/bin/python3.12' if native=='macos-arm64' else 'python/python.exe')
    vendor=runtime/'vendor'
    run('offline-install',[str(python),'-I','-B','-m','pip','--isolated','--disable-pip-version-check','install','--no-index','--no-cache-dir','--no-compile','--no-deps','--require-hashes','--find-links',str(component/'wheels'),'--target',str(vendor),'-r',str(component/'requirements.lock')])
    music_raw=args.music_zip.read_bytes()
    assert hashlib.sha256(music_raw).hexdigest()==args.music_sha256,'MUSIC_PACKAGE_HASH_MISMATCH'
    helpers=output/'helpers'
    with zipfile.ZipFile(io.BytesIO(music_raw)) as archive:
        for name in archive.namelist():
            p=PurePosixPath(name)
            assert not p.is_absolute() and p.parts[0]=='verdantflare-music' and '..' not in p.parts
        archive.extractall(helpers)
    music=helpers/'verdantflare-music';tests=music/'tests';tests.mkdir()
    for source in sorted(args.music_tests.glob('test_*.py')):shutil.copyfile(source,tests/source.name)
    probe=Path(__file__).with_name('probe_python_runtime.py').resolve()
    run('probe',[str(python),'-I','-B',str(probe),'--vendor',str(vendor),'--music-package',str(music),'--output',str(output/'probe')],timeout=180)
    probe_receipt=json.loads((output/'probe/runtime-probe.json').read_text())
    result={'archive_sha256':args.sha256,'platform':native,'recipient_tool_verified':True,'offline_hash_locked_install':True,
            'interpreter_extracted_from_candidate':True,'probe':probe_receipt,'desktop_acceptance':False,'native_user_installer':False}
    (output/'recipient-receipt.json').write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'platform':native,'recipient_tool_verified':True,'offline_hash_locked_install':True,'music_tests':probe_receipt['music_tests'],'passed':probe_receipt['music_tests_passed'],'desktop_acceptance':False}))


if __name__=='__main__':main()
