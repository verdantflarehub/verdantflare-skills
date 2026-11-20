"""Actual 11-package/runtime bytes through the isolated WorkBuddy profile.

Engineering validation only: no vendor program, account, network or real user
configuration. The .invalid URL is an explicit fixture, not source admission.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import uuid
import wave

TOOLS = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(TOOLS), str(TOOLS / 'runtime_tools'), str(TOOLS / 'installer_tools')]
import runtime_composition as composition
import install_transaction as tx
from install_operation import InstallOperation
from runtime_tools.runtime_lifecycle import RuntimeLifecycle
import host_workbuddy as w
from offline_operation import skills

URL = 'https://workbuddy-engineering.invalid/mcp/desktop/fixture_connection'


def probe(candidate, candidate_sha, sample, sample_sha, native_source, output):
    tx.require(platform.system() == 'Darwin' and platform.machine() == 'arm64', 'MACOS_ARM64_PROBE_REQUIRED')
    candidate, sample, native_source, output = (Path(p).absolute() for p in
                                                (candidate, sample, native_source, output))
    for path in (candidate, sample, native_source, output):
        tx.no_links(path)
    tx.require(not output.exists() and output.parent.is_dir()
               and not any(p == output or output in p.parents or p in output.parents
                           for p in (candidate, sample, native_source)), 'NEW_EXTERNAL_OUTPUT_REQUIRED')
    verified = composition.verify_folder(candidate, candidate_sha)
    tx.require(verified['platform'] == 'macos-arm64', 'MACOS_COMPONENT_REQUIRED')
    payload, modes, release, count = skills(sample, sample_sha)
    tx.require(tx.sha(native_source.read_bytes()) ==
               '06756026779070ba66d04233a5507085cbb710689b34f8962b138393214dbe71',
               'WORKBUDDY_NATIVE_SOURCE_MISMATCH')
    output.mkdir(mode=0o700)
    for name in ('state', 'workbuddy', 'workbuddy/skills', 'runtime', 'logs'):
        (output / name).mkdir(mode=0o700)
    e = InstallOperation(*(output / n for n in ('state', 'workbuddy/skills', 'workbuddy')), 'mcp.json',
                         runtime_root=output / 'runtime', config_profile='workbuddy', admitted_url=URL)
    initial = b'{ "theme":"dark", "mcpServers":{"foreign":{"command":"keep"}} }\r\n'
    e.config.path.write_bytes(initial)
    e.config.path.chmod(0o640)
    manager = RuntimeLifecycle(e.runtime_store, [e])
    manager.initialize()
    key = next(iter(manager.scopes))
    manifest = composition.decode(composition.workspace.read(candidate / 'tree-manifest.json', tx.MAX_FILE))
    sources = {name: candidate / 'objects' / row['sha256'] for name, row in manifest['entries'].items()
               if row['type'] == 'file'}
    digest = verified['tree_sha256']
    manager.stage(manifest, sources, expected_digest=digest, transaction_id=uuid.uuid4().hex)

    def state():
        return e.runtime.snapshot(), e.skills.snapshot(), e.config.snapshot(), e.config._file()

    def install(checkpoint=lambda _: None):
        observed = e.config.observe()
        return manager.apply(key, release, payload, w.desired_connection(URL, admitted_url=URL),
            modes=modes, expected_skill_revision=e.skills.snapshot()[1],
            expected_config_revision=observed['ledger_revision'], expected_config_file=observed['file'],
            runtime_digest=digest, expected_runtime_revision=e.runtime.snapshot()[1], checkpoint=checkpoint)

    def settle():
        for _ in range(3):
            manager.recover_operation(key)

    class Interruption(BaseException):
        pass

    def interrupt(phase):
        if phase == 'config':
            raise Interruption()

    before = state()
    try:
        install(interrupt)
    except Interruption:
        pass
    else:
        raise ValueError('WORKBUDDY_EXPECTED_INTERRUPTION')
    interrupted_id = e._active()['id']
    settle()
    tx.require(state() == before and e._active()['id'] == interrupted_id
               and e._active()['state'] == 'cleaned_aborted', 'WORKBUDDY_THREE_RESOURCE_RECOVERY_MISMATCH')
    applied = install()
    settle()
    for name, raw in payload.items():
        tx.require((e.skills.root / name).read_bytes() == raw, 'WORKBUDDY_INSTALLED_SKILL_CHANGED')
    tx.require(e.skills.root == e.config.root / 'skills'
               and e.skills.meta == output / 'state/.vf-workbuddy-skill-files'
               and not (e.skills.root / tx.META).exists(), 'WORKBUDDY_NATIVE_LAYOUT_MISMATCH')
    node = json.loads(e.config.path.read_bytes())['mcpServers']['vf-studio']
    native = subprocess.run(['node', str(TOOLS / 'workbuddy_tests/native_consumer.cjs'), str(native_source)],
                            input=json.dumps(node).encode(), capture_output=True, timeout=10)
    tx.require(native.returncode == 0, 'WORKBUDDY_NATIVE_CONSUMER_FAILED')
    native_result = json.loads(native.stdout)
    commands = []
    env = {'PATH': '', 'LANG': 'C', 'LC_ALL': 'C', 'VF_RUNTIME_CACHE': str(output / 'cache')}

    def run(label, entry, arguments):
        result = manager.run(digest, entry, list(map(str, arguments)), env=env, cwd=output,
                             capture_output=True, text=True, timeout=90)
        for stream in ('stdout', 'stderr'):
            raw = getattr(result, stream).replace(str(output), '%PROBE%').replace(str(Path.home()), '%USERPROFILE%')
            (output / 'logs' / (label + '.' + stream)).write_text(raw, encoding='utf-8', newline='\n')
        commands.append({'label': label, 'exit_code': result.returncode})
        tx.require(result.returncode == 0, 'WORKBUDDY_MANAGED_COMMAND_FAILED')
        return result.stdout

    python_info = json.loads(run('managed-python', 'bin/vf-python', ['info']))
    run('managed-project-cli', 'bin/studio-workspace', ['--version'])
    fixture = output / 'synthetic-silence.wav'
    with wave.open(str(fixture), 'wb') as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(16000)
        stream.writeframes(b'\0\0' * 16000)
    music_result = json.loads(run('installed-music-helper', 'bin/vf-python',
        ['script', e.skills.root / 'verdantflare-music/scripts/analyze_tail.py', fixture, '--planned-duration', '1']))
    tx.require(music_result['status'] == 'passed' and music_result['duration_seconds'] == 1,
               'WORKBUDDY_INSTALLED_HELPER_FAILED')
    tx.require(json.loads(e.config.path.read_bytes())['theme'] == 'dark'
               and e.config.path.stat().st_mode & 0o777 == 0o640, 'WORKBUDDY_FOREIGN_CONFIG_CHANGED')
    observed = e.config.observe()
    removed = manager.uninstall(key, expected_skill_revision=e.skills.snapshot()[1],
        expected_config_revision=observed['ledger_revision'], expected_config_file=observed['file'],
        expected_runtime_revision=e.runtime.snapshot()[1])
    settle()
    tx.require(e.config.path.read_bytes() == initial and not (e.runtime.root / 'runtime.json').exists()
               and all(not (e.skills.root / name).exists() for name in payload), 'WORKBUDDY_UNINSTALL_MISMATCH')
    composition.verify_folder(candidate, candidate_sha)
    receipt = {'schema': 'vf.workbuddy.composed-install-probe/v1',
        'status': 'isolated_workbuddy_resources_verified', 'candidate_sha256': candidate_sha,
        'sample_sha256': sample_sha, 'runtime_tree_sha256': digest, 'release': release,
        'skill_packages': count, 'skill_files': len(payload), 'runtime_entries': len(manifest['entries']),
        'resource_layout': 'workbuddy-config-with-direct-skills-child',
        'recovery_state_outside_discovery_tree': True,
        'config_profile': 'workbuddy', 'source_admission': 'synthetic_fixture_only',
        'interruption': 'in_process_at_config', 'three_resources_restored': True,
        'same_interrupted_operation': True, 'native_config_readback': native_result,
        'applied': applied['status'], 'removed': removed['status'], 'uninstall_original_bytes_restored': True,
        'commands': commands, 'python_info': python_info, 'installed_music_helper_executed': True,
        'network_called': False, 'user_config_changed': False, 'vendor_program_started': False,
        'desktop_acceptance': 'not_run', 'business_acceptance': 'not_run', 'installable': False}
    with (output / 'receipt.json').open('x', encoding='utf-8') as stream:
        json.dump(receipt, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    return receipt


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('candidate', 'candidate-sha256', 'sample', 'sample-sha256', 'native-source', 'output'):
        parser.add_argument('--' + name, required=True)
    args = parser.parse_args()
    try:
        result = probe(args.candidate, args.candidate_sha256, args.sample, args.sample_sha256,
                       args.native_source, args.output)
        print(json.dumps({k: result[k] for k in ('status', 'skill_packages', 'skill_files',
            'runtime_entries', 'config_profile', 'desktop_acceptance', 'business_acceptance')}))
    except (ValueError, OSError, subprocess.SubprocessError):
        print(json.dumps({'status': 'rejected', 'code': 'WORKBUDDY_INSTALL_PROBE_FAILED'}))
        raise SystemExit(2)
