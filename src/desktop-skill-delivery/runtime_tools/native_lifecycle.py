"""Read-only native-scope accounting for the closed POSIX lifecycle facade.

The caller holds the parent, execution, source-journal and skill locks. This
module never prepares, resolves or cancels a native operation or edits config.
It does not admit real desktop authentication or discover host activity.
"""
from __future__ import annotations

import re
import os
import stat

import host_deepseek as h
import host_deepseek_origin as origin
from host_deepseek_journal import _origin
import install_transaction as tx
import config_transaction as private
import native_install_operation as native

require = tx.require


def is_native(scope):
    return type(scope) is native.NativeInstallOperation and scope.schema == native.SCHEMA


def guard(scope):
    # Journal._guard() also initializes missing directories. A lifecycle read
    # must fail before that API can recreate a removed source namespace.
    for path in (scope.native.journal.base, scope.native.journal.directory):
        tx.no_links(path)
        require(path.is_dir(), 'NATIVE_SOURCE_DIRECTORY_MISSING')
        info = path.stat()
        require(info.st_uid == os.getuid() and stat.S_IMODE(info.st_mode) == 0o700,
                'PRIVATE_DIRECTORY_REQUIRED')
        private.no_extended_metadata(path)


def _observed(scope):
    scope.native._require_locked()
    state, revision = scope.native.journal._snapshot()
    value = scope.native._observe()
    desc, effective, user, digest, _ = h.observation(value.identity, value.descriptor,
        value.writable, list(value.registered))
    return state, revision, value, effective, user, digest


def baseline_valid(baseline):
    require(type(baseline) is dict and set(baseline) == {
        'skills_revision', 'native_revision', 'native_observation'}
        and all(native._hex(value, 64) for value in baseline.values()), 'INVALID_INITIAL_SCOPE')


def initial(scope):
    state, revision, _, effective, user, digest = _observed(scope)
    require(state['sequence'] == 0 and all(state[k] is None for k in ('origin', 'pending', 'last'))
            and h.KEY not in user and h.KEY not in effective, 'FRESH_NATIVE_SCOPE_REQUIRED')
    return {'skills_revision': scope.skills.snapshot()[1], 'native_revision': revision,
            'native_observation': digest}


def sources(scopes):
    """Reject source namespaces outside the explicitly registered closed set."""
    bases = {}
    for scope in scopes:
        if is_native(scope):
            journal = scope.native.journal
            bases.setdefault(journal.base, set()).add(journal.scope)
            guard(scope)
            for path in journal.directory.iterdir():
                require(path.name in {'state.json', 'scope.lock', 'execution.lock'},
                        'UNKNOWN_NATIVE_SOURCE_CONTENT')
                private.private_file(path)
    for base, known in bases.items():
        require({p.name for p in base.iterdir()} == known, 'UNREGISTERED_NATIVE_SOURCE')


def _terminal(scope, active, state, revision, value, user, effective, digest):
    n = active['native']
    require(state['pending'] is None, 'NATIVE_SOURCE_RECOVERY_REQUIRED')
    op_id = scope.native.journal.request_operation_id(n['expected_state'], n['request_id'], n['target_sha256'])
    last = state['last']
    if last is not None and last['operation_id'] == op_id:
        require(last['after_digest'] == digest, 'LIFECYCLE_NATIVE_CHANGED')
        outcome = last['outcome']
    else:
        require(revision == n['expected_state'] and digest == n['before_digest'],
                'LIFECYCLE_NATIVE_CHANGED')
        outcome = 'before' if n['changed'] else 'unchanged'
    if active['state'] == 'cleaned_aborted':
        require(outcome in ('before', 'cancelled', 'unchanged'), 'NATIVE_COMMIT_CANNOT_ABORT')
    else:
        require(outcome in ('committed', 'unchanged'), 'LIFECYCLE_NATIVE_INCOMPLETE')
        target = n['target_sha256']
        if target is None:
            require(h.KEY not in user and h.KEY not in effective and state['origin'] is None,
                    'LIFECYCLE_NATIVE_TARGET_CHANGED')
        else:
            require(h.KEY in user and h.digest(user[h.KEY]) == target and state['origin'] is not None,
                    'LIFECYCLE_NATIVE_TARGET_CHANGED')
            h.validate_desired(user[h.KEY])
            h.effective_matches(effective.get(h.KEY), user[h.KEY])
    if state['origin'] is not None:
        # Also check protected and non-VF layers against the persisted origin.
        plan = origin.plan_owned(_origin(state['origin']), value.identity, value.descriptor,
            writable=value.writable, registered=list(value.registered), desired=user.get(h.KEY))
        require(not plan.changed, 'LIFECYCLE_NATIVE_CHANGED')
    child = active['skills']
    ledger = child['before'] if active['state'] == 'cleaned_aborted' else child['after']
    require(scope.skills.snapshot()[0] == ledger, 'LIFECYCLE_RESOURCE_CHANGED')
    scope.skills._check_tree(ledger, {*child['before']['files'], *child['after']['files']})
    if active['state'] == 'cleaned':
        result = child['result']
        require(type(result) is dict and result.get('revision') == tx.sha(tx.encode(ledger)),
                'CHILD_RESULT_MISSING')


def references(scope, key, baseline, store):
    """Return scope refs/metadata/blockers/activation IDs without writing."""
    refs, metadata, blockers, children = {}, {}, [], set()
    state, revision, value, effective, user, digest = _observed(scope)
    prefix = 'scope/' + key
    metadata[prefix + '/native/revision'] = revision
    metadata[prefix + '/native/observation'] = digest
    active = scope._active()
    if active is None:
        require(scope.skills.snapshot()[1] == baseline['skills_revision']
                and revision == baseline['native_revision'] and digest == baseline['native_observation'],
                'INITIAL_SCOPE_CHANGED')
    if state['pending'] is not None:
        blockers.append(prefix + '/native/pending')
    if active is not None and active['state'] in ('cleaned', 'cleaned_aborted'):
        _terminal(scope, active, state, revision, value, user, effective, digest)
    for path in sorted(scope.meta.iterdir()):
        if path.name == 'scope.lock':
            continue
        raw = scope._read(path)
        require(raw is not None, 'OPERATION_MISSING')
        metadata[prefix + '/' + path.name] = tx.sha(raw)
        if path.name == 'active.json':
            continue
        require(re.fullmatch('[0-9a-f]{32}\\.json', path.name), 'UNKNOWN_OPERATION_CONTENT')
        record = scope._validate_record(tx.decode(raw), path.stem)
        child = record['runtime']
        children.add(child['id'])
        if active is None or record['id'] != active['id']:
            require(record['state'] in ('cleaned', 'cleaned_aborted'), 'ORPHAN_OPERATION_REQUIRES_RECOVERY')
        else:
            if record['action'] != 'remove':
                store._verify(child['tree'])
                refs.setdefault(child['tree'], []).append(prefix)
            if record['state'] not in ('cleaned', 'cleaned_aborted'):
                blockers.append('operation/' + key)
    return refs, metadata, blockers, children
