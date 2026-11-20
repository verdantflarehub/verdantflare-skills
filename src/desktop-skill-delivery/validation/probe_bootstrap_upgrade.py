"""Execute distinct native runtime upgrades, interrupted recovery and rollback.

Uses only new isolated engineering scopes. A successful probe is not a desktop,
Windows, publisher-trust or production runtime-upgrade acceptance claim.
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
CORE_FILES = ('install_transaction.py', 'config_transaction.py', 'host_config.py', 'host_toml.py',
              'toml_transaction.py', 'install_operation.py', 'transaction_compensation.py',
              'runtime_tools/runtime_store.py', 'runtime_tools/runtime_lifecycle.py', 'offline_operation.py')


def probe(old_bundle, new_bundle, rollback_bundle, old_manifest_sha, new_manifest_sha,
          rollback_manifest_sha, old_binary_sha, new_binary_sha, rollback_binary_sha,
          sample, sample_sha, old_version, new_version, output):
    tx.require(platform.system() == 'Darwin' and platform.machine() == 'arm64', 'MACOS_ARM64_PROBE_REQUIRED')
    old_bundle, new_bundle, rollback_bundle, sample, output = (
        Path(p).absolute() for p in (old_bundle, new_bundle, rollback_bundle, sample, output))
    inputs = (old_bundle, new_bundle, rollback_bundle, sample)
    for path in (*inputs, output):
        tx.no_links(path)
    tx.require(not output.exists() and output.parent.is_dir()
               and all(not output.is_relative_to(p) and not p.is_relative_to(output) for p in inputs),
               'NEW_EXTERNAL_OUTPUT_REQUIRED')
    tx.require(type(old_version) is str and type(new_version) is str
               and old_version and new_version and old_version != new_version,
               'DISTINCT_RUNTIME_VERSIONS_REQUIRED')
    pins = ((old_bundle, old_manifest_sha, old_binary_sha),
            (new_bundle, new_manifest_sha, new_binary_sha),
            (rollback_bundle, rollback_manifest_sha, rollback_binary_sha))
    for bundle, manifest_sha, binary_sha in pins:
        transfer.verify_folder(bundle, manifest_sha, binary_sha)
    source_files = {}
    for name in CORE_FILES:
        local = TOOLS / ('installer_tools/' + name if name == 'offline_operation.py' else name)
        source_files[name] = tx.sha(local.read_bytes())
    for bundle in (new_bundle, rollback_bundle):
        admitted = tx.decode((bundle / 'bootstrap-manifest.json').read_bytes())['entries']
        tx.require(all(admitted['resources/installer/core/' + name]['sha256'] == digest
                       for name, digest in source_files.items()), 'UPGRADE_NATIVE_SOURCE_MISMATCH')
    old_tree, old_digest, _ = offline.bundle_runtime(old_bundle)
    new_tree, new_digest, _ = offline.bundle_runtime(new_bundle)
    rollback_tree, rollback_digest, _ = offline.bundle_runtime(rollback_bundle)
    executable = 'bin/studio-workspace'
    tx.require(old_digest == rollback_digest and old_tree == rollback_tree
               and old_digest != new_digest
               and old_tree['entries'][executable]['sha256'] != new_tree['entries'][executable]['sha256'],
               'DISTINCT_EXECUTABLE_RUNTIME_REQUIRED')
    payload, modes, release, packages = offline.skills(sample, sample_sha)
    output.mkdir(mode=0o700)
    (output / 'logs').mkdir(mode=0o700)
    commands, outcomes = [], []
    env = {'PATH': '', 'LANG': 'C', 'LC_ALL': 'C'}

    def capture(cp, label, expected):
        for stream in ('stdout', 'stderr'):
            raw = getattr(cp, stream).replace(str(Path.home()), '%USERPROFILE%')
            (output / 'logs' / (label + '.' + stream)).write_text(raw, encoding='utf-8', newline='\n')
        commands.append({'step': label, 'exit_code': cp.returncode})
        # Preserve partial command evidence even if a subsequent assertion fails.
        (output / 'commands.json').write_bytes(tx.encode(commands))
        tx.require(cp.returncode == expected, 'UPGRADE_NATIVE_COMMAND_FAILED')

    def execute(bundle, label, args, expected=0, error=None):
        cp = subprocess.run([str(bundle / 'vf-bootstrap'), *map(str, args)], cwd=output, env=env,
                            capture_output=True, text=True, timeout=300)
        capture(cp, label, expected)
        if error is not None:
            tx.require(error in cp.stderr, 'UPGRADE_NATIVE_ERROR_MISMATCH')
        return json.loads(cp.stdout) if expected == 0 else None

    for name, bundle in (('old', old_bundle), ('new', new_bundle), ('rollback', rollback_bundle)):
        execute(bundle, name + '-verify-before', ['verify'])
    for fmt in ('json', 'toml'):
        scope = output / (fmt + ' isolated upgrade scope')
        request_path = output / (fmt + '-request.json')
        common = {'schema': offline.SCHEMA, 'scope': str(scope), 'scope_id': uuid.uuid4().hex,
                  'config_format': fmt}
        apply_fields = {'sample': str(sample), 'sample_sha256': sample_sha, 'interrupt_after': None}

        def run(bundle, action, label, extra=None, expected=0, error=None):
            request_path.write_bytes(tx.encode({**common, 'action': action, **(extra or {})}))
            request_path.chmod(0o600)
            return execute(bundle, fmt + '-' + label, ['offline-operation', '--request', request_path], expected, error)

        initial = run(old_bundle, 'apply', 'old-install', apply_fields)
        tx.require(initial['runtime_tree_sha256'] == old_digest and initial['skill_packages'] == packages
                   and initial['skill_files'] == len(payload), 'UPGRADE_INITIAL_INSTALL_MISMATCH')
        target = 'config.toml' if fmt == 'toml' else 'mcp.json'
        engine = offline.InstallOperation(*(scope / p for p in ('state', 'skills', 'config')), target,
                                           runtime_root=scope / 'runtime', config_format=fmt)
        manager = offline.RuntimeLifecycle(engine.runtime_store, [engine])
        config = engine.config.path
        raw = config.read_bytes()
        foreign = (b'# preserve foreign upgrade comment\ntheme = "dark"\n' if fmt == 'toml'
                   else b'{"theme":"dark",')
        config.write_bytes(foreign + (raw if fmt == 'toml' else raw[1:]))
        config.chmod(0o640)
        # Record the actual non-VF edit using the native old-package operation.
        run(old_bundle, 'apply', 'old-register-foreign-config', apply_fields)

        def resources():
            actual_files = {p.relative_to(scope / 'skills').as_posix(): p
                            for p in (scope / 'skills').rglob('*') if p.is_file()
                            and p.relative_to(scope / 'skills').parts[0] != tx.META}
            tx.require(set(actual_files) == set(payload), 'UPGRADE_SKILL_FILE_SET_CHANGED')
            skills = {}
            for name, data in payload.items():
                p = actual_files[name]
                tx.require(not p.is_symlink() and p.read_bytes() == data
                           and stat.S_IMODE(p.stat().st_mode) == modes[name], 'UPGRADE_SKILL_CONTENT_CHANGED')
                skills[name] = {'sha256': tx.sha(data), 'mode': modes[name]}
            return {'skills': skills, 'config_sha256': tx.sha(config.read_bytes()),
                    'config_mode': stat.S_IMODE(config.stat().st_mode),
                    'runtime_pointer': tx.sha((engine.runtime.root / 'runtime.json').read_bytes()),
                    'seal': tx.sha((scope / 'runtime/lifecycle/seal.json').read_bytes())}

        def version(digest, expected_version, label):
            pointer = tx.decode((engine.runtime.root / 'runtime.json').read_bytes())
            tx.require(pointer['tree_sha256'] == digest, 'UPGRADE_ACTIVE_RUNTIME_MISMATCH')
            cp = manager.run(digest, executable, ['--version'], cwd=output, env=env,
                             capture_output=True, text=True, timeout=30)
            capture(cp, fmt + '-' + label, 0)
            tx.require(cp.stdout.strip() == expected_version, 'UPGRADE_EXECUTED_VERSION_MISMATCH')
            engine.runtime_store.verify(digest)
            manifest = engine.runtime_store._manifest(digest)
            actual_sha = offline.store.file_hash(engine.runtime_store._folder(digest) / 'tree' / executable)[0]
            tx.require(actual_sha == manifest['entries'][executable]['sha256'], 'UPGRADE_EXECUTABLE_CHANGED')
            return {'version': cp.stdout.strip(), 'runtime_tree_sha256': digest,
                    'executable_sha256': actual_sha, 'exit_code': cp.returncode}

        before = resources()
        tx.require(before['config_mode'] == 0o640, 'UPGRADE_CONFIG_MODE_CHANGED')
        old_execution = version(old_digest, old_version, 'old-version-before')
        inventory = run(old_bundle, 'inventory', 'old-inventory')['inventory']
        migration = run(new_bundle, 'migrate-binding', 'migrate-to-new', {
            'previous_bootstrap_manifest_sha256': old_manifest_sha,
            'inventory_revision': inventory['revision'], 'interrupt_after': None})
        tx.require(migration['status'] == 'offline_binding_migrated' and resources() == before,
                   'UPGRADE_MIGRATION_CHANGED_RESOURCES')
        binding = (scope / 'scope.json').read_bytes()
        with manager.lease(old_digest):
            run(new_bundle, 'apply', 'live-runtime-refused', apply_fields, 2, 'RUNTIME_ACTIVITY_BUSY')
        tx.require(resources() == before and (scope / 'scope.json').read_bytes() == binding,
                   'UPGRADE_ACTIVITY_REFUSAL_CHANGED_RESOURCES')
        interrupted = []
        for phase in ('runtime', 'skills', 'config'):
            run(new_bundle, 'apply', 'exit-after-' + phase,
                {**apply_fields, 'interrupt_after': phase}, 73)
            tx.require(run(new_bundle, 'status', phase + '-interrupted-status')['operation_state'] == phase,
                       'UPGRADE_INTERRUPTION_STATE_MISMATCH')
            recovered = run(new_bundle, 'recover', phase + '-recover')
            repeated = run(new_bundle, 'recover', phase + '-recover-repeat')
            tx.require(recovered['operation_state'] == repeated['operation_state'] == 'cleaned_aborted'
                       and resources() == before, 'UPGRADE_RECOVERY_RESOURCE_MISMATCH')
            executed = version(old_digest, old_version, phase + '-old-version-restored')
            interrupted.append({'phase': phase, 'process_exit': 73, 'resources_restored': True,
                                'repeated_recovery_completed': True, 'actual_execution': executed})
        run(new_bundle, 'apply', 'exit-after-committed', {**apply_fields, 'interrupt_after': 'committed'}, 73)
        tx.require(run(new_bundle, 'status', 'committed-interrupted-status')['operation_state'] == 'committed',
                   'UPGRADE_COMMIT_STATE_MISMATCH')
        committed = resources()
        tx.require(committed['runtime_pointer'] != before['runtime_pointer']
                   and {k: v for k, v in committed.items() if k != 'runtime_pointer'} ==
                       {k: v for k, v in before.items() if k != 'runtime_pointer'},
                   'UPGRADE_COMMIT_RESOURCE_MISMATCH')
        tx.require(run(new_bundle, 'recover', 'committed-recover')['operation_state'] == 'cleaned'
                   and run(new_bundle, 'recover', 'committed-recover-repeat')['operation_state'] == 'cleaned'
                   and resources() == committed, 'UPGRADE_COMMITTED_CLEANUP_CHANGED_RESOURCES')
        new_execution = version(new_digest, new_version, 'new-version-committed')
        reapplied = run(new_bundle, 'apply', 'new-reapply', apply_fields)
        tx.require(reapplied['runtime_tree_sha256'] == new_digest and resources() == committed,
                   'UPGRADE_REAPPLY_CHANGED_RESOURCES')
        rollback_inventory = run(new_bundle, 'inventory', 'new-inventory')['inventory']
        reverse = run(rollback_bundle, 'migrate-binding', 'migrate-to-rollback', {
            'previous_bootstrap_manifest_sha256': new_manifest_sha,
            'inventory_revision': rollback_inventory['revision'], 'interrupt_after': None})
        tx.require(reverse['status'] == 'offline_binding_migrated' and resources() == committed,
                   'UPGRADE_REVERSE_MIGRATION_CHANGED_RESOURCES')
        rolled_back = run(rollback_bundle, 'apply', 'apply-old-runtime', apply_fields)
        run(rollback_bundle, 'recover', 'rollback-recover')
        run(rollback_bundle, 'recover', 'rollback-recover-repeat')
        tx.require(rolled_back['runtime_tree_sha256'] == old_digest and resources() == before,
                   'UPGRADE_ROLLBACK_RESOURCE_MISMATCH')
        rollback_execution = version(old_digest, old_version, 'old-version-after-rollback')
        # Both full trees remain verified; no GC is implied by rollback.
        engine.runtime_store.verify(new_digest)
        outcomes.append({'config_format': fmt, 'skill_packages': packages, 'skill_files': len(payload),
                         'old_execution': old_execution, 'interrupted_upgrades': interrupted,
                         'new_execution': new_execution, 'rollback_execution': rollback_execution,
                         'live_activity_refused': True, 'committed_cleanup_only': True,
                         'new_reapply_unchanged': True, 'all_resources_restored_after_rollback': True,
                         'config_and_0640_preserved': True, 'all_skill_bytes_and_modes_preserved': True,
                         'seal_unchanged': True, 'migration_sha256': migration['migration_sha256'],
                         'reverse_migration_sha256': reverse['migration_sha256']})
    for name, bundle in (('old', old_bundle), ('new', new_bundle), ('rollback', rollback_bundle)):
        execute(bundle, name + '-verify-after', ['verify'])
    result = {'schema': 'vf.bootstrap-runtime-upgrade-probe/v1', 'status': 'native_runtime_upgrade_probed',
              'old_manifest_sha256': old_manifest_sha, 'new_manifest_sha256': new_manifest_sha,
              'rollback_manifest_sha256': rollback_manifest_sha, 'old_binary_sha256': old_binary_sha,
              'new_binary_sha256': new_binary_sha, 'rollback_binary_sha256': rollback_binary_sha,
              'old_runtime_tree_sha256': old_digest, 'new_runtime_tree_sha256': new_digest,
              'sample_sha256': sample_sha, 'skill_release': release, 'skill_version_changed': False,
              'probe_source_sha256': tx.sha(Path(__file__).read_bytes()), 'admitted_source_files': source_files,
              'commands': commands, 'checks': outcomes, 'empty_path': True,
              'native_runtime_upgrade_verified': True, 'runtime_upgrade_acceptance': False,
              'host_activity_verified': False, 'user_config_changed': False, 'installable': False,
              'publisher_signature_verified': False, 'windows_execution': False,
              'desktop_acceptance': False, 'mcp_acceptance': False, 'independent_review': 'pending'}
    (output / 'probe-receipt.json').write_bytes(tx.encode(result))
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ('old-bundle', 'new-bundle', 'rollback-bundle', 'sample', 'output'):
        parser.add_argument('--' + key, type=Path, required=True)
    for key in ('old-manifest-sha', 'new-manifest-sha', 'rollback-manifest-sha',
                'old-binary-sha', 'new-binary-sha', 'rollback-binary-sha', 'sample-sha',
                'old-version', 'new-version'):
        parser.add_argument('--' + key, required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(probe(**vars(args)), sort_keys=True))
    except Exception as exc:
        print(type(exc).__name__, str(exc).replace(str(Path.home()), '%USERPROFILE%'), file=sys.stderr)
        raise SystemExit(2)
