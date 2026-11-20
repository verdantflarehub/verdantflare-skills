"""Actual native offline install/recovery in new macOS engineering scopes."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import platform
import stat
import subprocess
import sys
import uuid

TOOLS = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(TOOLS), str(TOOLS / 'installer_tools')]
import install_transaction as tx
import offline_operation as offline


def probe(bundle, sample, sample_sha, output):
    tx.require(platform.system() == 'Darwin' and platform.machine() == 'arm64', 'MACOS_ARM64_PROBE_REQUIRED')
    bundle, sample, output = (Path(p).absolute() for p in (bundle, sample, output))
    for path in (bundle, sample, output):tx.no_links(path)
    tx.require(not output.exists() and output.parent.is_dir() and not output.is_relative_to(bundle)
               and not bundle.is_relative_to(output) and not sample.is_relative_to(output), 'NEW_EXTERNAL_OUTPUT_REQUIRED')
    payload, _, release, count = offline.skills(sample, sample_sha)
    binary = bundle / 'vf-bootstrap'
    output.mkdir(mode=0o700)
    (output / 'logs').mkdir(mode=0o700)
    commands = []
    env = {'PATH': '', 'LANG': 'C', 'LC_ALL': 'C'}
    def execute(label, arguments, expected=0):
        cp = subprocess.run([str(binary), *map(str, arguments)], env=env, cwd=output,
                            capture_output=True, text=True, timeout=300)
        for stream in ('stdout', 'stderr'):
            raw = getattr(cp, stream).replace(str(Path.home()), '%USERPROFILE%')
            (output / 'logs' / (label + '.' + stream)).write_text(raw)
        commands.append({'step': label, 'exit_code': cp.returncode})
        tx.require(cp.returncode == expected, 'OFFLINE_NATIVE_PROBE_COMMAND_FAILED')
        return json.loads(cp.stdout) if expected == 0 else None
    execute('verify-before', ['verify'])
    outcomes = []
    for fmt in ('json', 'toml'):
        scope = output / (fmt + ' parent with spaces')
        scope_id = uuid.uuid4().hex
        value = {'schema': offline.SCHEMA, 'action': 'apply', 'scope': str(scope),
                 'scope_id': scope_id, 'config_format': fmt, 'sample': str(sample),
                 'sample_sha256': sample_sha, 'interrupt_after': 'config'}
        request = output / (fmt + '-request.json')
        def run(action, label, expected=0, extra=None):
            message = dict(value) if action == 'apply' else {k: v for k, v in value.items() if k in offline.COMMON}
            message['action'] = action
            if extra is not None:
                message.update(extra)
            request.write_bytes(tx.encode(message));request.chmod(0o600)
            return execute(fmt + '-' + label, ['offline-operation', '--request', request], expected)
        run('apply', 'actual-process-exit', 73)
        tx.require(run('status', 'status-interrupted')['operation_state'] == 'config', 'OFFLINE_NATIVE_INTERRUPTION_STATE')
        tx.require(run('recover', 'recover')['operation_state'] == 'cleaned_aborted', 'OFFLINE_NATIVE_RECOVERY_STATE')
        run('recover', 'recover-repeat')
        config = scope / 'config' / ('config.toml' if fmt == 'toml' else 'mcp.json')
        tx.require(not config.exists() and not (scope / 'runtime/activation/runtime.json').exists()
                   and not (scope / 'skills/verdantflare-project/SKILL.md').exists(), 'OFFLINE_NATIVE_BEFORE_STATE_MISMATCH')
        # An explicitly data-only unused tree tests native inventory/CAS/GC;
        # it is not a second released runtime or an upgrade acceptance claim.
        engine = offline.InstallOperation(*(scope / n for n in ('state', 'skills', 'config')), config.name,
                                           runtime_root=scope / 'runtime', config_format=fmt)
        manager = offline.RuntimeLifecycle(engine.runtime_store, [engine])
        unused_raw = b'UNUSED_DATA_ONLY_GC_FIXTURE_NEVER_EXECUTE\n'
        unused = {'schema': offline.store.SCHEMA, 'release': 'unused.gc.fixture', 'source_sha256': '0' * 64,
                  'entries': {'unused.dat': {'type': 'file', 'mode': 0o644, 'size': len(unused_raw),
                                             'sha256': tx.sha(unused_raw)}}}
        unused_digest = tx.sha(tx.encode(unused))
        source = output / (fmt + '-unused.dat');source.write_bytes(unused_raw);source.chmod(0o644)
        manager.stage(unused, {'unused.dat': source}, expected_digest=unused_digest,
                      transaction_id=uuid.uuid4().hex)
        inventory = run('inventory', 'inventory-unused')['inventory']
        run('collect', 'stale-gc-revision', 2, {'runtime_tree_sha256': unused_digest, 'inventory_revision': '0' * 64})
        collected = run('collect', 'collect-unused', extra={'runtime_tree_sha256': unused_digest,
                                                          'inventory_revision': inventory['revision']})
        tx.require(collected['maintenance']['status'] == 'runtime_collected'
                   and unused_digest not in run('inventory', 'inventory-after-gc')['inventory']['versions'],
                   'OFFLINE_NATIVE_GC_MISMATCH')
        tx.require(run('recover-gc', 'repeat-gc-recovery')['maintenance']['status'] == 'nothing_pending',
                   'OFFLINE_NATIVE_GC_RECOVERY_MISMATCH')
        initial = b'# preserve foreign comment\ntheme="dark"\n' if fmt == 'toml' else b'{"theme":"dark"}\n'
        config.write_bytes(initial);config.chmod(0o640)
        value['interrupt_after'] = None
        applied = run('apply', 'apply-after-recovery')
        for name, raw in payload.items():
            tx.require((scope / 'skills' / name).read_bytes() == raw, 'OFFLINE_NATIVE_SKILL_CHANGED')
        after = config.read_bytes()
        if fmt == 'toml':
            import tomllib
            parsed = tomllib.loads(after.decode());parent = 'mcp_servers'
            tx.require(b'# preserve foreign comment\n' in after, 'OFFLINE_NATIVE_COMMENT_CHANGED')
        else:
            parsed = json.loads(after);parent = 'mcpServers'
        tx.require(parsed['theme'] == 'dark' and parsed[parent]['vf-studio'] == {'url': 'https://vf-offline-operation.invalid/mcp'}
                   and stat.S_IMODE(config.stat().st_mode) == 0o640, 'OFFLINE_NATIVE_FOREIGN_CONFIG_CHANGED')
        pointer = (scope / 'runtime/activation/runtime.json').read_bytes()
        reapplied = run('apply', 'reinstall-same-candidate')
        tx.require(config.read_bytes() == after and (scope / 'runtime/activation/runtime.json').read_bytes() == pointer,
                   'OFFLINE_NATIVE_REINSTALL_CHANGED')
        import fcntl
        activity = scope / 'runtime/lifecycle/activity.lock'
        with activity.open('rb') as active:
            fcntl.flock(active.fileno(), fcntl.LOCK_SH | fcntl.LOCK_NB)
            run('apply', 'active-runtime-refuses-apply', 2)
            run('recover', 'active-runtime-refuses-recover', 2)
            run('inventory', 'active-runtime-refuses-maintenance', 2)
            tx.require(run('status', 'active-runtime-status')['operation_state'] == 'cleaned',
                       'OFFLINE_NATIVE_READONLY_STATUS_MISMATCH')
        tx.require(config.read_bytes() == after and (scope / 'runtime/activation/runtime.json').read_bytes() == pointer,
                   'OFFLINE_NATIVE_ACTIVITY_REFUSAL_CHANGED_RESOURCES')
        current = run('inventory', 'inventory-active-tree')['inventory']
        run('collect', 'active-tree-protected', 2, {'runtime_tree_sha256': applied['runtime_tree_sha256'],
                                                 'inventory_revision': current['revision']})
        original_id = value['scope_id'];value['scope_id'] = '0' * 32
        run('status', 'wrong-scope-id', 2);value['scope_id'] = original_id
        tx.require(config.read_bytes() == after, 'OFFLINE_NATIVE_REFUSAL_CHANGED')
        outcomes.append({'config_format': fmt, 'scope_id': scope_id, 'skill_packages': count,
                         'skill_files': len(payload), 'skill_bytes': sum(map(len, payload.values())),
                         'release': release, 'actual_process_exit_code': 73, 'three_resources_restored': True,
                         'repeated_recovery_completed': True, 'subsequent_apply': applied['status'],
                         'reinstall': reapplied['status'], 'reinstall_bytes_unchanged': True,
                         'foreign_config_and_0640_preserved': True,
                         'lifecycle_managed': applied['lifecycle_managed'],
                         'active_lock_blocks_native_mutation': True, 'referenced_runtime_protected': True,
                         'unused_data_fixture_collected': True, 'stale_gc_revision_refused': True,
                         'host_activity_verified': False, 'runtime_upgrade_acceptance': False,
                         'runtime_tree_sha256': applied['runtime_tree_sha256'], 'wrong_scope_id_refused': True})
    execute('verify-after', ['verify'])
    result = {'schema': 'vf.bootstrap-offline-native-probe/v1', 'status': 'native_offline_operations_verified',
              'bootstrap_manifest_sha256': tx.sha((bundle / 'bootstrap-manifest.json').read_bytes()),
              'native_sha256': tx.sha(binary.read_bytes()), 'sample_sha256': sample_sha,
              'initial_PATH_empty': True, 'host_python_required_by_launcher': False, 'formats': outcomes,
              'commands': commands, 'installable': False, 'publisher_signature_verified': False,
              'user_config_changed': False, 'windows_execution': False, 'desktop_acceptance': False,
              'mcp_acceptance': False, 'independent_review': 'pending'}
    (output / 'probe-receipt.json').write_bytes(tx.encode(result))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ('bundle', 'sample', 'output'):parser.add_argument('--' + key, type=Path, required=True)
    parser.add_argument('--sample-sha256', required=True)
    args = parser.parse_args()
    print(json.dumps(probe(args.bundle, args.sample, args.sample_sha256, args.output)))


if __name__ == '__main__':
    try:
        main()
    except Exception:
        print(json.dumps({'error': 'OFFLINE_NATIVE_PROBE_FAILED'}), file=sys.stderr)
        raise SystemExit(2)
