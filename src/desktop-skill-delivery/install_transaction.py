"""Local managed-file transactions, not a release validator or host installer.

Call only after the installer admission layer verifies source, withdrawal,
compatibility and activity. No network, secrets, host discovery or desktop
support assertions are implemented here. The caller supplies a complete desired
file set and the previously observed ledger revision (compare-and-swap).

This core handles declared 0644/0755 modes and explicit terminal staging cleanup.
Host configuration merging, admission, power-loss verification and native
Windows verification are still required before an actual installer uses it.
It is deliberately absent from the engineering handoff's installable surface.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import uuid

import transaction_compensation as compensation

MAX_FILE = 8 * 1024 * 1024
MAX_TOTAL = 64 * 1024 * 1024
MAX_FILES = 2048
META = '.vf-skill-transaction'
SCHEMA = 'vf.skill-files.transaction/v2'


class Conflict(ValueError):
    pass


def require(test, reason):
    if not test:
        raise Conflict(reason)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def encode(value):
    return (json.dumps(value, sort_keys=True, ensure_ascii=False, indent=2) + '\n').encode()


def decode(raw):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, 'DUPLICATE_JSON_KEY')
            result[key] = value
        return result
    return json.loads(raw, object_pairs_hook=unique)


def relative(name):
    require(type(name) is str and name and name.isascii(), 'INVALID_PATH')
    reserved = {'con', 'prn', 'aux', 'nul'} | {f'{x}{i}' for x in ('com', 'lpt') for i in range(1, 10)}
    require(all(re.fullmatch('[A-Za-z0-9_.-]+', p) and p not in ('.', '..', META)
                and not p.endswith('.') and p.split('.')[0].lower() not in reserved
                for p in name.split('/')), 'INVALID_PATH')
    return name


def no_links(path):
    for p in (path, *path.absolute().parents):
        require(not p.is_symlink(), 'SYMLINK_REJECTED')


def read(path):
    no_links(path)
    if not path.exists():
        return None
    info = path.stat()
    require(stat.S_ISREG(info.st_mode) and info.st_size <= MAX_FILE, 'FILE_TYPE_OR_SIZE')
    fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
    with os.fdopen(fd, 'rb') as stream:
        now = os.fstat(stream.fileno())
        require((info.st_dev, info.st_ino) == (now.st_dev, now.st_ino), 'FILE_CHANGED')
        data = stream.read(MAX_FILE + 1)
    require(len(data) == info.st_size and len(data) <= MAX_FILE, 'FILE_CHANGED')
    return data


def sync_dir(path):
    if os.name != 'nt':
        fd = os.open(path, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def mkdirs(path, mode=0o755):
    no_links(path)
    missing = []
    parent = path
    while not parent.exists():
        missing.append(parent)
        parent = parent.parent
    require(parent.is_dir(), 'DIRECTORY_REQUIRED')
    for directory in reversed(missing):
        no_links(directory)
        directory.mkdir(mode=mode)
        sync_dir(directory.parent)


def atomic(path, data, staging=None, mode=0o600):
    no_links(path)
    mkdirs(path.parent)
    temporary_root = path.parent if staging is None else staging
    no_links(temporary_root)
    mkdirs(temporary_root)
    require(temporary_root.stat().st_dev == path.parent.stat().st_dev, 'CROSS_DEVICE')
    temp = temporary_root / ('.vf-write-' + uuid.uuid4().hex)
    try:
        with temp.open('xb') as stream:
            os.chmod(temp, mode)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
        sync_dir(path.parent)
    finally:
        if temp.exists():
            temp.unlink()


def empty_ledger():
    return {'schema': SCHEMA, 'platform': os.name, 'release': None, 'files': {}, 'modes': {}}


def validate_ledger(ledger):
    require(type(ledger) is dict and set(ledger) == {'schema', 'platform', 'release', 'files', 'modes'}
            and ledger['schema'] == SCHEMA and ledger['platform'] == os.name, 'INVALID_LEDGER')
    require(ledger['release'] is None or (type(ledger['release']) is str
            and re.fullmatch('[a-z0-9][a-z0-9.-]{0,63}', ledger['release'])), 'INVALID_RELEASE')
    files = ledger['files']
    require(type(files) is dict and len(files) <= MAX_FILES, 'INVALID_LEDGER')
    require(ledger['release'] is not None or not files, 'INVALID_RELEASE')
    require(type(ledger['modes']) is dict and set(ledger['modes']) == set(files)
            and all(type(m) is int and m in (0o644, 0o755) for m in ledger['modes'].values()), 'INVALID_MODES')
    for name in files:
        relative(name)
    require(len({p.casefold() for p in files}) == len(files), 'PATH_COLLISION')
    folded = {p.casefold() for p in files}
    require(not any('/'.join(p.split('/')[:i]).casefold() in folded
                    for p in files for i in range(1, len(p.split('/')))), 'PATH_PREFIX_COLLISION')
    for name, digest in files.items():
        relative(name)
        require(type(digest) is str and re.fullmatch('[0-9a-f]{64}', digest), 'INVALID_DIGEST')


class ManagedFiles:
    """One existing skill root; never touches host configuration or processes."""

    def __init__(self, root, *, metadata_root=None):
        self.root = Path(root).absolute()
        no_links(self.root)
        require(self.root.is_dir(), 'ROOT_REQUIRED')
        self._external_binding = None
        if metadata_root is None:
            self.meta = self.root / META
        else:
            require(os.name == 'posix', 'WINDOWS_PRIVATE_STORAGE_UNVERIFIED')
            import config_transaction as cfg
            self.meta = Path(metadata_root).absolute()
            no_links(self.meta)
            resolved_root, resolved_meta = self.root.resolve(), self.meta.resolve()
            require(resolved_root != resolved_meta and resolved_root not in resolved_meta.parents
                    and resolved_meta not in resolved_root.parents, 'TRANSACTION_STATE_OVERLAP')
            cfg.private_directory(self.meta)
            self._external_binding = {'schema': 'vf.skill-files.external-state/v1',
                                      'root_sha256': sha(os.fsencode(resolved_root)),
                                      'metadata_sha256': sha(os.fsencode(resolved_meta))}
        no_links(self.meta)
        mkdirs(self.meta, mode=0o700)
        self.ledger = self.meta / 'ledger.json'
        self.journal = self.meta / 'journal.json'
        if self._external_binding is not None:
            self._initializing_external = True
            try:
                with self.locked():
                    pass
            finally:
                self._initializing_external = False

    def _check_external_binding(self):
        if self._external_binding is None:
            return
        import config_transaction as cfg
        cfg.private_directory(self.meta)
        binding = self.meta / 'binding.json'
        cfg.private_file(binding)
        raw = read(binding)
        if raw is None and getattr(self, '_initializing_external', False):
            require({p.name for p in self.meta.iterdir()} == {'scope.lock'}, 'UNBOUND_TRANSACTION_STATE')
            atomic(binding, encode(self._external_binding))
            raw = read(binding)
        require(raw is not None and decode(raw) == self._external_binding, 'TRANSACTION_STATE_BINDING_CHANGED')

    @contextmanager
    def locked(self):
        lock = self.meta / 'scope.lock'
        no_links(lock)
        with lock.open('a+b') as stream:
            if os.name == 'nt':
                import msvcrt
                if stream.tell() == 0:
                    stream.write(b'0')
                    stream.flush()
                stream.seek(0)
                try:
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                except OSError as exc:
                    raise Conflict('SCOPE_BUSY') from exc
            else:
                import fcntl
                try:
                    fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError as exc:
                    raise Conflict('SCOPE_BUSY') from exc
            try:
                self._check_external_binding()
                yield
            finally:
                if os.name == 'nt':
                    stream.seek(0)
                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)

    def snapshot(self):
        self._check_external_binding()
        raw = read(self.ledger)
        ledger = empty_ledger() if raw is None else decode(raw)
        validate_ledger(ledger)
        return ledger, sha(encode(ledger))

    def _read_managed(self, name, expected_mode=None):
        path = self.root / relative(name)
        data = read(path)
        if data is not None and expected_mode is not None:
            require(self._mode_matches(path, expected_mode), 'LOCAL_MODE_CONFLICT')
        return data

    def _mode_matches(self, path, expected):
        actual = stat.S_IMODE(path.stat().st_mode)
        # Windows has no POSIX executable bit; only the writable DOS attribute
        # maps here. This does not attest ACLs or native Windows execution.
        return bool(actual & stat.S_IWRITE) if os.name == 'nt' else actual == expected

    def _matches(self, name, data, ledger):
        if name not in ledger['files']:
            return data is None
        return data is not None and sha(data) == ledger['files'][name] and self._mode_matches(
            self.root / name, ledger['modes'][name])

    def _check_tree(self, ledger, extra=()):
        for name in sorted(set(ledger['files']) | set(extra)):
            data = self._read_managed(name, ledger['modes'].get(name))
            require((sha(data) if data is not None else None) == ledger['files'].get(name), 'LOCAL_CHANGE_CONFLICT')

    def _bind_release(self, ledger):
        path = self.meta / 'releases' / ('release-' + ledger['release'] + '.json')
        raw = read(path)
        bound = empty_ledger() if raw is None else decode(raw)
        if raw is not None:
            validate_ledger(bound)
            require(bound['release'] == ledger['release'], 'INVALID_RELEASE_BINDING')
        bound['release'] = ledger['release']
        for name, digest in ledger['files'].items():
            if name in bound['files']:
                require(bound['files'][name] == digest and bound['modes'][name] == ledger['modes'][name],
                        'IMMUTABLE_RELEASE_CONFLICT')
            bound['files'][name] = digest
            bound['modes'][name] = ledger['modes'][name]
        validate_ledger(bound)
        atomic(path, encode(bound))

    def _remember(self, journal):
        if journal is None or journal['state'] not in ('committed', 'recovered'):
            return
        path = self.meta / 'history' / (journal['id'] + '.json')
        raw = read(path)
        if raw is None:
            atomic(path, encode(journal))
        else:
            historic = decode(raw)
            # Cleanup progress lives in history; never replace it with an older
            # terminal journal when the next apply begins.
            require(all(historic.get(k) == v for k, v in journal.items() if k != 'state')
                    and historic.get('state') in (journal['state'], 'cleaning', 'cleaned'), 'HISTORY_CONFLICT')

    def _pending(self):
        self._check_external_binding()
        raw = read(self.journal)
        if raw is None:
            return None
        journal = decode(raw)
        require(type(journal) is dict and set(journal) == {'schema', 'id', 'state', 'before', 'after'}
                and journal['schema'] == SCHEMA
                and type(journal['id']) is str and re.fullmatch('[0-9a-f]{32}', journal['id']), 'INVALID_JOURNAL')
        require(journal['state'] in {'staged', 'committing', 'committed', 'recovered'}, 'INVALID_JOURNAL')
        validate_ledger(journal['before'])
        validate_ledger(journal['after'])
        return journal

    def apply(self, release, payload, expected_revision, checkpoint=lambda _: None, *, modes=None, transaction_id=None):
        """Apply bytes/modes only. Admission remains the caller's responsibility."""
        require(release is not None, 'RELEASE_REQUIRED')
        return self._apply(release, payload, expected_revision, checkpoint, modes=modes, transaction_id=transaction_id)

    def remove(self, expected_revision, checkpoint=lambda _: None, *, transaction_id=None):
        """Remove only unchanged owned files, retaining recoverable private state."""
        return self._apply(None, {}, expected_revision, checkpoint, modes={}, transaction_id=transaction_id)

    def _apply(self, release, payload, expected_revision, checkpoint, *, modes, transaction_id):
        require(type(payload) is dict and len(payload) <= MAX_FILES, 'INVALID_PAYLOAD')
        require(all(type(b) is bytes and len(b) <= MAX_FILE for b in payload.values())
                and sum(map(len, payload.values())) <= MAX_TOTAL, 'PAYLOAD_SIZE')
        require(modes is None or type(modes) is dict, 'INVALID_MODES')
        after = {'schema': SCHEMA, 'platform': os.name, 'release': release,
                 'files': {n: sha(b) for n, b in payload.items()},
                 'modes': {n: 0o644 for n in payload} if modes is None else dict(modes)}
        validate_ledger(after)
        paths = set(payload)
        folded = {n.casefold() for n in paths}
        require(not any('/'.join(n.split('/')[:i]).casefold() in folded
                        for n in paths for i in range(1, len(n.split('/')))), 'PATH_PREFIX_COLLISION')
        with self.locked():
            pending = self._pending()
            require(pending is None or pending['state'] in {'committed', 'recovered'}, 'RECOVERY_REQUIRED')
            require(pending is None or self._compensation(pending).state() != 'requested', 'COMPENSATION_REQUIRED')
            txn_id = uuid.uuid4().hex if transaction_id is None else transaction_id
            require(type(txn_id) is str and re.fullmatch('[0-9a-f]{32}', txn_id), 'INVALID_TRANSACTION')
            for path in (self.meta / txn_id, self.meta / 'history' / (txn_id + '.json'),
                         self.meta / ('compensation-' + txn_id + '.json')):
                no_links(path)
                require(not path.exists(), 'TRANSACTION_ALREADY_EXISTS')
            require(pending is None or pending['id'] != txn_id, 'TRANSACTION_ALREADY_EXISTS')
            self._remember(pending)
            before, revision = self.snapshot()
            require(revision == expected_revision, 'REVISION_CHANGED')
            old = {}
            for name in sorted(set(before['files']) | paths):
                data = self._read_managed(name, before['modes'].get(name))
                expected = before['files'].get(name)
                require((sha(data) if data is not None else None) == expected, 'LOCAL_CHANGE_CONFLICT')
                if data is not None:
                    old[name] = data
            if release is not None:
                self._bind_release(after)
            if before == after:
                return {'status': 'unchanged', 'revision': revision, 'host_verified': False}
            folder = self.meta / txn_id
            no_links(folder)
            mkdirs(folder, mode=0o700)
            for name, data in old.items():
                atomic(folder / 'before' / name, data)
            for name, data in payload.items():
                atomic(folder / 'after' / name, data)
            journal = {'schema': SCHEMA, 'id': txn_id, 'state': 'staged', 'before': before, 'after': after}
            atomic(self.journal, encode(journal))
            checkpoint('staged')
            # Revalidate original bytes immediately before touching the live tree.
            for name in sorted(set(before['files']) | paths):
                require(self._read_managed(name, before['modes'].get(name)) == old.get(name), 'LOCAL_CHANGE_CONFLICT')
            journal['state'] = 'committing'
            atomic(self.journal, encode(journal))
            checkpoint('committing')
            for name in sorted(set(before['files']) | paths):
                target = self.root / name
                require(self._read_managed(name, before['modes'].get(name)) == old.get(name), 'LOCAL_CHANGE_CONFLICT')
                if name in payload:
                    staged = read(folder / 'after' / name)
                    require(staged is not None and sha(staged) == after['files'][name], 'STAGED_BYTES_CHANGED')
                    atomic(target, staged, folder / 'pending', mode=after['modes'][name])
                else:
                    target.unlink()
                    sync_dir(target.parent)
                checkpoint('file:' + name)
            for name in sorted(set(before['files']) | paths):
                require(self._read_managed(name, after['modes'].get(name)) == payload.get(name), 'LOCAL_CHANGE_CONFLICT')
            require(self.snapshot()[1] == revision, 'REVISION_CHANGED')
            atomic(self.ledger, encode(after))
            checkpoint('ledger')
            require(self.snapshot()[0] == after, 'REVISION_CHANGED')
            self._check_tree(after, before['files'])
            journal['state'] = 'committed'
            atomic(self.journal, encode(journal))
            self._remember(journal)
            checkpoint('committed')
            return {'status': 'bytes_applied', 'transaction': txn_id,
                    'revision': sha(encode(after)), 'host_verified': False}

    def _compensation(self, journal):
        return compensation.Compensation(journal, self.meta / ('compensation-' + journal['id'] + '.json'), read, atomic)

    def _backups_for_compensation(self, journal):
        historic = read(self.meta / 'history' / (journal['id'] + '.json'))
        require(historic is not None and decode(historic) == journal, 'COMMITTED_BACKUPS_UNAVAILABLE')
        for side in ('before', 'after'):
            for name, digest in journal[side]['files'].items():
                raw = read(self.meta / journal['id'] / side / name)
                require(raw is not None and sha(raw) == digest, 'BACKUP_CHANGED')

    def rollback(self, transaction, expected_revision, checkpoint=lambda _: None):
        """Compensate only the latest exact commit, without rewriting its history."""
        with self.locked():
            journal = self._pending()
            require(journal is not None and journal['id'] == transaction
                    and journal['state'] == 'committed', 'NOT_LATEST_COMMIT')
            require(expected_revision == sha(encode(journal['after'])), 'REVISION_CHANGED')
            record = self._compensation(journal)
            state = record.state()
            if state == 'completed':
                require(self.snapshot()[0] == journal['before'], 'RECOVERY_LEDGER_CONFLICT')
                self._check_tree(journal['before'], journal['after']['files'])
                return {'status': 'already_compensated', 'transaction': transaction, 'host_verified': False}
            self._backups_for_compensation(journal)
            if state is None:
                require(self.snapshot()[1] == expected_revision, 'REVISION_CHANGED')
                self._check_tree(journal['after'], journal['before']['files'])
                record.save('requested')
                checkpoint('compensation_requested')
            return self._restore(journal, checkpoint, compensate=True)

    def recover(self, checkpoint=lambda _: None, *, transaction_id=None):
        """Resume pending writes or explicit compensation; never undo a normal commit."""
        with self.locked():
            journal = self._pending()
            require(transaction_id is None or (journal is not None and journal['id'] == transaction_id), 'TRANSACTION_CHANGED')
            if journal is not None and self._compensation(journal).state() == 'requested':
                require(journal['state'] == 'committed', 'INVALID_COMPENSATION')
                self._backups_for_compensation(journal)
                return self._restore(journal, checkpoint, compensate=True)
            if journal is None or journal['state'] in {'committed', 'recovered'}:
                return {'status': 'nothing_pending', 'host_verified': False}
            return self._restore(journal, checkpoint)

    def _restore(self, journal, checkpoint, *, compensate=False):
        before, after = journal['before'], journal['after']
        current, _ = self.snapshot()
        require(current in (before, after), 'RECOVERY_LEDGER_CONFLICT')
        old = {}
        names = sorted(set(before['files']) | set(after['files']))
        for name in names:
            data = self._read_managed(name)
            require(self._matches(name, data, before) or self._matches(name, data, after), 'RECOVERY_FILE_CONFLICT')
            if name in before['files']:
                backup = read(self.meta / journal['id'] / 'before' / name)
                require(backup is not None and sha(backup) == before['files'][name], 'BACKUP_CHANGED')
                old[name] = backup
        # Validation precedes recovery writes, so a known conflict preserves all files.
        for name in names:
            target = self.root / name
            data = self._read_managed(name)
            require(self._matches(name, data, before) or self._matches(name, data, after), 'RECOVERY_FILE_CONFLICT')
            if name in old:
                if not self._matches(name, data, before):
                    atomic(target, old[name], self.meta / journal['id'] / 'pending', mode=before['modes'][name])
            elif data is not None:
                target.unlink()
                sync_dir(target.parent)
            checkpoint('file:' + name)
        require(self.snapshot()[0] in (before, after), 'RECOVERY_LEDGER_CONFLICT')
        self._check_tree(before, names)
        atomic(self.ledger, encode(before))
        checkpoint('ledger')
        require(self.snapshot()[0] == before, 'RECOVERY_LEDGER_CONFLICT')
        self._check_tree(before, names)
        if compensate:
            self._compensation(journal).save('completed')
            checkpoint('compensation_completed')
        else:
            journal['state'] = 'recovered'
            atomic(self.journal, encode(journal))
            self._remember(journal)
        return {'status': 'bytes_restored', 'transaction': journal['id'], 'host_verified': False}

    def cleanup(self, transaction, expected_revision, checkpoint=lambda _: None):
        """Explicitly discard verified staging for a completed transaction only."""
        require(type(transaction) is str and re.fullmatch('[0-9a-f]{32}', transaction), 'INVALID_TRANSACTION')
        with self.locked():
            pending = self._pending()
            require(pending is None or pending['state'] in ('committed', 'recovered'), 'RECOVERY_REQUIRED')
            require(pending is None or self._compensation(pending).state() != 'requested', 'COMPENSATION_REQUIRED')
            self._remember(pending)
            current, revision = self.snapshot()
            require(revision == expected_revision, 'REVISION_CHANGED')
            self._check_tree(current)
            history_path = self.meta / 'history' / (transaction + '.json')
            raw = read(history_path)
            require(raw is not None, 'UNKNOWN_TRANSACTION')
            history = decode(raw)
            require(type(history) is dict and set(history) == {'schema', 'id', 'state', 'before', 'after'}
                    and history['schema'] == SCHEMA and history['id'] == transaction
                    and history['state'] in ('committed', 'recovered', 'cleaning', 'cleaned'), 'INVALID_HISTORY')
            validate_ledger(history['before'])
            validate_ledger(history['after'])
            folder = self.meta / transaction
            no_links(folder)
            if history['state'] == 'cleaned':
                require(not folder.exists(), 'CLEANED_STORAGE_REAPPEARED')
                return {'status': 'already_cleaned', 'transaction': transaction, 'host_verified': False}
            require(folder.is_dir() or history['state'] == 'cleaning', 'TRANSACTION_STORAGE_MISSING')
            allowed = {side + '/' + name: digest for side in ('before', 'after')
                       for name, digest in history[side]['files'].items()}
            directories = {'before', 'after', 'pending'}
            for name in allowed:
                directories.update('/'.join(name.split('/')[:i]) for i in range(1, len(name.split('/'))))
            present = []
            if folder.exists():
                for path in folder.rglob('*'):
                    no_links(path)
                    name = path.relative_to(folder).as_posix()
                    if path.is_dir():
                        require(name in directories, 'UNKNOWN_STAGING_DIRECTORY')
                    else:
                        require(name in allowed, 'UNKNOWN_STAGING_FILE')
                        require(sha(read(path)) == allowed[name], 'STAGING_CHANGED')
                        present.append(path)
                if history['state'] != 'cleaning':
                    require({p.relative_to(folder).as_posix() for p in present} == set(allowed), 'STAGING_FILE_MISSING')
            history['state'] = 'cleaning'
            atomic(history_path, encode(history))
            checkpoint('cleaning')
            for path in sorted(present):
                name = path.relative_to(folder).as_posix()
                require(sha(read(path)) == allowed[name], 'STAGING_CHANGED')
                path.unlink()
                sync_dir(path.parent)
                checkpoint('file:' + name)
            if folder.exists():
                for path in sorted((p for p in folder.rglob('*') if p.is_dir()), key=lambda p: len(p.parts), reverse=True):
                    no_links(path)
                    require(path.relative_to(folder).as_posix() in directories, 'UNKNOWN_STAGING_DIRECTORY')
                    path.rmdir()  # Unknown new files are preserved by nonempty-dir rejection.
                folder.rmdir()
                sync_dir(self.meta)
            checkpoint('removed')
            history['state'] = 'cleaned'
            atomic(history_path, encode(history))
            return {'status': 'staging_removed', 'transaction': transaction, 'host_verified': False}
