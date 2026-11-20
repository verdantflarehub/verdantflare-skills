"""POSIX skill/config and optional staged-runtime operation coordinator.

Explicit isolated roots only. Recovery aborts incomplete operations and preserves
external edits. Runtime staging is separate; host activity, admission and Windows
remain caller integration work. Journals contain no config plaintext.
"""
from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path
import re
import uuid

import config_transaction as cfg
import install_transaction as io

SCHEMA = 'vf.skill-config.operation/v1'
RUNTIME_SCHEMA = 'vf.runtime-skill-config.operation/v1'
REMOVAL_SCHEMA = 'vf.runtime-skill-config.removal/v1'
STATES = {'preparing', 'runtime', 'skills', 'config', 'committed', 'aborting', 'aborted', 'cleaned', 'cleaned_aborted'}


class InstallOperation:
    def __init__(self, state_root, skill_root, config_root, config_target, *, runtime_root=None, config_format='json',
                 config_profile='generic', admitted_url=None):
        io.require(os.name == 'posix', 'WINDOWS_PRIVATE_STORAGE_UNVERIFIED')
        io.require(type(config_format) is str and config_format in {'json', 'toml'}, 'CONFIG_FORMAT_UNSUPPORTED')
        io.require(type(config_profile) is str and config_profile in {'generic', 'workbuddy'},
                   'CONFIG_PROFILE_UNSUPPORTED')
        self._workbuddy_url = None
        config_class = cfg.ConfigFile
        if config_format == 'toml':
            from toml_transaction import TomlConfigFile
            config_class = TomlConfigFile
        if config_profile == 'workbuddy':
            import host_workbuddy as workbuddy
            io.require(config_format == 'json' and config_target == 'mcp.json', 'WORKBUDDY_CONFIG_TARGET_INVALID')
            workbuddy.validate_url(admitted_url)
            self._workbuddy_url = admitted_url
            config_class = lambda root, target: workbuddy.ConfigFile(root, admitted_url=admitted_url)
        else:
            io.require(admitted_url is None, 'CONFIG_PROFILE_CONNECTION_UNEXPECTED')
        roots = [Path(p).absolute() for p in (state_root, skill_root, config_root)]
        if runtime_root is not None:
            roots.append(Path(runtime_root).absolute())
        for root in roots:
            io.no_links(root)
            io.require(root.is_dir() and root.stat().st_uid == os.getuid(), 'ROOT_REQUIRED')
        # resolve after refusing links, so spelling aliases cannot evade overlap.
        roots = [p.resolve() for p in roots]
        for i, a in enumerate(roots):
            for j, b in enumerate(roots[i+1:], i+1):
                # The real WorkBuddy file contract has mcp.json beside skills/.
                # Only these exact roots may contain one another; their managed
                # file sets and all transaction metadata remain disjoint.
                if config_profile == 'workbuddy' and (i, j) == (1, 2) and a == b / 'skills':
                    continue
                io.require(not any(os.path.samefile(a, parent) for parent in (b, *b.parents))
                           and not any(os.path.samefile(b, parent) for parent in a.parents), 'ROOT_OVERLAP')
        self.bindings = [io.sha(os.fsencode(p)) for p in roots]
        self.bindings.append(io.sha(config_target.encode('utf-8')))
        if config_format == 'toml':
            self.bindings.append(io.sha(b'vf.config-format:toml/v1'))
        if config_profile == 'workbuddy':
            io.require(not (roots[1] / io.META).exists(), 'WORKBUDDY_LEGACY_SKILL_STATE_PRESENT')
            self.bindings.append(io.sha(b'vf.host-config-profile:workbuddy/v2'))
        self.meta = roots[0] / '.vf-install-operation'
        cfg.private_directory(self.meta)
        self.active_path = self.meta / 'active.json'
        self.skills = io.ManagedFiles(roots[1], metadata_root=roots[0] / '.vf-workbuddy-skill-files') \
            if config_profile == 'workbuddy' else io.ManagedFiles(roots[1])
        self.config = config_class(roots[2], config_target)
        self.kinds = ('skills', 'config')
        self.schema = SCHEMA
        if runtime_root is not None:
            from runtime_tools.runtime_store import RuntimeStore
            self.runtime_store = RuntimeStore(roots[3])
            self.runtime = self.runtime_store.pointer
            self.kinds = ('runtime', 'skills', 'config')
            self.schema = RUNTIME_SCHEMA

    def _read(self, path):
        cfg.private_file(path)
        return io.read(path)

    def _write(self, path, value):
        cfg.private_directory(self.meta)
        cfg.private_file(path)
        io.atomic(path, io.encode(value), mode=0o600)
        cfg.private_file(path)

    @contextmanager
    def locked(self):
        import fcntl
        cfg.private_directory(self.meta)
        path = self.meta / 'scope.lock'
        cfg.private_file(path)
        fd = os.open(path, os.O_RDWR | os.O_CREAT | getattr(os, 'O_NOFOLLOW', 0), 0o600)
        with os.fdopen(fd, 'r+b') as stream:
            cfg.private_file(path)
            io.require(path.stat().st_ino == os.fstat(stream.fileno()).st_ino, 'LOCK_CHANGED')
            try:
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise io.Conflict('SCOPE_BUSY') from None
            try:
                yield
            finally:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)

    def _save(self, journal):
        self._write(self.meta / (journal['id'] + '.json'), journal)

    def _active(self):
        raw = self._read(self.active_path)
        if raw is None:
            return None
        pointer = io.decode(raw)
        io.require(type(pointer) is dict and set(pointer) == {'id'} and type(pointer['id']) is str
                   and re.fullmatch('[0-9a-f]{32}', pointer['id']), 'INVALID_OPERATION_POINTER')
        raw = self._read(self.meta / (pointer['id'] + '.json'))
        io.require(raw is not None, 'OPERATION_MISSING')
        j = io.decode(raw)
        io.require(type(j) is dict and set(j) == {'schema','id','bindings','state',*self.kinds}
                   and self.valid_schema(j) and j['id'] == pointer['id'] and j['bindings'] == self.bindings
                   and type(j['state']) is str and j['state'] in STATES, 'INVALID_OPERATION')
        for kind in self.kinds:
            child = j[kind]
            fields = {'id','previous','before','before_file','result'} | ({'tree'} if kind == 'runtime' else set())
            io.require(type(child) is dict and set(child) == fields
                       and type(child['id']) is str and re.fullmatch('[0-9a-f]{32}', child['id'])
                       and (child['previous'] is None or (type(child['previous']) is str
                           and re.fullmatch('[0-9a-f]{32}', child['previous'])))
                       and (child['result'] is None or type(child['result']) is dict), 'INVALID_CHILD')
            if kind != 'config':
                io.validate_ledger(child['before'])
                io.require(child['before_file'] is None, 'INVALID_CHILD')
                if kind == 'runtime':
                    io.require(child['tree'] is None if j['schema'] == REMOVAL_SCHEMA else
                               type(child['tree']) is str and re.fullmatch('[0-9a-f]{64}',child['tree']), 'INVALID_CHILD')
            else:
                self.config._ledger_valid(child['before'])
                self.config._record_valid(child['before_file'])
        return j

    def valid_schema(self, journal):
        return (journal.get('schema') == self.schema or
                (self.schema == RUNTIME_SCHEMA and journal.get('schema') == REMOVAL_SCHEMA))

    def _child_active(self, kind):
        return self.config._active() if kind == 'config' else getattr(self, kind)._pending()

    def _runtime_pointer_valid(self, expected_tree=None):
        raw = io.read(self.runtime.root / 'runtime.json')
        if raw is None:
            io.require(expected_tree is None, 'RUNTIME_POINTER_MISSING')
            return
        value = io.decode(raw)
        io.require(type(value) is dict and set(value) == {'schema','tree_sha256','release'}
                   and value['schema'] == 'vf.runtime-pointer/v1', 'INVALID_RUNTIME_POINTER')
        receipt = self.runtime_store.verify(value['tree_sha256'])
        io.require(receipt['release'] == value['release']
                   and (expected_tree is None or value['tree_sha256'] == expected_tree), 'RUNTIME_POINTER_CHANGED')

    def _prepare(self, kind):
        engine = getattr(self, kind)
        with engine.locked():
            active = self._child_active(kind)
            terminal = cfg.TERMINAL if kind == 'config' else {'committed','recovered'}
            io.require(active is None or active['state'] in terminal, 'CHILD_RECOVERY_REQUIRED')
            io.require(active is None or engine._compensation(active).state() != 'requested', 'COMPENSATION_REQUIRED')
            before = engine.snapshot()[0]
            if kind != 'config':
                engine._check_tree(before)
            if kind == 'runtime':
                self._runtime_pointer_valid()
            return {'id': uuid.uuid4().hex, 'previous': active['id'] if active else None,
                    'before': before, 'before_file': engine._file()[1] if kind == 'config' else None, 'result': None}

    def _bind_child(self, kind, child, active):
        io.require(active['before'] == child['before'], 'CHILD_INPUT_CHANGED')
        if kind == 'config':
            io.require(active['before_file'] == child['before_file'], 'CHILD_INPUT_CHANGED')

    def _matches_before(self, kind, child):
        engine = getattr(self, kind)
        io.require(engine.snapshot()[0] == child['before'], 'OPERATION_LEDGER_CONFLICT')
        if kind != 'config':
            engine._check_tree(child['before'])
            if kind == 'runtime':
                self._runtime_pointer_valid()
        else:
            io.require(engine._file()[1] == child['before_file'], 'OPERATION_CONFIG_CONFLICT')

    def apply(self, release, payload, desired_config, *, expected_skill_revision,
              expected_config_revision, expected_config_file, modes=None, checkpoint=lambda _: None,
              runtime_digest=None, expected_runtime_revision=None):
        return self._apply(release, payload, desired_config, expected_skill_revision=expected_skill_revision,
                           expected_config_revision=expected_config_revision, expected_config_file=expected_config_file,
                           modes=modes, checkpoint=checkpoint, runtime_digest=runtime_digest,
                           expected_runtime_revision=expected_runtime_revision, removing=False)

    def uninstall(self, *, expected_skill_revision, expected_config_revision, expected_config_file,
                  expected_runtime_revision, checkpoint=lambda _: None):
        io.require(self.schema == RUNTIME_SCHEMA, 'RUNTIME_MODE_MISMATCH')
        return self._apply(None, {}, None, expected_skill_revision=expected_skill_revision,
                           expected_config_revision=expected_config_revision, expected_config_file=expected_config_file,
                           modes={}, checkpoint=checkpoint, runtime_digest=None,
                           expected_runtime_revision=expected_runtime_revision, removing=True)

    def _apply(self, release, payload, desired_config, *, expected_skill_revision,
               expected_config_revision, expected_config_file, modes, checkpoint,
               runtime_digest, expected_runtime_revision, removing):
        if self._workbuddy_url is not None and not removing:
            import host_workbuddy as workbuddy
            # Reject malformed targets before runtime activation or skill writes.
            workbuddy.validate_desired(desired_config, admitted_url=self._workbuddy_url)
        with self.locked():
            active = self._active()
            io.require(active is None or active['state'] in {'cleaned_aborted','cleaned'}, 'OPERATION_RECOVERY_REQUIRED')
            io.require(removing or ('runtime' in self.kinds) == (runtime_digest is not None), 'RUNTIME_MODE_MISMATCH')
            if 'runtime' in self.kinds and not removing:
                self.runtime_store.verify(runtime_digest)
            journal = {'schema': REMOVAL_SCHEMA if removing else self.schema, 'id': uuid.uuid4().hex, 'bindings': self.bindings,
                       'state': 'preparing', **{kind:self._prepare(kind) for kind in self.kinds}}
            if 'runtime' in self.kinds:
                journal['runtime']['tree'] = runtime_digest
                io.require(io.sha(io.encode(journal['runtime']['before'])) == expected_runtime_revision, 'PREFLIGHT_CHANGED')
            else:
                io.require(expected_runtime_revision is None, 'RUNTIME_MODE_MISMATCH')
            io.require(io.sha(io.encode(journal['skills']['before'])) == expected_skill_revision
                       and io.sha(io.encode(journal['config']['before'])) == expected_config_revision
                       and journal['config']['before_file'] == expected_config_file, 'PREFLIGHT_CHANGED')
            self._save(journal)
            self._write(self.active_path, {'id': journal['id']})
            try:
                checkpoint('preparing')
                if 'runtime' in self.kinds:
                    child = journal['runtime']
                    runtime_args = dict(expected_revision=expected_runtime_revision, transaction_id=child['id'],
                                        checkpoint=lambda p: checkpoint('runtime:' + p))
                    child['result'] = (self.runtime_store.deactivate(**runtime_args) if removing else
                                       self.runtime_store.activate(runtime_digest, **runtime_args))
                    journal['state'] = 'runtime'
                    self._save(journal)
                    checkpoint('runtime')
                skill_args = dict(transaction_id=journal['skills']['id'], checkpoint=lambda p: checkpoint('skills:' + p))
                journal['skills']['result'] = (self.skills.remove(expected_skill_revision, **skill_args) if removing else
                    self.skills.apply(release, payload, expected_skill_revision, modes=modes, **skill_args))
                journal['state'] = 'skills'
                self._save(journal)
                checkpoint('skills')
                journal['config']['result'] = self.config.apply(desired_config, expected_revision=expected_config_revision,
                    expected_file=expected_config_file, transaction_id=journal['config']['id'],
                    checkpoint=lambda p: checkpoint('config:' + p))
                journal['state'] = 'config'
                self._save(journal)
                checkpoint('config')
                self._verify_committed(journal)
                journal['state'] = 'committed'
                self._save(journal)
                checkpoint('committed')
                return {'status': 'resources_removed' if removing else 'resources_applied',
                        'operation': journal['id'], 'host_verified': False}
            except Exception:
                # Once global commit is durable, an observer failure must not undo it.
                if journal['state'] != 'committed':
                    self._abort(journal, checkpoint=lambda _: None)
                raise

    def _verify_committed(self, journal):
        for kind in self.kinds:
            child = journal[kind]
            result = child['result']
            io.require(type(result) is dict and type(result.get('revision')) is str, 'CHILD_RESULT_MISSING')
            engine = getattr(self, kind)
            with engine.locked():
                active = self._child_active(kind)
                if result.get('status') == 'unchanged':
                    io.require((active['id'] if active else None) == child['previous'], 'CHILD_TRANSACTION_CHANGED')
                    self._matches_before(kind, child)
                else:
                    io.require(active is not None and active['id'] == child['id'] and active['state'] == 'committed'
                               and engine._compensation(active).state() is None, 'CHILD_TRANSACTION_CHANGED')
                    self._bind_child(kind, child, active)
                    io.require(engine.snapshot()[0] == active['after'], 'CHILD_LEDGER_CHANGED')
                    if kind != 'config':
                        engine._check_tree(active['after'], active['before']['files'])
                    else:
                        io.require(engine._file()[1] == active['after_file'], 'CHILD_CONFIG_CHANGED')
                io.require(engine.snapshot()[1] == result['revision'], 'CHILD_LEDGER_CHANGED')
                if kind == 'runtime':
                    self._verify_target(journal)

    def _verify_target(self, journal):
        if journal['schema'] == REMOVAL_SCHEMA:
            io.require(io.read(self.runtime.root / 'runtime.json') is None
                       and self.runtime.snapshot()[0] == io.empty_ledger(), 'RUNTIME_POINTER_NOT_REMOVED')
        else:
            self._runtime_pointer_valid(journal['runtime']['tree'])

    def _abort(self, journal, checkpoint):
        journal['state'] = 'aborting'
        self._save(journal)
        for kind in reversed(self.kinds):
            engine = getattr(self, kind)
            child = journal[kind]
            active = self._child_active(kind)
            if active is not None and active['id'] == child['id']:
                self._bind_child(kind, child, active)
                if active['state'] == 'committed':
                    rollback = self.runtime_store.rollback_activation if kind == 'runtime' else engine.rollback
                    rollback(child['id'], expected_revision=io.sha(io.encode(active['after'])),
                             checkpoint=lambda p: checkpoint(kind + ':' + p))
                else:
                    # Refuse an unexpected cleanup or another task's terminal state.
                    allowed = {'preparing','staged','writing','recovered'} if kind == 'config' else {'staged','committing','recovered'}
                    io.require(active['state'] in allowed, 'CHILD_TRANSACTION_CHANGED')
                    if kind == 'runtime':
                        self.runtime_store.recover_activation(child['id'], checkpoint=lambda p: checkpoint(kind + ':' + p))
                    else:
                        engine.recover(checkpoint=lambda p: checkpoint(kind + ':' + p), transaction_id=child['id'])
            else:
                io.require((active['id'] if active else None) == child['previous'], 'CHILD_TRANSACTION_CHANGED')
            self._matches_before(kind, child)
            checkpoint('restored:' + kind)
        journal['state'] = 'aborted'
        self._save(journal)
        return {'status': 'resources_restored', 'operation': journal['id'], 'host_verified': False}

    def recover(self, checkpoint=lambda _: None):
        with self.locked():
            journal = self._active()
            if journal is None or journal['state'] in {'cleaned','cleaned_aborted'}:
                return {'status': 'nothing_pending', 'host_verified': False}
            if journal['state'] == 'aborted':
                for kind in self.kinds:
                    self._matches_before(kind, journal[kind])
                return self._cleanup_aborted(journal, checkpoint)
            if journal['state'] == 'committed':
                return self._cleanup(journal, checkpoint)
            return self._abort(journal, checkpoint)

    def _cleanup_aborted(self, journal, checkpoint):
        for kind in reversed(self.kinds):
            engine = getattr(self, kind)
            child = journal[kind]
            active = self._child_active(kind)
            self._matches_before(kind, child)
            if active is not None and active['id'] == child['id']:
                self._bind_child(kind, child, active)
                io.require(active['state'] in (cfg.TERMINAL | {'cleaning_committed','cleaning_recovered'} if kind == 'config' else {'committed','recovered'}),
                           'CHILD_TRANSACTION_CHANGED')
                io.require(active['state'].endswith('recovered') or engine._compensation(active).state() == 'completed',
                           'CHILD_COMPENSATION_MISSING')
                engine.cleanup(child['id'], expected_revision=io.sha(io.encode(child['before'])),
                               checkpoint=lambda p: checkpoint(kind + ':' + p))
            else:
                io.require((active['id'] if active else None) == child['previous'], 'CHILD_TRANSACTION_CHANGED')
            checkpoint('cleaned:' + kind)
        journal['state'] = 'cleaned_aborted'
        self._save(journal)
        return {'status': 'aborted_operation_cleaned', 'operation': journal['id'], 'host_verified': False}

    def _cleanup(self, journal, checkpoint):
        # Validate identity/revision before cleanup; a repeated cleanup may have
        # changed only the child journal's terminal cleanup state.
        for kind in reversed(self.kinds):
            child = journal[kind]
            engine = getattr(self, kind)
            active = self._child_active(kind)
            result = child['result']
            io.require(type(result) is dict, 'CHILD_RESULT_MISSING')
            if result.get('status') == 'unchanged':
                io.require((active['id'] if active else None) == child['previous'], 'CHILD_TRANSACTION_CHANGED')
                self._matches_before(kind, child)
                continue
            io.require(active is not None and active['id'] == child['id'], 'CHILD_TRANSACTION_CHANGED')
            self._bind_child(kind, child, active)
            io.require(engine._compensation(active).state() is None, 'CHILD_COMPENSATED')
            io.require(engine.snapshot()[1] == result.get('revision'), 'CHILD_LEDGER_CHANGED')
            if kind != 'config':
                engine._check_tree(active['after'], active['before']['files'])
            else:
                io.require(engine._file()[1] == active['after_file'], 'CHILD_CONFIG_CHANGED')
            if kind == 'runtime':
                self._verify_target(journal)
            engine.cleanup(child['id'], expected_revision=result['revision'],
                           checkpoint=lambda p: checkpoint(kind + ':' + p))
            checkpoint('cleaned:' + kind)
        journal['state'] = 'cleaned'
        self._save(journal)
        return {'status': 'operation_cleaned', 'operation': journal['id'], 'host_verified': False}
