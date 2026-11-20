"""Recoverable JSON configuration byte transactions; not a host installer.

Callers must complete release admission, credential policy, host-version and
activity checks first. This module never discovers hosts or acquires credentials.
The POSIX private-storage path is implemented; Windows ACL support is explicitly
unavailable. No operating-system CAS against non-cooperating editors is claimed.
"""
from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import uuid

import host_config as config
import install_transaction as io
import transaction_compensation as compensation

META = '.vf-config-transaction'
SCHEMA = 'vf.host-config.transaction/v1'
MODES = {0o600, 0o640, 0o644}
TERMINAL = {'committed', 'recovered', 'cleaned_committed', 'cleaned_recovered'}
STATES = TERMINAL | {'preparing', 'staged', 'writing', 'cleaning_committed', 'cleaning_recovered'}
require = io.require


def no_extended_metadata(path):
    """Reject ACL/custom metadata; fingerprint the OS-created provenance tag."""
    provenance = None
    if hasattr(os, 'listxattr'):
        require(not os.listxattr(path, follow_symlinks=False), 'EXTENDED_METADATA_UNSUPPORTED')
    require(not getattr(path.stat(), 'st_flags', 0), 'FILE_FLAGS_UNSUPPORTED')
    if sys.platform == 'darwin':
        try:
            attrs = subprocess.run(['/usr/bin/xattr', str(path)], capture_output=True, timeout=5)
            result = subprocess.run(['/bin/ls', '-lde', str(path)], capture_output=True, timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            raise io.Conflict('METADATA_INSPECTION_FAILED') from None
        require(attrs.returncode == 0 and set(attrs.stdout.splitlines()) <= {b'com.apple.provenance'},
                'EXTENDED_METADATA_UNSUPPORTED')
        if attrs.stdout:
            tag = subprocess.run(['/usr/bin/xattr', '-px', 'com.apple.provenance', str(path)],
                                 capture_output=True, timeout=5)
            require(tag.returncode == 0 and len(tag.stdout) <= 4096, 'METADATA_INSPECTION_FAILED')
            try:
                provenance = io.sha(bytes.fromhex(tag.stdout.decode('ascii')))
            except (ValueError, UnicodeError):
                raise io.Conflict('METADATA_INSPECTION_FAILED') from None
        # Do not disclose ls output: it includes local owner and path information.
        require(result.returncode == 0 and len(result.stdout.splitlines()) == 1,
                'EXTENDED_ACL_UNSUPPORTED')
    return provenance


def private_directory(path):
    io.no_links(path)
    try:
        path.mkdir(mode=0o700)
        io.sync_dir(path.parent)
    except FileExistsError:
        pass
    info = path.stat()
    require(stat.S_ISDIR(info.st_mode) and info.st_uid == os.getuid()
            and stat.S_IMODE(info.st_mode) == 0o700, 'PRIVATE_DIRECTORY_REQUIRED')
    no_extended_metadata(path)


def private_file(path):
    io.no_links(path)
    if not path.exists():
        return
    info = path.stat()
    require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_uid == os.getuid()
            and stat.S_IMODE(info.st_mode) == 0o600, 'PRIVATE_FILE_REQUIRED')
    no_extended_metadata(path)


class ConfigFile:
    """One explicit target and its fixed-format installer-owned VF ledger."""

    schema = SCHEMA
    planner = config

    def _owned_bytes(self, raw):
        return self.planner.inspect(raw)[-1]

    def __init__(self, root, target):
        require(os.name == 'posix', 'WINDOWS_PRIVATE_STORAGE_UNVERIFIED')
        self.root = Path(root).absolute()
        io.no_links(self.root)
        require(self.root.is_dir() and self.root.stat().st_uid == os.getuid(), 'ROOT_REQUIRED')
        self.target = io.relative(target)
        require(not any(part.casefold() in {META, io.META} for part in target.split('/')), 'INVALID_TARGET')
        self.path = self.root / target
        io.no_links(self.path)
        require(self.path.parent.is_dir(), 'TARGET_PARENT_REQUIRED')
        self.base = self.root / META
        private_directory(self.base)
        self.meta = self.base / io.sha(target.casefold().encode('utf-8'))
        private_directory(self.meta)
        require(self.meta.stat().st_dev == self.path.parent.stat().st_dev, 'CROSS_DEVICE')
        self.transactions = self.meta / 'transactions'
        private_directory(self.transactions)
        self.ledger_path = self.meta / 'ledger.json'
        self.active_path = self.meta / 'active.json'

    def _guard(self):
        for path in (self.base, self.meta, self.transactions):
            private_directory(path)
        io.no_links(self.path)

    def _read_private(self, path):
        private_file(path)
        return io.read(path)

    def _write_private(self, path, raw):
        private_file(path)
        private_directory(path.parent)
        io.atomic(path, raw, mode=0o600)
        private_file(path)

    @contextmanager
    def locked(self):
        import fcntl
        self._guard()
        path = self.meta / 'scope.lock'
        private_file(path)
        fd = os.open(path, os.O_RDWR | os.O_CREAT | getattr(os, 'O_NOFOLLOW', 0), 0o600)
        with os.fdopen(fd, 'r+b') as stream:
            private_file(path)
            require(path.stat().st_ino == os.fstat(stream.fileno()).st_ino, 'LOCK_CHANGED')
            try:
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise io.Conflict('SCOPE_BUSY') from None
            try:
                yield
            finally:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)

    def _empty(self):
        return {'schema': self.schema, 'target': self.target, 'sequence': 0, 'owned_sha256': None}

    def _ledger_valid(self, value):
        require(type(value) is dict and set(value) == {'schema', 'target', 'sequence', 'owned_sha256'}
                and value['schema'] == self.schema and value['target'] == self.target
                and type(value['sequence']) is int and 0 <= value['sequence'] < 2**53,
                'INVALID_LEDGER')
        require(value['owned_sha256'] is None or (type(value['owned_sha256']) is str
                and re.fullmatch('[0-9a-f]{64}', value['owned_sha256'])), 'INVALID_LEDGER')
        require(value['sequence'] > 0 or value['owned_sha256'] is None, 'INVALID_LEDGER')

    def snapshot(self):
        self._guard()
        raw = self._read_private(self.ledger_path)
        value = self._empty() if raw is None else io.decode(raw)
        self._ledger_valid(value)
        return value, io.sha(io.encode(value))

    def observe(self):
        """Non-secret preflight binding, including file permissions and metadata."""
        with self.locked():
            return {'ledger_revision': self.snapshot()[1], 'file': self._file()[1]}

    def _file(self):
        io.no_links(self.path)
        raw = io.read(self.path)
        if raw is None:
            return None, {'sha256': None, 'mode': None, 'gid': None, 'provenance_sha256': None}
        info = self.path.stat()
        require(info.st_nlink == 1 and info.st_uid == os.getuid()
                and stat.S_IMODE(info.st_mode) in MODES, 'CONFIG_PERMISSIONS_UNSUPPORTED')
        require(len(raw) <= self.planner.MAX_BYTES, 'CONFIG_SIZE_OR_TYPE')
        provenance = no_extended_metadata(self.path)
        return raw, {'sha256': io.sha(raw), 'mode': stat.S_IMODE(info.st_mode), 'gid': info.st_gid,
                     'provenance_sha256': provenance}

    def _record_valid(self, record):
        require(type(record) is dict and set(record) == {'sha256', 'mode', 'gid', 'provenance_sha256'}, 'INVALID_FILE_RECORD')
        if record['sha256'] is None:
            require(record['mode'] is None and record['gid'] is None and record['provenance_sha256'] is None, 'INVALID_FILE_RECORD')
        else:
            require(type(record['sha256']) is str and re.fullmatch('[0-9a-f]{64}', record['sha256'])
                    and type(record['mode']) is int and record['mode'] in MODES, 'INVALID_FILE_RECORD')
            require(type(record['gid']) is int and 0 <= record['gid'] < 2**32, 'INVALID_FILE_RECORD')
            require(record['provenance_sha256'] is None or (type(record['provenance_sha256']) is str
                    and re.fullmatch('[0-9a-f]{64}', record['provenance_sha256'])), 'INVALID_FILE_RECORD')

    def _replace_config(self, raw, record, expected, folder):
        pending = folder / 'pending'
        private_directory(pending)
        temporary = pending / ('.vf-config-write-' + uuid.uuid4().hex)
        try:
            with temporary.open('xb') as stream:
                os.chmod(temporary, record['mode'])
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            require(no_extended_metadata(temporary) == record['provenance_sha256'], 'CONFIG_METADATA_CANNOT_PRESERVE')
            require(temporary.stat().st_gid == record['gid'], 'CONFIG_GROUP_CANNOT_PRESERVE')
            require(self._file()[1] == expected, 'CONFIG_CHANGED')
            os.replace(temporary, self.path)
            io.sync_dir(self.path.parent)
        finally:
            if temporary.exists():
                io.no_links(temporary)
                temporary.unlink()

    def _new_metadata(self, folder):
        probe = folder / 'pending' / ('.vf-metadata-probe-' + uuid.uuid4().hex)
        try:
            with probe.open('xb'):
                os.chmod(probe, 0o600)
            return no_extended_metadata(probe), probe.stat().st_gid
        finally:
            if probe.exists():
                io.no_links(probe)
                probe.unlink()

    def _folder(self, identifier):
        require(type(identifier) is str and re.fullmatch('[0-9a-f]{32}', identifier), 'INVALID_TRANSACTION')
        folder = self.transactions / identifier
        io.no_links(folder)
        require(folder.is_dir(), 'TRANSACTION_MISSING')
        private_directory(folder)
        return folder

    def _journal(self, identifier):
        folder = self._folder(identifier)
        raw = self._read_private(folder / 'journal.json')
        require(raw is not None, 'JOURNAL_MISSING')
        value = io.decode(raw)
        require(type(value) is dict and set(value) == {'schema', 'id', 'state', 'prepared', 'before', 'after', 'before_file', 'after_file'}
                and value['schema'] == self.schema and value['id'] == identifier
                and type(value['state']) is str and value['state'] in STATES
                and type(value['prepared']) is bool, 'INVALID_JOURNAL')
        require(value['prepared'] or value['state'] in {'preparing','recovered','cleaning_recovered','cleaned_recovered'}, 'INVALID_JOURNAL')
        for side in ('before', 'after'):
            self._ledger_valid(value[side])
            self._record_valid(value[side + '_file'])
        require(value['after']['sequence'] == value['before']['sequence'] + 1, 'INVALID_JOURNAL')
        require(value['after_file']['sha256'] is not None and
                value['after_file']['mode'] == (value['before_file']['mode'] or 0o600), 'INVALID_JOURNAL')
        return value

    def _active(self):
        raw = self._read_private(self.active_path)
        if raw is None:
            return None
        pointer = io.decode(raw)
        require(type(pointer) is dict and set(pointer) == {'id'}, 'INVALID_ACTIVE_POINTER')
        return self._journal(pointer['id'])

    def _save(self, journal):
        self._write_private(self._folder(journal['id']) / 'journal.json', io.encode(journal))

    def apply(self, desired, *, expected_revision, expected_file, checkpoint=lambda _: None, transaction_id=None):
        self._record_valid(expected_file)
        with self.locked():
            previous = self._active()
            require(previous is None or previous['state'] in TERMINAL, 'RECOVERY_OR_CLEANUP_REQUIRED')
            require(previous is None or self._compensation(previous).state() != 'requested', 'COMPENSATION_REQUIRED')
            identifier = uuid.uuid4().hex if transaction_id is None else transaction_id
            require(type(identifier) is str and re.fullmatch('[0-9a-f]{32}', identifier), 'INVALID_TRANSACTION')
            for path in (self.transactions / identifier, self.meta / ('compensation-' + identifier + '.json')):
                io.no_links(path)
                require(not path.exists(), 'TRANSACTION_ALREADY_EXISTS')
            before, revision = self.snapshot()
            require(revision == expected_revision, 'REVISION_CHANGED')
            raw, before_file = self._file()
            require(before_file == expected_file, 'CONFIG_CHANGED')
            plan = self.planner.plan_update(raw, desired, expected_config_sha256=expected_file['sha256'],
                                      expected_owned_sha256=before['owned_sha256'])
            if not plan.changed:
                return {'status': 'unchanged', 'revision': revision, 'host_verified': False}
            after = {**before, 'sequence': before['sequence']+1, 'owned_sha256': plan.owned_after_sha256}
            self._ledger_valid(after)
            folder = self.transactions / identifier
            private_directory(folder)
            private_directory(folder / 'pending')
            provenance, group = self._new_metadata(folder)
            require(raw is None or provenance == before_file['provenance_sha256'], 'CONFIG_METADATA_CANNOT_PRESERVE')
            require(raw is None or group == before_file['gid'], 'CONFIG_GROUP_CANNOT_PRESERVE')
            after_file = {'sha256': self.planner.digest(plan.after), 'mode': before_file['mode'] or 0o600,
                          'gid': group, 'provenance_sha256': provenance}
            journal = {'schema': self.schema, 'id': identifier, 'state': 'preparing', 'prepared': False,
                       'before': before, 'after': after, 'before_file': before_file, 'after_file': after_file}
            self._save(journal)
            self._write_private(self.active_path, io.encode({'id': identifier}))
            checkpoint('preparing')
            if raw is not None:
                self._write_private(folder / 'before.bin', raw)
            checkpoint('before_backup')
            self._write_private(folder / 'after.bin', plan.after)
            checkpoint('after_backup')
            journal['prepared'] = True
            journal['state'] = 'staged'
            self._save(journal)
            checkpoint('staged')
            require(self.snapshot()[1] == revision, 'REVISION_CHANGED')
            require(self._file()[1] == before_file, 'CONFIG_CHANGED')
            journal['state'] = 'writing'
            self._save(journal)
            checkpoint('writing')
            require(self._file()[1] == before_file and self.snapshot()[1] == revision, 'CONFIG_CHANGED')
            staged = self._read_private(folder / 'after.bin')
            require(self.planner.digest(staged) == after_file['sha256'], 'STAGED_BYTES_CHANGED')
            self._replace_config(staged, after_file, before_file, folder)
            checkpoint('config')
            require(self._file()[1] == after_file, 'CONFIG_CHANGED')
            require(self.snapshot()[1] == revision, 'REVISION_CHANGED')
            self._write_private(self.ledger_path, io.encode(after))
            checkpoint('ledger')
            require(self._file()[1] == after_file and self.snapshot()[0] == after, 'COMMIT_CHANGED')
            journal['state'] = 'committed'
            self._save(journal)
            checkpoint('committed')
            return {'status': 'config_bytes_applied', 'transaction': identifier,
                    'revision': io.sha(io.encode(after)), 'host_verified': False}

    def _backups(self, journal, *, missing_allowed=False):
        folder = self._folder(journal['id'])
        result = {}
        for side in ('before', 'after'):
            raw = self._read_private(folder / (side + '.bin'))
            expected = journal[side + '_file']['sha256']
            require((missing_allowed and raw is None) or self.planner.digest(raw) == expected, 'BACKUP_CHANGED')
            if raw is not None:
                # Bind ownership ledger to the actual backup, not just a journal assertion.
                require(self.planner.digest(self._owned_bytes(raw)) == journal[side]['owned_sha256'], 'BACKUP_OWNERSHIP_MISMATCH')
            result[side] = raw
        return result

    def _compensation(self, journal):
        return compensation.Compensation(journal, self.meta / ('compensation-' + journal['id'] + '.json'),
                                         self._read_private, self._write_private)

    def rollback(self, identifier, *, expected_revision, checkpoint=lambda _: None):
        """Explicit latest-commit compensation; preserves the original commit journal."""
        with self.locked():
            journal = self._active()
            require(journal is not None and journal['id'] == identifier, 'NOT_LATEST_COMMIT')
            require(expected_revision == io.sha(io.encode(journal['after'])), 'REVISION_CHANGED')
            record = self._compensation(journal)
            state = record.state()
            if state == 'completed':
                require(self.snapshot()[0] == journal['before'] and self._file()[1] == journal['before_file'],
                        'RECOVERY_CHANGED')
                return {'status': 'already_compensated', 'transaction': identifier, 'host_verified': False}
            require(journal['state'] == 'committed', 'NOT_LATEST_COMMIT')
            self._backups(journal)
            if state is None:
                require(self.snapshot()[1] == expected_revision, 'REVISION_CHANGED')
                require(self._file()[1] == journal['after_file'], 'CONFIG_CHANGED')
                record.save('requested')
                checkpoint('compensation_requested')
            return self._restore(journal, checkpoint, compensate=True)

    def recover(self, checkpoint=lambda _: None, *, transaction_id=None):
        with self.locked():
            journal = self._active()
            require(transaction_id is None or (journal is not None and journal['id'] == transaction_id), 'TRANSACTION_CHANGED')
            if journal is not None and self._compensation(journal).state() == 'requested':
                require(journal['state'] == 'committed', 'INVALID_COMPENSATION')
                return self._restore(journal, checkpoint, compensate=True)
            if journal is None or journal['state'] in TERMINAL:
                return {'status': 'nothing_pending', 'host_verified': False}
            require(journal['state'] in {'preparing', 'staged', 'writing'}, 'CLEANUP_REQUIRED')
            return self._restore(journal, checkpoint)

    def _restore(self, journal, checkpoint, *, compensate=False):
        backups = self._backups(journal, missing_allowed=not journal['prepared'])
        if not journal['prepared']:
            require(self.snapshot()[0] == journal['before'] and self._file()[1] == journal['before_file'],
                    'UNPREPARED_TRANSACTION_CHANGED')
        require(self.snapshot()[0] in (journal['before'], journal['after']), 'RECOVERY_LEDGER_CONFLICT')
        require(self._file()[1] in (journal['before_file'], journal['after_file']), 'RECOVERY_CONFIG_CONFLICT')
        folder = self._folder(journal['id'])
        if self._file()[1] != journal['before_file']:
            if backups['before'] is None:
                self.path.unlink()
                io.sync_dir(self.path.parent)
            else:
                self._replace_config(backups['before'], journal['before_file'], self._file()[1], folder)
        checkpoint('config')
        require(self._file()[1] == journal['before_file'], 'RECOVERY_CONFIG_CONFLICT')
        require(self.snapshot()[0] in (journal['before'], journal['after']), 'RECOVERY_LEDGER_CONFLICT')
        self._write_private(self.ledger_path, io.encode(journal['before']))
        checkpoint('ledger')
        require(self._file()[1] == journal['before_file'] and self.snapshot()[0] == journal['before'], 'RECOVERY_CHANGED')
        if compensate:
            self._compensation(journal).save('completed')
            checkpoint('compensation_completed')
        else:
            journal['state'] = 'recovered'
            self._save(journal)
        return {'status': 'config_bytes_restored', 'transaction': journal['id'], 'host_verified': False}

    def cleanup(self, identifier, *, expected_revision, checkpoint=lambda _: None):
        """Delete only verified terminal backups; retain non-secret journal."""
        with self.locked():
            active = self._active()
            require(active is None or active['state'] in TERMINAL or
                    (active['id'] == identifier and active['state'].startswith('cleaning_')), 'RECOVERY_OR_CLEANUP_REQUIRED')
            require(active is None or self._compensation(active).state() != 'requested', 'COMPENSATION_REQUIRED')
            require(self.snapshot()[1] == expected_revision, 'REVISION_CHANGED')
            journal = self._journal(identifier)
            require(journal['state'] not in {'preparing', 'staged', 'writing'}, 'TRANSACTION_NOT_TERMINAL')
            folder = self._folder(identifier)
            for path in folder.iterdir():
                io.no_links(path)
                require(path.name in {'journal.json', 'before.bin', 'after.bin', 'pending'}, 'UNKNOWN_TRANSACTION_CONTENT')
            pending = folder / 'pending'
            if pending.exists():
                private_directory(pending)
                require(not any(pending.iterdir()), 'UNKNOWN_PENDING_CONTENT')
            if journal['state'].startswith('cleaned_'):
                require(not (folder/'before.bin').exists() and not (folder/'after.bin').exists(), 'CLEANED_BACKUP_REAPPEARED')
                return {'status': 'already_cleaned', 'transaction': identifier, 'host_verified': False}
            self._backups(journal, missing_allowed=not journal['prepared'] or journal['state'].startswith('cleaning_'))
            outcome = journal['state'].removeprefix('cleaning_')
            journal['state'] = 'cleaning_' + outcome
            self._save(journal)
            checkpoint('cleaning')
            for side in ('before', 'after'):
                path = folder / (side + '.bin')
                raw = self._read_private(path)
                if raw is not None:
                    require(self.planner.digest(raw) == journal[side+'_file']['sha256'], 'BACKUP_CHANGED')
                    path.unlink()
                    io.sync_dir(folder)
                checkpoint(side)
            if pending.exists():
                pending.rmdir()
                io.sync_dir(folder)
            journal['state'] = 'cleaned_' + outcome
            self._save(journal)
            return {'status': 'private_backups_removed', 'transaction': identifier, 'host_verified': False}
