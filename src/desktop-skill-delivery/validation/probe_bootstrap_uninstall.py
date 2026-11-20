"""Native owned-resource removal/recovery and runtime GC with real skill bytes.

Closed macOS engineering scopes only; never adopts a desktop/user configuration.
External pins establish byte identity, not publisher trust or product acceptance.
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


def probe(bundle, manifest_sha, binary_sha, sample, sample_sha, version, output):
    tx.require(platform.system() == 'Darwin' and platform.machine() == 'arm64', 'MACOS_ARM64_PROBE_REQUIRED')
    bundle, sample, output = (Path(p).absolute() for p in (bundle, sample, output))
    for path in (bundle, sample, output):
        tx.no_links(path)
    tx.require(not output.exists() and output.parent.is_dir()
               and all(not output.is_relative_to(p) and not p.is_relative_to(output) for p in (bundle, sample)),
               'NEW_EXTERNAL_OUTPUT_REQUIRED')
    transfer.verify_folder(bundle, manifest_sha, binary_sha)
    admitted = tx.decode((bundle / 'bootstrap-manifest.json').read_bytes())['entries']
    source_files = {}
    for name in CORE_FILES:
        local = TOOLS / ('installer_tools/' + name if name == 'offline_operation.py' else name)
        digest = tx.sha(local.read_bytes())
        tx.require(admitted['resources/installer/core/' + name]['sha256'] == digest, 'UNINSTALL_NATIVE_SOURCE_MISMATCH')
        source_files[name] = digest
    manifest, digest, _ = offline.bundle_runtime(bundle)
    payload, modes, release, packages = offline.skills(sample, sample_sha)
    tx.require(packages == 11, 'UNINSTALL_ELEVEN_PACKAGES_REQUIRED')
    output.mkdir(mode=0o700);(output / 'logs').mkdir(mode=0o700)
    commands, outcomes = [], []
    env = {'PATH': '', 'LANG': 'C', 'LC_ALL': 'C'}

    def capture(cp, label, expected):
        for stream in ('stdout', 'stderr'):
            raw = getattr(cp, stream).replace(str(Path.home()), '%USERPROFILE%')
            (output / 'logs' / (label + '.' + stream)).write_text(raw, encoding='utf-8', newline='\n')
        commands.append({'step': label, 'exit_code': cp.returncode})
        (output / 'commands.json').write_bytes(tx.encode(commands))
        tx.require(cp.returncode == expected, 'UNINSTALL_NATIVE_COMMAND_FAILED')

    def execute(label, args, expected=0, error=None):
        cp = subprocess.run([str(bundle / 'vf-bootstrap'), *map(str, args)], cwd=output, env=env,
                            capture_output=True, text=True, timeout=300)
        capture(cp, label, expected)
        if error is not None:
            tx.require(error in cp.stderr, 'UNINSTALL_NATIVE_ERROR_MISMATCH')
        return json.loads(cp.stdout) if expected == 0 else None

    execute('verify-before', ['verify'])
    for fmt in ('json', 'toml'):
        scope = output / (fmt + ' isolated removal scope')
        request_path = output / (fmt + '-request.json')
        common = {'schema': offline.SCHEMA, 'scope': str(scope), 'scope_id': uuid.uuid4().hex,
                  'config_format': fmt}
        apply = {'sample': str(sample), 'sample_sha256': sample_sha, 'interrupt_after': None}

        def run(action, label, extra=None, expected=0, error=None):
            request_path.write_bytes(tx.encode({**common, 'action': action, **(extra or {})}))
            request_path.chmod(0o600)
            return execute(fmt + '-' + label, ['offline-operation', '--request', request_path], expected, error)

        initial = run('apply', 'install', apply)
        tx.require(initial['runtime_tree_sha256'] == digest and initial['skill_packages'] == packages
                   and initial['skill_files'] == len(payload), 'UNINSTALL_INITIAL_INSTALL_MISMATCH')
        target = 'config.toml' if fmt == 'toml' else 'mcp.json'
        engine = offline.InstallOperation(*(scope / p for p in ('state', 'skills', 'config')), target,
                                           runtime_root=scope / 'runtime', config_format=fmt)
        manager = offline.RuntimeLifecycle(engine.runtime_store, [engine])
        config = engine.config.path
        raw = config.read_bytes()
        foreign = (b'# preserve foreign removal comment\ntheme = "dark"\n' if fmt == 'toml'
                   else b'{"theme":"dark",')
        config.write_bytes(foreign + (raw if fmt == 'toml' else raw[1:]));config.chmod(0o640)
        run('apply', 'register-foreign-config', apply)
        extra = scope / 'skills' / sorted(offline.USER_SKILLS)[0] / 'user-material.txt'
        extra.write_bytes(b'local user material\n');extra.chmod(0o644)
        seal_path = scope / 'runtime/lifecycle/seal.json'
        seal_sha = tx.sha(seal_path.read_bytes())

        def resources():
            return {'config_sha256': tx.sha(config.read_bytes()), 'config_mode': stat.S_IMODE(config.stat().st_mode),
                    'skills_revision': engine.skills.snapshot()[1], 'config_revision': engine.config.snapshot()[1],
                    'runtime_revision': engine.runtime.snapshot()[1],
                    'pointer_sha256': tx.sha(tx.read(engine.runtime.root / 'runtime.json'))
                    if tx.read(engine.runtime.root / 'runtime.json') is not None else None,
                    'skills': {name: {'sha256': tx.sha((engine.skills.root / name).read_bytes()),
                                      'mode': stat.S_IMODE((engine.skills.root / name).stat().st_mode)}
                               for name in engine.skills.snapshot()[0]['files']},
                    'user_file_sha256': tx.sha(extra.read_bytes()), 'seal_sha256': tx.sha(seal_path.read_bytes())}

        def check_installed():
            current = resources()
            tx.require(current['skills'] == {n: {'sha256': tx.sha(b), 'mode': modes[n]} for n, b in payload.items()}
                       and current['config_mode'] == 0o640 and current['seal_sha256'] == seal_sha,
                       'UNINSTALL_INSTALLED_CONTENT_CHANGED')
            return current

        def check_removed():
            current = resources()
            tx.require(engine.skills.snapshot()[0] == tx.empty_ledger()
                       and engine.runtime.snapshot()[0] == tx.empty_ledger()
                       and current['pointer_sha256'] is None
                       and engine.config.snapshot()[0]['owned_sha256'] is None
                       and current['config_mode'] == 0o640 and current['seal_sha256'] == seal_sha
                       and all(not (engine.skills.root / name).exists() for name in payload)
                       and extra.read_bytes() == b'local user material\n'
                       and b'vf-studio' not in config.read_bytes(), 'UNINSTALL_REMOVAL_CONTENT_CHANGED')
            if fmt == 'json':
                tx.require(json.loads(config.read_bytes())['theme'] == 'dark', 'UNINSTALL_FOREIGN_CONFIG_CHANGED')
            else:
                tx.require(b'# preserve foreign removal comment' in config.read_bytes()
                           and b'theme = "dark"' in config.read_bytes(), 'UNINSTALL_FOREIGN_CONFIG_CHANGED')
            return current

        def cli(label):
            cp = manager.run(digest, 'bin/studio-workspace', ['--version'], cwd=output, env=env,
                             capture_output=True, text=True, timeout=30)
            capture(cp, fmt + '-' + label, 0)
            tx.require(cp.stdout.strip() == version, 'UNINSTALL_EXECUTED_VERSION_MISMATCH')

        baseline = check_installed();cli('version-before')
        with manager.lease(digest):
            run('uninstall', 'activity-rejected', {'interrupt_after': None}, 2, 'RUNTIME_ACTIVITY_BUSY')
        tx.require(resources() == baseline, 'UNINSTALL_BUSY_CHANGED_RESOURCES')

        # A native refusal must preserve a user edit and restore any earlier child.
        name = sorted(payload)[0];path = engine.skills.root / name
        path.write_bytes(b'user changed this skill\n')
        changed = resources()
        run('uninstall', 'modified-skill-rejected', {'interrupt_after': None}, 2, 'LOCAL_CHANGE_CONFLICT')
        tx.require(resources() == changed, 'UNINSTALL_USER_EDIT_OVERWRITTEN')
        path.write_bytes(payload[name]);path.chmod(modes[name])

        boundary_outcomes = []
        for phase in ('runtime', 'skills', 'config', 'committed'):
            before = check_installed()
            run('uninstall', phase + '-exit', {'interrupt_after': phase}, 73)
            observed = run('status', phase + '-status')
            tx.require(observed['operation_state'] == phase, 'UNINSTALL_BOUNDARY_MISMATCH')
            if phase != 'committed':
                inventory = run('inventory', phase + '-pending-inventory')['inventory']
                tx.require(inventory['versions'][digest]['references'], 'UNINSTALL_OLD_TREE_UNPROTECTED')
            run('recover', phase + '-recover');run('recover', phase + '-recover-again')
            if phase == 'committed':
                check_removed()
            else:
                tx.require(resources() == before, 'UNINSTALL_RECOVERY_CHANGED_RESOURCES')
            boundary_outcomes.append({'phase': phase, 'process_exit': 73,
                                      'result': 'removed' if phase == 'committed' else 'restored'})
        removed = check_removed()
        again = run('uninstall', 'remove-again', {'interrupt_after': None})
        tx.require(again['skill_files_removed'] == 0 and resources() == removed, 'UNINSTALL_REPEAT_CHANGED_RESOURCES')
        inventory = run('inventory', 'removed-inventory')['inventory']
        tx.require(not inventory['versions'][digest]['references'], 'UNINSTALL_COMPLETED_TARGET_PROTECTED')
        run('collect', 'collect-runtime', {'runtime_tree_sha256': digest, 'inventory_revision': inventory['revision']})
        tx.require(run('inventory', 'collected-inventory')['inventory']['versions'] == {}, 'UNINSTALL_TREE_NOT_COLLECTED')
        run('apply', 'reinstall-original', apply);check_installed();cli('version-reinstalled')
        result = run('uninstall', 'final-remove', {'interrupt_after': None});final = check_removed()
        tx.require(result['skill_files_removed'] == len(payload) and result['uninstall_acceptance'] is False,
                   'UNINSTALL_RECEIPT_MISMATCH')
        final_inventory = run('inventory', 'final-inventory')['inventory']
        run('collect', 'final-collect', {'runtime_tree_sha256': digest,
                                       'inventory_revision': final_inventory['revision']})
        tx.require(run('inventory', 'final-collected-inventory')['inventory']['versions'] == {},
                   'UNINSTALL_FINAL_TREE_NOT_COLLECTED')
        tx.require(resources() == final, 'UNINSTALL_GC_CHANGED_OTHER_RESOURCES')
        outcomes.append({'config_format': fmt, 'skill_packages': packages, 'skill_files': len(payload),
                         'boundary_outcomes': boundary_outcomes, 'runtime_collected': True,
                         'original_release_reinstalled_and_executed': True, 'final_resources': final})
    execute('verify-after', ['verify'])
    receipt = {'schema': 'vf.bootstrap-uninstall-probe/v1', 'native_uninstall_engineering_verified': True,
               'manifest_sha256': manifest_sha, 'binary_sha256': binary_sha,
               'sample_sha256': sample_sha, 'runtime_tree_sha256': digest, 'release': release,
               'runtime_release': manifest['release'], 'source_files': source_files,
               'probe_source_sha256': tx.sha(Path(__file__).read_bytes()), 'commands': commands,
               'outcomes': outcomes, 'uninstall_acceptance': False, 'installable': False,
               'windows_execution': False, 'desktop_acceptance': False, 'mcp_acceptance': False}
    (output / 'probe-receipt.json').write_bytes(tx.encode(receipt))
    return receipt


def main():
    parser = argparse.ArgumentParser()
    for field in ('bundle', 'manifest-sha', 'binary-sha', 'sample', 'sample-sha', 'version', 'output'):
        parser.add_argument('--' + field, required=True)
    args = parser.parse_args()
    result = probe(args.bundle, args.manifest_sha, args.binary_sha, args.sample, args.sample_sha, args.version, args.output)
    print(json.dumps({'status': 'native_uninstall_engineering_verified', 'commands': len(result['commands']),
                      'uninstall_acceptance': False}))


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        code = str(exc)
        if not code or not all(c in 'ABCDEFGHIJKLMNOPQRSTUVWXYZ_' for c in code):
            code = 'UNINSTALL_PROBE_FAILED'
        print(json.dumps({'error': code}), file=sys.stderr)
        raise SystemExit(2)
