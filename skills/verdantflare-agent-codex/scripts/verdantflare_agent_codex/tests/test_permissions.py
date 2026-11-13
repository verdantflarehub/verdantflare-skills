import json
import os
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest

from verdantflare_agent_codex.errors import BridgeError
from verdantflare_agent_codex.permissions import (
    assert_effective_selection, assert_selection_current, read_selection,
    selection_from_atoms, assert_bound_processes, assert_supported_thread, read_turn_permissions, rollout_path,
)


def atoms(mode):
    return {'permission-selection-by-host-id:local': {'kind': 'agent-mode', 'agentMode': mode},
            'agent-mode-by-host-id': {'local': mode}}


class PermissionTests(unittest.TestCase):
    def test_native_context_is_bound_to_exact_turn_not_requested_ui_parameters(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'rollout.jsonl'
            entries = [
                {'type': 'turn_context', 'payload': {'turn_id': 'previous', 'approval_policy': 'never',
                    'approvals_reviewer': 'user', 'sandbox_policy': {'type': 'danger-full-access'}}},
                {'type': 'response_item', 'payload': {'permissions': ':danger-full-access'}},
                {'type': 'turn_context', 'payload': {'turn_id': 'current', 'approval_policy': 'on-request',
                    'approvals_reviewer': 'user', 'sandbox_policy': {'type': 'workspace-write', 'network_access': False}}},
            ]
            path.write_bytes(('\n'.join(json.dumps(e) for e in entries)+'\n{"partial":').encode())
            actual = read_turn_permissions(path, 'current')
            self.assertEqual('workspaceWrite', actual['sandboxPolicy']['type'])
            with self.assertRaises(BridgeError):
                assert_effective_selection(selection_from_atoms(atoms('full-access')), actual)
            self.assertIsNone(read_turn_permissions(path, 'missing'))

    def test_native_index_rejects_other_workspace_and_paths_outside_codex_store(self):
        with tempfile.TemporaryDirectory() as directory:
            home=Path(directory); sessions=home/'sessions'; sessions.mkdir()
            log=sessions/'rollout.jsonl'; log.write_text('')
            db=home/'state_5.sqlite'
            with closing(sqlite3.connect(db)) as connection:
                connection.execute('CREATE TABLE threads(id TEXT, rollout_path TEXT, cwd TEXT)')
                connection.execute('INSERT INTO threads VALUES (?,?,?)', ('thread',str(log),str(home)))
                connection.commit()
            self.assertEqual(log.resolve(), rollout_path('thread',str(home),home))
            if os.name == 'nt':
                with closing(sqlite3.connect(db)) as connection:
                    connection.execute('UPDATE threads SET rollout_path=?,cwd=?',
                                       ('\\\\?\\'+str(log), '\\\\?\\'+str(home)))
                    connection.commit()
                self.assertEqual(log.resolve(), rollout_path('thread',str(home),home))
            with self.assertRaises(BridgeError):rollout_path('thread',str(home/'other'),home)
            with closing(sqlite3.connect(db)) as connection:
                connection.execute('UPDATE threads SET rollout_path=?',(str(home/'outside.jsonl'),))
                connection.commit()
            with self.assertRaises(BridgeError):rollout_path('thread',str(home),home)

    def test_global_preference_never_overrides_custom_remote_or_unknown_thread(self):
        known = {'latestThreadSettings': {'permissions': ':workspace'},
                 'currentPermissions': {'activePermissionProfile': {'id': ':workspace'}}}
        assert_supported_thread(known)
        for value in [{}, known | {'mode': 'durable'},
                      known | {'environments': [{'environmentId': 'remote'}]},
                      known | {'latestThreadSettings': {'permissions': 'custom-profile'}}]:
            with self.subTest(value=value), self.assertRaises(BridgeError):
                assert_supported_thread(value)

    def test_host_binding_rejects_custom_profiles_ambiguous_and_unreadable_processes(self):
        root = str(Path.cwd() / 'data-root')
        exe = str(Path.cwd() / 'install/Code.exe')
        good = {'ExecutablePath': exe, 'CommandLine': f'Code.exe --user-data-dir="{root}" --type=renderer'}
        assert_bound_processes([good], root)
        for rows in [[], [None], [{'ExecutablePath': exe, 'CommandLine': None}],
                     [good, good | {'CommandLine': 'Code.exe --profile test'}],
                     [good, good | {'CommandLine': f'Code.exe --user-data-dir="{root}-other"'}]]:
            with self.subTest(rows=rows), self.assertRaises(BridgeError):
                assert_bound_processes(rows, root)

    def test_missing_unknown_and_custom_state_never_grant_access(self):
        for value in [None, {}, atoms('unknown'),
                      {'permission-selection-by-host-id:local': {'kind': 'custom'}},
                      {'permission-selection-by-host-id:local': {'kind': 'profile', 'profileId': ':danger-full-access'}}]:
            with self.subTest(value=value), self.assertRaises(BridgeError):
                selection_from_atoms(value)

    def test_conflicting_ui_generations_are_not_resolved_by_choosing_full_access(self):
        value = atoms('full-access')
        value['agent-mode-by-host-id']['local'] = 'auto'
        with self.assertRaises(BridgeError) as caught:
            selection_from_atoms(value)
        self.assertEqual('PERMISSIONS_CONFLICT', caught.exception.code)

    def test_full_access_and_restriction_are_both_checked(self):
        full = selection_from_atoms(atoms('full-access'))
        restricted = selection_from_atoms(atoms('auto'))
        actual = full.request_fields() | {'sandboxPolicy': {'type': 'dangerFullAccess'}}
        self.assertEqual(actual, assert_effective_selection(full, actual))
        with self.assertRaises(BridgeError):
            assert_effective_selection(restricted, actual)
        actual = restricted.request_fields() | {'sandboxPolicy': {'type': 'workspaceWrite', 'networkAccess': False}}
        self.assertEqual(actual, assert_effective_selection(restricted, actual))
        with self.assertRaises(BridgeError):
            assert_effective_selection(full, actual)

    def test_approval_reviewer_and_sandbox_must_match_not_just_profile(self):
        selected = selection_from_atoms(atoms('auto'))
        actual = selected.request_fields() | {'sandboxPolicy': {'type': 'workspaceWrite'}}
        for override in [{'approvalsReviewer': 'guardian_subagent'}, {'approvalPolicy': 'never'},
                         {'sandboxPolicy': {'type': 'dangerFullAccess'}}, {'approvalsReviewer': None}]:
            with self.subTest(override=override), self.assertRaises(BridgeError):
                assert_effective_selection(selected, actual | override)

    def test_reads_do_not_create_database_and_changes_invalidate_selection(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'state.vscdb'
            with self.assertRaises(BridgeError):
                read_selection(path)
            self.assertFalse(path.exists())
            with closing(sqlite3.connect(path)) as connection:
                connection.execute('CREATE TABLE ItemTable(key TEXT PRIMARY KEY, value TEXT)')
                connection.execute('INSERT INTO ItemTable VALUES (?, ?)',
                                   ('openai.chatgpt', json.dumps({'persisted-atom-state': atoms('full-access')})))
                connection.commit()
            selected = read_selection(path)
            assert_selection_current(path, selected)
            with closing(sqlite3.connect(path)) as connection:
                connection.execute('UPDATE ItemTable SET value=?',
                                   (json.dumps({'persisted-atom-state': atoms('auto')}),))
                connection.commit()
            with self.assertRaises(BridgeError) as caught:
                assert_selection_current(path, selected)
            self.assertEqual('PERMISSIONS_CHANGED', caught.exception.code)


if __name__ == '__main__':
    unittest.main()
