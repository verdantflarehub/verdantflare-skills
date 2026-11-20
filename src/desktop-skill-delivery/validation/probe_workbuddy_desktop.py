"""Observe an existing signed WorkBuddy desktop; never operate or authenticate it."""
from __future__ import annotations

import argparse
import hashlib
import http.client
import json
import os
from pathlib import Path
import platform
import plistlib
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import host_discovery as discovery

require = discovery.require
Error = discovery.ObservationError
PORTS = (18488, 18489, 18490)
PROBE_PATH = '/workbuddy/probe'
VERSION = '5.11.3'
BUILD = '5.11.3'
PINNED_PLIST = '50b16223c35457e679dd6564acddcf7faa1f9650c95d3f151bd438185108dcb0'
PINNED_EXECUTABLE = 'e97c6d44a828b6c74e38a6c9cf333c66f23b9e8fadf1b8bdb384e26ae49e0495'
BUNDLE = 'com.tencent.workbuddy.mac'
TEAM = 'FN2V63AD2J'
MAX_BODY = 1024


def command(args, *, absent_ok=False):
    try:
        cp = subprocess.run(args, capture_output=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        raise Error('DESKTOP_SYSTEM_OBSERVATION_FAILED') from None
    if absent_ok and cp.returncode == 1 and not cp.stdout and not cp.stderr:
        return None
    require(cp.returncode == 0, 'DESKTOP_SYSTEM_OBSERVATION_FAILED')
    require(len(cp.stdout) <= 1024 * 1024, 'DESKTOP_SYSTEM_OUTPUT_LIMIT')
    return cp.stdout.decode('utf-8', errors='strict')


def listener(port):
    require(type(port) is int and port in PORTS, 'DESKTOP_FIXED_PORT_REQUIRED')
    raw = command(['/usr/sbin/lsof', '-nP', '-a', '-iTCP:' + str(port),
                   '-sTCP:LISTEN', '-Fpn'], absent_ok=True)
    if raw is None:
        return None
    pids = [line[1:] for line in raw.splitlines() if line.startswith('p')]
    addresses = [line[1:] for line in raw.splitlines() if line.startswith('n')]
    require(len(pids) == 1 and pids[0].isascii() and pids[0].isdigit()
            and int(pids[0]) > 0 and addresses == ['127.0.0.1:' + str(port)],
            'DESKTOP_LISTENER_MISMATCH')
    return pids[0]


def process_snapshot(pid, expected_executable):
    raw = command(['/usr/sbin/lsof', '-nP', '-a', '-p', pid, '-d', 'txt', '-Fn'])
    names = [line[1:] for line in raw.splitlines() if line.startswith('n')]
    require(names and names[0] == str(expected_executable), 'DESKTOP_PROCESS_MISMATCH')
    started = command(['/bin/ps', '-p', pid, '-o', 'lstart=']).strip()
    require(started and len(started) <= 100, 'DESKTOP_PROCESS_START_REQUIRED')
    return started


def verify_signature(app):
    requirement = ('anchor apple generic and identifier "' + BUNDLE
                   + '" and certificate leaf[subject.OU] = "' + TEAM + '"')
    command(['/usr/bin/codesign', '--verify', '--deep', '--strict',
             '-R=' + requirement, str(app)])


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, 'DESKTOP_RESPONSE_DUPLICATE_KEY')
        result[key] = value
    return result


def read_probe(port):
    require(type(port) is int and port in PORTS, 'DESKTOP_FIXED_PORT_REQUIRED')
    # HTTPConnection ignores proxy variables and never follows a redirect.
    connection = http.client.HTTPConnection('127.0.0.1', port, timeout=3)
    try:
        connection.request('GET', PROBE_PATH, headers={'Connection': 'close'})
        response = connection.getresponse()
        require(response.status == 200, 'DESKTOP_RESPONSE_STATUS')
        require(response.getheader('Content-Type', '').split(';')[0].strip().lower()
                == 'application/json', 'DESKTOP_RESPONSE_TYPE')
        raw = response.read(MAX_BODY + 1)
        require(0 < len(raw) <= MAX_BODY, 'DESKTOP_RESPONSE_SIZE')
        value = json.loads(raw, object_pairs_hook=unique_object)
        require(type(value) is dict and set(value) == {'ok', 'app', 'version', 'platform'}
                and value['ok'] is True and value['app'] == 'workbuddy-desktop'
                and value['version'] == VERSION and value['platform'] == 'darwin',
                'DESKTOP_RESPONSE_MISMATCH')
        return hashlib.sha256(raw).hexdigest()
    except (OSError, http.client.HTTPException, ValueError, UnicodeError, RecursionError) as exc:
        if isinstance(exc, Error):
            raise
        raise Error('DESKTOP_PROBE_FAILED') from None
    finally:
        connection.close()


def observe(app):
    require(platform.system() == 'Darwin', 'MACOS_PROBE_REQUIRED')
    identity = discovery.inspect_application(Path(app).absolute())
    require(identity is not None and identity.identity['bundle_id'] == BUNDLE
            and identity.identity['version'] == VERSION and identity.identity['build'] == BUILD
            and identity.identity['plist_sha256'] == PINNED_PLIST
            and identity.identity['executable_sha256'] == PINNED_EXECUTABLE,
            'DESKTOP_PINNED_APPLICATION_REQUIRED')
    verify_signature(identity.path)
    _, raw, _ = discovery.read_regular(identity.path / 'Contents/Info.plist',
                                        discovery.MAX_PLIST, keep=True)
    executable = identity.path / 'Contents/MacOS' / plistlib.loads(raw)['CFBundleExecutable']
    candidates = []
    for port in PORTS:
        pid = listener(port)
        if pid is None:
            continue
        # A responding unrelated process is never accepted as WorkBuddy.
        started = process_snapshot(pid, executable)
        digest = read_probe(port)
        require(listener(port) == pid and process_snapshot(pid, executable) == started,
                'DESKTOP_PROCESS_CHANGED')
        candidates.append(digest)
    require(len(candidates) == 1, 'DESKTOP_UNIQUE_RUNNING_INSTANCE_REQUIRED')
    discovery.revalidate(identity)
    verify_signature(identity.path)
    return {'schema': 'vf.workbuddy-desktop-observation/v1',
            'status': 'desktop_running_observed', 'application': identity.receipt(),
            'application_signature_verified': True, 'vendor_team_id': TEAM,
            'listener_process_and_start_observed': True, 'process_attested': False,
            'response_sha256': candidates[0], 'app_started': False,
            'configuration_written': False, 'credentials_read': False,
            'host_verified': False, 'desktop_acceptance': False, 'mcp_acceptance': False,
            'independent_review': 'pending'}


def save_receipt(output, value):
    output = Path(output).absolute()
    discovery.no_links(output.parent)
    raw = (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + '\n').encode()
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0)
    with os.fdopen(os.open(output, flags, 0o600), 'wb') as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--app', type=Path, default=Path.home() / 'Applications/WorkBuddy.app')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    try:
        # Refuse existing outputs before performing the read-only observation.
        require(not os.path.lexists(args.output), 'DESKTOP_NEW_RECEIPT_REQUIRED')
        result = observe(args.app)
        save_receipt(args.output, result)
    except Error as error:
        print(json.dumps({'status': 'observation_failed', 'code': str(error), 'host_verified': False}))
        return 1
    except (OSError, ValueError, UnicodeError):
        print(json.dumps({'status': 'observation_failed', 'host_verified': False}))
        return 1
    print(json.dumps({'status': result['status'], 'host_verified': False,
                      'desktop_acceptance': False}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
