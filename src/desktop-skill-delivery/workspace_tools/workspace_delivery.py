"""Prepare and rebuild standalone unsigned Studio workspace CLI components.

Builds use an explicit official Go SDK archive, locked original module bytes
and a selected main-module snapshot. No downloads or developer-cache fallback.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import stat
import subprocess
import unicodedata
import zipfile

import workspace_sources as sdk
import workspace_modules as modules

MAIN='github.com/verdantflarehub/verdantflare-studio'
SCHEMA='vf.workspace-cli-source/v1'
TARGETS={'macos-arm64':('darwin','arm64','studio-workspace'),
         'windows-amd64':('windows','amd64','studio-workspace.exe')}
TEST_PACKAGES=('cmd/workspace','internal/workspace','internal/workspacehttp')
TOOL_FILES=('workspace_delivery.py','workspace_sources.py','workspace_modules.py','go-toolchain-lock.json','README.md')
MAX_FILE=128*1024*1024
MAX_TOTAL=256*1024*1024
require=sdk.require


def encode(value):return (json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':'))+'\n').encode('utf-8')


def read(path,limit=MAX_FILE):
    path=path.absolute();sdk.no_links(path);info=path.stat()
    require(stat.S_ISREG(info.st_mode) and info.st_nlink==1 and info.st_size<=limit,'INVALID_WORKSPACE_INPUT_FILE')
    raw=path.read_bytes();require(len(raw)==info.st_size,'WORKSPACE_INPUT_CHANGED');return raw


def decode(raw):
    def constant(_):raise ValueError('NONFINITE_WORKSPACE_JSON')
    return json.loads(raw,object_pairs_hook=sdk.pairs,parse_constant=constant)


def digest(raw):return hashlib.sha256(raw).hexdigest()


def escaped(value):return ''.join('!'+c.lower() if c.isupper() else c for c in value)


def module_directory(module,version):
    sdk.parts(module)
    require('@' not in module and re.fullmatch('v[0-9][A-Za-z0-9.+-]*',version),'INVALID_WORKSPACE_MODULE')
    return Path(escaped(module))/'@v'


def file_set(folder):
    sdk.no_links(folder);require(folder.is_dir(),'WORKSPACE_DIRECTORY_REQUIRED')
    result=set()
    for path in folder.rglob('*'):
        sdk.no_links(path)
        require(path.is_dir() or path.is_file(),'WORKSPACE_SPECIAL_FILE')
        if path.is_file():result.add(path.relative_to(folder).as_posix())
    return result


def validate_names(names):
    prefixes={};files=set(names)
    for name in names:
        components=sdk.parts(name)
        for i in range(1,len(components)+1):
            prefix='/'.join(components[:i]);key=unicodedata.normalize('NFD',prefix).casefold()
            require(key not in prefixes or prefixes[key]==prefix,'WORKSPACE_PATH_ALIAS');prefixes[key]=prefix
            if i<len(components):require(prefix not in files,'WORKSPACE_FILE_PARENT')


def add(folder,name,raw,mode=0o644):
    sdk.parts(name);require(type(raw) is bytes and len(raw)<=MAX_FILE,'WORKSPACE_PAYLOAD_LIMIT')
    target=folder/name;target.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
    with target.open('xb') as stream:stream.write(raw)
    target.chmod(mode)


def inventory(folder,*,exclude=()):
    names=file_set(folder)-set(exclude);validate_names(names);require(len(names)<=4096,'WORKSPACE_FILE_COUNT')
    result={};total=0
    for name in sorted(names):
        path=folder/name;raw=read(path);total+=len(raw)
        require(total<=MAX_TOTAL and path.stat().st_mode&0o777 in (0o644,0o755),'WORKSPACE_PAYLOAD_LIMIT')
        result[name]={'sha256':digest(raw),'bytes':len(raw),'mode':path.stat().st_mode&0o777}
    return result


def check_files(folder,records,*,metadata):
    require(type(records) is dict and 0<len(records)<=4096,'INVALID_WORKSPACE_FILE_MANIFEST')
    validate_names(records);require(file_set(folder)==set(records)|set(metadata),'WORKSPACE_FILE_SET_CHANGED')
    total=0
    for name,item in records.items():
        require(type(item) is dict and set(item)=={'sha256','bytes','mode'}
                and type(item['sha256']) is str and re.fullmatch('[0-9a-f]{64}',item['sha256'])
                and type(item['bytes']) is int and 0<=item['bytes']<=MAX_FILE
                and type(item['mode']) is int and item['mode'] in (0o644,0o755),'INVALID_WORKSPACE_FILE_RECORD')
        raw=read(folder/name);total+=len(raw)
        require(total<=MAX_TOTAL and digest(raw)==item['sha256'] and len(raw)==item['bytes']
                and (folder/name).stat().st_mode&0o777==item['mode'],'WORKSPACE_FILE_CHANGED')


def verify_modules(folder,go_mod,go_sum):
    index=decode(read(folder/'module-inputs.json',16*1024*1024))
    require(index.get('schema')=='vf.workspace-offline-module-inputs/v1'
            and index.get('root_go_mod_sha256')==sdk.sha(go_mod)
            and index.get('root_go_sum_sha256')==sdk.sha(go_sum),'WORKSPACE_MODULE_ROOT_BINDING')
    sums=modules.read_go_sum(go_sum);expected=set();metadata={};code={}
    require(type(index.get('metadata_modules')) is list and type(index.get('code_modules')) is list
            and len(index['metadata_modules'])<=512 and len(index['code_modules'])<=128,'INVALID_WORKSPACE_MODULE_INDEX')
    for item in index['metadata_modules']:
        module,version=item['module'],item['version'];key=(module,version)
        require(key not in metadata,'WORKSPACE_MODULE_DUPLICATE')
        relative=module_directory(module,version)/(escaped(version)+'.mod')
        path=folder/'download-cache'/relative;raw=read(path,2*1024*1024)
        require(item['go_mod_sum']==sums.get((module,version+'/go.mod'))==modules.mod_sum(raw)
                and item['mod_sha256']==digest(raw) and item['mod_bytes']==len(raw),'WORKSPACE_MOD_SUM_MISMATCH')
        expected.add('download-cache/'+relative.as_posix());metadata[key]=item
    for item in index['code_modules']:
        module,version=item['module'],item['version'];key=(module,version)
        require(key in metadata and key not in code,'WORKSPACE_MODULE_DUPLICATE')
        relative=module_directory(module,version);name=escaped(version)
        archive=folder/'download-cache'/relative/(name+'.zip')
        result=modules.verify_module_zip(archive,module,version,sums.get(key))
        require(result['zip_sha256']==item['zip_sha256'] and result['zip_bytes']==item['zip_bytes']
                and result['files']==item['files'] and result['license_files']==item['license_files']
                and bool(result['license_files']),'WORKSPACE_MODULE_ZIP_CHANGED')
        if 'go.mod' in result['files']:
            require(result['files']['go.mod']['sha256']==sdk.sha(archive.with_suffix('.mod')),'WORKSPACE_MODULE_MOD_CONFLICT')
        info=archive.with_suffix('.info');raw=read(info,65536)
        require(decode(raw).get('Version')==version and digest(raw)==item['info_sha256']
                and len(raw)==item['info_bytes'],'WORKSPACE_MODULE_INFO_CHANGED')
        require(read(archive.with_suffix('.ziphash'),1024).decode('ascii')==result['go_sum'],'WORKSPACE_MODULE_ZIPHASH_CHANGED')
        expected.update('download-cache/'+(relative/(name+suffix)).as_posix() for suffix in ('.zip','.ziphash','.info'))
        code[key]=result
    require(file_set(folder)==expected|{'module-inputs.json'},'WORKSPACE_MODULE_FILE_SET')
    return index,code


def environment(work,sdk_root):
    for name in ('temp','go-cache','gopath','module-cache'):(work/name).mkdir(mode=0o700)
    return {'PATH':str(sdk_root/'bin')+os.pathsep+os.defpath,'LANG':'C','LC_ALL':'C',
            'TMPDIR':str(work/'temp'),'TMP':str(work/'temp'),'TEMP':str(work/'temp'),'GOTMPDIR':str(work/'temp'),
            'GOENV':'off','GOWORK':'off','GOTOOLCHAIN':'local','GOPROXY':'off','GOSUMDB':'off','GOFLAGS':'',
            'CGO_ENABLED':'0','GOROOT':str(sdk_root),'GOPATH':str(work/'gopath'),
            'GOMODCACHE':str(work/'module-cache'),'GOCACHE':str(work/'go-cache'),
            'GOPRIVATE':'','GONOPROXY':'','GONOSUMDB':''}


def runner(work,env,source):
    (work/'logs').mkdir(mode=0o700);commands=[]
    def sanitize(text):
        return text.replace(str(work),'%BUILD%').replace(str(source),'%SOURCE%').replace(str(Path.home()),'%USERPROFILE%')
    def run(name,args,*,cwd=source,expected=0):
        cp=subprocess.run([str(a) for a in args],cwd=cwd,env=env,capture_output=True,text=True,timeout=300)
        for key in ('stdout','stderr'):(work/'logs'/(name+'.'+key)).write_text(sanitize(getattr(cp,key)),encoding='utf-8')
        commands.append({'step':name,'argv':[sanitize(str(a)) for a in args],'exit_code':cp.returncode})
        (work/'commands.json').write_bytes(encode(commands))
        require(cp.returncode==expected,'WORKSPACE_BUILD_STEP_FAILED')
        return cp.stdout+cp.stderr
    return run,commands


def json_stream(raw):
    decoder=json.JSONDecoder();pos=0;result=[]
    while pos<len(raw):
        pos+=len(raw[pos:])-len(raw[pos:].lstrip())
        if pos==len(raw):break
        value,pos=decoder.raw_decode(raw,pos);result.append(value)
    return result


def source_selection(raw,source,*,tests=False):
    result=set()
    for package in json_stream(raw):
        if not package.get('Module',{}).get('Main'):continue
        require(package['Module']['Path']==MAIN,'UNEXPECTED_WORKSPACE_MAIN_MODULE')
        directory=Path(package['Dir']).absolute();sdk.no_links(directory)
        require(directory==source or source in directory.parents,'WORKSPACE_PACKAGE_OUTSIDE_SOURCE')
        keys=('TestGoFiles','XTestGoFiles') if tests else ('GoFiles','CgoFiles','CFiles','CXXFiles','HFiles','SFiles','SysoFiles','EmbedFiles')
        for key in keys:
            for name in package.get(key,[]):
                path=directory/name;sdk.no_links(path)
                relative=path.relative_to(source).as_posix();components=sdk.parts(relative)
                require(not any(c.startswith('.') for c in components)
                        and path.suffix in ('.go','.c','.cc','.cpp','.h','.s','.syso','.json','.sql'),
                        'UNEXPECTED_WORKSPACE_SOURCE_FILE')
                result.add(relative)
    return result


def new_work(output,inputs):
    output=output.absolute();sdk.no_links(output)
    require(not output.exists() and output.parent.is_dir(),'NEW_WORKSPACE_OUTPUT_REQUIRED')
    for source in inputs:
        source=source.absolute();sdk.no_links(source)
        require(output!=source and source not in output.parents,'WORKSPACE_ROOT_OVERLAP')
    require(platform.system()=='Darwin' and platform.machine()=='arm64','WORKSPACE_MACOS_ARM64_BUILDER_REQUIRED')
    output.mkdir(mode=0o700);return output


def prepare(source,module_inputs,sdk_archive,sdk_lock_path,output):
    source=source.absolute();module_inputs=module_inputs.absolute();sdk_archive=sdk_archive.absolute()
    sdk_lock=sdk.read_sdk_lock(sdk_lock_path);index,code=verify_modules(module_inputs,source/'go.mod',source/'go.sum')
    require(sdk.sha(sdk_archive)==sdk_lock['sha256'] and sdk_archive.stat().st_size==sdk_lock['bytes'],'SDK_SOURCE_HASH_MISMATCH')
    tool=Path(__file__).parent;tool_bytes={name:read(tool/name) for name in TOOL_FILES}
    require(decode(tool_bytes['go-toolchain-lock.json'])==sdk_lock,'WORKSPACE_TOOLCHAIN_LOCK_CONFLICT')
    output=new_work(output,(source,module_inputs,sdk_archive));work=output/'work';work.mkdir(mode=0o700)
    sdk_root=work/'sdk';sdk.extract_sdk(sdk_archive,sdk_root,sdk_lock)
    env=environment(work,sdk_root);shutil.copytree(module_inputs/'download-cache',work/'module-cache/cache/download')
    run,commands=runner(work,env,source);go=sdk_root/'bin/go';selected={'go.mod','go.sum'}
    require(run('sdk-version',[go,'version']).strip()=='go version '+sdk_lock['version']+' darwin/arm64','WORKSPACE_SDK_VERSION_MISMATCH')
    for target,(goos,goarch,_) in TARGETS.items():
        env.update({'GOOS':goos,'GOARCH':goarch})
        selected.update(source_selection(run('list-'+target,[go,'list','-mod=readonly','-deps','-json','./cmd/workspace']),source))
        selected.update(source_selection(run('test-files-'+target,[go,'list','-mod=readonly','-json',*('./'+p for p in TEST_PACKAGES)]),source,tests=True))
    require(selected-{'go.mod','go.sum'} and all((source/n).is_file() for n in selected),'WORKSPACE_SOURCE_SET_EMPTY')
    version_source=read(source/'internal/buildinfo/version.go').decode('utf-8')
    match=re.search(r'const Version = "([0-9]+\.[0-9]+\.[0-9]+(?:[-+][A-Za-z0-9.-]+)?)"',version_source)
    require(match is not None,'WORKSPACE_VERSION_UNAVAILABLE')
    candidate=output/'candidate';candidate.mkdir(mode=0o700)
    for name in sorted(selected):add(candidate,'source/'+name,read(source/name))
    for name in sorted(file_set(module_inputs)):add(candidate,'modules/'+name,read(module_inputs/name))
    for name,raw in tool_bytes.items():add(candidate,'tooling/'+name,raw)
    license_status=[]
    for name in ('LICENSE','LICENSE.md','LICENSE.txt','COPYING','COPYING.md','COPYING.txt','NOTICE','PATENTS'):
        path=source/name
        if path.exists():add(candidate,'licenses/studio/'+name,read(path));license_status.append(name)
    for path in sorted(sdk_root.rglob('*')):
        if path.is_file() and path.name.upper().startswith(('LICENSE','LICENCE','COPYING','PATENTS')):
            add(candidate,'licenses/go-sdk/'+path.relative_to(sdk_root).as_posix(),read(path))
    for (module,version),result in sorted(code.items()):
        archive=module_inputs/'download-cache'/module_directory(module,version)/(escaped(version)+'.zip')
        prefix=module+'@'+version+'/'
        with zipfile.ZipFile(archive) as z:
            for name in result['license_files']:
                add(candidate,'licenses/modules/'+digest((module+'@'+version).encode())+'/'+name,z.read(prefix+name))
    git_commit=run('git-commit',['git','rev-parse','HEAD']).strip()
    require(re.fullmatch('[0-9a-f]{40}',git_commit),'WORKSPACE_SOURCE_COMMIT_UNAVAILABLE')
    dirty=bool(run('git-status',['git','status','--porcelain','--untracked-files=normal']).strip())
    selected_dirty=bool(run('git-selected-status',['git','status','--porcelain','--untracked-files=normal','--',*sorted(selected)]).strip())
    status={'source_kind':'explicit_working_tree_snapshot','git_commit':git_commit,
            'worktree_dirty':dirty,'selected_source_dirty':selected_dirty,
            'original_license_files':license_status,'main_source_license_grant_verified':False,
            'public_distribution':False,'installable':False}
    add(candidate,'source-status.json',encode(status))
    require(all(read(tool/name)==raw for name,raw in tool_bytes.items()),'WORKSPACE_BUILDER_CHANGED')
    source_lock={'schema':SCHEMA,'version':1,'main_module':MAIN,'cli_version':match.group(1),
                 'sdk_lock':sdk_lock,'source':status,'source_files':sorted(selected),
                 'files':inventory(candidate),'sdk_archive_included':False,
                 'public_distribution':False,'installable':False,'desktop_acceptance':False,
                 'windows_execution':False,'authentication':'unsigned'}
    add(candidate,'source-lock.json',encode(source_lock));h=sdk.sha(candidate/'source-lock.json')
    verify_source(candidate,h)
    return {'status':'workspace_source_candidate_prepared','sha256':h,'files':len(source_lock['files']),
            'source_files':len(selected),'module_count':len(code),'installable':False}


def verify_source(folder,expected_sha):
    folder=folder.absolute();raw=read(folder/'source-lock.json',16*1024*1024)
    require(type(expected_sha) is str and re.fullmatch('[0-9a-f]{64}',expected_sha) and digest(raw)==expected_sha,'WORKSPACE_SOURCE_LOCK_HASH')
    lock=decode(raw)
    require(type(lock) is dict and set(lock)=={'schema','version','main_module','cli_version','sdk_lock','source','source_files','files',
            'sdk_archive_included','public_distribution','installable','desktop_acceptance','windows_execution','authentication'}
            and lock['schema']==SCHEMA and type(lock['version']) is int and lock['version']==1 and lock['main_module']==MAIN
            and lock['authentication']=='unsigned' and all(lock[k] is False for k in
                ('sdk_archive_included','public_distribution','installable','desktop_acceptance','windows_execution')),'INVALID_WORKSPACE_SOURCE_LOCK')
    check_files(folder,lock['files'],metadata=('source-lock.json',))
    require(type(lock['cli_version']) is str and re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+(?:[-+][A-Za-z0-9.-]+)?',lock['cli_version'])
            and type(lock['source_files']) is list and all(type(n) is str for n in lock['source_files'])
            and len(set(lock['source_files']))==len(lock['source_files'])
            and {n[7:] for n in lock['files'] if n.startswith('source/')}==set(lock['source_files'])
            and {'go.mod','go.sum','internal/buildinfo/version.go'}<=set(lock['source_files']),'WORKSPACE_SOURCE_FILE_BINDING')
    require({n[8:] for n in lock['files'] if n.startswith('tooling/')}==set(TOOL_FILES)
            and all(n=='source-status.json' or n.startswith(('source/','modules/','tooling/','licenses/'))
                    for n in lock['files']),'WORKSPACE_SOURCE_LAYOUT')
    require(sdk.read_sdk_lock(folder/'tooling/go-toolchain-lock.json')==lock['sdk_lock'],'WORKSPACE_SDK_LOCK_BINDING')
    verify_modules(folder/'modules',folder/'source/go.mod',folder/'source/go.sum')
    status=decode(read(folder/'source-status.json'))
    require(type(status) is dict and set(status)=={'source_kind','git_commit','worktree_dirty','selected_source_dirty',
            'original_license_files','main_source_license_grant_verified','public_distribution','installable'}
            and status==lock['source'] and status['source_kind']=='explicit_working_tree_snapshot'
            and type(status['git_commit']) is str and re.fullmatch('[0-9a-f]{40}',status['git_commit'])
            and type(status['worktree_dirty']) is bool and type(status['selected_source_dirty']) is bool
            and all(status[k] is False for k in ('main_source_license_grant_verified','public_distribution','installable'))
            and type(status['original_license_files']) is list
            and all(type(n) is str for n in status['original_license_files'])
            and len(set(status['original_license_files']))==len(status['original_license_files'])
            and set(status['original_license_files'])=={n[16:] for n in lock['files'] if n.startswith('licenses/studio/')},
            'WORKSPACE_SOURCE_STATUS_BINDING')
    match=re.search(r'const Version = "([0-9]+\.[0-9]+\.[0-9]+(?:[-+][A-Za-z0-9.-]+)?)"',
                    read(folder/'source/internal/buildinfo/version.go').decode('utf-8'))
    require(match is not None and match.group(1)==lock['cli_version'],'WORKSPACE_SOURCE_VERSION_BINDING')
    return lock


def binary_format(raw,target):
    if target=='windows-amd64':
        require(raw[:2]==b'MZ' and len(raw)>64,'WORKSPACE_PE_REQUIRED');offset=int.from_bytes(raw[60:64],'little')
        require(64<=offset<=len(raw)-6 and raw[offset:offset+6]==b'PE\0\0\x64\x86','WORKSPACE_AMD64_REQUIRED')
    else:
        require(len(raw)>=32 and raw[:8]==b'\xcf\xfa\xed\xfe\x0c\x00\x00\x01','WORKSPACE_ARM64_MACHO_REQUIRED')


def verify_runtime(folder,expected_sha):
    folder=folder.absolute();raw=read(folder/'workspace-runtime.json',16*1024*1024)
    require(type(expected_sha) is str and re.fullmatch('[0-9a-f]{64}',expected_sha)
            and digest(raw)==expected_sha,'WORKSPACE_RUNTIME_HASH')
    value=decode(raw)
    require(type(value) is dict and set(value)=={'schema','version','source_lock_sha256','sdk_version','sdk_source_sha256',
            'binaries','tests_passed','tests_failed','tests_skipped','files','authentication','installable','public_distribution',
            'main_source_license_grant_verified','windows_execution','mcp_acceptance','desktop_acceptance','independent_review'}
            and value['schema']=='vf.workspace-cli-candidate/v1' and value['authentication']=='unsigned'
            and value['independent_review']=='pending' and all(value[k] is False for k in
                ('installable','public_distribution','main_source_license_grant_verified','windows_execution','mcp_acceptance','desktop_acceptance'))
            and type(value['tests_passed']) is int and value['tests_passed']>0
            and type(value['tests_failed']) is int and value['tests_failed']==0
            and type(value['tests_skipped']) is int and value['tests_skipped']==0,'INVALID_WORKSPACE_RUNTIME')
    check_files(folder,value['files'],metadata=('workspace-runtime.json',))
    source=verify_source(folder/'build-source',value['source_lock_sha256'])
    require(value['version']==source['cli_version'] and value['sdk_version']==source['sdk_lock']['version']
            and value['sdk_source_sha256']==source['sdk_lock']['sha256'],'WORKSPACE_RUNTIME_SOURCE_BINDING')
    require(type(value['binaries']) is dict and set(value['binaries'])==set(TARGETS),'WORKSPACE_BINARY_SET')
    binary_names=set()
    for target,(_,_,name) in TARGETS.items():
        item=value['binaries'][target];path='bin/'+target+'/'+name;binary_names.add(path)
        require(type(item) is dict and set(item)=={'path','sha256','bytes'} and item['path']==path
                and path in value['files'] and item['sha256']==value['files'][path]['sha256']
                and type(item['bytes']) is int and item['bytes']==value['files'][path]['bytes']
                and value['files'][path]['mode']==0o755,'WORKSPACE_BINARY_BINDING')
        binary_format(read(folder/path),target)
    source_names={'build-source/'+n for n in source['files']}|{'build-source/source-lock.json'}
    license_names={'licenses/'+n[9:] for n in source['files'] if n.startswith('licenses/')}
    require(set(value['files'])==source_names|license_names|binary_names,'WORKSPACE_RUNTIME_LAYOUT')
    for name in license_names:
        require(value['files'][name]==value['files']['build-source/'+name],'WORKSPACE_LICENSE_COPY_CHANGED')
    return value


def probe(folder,expected_sha,output):
    folder=folder.absolute();value=verify_runtime(folder,expected_sha)
    output=new_work(output,(folder,));env={'PATH':'','LANG':'C','LC_ALL':'C'}
    original=output/'original parent';original.mkdir(mode=0o700)
    binary=original/'studio-workspace';shutil.copy2(folder/value['binaries']['macos-arm64']['path'],binary)
    moved=output/'relocated parent with spaces';original.rename(moved);binary=moved/binary.name
    require(not original.exists(),'WORKSPACE_OLD_PATH_REMAINS')
    run,commands=runner(output,env,moved)
    require(run('version',[binary,'--version']).strip()==value['version'],'WORKSPACE_CLI_VERSION_MISMATCH')
    require('studio-workspace tools' in run('help',[binary,'--help']),'WORKSPACE_CLI_HELP_MISMATCH')
    require('STUDIO_SESSION_CONFIGURATION_REQUIRED' in run('missing-session',[binary,'tools'],expected=1),'WORKSPACE_MISSING_SESSION_ACCEPTED')
    require(sdk.sha(binary)==value['binaries']['macos-arm64']['sha256'],'WORKSPACE_MOVED_BINARY_CHANGED')
    verify_runtime(folder,expected_sha)
    result={'schema':'vf.workspace-cli-native-probe/v1','component_sha256':expected_sha,'platform':'macos-arm64',
            'checks':['moved-parent-with-spaces','old-parent-absent','empty-path','version','help','missing-identity-refused','binary-unchanged'],
            'commands':commands,'mcp_acceptance':False,'desktop_acceptance':False,'windows_execution':False,'installable':False}
    add(output,'probe-receipt.json',encode(result))
    return result


def build(folder,expected_sha,sdk_archive,output):
    folder=folder.absolute();sdk_archive=sdk_archive.absolute();lock=verify_source(folder,expected_sha)
    own=Path(__file__).parent;tool_bytes={n:read(own/n) for n in TOOL_FILES}
    require(all(raw==read(folder/'tooling'/n) for n,raw in tool_bytes.items()),'WORKSPACE_BUILD_TOOL_VERSION')
    require(sdk.sha(sdk_archive)==lock['sdk_lock']['sha256'],'SDK_SOURCE_HASH_MISMATCH')
    output=new_work(output,(folder,sdk_archive));candidate=output/'candidate';candidate.mkdir(mode=0o700)
    shutil.copytree(folder,candidate/'build-source');verify_source(candidate/'build-source',expected_sha)
    work=output/'work';work.mkdir(mode=0o700);sdk_root=work/'sdk';sdk.extract_sdk(sdk_archive,sdk_root,lock['sdk_lock'])
    env=environment(work,sdk_root);shutil.copytree(folder/'modules/download-cache',work/'module-cache/cache/download')
    source=candidate/'build-source/source';run,commands=runner(work,env,source);go=sdk_root/'bin/go'
    require(run('sdk-version',[go,'version']).strip()=='go version '+lock['sdk_lock']['version']+' darwin/arm64','WORKSPACE_SDK_VERSION_MISMATCH')
    binaries={}
    for target,(goos,goarch,name) in TARGETS.items():
        env.update({'GOOS':goos,'GOARCH':goarch});destination=candidate/'bin'/target;destination.mkdir(parents=True,mode=0o700)
        binary=destination/name
        run('list-'+target,[go,'list','-mod=readonly','-deps','-json','./cmd/workspace'])
        run('build-'+target,[go,'build','-mod=readonly','-trimpath','-buildvcs=false','-p','2','-ldflags=-s -w','-o',binary,'./cmd/workspace'])
        raw=read(binary);binary.chmod(0o755)
        require(os.fsencode(Path.home()) not in raw and os.fsencode(output) not in raw,'WORKSPACE_PRIVATE_PATH_IN_BINARY')
        if goos=='windows':
            require(raw[:2]==b'MZ' and len(raw)>64,'WORKSPACE_PE_REQUIRED');offset=int.from_bytes(raw[60:64],'little')
            require(raw[offset:offset+6]==b'PE\0\0\x64\x86','WORKSPACE_AMD64_REQUIRED')
        else:
            require(raw[:4]==b'\xcf\xfa\xed\xfe','WORKSPACE_MACHO_REQUIRED')
            require(run('cli-version',[binary,'--version']).strip()==lock['cli_version'],'WORKSPACE_CLI_VERSION_MISMATCH')
            require('studio-workspace tools' in run('cli-help',[binary,'--help']),'WORKSPACE_CLI_HELP_MISMATCH')
            require('STUDIO_SESSION_CONFIGURATION_REQUIRED' in run('missing-session',[binary,'tools'],expected=1),'WORKSPACE_MISSING_SESSION_ACCEPTED')
            libraries=run('dylibs',['otool','-L',binary])
            require(all(line.strip().split(' (')[0].startswith(('/usr/lib/','/System/Library/')) for line in libraries.splitlines()[1:]),'WORKSPACE_EXTERNAL_DYLIB')
        run('metadata-'+target,[go,'version','-m',binary])
        binaries[target]={'path':binary.relative_to(candidate).as_posix(),'sha256':digest(raw),'bytes':len(raw)}
    env.update({'GOOS':'darwin','GOARCH':'arm64'})
    events=json_stream(run('tests',[go,'test','-mod=readonly','-p','2','-count=1','-json',*('./'+p for p in TEST_PACKAGES)]))
    terminal=[e for e in events if e.get('Test') and e.get('Action') in ('pass','fail','skip')]
    require(bool(terminal) and all(e['Action']=='pass' for e in terminal),'WORKSPACE_TEST_FAILURE_OR_SKIP')
    verify_source(candidate/'build-source',expected_sha)
    require(all(read(own/n)==raw for n,raw in tool_bytes.items()),'WORKSPACE_BUILDER_CHANGED')
    shutil.copytree(candidate/'build-source/licenses',candidate/'licenses')
    descriptor={'schema':'vf.workspace-cli-candidate/v1','version':lock['cli_version'],'source_lock_sha256':expected_sha,
                'sdk_version':lock['sdk_lock']['version'],'sdk_source_sha256':lock['sdk_lock']['sha256'],
                'binaries':binaries,'tests_passed':len(terminal),'tests_failed':0,'tests_skipped':0,
                'files':inventory(candidate),'authentication':'unsigned','installable':False,'public_distribution':False,
                'main_source_license_grant_verified':False,'windows_execution':False,'mcp_acceptance':False,
                'desktop_acceptance':False,'independent_review':'pending'}
    add(candidate,'workspace-runtime.json',encode(descriptor))
    verify_runtime(candidate,sdk.sha(candidate/'workspace-runtime.json'))
    return {'status':'workspace_cli_component_built','sha256':sdk.sha(candidate/'workspace-runtime.json'),
            'binaries':binaries,'tests_passed':len(terminal),'installable':False,'windows_execution':False}


def main():
    parser=argparse.ArgumentParser(description=__doc__);sub=parser.add_subparsers(dest='command',required=True)
    p=sub.add_parser('prepare');p.add_argument('--source',type=Path,required=True);p.add_argument('--modules',type=Path,required=True)
    p.add_argument('--sdk-archive',type=Path,required=True);p.add_argument('--sdk-lock',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    for name in ('verify','build','verify-runtime','probe'):
        p=sub.add_parser(name);p.add_argument('--candidate',type=Path,required=True);p.add_argument('--sha256',required=True)
        if name=='build':p.add_argument('--sdk-archive',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
        if name=='probe':p.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if args.command=='prepare':result=prepare(args.source,args.modules,args.sdk_archive,args.sdk_lock,args.output)
    elif args.command=='build':result=build(args.candidate,args.sha256,args.sdk_archive,args.output)
    elif args.command=='probe':result=probe(args.candidate,args.sha256,args.output)
    elif args.command=='verify-runtime':
        value=verify_runtime(args.candidate,args.sha256)
        result={'status':'workspace_runtime_verified','files':len(value['files']),'binaries':value['binaries'],'installable':False}
    else:
        lock=verify_source(args.candidate,args.sha256)
        result={'status':'workspace_source_verified','files':len(lock['files']),'installable':False}
    print(json.dumps(result))


if __name__=='__main__':
    try:main()
    except (ValueError,OSError,KeyError,TypeError,zipfile.BadZipFile,subprocess.TimeoutExpired) as exc:
        code=str(exc) if isinstance(exc,ValueError) and re.fullmatch('[A-Z_]+',str(exc)) else 'WORKSPACE_DELIVERY_FAILED'
        print(json.dumps({'error':code}),file=__import__('sys').stderr);raise SystemExit(2)
