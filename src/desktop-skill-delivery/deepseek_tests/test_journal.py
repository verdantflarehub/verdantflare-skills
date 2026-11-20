"""Durable ownership and crash recovery in isolated, private local scopes."""
from __future__ import annotations

import copy
from dataclasses import asdict, replace
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))
import host_deepseek as h
import host_deepseek_journal as j
import install_transaction as io
import test_host_deepseek as fixtures


def worker(request_path):
    request = json.loads(Path(request_path).read_bytes())
    identity = h.Identity(**request['identity'])
    store = j.OriginJournal(request['root'], identity)
    common = dict(writable=True, registered=['plugin'])
    def checkpoint(name):
        if name == request.get('crash'):
            os._exit(73)
    if request['action'] == 'prepare':
        plan, operation, revision = store.prepare(request['expected'], identity,
            h.encode(request['descriptor']), desired=request['desired'], checkpoint=checkpoint, **common)
        result = {'operation_id': operation, 'revision': revision, 'changed': plan.changed}
    else:
        result = store.resolve(request['expected'], request['operation_id'], identity,
            h.encode(request['descriptor']), cancel=request.get('cancel', False),
            checkpoint=checkpoint, **common)
    print(json.dumps(result))


class JournalTests(unittest.TestCase):
    def setUp(self):
        repo = TOOLS.parents[2]
        self.temp = tempfile.TemporaryDirectory(prefix='deepseek-journal-', dir=repo / '.tmp/agent-skill-delivery')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.f = fixtures.DeepSeekPlanningTests(); self.f.setUp()
        self.identity = self.f.identity
        self.f.desc['user'] = {}
        self.f.desc['base'] = {'servers': {'other': copy.deepcopy(self.f.other)}}
        self.f.desc['value'] = copy.deepcopy(self.f.desc['base'])
        self.before = copy.deepcopy(self.f.desc)
        self.store = j.OriginJournal(self.root, self.identity)
        self.common = dict(writable=True, registered=['plugin'])

    def prepare(self, desc=None, desired=None, expected=None):
        return self.store.prepare(expected or self.store.snapshot()[1], self.identity,
            h.encode(self.before if desc is None else desc),
            desired=self.f.target if desired is None else desired, **self.common)

    def installed(self, plan):
        result = self.f.installed(plan)
        result['user']['servers']['other'] = copy.deepcopy(self.f.other)
        return result

    def resolve(self, handle, desc, **kw):
        _, operation, revision = handle
        return self.store.resolve(revision, operation, self.identity, h.encode(desc), **self.common, **kw)

    def install(self):
        handle = self.prepare()
        self.after = self.installed(handle[0])
        self.resolve(handle, self.after)
        return handle

    def child(self, request):
        path = self.root / 'request.json'
        path.write_bytes(h.encode({'root': str(self.root), 'identity': asdict(self.identity), **request}))
        return subprocess.run([sys.executable, '-B', str(Path(__file__).resolve()), 'worker', str(path)],
                              capture_output=True, timeout=25)

    def test_install_update_remove_persists_across_new_instances(self):
        self.install()
        self.store = j.OriginJournal(self.root, self.identity)
        state, revision = self.store.snapshot()
        self.assertEqual(state['origin']['owned_sha256'], h.digest(self.f.target))
        new = h.desired_connection('https://studio.invalid/updated', 'VF_NEW')
        update = self.prepare(self.after, new)
        changed = copy.deepcopy(self.after); changed['revision'] += 1
        for layer in ('user', 'value'): changed[layer]['servers'][h.KEY] = copy.deepcopy(new)
        self.resolve(update, changed)
        self.store = j.OriginJournal(self.root, self.identity)
        _, revision = self.store.snapshot()
        handle = self.store.prepare(revision, self.identity, h.encode(changed), desired=None, **self.common)
        removed = copy.deepcopy(changed); removed['revision'] += 1
        removed['user'] = {}; del removed['value']['servers'][h.KEY]
        receipt = self.resolve(handle, removed)
        self.assertFalse(receipt['origin_record_retained'])
        self.assertIsNone(j.OriginJournal(self.root, self.identity).snapshot()[0]['origin'])
        self.assertTrue(receipt['persistent_ledger_written'])
        self.assertFalse(receipt['desktop_acceptance'])

    def test_write_ahead_contains_only_identity_and_hashes(self):
        handle = self.prepare()
        raw = self.store.path.read_bytes()
        for forbidden in (b'PRIVATE_TEST_VALUE', b'unrelated', b'https://', b'Authorization',
                          b'credential:', b'VF_STUDIO', b'other', b'servers'):
            self.assertNotIn(forbidden, raw)
        state, revision = self.store.snapshot()
        self.assertIsNone(state['origin'])
        self.assertIsNotNone(state['pending'])
        self.assertEqual(revision, handle[2])
        self.assertEqual(self.store.path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.store.directory.stat().st_mode & 0o777, 0o700)

    def test_pending_cannot_be_overwritten_and_stale_cas_is_rejected(self):
        before = self.store.snapshot()[1]
        self.prepare(expected=before)
        raw = self.store.path.read_bytes()
        with self.assertRaisesRegex(h.Conflict, 'DEEPSEEK_JOURNAL_STALE'): self.prepare(expected=before)
        with self.assertRaisesRegex(h.Conflict, 'DEEPSEEK_JOURNAL_PENDING'): self.prepare()
        self.assertEqual(raw, self.store.path.read_bytes())

    def test_failed_readback_preserves_exact_pending_bytes(self):
        handle = self.prepare(); after = self.installed(handle[0])
        for modify in (lambda d: d.update(revision=self.before['revision']),
                       lambda d: d['user']['servers']['other'].update(command='changed'),
                       lambda d: d['value']['servers'][h.KEY].update(enabled=False),
                       lambda d: d['user']['servers'][h.KEY].update(url='https://studio.invalid/wrong'),
                       lambda d: d['user']['servers'].update(intruder={'command': 'private'})):
            bad = copy.deepcopy(after); modify(bad)
            raw = self.store.path.read_bytes()
            with self.assertRaises(h.Conflict): self.resolve(handle, bad)
            self.assertEqual(raw, self.store.path.read_bytes())
        self.resolve(handle, after)

    def test_generation_restarts_find_and_protect_same_scope(self):
        self.install()
        changed = replace(self.identity, backend_generation='backend-restarted')
        restarted = j.OriginJournal(self.root, changed)
        self.assertEqual(restarted.path, self.store.path)
        raw = restarted.path.read_bytes()
        with self.assertRaisesRegex(h.Conflict, 'DEEPSEEK_ORIGIN_CONTEXT_CHANGED'):
            restarted.prepare(restarted.snapshot()[1], changed, h.encode(self.after),
                              desired=self.f.target, **self.common)
        self.assertEqual(raw, restarted.path.read_bytes())

    def test_cancel_requires_exact_original_observation_and_is_idempotent(self):
        handle = self.prepare()
        with self.assertRaisesRegex(h.Conflict, 'DEEPSEEK_JOURNAL_CANCEL_CHANGED'):
            self.resolve(handle, self.installed(handle[0]), cancel=True)
        self.assertEqual(self.resolve(handle, self.before, cancel=True)['outcome'], 'cancelled')
        raw = self.store.path.read_bytes()
        self.resolve(handle, self.before, cancel=True)
        self.assertEqual(raw, self.store.path.read_bytes())
        with self.assertRaisesRegex(h.Conflict, 'DEEPSEEK_JOURNAL_STALE'): self.resolve(handle, self.before)

    def test_finished_commit_is_idempotent_but_new_observation_is_not_relabelled(self):
        handle = self.install(); raw = self.store.path.read_bytes()
        self.resolve(handle, self.after)
        self.assertEqual(raw, self.store.path.read_bytes())
        newer = copy.deepcopy(self.after); newer['revision'] += 1
        with self.assertRaisesRegex(h.Conflict, 'DEEPSEEK_JOURNAL_STALE'): self.resolve(handle, newer)

    def test_noop_does_not_create_pending_or_change_ledger(self):
        self.install(); raw = self.store.path.read_bytes()
        plan, operation, revision = self.prepare(self.after)
        self.assertFalse(plan.changed); self.assertIsNone(operation)
        self.assertEqual(revision, self.store.snapshot()[1]); self.assertEqual(raw, self.store.path.read_bytes())

    def test_missing_origin_cannot_adopt_existing_vf(self):
        self.install(); self.store.path.unlink()
        with self.assertRaisesRegex(h.Conflict, 'DEEPSEEK_OWNERSHIP_CONFLICT'): self.prepare(self.after)
        self.assertFalse(self.store.path.exists())

    def test_private_mode_symlink_and_hardlink_fail_closed(self):
        self.prepare(); raw = self.store.path.read_bytes()
        os.chmod(self.store.path, 0o644)
        with self.assertRaisesRegex(io.Conflict, 'PRIVATE_FILE_REQUIRED'): self.store.snapshot()
        os.chmod(self.store.path, 0o600)
        link = self.root / 'hardlink'; os.link(self.store.path, link)
        with self.assertRaisesRegex(io.Conflict, 'PRIVATE_FILE_REQUIRED'): self.store.snapshot()
        link.unlink()
        saved = self.root / 'original'; self.store.path.rename(saved); self.store.path.symlink_to(saved)
        with self.assertRaisesRegex(io.Conflict, 'SYMLINK_REJECTED'): self.store.snapshot()
        self.assertEqual(raw, saved.read_bytes())

    def test_corrupt_unknown_and_duplicate_ledger_fields_are_rejected(self):
        self.prepare(); raw = self.store.path.read_bytes()
        for bad in (raw[:-1], b'{"schema":1,"schema":2}',
                    h.encode({**json.loads(raw), 'extra': 'unexpected'})):
            self.store.path.write_bytes(bad)
            with self.assertRaisesRegex(h.Conflict, 'DEEPSEEK_JOURNAL_INVALID'): self.store.snapshot()
        self.store.path.write_bytes(raw)

    def test_nonblocking_scope_lock(self):
        with self.store.locked():
            with self.assertRaisesRegex(h.Conflict, 'DEEPSEEK_JOURNAL_BUSY'):
                j.OriginJournal(self.root, self.identity).snapshot()

    def test_prepared_exit_recovers_by_real_readback_in_another_process(self):
        initial = self.store.snapshot()[1]
        cp = self.child({'action': 'prepare', 'expected': initial, 'descriptor': self.before,
                         'desired': self.f.target, 'crash': 'prepared'})
        self.assertEqual(cp.returncode, 73, cp.stderr.decode())
        state, revision = self.store.snapshot(); p = state['pending']
        self.assertIsNotNone(p); self.assertIsNone(state['origin'])
        after = self.installed(self.f.plan())
        request = {'action': 'resolve', 'expected': revision, 'operation_id': p['operation_id'],
                   'descriptor': after, 'crash': 'committed'}
        cp = self.child(request)
        self.assertEqual(cp.returncode, 73, cp.stderr.decode())
        self.assertIsNone(self.store.snapshot()[0]['pending'])
        cp = self.child({**request, 'crash': None})
        self.assertEqual(cp.returncode, 0, cp.stderr.decode())
        self.assertTrue(json.loads(cp.stdout)['origin_record_retained'])

    def test_cancelled_exit_is_recovered_without_mutation_in_another_process(self):
        handle = self.prepare()
        request = {'action': 'resolve', 'expected': handle[2], 'operation_id': handle[1],
                   'descriptor': self.before, 'cancel': True, 'crash': 'cancelled'}
        cp = self.child(request)
        self.assertEqual(cp.returncode, 73, cp.stderr.decode())
        cp = self.child({**request, 'crash': None})
        self.assertEqual(cp.returncode, 0, cp.stderr.decode())
        self.assertFalse(json.loads(cp.stdout)['origin_record_retained'])

    def test_update_and_remove_crash_at_prepare_and_commit_recover_in_new_processes(self):
        self.install()
        updated = copy.deepcopy(self.after); updated['revision'] += 1
        target = h.desired_connection('https://studio.invalid/new', 'NEW_REFERENCE')
        for layer in ('user', 'value'):
            updated[layer]['servers'][h.KEY] = copy.deepcopy(target)
        removed = copy.deepcopy(updated); removed['revision'] += 1
        removed['user'] = {}; del removed['value']['servers'][h.KEY]
        for desired, before, after in ((target, self.after, updated), (None, updated, removed)):
            cp = self.child({'action': 'prepare', 'expected': self.store.snapshot()[1],
                             'descriptor': before, 'desired': desired, 'crash': 'prepared'})
            self.assertEqual(cp.returncode, 73, cp.stderr.decode())
            state, revision = self.store.snapshot()
            old_owned = state['origin']['owned_sha256']
            request = {'action': 'resolve', 'expected': revision,
                       'operation_id': state['pending']['operation_id'],
                       'descriptor': after, 'crash': 'committed'}
            cp = self.child(request)
            self.assertEqual(cp.returncode, 73, cp.stderr.decode())
            state, revision = self.store.snapshot()
            self.assertIsNone(state['pending'])
            if desired is None:
                self.assertIsNone(state['origin'])
            else:
                self.assertNotEqual(state['origin']['owned_sha256'], old_owned)
                self.assertEqual(state['origin']['owned_sha256'], h.digest(desired))
            cp = self.child({**request, 'crash': None})
            self.assertEqual(cp.returncode, 0, cp.stderr.decode())

    def test_wrong_scope_and_inherited_changes_preserve_pending(self):
        handle = self.prepare(); after = self.installed(handle[0])
        raw = self.store.path.read_bytes()
        with self.assertRaisesRegex(h.Conflict, 'DEEPSEEK_JOURNAL_SCOPE_MISMATCH'):
            self.store.resolve(handle[2], handle[1], replace(self.identity, profile='different'),
                               h.encode(after), **self.common)
        after['base']['servers']['other']['command'] = 'changed'
        with self.assertRaisesRegex(h.Conflict, 'DEEPSEEK_ORIGIN_CONTEXT_CHANGED'):
            self.resolve(handle, after)
        self.assertEqual(raw, self.store.path.read_bytes())

    def test_extended_metadata_is_rejected_without_stripping_it(self):
        self.prepare()
        key = 'com.verdantflare.test' if sys.platform == 'darwin' else 'user.verdantflare.test'
        if hasattr(os, 'setxattr'):
            os.setxattr(self.store.path, key, b'preserve-this')
        else:
            cp = subprocess.run(['/usr/bin/xattr', '-w', key, 'preserve-this', str(self.store.path)],
                                capture_output=True, timeout=5)
            self.assertEqual(cp.returncode, 0)
        with self.assertRaisesRegex(io.Conflict, 'EXTENDED_METADATA_UNSUPPORTED'): self.store.snapshot()
        if hasattr(os, 'getxattr'):
            self.assertEqual(os.getxattr(self.store.path, key), b'preserve-this')
        else:
            cp = subprocess.run(['/usr/bin/xattr', '-p', key, str(self.store.path)],
                                capture_output=True, timeout=5)
            self.assertEqual(cp.returncode, 0); self.assertEqual(cp.stdout.strip(), b'preserve-this')

    def test_native_macos_acl_is_rejected_without_changing_it(self):
        self.prepare()
        cp = subprocess.run(['/bin/chmod', '+a', 'everyone allow read', str(self.store.path)],
                            capture_output=True, timeout=5)
        self.assertEqual(cp.returncode, 0)
        before = subprocess.run(['/bin/ls', '-lde', str(self.store.path)], capture_output=True, timeout=5)
        self.assertGreater(len(before.stdout.splitlines()), 1)
        with self.assertRaisesRegex(io.Conflict, 'EXTENDED_ACL_UNSUPPORTED'): self.store.snapshot()
        after = subprocess.run(['/bin/ls', '-lde', str(self.store.path)], capture_output=True, timeout=5)
        self.assertEqual(before.stdout, after.stdout)

    def test_windows_storage_is_not_claimed_from_posix_tests(self):
        with patch.object(j.os, 'name', 'nt'):
            with self.assertRaisesRegex(h.Conflict, 'WINDOWS_PRIVATE_STORAGE_UNVERIFIED'):
                j.OriginJournal(self.root, self.identity)

    def test_absent_unowned_remove_is_noop_but_foreign_names_are_not_adopted(self):
        initial = self.store.snapshot()[1]
        for user in ({}, {'servers': {}}):
            desc = copy.deepcopy(self.before); desc['user'] = user
            plan, operation, revision = self.store.prepare(initial, self.identity, h.encode(desc),
                                                           desired=None, **self.common)
            self.assertFalse(plan.changed); self.assertEqual(plan.mutation()[1], [])
            self.assertIsNone(operation); self.assertEqual(initial, revision)
            self.assertFalse(self.store.path.exists())
        for layer in ('user', 'base', 'value'):
            desc = copy.deepcopy(self.before)
            desc[layer].setdefault('servers', {})[h.KEY] = self.f.target
            with self.subTest(layer=layer), self.assertRaises(h.Conflict):
                self.store.prepare(initial, self.identity, h.encode(desc), desired=None, **self.common)
            self.assertFalse(self.store.path.exists())
        with self.assertRaisesRegex(h.Conflict, 'DEEPSEEK_REGISTERED_NAME_CONFLICT'):
            self.store.prepare(initial, self.identity, h.encode(self.before), desired=None,
                               writable=True, registered=[h.KEY])
        self.assertFalse(self.store.path.exists())

    def test_completed_remove_can_be_repeated_and_original_release_reinstalled(self):
        self.install()
        handle = self.store.prepare(self.store.snapshot()[1], self.identity,
                                    h.encode(self.after), desired=None, **self.common)
        removed = copy.deepcopy(self.after); removed['revision'] += 1
        removed['user'] = {}; del removed['value']['servers'][h.KEY]
        self.resolve(handle, removed)
        before = self.store.path.read_bytes()
        self.store = j.OriginJournal(self.root, self.identity)
        plan, operation, revision = self.store.prepare(self.store.snapshot()[1], self.identity,
                                                       h.encode(removed), desired=None, **self.common)
        self.assertFalse(plan.changed); self.assertIsNone(operation)
        self.assertEqual(before, self.store.path.read_bytes())
        install = self.store.prepare(revision, self.identity, h.encode(removed),
                                     desired=self.f.target, **self.common)
        reinstalled = copy.deepcopy(removed); reinstalled['revision'] += 1
        reinstalled['user']['servers'] = copy.deepcopy(reinstalled['value']['servers'])
        for layer in ('user', 'value'):
            reinstalled[layer]['servers'][h.KEY] = copy.deepcopy(self.f.target)
        self.resolve(install, reinstalled)
        self.assertEqual(self.store.snapshot()[0]['origin']['owned_sha256'], h.digest(self.f.target))


if __name__ == '__main__':
    if len(sys.argv) == 3 and sys.argv[1] == 'worker':
        worker(sys.argv[2])
    else:
        unittest.main()
