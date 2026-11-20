"""Install the actual 11-skill handoff and composed runtime via native settings.

Uses a retained official isolated backend, never a real desktop/profile or
credential. Test exception recovery here complements, not replaces, the real
coordinator exit tests. No creative generation or network request is made.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import platform
import re
import shutil
import stat
import subprocess
import sys
import uuid
import wave

TOOLS = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(TOOLS), str(TOOLS / 'runtime_tools'), str(TOOLS / 'installer_tools')]
import install_transaction as io
import host_deepseek as h
from host_deepseek_journal import OriginJournal
from host_deepseek_operation import NativeOperation
from native_install_operation import NativeInstallOperation
from deepseek_management_backend import NativeBackend
import runtime_composition as composition
from offline_operation import skills


class Interruption(BaseException):
    pass


def probe(candidate, expected_sha, sample, sample_sha, modules, output):
    io.require(platform.system() == 'Darwin' and platform.machine() == 'arm64', 'MACOS_ARM64_PROBE_REQUIRED')
    candidate, output = candidate.absolute(), output.absolute()
    io.no_links(output)
    repo = TOOLS.parents[2]
    io.require(output.is_relative_to(repo / '.tmp') and output.parent.is_dir()
               and not output.exists(), 'NEW_TEMP_OUTPUT_REQUIRED')
    io.require(candidate != output and candidate not in output.parents, 'PROBE_ROOT_OVERLAP')
    verified = composition.verify_folder(candidate, expected_sha)
    io.require(verified['platform'] == 'macos-arm64', 'MACOS_COMPONENT_REQUIRED')
    payload, modes, release, packages = skills(sample.absolute(), sample_sha)
    manifest = io.decode((candidate / 'tree-manifest.json').read_bytes())
    sources = {name: candidate / 'objects' / record['sha256']
               for name, record in manifest['entries'].items() if record['type'] == 'file'}
    output.mkdir(mode=0o700)
    for name in ('state', 'skills', 'runtime with spaces', 'logs'):
        (output / name).mkdir(mode=0o700)
    commands = []
    def redact(value):
        return value.replace(str(output), '%PROBE%').replace(str(candidate), '%CANDIDATE%').replace(
            str(Path.home()), '%USERPROFILE%')
    def run(name, args, expected=0):
        sandbox = shutil.which('sandbox-exec')
        io.require(sandbox is not None, 'MACOS_SANDBOX_REQUIRED')
        cp = subprocess.run([sandbox, '-p', '(version 1)(allow default)(deny network*)',
            *[str(arg) for arg in args]], cwd=output,
            env={'PATH': '', 'VF_RUNTIME_CACHE': str(output / 'cache'), 'LANG': 'C', 'LC_ALL': 'C'},
            capture_output=True, text=True, timeout=120)
        stdout, stderr = redact(cp.stdout), redact(cp.stderr)
        (output / 'logs' / (name + '.stdout')).write_text(stdout, encoding='utf-8', newline='\n')
        (output / 'logs' / (name + '.stderr')).write_text(stderr, encoding='utf-8', newline='\n')
        commands.append({'step': name, 'exit_code': cp.returncode})
        io.require(cp.returncode == expected, 'NATIVE_INSTALL_TOOL_COMMAND_FAILED')
        return stdout
    with NativeBackend(modules, output / 'native-fixture') as backend:
        journal = OriginJournal(output / 'state', backend.identity)
        native = NativeOperation(journal, backend.identity, backend)
        operation = NativeInstallOperation(output / 'state', output / 'skills', output / 'runtime with spaces', native)
        original = h.read(backend.observe().descriptor)['user']
        operation.runtime_store.stage(manifest, sources, expected_digest=verified['tree_sha256'],
                                      transaction_id=uuid.uuid4().hex)
        (operation.skills.root / 'my-notes.txt').write_bytes(b'USER_CONTENT_RETAINED\n')
        desired = h.desired_connection('https://studio.invalid/mcp', 'VF_STUDIO')
        def install(checkpoint=lambda _: None):
            return operation.apply(release, payload, desired, runtime_digest=verified['tree_sha256'],
                                   modes=modes, checkpoint=checkpoint, **operation.preflight())
        def interrupt(stage):
            if stage == 'native:submitted':
                raise Interruption()
        try:
            install(interrupt)
        except Interruption:
            pass
        else:
            raise io.Conflict('EXPECTED_NATIVE_INSTALL_INTERRUPTION')
        io.require(journal.snapshot()[0]['pending'] is not None and backend.mutations == 1,
                   'NATIVE_INSTALL_PENDING_NOT_PRESERVED')
        count = backend.mutations
        operation.recover(); operation.recover()
        io.require(backend.mutations == count and operation._active()['state'] == 'cleaned',
                   'NATIVE_INSTALL_RECOVERY_REPLAYED')
        def check_payload():
            for name, raw in payload.items():
                path = operation.skills.root / name
                io.require(path.read_bytes() == raw and stat.S_IMODE(path.stat().st_mode) == modes[name],
                           'NATIVE_INSTALLED_SKILL_CHANGED')
            io.require((operation.skills.root / 'my-notes.txt').read_bytes() == b'USER_CONTENT_RETAINED\n',
                       'USER_CONTENT_CHANGED')
        check_payload()
        pointer = io.decode((operation.runtime.root / 'runtime.json').read_bytes())
        io.require(pointer['tree_sha256'] == verified['tree_sha256'], 'NATIVE_RUNTIME_POINTER_CHANGED')
        tree = operation.runtime_store.versions / verified['tree_sha256'] / 'tree'
        info = json.loads(run('python-info', [tree / 'bin/vf-python', 'info']))
        versions = {}
        for tool, flag in (('ffmpeg', '-version'), ('ffprobe', '-version'), ('studio-workspace', '--version')):
            text = run(tool + '-version', [tree / 'bin' / tool, flag])
            io.require(bool(text.splitlines()), 'TOOL_VERSION_MISSING')
            versions[tool] = text.splitlines()[0]
        no_session = run('workspace-requires-session', [tree / 'bin/studio-workspace', 'tools'], expected=1)
        logs = no_session + (output / 'logs/workspace-requires-session.stderr').read_text()
        io.require('STUDIO_SESSION_CONFIGURATION_REQUIRED' in logs, 'WORKSPACE_SESSION_BOUNDARY_FAILED')
        audio = output / 'synthetic-silence.wav'
        with wave.open(str(audio), 'wb') as stream:
            stream.setnchannels(1); stream.setsampwidth(2); stream.setframerate(16000)
            stream.writeframes(b'\0\0' * 16000)
        analysis = json.loads(run('installed-music-helper', [tree / 'bin/vf-python', 'script',
            operation.skills.root / 'verdantflare-music/scripts/analyze_tail.py', audio, '--planned-duration', '1']))
        io.require(analysis['status'] == 'passed' and analysis['duration_seconds'] == 1,
                   'INSTALLED_MUSIC_HELPER_FAILED')
        install(); operation.recover()
        io.require(backend.mutations == count, 'NOOP_NATIVE_WRITE_UNEXPECTED')
        operation.uninstall(**operation.preflight()); operation.recover()
        io.require(not (operation.runtime.root / 'runtime.json').exists()
                   and all(not (operation.skills.root / name).exists() for name in payload)
                   and h.read(backend.observe().descriptor)['user'] == original,
                   'NATIVE_RESOURCE_REMOVAL_FAILED')
        io.require((operation.skills.root / 'my-notes.txt').read_bytes() == b'USER_CONTENT_RETAINED\n',
                   'USER_CONTENT_CHANGED')
        install(); operation.recover(); check_payload()
        operation.runtime_store.verify(verified['tree_sha256'])
        composition.verify_folder(candidate, expected_sha)
        io.require(backend.mutations == 3 and operation._active()['state'] == 'cleaned',
                   'NATIVE_INSTALL_FINAL_STATE_FAILED')
        receipt = {'schema': 'vf.native-composed-install-probe/v1',
            'status': 'isolated_native_composed_install_verified', 'descriptor_sha256': expected_sha,
            'sample_sha256': sample_sha, 'runtime_tree_sha256': verified['tree_sha256'],
            'runtime_entries': len(manifest['entries']), 'skill_packages': packages,
            'skill_files': len(payload), 'skill_bytes': sum(map(len, payload.values())),
            'file_bytes_and_modes_verified': True, 'runtime_parent_contains_spaces': True,
            'python_exception_at_native_submitted': True, 'real_coordinator_exit': False,
            'recovery_native_writes': 0, 'native_mutations': backend.mutations,
            'original_user_layer_restored_after_removal': True, 'user_content_preserved': True,
            'same_release_reinstalled': True, 'noop_native_writes': 0,
            'python_info': info, 'tool_versions': versions, 'commands': commands,
            'installed_music_helper_executed': True, 'initial_PATH_empty': True,
            'tool_commands_network_denied': True, 'native_fixture_network_denied': True,
            'python_coordinator_sandboxed': False, 'runtime_fixture': False,
            'desktop_acceptance': False, 'mcp_acceptance': False, 'installer_acceptance': False,
            'windows_execution': False, 'signed_release': False, 'independent_review': 'pending'}
    io.atomic(output / 'probe-receipt.json', h.encode(receipt) + b'\n', mode=0o600)
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for field in ('candidate', 'sample', 'modules', 'output'):
        parser.add_argument('--' + field, required=True, type=Path)
    for field in ('sha256', 'sample-sha256'):
        parser.add_argument('--' + field, required=True)
    args = parser.parse_args()
    print(json.dumps(probe(args.candidate, args.sha256, args.sample, args.sample_sha256, args.modules, args.output)))


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        code = str(error) if isinstance(error, (io.Conflict, h.Conflict, ValueError)) and re.fullmatch(
            '[A-Z0-9_]{1,80}', str(error)) else 'NATIVE_COMPOSED_INSTALL_PROBE_FAILED'
        print(json.dumps({'error': code}), file=sys.stderr)
        sys.exit(2)
