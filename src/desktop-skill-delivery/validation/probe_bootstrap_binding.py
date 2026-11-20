"""Real native cross-bootstrap binding migration; keeps the runtime unchanged."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import platform
import subprocess
import sys
import uuid

TOOLS = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(TOOLS), str(TOOLS / 'installer_tools')]
import offline_operation as offline
import bootstrap_transfer as transfer

tx = offline.tx


def probe(old_bundle, new_bundle, old_manifest_sha, new_manifest_sha, old_binary_sha, new_binary_sha,
          sample, sample_sha, output):
    tx.require(platform.system() == 'Darwin' and platform.machine() == 'arm64', 'MACOS_ARM64_PROBE_REQUIRED')
    old_bundle, new_bundle, sample, output = (Path(p).absolute() for p in (old_bundle, new_bundle, sample, output))
    for path in (old_bundle, new_bundle, sample, output):
        tx.no_links(path)
    tx.require(not output.exists() and output.parent.is_dir()
               and all(not output.is_relative_to(p) and not p.is_relative_to(output)
                       for p in (old_bundle, new_bundle, sample)), 'NEW_EXTERNAL_OUTPUT_REQUIRED')
    transfer.verify_folder(old_bundle, old_manifest_sha, old_binary_sha)
    transfer.verify_folder(new_bundle, new_manifest_sha, new_binary_sha)
    admitted = tx.decode((new_bundle / 'bootstrap-manifest.json').read_bytes())['entries']
    source_files = {}
    for name in ('install_transaction.py', 'config_transaction.py', 'host_config.py', 'host_toml.py',
                 'toml_transaction.py', 'install_operation.py', 'transaction_compensation.py',
                 'runtime_tools/runtime_store.py', 'runtime_tools/runtime_lifecycle.py', 'offline_operation.py'):
        path = TOOLS / 'installer_tools/offline_operation.py' if name == 'offline_operation.py' else TOOLS / name
        source_files[name] = tx.sha(path.read_bytes())
        tx.require(admitted['resources/installer/core/' + name]['sha256'] == source_files[name],
                   'BINDING_NATIVE_SOURCE_MISMATCH')
    old_runtime = offline.bundle_runtime(old_bundle)[1]
    tx.require(offline.bundle_runtime(new_bundle)[1] == old_runtime
               and old_manifest_sha != new_manifest_sha, 'BINDING_ONLY_DIFFERENT_BOOTSTRAPS_REQUIRED')
    payload, _, release, packages = offline.skills(sample, sample_sha)
    output.mkdir(mode=0o700);(output / 'logs').mkdir(mode=0o700)
    commands, outcomes = [], []
    env = {'PATH': '', 'LANG': 'C', 'LC_ALL': 'C'}

    def execute(bundle, label, args, expected=0, error=None):
        cp = subprocess.run([str(bundle / 'vf-bootstrap'), *map(str, args)], cwd=output, env=env,
                            capture_output=True, text=True, timeout=300)
        for stream in ('stdout', 'stderr'):
            raw = getattr(cp, stream).replace(str(Path.home()), '%USERPROFILE%')
            (output / 'logs' / (label + '.' + stream)).write_text(raw, encoding='utf-8', newline='\n')
        commands.append({'step': label, 'exit_code': cp.returncode})
        tx.require(cp.returncode == expected, 'BINDING_NATIVE_COMMAND_FAILED')
        if error is not None:
            tx.require(error in cp.stderr, 'BINDING_NATIVE_ERROR_MISMATCH')
        return json.loads(cp.stdout) if expected == 0 else None

    execute(old_bundle, 'old-verify-before', ['verify'])
    execute(new_bundle, 'new-verify-before', ['verify'])
    for fmt in ('json', 'toml'):
        scope = output / (fmt + ' isolated scope')
        request_path = output / (fmt + '-request.json')
        common = {'schema': offline.SCHEMA, 'scope': str(scope), 'scope_id': uuid.uuid4().hex, 'config_format': fmt}

        def run(bundle, action, label, extra=None, expected=0, error=None):
            value = {**common, 'action': action, **(extra or {})}
            request_path.write_bytes(tx.encode(value));request_path.chmod(0o600)
            return execute(bundle, fmt + '-' + label, ['offline-operation', '--request', request_path], expected, error)

        apply_fields = {'sample': str(sample), 'sample_sha256': sample_sha, 'interrupt_after': None}
        applied = run(old_bundle, 'apply', 'old-apply', apply_fields)
        tx.require(applied['skill_packages'] == packages and applied['skill_files'] == len(payload),
                   'BINDING_NATIVE_SKILL_SET_MISMATCH')
        inventory = run(old_bundle, 'inventory', 'old-inventory')['inventory']
        target = 'config.toml' if fmt == 'toml' else 'mcp.json'
        engine = offline.InstallOperation(*(scope / p for p in ('state', 'skills', 'config')), target,
                                           runtime_root=scope / 'runtime', config_format=fmt)
        manager = offline.RuntimeLifecycle(engine.runtime_store, [engine])

        def resources():
            return {'skills': {n: tx.sha((scope / 'skills' / n).read_bytes()) for n in payload},
                    'config': tx.sha(engine.config.path.read_bytes()),
                    'config_mode': engine.config.path.stat().st_mode & 0o777,
                    'runtime_pointer': tx.sha((engine.runtime.root / 'runtime.json').read_bytes()),
                    'seal': tx.sha((scope / 'runtime/lifecycle/seal.json').read_bytes())}

        before = resources();binding_before = (scope / 'scope.json').read_bytes()
        migration = {'previous_bootstrap_manifest_sha256': old_manifest_sha,
                     'inventory_revision': inventory['revision'], 'interrupt_after': None}
        run(new_bundle, 'apply', 'new-implicit-refused', apply_fields, 2, 'OFFLINE_SCOPE_BINDING_MISMATCH')
        run(new_bundle, 'migrate-binding', 'stale-inventory-refused',
            {**migration, 'inventory_revision': '0' * 64}, 2, 'LIFECYCLE_REVISION_CHANGED')
        # Controlled removal of an empty fixture directory must not be repaired
        # by the admitted tool's underlying engine constructors.
        quarantine = scope / 'runtime/lifecycle/quarantine'
        quarantine.rmdir()
        run(new_bundle, 'migrate-binding', 'missing-registration-refused', migration, 2,
            'OFFLINE_REGISTERED_SCOPE_REQUIRED')
        tx.require(not quarantine.exists() and (scope / 'scope.json').read_bytes() == binding_before,
                   'BINDING_NATIVE_RECREATED_REGISTRATION')
        quarantine.mkdir(mode=0o700)
        with manager.lease(old_runtime):
            run(new_bundle, 'migrate-binding', 'active-runtime-refused', migration, 2, 'RUNTIME_ACTIVITY_BUSY')
        tx.require((scope / 'scope.json').read_bytes() == binding_before
                   and resources() == before and not (scope / 'state/.vf-bootstrap-binding').exists(),
                   'BINDING_NATIVE_REFUSAL_CHANGED_RESOURCES')
        run(new_bundle, 'migrate-binding', 'exit-before-binding',
            {**migration, 'interrupt_after': 'binding-prepared'}, 73)
        tx.require((scope / 'scope.json').read_bytes() == binding_before, 'BINDING_PREPARED_CHANGED_DESCRIPTOR')
        tx.require(run(old_bundle, 'status', 'old-readable-after-prepare')['operation_state'] == 'cleaned',
                   'BINDING_OLD_OBSERVATION_FAILED')
        run(new_bundle, 'migrate-binding', 'exit-after-binding',
            {**migration, 'interrupt_after': 'binding-committed'}, 73)
        descriptor = tx.decode((scope / 'scope.json').read_bytes())
        tx.require(descriptor['bootstrap_manifest_sha256'] == new_manifest_sha
                   and resources() == before, 'BINDING_COMMITTED_RESOURCE_MISMATCH')
        result = run(new_bundle, 'migrate-binding', 'retry-migration', migration)
        tx.require(result['status'] == 'offline_binding_already_migrated'
                   and result['runtime_upgrade_acceptance'] is False, 'BINDING_RETRY_FAILED')
        record = scope / 'state/.vf-bootstrap-binding' / (result['migration_sha256'] + '.json')
        raw = offline.checked_file(record, 16 * 1024, private=True)
        intent = tx.decode(raw)
        tx.require(tx.sha(raw) == result['migration_sha256'] and tx.encode(intent) == raw
                   and intent['before'] == tx.decode(binding_before) and intent['after'] == descriptor
                   and intent['inventory_revision'] == inventory['revision'], 'BINDING_INTENT_MISMATCH')
        tx.require(run(new_bundle, 'inventory', 'new-inventory')['inventory'] == inventory,
                   'BINDING_MIGRATION_CHANGED_INVENTORY')
        run(old_bundle, 'status', 'old-implicit-refused', expected=2, error='OFFLINE_SCOPE_BINDING_MISMATCH')
        reinstalled = run(new_bundle, 'apply', 'new-apply', apply_fields)
        tx.require(reinstalled['runtime_tree_sha256'] == old_runtime and resources() == before,
                   'BINDING_POST_MIGRATION_RESOURCE_MISMATCH')
        run(new_bundle, 'recover', 'new-recover')
        engine.runtime_store.verify(old_runtime)
        outcomes.append({'config_format': fmt, 'skill_packages': packages, 'skill_files': len(payload),
                         'implicit_switch_refused': True, 'stale_snapshot_refused': True,
                         'missing_registration_refused_without_repair': True,
                         'live_activity_refused': True, 'process_exit_before_binding': 73,
                         'process_exit_after_binding': 73, 'original_intent_retried': True,
                         'all_resources_unchanged': True, 'runtime_tree_sha256': old_runtime,
                         'migration_sha256': result['migration_sha256'], 'new_native_apply_completed': True})
    execute(old_bundle, 'old-verify-after', ['verify'])
    execute(new_bundle, 'new-verify-after', ['verify'])
    receipt = {'schema': 'vf.bootstrap-binding-probe/v1', 'status': 'native_binding_migration_probed',
               'old_manifest_sha256': old_manifest_sha, 'new_manifest_sha256': new_manifest_sha,
               'old_binary_sha256': old_binary_sha, 'new_binary_sha256': new_binary_sha,
               'sample_sha256': sample_sha, 'release': release, 'commands': commands, 'checks': outcomes,
               'probe_source_sha256': tx.sha(Path(__file__).read_bytes()), 'admitted_source_files': source_files,
               'empty_path': True, 'runtime_unchanged': True, 'runtime_upgrade_acceptance': False,
               'user_config_changed': False, 'publisher_signature_verified': False, 'installable': False,
               'windows_execution': False, 'desktop_acceptance': False, 'mcp_acceptance': False,
               'independent_review': 'pending'}
    (output / 'probe-receipt.json').write_bytes(tx.encode(receipt))
    return receipt


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ('old-bundle', 'new-bundle', 'sample', 'output'):
        parser.add_argument('--' + key, type=Path, required=True)
    for key in ('old-manifest-sha', 'new-manifest-sha', 'old-binary-sha', 'new-binary-sha', 'sample-sha'):
        parser.add_argument('--' + key, required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(probe(**vars(args)), sort_keys=True))
    except Exception as exc:
        print(type(exc).__name__, str(exc).replace(str(Path.home()), '%USERPROFILE%'), file=sys.stderr)
        raise SystemExit(2)
