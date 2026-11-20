"""POSIX immutable runtime-tree staging and activation engineering core.

Sources must be admitted separately. This is not archive extraction, a native
bootstrapper, a signed installer or Windows support. No installed host is probed.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import os
from pathlib import Path
import posixpath
import re
import stat
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config_transaction as cfg
import install_transaction as tx

SCHEMA = 'vf.runtime-tree/v1'
JOURNAL = 'vf.runtime-tree-stage/v1'
POINTER = 'vf.runtime-pointer/v1'
MAX_FILE = 256 * 1024 * 1024
MAX_TOTAL = 2 * 1024 * 1024 * 1024
MAX_ENTRIES = 20000
CHUNK = 1024 * 1024
require = tx.require


def digest_ok(value):
    return type(value) is str and re.fullmatch('[0-9a-f]{64}', value)


def relative(name):
    require(type(name) is str and name and name.isascii() and len(name)<=1024,'INVALID_RUNTIME_PATH')
    reserved={'con','prn','aux','nul'}|{f'{p}{i}' for p in ('com','lpt') for i in range(1,10)}
    require(all(0<len(part)<=255 and re.fullmatch('[A-Za-z0-9_.+ -]+',part)
                and part not in ('.','..') and part==part.strip() and not part.endswith('.')
                and part.split('.')[0].lower() not in reserved for part in name.split('/')), 'INVALID_RUNTIME_PATH')
    return name


def validate_manifest(manifest):
    require(type(manifest) is dict and set(manifest) == {'schema','release','source_sha256','entries'}
            and manifest['schema'] == SCHEMA and digest_ok(manifest['source_sha256'])
            and type(manifest['release']) is str and re.fullmatch('[a-z0-9][a-z0-9.-]{0,63}', manifest['release']), 'INVALID_TREE_MANIFEST')
    entries = manifest['entries']
    require(type(entries) is dict and 0 < len(entries) <= MAX_ENTRIES, 'INVALID_TREE_ENTRIES')
    folded = set()
    total = 0
    for name, item in entries.items():
        relative(name)
        require(name.casefold() not in folded, 'TREE_PATH_COLLISION')
        folded.add(name.casefold())
        require(type(item) is dict and item.get('type') in ('file','symlink'), 'INVALID_TREE_ENTRY')
        if item['type'] == 'file':
            require(set(item) == {'type','size','sha256','mode'} and type(item['size']) is int
                    and 0 <= item['size'] <= MAX_FILE and digest_ok(item['sha256'])
                    and type(item['mode']) is int and item['mode'] in (0o644,0o755), 'INVALID_TREE_FILE')
            total += item['size']
        else:
            require(set(item) == {'type','target'} and type(item['target']) is str
                    and item['target'] and not item['target'].startswith('/') and '\\' not in item['target'], 'INVALID_TREE_LINK')
            # Only explicit relative POSIX components; resolve '..' below.
            require(all(p in ('.','..') or (p and re.fullmatch('[A-Za-z0-9_.+ -]+',p) and p==p.strip())
                        for p in item['target'].split('/')), 'INVALID_TREE_LINK')
    require(total <= MAX_TOTAL, 'TREE_TOO_LARGE')
    require(len(tx.encode(manifest))<=tx.MAX_FILE-1024,'TREE_MANIFEST_TOO_LARGE')
    for name in entries:
        require(not any('/'.join(name.split('/')[:i]).casefold() in folded
                        for i in range(1,len(name.split('/')))), 'TREE_PATH_PREFIX_COLLISION')
    # Component case aliases for implicit directories are also forbidden.
    prefixes = {}
    for name in entries:
        for i in range(1,len(name.split('/'))+1):
            prefix='/'.join(name.split('/')[:i]);key=prefix.casefold()
            require(key not in prefixes or prefixes[key] == prefix, 'TREE_DIRECTORY_ALIAS')
            prefixes[key]=prefix
    for name, entry in entries.items():
        if entry['type'] != 'symlink':
            continue
        visited={name}; current=name
        while entries[current]['type'] == 'symlink':
            components=posixpath.dirname(current).split('/') if '/' in current else []
            target_parts=entries[current]['target'].split('/')
            for index, part in enumerate(target_parts):
                if part == '..':
                    require(bool(components),'TREE_LINK_ESCAPE_CYCLE_OR_MISSING');components.pop()
                elif part != '.':
                    components.append(part)
                if index < len(target_parts)-1:
                    require('/'.join(components) not in entries,'TREE_LINK_PARENT_NOT_DIRECTORY')
            destination='/'.join(components)
            require(destination in entries and destination not in visited, 'TREE_LINK_ESCAPE_CYCLE_OR_MISSING')
            visited.add(destination);require(len(visited)<=64,'TREE_LINK_CHAIN_TOO_DEEP');current=destination
    return sorted(entries, key=lambda n: (entries[n]['type'] != 'file',n))


def _regular(path, mode=None):
    tx.no_links(path)
    info=path.stat()
    require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_uid == os.getuid()
            and not getattr(info,'st_flags',0) and (mode is None or stat.S_IMODE(info.st_mode) == mode), 'RUNTIME_FILE_CONFLICT')
    return info


def _directory(path):
    # Root ACL/provenance is checked by ConfigFile's POSIX guard. Descendants are
    # newly owned runtime content, never imported filesystem metadata.
    tx.no_links(path)
    try:
        path.mkdir(mode=0o700);tx.sync_dir(path.parent)
    except FileExistsError:
        pass
    info=path.stat()
    require(stat.S_ISDIR(info.st_mode) and info.st_uid==os.getuid()
            and stat.S_IMODE(info.st_mode)==0o700 and not getattr(info,'st_flags',0),'RUNTIME_DIRECTORY_CONFLICT')


def file_hash(path, limit=MAX_FILE):
    info=_regular(path)
    require(info.st_size <= limit, 'RUNTIME_FILE_TOO_LARGE')
    fd=os.open(path, os.O_RDONLY | getattr(os,'O_NOFOLLOW',0))
    result=hashlib.sha256();count=0
    with os.fdopen(fd,'rb') as stream:
        current=os.fstat(stream.fileno())
        require((current.st_dev,current.st_ino)==(info.st_dev,info.st_ino), 'SOURCE_CHANGED')
        while chunk := stream.read(CHUNK):
            count += len(chunk);require(count <= limit,'RUNTIME_FILE_TOO_LARGE');result.update(chunk)
    require(count == info.st_size,'SOURCE_CHANGED')
    return result.hexdigest(),count


class RuntimeStore:
    def __init__(self, root):
        require(os.name == 'posix', 'WINDOWS_PRIVATE_STORAGE_UNVERIFIED')
        self.root=Path(root).absolute()
        require(self.root.is_dir(),'ROOT_REQUIRED')
        cfg.private_directory(self.root)
        self.versions=self.root/'versions';cfg.private_directory(self.versions)
        self.activation=self.root/'activation';cfg.private_directory(self.activation)
        self.pointer=tx.ManagedFiles(self.activation)

    @contextmanager
    def locked(self):
        import fcntl
        cfg.private_directory(self.root);cfg.private_directory(self.versions)
        path=self.root/'scope.lock';cfg.private_file(path)
        fd=os.open(path,os.O_RDWR|os.O_CREAT|getattr(os,'O_NOFOLLOW',0),0o600)
        with os.fdopen(fd,'r+b') as stream:
            cfg.private_file(path)
            require(path.stat().st_ino==os.fstat(stream.fileno()).st_ino,'LOCK_CHANGED')
            try:fcntl.flock(stream.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:raise tx.Conflict('SCOPE_BUSY') from None
            try:yield
            finally:fcntl.flock(stream.fileno(),fcntl.LOCK_UN)

    def _read(self,path):
        cfg.private_file(path)
        return tx.read(path)

    def _write(self,path,value):
        _directory(path.parent)
        if path.exists():_regular(path,0o600)
        tx.atomic(path,tx.encode(value),mode=0o600)

    def _folder(self,digest):
        require(digest_ok(digest),'INVALID_TREE_DIGEST')
        path=self.versions/digest;tx.no_links(path)
        return path

    def _manifest(self,digest):
        folder=self._folder(digest);require(folder.is_dir(),'RUNTIME_VERSION_MISSING');cfg.private_directory(folder)
        require({p.name for p in folder.iterdir()} <= {'manifest.json','journal.json','tree'},'UNKNOWN_RUNTIME_METADATA')
        raw=self._read(folder/'manifest.json')
        require(raw is not None,'TREE_MANIFEST_CHANGED')
        intent=tx.decode(raw)
        require(type(intent) is dict and set(intent)=={'transaction','manifest'}
                and type(intent['transaction']) is str and re.fullmatch('[0-9a-f]{32}',intent['transaction']), 'INVALID_RUNTIME_INTENT')
        manifest=intent['manifest'];validate_manifest(manifest)
        require(tx.sha(tx.encode(manifest))==digest,'TREE_MANIFEST_CHANGED')
        return manifest

    def _journal(self,digest,manifest):
        raw=self._read(self._folder(digest)/'journal.json')
        intent=tx.decode(self._read(self._folder(digest)/'manifest.json'))
        j=tx.decode(raw) if raw is not None else {'schema':JOURNAL,'id':intent['transaction'],
            'tree_sha256':digest,'state':'staging','next':0,'writing':None}
        order=validate_manifest(manifest)
        require(type(j) is dict and set(j)=={'schema','id','tree_sha256','state','next','writing'}
                and j['schema']==JOURNAL and j['tree_sha256']==digest and j['id']==intent['transaction']
                and type(j['id']) is str and re.fullmatch('[0-9a-f]{32}',j['id'])
                and j['state'] in ('staging','ready') and type(j['next']) is int and 0<=j['next']<=len(order), 'INVALID_STAGING_JOURNAL')
        require(j['writing'] is None or (j['next']<len(order) and j['writing']==order[j['next']]),'INVALID_STAGING_JOURNAL')
        require(j['state']!='ready' or (j['next']==len(order) and j['writing'] is None),'INVALID_STAGING_JOURNAL')
        return j

    def _source(self,path,entry):
        path=Path(path).absolute()
        require(file_hash(path)==(entry['sha256'],entry['size']),'RUNTIME_SOURCE_CHANGED')
        return path

    def _prefix(self,path,source,entry):
        info=_regular(path,entry['mode']);require(info.st_size<=entry['size'],'RUNTIME_PARTIAL_CONFLICT')
        with path.open('rb') as existing, source.open('rb') as original:
            while chunk := existing.read(CHUNK):
                require(original.read(len(chunk))==chunk,'RUNTIME_PARTIAL_CONFLICT')
        return info.st_size

    def _audit(self,digest,manifest,journal,sources=None):
        tree=self._folder(digest)/'tree';require(tree.is_dir(),'RUNTIME_TREE_MISSING');_directory(tree)
        entries=manifest['entries'];order=validate_manifest(manifest)
        completed=set(order[:journal['next']]);writing=journal['writing']
        dirs=set()
        for name in entries:
            dirs.update('/'.join(name.split('/')[:i]) for i in range(1,len(name.split('/'))))
        seen=set()
        # os.walk does not descend directory symlinks; reject them as entries.
        for directory,subdirs,files in os.walk(tree,followlinks=False):
            parent=Path(directory);_directory(parent)
            for name in subdirs+files:
                path=parent/name;relative=path.relative_to(tree).as_posix();info=path.lstat()
                if stat.S_ISDIR(info.st_mode):
                    require(relative in dirs,'UNKNOWN_RUNTIME_DIRECTORY')
                    continue
                require(relative in entries and (relative in completed or relative==writing),'UNKNOWN_RUNTIME_FILE')
                entry=entries[relative];seen.add(relative)
                if entry['type']=='symlink':
                    require(stat.S_ISLNK(info.st_mode) and info.st_uid==os.getuid() and os.readlink(path)==entry['target'], 'RUNTIME_LINK_CONFLICT')
                else:
                    _regular(path,entry['mode'])
                    if relative==writing and sources is not None:
                        self._prefix(path,sources[relative],entry)
                    else:
                        require(file_hash(path)==(entry['sha256'],entry['size']),'RUNTIME_FILE_CONFLICT')
        require(completed<=seen,'RUNTIME_FILE_MISSING')
        if journal['state']=='ready':require(seen==set(entries),'RUNTIME_FILE_MISSING')

    def stage(self,manifest,sources,*,expected_digest,transaction_id,checkpoint=lambda _:None):
        order=validate_manifest(manifest);raw=tx.encode(manifest);digest=tx.sha(raw)
        require(digest==expected_digest,'TREE_DIGEST_MISMATCH')
        require(type(transaction_id) is str and re.fullmatch('[0-9a-f]{32}',transaction_id),'INVALID_TRANSACTION')
        require(type(sources) is dict and set(sources)=={n for n in order if manifest['entries'][n]['type']=='file'},'RUNTIME_SOURCE_SET')
        with self.locked():
            folder=self._folder(digest)
            if folder.exists() and any(folder.iterdir()):
                require(self._manifest(digest)==manifest,'TREE_MANIFEST_CHANGED')
                journal=self._journal(digest,manifest)
                if journal['state']=='ready':
                    self._audit(digest,manifest,journal)
                    return self._receipt(digest,manifest,'already_ready',journal['id'])
                require(journal['id']==transaction_id,'STAGING_OWNED_BY_OTHER_TRANSACTION')
            else:
                cfg.private_directory(folder)
                self._write(folder/'manifest.json',{'transaction':transaction_id,'manifest':manifest})
                journal={'schema':JOURNAL,'id':transaction_id,'tree_sha256':digest,'state':'staging','next':0,'writing':None}
                self._write(folder/'journal.json',journal)
                _directory(folder/'tree')
                checkpoint('prepared')
            bound={n:self._source(p,manifest['entries'][n]) for n,p in sources.items()}
            if not (folder/'tree').exists() and journal['next']==0 and journal['writing'] is None:
                _directory(folder/'tree')
            self._audit(digest,manifest,journal,bound)
            for index in range(journal['next'],len(order)):
                name=order[index];entry=manifest['entries'][name];path=folder/'tree'/name
                # Create only planned parent directories under our private tree.
                for parent in reversed(path.parents):
                    if parent==folder/'tree' or folder/'tree' in parent.parents:_directory(parent)
                journal['writing']=name;self._write(folder/'journal.json',journal)
                checkpoint('writing:'+name)
                if entry['type']=='symlink':
                    if not path.is_symlink():
                        require(not path.exists(),'RUNTIME_LINK_CONFLICT');os.symlink(entry['target'],path);tx.sync_dir(path.parent)
                    require(os.readlink(path)==entry['target'],'RUNTIME_LINK_CONFLICT')
                else:
                    offset=self._prefix(path,bound[name],entry) if path.exists() else 0
                    flags=os.O_WRONLY|os.O_APPEND|getattr(os,'O_NOFOLLOW',0)
                    flags |= 0 if path.exists() else os.O_CREAT|os.O_EXCL
                    fd=os.open(path,flags,entry['mode'])
                    with os.fdopen(fd,'ab') as output,bound[name].open('rb') as source:
                        os.fchmod(output.fileno(),entry['mode']);source.seek(offset)
                        while chunk := source.read(CHUNK):
                            require(output.tell()+len(chunk)<=entry['size'],'RUNTIME_SOURCE_CHANGED')
                            output.write(chunk);output.flush();checkpoint('chunk:'+name)
                        os.fsync(output.fileno())
                    tx.sync_dir(path.parent)
                    require(file_hash(path)==(entry['sha256'],entry['size']),'RUNTIME_SOURCE_CHANGED')
                checkpoint('written:'+name)
                journal['next']=index+1;journal['writing']=None;self._write(folder/'journal.json',journal)
            ready={**journal,'state':'ready'}
            self._audit(digest,manifest,ready)
            self._write(folder/'journal.json',ready);checkpoint('ready')
            return self._receipt(digest,manifest,'tree_ready',journal['id'])

    def _receipt(self,digest,manifest,status,transaction):
        return {'status':status,'tree_sha256':digest,'release':manifest['release'],'transaction':transaction,
                'host_verified':False,'native_installer':False}

    def _verify(self,digest):
        manifest=self._manifest(digest);j=self._journal(digest,manifest)
        require(j['state']=='ready','RUNTIME_NOT_READY');self._audit(digest,manifest,j)
        return manifest

    def verify(self,digest):
        with self.locked():
            manifest=self._verify(digest)
            return {'status':'tree_verified','tree_sha256':digest,'release':manifest['release'],'host_verified':False}

    def activate(self,digest,*,expected_revision,transaction_id=None,checkpoint=lambda _:None):
        with self.locked():
            manifest=self._verify(digest)
            pointer={'schema':POINTER,'tree_sha256':digest,'release':manifest['release']}
            return self.pointer.apply(manifest['release'],{'runtime.json':tx.encode(pointer)},expected_revision,
                                      transaction_id=transaction_id,checkpoint=checkpoint)

    def _check_previous_pointer(self,journal):
        require(journal is not None,'ACTIVATION_TRANSACTION_MISSING')
        if 'runtime.json' in journal['before']['files']:
            raw=tx.read(self.pointer.meta/journal['id']/'before/runtime.json')
            require(raw is not None and tx.sha(raw)==journal['before']['files']['runtime.json'],'ACTIVATION_BACKUP_CHANGED')
            value=tx.decode(raw)
            require(type(value) is dict and set(value)=={'schema','tree_sha256','release'} and value['schema']==POINTER,'INVALID_RUNTIME_POINTER')
            manifest=self._verify(value['tree_sha256'])
            require(manifest['release']==value['release'],'RUNTIME_RELEASE_CHANGED')

    def deactivate(self,*,expected_revision,transaction_id=None,checkpoint=lambda _:None):
        with self.locked():
            return self.pointer.remove(expected_revision, transaction_id=transaction_id, checkpoint=checkpoint)

    def rollback_activation(self,transaction_id,*,expected_revision,checkpoint=lambda _:None):
        with self.locked():
            journal=self.pointer._pending()
            require(journal is not None and journal['id']==transaction_id,'TRANSACTION_CHANGED')
            self._check_previous_pointer(journal)
            return self.pointer.rollback(transaction_id,expected_revision,checkpoint)

    def recover_activation(self,transaction_id,checkpoint=lambda _:None):
        with self.locked():
            journal=self.pointer._pending()
            require(journal is not None and journal['id']==transaction_id,'TRANSACTION_CHANGED')
            if journal['state'] not in ('committed','recovered') or self.pointer._compensation(journal).state()=='requested':
                self._check_previous_pointer(journal)
            return self.pointer.recover(checkpoint,transaction_id=transaction_id)
