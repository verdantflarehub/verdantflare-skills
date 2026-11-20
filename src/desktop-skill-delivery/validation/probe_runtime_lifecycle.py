"""Actual composed bytes through fresh POSIX install/update/use/GC engineering.

The second tree changes only the local engineering release label. It is an
update fixture, not a second signed/composed release or desktop acceptance.
"""
from __future__ import annotations

import argparse
import copy
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import uuid

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))
sys.path.insert(0, str(TOOLS / 'runtime_tools'))
from runtime_tools import runtime_lifecycle as lc
import runtime_composition as composition
from probe_composed_install import skills_from_sample


def probe(candidate, expected_sha, sample, sample_sha, output):
    composition.need(platform.system() == 'Darwin' and platform.machine() == 'arm64', 'MACOS_ARM64_PROBE_REQUIRED')
    candidate = candidate.absolute()
    output = output.absolute()
    verification = composition.verify_folder(candidate, expected_sha)
    composition.need(verification['platform'] == 'macos-arm64', 'MACOS_COMPONENT_REQUIRED')
    payload, packages, release = skills_from_sample(sample, sample_sha)
    composition.portable.bundle.no_links(output)
    composition.need(not output.exists() and output.parent.is_dir()
                     and candidate not in output.parents and output != candidate, 'NEW_OUTPUT_REQUIRED')
    output.mkdir(mode=0o700)
    for name in ('state', 'skills', 'config', 'runtime', 'logs'):
        (output / name).mkdir(mode=0o700)
    store = lc.rt.RuntimeStore(output / 'runtime')
    scope = lc.operation.InstallOperation(*(output / n for n in ('state', 'skills', 'config')),
                                         'mcp.json', runtime_root=store.root)
    scope.config.path.write_bytes(b'{"theme":"dark","mcpServers":{"foreign":{"command":"keep"}}}\n')
    scope.config.path.chmod(0o640)
    manager = lc.RuntimeLifecycle(store, [scope])
    manager.initialize()
    key = next(iter(manager.scopes))
    original = composition.decode(composition.workspace.read(candidate / 'tree-manifest.json', lc.tx.MAX_FILE))
    sources = {name: candidate / 'objects' / entry['sha256'] for name, entry in original['entries'].items()
               if entry['type'] == 'file'}
    original_digest = verification['tree_sha256']
    fixture = copy.deepcopy(original)
    fixture['release'] = 'lifecycle-update.fixture.1'
    fixture_digest = lc.tx.sha(lc.tx.encode(fixture))
    for manifest, digest in ((original, original_digest), (fixture, fixture_digest)):
        manager.stage(manifest, sources, expected_digest=digest, transaction_id=uuid.uuid4().hex)

    def install(digest):
        observed = scope.config.observe()
        result = manager.apply(key, release, payload, {'url': 'https://mcp.invalid/engineering'},
            expected_skill_revision=scope.skills.snapshot()[1], expected_config_revision=observed['ledger_revision'],
            expected_config_file=observed['file'], runtime_digest=digest,
            expected_runtime_revision=scope.runtime.snapshot()[1])
        manager.recover_operation(key)
        return result

    initial_install = install(original_digest)
    env = {'PATH': '', 'LANG': 'C', 'LC_ALL': 'C', 'VF_RUNTIME_CACHE': str(output / 'cache')}
    commands = []

    def run(digest, label):
        result = manager.run(digest, 'bin/vf-python', ['info'], env=env, cwd=output,
                             capture_output=True, text=True, timeout=120)
        for stream in ('stdout', 'stderr'):
            sanitized = getattr(result, stream).replace(str(output), '%PROBE%').replace(str(Path.home()), '%USERPROFILE%')
            (output / 'logs' / (label + '.' + stream)).write_text(sanitized, encoding='utf-8', newline='\n')
        composition.need(result.returncode == 0, 'LIFECYCLE_COMMAND_FAILED')
        commands.append({'label': label, 'exit_code': result.returncode})
        return json.loads(result.stdout)

    first_info = run(original_digest, 'original-use')
    before = manager.inventory()
    try:
        manager.collect(original_digest, expected_revision=before['revision'])
    except lc.tx.Conflict as exc:
        composition.need(str(exc) == 'RUNTIME_VERSION_PROTECTED', 'WRONG_REFERENCE_REFUSAL')
    else:
        raise ValueError('REFERENCED_TREE_COLLECTED')
    updated = install(fixture_digest)
    updated_info = run(fixture_digest, 'updated-use')
    try:
        with manager.lease(fixture_digest):
            manager.inventory()
    except lc.tx.Conflict as exc:
        composition.need(str(exc) == 'RUNTIME_ACTIVITY_BUSY', 'WRONG_ACTIVITY_REFUSAL')
    else:
        raise ValueError('ACTIVITY_NOT_PROTECTED')
    inventory = manager.inventory()
    composition.need(not inventory['versions'][original_digest]['references'], 'OLD_TREE_STILL_REFERENCED')

    class Interruption(BaseException):
        pass

    def interrupt(phase):
        if phase == 'removed:journal.json':
            raise Interruption()

    try:
        manager.collect(original_digest, expected_revision=inventory['revision'], checkpoint=interrupt)
    except Interruption:
        pass
    else:
        raise ValueError('EXPECTED_GC_INTERRUPTION')
    composition.need(not (store.versions / original_digest).exists(), 'OLD_VERSION_NOT_QUARANTINED')
    collected = manager.recover_gc()
    final_info = run(fixture_digest, 'after-gc-use')
    final = manager.inventory()
    composition.need(set(final['versions']) == {fixture_digest} and not final['blockers'], 'FINAL_VERSION_SET_CHANGED')
    composition.need(all((scope.skills.root / n).read_bytes() == raw for n, raw in payload.items()), 'INSTALLED_SKILL_CHANGED')
    config = composition.decode(scope.config.path.read_bytes())
    composition.need(config['theme'] == 'dark' and config['mcpServers']['foreign'] == {'command': 'keep'}
                     and scope.config.path.stat().st_mode & 0o777 == 0o640, 'FOREIGN_CONFIG_CHANGED')
    composition.verify_folder(candidate, expected_sha)
    receipt = {'schema': 'vf.runtime-lifecycle-probe/v1', 'status': 'isolated_lifecycle_verified',
               'descriptor_sha256': expected_sha, 'sample_sha256': sample_sha,
               'original_tree_sha256': original_digest, 'update_fixture_tree_sha256': fixture_digest,
               'update_fixture_changes_only_release_label': True, 'real_runtime_entries': len(original['entries']),
               'skill_packages': len(packages), 'skill_files': len(payload), 'commands': commands,
               'initial_install': initial_install['status'], 'update': updated['status'],
               'collection': collected['status'], 'referenced_tree_refused': True, 'activity_refused': True,
               'interruption_after_first_unlink_recovered': True, 'old_tree_removed': True,
               'new_tree_verified': True, 'foreign_config_and_mode_preserved': True,
               'python_versions': [first_info['python'], updated_info['python'], final_info['python']],
               'initial_PATH_empty': True, 'installable': False, 'independent_review': 'pending',
               'host_verified': False, 'desktop_acceptance': False, 'mcp_acceptance': False,
               'windows_execution': False, 'public_distribution': False}
    (output / 'probe-receipt.json').write_bytes(composition.encode(receipt))
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('candidate', 'sample', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    for name in ('sha256', 'sample-sha256'):
        parser.add_argument('--' + name, required=True)
    args = parser.parse_args()
    print(json.dumps(probe(args.candidate, args.sha256, args.sample, args.sample_sha256, args.output)))


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, KeyError, TypeError, subprocess.TimeoutExpired) as exc:
        code = str(exc) if isinstance(exc, ValueError) and __import__('re').fullmatch('[A-Z_]+', str(exc)) else 'LIFECYCLE_PROBE_FAILED'
        print(json.dumps({'error': code}), file=sys.stderr)
        raise SystemExit(2)
