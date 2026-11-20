"""Exercise the fixed native WorkBuddy installer in new macOS engineering roots.

Never starts the vendor, authenticates a publisher or adopts a user configuration.
Input samples are externally pinned, unsigned engineering candidates.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import platform
import stat
import subprocess
import sys
import uuid

TOOLS = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(TOOLS), str(TOOLS / 'installer_tools')]
import offline_operation as offline
import bootstrap_transfer as transfer

tx = offline.tx


def probe(bundle, manifest_sha, binary_sha, initial_sample, initial_sha, update_sample, update_sha, output):
    tx.require(platform.system() == 'Darwin' and platform.machine() == 'arm64', 'MACOS_ARM64_PROBE_REQUIRED')
    bundle, initial_sample, update_sample, output = [Path(p).absolute() for p in
                                                    (bundle, initial_sample, update_sample, output)]
    for path in (bundle, initial_sample, update_sample, output):
        tx.no_links(path)
    tx.require(not output.exists() and output.parent.is_dir()
               and all(not output.is_relative_to(p) and not p.is_relative_to(output)
                       for p in (bundle, initial_sample, update_sample)), 'NEW_EXTERNAL_OUTPUT_REQUIRED')
    transfer.verify_folder(bundle, manifest_sha, binary_sha)
    source = bundle / 'resources/installer/core/offline_operation.py'
    tx.require(source.read_bytes() == Path(offline.__file__).read_bytes(), 'WORKBUDDY_BOOTSTRAP_SOURCE_MISMATCH')
    initial, _, release1, count1 = offline.skills(initial_sample, initial_sha)
    updated, _, release2, count2 = offline.skills(update_sample, update_sha)
    tx.require(count1 == count2 == 11 and release1 != release2, 'WORKBUDDY_DISTINCT_RELEASES_REQUIRED')
    _, runtime_digest, _ = offline.bundle_runtime(bundle)
    output.mkdir(mode=0o700)
    (output / 'logs').mkdir(mode=0o700)
    scope = output / 'WorkBuddy isolated scope with spaces'
    request = output / 'request.json'
    common = {'schema': offline.WORKBUDDY_SCHEMA, 'scope': str(scope), 'scope_id': uuid.uuid4().hex,
              'config_format': 'json', 'host_profile': 'workbuddy'}
    commands = []

    def execute(label, action, extra=None, expected=0, common_override=None):
        message = {**(common_override or common), 'action': action, **(extra or {})}
        request.write_bytes(tx.encode(message));request.chmod(0o600)
        cp = subprocess.run([str(bundle / 'vf-bootstrap'), 'offline-operation', '--request', str(request)],
                            cwd=output, env={'PATH': '', 'LANG': 'C', 'LC_ALL': 'C'},
                            capture_output=True, text=True, timeout=300)
        for name in ('stdout', 'stderr'):
            raw = getattr(cp, name).replace(str(Path.home()), '%USERPROFILE%')
            path = output / 'logs' / (label + '.' + name)
            path.write_text(raw, encoding='utf-8');path.chmod(0o600)
        commands.append({'step': label, 'exit_code': cp.returncode, 'expected_exit_code': expected})
        (output / 'commands.json').write_bytes(tx.encode(commands))
        tx.require(cp.returncode == expected, 'WORKBUDDY_BOOTSTRAP_COMMAND_FAILED')
        if expected:
            return None
        result = json.loads(cp.stdout)
        tx.require(result['schema'] == offline.WORKBUDDY_SCHEMA and result['host_profile'] == 'workbuddy'
                   and result['host_refresh'] == 'refresh_required' and result['host_verified'] is False
                   and result['installable'] is False and result['desktop_acceptance'] is False
                   and result['mcp_acceptance'] is False, 'WORKBUDDY_BOOTSTRAP_FALSE_HOST_CLAIM')
        return result

    first = {'sample': str(initial_sample), 'sample_sha256': initial_sha, 'interrupt_after': 'config'}
    execute('initial-process-exit', 'apply', first, 73)
    tx.require(execute('interrupted-status', 'status')['operation_state'] == 'config', 'WORKBUDDY_INTERRUPTION_MISSING')
    tx.require(execute('recover', 'recover')['operation_state'] == 'cleaned_aborted', 'WORKBUDDY_RECOVERY_MISSING')
    config, skills = scope / 'workbuddy/mcp.json', scope / 'workbuddy/skills'
    tx.require(not config.exists() and not (scope / 'runtime/activation/runtime.json').exists()
               and all(not (skills / name).exists() for name in initial), 'WORKBUDDY_BEFORE_STATE_MISMATCH')
    original = b'{ "theme":"dark", "mcpServers":{"foreign":{"command":"keep"}} }\r\n'
    config.write_bytes(original);config.chmod(0o640)
    canary = skills / 'user-skill/SKILL.md'
    canary.parent.mkdir(mode=0o700);canary.write_bytes(b'# Foreign user skill\n')
    first['interrupt_after'] = None
    installed = execute('initial-install', 'apply', first)
    tx.require(installed['release'] == release1, 'WORKBUDDY_INITIAL_RELEASE_MISMATCH')
    for name, raw in initial.items():
        tx.require((skills / name).read_bytes() == raw, 'WORKBUDDY_INITIAL_SKILL_MISMATCH')
    update = {'sample': str(update_sample), 'sample_sha256': update_sha, 'interrupt_after': None}
    applied = execute('different-release-update', 'apply', update)
    tx.require(applied['release'] == release2 and applied['runtime_tree_sha256'] == runtime_digest,
               'WORKBUDDY_UPDATED_RELEASE_MISMATCH')
    for name, raw in updated.items():
        tx.require((skills / name).read_bytes() == raw, 'WORKBUDDY_UPDATED_SKILL_MISMATCH')
    node = json.loads(config.read_bytes())['mcpServers']['vf-studio']
    tx.require(node == {'url': offline.WORKBUDDY_URL, 'timeout': 30000, 'disabled': False},
               'WORKBUDDY_NATIVE_CONFIG_MISMATCH')
    tx.require(not (skills / tx.META).exists() and (scope / 'state/.vf-workbuddy-skill-files').is_dir(),
               'WORKBUDDY_BACKUP_IN_SCAN_ROOT')
    engine = offline.make_engine(scope, common, 'mcp.json')
    manager = offline.RuntimeLifecycle(engine.runtime_store, [engine])
    env = {'PATH': '', 'LANG': 'C', 'LC_ALL': 'C', 'VF_RUNTIME_CACHE': str(output / 'cache')}
    for label, entry, args in [('managed-python', 'bin/vf-python', ['info']),
                               ('managed-project-cli', 'bin/studio-workspace', ['--version'])]:
        cp = manager.run(runtime_digest, entry, args, env=env, cwd=output, capture_output=True, timeout=90)
        for name in ('stdout', 'stderr'):
            path = output / 'logs' / (label + '.' + name)
            path.write_bytes(getattr(cp, name).replace(str(Path.home()).encode(), b'%USERPROFILE%'))
            path.chmod(0o600)
        commands.append({'step': label, 'exit_code': cp.returncode, 'expected_exit_code': 0})
        tx.require(cp.returncode == 0, 'WORKBUDDY_BOOTSTRAP_RUNTIME_FAILED')
    generic = {k: v for k, v in common.items() if k != 'host_profile'}
    generic['schema'] = offline.SCHEMA
    execute('generic-scope-misuse-refused', 'recover', expected=2, common_override=generic)
    tx.require('OFFLINE_SCOPE_BINDING_MISMATCH' in
               (output / 'logs/generic-scope-misuse-refused.stderr').read_text(), 'WORKBUDDY_BINDING_REFUSAL_MISSING')
    execute('uninstall-process-exit', 'uninstall', {'interrupt_after': 'config'}, 73)
    execute('uninstall-recovery', 'recover')
    for name, raw in updated.items():
        tx.require((skills / name).read_bytes() == raw, 'WORKBUDDY_UNINSTALL_RECOVERY_MISMATCH')
    removed = execute('uninstall', 'uninstall', {'interrupt_after': None})
    tx.require(config.read_bytes() == original and stat.S_IMODE(config.stat().st_mode) == 0o640
               and canary.read_bytes() == b'# Foreign user skill\n'
               and all(not (skills / name).exists() for name in updated)
               and not (scope / 'runtime/activation/runtime.json').exists(), 'WORKBUDDY_UNINSTALL_MISMATCH')
    tx.require(removed['skill_files_removed'] == len(updated), 'WORKBUDDY_REMOVAL_COUNT_MISMATCH')
    result = {'schema': 'vf.workbuddy-native-bootstrap-probe/v1', 'status': 'isolated_native_operations_verified',
              'bootstrap_manifest_sha256': manifest_sha, 'native_sha256': binary_sha,
              'initial_sample_sha256': initial_sha, 'update_sample_sha256': update_sha,
              'initial_release': release1, 'update_release': release2, 'skill_packages': 11,
              'initial_skill_files': len(initial), 'updated_skill_files': len(updated),
              'runtime_tree_sha256': runtime_digest, 'source_sha256': tx.sha(source.read_bytes()),
              'initial_PATH_empty': True, 'host_python_required_by_launcher': False,
              'actual_process_exit_code': 73, 'install_and_uninstall_recovery_verified': True,
              'foreign_config_bytes_and_mode_preserved': True, 'foreign_skill_preserved': True,
              'skill_backups_outside_scan_root': True, 'generic_scope_misuse_refused': True,
              'commands': commands, 'host_refresh': 'refresh_required', 'host_verified': False,
              'installable': False, 'publisher_signature_verified': False, 'user_config_changed': False,
              'windows_execution': False, 'desktop_acceptance': False, 'mcp_acceptance': False,
              'independent_review': 'pending'}
    (output / 'probe-receipt.json').write_bytes(tx.encode(result))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ('bundle', 'initial-sample', 'update-sample', 'output'):
        parser.add_argument('--' + key, type=Path, required=True)
    for key in ('manifest-sha256', 'binary-sha256', 'initial-sha256', 'update-sha256'):
        parser.add_argument('--' + key, required=True)
    args = parser.parse_args()
    result = probe(args.bundle, args.manifest_sha256, args.binary_sha256, args.initial_sample,
                   args.initial_sha256, args.update_sample, args.update_sha256, args.output)
    print(json.dumps(result, sort_keys=True))


if __name__ == '__main__':
    try:
        main()
    except Exception:
        print(json.dumps({'error': 'WORKBUDDY_BOOTSTRAP_PROBE_FAILED'}), file=sys.stderr)
        raise SystemExit(2)
