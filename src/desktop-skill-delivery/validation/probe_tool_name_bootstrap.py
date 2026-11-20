"""Run the packaged diagnostic offline; synthetic confirmations are not MCP proof."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess


def run(bundle, output):
    bundle, output = Path(bundle).absolute(), Path(output).absolute()
    workspace = Path(__file__).resolve().parents[4]
    assert output.is_relative_to(workspace / '.tmp') and not output.exists()
    assert bundle.is_relative_to(workspace / '.tmp') and (bundle / 'vf-bootstrap').is_file()
    output.mkdir(mode=0o700)
    fixture = output / 'private snapshot with spaces'
    fixture.mkdir(mode=0o700)
    names = json.loads((Path(__file__).resolve().parents[1] /
        'profile_tests/fixtures/studio-tool-names.json').read_bytes())['names']
    pairs = sorted((name, name.replace('_', '_u').replace('.', '__')) for name in names)
    digest = 'sha256:' + hashlib.sha256(''.join(a + '\t' + b + '\n' for a, b in pairs).encode()).hexdigest()
    initial = {'_meta': {'verdantflare.ai/tool-name-profile': 'portable-v1'}}
    directory = {'_meta': {**initial['_meta'], 'verdantflare.ai/tool-name-map-digest': digest},
        'tools': [{'name': b, 'description': 'DO_NOT_DISCLOSE_REMOTE_TEXT\nVF canonical tool: ' + a + '.',
                   'inputSchema': {'type': 'object'}} for a, b in pairs]}
    request = fixture / 'request.json'
    def write(name, raw):
        path = fixture / name
        path.write_bytes(raw)
        path.chmod(0o600)
    def prepare(init):
        first, second = json.dumps(init).encode(), json.dumps(directory).encode()
        write('initialize-result.json', first)
        write('tools-result.json', second)
        value = {'schema': 'vf.tool-name-profile-request/v1',
                 'initialize_sha256': hashlib.sha256(first).hexdigest(),
                 'tools_sha256': hashlib.sha256(second).hexdigest(), 'observed_prefix': 'mcp_vf_'}
        write('request.json', json.dumps(value).encode())
    def snapshot():
        return {p.name: (hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_mode, p.stat().st_mtime_ns)
                for p in fixture.iterdir()}
    records = []
    def command(label, expected, extra=None):
        before = snapshot()
        args = extra or ['tool-name-profile', '--request', str(request)]
        cp = subprocess.run(['/usr/bin/sandbox-exec', '-p',
            '(version 1) (allow default) (deny network*) (deny file-write*)',
            str(bundle / 'vf-bootstrap'), *args], cwd=output,
            env={'PATH': '', 'LANG': 'C', 'LC_ALL': 'C'}, capture_output=True, timeout=60)
        assert before == snapshot(), 'SNAPSHOT_WAS_WRITTEN'
        combined = cp.stdout + cp.stderr
        assert b'DO_NOT_DISCLOSE_REMOTE_TEXT' not in combined
        assert str(workspace).encode() not in combined and str(fixture).encode() not in combined
        if expected == 'success':
            assert cp.returncode == 0, 'DIAGNOSTIC_REJECTED_VALID_SNAPSHOT'
            result = json.loads(cp.stdout)
            assert result['mapping_sha256'] == digest and result['tool_count'] == 100
            assert result['status'] == 'local_snapshot_checked'
            assert all(result[key] is False for key in ('host_verified', 'desktop_acceptance',
                'mcp_acceptance', 'server_authenticated', 'configuration_written', 'network_requested'))
        else:
            assert cp.returncode == 2 and expected.encode() in cp.stderr, 'DIAGNOSTIC_REJECTION_MISSING'
            result = {'error': expected}
        records.append({'case': label, 'exit_code': cp.returncode,
            'stdout_sha256': hashlib.sha256(cp.stdout).hexdigest(),
            'stderr_sha256': hashlib.sha256(cp.stderr).hexdigest(), 'result': result})
    prepare(initial)
    command('confirmed-synthetic-100-tool-directory', 'success')
    prepare({})
    command('old-server-without-confirmation', 'TOOL_NAME_PROFILE_UNSUPPORTED')
    prepare(initial)
    write('tools-result.json', b'{"private":"DO_NOT_DISCLOSE_REMOTE_TEXT"}')
    command('changed-directory-hash', 'TOOL_NAME_INPUT_HASH')
    prepare(initial)
    request.chmod(0o644)
    command('nonprivate-request', 'PRIVATE_FILE_REQUIRED')
    request.chmod(0o600)
    command('invalid-cli-shape', 'BOOTSTRAP_ARGUMENTS_INVALID', ['tool-name-profile', '--output', 'unused'])
    receipt = {'schema': 'vf.bootstrap-tool-name-diagnostic-probe/v1', 'cases': records,
        'manifest_sha256': hashlib.sha256((bundle / 'bootstrap-manifest.json').read_bytes()).hexdigest(),
        'tool_count': 100, 'mapping_sha256': digest, 'initial_PATH_empty': True,
        'host_python_required': False, 'command_network_denied': True, 'command_file_writes_denied': True,
        'metadata_and_descriptions_synthetic': True, 'live_server_requested': False,
        'actual_native_prefix_tested': False, 'windows_execution': False,
        'installer_acceptance': False, 'desktop_acceptance': False, 'mcp_acceptance': False}
    (output / 'probe-receipt.json').write_text(json.dumps(receipt, sort_keys=True, indent=2) + '\n',
                                             encoding='utf-8', newline='\n')
    return receipt


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundle', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    try:
        result = run(args.bundle, args.output)
    except Exception:
        print(json.dumps({'error': 'BOOTSTRAP_TOOL_NAME_PROBE_FAILED'}))
        raise SystemExit(2)
    print(json.dumps(result, sort_keys=True))
