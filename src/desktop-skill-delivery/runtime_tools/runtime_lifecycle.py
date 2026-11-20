"""Closed, fresh POSIX engineering scopes: references, process leases and GC.

No host discovery, public admission, Windows support or user-root adoption.
All consumers in this isolated scope must use this facade. Same-user malicious
filesystem/syscall races are outside the underlying transaction threat model.
"""
from __future__ import annotations

from contextlib import ExitStack, contextmanager
import os
from pathlib import Path
import re
import stat
import subprocess
import uuid

from . import runtime_store as rt
from . import native_lifecycle as nl
import install_operation as operation

tx = rt.tx
cfg = rt.cfg
require = tx.require
SEAL = 'vf.runtime-lifecycle-scope/v1'
GC = 'vf.runtime-lifecycle-gc/v1'


def _dir(path, mode=0o700):
    tx.no_links(path)
    info = path.stat()
    require(stat.S_ISDIR(info.st_mode) and info.st_uid == os.getuid()
            and stat.S_IMODE(info.st_mode) == mode and not getattr(info, 'st_flags', 0),
            'LIFECYCLE_DIRECTORY_CONFLICT')


class RuntimeLifecycle:
    def __init__(self, store, scopes):
        require(type(store) is rt.RuntimeStore and os.name == 'posix', 'ISOLATED_POSIX_SCOPE_REQUIRED')
        require(type(scopes) in (list, tuple) and 0 < len(scopes) <= 16, 'INVALID_LIFECYCLE_SCOPES')
        self.store = store
        self.scopes = {}
        native_sources = set()
        for scope in scopes:
            require((type(scope) is operation.InstallOperation and scope.schema == operation.RUNTIME_SCHEMA
                     or nl.is_native(scope)) and os.path.samefile(scope.runtime_store.root, store.root),
                    'INVALID_LIFECYCLE_SCOPE')
            if nl.is_native(scope):
                source = scope.native.journal.directory
                require(source not in native_sources, 'DUPLICATE_NATIVE_SOURCE')
                native_sources.add(source)
            key = tx.sha(tx.encode(scope.bindings))
            require(key not in self.scopes, 'DUPLICATE_LIFECYCLE_SCOPE')
            self.scopes[key] = scope
        self.meta = store.root / 'lifecycle'
        cfg.private_directory(self.meta)
        self.trash = self.meta / 'quarantine'
        cfg.private_directory(self.trash)
        self.expected_seal = {'schema': SEAL, 'store': tx.sha(os.fsencode(store.root.resolve())),
                              'scopes': sorted(self.scopes)}

    def _read(self, path):
        cfg.private_file(path)
        return tx.read(path)

    def _save(self, path, value):
        cfg.private_file(path)
        tx.atomic(path, tx.encode(value), mode=0o600)

    @contextmanager
    def _gate(self, shared=False):
        import fcntl
        _dir(self.meta)
        path = self.meta / 'activity.lock'
        cfg.private_file(path)
        fd = os.open(path, os.O_RDWR | os.O_CREAT | getattr(os, 'O_NOFOLLOW', 0), 0o600)
        try:
            cfg.private_file(path)
            require(os.fstat(fd).st_ino == path.stat().st_ino, 'LIFECYCLE_LOCK_CHANGED')
            try:
                fcntl.flock(fd, (fcntl.LOCK_SH if shared else fcntl.LOCK_EX) | fcntl.LOCK_NB)
            except BlockingIOError:
                raise tx.Conflict('RUNTIME_ACTIVITY_BUSY') from None
            yield fd
        finally:
            # Close, do not LOCK_UN: a child inheriting this open-file description
            # must retain the lease after an unexpected parent exit.
            os.close(fd)

    @contextmanager
    def _resources(self):
        with ExitStack() as stack:
            for key in sorted(self.scopes):
                scope = self.scopes[key]
                stack.enter_context(scope.locked())
                if nl.is_native(scope):
                    nl.guard(scope)
                    stack.enter_context(scope.native.locked())
                    stack.enter_context(scope.native.journal.locked())
                stack.enter_context(scope.skills.locked())
                if not nl.is_native(scope):
                    stack.enter_context(scope.config.locked())
            stack.enter_context(self.store.locked())
            stack.enter_context(self.store.pointer.locked())
            yield

    def _seal(self):
        _dir(self.store.root)
        _dir(self.meta)
        _dir(self.trash)
        raw = self._read(self.meta / 'seal.json')
        require(raw is not None, 'LIFECYCLE_SCOPE_CHANGED')
        value = tx.decode(raw)
        require(type(value) is dict and set(value) == {*self.expected_seal, 'initial_scopes'}
                and all(value[k] == v for k, v in self.expected_seal.items())
                and type(value['initial_scopes']) is dict
                and set(value['initial_scopes']) == set(self.scopes), 'LIFECYCLE_SCOPE_CHANGED')
        for key, baseline in value['initial_scopes'].items():
            if nl.is_native(self.scopes[key]):
                nl.baseline_valid(baseline)
                continue
            require(type(baseline) is dict and set(baseline) == {'skills_revision', 'config_revision', 'config_file'}
                    and rt.digest_ok(baseline['skills_revision']) and rt.digest_ok(baseline['config_revision']),
                    'INVALID_INITIAL_SCOPE')
            self.scopes[key].config._record_valid(baseline['config_file'])
        return value

    def initialize(self):
        with self._gate(), self._resources():
            if self._read(self.meta / 'seal.json') is not None:
                self._seal()
                return {'status': 'scope_verified', 'host_verified': False}
            require(not any(self.store.versions.iterdir())
                    and self.store.pointer.snapshot()[0] == tx.empty_ledger()
                    and self.store.pointer._pending() is None, 'FRESH_RUNTIME_SCOPE_REQUIRED')
            require({p.name for p in self.store.activation.iterdir()} == {tx.META},
                    'FRESH_RUNTIME_SCOPE_REQUIRED')
            require(not any(p.name != 'scope.lock' for p in self.store.pointer.meta.iterdir()),
                    'FRESH_RUNTIME_SCOPE_REQUIRED')
            for scope in self.scopes.values():
                require(scope._active() is None and not any(p.name != 'scope.lock' for p in scope.meta.iterdir()),
                        'FRESH_OPERATION_SCOPE_REQUIRED')
            require(not any(self.trash.iterdir()) and self._read(self.meta / 'gc.json') is None,
                    'FRESH_RUNTIME_SCOPE_REQUIRED')
            nl.sources(self.scopes.values())
            initial = {key: nl.initial(scope) if nl.is_native(scope) else {
                'skills_revision': scope.skills.snapshot()[1],
                'config_revision': scope.config.snapshot()[1], 'config_file': scope.config._file()[1]}
                for key, scope in self.scopes.items()}
            self._save(self.meta / 'seal.json', {**self.expected_seal, 'initial_scopes': initial})
            return {'status': 'scope_initialized', 'host_verified': False}

    def _pending_gc(self):
        _dir(self.trash)
        raw = self._read(self.meta / 'gc.json')
        if raw is None:
            require(not any(self.trash.iterdir()), 'UNKNOWN_QUARANTINE_CONTENT')
            return None
        progress = tx.decode(raw)
        require(type(progress) is dict and set(progress) == {'schema', 'id', 'record_sha256', 'state', 'next'}
                and progress['schema'] == GC and type(progress['id']) is str
                and re.fullmatch('[0-9a-f]{32}', progress['id']) and rt.digest_ok(progress['record_sha256']),
                'INVALID_GC_RECORD')
        record_path = self.meta / 'records' / (progress['id'] + '.json')
        raw_record = self._read(record_path)
        require(raw_record is not None and tx.sha(raw_record) == progress['record_sha256'], 'GC_RECORD_CHANGED')
        value = tx.decode(raw_record)
        require(type(value) is dict and set(value) == {'schema', 'id', 'digest', 'intent', 'stage'}
                and value['schema'] == progress['schema'] and value['id'] == progress['id'], 'INVALID_GC_RECORD')
        value.update({key: progress[key] for key in ('state', 'next')})
        require(type(value) is dict and set(value) == {'schema', 'id', 'digest', 'intent', 'stage', 'state', 'next'}
                and value['schema'] == GC and type(value['id']) is str
                and re.fullmatch('[0-9a-f]{32}', value['id']) and rt.digest_ok(value['digest'])
                and value['state'] in ('quarantining', 'deleting', 'done')
                and type(value['next']) is int, 'INVALID_GC_RECORD')
        intent = value['intent']
        require(type(intent) is dict and set(intent) == {'transaction', 'manifest'}
                and type(intent['transaction']) is str and re.fullmatch('[0-9a-f]{32}', intent['transaction']),
                'INVALID_GC_RECORD')
        order = rt.validate_manifest(intent['manifest'])
        require(tx.sha(tx.encode(intent['manifest'])) == value['digest'], 'INVALID_GC_RECORD')
        require(value['stage'] == {'schema': rt.JOURNAL, 'id': intent['transaction'],
                'tree_sha256': value['digest'], 'state': 'ready', 'next': len(order), 'writing': None},
                'INVALID_GC_RECORD')
        require(0 <= value['next'] <= len(self._items(value))
                and (value['state'] != 'quarantining' or value['next'] == 0)
                and (value['state'] != 'done' or value['next'] == len(self._items(value))), 'INVALID_GC_PROGRESS')
        expected = set() if value['state'] == 'done' else {value['id']}
        require({p.name for p in self.trash.iterdir()} <= expected, 'UNKNOWN_QUARANTINE_CONTENT')
        if value['state'] == 'done':
            require(not any(self.trash.iterdir()), 'COMPLETED_GC_REAPPEARED')
        return {**value, 'record_sha256': progress['record_sha256']}

    def _progress(self, record):
        return {key: record[key] for key in ('schema', 'id', 'record_sha256', 'state', 'next')}

    def _save_gc(self, record):
        self._save(self.meta / 'gc.json', self._progress(record))

    def _idle(self):
        self._seal()
        record = self._pending_gc()
        require(record is None or record['state'] == 'done', 'GC_RECOVERY_REQUIRED')

    def stage(self, *args, **kwargs):
        with self._gate():
            self._idle()
            return self.store.stage(*args, **kwargs)

    def apply(self, scope_key, *args, **kwargs):
        require(scope_key in self.scopes, 'UNKNOWN_LIFECYCLE_SCOPE')
        with self._gate():
            self._idle()
            return self.scopes[scope_key].apply(*args, **kwargs)

    def recover_operation(self, scope_key, *args, **kwargs):
        require(scope_key in self.scopes, 'UNKNOWN_LIFECYCLE_SCOPE')
        with self._gate():
            self._idle()
            return self.scopes[scope_key].recover(*args, **kwargs)

    def uninstall(self, scope_key, **kwargs):
        require(scope_key in self.scopes, 'UNKNOWN_LIFECYCLE_SCOPE')
        with self._gate():
            self._idle()
            return self.scopes[scope_key].uninstall(**kwargs)

    def _references(self):
        references, metadata, blockers, children = {}, {}, [], set()
        seal = self._seal()
        nl.sources(self.scopes.values())

        def pointer(raw, reason):
            value = tx.decode(raw)
            require(type(value) is dict and set(value) == {'schema', 'tree_sha256', 'release'}
                    and value['schema'] == rt.POINTER and rt.digest_ok(value['tree_sha256']), 'INVALID_RUNTIME_POINTER')
            manifest = self.store._verify(value['tree_sha256'])
            require(manifest['release'] == value['release'], 'RUNTIME_RELEASE_CHANGED')
            references.setdefault(value['tree_sha256'], []).append(reason)

        for key, scope in sorted(self.scopes.items()):
            if nl.is_native(scope):
                refs, meta, blocked, ids = nl.references(scope, key, seal['initial_scopes'][key], self.store)
                for digest, reasons in refs.items():
                    references.setdefault(digest, []).extend(reasons)
                metadata.update(meta); blockers.extend(blocked); children.update(ids)
                continue
            active = scope._active()
            if active is None:
                baseline = seal['initial_scopes'][key]
                require(scope.skills.snapshot()[1] == baseline['skills_revision']
                        and scope.config.snapshot()[1] == baseline['config_revision']
                        and scope.config._file()[1] == baseline['config_file'], 'INITIAL_SCOPE_CHANGED')
            if active is not None and active['state'] in ('cleaned', 'cleaned_aborted'):
                # The persisted target alone cannot protect a manually edited
                # config that now points at a different tree. Refuse drift.
                for kind in ('skills', 'config'):
                    engine = getattr(scope, kind)
                    child = active[kind]
                    if active['state'] == 'cleaned_aborted':
                        ledger = child['before']
                        descriptor = child['before_file']
                    else:
                        result = child['result']
                        require(type(result) is dict, 'CHILD_RESULT_MISSING')
                        ledger, revision = engine.snapshot()
                        require(revision == result.get('revision'), 'LIFECYCLE_RESOURCE_CHANGED')
                        if kind == 'config':
                            if result.get('status') == 'unchanged':
                                descriptor = child['before_file']
                            else:
                                config_active = engine._active()
                                require(config_active is not None and config_active['id'] == child['id'],
                                        'CHILD_TRANSACTION_CHANGED')
                                descriptor = config_active['after_file']
                    require(engine.snapshot()[0] == ledger, 'LIFECYCLE_RESOURCE_CHANGED')
                    if kind == 'config':
                        require(engine._file()[1] == descriptor, 'LIFECYCLE_RESOURCE_CHANGED')
                    else:
                        engine._check_tree(ledger, child['before']['files'])
            for path in sorted(scope.meta.iterdir()):
                if path.name == 'scope.lock':
                    continue
                raw = scope._read(path)
                require(raw is not None, 'OPERATION_MISSING')
                metadata['scope/' + key + '/' + path.name] = tx.sha(raw)
                if path.name == 'active.json':
                    continue
                require(re.fullmatch('[0-9a-f]{32}\\.json', path.name), 'UNKNOWN_OPERATION_CONTENT')
                j = tx.decode(raw)
                require(type(j) is dict and set(j) == {'schema', 'id', 'bindings', 'state', *scope.kinds}
                        and scope.valid_schema(j) and j['id'] == path.stem
                        and j['bindings'] == scope.bindings and j['state'] in operation.STATES,
                        'INVALID_OPERATION')
                child = j['runtime']
                removing = j['schema'] == operation.REMOVAL_SCHEMA
                require(type(child) is dict and type(child.get('id')) is str
                        and re.fullmatch('[0-9a-f]{32}', child['id']) and
                        ((child.get('tree') is None and 'tree' in child) if removing else rt.digest_ok(child.get('tree'))),
                        'INVALID_CHILD')
                children.add(child['id'])
                if active is None or j['id'] != active['id']:
                    require(j['state'] in ('cleaned', 'cleaned_aborted'), 'ORPHAN_OPERATION_REQUIRES_RECOVERY')
                else:
                    if not removing:
                        self.store._verify(child['tree'])
                        references.setdefault(child['tree'], []).append('scope/' + key)
                    if j['state'] not in ('cleaned', 'cleaned_aborted'):
                        blockers.append('operation/' + key)

        engine = self.store.pointer
        ledger, revision = engine.snapshot()
        engine._check_tree(ledger)
        require(set(ledger['files']) <= {'runtime.json'}, 'UNKNOWN_ACTIVATION_FILE')
        require({p.name for p in engine.root.iterdir()} == {tx.META} | set(ledger['files']),
                'UNKNOWN_ACTIVATION_FILE')
        metadata['activation/revision'] = revision
        if 'runtime.json' in ledger['files']:
            pointer(tx.read(engine.root / 'runtime.json'), 'activation/current')
        pending = engine._pending()
        if pending is not None:
            require(pending['id'] in children, 'UNREGISTERED_ACTIVATION_TRANSACTION')
            metadata['activation/journal'] = tx.sha(tx.encode(pending))
        known = set()
        history = engine.meta / 'history'
        if history.exists():
            _dir(history, mode=0o755)
            for path in sorted(history.iterdir()):
                raw = self._read(path)
                require(re.fullmatch('[0-9a-f]{32}\\.json', path.name) and raw is not None,
                        'UNKNOWN_ACTIVATION_HISTORY')
                j = tx.decode(raw)
                require(type(j) is dict and set(j) == {'schema', 'id', 'state', 'before', 'after'}
                        and j['schema'] == tx.SCHEMA and j['id'] == path.stem and j['id'] in children
                        and j['state'] in ('committed', 'recovered', 'cleaning', 'cleaned'), 'INVALID_HISTORY')
                tx.validate_ledger(j['before']); tx.validate_ledger(j['after'])
                metadata['activation/history/' + path.name] = tx.sha(raw)
                known.add(j['id'])
                if j['state'] != 'cleaned':
                    blockers.append('activation/backup/' + j['id'])
                    for side in ('before', 'after'):
                        require(set(j[side]['files']) <= {'runtime.json'}, 'UNKNOWN_ACTIVATION_FILE')
                        if 'runtime.json' in j[side]['files']:
                            data = tx.read(engine.meta / j['id'] / side / 'runtime.json')
                            require(data is not None and tx.sha(data) == j[side]['files']['runtime.json'],
                                    'ACTIVATION_BACKUP_CHANGED')
                            pointer(data, 'activation/' + side + '/' + j['id'])
        for path in sorted(engine.meta.iterdir()):
            tx.no_links(path)
            if path.name in ('scope.lock', 'ledger.json', 'journal.json', 'history', 'releases'):
                continue
            if re.fullmatch('[0-9a-f]{32}', path.name):
                require(path.name in children and (path.name in known or pending is not None
                        and path.name == pending['id']), 'ORPHAN_ACTIVATION_STORAGE')
                blockers.append('activation/storage/' + path.name)
            elif re.fullmatch('compensation-[0-9a-f]{32}\\.json', path.name):
                require(path.name[13:-5] in children, 'ORPHAN_COMPENSATION')
                metadata['activation/' + path.name] = tx.sha(self._read(path))
            else:
                raise tx.Conflict('UNKNOWN_ACTIVATION_CONTENT')
        return {k: sorted(v) for k, v in sorted(references.items())}, metadata, sorted(set(blockers))

    def _inventory(self):
        refs, metadata, blockers = self._references()
        versions = {}
        for folder in sorted(self.store.versions.iterdir()):
            require(rt.digest_ok(folder.name), 'UNKNOWN_RUNTIME_VERSION')
            manifest = self.store._manifest(folder.name)
            journal = self.store._journal(folder.name, manifest)
            if journal['state'] == 'ready':
                self.store._audit(folder.name, manifest, journal)
            versions[folder.name] = {'state': journal['state'], 'references': refs.get(folder.name, []),
                                    'intent_sha256': tx.sha(self.store._read(folder / 'manifest.json')),
                                    'journal_sha256': tx.sha(tx.encode(journal))}
        value = {'schema': 'vf.runtime-lifecycle-inventory/v1', 'versions': versions,
                 'references': refs, 'metadata': metadata, 'blockers': blockers}
        return {**value, 'revision': tx.sha(tx.encode(value)), 'host_verified': False}

    def inventory(self):
        with self._gate(), self._resources():
            self._idle()
            return self._inventory()

    @contextmanager
    def quiescent_snapshot(self, *, expected_revision):
        """Hold every cooperating resource unchanged for an external binding CAS.

        This verifies an existing seal; it never initializes or repairs a scope.
        The caller may change only its own binding metadata, not these resources.
        """
        require(rt.digest_ok(expected_revision), 'INVALID_LIFECYCLE_REVISION')
        with self._gate(), self._resources():
            self._idle()
            observed = self._inventory()
            require(observed['revision'] == expected_revision, 'LIFECYCLE_REVISION_CHANGED')
            require(not observed['blockers'], 'RUNTIME_RECOVERY_REQUIRED')
            require(all(item['state'] == 'ready' for item in observed['versions'].values()),
                    'RUNTIME_STAGING_INCOMPLETE')
            yield observed

    @contextmanager
    def lease(self, digest):
        with self._gate(shared=True) as fd:
            self._idle()
            with self._resources():
                refs, _, blockers = self._references()
                require(not blockers, 'RUNTIME_RECOVERY_REQUIRED')
                require(digest in refs, 'UNREFERENCED_RUNTIME_EXECUTION')
                self.store._verify(digest)
            yield fd

    def run(self, digest, executable, arguments=(), **kwargs):
        rt.relative(executable)
        require(type(arguments) in (list, tuple) and all(type(a) is str and '\x00' not in a for a in arguments),
                'INVALID_RUNTIME_ARGUMENTS')
        require(not set(kwargs) & {'shell', 'executable', 'pass_fds', 'close_fds', 'preexec_fn'},
                'UNSAFE_RUNTIME_EXECUTION_OPTIONS')
        with self.lease(digest) as fd:
            manifest = self.store._manifest(digest)
            item = manifest['entries'].get(executable)
            require(item is not None and item['type'] == 'file' and item['mode'] == 0o755,
                    'RUNTIME_EXECUTABLE_REQUIRED')
            return subprocess.run([str(self.store._folder(digest) / 'tree' / executable), *arguments],
                                  pass_fds=(fd,), **kwargs)

    def _items(self, record):
        entries = record['intent']['manifest']['entries']
        items = {'tree/' + name: item for name, item in entries.items()}
        dirs = {'tree', ''}
        for name in entries:
            dirs.update('tree/' + '/'.join(name.split('/')[:i]) for i in range(1, len(name.split('/'))))
        for name, value in (('manifest.json', record['intent']), ('journal.json', record['stage'])):
            raw = tx.encode(value)
            items[name] = {'type': 'file', 'sha256': tx.sha(raw), 'size': len(raw), 'mode': 0o600}
        return [(n, items[n]) for n in sorted(items)] + [(n, {'type': 'directory'})
            for n in sorted(dirs, key=lambda n: (-len(n.split('/')) if n else 0, n))]

    def _check_item(self, path, item):
        if item['type'] == 'directory':
            _dir(path)
        elif item['type'] == 'symlink':
            info = path.lstat()
            require(stat.S_ISLNK(info.st_mode) and info.st_uid == os.getuid() and info.st_nlink == 1
                    and not getattr(info, 'st_flags', 0)
                    and os.readlink(path) == item['target'], 'GC_LINK_CONFLICT')
        else:
            rt._regular(path, item['mode'])
            require(rt.file_hash(path) == (item['sha256'], item['size']), 'GC_FILE_CONFLICT')

    def _audit_quarantine(self, record):
        root = self.trash / record['id']
        items = self._items(record)
        expected = {name: item for name, item in items}
        seen = set()
        if root.exists() or root.is_symlink():
            self._check_item(root, expected[''])
            seen.add('')
            for directory, subdirs, files in os.walk(root, followlinks=False):
                for name in subdirs + files:
                    path = Path(directory) / name
                    relative = path.relative_to(root).as_posix()
                    require(relative in expected, 'UNKNOWN_GC_CONTENT')
                    self._check_item(path, expected[relative])
                    seen.add(relative)
        prior = {n for n, _ in items[:record['next']]}
        later = {n for n, _ in items[record['next'] + 1:]}
        allowed = {n for n, _ in items[record['next']:]}
        require(not seen & prior and later <= seen and seen <= allowed, 'GC_PROGRESS_CONFLICT')

    def collect(self, digest, *, expected_revision, checkpoint=lambda _: None):
        require(rt.digest_ok(digest), 'INVALID_TREE_DIGEST')
        with self._gate(), self._resources():
            self._idle()
            inventory = self._inventory()
            require(inventory['revision'] == expected_revision, 'LIFECYCLE_REVISION_CHANGED')
            require(not inventory['blockers'], 'RUNTIME_RECOVERY_REQUIRED')
            require(digest in inventory['versions'] and inventory['versions'][digest]['state'] == 'ready'
                    and not inventory['versions'][digest]['references'], 'RUNTIME_VERSION_PROTECTED')
            folder = self.store._folder(digest)
            record = {'schema': GC, 'id': uuid.uuid4().hex, 'digest': digest,
                      'intent': tx.decode(self.store._read(folder / 'manifest.json')),
                      'stage': tx.decode(self.store._read(folder / 'journal.json')),
                      'state': 'quarantining', 'next': 0}
            previous = self._pending_gc()
            if previous is not None:
                history = self.meta / 'history'
                cfg.private_directory(history)
                path = history / (previous['id'] + '.json')
                raw = self._read(path)
                require(raw is None or raw == tx.encode(self._progress(previous)), 'GC_HISTORY_CHANGED')
                if raw is None:
                    self._save(path, self._progress(previous))
            records = self.meta / 'records'
            cfg.private_directory(records)
            static = {key: record[key] for key in ('schema', 'id', 'digest', 'intent', 'stage')}
            record['record_sha256'] = tx.sha(tx.encode(static))
            record_path = records / (record['id'] + '.json')
            require(not record_path.exists(), 'GC_RECORD_ALREADY_EXISTS')
            self._save(record_path, static)
            self._save_gc(record)
            checkpoint('recorded')
            return self._resume(record, checkpoint)

    def recover_gc(self, checkpoint=lambda _: None):
        with self._gate(), self._resources():
            self._seal()
            record = self._pending_gc()
            if record is None or record['state'] == 'done':
                return {'status': 'nothing_pending', 'host_verified': False}
            return self._resume(record, checkpoint)

    def _resume(self, record, checkpoint):
        refs, _, blockers = self._references()
        require(record['digest'] not in refs and not blockers, 'RUNTIME_VERSION_PROTECTED')
        source = self.store._folder(record['digest'])
        target = self.trash / record['id']
        if record['state'] == 'quarantining':
            require(source.exists() != target.exists(), 'GC_QUARANTINE_CONFLICT')
            if source.exists():
                self.store._verify(record['digest'])
                require(self.store._read(source / 'manifest.json') == tx.encode(record['intent'])
                        and self.store._read(source / 'journal.json') == tx.encode(record['stage']),
                        'GC_SOURCE_CHANGED')
                os.rename(source, target)
                tx.sync_dir(source.parent); tx.sync_dir(target.parent)
            checkpoint('quarantined')
            record['state'] = 'deleting'
            self._save_gc(record)
        require(not source.exists() and not source.is_symlink(), 'GC_VERSION_REAPPEARED')
        self._audit_quarantine(record)
        items = self._items(record)
        while record['next'] < len(items):
            name, item = items[record['next']]
            path = target / name if name else target
            if path.exists() or path.is_symlink():
                self._check_item(path, item)
                if item['type'] == 'directory':
                    path.rmdir()
                else:
                    path.unlink()
                tx.sync_dir(path.parent)
            checkpoint('removed:' + name)
            record['next'] += 1
            self._save_gc(record)
            checkpoint('progress:' + name)
        require(not target.exists(), 'GC_STORAGE_REAPPEARED')
        record['state'] = 'done'
        self._save_gc(record)
        return {'status': 'runtime_collected', 'tree_sha256': record['digest'],
                'maintenance': record['id'], 'host_verified': False, 'windows_execution': False}
