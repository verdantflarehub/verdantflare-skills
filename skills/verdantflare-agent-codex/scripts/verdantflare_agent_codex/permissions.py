"""Read explicit host selections without accepting caller-supplied permissions.

This reader is not a host-discovery mechanism: the caller must bind the database
to the live owner before using its result for a submission. Missing/ambiguous
state is an error, never an invitation to fall back to full access.
"""
from copy import deepcopy
from contextlib import closing
from dataclasses import dataclass
import json
import os
from pathlib import Path
import sqlite3
import subprocess

from .errors import BridgeError


# Mirrors the native built-in permission catalog. Custom/profile/server-default
# selections require the host's resolver and must not be guessed here.
CATALOG = {
    'read-only': (':read-only', 'on-request', 'user'),
    'auto': (':workspace', 'on-request', 'user'),
    'granular': (':workspace', {'granular': {
        'sandbox_approval': False, 'rules': False, 'skill_approval': False,
        'request_permissions': True, 'mcp_elicitations': True}}, 'user'),
    'guardian-approvals': (':workspace', 'on-request', 'guardian_subagent'),
    'full-access': (':danger-full-access', 'never', 'user'),
}


@dataclass(frozen=True)
class PermissionSelection:
    mode: str
    fingerprint: str

    def request_fields(self):
        profile, approval, reviewer = CATALOG[self.mode]
        return {'permissions': profile, 'approvalPolicy': deepcopy(approval),
                'approvalsReviewer': reviewer}


def selection_from_atoms(atoms):
    if not isinstance(atoms, dict):
        raise BridgeError('PERMISSIONS_UNAVAILABLE', 'Host permission state is invalid')
    selected = atoms.get('permission-selection-by-host-id:local')
    legacy = atoms.get('agent-mode-by-host-id')
    legacy = legacy.get('local') if isinstance(legacy, dict) else None
    # Requiring both generations to agree avoids treating a stale preference as
    # the current selection when the host feature flag changes its UI resolver.
    if (not isinstance(selected, dict) or selected.get('kind') != 'agent-mode'
            or set(selected) != {'kind', 'agentMode'}):
        raise BridgeError('PERMISSIONS_UNAVAILABLE', 'Explicit built-in host permission selection is required')
    mode = selected['agentMode']
    if not isinstance(mode, str) or mode not in CATALOG:
        raise BridgeError('PERMISSIONS_UNAVAILABLE', 'Host permission selection requires its native resolver')
    if legacy != mode:
        raise BridgeError('PERMISSIONS_CONFLICT', 'Host permission selections disagree; no turn was submitted')
    fingerprint = json.dumps({'selection': selected, 'legacy': legacy}, sort_keys=True)
    return PermissionSelection(mode, fingerprint)


def assert_supported_thread(state):
    """A global preference cannot override custom or remote thread policy."""
    if state.get('mode') == 'durable':
        raise BridgeError('PERMISSIONS_UNAVAILABLE', 'Durable thread permissions require native host resolution')
    environments = state.get('environments') or []
    if any(e.get('environmentId') not in (None, 'local') for e in environments):
        raise BridgeError('PERMISSIONS_UNAVAILABLE', 'Remote thread permissions require native host resolution')
    settings = state.get('latestThreadSettings') or {}
    current = state.get('currentPermissions') or {}
    for source in (settings, current):
        profile = source.get('activePermissionProfile')
        profile_id = source.get('permissions')
        if isinstance(profile, dict):
            profile_id = profile.get('id')
        if profile_id not in (':read-only', ':workspace', ':danger-full-access'):
            raise BridgeError('PERMISSIONS_UNAVAILABLE', 'Custom or unknown thread permissions require native host resolution')


def read_selection(database):
    """Read only the extension's permission preferences; never emit other atoms."""
    try:
        uri = Path(database).resolve().as_uri() + '?mode=ro'
        with closing(sqlite3.connect(uri, uri=True, timeout=2)) as connection:
            row = connection.execute('''SELECT
                json_extract(json_extract(value, '$."persisted-atom-state"'), '$."permission-selection-by-host-id:local"'),
                json_extract(json_extract(value, '$."persisted-atom-state"'), '$."agent-mode-by-host-id"')
                FROM ItemTable WHERE key = 'openai.chatgpt' ''').fetchone()
        if row is None:
            raise ValueError('missing extension state')
        atoms = {'permission-selection-by-host-id:local': json.loads(row[0]) if row[0] else None,
                 'agent-mode-by-host-id': json.loads(row[1]) if row[1] else None}
        return selection_from_atoms(atoms)
    except (OSError, sqlite3.Error, ValueError, TypeError, AttributeError):
        raise BridgeError('PERMISSIONS_UNAVAILABLE', 'Cannot read bound host permission selection') from None


def assert_no_pending_permission_conflict(state, turns, selection):
    """A changed thread setting must not be overwritten by an older preference.

    Historical UI params are used only to detect ambiguity and reject submission,
    never as proof of effective authorization.
    """
    if not turns:
        return
    latest = state.get('latestThreadSettings') or {}
    previous = turns[-1].get('params') or {}
    def profile(value):
        active = value.get('activePermissionProfile')
        if isinstance(active, dict):
            return active.get('id')
        if value.get('permissions') is not None:
            return value['permissions']
        sandbox = value.get('sandboxPolicy') or {}
        return {'readOnly': ':read-only', 'workspaceWrite': ':workspace',
                'dangerFullAccess': ':danger-full-access'}.get(sandbox.get('type'))
    expected = selection.request_fields()
    for key, current, historical, selected in (
        ('permissions', profile(latest), profile(previous), expected['permissions']),
        ('approvalPolicy', latest.get('approvalPolicy'), previous.get('approvalPolicy'), expected['approvalPolicy']),
        ('approvalsReviewer', latest.get('approvalsReviewer'), previous.get('approvalsReviewer'), expected['approvalsReviewer']),
    ):
        if current is not None and current != historical and current != selected:
            raise BridgeError('PERMISSIONS_CONFLICT',
                              'Pending thread permission settings conflict with the persisted host selection; no turn was submitted')


def default_vscode_database(extension):
    """Fail closed unless the reachable local VS Code can use only one store.

    A custom user-data/profile or portable installation needs native owner-side
    discovery. Never silently read the standard store for such a host.
    """
    extension = Path(extension).resolve()
    standard_extensions = (Path.home() / '.vscode/extensions').resolve()
    if os.name != 'nt' or extension.parent != standard_extensions:
        raise BridgeError('PERMISSIONS_UNAVAILABLE', 'Cannot bind the plugin owner to a supported permission store')
    root = Path(os.environ['APPDATA']) / 'Code'
    profiles = root / 'User/profiles'
    if profiles.is_dir() and any(profiles.iterdir()):
        raise BridgeError('PERMISSIONS_UNAVAILABLE', 'Named VS Code profiles require native owner permission discovery')
    shell = Path(os.environ['SystemRoot']) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
    # Output process metadata only to this parser, not to MCP logs. Every Code
    # process must use the same data directory; otherwise owner binding is unknown.
    command = "@(Get-CimInstance Win32_Process -Filter \"Name = 'Code.exe'\" | Select-Object ExecutablePath,CommandLine) | ConvertTo-Json -Compress"
    try:
        result = subprocess.run([str(shell), '-NoProfile', '-NonInteractive', '-Command', command],
                                capture_output=True, text=True, timeout=10, check=True,
                                creationflags=0x08000000)
        processes = json.loads(result.stdout)
        if isinstance(processes, dict):
            processes = [processes]
        assert_bound_processes(processes, root)
    except (OSError, ValueError, subprocess.SubprocessError):
        raise BridgeError('PERMISSIONS_UNAVAILABLE', 'Cannot establish a unique live VS Code permission store') from None
    return root / 'User/globalStorage/state.vscdb'


def assert_bound_processes(processes, expected_root):
    import re
    if not isinstance(processes, list) or not processes:
        raise BridgeError('PERMISSIONS_UNAVAILABLE', 'No live VS Code process was found')
    for process in processes:
        if not isinstance(process, dict):
            raise BridgeError('PERMISSIONS_UNAVAILABLE', 'VS Code process identity is invalid')
        command = process.get('CommandLine')
        executable = process.get('ExecutablePath')
        if not isinstance(command, str) or not isinstance(executable, str):
            raise BridgeError('PERMISSIONS_UNAVAILABLE', 'VS Code process identity is unreadable')
        if (Path(executable).parent / 'data').exists() or re.search(r'--profile(?:[=\s]|$)', command):
            raise BridgeError('PERMISSIONS_UNAVAILABLE', 'Portable or named-profile VS Code is not supported by this reader')
        match = re.search(r'--user-data-dir(?:=|\s+)(?:"([^"]+)"|([^\s]+))', command)
        if match and os.path.normcase(os.path.abspath(match[1] or match[2])) != os.path.normcase(os.path.abspath(expected_root)):
            raise BridgeError('PERMISSIONS_UNAVAILABLE', 'Multiple VS Code user-data directories make permission ownership ambiguous')


def assert_selection_current(database, expected):
    actual = read_selection(database)
    if actual.fingerprint != expected.fingerprint:
        raise BridgeError('PERMISSIONS_CHANGED', 'Host permission selection changed; no turn was submitted')


def effective_permissions(value):
    """Extract only permissions from a native thread/turn response."""
    if not isinstance(value, dict):
        raise BridgeError('PERMISSIONS_UNAVAILABLE', 'Native effective permissions are missing')
    sandbox = value.get('sandboxPolicy', value.get('sandbox'))
    approval = value.get('approvalPolicy')
    if not isinstance(sandbox, dict) or not isinstance(sandbox.get('type'), str) or approval is None:
        raise BridgeError('PERMISSIONS_UNAVAILABLE', 'Native effective permissions are incomplete')
    result = {'sandboxPolicy': deepcopy(sandbox), 'approvalPolicy': deepcopy(approval)}
    for key in ('permissions', 'activePermissionProfile', 'approvalsReviewer', 'runtimeWorkspaceRoots'):
        if key in value:
            result[key] = deepcopy(value[key])
    return result


def assert_effective_selection(expected, actual):
    fields = expected.request_fields()
    actual = effective_permissions(actual)
    profile = actual.get('permissions')
    if profile is None:
        active = actual.get('activePermissionProfile')
        profile = active.get('id') if isinstance(active, dict) else None
    sandbox_type = {':read-only': 'readOnly', ':workspace': 'workspaceWrite',
                    ':danger-full-access': 'dangerFullAccess'}[fields['permissions']]
    if ((profile is not None and profile != fields['permissions']) or actual['sandboxPolicy']['type'] != sandbox_type
            or actual['approvalPolicy'] != fields['approvalPolicy']
            or actual.get('approvalsReviewer') != fields['approvalsReviewer']):
        raise BridgeError('PERMISSIONS_MISMATCH', 'Native turn permissions differ from the selected host permissions')
    return actual


def native_path(value):
    value = os.fspath(value)
    if os.name == 'nt':
        if value.lower().startswith('\\\\?\\unc\\'):
            value = '\\\\' + value[8:]
        elif value.startswith('\\\\?\\'):
            value = value[4:]
    return Path(value).resolve()


def rollout_path(thread_id, cwd, codex_home=None):
    """Resolve an exact thread through Codex's native read-only state index."""
    from .store import canonical
    home = native_path(codex_home or os.environ.get('CODEX_HOME', Path.home()/'.codex'))
    matches = set()
    try:
        for database in home.glob('state_*.sqlite'):
            with closing(sqlite3.connect(database.as_uri()+'?mode=ro', uri=True, timeout=2)) as connection:
                row = connection.execute('SELECT rollout_path,cwd FROM threads WHERE id=?', (thread_id,)).fetchone()
            if row:
                if canonical(str(native_path(row[1]))) != canonical(str(native_path(cwd))):
                    raise BridgeError('WORKSPACE_DENIED', 'Native thread workspace differs from the task')
                path = native_path(row[0])
                if not any(path.is_relative_to(home/name) for name in ('sessions', 'archived_sessions')):
                    raise BridgeError('PERMISSIONS_UNAVAILABLE', 'Native thread history is outside the bound Codex store')
                matches.add(path)
    except (OSError, sqlite3.Error, ValueError, TypeError):
        raise BridgeError('PERMISSIONS_UNAVAILABLE', 'Cannot resolve native thread permission evidence') from None
    if len(matches) != 1:
        raise BridgeError('PERMISSIONS_UNAVAILABLE', 'Native thread permission evidence is missing or ambiguous')
    return matches.pop()


def read_turn_permissions(path, turn_id):
    """Read a bounded tail; never mistake requested UI params for effective policy."""
    try:
        with Path(path).open('rb') as stream:
            stream.seek(0, 2)
            size = stream.tell()
            stream.seek(max(0, size - 8 * 1024 * 1024))
            if stream.tell():
                stream.readline()  # discard a possibly partial first record
            lines = stream.read().splitlines()
        for line in reversed(lines):
            try:
                entry = json.loads(line)
            except (ValueError, UnicodeDecodeError):
                continue  # writer may still be completing the last record
            if not isinstance(entry, dict):
                continue
            context = entry.get('payload', {})
            if entry.get('type') != 'turn_context' or not isinstance(context, dict) or context.get('turn_id') != turn_id:
                continue
            sandbox = deepcopy(context.get('sandbox_policy'))
            if not isinstance(sandbox, dict):
                raise BridgeError('PERMISSIONS_UNAVAILABLE', 'Native turn sandbox policy is incomplete')
            sandbox['type'] = {'danger-full-access': 'dangerFullAccess', 'workspace-write': 'workspaceWrite',
                               'read-only': 'readOnly', 'external-sandbox': 'externalSandbox'}.get(sandbox.get('type'), sandbox.get('type'))
            return effective_permissions({'sandboxPolicy': sandbox,
                'approvalPolicy': context.get('approval_policy'), 'approvalsReviewer': context.get('approvals_reviewer')})
    except OSError:
        return None
    return None
