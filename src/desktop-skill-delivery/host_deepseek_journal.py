"""POSIX write-ahead origin storage; no host, credential or network client.

Prepare durably before submitting the returned native plan. Finish (including
after process exit) with a fresh authenticated native observation. A pending
record never authorizes replay. Local permissions protect cooperating callers;
hashes do not authenticate observations or resist malicious same-user writes.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict
import json
import os
from pathlib import Path
import re
import uuid

import config_transaction as private
import host_deepseek as h
import host_deepseek_origin as origin
import install_transaction as io

SCHEMA = 'vf.deepseek-origin.transaction/v1'
META = '.vf-deepseek-origin'
require = h.require


def _hash(value):
    return type(value) is str and re.fullmatch('[0-9a-f]{64}', value) is not None


def _origin(value):
    require(type(value) is dict and set(value) == {
        'identity', 'protected_digest', 'original_user_sha256', 'original_user_empty',
        'current_non_vf_user_sha256', 'owned_sha256'}, 'DEEPSEEK_JOURNAL_INVALID')
    try:
        record = origin.LayerOrigin(**{**value, 'identity': h.Identity(**value['identity'])})
        record.validate()
        return record
    except (TypeError, KeyError):
        raise h.Conflict('DEEPSEEK_JOURNAL_INVALID') from None


def _identity(value):
    try:
        require(type(value) is dict, 'DEEPSEEK_JOURNAL_INVALID')
        result = h.Identity(**value)
        result.validate()
        return result
    except TypeError:
        raise h.Conflict('DEEPSEEK_JOURNAL_INVALID') from None


def _scope(identity):
    identity.validate()
    # Generation deliberately excluded: a restarted backend must find the old
    # record and fail identity checks, not obtain a fresh unowned namespace.
    return h.digest([identity.product, identity.profile, identity.entry])


def _fingerprints(servers, *, normalized=False):
    return {h.digest(key): h.digest(h.normalize_server(value) if normalized else value)
            for key, value in servers.items() if key != h.KEY}


class OriginJournal:
    def __init__(self, root, identity):
        require(os.name == 'posix', 'WINDOWS_PRIVATE_STORAGE_UNVERIFIED')
        require(type(identity) is h.Identity, 'DEEPSEEK_IDENTITY_INVALID')
        self.scope = _scope(identity)
        root = Path(root).absolute()
        io.no_links(root)
        require(root.is_dir() and root.stat().st_uid == os.getuid(), 'ROOT_REQUIRED')
        self.base = root / META
        private.private_directory(self.base)
        self.directory = self.base / self.scope
        private.private_directory(self.directory)
        self.path = self.directory / 'state.json'

    def _guard(self):
        private.private_directory(self.base)
        private.private_directory(self.directory)
        private.private_file(self.path)

    @contextmanager
    def locked(self):
        import fcntl
        self._guard()
        path = self.directory / 'scope.lock'
        private.private_file(path)
        fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'r+b') as stream:
            private.private_file(path)
            require((path.stat().st_dev, path.stat().st_ino) ==
                    (os.fstat(fd).st_dev, os.fstat(fd).st_ino), 'LOCK_CHANGED')
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise h.Conflict('DEEPSEEK_JOURNAL_BUSY') from None
            try:
                yield
            finally:
                fcntl.flock(fd, fcntl.LOCK_UN)

    def _valid(self, state):
        require(type(state) is dict and set(state) == {
            'schema', 'scope', 'sequence', 'origin', 'pending', 'last'}, 'DEEPSEEK_JOURNAL_INVALID')
        require(state['schema'] == SCHEMA and state['scope'] == self.scope
                and type(state['sequence']) is int and 0 <= state['sequence'] < h.SAFE_INT,
                'DEEPSEEK_JOURNAL_INVALID')
        if state['origin'] is not None:
            require(_scope(_origin(state['origin']).identity) == self.scope,
                    'DEEPSEEK_JOURNAL_SCOPE_MISMATCH')
        p = state['pending']
        if p is not None:
            require(type(p) is dict and set(p) == {
                'operation_id', 'identity', 'protected_digest', 'before_digest', 'expected_revision',
                'target_sha256', 'original_user_sha256', 'original_user_empty', 'users', 'effective'},
                'DEEPSEEK_JOURNAL_INVALID')
            require(type(p['operation_id']) is str and re.fullmatch('[0-9a-f]{32}', p['operation_id'])
                    and _scope(_identity(p['identity'])) == self.scope
                    and type(p['expected_revision']) is int and 0 <= p['expected_revision'] < h.SAFE_INT
                    and all(_hash(p[k]) for k in ('protected_digest', 'before_digest', 'original_user_sha256'))
                    and (p['target_sha256'] is None or _hash(p['target_sha256']))
                    and type(p['original_user_empty']) is bool
                    and p['original_user_empty'] == (p['original_user_sha256'] == h.digest({})),
                    'DEEPSEEK_JOURNAL_INVALID')
            for key in ('users', 'effective'):
                require(type(p[key]) is dict and len(p[key]) <= 4096
                        and all(_hash(k) and _hash(v) for k, v in p[key].items()),
                        'DEEPSEEK_JOURNAL_INVALID')
            previous = state['origin']
            if previous is None:
                require(p['target_sha256'] is not None, 'DEEPSEEK_JOURNAL_INVALID')
            else:
                require(all(p[k] == previous[k] for k in (
                    'identity', 'protected_digest', 'original_user_sha256', 'original_user_empty')),
                    'DEEPSEEK_JOURNAL_INVALID')
        last = state['last']
        if last is not None:
            require(type(last) is dict and set(last) == {
                'operation_id', 'pending_revision', 'after_digest', 'outcome'}
                and type(last['operation_id']) is str and re.fullmatch('[0-9a-f]{32}', last['operation_id'])
                and _hash(last['pending_revision']) and _hash(last['after_digest'])
                and last['outcome'] in ('committed', 'cancelled'), 'DEEPSEEK_JOURNAL_INVALID')

    def _snapshot(self):
        self._guard()
        raw = io.read(self.path)
        if raw is None:
            state = {'schema': SCHEMA, 'scope': self.scope, 'sequence': 0,
                     'origin': None, 'pending': None, 'last': None}
        else:
            try:
                state = h.read(raw)
            except h.Conflict:
                raise h.Conflict('DEEPSEEK_JOURNAL_INVALID') from None
        self._valid(state)
        return state, h.digest(state)

    def snapshot(self):
        with self.locked():
            return self._snapshot()

    def _write(self, state):
        self._valid(state)
        self._guard()
        io.atomic(self.path, h.encode(state), mode=0o600)
        private.private_file(self.path)

    def request_operation_id(self, expected_state, request_id, target_sha256):
        """Bind a parent's prewritten intent without adding a disk format.

        The parent must persist these inputs and its resource-root bindings
        BEFORE starting any child. This digest is association, not authority.
        """
        require(_hash(expected_state) and type(request_id) is str
                and re.fullmatch('[0-9a-f]{32}', request_id) is not None
                and (target_sha256 is None or _hash(target_sha256)),
                'DEEPSEEK_REQUEST_INVALID')
        return h.digest(['vf.deepseek-origin.request/v1', self.scope, expected_state,
                         request_id, target_sha256])[:32]

    def request_handle(self, expected_state, request_id, target_sha256):
        """Find only this prebound request; never adopt an unrelated pending.

        not_prepared proves the ledger is unchanged, not the native host. Only
        the current pending or last completion can be located; old completions
        replaced by another operation are intentionally not relabelled.
        """
        operation_id = self.request_operation_id(expected_state, request_id, target_sha256)
        with self.locked():
            state, revision = self._snapshot()
            pending, last = state['pending'], state['last']
            if pending is not None and pending['operation_id'] == operation_id:
                require(pending['target_sha256'] == target_sha256, 'DEEPSEEK_REQUEST_MISMATCH')
                before = {**state, 'sequence': state['sequence'] - 1, 'pending': None}
                require(state['sequence'] > 0 and h.digest(before) == expected_state,
                        'DEEPSEEK_REQUEST_MISMATCH')
                return {'status': 'pending', 'operation_id': operation_id,
                        'pending_revision': revision}
            # Never locate an older completion underneath a newer pending.
            require(pending is None, 'DEEPSEEK_REQUEST_MISMATCH')
            if last is not None and last['operation_id'] == operation_id:
                return {'status': 'resolved', 'operation_id': operation_id,
                        'pending_revision': last['pending_revision'], 'outcome': last['outcome']}
            require(revision == expected_state, 'DEEPSEEK_REQUEST_MISMATCH')
            return {'status': 'not_prepared', 'operation_id': operation_id,
                    'pending_revision': None}

    def prepare(self, expected_state, identity, descriptor_raw, *, writable, registered, desired,
                checkpoint=lambda _: None, request_id=None):
        """Return (native plan, operation ID, pending CAS). No mutation is sent."""
        target_sha256 = None
        if desired is not None:
            h.validate_desired(desired)
            target_sha256 = h.digest(desired)
        operation_id = (uuid.uuid4().hex if request_id is None else
                        self.request_operation_id(expected_state, request_id, target_sha256))
        with self.locked():
            state, revision = self._snapshot()
            require(revision == expected_state, 'DEEPSEEK_JOURNAL_STALE')
            require(state['pending'] is None, 'DEEPSEEK_JOURNAL_PENDING')
            require(type(identity) is h.Identity and _scope(identity) == self.scope,
                    'DEEPSEEK_JOURNAL_SCOPE_MISMATCH')
            previous = None if state['origin'] is None else _origin(state['origin'])
            if previous is None:
                if desired is None:
                    # Absence is an idempotent removal, never a first install.
                    # The normal planner still rejects any unowned, inherited,
                    # effective or registered same-name connection.
                    plan = h.plan_update(identity, descriptor_raw, writable=writable,
                        registered=registered, desired=None, expected_owned_sha256=None)
                else:
                    plan = origin.plan_first_install(identity, descriptor_raw, writable=writable,
                        registered=registered, desired=desired)
            else:
                plan = origin.plan_owned(previous, identity, descriptor_raw, writable=writable,
                                         registered=registered, desired=desired)
            if not plan.changed:
                return plan, None, revision
            desc, effective, user, before, protected = h.observation(
                identity, descriptor_raw, writable, registered)
            require((None if plan.target is None else io.sha(plan.target)) == target_sha256,
                    'DEEPSEEK_REQUEST_MISMATCH')
            pending = {'operation_id': operation_id, 'identity': asdict(identity),
                       'protected_digest': protected, 'before_digest': before,
                       'expected_revision': plan.expected_revision,
                       'target_sha256': None if plan.target is None else io.sha(plan.target),
                       'original_user_sha256': h.digest(desc['user']) if previous is None
                           else previous.original_user_sha256,
                       'original_user_empty': desc['user'] == {} if previous is None
                           else previous.original_user_empty,
                       'users': _fingerprints(user), 'effective': _fingerprints(effective, normalized=True)}
            state.update(sequence=state['sequence'] + 1, pending=pending)
            self._write(state)
            checkpoint('prepared')
            return plan, pending['operation_id'], h.digest(state)

    def resolve(self, expected_pending, operation_id, identity, descriptor_raw, *, writable,
                registered, cancel=False, checkpoint=lambda _: None):
        """Verify actual readback; commit or cancel without replaying any mutation."""
        with self.locked():
            state, revision = self._snapshot()
            require(type(identity) is h.Identity and _scope(identity) == self.scope,
                    'DEEPSEEK_JOURNAL_SCOPE_MISMATCH')
            desc, effective, user, observed, protected = h.observation(
                identity, descriptor_raw, writable, registered)
            require(type(cancel) is bool, 'DEEPSEEK_JOURNAL_INVALID')
            outcome = 'cancelled' if cancel else 'committed'
            if state['pending'] is None:
                last = state['last']
                require(last is not None and last['operation_id'] == operation_id
                        and last['pending_revision'] == expected_pending and last['outcome'] == outcome
                        and last['after_digest'] == observed, 'DEEPSEEK_JOURNAL_STALE')
                return self._receipt(state, revision, outcome)
            p = state['pending']
            require(revision == expected_pending and p['operation_id'] == operation_id,
                    'DEEPSEEK_JOURNAL_STALE')
            require(identity == _identity(p['identity']) and protected == p['protected_digest'],
                    'DEEPSEEK_ORIGIN_CONTEXT_CHANGED')
            if cancel:
                require(observed == p['before_digest'], 'DEEPSEEK_JOURNAL_CANCEL_CHANGED')
            else:
                require(desc['revision'] > p['expected_revision'], 'DEEPSEEK_READBACK_REVISION')
                if p['target_sha256'] is None:
                    require(h.KEY not in user and h.KEY not in effective, 'DEEPSEEK_REMOVAL_NOT_EFFECTIVE')
                    require(h.digest(desc['user']) == p['original_user_sha256'],
                            'DEEPSEEK_ORIGINAL_USER_LAYER_NOT_RESTORED')
                    state['origin'] = None
                else:
                    require(h.KEY in user, 'DEEPSEEK_READBACK_VALUE')
                    h.validate_desired(user[h.KEY])
                    require(h.digest(user[h.KEY]) == p['target_sha256'], 'DEEPSEEK_READBACK_VALUE')
                    h.effective_matches(effective.get(h.KEY), user[h.KEY])
                    actual = _fingerprints(user)
                    require(all(actual.get(k) == v for k, v in p['users'].items()),
                            'DEEPSEEK_USER_LAYER_CHANGED')
                    normalized = _fingerprints(user, normalized=True)
                    require(all(k in p['effective'] and normalized[k] == p['effective'][k]
                                for k in set(actual) - set(p['users'])),
                            'DEEPSEEK_UNEXPECTED_MATERIALIZATION')
                    record = origin.LayerOrigin(identity, protected, p['original_user_sha256'],
                        p['original_user_empty'], origin._non_vf_user(desc), p['target_sha256'])
                    record.validate()
                    state['origin'] = asdict(record)
            state.update(sequence=state['sequence'] + 1, pending=None,
                         last={'operation_id': operation_id, 'pending_revision': expected_pending,
                               'after_digest': observed, 'outcome': outcome})
            self._write(state)
            checkpoint(outcome)
            return self._receipt(state, h.digest(state), outcome)

    @staticmethod
    def _receipt(state, revision, outcome):
        return {'state_revision': revision, 'outcome': outcome,
                'persistent_ledger_written': True, 'origin_record_retained': state['origin'] is not None,
                'mutation_executed_by_journal': False, 'desktop_acceptance': False, 'mcp_acceptance': False}
