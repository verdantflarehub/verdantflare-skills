"""Offline Hub development example. No HTTP API, credentials or host installation."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import stat
import sys
import zipfile

VERSION = '0.1.0'
SOURCE_SHA256 = '529deb60b309edee7cb2c5b98c0b8a2fead15650a4811e1efa2a81dffca3102b'
MAX_BYTES = 16 * 1024 * 1024
SCENARIOS = ('preview', 'missing-installer', 'withdrawn', 'checksum-mismatch')


class ExampleError(ValueError):
    pass


def need(value, code):
    if not value:
        raise ExampleError(code)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def encode(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + '\n').encode()


def decode(raw):
    def pairs(items):
        value = {}
        for k,v in items:
            need(k not in value, 'DUPLICATE_KEY')
            value[k] = v
        return value
    return json.loads(raw, object_pairs_hook=pairs)


def relative(name):
    need(type(name) is str and name and len(name)<1024
         and all(re.fullmatch('[a-zA-Z0-9_.-]+', p) and p not in ('.','..')
                 for p in name.split('/')), 'INVALID_PATH')
    return name


def no_links(path):
    need(not any(p.is_symlink() for p in (path.absolute(), *path.absolute().parents)), 'LINK_REJECTED')


def read(path):
    no_links(path)
    info=path.stat()
    need(stat.S_ISREG(info.st_mode) and info.st_size<=MAX_BYTES, 'INVALID_FILE')
    raw=path.read_bytes()
    need(len(raw)==info.st_size, 'FILE_CHANGED')
    return raw


def source_files(path):
    raw=read(path)
    need(sha(raw)==SOURCE_SHA256, 'SOURCE_HANDOFF_HASH_MISMATCH')
    with zipfile.ZipFile(path) as z:
        items=z.infolist();names=[i.filename for i in items]
        need(len(set(names))==len(names) and len(names)<512
             and sum(i.file_size for i in items)<MAX_BYTES, 'INVALID_HANDOFF')
        for item in items:
            relative(item.filename)
            need(not item.is_dir() and not item.flag_bits&1
                 and stat.S_IFMT(item.external_attr>>16)==stat.S_IFREG, 'INVALID_HANDOFF_MEMBER')
        files={i.filename:z.read(i) for i in items}
    manifest=decode(files['candidate/release-manifest.json'])
    need(manifest['installable'] is False and manifest['public_distribution'] is False
         and manifest['schema_version']=='vf.skills.handoff/v2', 'UNEXPECTED_CANDIDATE')
    for package in manifest['packages']:
        a=package['artifact']
        need(len(files['candidate/'+a['path']])==a['size']
             and sha(files['candidate/'+a['path']])==a['sha256'], 'PACKAGE_HASH_MISMATCH')
    return files, manifest


def project(manifest, source_manifest_sha):
    return {'schema':'vf.hub-development-example/v1','example_version':VERSION,
            'sample':True,'production_usable':False,'publishedReleaseId':None,
            'distribution':'unavailable','candidateReleaseId':manifest['release_id'],
            'candidateManifestSha256':source_manifest_sha,'sourceHandoffSha256':SOURCE_SHA256,
            'installable':False,'installer':{'status':'missing','path':None,'sha256':None},
            'skills':[{'id':p['id'],'version':p['version'],'requires':p['requires'],
                       'artifact':{**p['artifact'],'path':'candidate/'+p['artifact']['path']}}
                      for p in manifest['packages']],
            'supportMatrix':manifest['support_matrix'],
            'testScenarios':[{'id':s,'simulated':True,'installationPerformed':False} for s in SCENARIOS]}


def index(root):
    files=decode(read(root/'example-files.json'))
    need(type(files) is dict and 1<=len(files)<512, 'INVALID_FILE_INDEX')
    for name, record in files.items():
        relative(name)
        need(type(record) is dict and set(record)=={'size','sha256'}, 'INVALID_FILE_INDEX')
        raw=read(root/name)
        need(len(raw)==record['size'] and sha(raw)==record['sha256'], 'EXAMPLE_FILE_HASH_MISMATCH')
    actual=set()
    for path in root.rglob('*'):
        need(not path.is_symlink(), 'LINK_REJECTED')
        if path.is_file():actual.add(path.relative_to(root).as_posix())
    need(actual==set(files)|{'example-files.json'}, 'EXAMPLE_MEMBER_SET_CHANGED')
    m=decode(read(root/'candidate/release-manifest.json'))
    expected=project(m,sha(read(root/'candidate/release-manifest.json')))
    need(decode(read(root/'hub-example.json'))==expected, 'EXAMPLE_PROJECTION_CHANGED')
    return expected


def closure(example, requested):
    need(type(requested) is list and requested, 'SKILL_REQUIRED')
    packages={p['id']:p for p in example['skills']};done=[];visiting=set()
    def visit(key):
        need(key in packages, 'UNKNOWN_SKILL')
        need(key not in visiting, 'DEPENDENCY_CYCLE')
        if key in done:return
        visiting.add(key)
        for dep in packages[key]['requires']:visit(dep)
        visiting.remove(key);done.append(key)
    for key in requested:visit(key)
    return [packages[key] for key in done]


def plan(example, requested, scenario):
    packages=closure(example,requested)
    need(scenario in SCENARIOS,'UNKNOWN_SCENARIO')
    reason={'preview':'ENGINEERING_PREVIEW_ONLY','missing-installer':'INSTALLER_NOT_DELIVERED',
            'withdrawn':'SIMULATED_RELEASE_WITHDRAWN','checksum-mismatch':'SIMULATED_CHECKSUM_MISMATCH'}[scenario]
    return {'schema':'vf.hub-development-plan/v1','sample':True,'scenario':scenario,
            'candidateReleaseId':example['candidateReleaseId'],'requestedSkills':requested,
            'orderedPackages':[{'id':p['id'],'path':p['artifact']['path'],'size':p['artifact']['size'],
                                'sha256':p['artifact']['sha256']} for p in packages],
            'downloadBytes':sum(p['artifact']['size'] for p in packages),
            'engineeringDownloadAllowed':scenario in ('preview','missing-installer'),
            'installAllowed':False,'installationPerformed':False,'reason':reason}


def download(root, requested, output, scenario):
    example=index(root);result=plan(example,requested,scenario)
    need(result['engineeringDownloadAllowed'], result['reason'])
    output=output.absolute();no_links(output)
    need(not output.exists() and output.parent.is_dir(), 'NEW_OUTPUT_DIRECTORY_REQUIRED')
    need(root.resolve()!=output and root.resolve() not in output.parents, 'OUTPUT_INSIDE_EXAMPLE')
    # Capture/verify every byte before creating output. Never open payload ZIPs.
    captured={}
    for p in result['orderedPackages']:
        raw=read(root/p['path']);need(len(raw)==p['size'] and sha(raw)==p['sha256'], 'PACKAGE_HASH_MISMATCH')
        captured[p['id']+'.zip']=raw
    output.mkdir()
    for name,raw in captured.items():
        with (output/name).open('xb') as stream:stream.write(raw)
    receipt={**result,'status':'engineering_bytes_copied','hostVerified':False,
             'files':{name:{'size':len(raw),'sha256':sha(raw)} for name,raw in captured.items()}}
    (output/'download-receipt.json').write_bytes(encode(receipt))
    return receipt


def build(source,output):
    files,manifest=source_files(source)
    files['hub-example.json']=encode(project(manifest,sha(files['candidate/release-manifest.json'])))
    # The original handoff validator and all payload bytes remain unchanged.
    files['hub_example.py']=read(Path(__file__))
    files['START-HERE.md']=read(Path(__file__).with_name('START-HERE.md'))
    preview=decode(files['hub-example.json'])
    for scenario in SCENARIOS:
        files['examples/project-'+scenario+'.json']=encode(plan(preview,['verdantflare-project'],scenario))
    files['examples/workbuddy-selection.json']=encode({
        'schema':'vf.hub-development-selection/v1','sample':True,'candidateReleaseId':manifest['release_id'],
        'skillIds':['verdantflare-project'],'host':'workbuddy','os':'windows',
        'environmentRef':None,'supportStatus':'not_run','installAllowed':False,
        'reason':'INSTALLER_AND_HOST_PROFILE_NOT_DELIVERED',
        'note':'Local test fixture; not a web-to-device authorization request or production API.'})
    files['example-files.json']=encode({n:{'size':len(raw),'sha256':sha(raw)} for n,raw in sorted(files.items())})
    no_links(output);need(not output.exists() and output.parent.is_dir(),'NEW_OUTPUT_FILE_REQUIRED')
    with output.open('xb') as stream,zipfile.ZipFile(stream,'w',compression=zipfile.ZIP_STORED) as z:
        for name,raw in sorted(files.items()):
            info=zipfile.ZipInfo(name,(1980,1,1,0,0,0));info.create_system=3
            info.external_attr=(stat.S_IFREG|0o644)<<16;z.writestr(info,raw)
    return {'status':'example_built','path':output.name,'sha256':sha(read(output)),
            'bytes':output.stat().st_size,'skills':len(manifest['packages']),'sample':True,'installable':False}


def main():
    p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='command',required=True)
    b=sub.add_parser('build');b.add_argument('--source',type=Path,required=True);b.add_argument('--output',type=Path,required=True)
    for command in ('verify','plan','download'):
        c=sub.add_parser(command);c.add_argument('--root',type=Path,default=Path('.'))
        if command!='verify':
            c.add_argument('--skill',action='append',required=True);c.add_argument('--scenario',choices=SCENARIOS,default='preview')
        if command=='download':c.add_argument('--output',type=Path,required=True)
    args=p.parse_args()
    if args.command=='build':result=build(args.source,args.output)
    elif args.command=='verify':
        e=index(args.root.absolute());result={'status':'example_integrity_verified','skills':len(e['skills']),'installable':False,'sample':True}
    elif args.command=='plan':result=plan(index(args.root.absolute()),args.skill,args.scenario)
    else:result=download(args.root.absolute(),args.skill,args.output,args.scenario)
    print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':
    try:main()
    except (ValueError,OSError,KeyError,TypeError,RecursionError,zipfile.BadZipFile) as exc:
        code=str(exc) if isinstance(exc,ExampleError) and re.fullmatch('[A-Z_]+',str(exc)) else 'INVALID_EXAMPLE'
        print(json.dumps({'status':'rejected','error':code,'installationPerformed':False}),file=sys.stderr)
        raise SystemExit(2)
