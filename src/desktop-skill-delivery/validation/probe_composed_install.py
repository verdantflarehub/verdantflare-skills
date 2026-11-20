"""Run a composed runtime and real skill bytes in fresh isolated macOS roots.

No desktop, credentials, network, user configuration or creative generation.
This validates engineering integration, not the five-desktop business exam.
"""
from __future__ import annotations

import argparse
import io
import json
import os
from pathlib import Path
import platform
import stat
import subprocess
import sys
import uuid
import wave
import zipfile

TOOLS=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(TOOLS));sys.path.insert(0,str(TOOLS/'runtime_tools'));sys.path.insert(0,str(TOOLS/'media_tools'))
import runtime_composition as composition
from install_operation import InstallOperation
import install_transaction as tx
from runtime_store import RuntimeStore
from probe_media_runtime import probe as media_probe


def skills_from_sample(path,expected_sha):
    raw=composition.workspace.read(path,16*1024*1024)
    composition.need(composition.digest(raw)==expected_sha,'SAMPLE_HASH_MISMATCH')
    payload={};packages={}
    with zipfile.ZipFile(io.BytesIO(raw)) as outer:
        names=outer.namelist();composition.need(len(names)==len(set(names)),'SAMPLE_DUPLICATE_PATH')
        manifest=composition.decode(outer.read('candidate/release-manifest.json'))
        composition.need(manifest['schema_version']=='vf.skills.handoff/v2' and manifest['installable'] is False
                         and manifest['public_distribution'] is False and len(manifest['packages'])==11,'SAMPLE_MANIFEST')
        for package in manifest['packages']:
            artifact=package['artifact'];name='candidate/'+artifact['path'];content=outer.read(name)
            composition.need(len(content)==artifact['size'] and composition.digest(content)==artifact['sha256'],'SAMPLE_PACKAGE_HASH')
            composition.need(name not in packages,'SAMPLE_DUPLICATE_PACKAGE')
            packages[name]={'sha256':artifact['sha256'],'bytes':len(content)}
            with zipfile.ZipFile(io.BytesIO(content)) as inner:
                members=inner.infolist()
                composition.need(len(members)<=tx.MAX_FILES and sum(m.file_size for m in members)<=tx.MAX_TOTAL,'SAMPLE_PACKAGE_LIMIT')
                for member in members:
                    tx.relative(member.filename)
                    composition.need(not member.is_dir() and not member.flag_bits&1 and member.file_size<=tx.MAX_FILE
                                     and stat.S_IFMT(member.external_attr>>16)==stat.S_IFREG
                                     and member.filename not in payload,'SAMPLE_PACKAGE_MEMBER')
                    payload[member.filename]=inner.read(member)
    composition.need(len(payload)<=tx.MAX_FILES and sum(map(len,payload.values()))<=tx.MAX_TOTAL,'SAMPLE_TOTAL_LIMIT')
    return payload,packages,manifest['release_id']


def probe(candidate,expected_sha,sample,sample_sha,output,*,config_format='json'):
    composition.need(type(config_format) is str and config_format in {'json','toml'},'CONFIG_FORMAT_UNSUPPORTED')
    composition.need(platform.system()=='Darwin' and platform.machine()=='arm64','MACOS_ARM64_PROBE_REQUIRED')
    candidate=candidate.absolute();output=output.absolute()
    result=composition.verify_folder(candidate,expected_sha)
    composition.need(result['platform']=='macos-arm64','MACOS_COMPONENT_REQUIRED')
    payload,packages,release=skills_from_sample(sample,sample_sha)
    composition.portable.bundle.no_links(output)
    composition.need(not output.exists() and output.parent.is_dir(),'NEW_OUTPUT_REQUIRED')
    composition.need(candidate!=output and candidate not in output.parents,'PROBE_ROOT_OVERLAP')
    output.mkdir(mode=0o700)
    for name in ('state','skills','config','logs'):(output/name).mkdir(mode=0o700)
    original=output/'original parent';original.mkdir(mode=0o700);(original/'runtime').mkdir(mode=0o700)
    manifest=composition.decode(composition.workspace.read(candidate/'tree-manifest.json',tx.MAX_FILE))
    sources={n:candidate/'objects'/r['sha256'] for n,r in manifest['entries'].items() if r['type']=='file'}
    tree_sha=result['tree_sha256'];store=RuntimeStore(original/'runtime')
    store.stage(manifest,sources,expected_digest=tree_sha,transaction_id=uuid.uuid4().hex)
    moved=output/'relocated parent with spaces';original.rename(moved)
    composition.need(not original.exists(),'ORIGINAL_RUNTIME_PATH_REMAINS')
    target='config.toml' if config_format=='toml' else 'mcp.json'
    operation=InstallOperation(*(output/n for n in ('state','skills','config')),target,runtime_root=moved/'runtime',config_format=config_format)
    operation.runtime_store.verify(tree_sha)
    initial=b'{"theme":"dark","mcpServers":{"other":{"command":"example"}}}\n'
    if config_format=='toml':initial=b'# preserve user note\ntheme = \"dark\"\n[mcp_servers.other]\ncommand = \"example\"\n'
    operation.config.path.write_bytes(initial);operation.config.path.chmod(0o640)
    before=(operation.runtime.snapshot(),operation.skills.snapshot(),operation.config.snapshot(),operation.config._file())
    def install(checkpoint=lambda _:None):
        observed=operation.config.observe()
        return operation.apply(release,payload,{'url':'https://mcp.invalid/engineering'},
            expected_skill_revision=operation.skills.snapshot()[1],expected_config_revision=observed['ledger_revision'],
            expected_config_file=observed['file'],runtime_digest=tree_sha,
            expected_runtime_revision=operation.runtime.snapshot()[1],checkpoint=checkpoint)
    class Interruption(BaseException):pass
    def interrupt(phase):
        if phase=='config':raise Interruption()
    try:install(interrupt)
    except Interruption:pass
    else:raise ValueError('EXPECTED_INSTALL_INTERRUPTION')
    operation.recover();operation.recover()
    composition.need(before==(operation.runtime.snapshot(),operation.skills.snapshot(),operation.config.snapshot(),operation.config._file())
                     and operation.config.path.read_bytes()==initial
                     and operation.config.path.stat().st_mode&0o777==0o640
                     and not (operation.runtime.root/'runtime.json').exists(),'INSTALL_RECOVERY_MISMATCH')
    installed=install()
    pointer=composition.decode((operation.runtime.root/'runtime.json').read_bytes())
    composition.need(pointer['tree_sha256']==tree_sha,'INSTALLED_RUNTIME_POINTER_MISMATCH')
    tree=operation.runtime_store.versions/tree_sha/'tree';entry=tree/'bin/vf-python'
    env={'PATH':'','VF_RUNTIME_CACHE':str(output/'cache'),'LANG':'C','LC_ALL':'C'};commands=[]
    def run(name,args,expected=0):
        cp=subprocess.run([str(a) for a in args],env=env,cwd=output,capture_output=True,text=True,timeout=120)
        def sanitized(text):return text.replace(str(output),'%PROBE%').replace(str(Path.home()),'%USERPROFILE%')
        for key in ('stdout','stderr'):(output/'logs'/(name+'.'+key)).write_text(sanitized(getattr(cp,key)))
        commands.append({'step':name,'exit_code':cp.returncode})
        composition.need(cp.returncode==expected,'COMPOSED_PROBE_COMMAND_FAILED');return cp.stdout
    info=json.loads(run('python-info',[entry,'info']))
    program=output/'managed-tools-probe.py'
    program.write_text('import json,shutil,subprocess,sys\nfrom pathlib import Path\nroot=Path(sys.argv[1]);results={}\n'
        'for name,flag in [("ffmpeg","-version"),("ffprobe","-version"),("studio-workspace","--version")]:\n'
        ' path=shutil.which(name);assert path==str(root/"bin"/name),(name,path)\n'
        ' result=subprocess.run([name,flag],capture_output=True,text=True);assert result.returncode==0,result.stderr\n'
        ' results[name]={"managed_bin":True,"first_line":result.stdout.splitlines()[0]}\n'
        'result=subprocess.run(["studio-workspace","tools"],capture_output=True,text=True)\n'
        'assert result.returncode==1 and "STUDIO_SESSION_CONFIGURATION_REQUIRED" in result.stderr+result.stdout\n'
        'print(json.dumps(results))\n')
    managed=json.loads(run('managed-tools',[entry,'script',program,tree]))
    for name,content in payload.items():composition.need((operation.skills.root/name).read_bytes()==content,'INSTALLED_SKILL_CHANGED')
    fixture=output/'synthetic-silence.wav'
    with wave.open(str(fixture),'wb') as stream:
        stream.setnchannels(1);stream.setsampwidth(2);stream.setframerate(16000);stream.writeframes(b'\0\0'*16000)
    analysis=json.loads(run('installed-music-helper',[entry,'script',operation.skills.root/'verdantflare-music/scripts/analyze_tail.py',
                                                   fixture,'--planned-duration','1']))
    composition.need(analysis['status']=='passed' and analysis['duration_seconds']==1
                     and analysis['format']['sample_rate_hz']==16000,'INSTALLED_HELPER_ASSERTION')
    media=media_probe(tree/'components/media',output/'media-probe')
    operation.recover();composition.need(operation._active()['state']=='cleaned','INSTALL_CLEANUP_INCOMPLETE')
    operation.runtime_store.verify(tree_sha)
    if config_format=='toml':
        import tomllib
        config=tomllib.loads(operation.config.path.read_text())
        parent='mcp_servers'
        composition.need(b'# preserve user note\n' in operation.config.path.read_bytes(),'FOREIGN_COMMENT_CHANGED')
    else:
        config=composition.decode(operation.config.path.read_bytes());parent='mcpServers'
    composition.need(config['theme']=='dark' and config[parent]['other']=={'command':'example'},'FOREIGN_CONFIG_CHANGED')
    composition.verify_folder(candidate,expected_sha)
    receipt={'schema':'vf.composed-runtime-install-probe/v1','status':'isolated_composed_install_verified',
             'config_format':config_format,'config_schema':operation.config.schema,
             'descriptor_sha256':expected_sha,'sample_sha256':sample_sha,'runtime_tree_sha256':tree_sha,
             'runtime_entries':len(manifest['entries']),'skill_packages':len(packages),'skill_files':len(payload),
             'skill_bytes':sum(map(len,payload.values())),'packages':packages,'python_info':info,'managed_tools':managed,
             'commands':commands,'media_checks':media['checks'],'rgb_mean_absolute_error':media['rgb_mean_absolute_error'],
             'interruption_simulated_at_config':True,'three_resource_before_state_restored':True,
             'global_commit':installed['status'],'old_parent_absent':True,'parent_contains_spaces':True,
             'initial_PATH_empty':True,'installed_music_helper_executed':True,'foreign_config_preserved':True,
             'ready_tree_verified_after_execution':True,'cleanup_complete':True,'synthetic_inputs_only':True,
             'installable':False,'windows_execution':False,'desktop_acceptance':False,'mcp_acceptance':False,
             'independent_review':'pending'}
    (output/'probe-receipt.json').write_bytes(composition.encode(receipt));return receipt


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('candidate','sample','output'):p.add_argument('--'+name,type=Path,required=True)
    for name in ('sha256','sample-sha256'):p.add_argument('--'+name,required=True)
    p.add_argument('--config-format',choices=('json','toml'),default='json')
    a=p.parse_args();print(json.dumps(probe(a.candidate,a.sha256,a.sample,a.sample_sha256,a.output,config_format=a.config_format)))


if __name__=='__main__':
    try:main()
    except (ValueError,OSError,KeyError,TypeError,zipfile.BadZipFile,subprocess.TimeoutExpired,RuntimeError) as exc:
        code=str(exc) if isinstance(exc,ValueError) and __import__('re').fullmatch('[A-Z_]+',str(exc)) else 'COMPOSED_INSTALL_PROBE_FAILED'
        print(json.dumps({'error':code}),file=sys.stderr);raise SystemExit(2)
