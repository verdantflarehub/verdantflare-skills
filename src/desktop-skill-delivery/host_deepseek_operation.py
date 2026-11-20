"""Serialize native mutation with durable origin preparation and readback.

Transport identity/authentication and activity admission belong to the caller.
This module never discovers hosts or handles YAML/credentials. An uncertain
submission leaves pending evidence; recovery observes and never replays writes.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import os
import re
import threading
from typing import Protocol

import config_transaction as private
import host_deepseek as h
from host_deepseek_journal import OriginJournal


@dataclass(frozen=True, repr=False)
class Observation:
    identity: h.Identity
    descriptor: bytes
    writable: bool
    registered: tuple[str, ...]


class NativeTransport(Protocol):
    def observe(self) -> Observation:
        """Read from an already authenticated, identity-bound native session."""
        ...

    def mutate(self, mutation: tuple[str, list[dict], int]) -> None:
        """Submit exactly once; error or lost reply leaves outcome uncertain."""
        ...


class NativeOperation:
    def __init__(self, journal: OriginJournal, identity: h.Identity, transport: NativeTransport):
        h.require(type(journal) is OriginJournal, 'DEEPSEEK_OPERATION_JOURNAL_REQUIRED')
        h.require(type(identity) is h.Identity, 'DEEPSEEK_IDENTITY_INVALID')
        identity.validate()
        self.journal, self.identity, self.transport = journal, identity, transport
        self._lock_owner = None
        self._lock_fd = None

    @contextmanager
    def locked(self):
        """One native execution/recovery per scope, including in-flight I/O."""
        import fcntl
        self.journal._guard()
        path = self.journal.directory / 'execution.lock'
        private.private_file(path)
        fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'r+b') as stream:
            private.private_file(path)
            h.require((path.stat().st_dev, path.stat().st_ino) ==
                      (os.fstat(fd).st_dev, os.fstat(fd).st_ino), 'LOCK_CHANGED')
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise h.Conflict('DEEPSEEK_OPERATION_BUSY') from None
            try:
                self._lock_owner = (os.getpid(), threading.get_ident())
                self._lock_fd = fd
                yield
            finally:
                self._lock_owner = self._lock_fd = None
                fcntl.flock(fd, fcntl.LOCK_UN)

    def _require_locked(self):
        h.require(self._lock_owner == (os.getpid(), threading.get_ident())
                  and self._lock_fd is not None, 'DEEPSEEK_OPERATION_LOCK_REQUIRED')

    def _observe(self):
        try:
            value = self.transport.observe()
        except Exception:
            # Native diagnostics may embed paths, connection values or secrets.
            raise h.Conflict('DEEPSEEK_NATIVE_OBSERVATION_FAILED') from None
        h.require(type(value) is Observation and type(value.identity) is h.Identity
                  and value.identity == self.identity, 'DEEPSEEK_OPERATION_IDENTITY_CHANGED')
        h.require(type(value.descriptor) is bytes and type(value.writable) is bool
                  and type(value.registered) is tuple and all(type(k) is str for k in value.registered),
                  'DEEPSEEK_OPERATION_OBSERVATION_INVALID')
        return value

    def apply(self, expected_state, desired, *, checkpoint=lambda _: None, request_id=None,
              expected_observation=None):
        """Persist -> reobserve -> validate -> submit once -> read -> resolve.

        All failures after prepare intentionally retain pending. In particular,
        an exception from mutate must never trigger retry or implicit cancel.
        """
        if request_id is not None:
            if desired is not None:
                h.validate_desired(desired)
            self.journal.request_operation_id(expected_state, request_id,
                None if desired is None else h.digest(desired))
        with self.locked():
            return self._apply_locked(expected_state, desired, checkpoint=checkpoint,
                request_id=request_id, expected_observation=expected_observation)

    def _apply_locked(self, expected_state, desired, *, checkpoint=lambda _: None, request_id=None,
                      expected_observation=None):
        """Parent composition only; the same thread must already hold locked()."""
        self._require_locked()
        if expected_observation is not None:
            h.require(type(expected_observation) is str and
                      re.fullmatch('[0-9a-f]{64}', expected_observation) is not None,
                      'DEEPSEEK_OPERATION_OBSERVATION_INVALID')
        before = self._observe()
        if expected_observation is not None:
            observed = h.observation(before.identity, before.descriptor, before.writable,
                                     list(before.registered))[3]
            h.require(observed == expected_observation, 'DEEPSEEK_PRECONDITION_CHANGED')
        plan, operation_id, pending = self.journal.prepare(expected_state, self.identity,
            before.descriptor, writable=before.writable, registered=list(before.registered),
            desired=desired, checkpoint=checkpoint, request_id=request_id)
        if not plan.changed:
            return {'status': 'observed_no_change', 'mutation_submitted': False,
                    'state_revision': pending, 'desktop_acceptance': False, 'mcp_acceptance': False}
        fresh = self._observe()
        mutation = h.validate_submit(plan, fresh.identity, fresh.descriptor,
            writable=fresh.writable, registered=list(fresh.registered))
        checkpoint('validated')
        try:
            self.transport.mutate(mutation)
        except Exception:
            raise h.Conflict('DEEPSEEK_NATIVE_MUTATION_UNCERTAIN') from None
        checkpoint('submitted')
        after = self._observe()
        receipt = self.journal.resolve(pending, operation_id, after.identity, after.descriptor,
            writable=after.writable, registered=list(after.registered), checkpoint=checkpoint)
        return {**receipt, 'status': 'native_configuration_verified', 'operation_id': operation_id,
                'pending_revision': pending, 'mutation_submitted': True}

    def recover(self, expected_pending, operation_id, *, cancel=False, checkpoint=lambda _: None):
        """Read actual native state and resolve the original handle, never mutate."""
        with self.locked():
            return self._recover(expected_pending, operation_id, cancel, checkpoint)

    def _recover(self, expected_pending, operation_id, cancel, checkpoint):
        self._require_locked()
        observed = self._observe()
        receipt = self.journal.resolve(expected_pending, operation_id, observed.identity,
            observed.descriptor, writable=observed.writable, registered=list(observed.registered),
            cancel=cancel, checkpoint=checkpoint)
        return {**receipt, 'status': 'native_recovery_verified', 'operation_id': operation_id,
                'pending_revision': expected_pending, 'mutation_submitted': False}

    def recover_request(self, expected_state, request_id, target_sha256, *, cancel=False,
                        checkpoint=lambda _: None):
        """Recover from prewritten intent even when no child handle returned.

        Hold the execution lock across lookup and native readback. No native
        state claim is made if the child never durably prepared this request.
        """
        h.require(type(cancel) is bool, 'DEEPSEEK_JOURNAL_INVALID')
        with self.locked():
            return self._recover_request_locked(expected_state, request_id, target_sha256,
                                                 cancel=cancel, checkpoint=checkpoint)

    def _recover_request_locked(self, expected_state, request_id, target_sha256, *, cancel=False,
                                checkpoint=lambda _: None):
        self._require_locked()
        h.require(type(cancel) is bool, 'DEEPSEEK_JOURNAL_INVALID')
        handle = self.journal.request_handle(expected_state, request_id, target_sha256)
        if handle['status'] == 'not_prepared':
            return {**handle, 'status': 'native_request_not_prepared',
                    'mutation_submitted': False, 'native_configuration_verified': False,
                    'desktop_acceptance': False, 'mcp_acceptance': False}
        return self._recover(handle['pending_revision'], handle['operation_id'], cancel, checkpoint)
