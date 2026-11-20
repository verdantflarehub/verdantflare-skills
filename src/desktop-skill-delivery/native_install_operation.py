"""POSIX isolated runtime/skill/native management coordination.

Native uncertainty retains every resource and backup. No recovery path submits
a native mutation. Authentication, real host activity admission, credentials,
runtime lifecycle registration and Windows remain adapter integration work.
"""
from __future__ import annotations

from contextlib import contextmanager, ExitStack
from dataclasses import asdict
import os
from pathlib import Path
import re
import uuid

import config_transaction as private
import host_deepseek as h
import host_deepseek_origin as origin
from host_deepseek_operation import NativeOperation
from host_deepseek_journal import _origin, _scope
from install_operation import InstallOperation
import install_transaction as io
from runtime_tools.runtime_store import RuntimeStore

SCHEMA = 'vf.runtime-skill-native.operation/v1'
STATES = {'preparing', 'runtime', 'skills', 'native', 'committed',
          'aborting', 'aborted', 'cleaned', 'cleaned_aborted'}


def _hex(value, length):
    return type(value) is str and re.fullmatch('[0-9a-f]{' + str(length) + '}', value) is not None


def _target(release, payload, modes):
    io.require(type(payload) is dict and len(payload) <= io.MAX_FILES, 'INVALID_PAYLOAD')
    io.require(all(type(b) is bytes and len(b) <= io.MAX_FILE for b in payload.values())
               and sum(map(len, payload.values())) <= io.MAX_TOTAL, 'PAYLOAD_SIZE')
    io.require(modes is None or type(modes) is dict, 'INVALID_MODES')
    after = {'schema': io.SCHEMA, 'platform': os.name, 'release': release,
             'files': {n: io.sha(b) for n, b in payload.items()},
             'modes': {n: 0o644 for n in payload} if modes is None else dict(modes)}
    io.validate_ledger(after)
    folded = {n.casefold() for n in payload}
    io.require(not any('/'.join(n.split('/')[:i]).casefold() in folded
                       for n in payload for i in range(1, len(n.split('/')))), 'PATH_PREFIX_COLLISION')
    return after


class NativeInstallOperation(InstallOperation):
    """Reuse verified file-child recovery helpers, never file-config rollback."""

    def __init__(self, state_root, skill_root, runtime_root, native):
        io.require(os.name == 'posix', 'WINDOWS_PRIVATE_STORAGE_UNVERIFIED')
        io.require(type(native) is NativeOperation, 'DEEPSEEK_OPERATION_REQUIRED')
        roots = [Path(p).absolute() for p in (state_root, skill_root, runtime_root)]
        for root in roots:
            io.no_links(root)
            io.require(root.is_dir() and root.stat().st_uid == os.getuid(), 'ROOT_REQUIRED')
        roots = [p.resolve() for p in roots]
        io.require(all(not any(os.path.samefile(a, parent) for parent in (b, *b.parents))
                       and not any(os.path.samefile(b, parent) for parent in a.parents)
                       for i, a in enumerate(roots) for b in roots[i+1:]), 'ROOT_OVERLAP')
        io.require(native.journal.base.parent.resolve() == roots[0], 'DEEPSEEK_STATE_ROOT_MISMATCH')
        h.require(native.journal.scope == _scope(native.identity), 'DEEPSEEK_JOURNAL_SCOPE_MISMATCH')
        self.native = native
        self.bindings = [io.sha(os.fsencode(p)) for p in roots]
        self.bindings += [native.journal.scope, h.digest(asdict(native.identity))]
        self.meta = roots[0] / '.vf-native-install-operation'
        private.private_directory(self.meta)
        self.active_path = self.meta / 'active.json'
        self.skills = io.ManagedFiles(roots[1])
        self.runtime_store = RuntimeStore(roots[2])
        self.runtime = self.runtime_store.pointer
        self.kinds = ('runtime', 'skills')
        self.schema = SCHEMA

    def _active(self):
        raw = self._read(self.active_path)
        if raw is None:
            return None
        try:
            pointer = io.decode(raw)
        except (ValueError, UnicodeError):
            raise io.Conflict('INVALID_OPERATION_POINTER') from None
        io.require(type(pointer) is dict and set(pointer) == {'id'} and _hex(pointer['id'], 32),
                   'INVALID_OPERATION_POINTER')
        raw = self._read(self.meta / (pointer['id'] + '.json'))
        io.require(raw is not None, 'OPERATION_MISSING')
        try:
            journal = io.decode(raw)
        except (ValueError, UnicodeError):
            raise io.Conflict('INVALID_OPERATION') from None
        return self._validate_record(journal, pointer['id'])

    def _validate_record(self, journal, record_id):
        io.require(type(journal) is dict and set(journal) == {
            'schema', 'id', 'bindings', 'action', 'state', 'native', *self.kinds}
            and journal['schema'] == SCHEMA and journal['id'] == record_id
            and journal['bindings'] == self.bindings and type(journal['action']) is str
            and journal['action'] in ('apply', 'remove') and type(journal['state']) is str
            and journal['state'] in STATES, 'INVALID_OPERATION')
        n = journal['native']
        io.require(type(n) is dict and set(n) == {
            'request_id', 'expected_state', 'before_digest', 'target_sha256', 'changed'}
            and _hex(n['request_id'], 32) and _hex(n['expected_state'], 64)
            and _hex(n['before_digest'], 64) and (n['target_sha256'] is None or _hex(n['target_sha256'], 64))
            and type(n['changed']) is bool
            and ((n['target_sha256'] is None) == (journal['action'] == 'remove')), 'INVALID_NATIVE_CHILD')
        for kind in self.kinds:
            child = journal[kind]
            io.require(type(child) is dict and set(child) == {
                'id', 'previous', 'before', 'before_file', 'after', 'result',
                *({'tree'} if kind == 'runtime' else set())}
                and _hex(child['id'], 32) and (child['previous'] is None or _hex(child['previous'], 32))
                and child['before_file'] is None and (child['result'] is None or type(child['result']) is dict),
                'INVALID_CHILD')
            io.validate_ledger(child['before'])
            io.validate_ledger(child['after'])
            if kind == 'runtime':
                io.require(child['tree'] is None if journal['action'] == 'remove'
                           else _hex(child['tree'], 64), 'INVALID_CHILD')
            if journal['action'] == 'remove':
                io.require(child['after'] == io.empty_ledger(), 'INVALID_CHILD')
        return journal

    @contextmanager
    def _resources_locked(self):
        with ExitStack() as locks:
            for kind in self.kinds:
                locks.enter_context(getattr(self, kind).locked())
            yield

    def _native_observed(self):
        observed = self.native._observe()
        digest = h.observation(observed.identity, observed.descriptor, observed.writable,
                               list(observed.registered))[3]
        return observed, digest

    def preflight(self):
        with self.locked(), self.native.locked(), self._resources_locked():
            active = self._active()
            io.require(active is None or active['state'] in ('cleaned', 'cleaned_aborted'),
                       'OPERATION_RECOVERY_REQUIRED')
            for kind in self.kinds:
                engine = getattr(self, kind)
                engine._check_tree(engine.snapshot()[0])
            self._runtime_pointer_valid()
            state, revision = self.native.journal.snapshot()
            h.require(state['pending'] is None, 'DEEPSEEK_JOURNAL_PENDING')
            _, observed = self._native_observed()
            return {'expected_runtime_revision': self.runtime.snapshot()[1],
                    'expected_skill_revision': self.skills.snapshot()[1],
                    'expected_native_revision': revision, 'expected_native_observation': observed}

    def _bind_child(self, kind, child, active):
        super()._bind_child(kind, child, active)
        io.require(active['after'] == child['after'], 'CHILD_TARGET_CHANGED')

    def _verify_target(self, journal):
        if journal['action'] == 'remove':
            io.require(io.read(self.runtime.root / 'runtime.json') is None
                       and self.runtime.snapshot()[0] == io.empty_ledger(), 'RUNTIME_POINTER_NOT_REMOVED')
        else:
            self._runtime_pointer_valid(journal['runtime']['tree'])

    def _verify_children(self, journal):
        """Caller holds both file locks; reconstruct lost results by exact IDs."""
        for kind in self.kinds:
            child, engine = journal[kind], getattr(self, kind)
            active = engine._pending()
            if active is not None and active['id'] == child['id']:
                self._bind_child(kind, child, active)
                io.require(active['state'] == 'committed', 'NATIVE_LOCAL_INCOMPLETE')
                io.require(engine._compensation(active).state() is None, 'CHILD_COMPENSATED')
                io.require(engine.snapshot()[0] == child['after'], 'CHILD_LEDGER_CHANGED')
                engine._check_tree(child['after'], child['before']['files'])
                child['result'] = {'status': 'bytes_applied', 'transaction': child['id'],
                                   'revision': io.sha(io.encode(child['after'])), 'host_verified': False}
            else:
                io.require((active['id'] if active else None) == child['previous'], 'CHILD_TRANSACTION_CHANGED')
                self._matches_before(kind, child)
                io.require(child['before'] == child['after'], 'NATIVE_LOCAL_INCOMPLETE')
                child['result'] = {'status': 'unchanged', 'revision': engine.snapshot()[1], 'host_verified': False}
        self._verify_target(journal)

    def apply(self, release, payload, desired, *, runtime_digest, expected_runtime_revision,
              expected_skill_revision, expected_native_revision, expected_native_observation,
              modes=None, checkpoint=lambda _: None):
        io.require(release is not None and desired is not None, 'INSTALL_TARGET_REQUIRED')
        return self._run(release, payload, desired, runtime_digest=runtime_digest,
            expected_runtime_revision=expected_runtime_revision, expected_skill_revision=expected_skill_revision,
            expected_native_revision=expected_native_revision, expected_native_observation=expected_native_observation,
            modes=modes, checkpoint=checkpoint, removing=False)

    def uninstall(self, *, expected_runtime_revision, expected_skill_revision,
                  expected_native_revision, expected_native_observation, checkpoint=lambda _: None):
        return self._run(None, {}, None, runtime_digest=None, expected_runtime_revision=expected_runtime_revision,
            expected_skill_revision=expected_skill_revision, expected_native_revision=expected_native_revision,
            expected_native_observation=expected_native_observation, modes={}, checkpoint=checkpoint, removing=True)

    def _run(self, release, payload, desired, *, runtime_digest, expected_runtime_revision,
             expected_skill_revision, expected_native_revision, expected_native_observation, modes, checkpoint, removing):
        skill_after = _target(release, payload, modes)
        if desired is not None:
            h.validate_desired(desired)
        with self.locked(), self.native.locked():
            old = self._active()
            io.require(old is None or old['state'] in ('cleaned', 'cleaned_aborted'), 'OPERATION_RECOVERY_REQUIRED')
            state, revision = self.native.journal.snapshot()
            h.require(revision == expected_native_revision, 'DEEPSEEK_JOURNAL_STALE')
            h.require(state['pending'] is None, 'DEEPSEEK_JOURNAL_PENDING')
            observed, before = self._native_observed()
            h.require(before == expected_native_observation, 'DEEPSEEK_PRECONDITION_CHANGED')
            common = dict(writable=observed.writable, registered=list(observed.registered), desired=desired)
            if state['origin'] is None:
                plan = (h.plan_update(observed.identity, observed.descriptor, expected_owned_sha256=None, **common)
                        if removing else origin.plan_first_install(observed.identity, observed.descriptor, **common))
            else:
                plan = origin.plan_owned(_origin(state['origin']), observed.identity, observed.descriptor, **common)
            runtime_after = io.empty_ledger()
            if not removing:
                manifest = self.runtime_store.verify(runtime_digest)
                pointer = {'schema': 'vf.runtime-pointer/v1', 'tree_sha256': runtime_digest,
                           'release': manifest['release']}
                runtime_after = _target(manifest['release'], {'runtime.json': io.encode(pointer)}, None)
            journal = {'schema': SCHEMA, 'id': uuid.uuid4().hex, 'bindings': self.bindings,
                'action': 'remove' if removing else 'apply', 'state': 'preparing',
                'native': {'request_id': uuid.uuid4().hex, 'expected_state': revision,
                    'before_digest': before, 'target_sha256': None if desired is None else h.digest(desired),
                    'changed': plan.changed},
                'runtime': {**self._prepare('runtime'), 'tree': runtime_digest, 'after': runtime_after},
                'skills': {**self._prepare('skills'), 'after': skill_after}}
            io.require(io.sha(io.encode(journal['runtime']['before'])) == expected_runtime_revision
                       and io.sha(io.encode(journal['skills']['before'])) == expected_skill_revision, 'PREFLIGHT_CHANGED')
            self._save(journal)
            self._write(self.active_path, {'id': journal['id']})
            checkpoint('preparing')
            args = dict(expected_revision=expected_runtime_revision, transaction_id=journal['runtime']['id'],
                        checkpoint=lambda phase: checkpoint('runtime:' + phase))
            journal['runtime']['result'] = (self.runtime_store.deactivate(**args) if removing
                                            else self.runtime_store.activate(runtime_digest, **args))
            journal['state'] = 'runtime'; self._save(journal); checkpoint('runtime')
            args = dict(transaction_id=journal['skills']['id'], checkpoint=lambda phase: checkpoint('skills:' + phase))
            journal['skills']['result'] = (self.skills.remove(expected_skill_revision, **args) if removing else
                self.skills.apply(release, payload, expected_skill_revision, modes=modes, **args))
            journal['state'] = 'skills'; self._save(journal); checkpoint('skills')
            with self._resources_locked():
                self._verify_children(journal)
                self.native._apply_locked(revision, desired, request_id=journal['native']['request_id'],
                    expected_observation=before, checkpoint=lambda phase: checkpoint('native:' + phase))
                journal['state'] = 'native'; self._save(journal); checkpoint('native')
                self._verify_children(journal)
                journal['state'] = 'committed'; self._save(journal); checkpoint('committed')
            return self._receipt(journal, 'resources_removed' if removing else 'resources_applied')

    def _native_outcome(self, journal, cancel_native, checkpoint):
        n = journal['native']
        handle = self.native.journal.request_handle(n['expected_state'], n['request_id'], n['target_sha256'])
        if handle['status'] == 'not_prepared':
            observed, current = self._native_observed()
            h.require(current == n['before_digest'], 'DEEPSEEK_PRECONDITION_CHANGED')
            if not n['changed']:
                _, effective, user, _, _ = h.observation(observed.identity, observed.descriptor,
                    observed.writable, list(observed.registered))
                if n['target_sha256'] is None:
                    h.require(h.KEY not in user and h.KEY not in effective, 'DEEPSEEK_NOOP_TARGET_MISMATCH')
                else:
                    h.require(h.KEY in user and h.digest(user[h.KEY]) == n['target_sha256'],
                              'DEEPSEEK_NOOP_TARGET_MISMATCH')
                    h.validate_desired(user[h.KEY])
                    h.effective_matches(effective.get(h.KEY), user[h.KEY])
            return 'before' if n['changed'] else 'unchanged'
        cancel = cancel_native or handle.get('outcome') == 'cancelled'
        receipt = self.native._recover_request_locked(n['expected_state'], n['request_id'], n['target_sha256'],
            cancel=cancel, checkpoint=lambda phase: checkpoint('native:' + phase))
        return 'before' if receipt['outcome'] == 'cancelled' else 'committed'

    def recover(self, *, cancel_native=False, checkpoint=lambda _: None):
        io.require(type(cancel_native) is bool, 'INVALID_CANCEL_MODE')
        with self.locked(), self.native.locked():
            journal = self._active()
            if journal is None or journal['state'] in ('cleaned', 'cleaned_aborted'):
                return {'status': 'nothing_pending', 'host_verified': False, 'desktop_acceptance': False,
                        'mcp_acceptance': False, 'installer_acceptance': False}
            outcome = self._native_outcome(journal, cancel_native, checkpoint)
            if journal['state'] in ('aborting', 'aborted') or outcome == 'before':
                io.require(journal['state'] != 'committed' and outcome in ('before', 'unchanged'),
                           'NATIVE_COMMIT_CANNOT_ABORT')
                if journal['state'] == 'aborted':
                    result = self._cleanup_aborted(journal, checkpoint)
                else:
                    result = self._abort(journal, checkpoint)
                return {**result, 'desktop_acceptance': False, 'mcp_acceptance': False, 'installer_acceptance': False}
            try:
                with self._resources_locked():
                    self._verify_children(journal)
                    journal['state'] = 'committed'; self._save(journal); checkpoint('committed')
            except io.Conflict as error:
                if str(error) != 'NATIVE_LOCAL_INCOMPLETE' or outcome != 'unchanged':
                    raise
                io.require(journal['state'] != 'committed', 'NATIVE_COMMIT_CANNOT_ABORT')
                return {**self._abort(journal, checkpoint), 'desktop_acceptance': False,
                        'mcp_acceptance': False, 'installer_acceptance': False}
            return {**self._cleanup(journal, checkpoint), 'desktop_acceptance': False,
                    'mcp_acceptance': False, 'installer_acceptance': False}

    @staticmethod
    def _receipt(journal, status):
        return {'status': status, 'operation': journal['id'], 'host_verified': False,
                'desktop_acceptance': False, 'mcp_acceptance': False, 'installer_acceptance': False}
