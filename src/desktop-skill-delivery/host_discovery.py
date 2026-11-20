"""Read-only macOS application identity observations, never host admission.

No application is executed. Paths stay in local objects, not public receipts.
An executable/plist fingerprint is not a whole-bundle or signing identity.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import plistlib
import re
import stat
import sys
from xml.parsers.expat import ExpatError

PRODUCTS = {
    'com.openai.codex': 'codex',
    'com.anthropic.claudefordesktop': 'claude-code',
    'com.google.antigravity': 'antigravity',
    'com.tencent.workbuddy.mac': 'workbuddy',
    'com.deepseek.dsh': 'deepseek',
}
EXCLUDED = {'com.google.antigravity-ide': 'antigravity-ide'}
SCHEMA = 'vf.desktop-host-observation/v1'
MAX_PLIST = 2 * 1024 * 1024
MAX_EXECUTABLE = 512 * 1024 * 1024


class ObservationError(ValueError):
    """Constant diagnostics, never names, paths or plist values."""


def require(condition, code):
    if not condition:
        raise ObservationError(code)


def fingerprint(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_size,
            info.st_mtime_ns, info.st_ctime_ns)


def no_links(path):
    path = Path(os.path.abspath(path))
    for member in reversed((path, *path.parents)):
        require(not stat.S_ISLNK(member.lstat().st_mode), 'APPLICATION_LINK_REJECTED')
    return path


def read_regular(path, limit, *, keep=False):
    no_links(path)
    flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0)
    with os.fdopen(os.open(path, flags), 'rb') as stream:
        before = os.fstat(stream.fileno())
        require(stat.S_ISREG(before.st_mode), 'APPLICATION_FILE_REQUIRED')
        require(0 < before.st_size <= limit, 'APPLICATION_FILE_SIZE')
        digest, chunks, total = hashlib.sha256(), [], 0
        while chunk := stream.read(min(1024 * 1024, limit + 1 - total)):
            total += len(chunk)
            require(total <= limit, 'APPLICATION_FILE_SIZE')
            digest.update(chunk)
            if keep:
                chunks.append(chunk)
        require(total == before.st_size, 'APPLICATION_CHANGED')
        require(fingerprint(before) == fingerprint(os.fstat(stream.fileno()))
                == fingerprint(path.lstat()), 'APPLICATION_CHANGED')
    return digest.hexdigest(), b''.join(chunks), fingerprint(before)


def version(value):
    require(type(value) is str and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._+\-]{0,127}', value),
            'APPLICATION_VERSION_INVALID')
    return value


@dataclass(frozen=True, repr=False)
class Application:
    path: Path
    identity: dict

    def receipt(self):
        raw = json.dumps(self.identity, sort_keys=True, separators=(',', ':')).encode()
        return {**self.identity, 'identity_sha256': hashlib.sha256(raw).hexdigest(),
                'signature_verified': False, 'host_verified': False,
                'configuration_written': False}


def inspect_application(path):
    path = no_links(path)
    require(path.is_dir() and path.suffix == '.app', 'APPLICATION_DIRECTORY_REQUIRED')
    directories = [path, path / 'Contents', path / 'Contents/MacOS']
    snapshots = [fingerprint(no_links(p).lstat()) for p in directories]
    plist_path = path / 'Contents/Info.plist'
    plist_sha, raw, plist_stat = read_regular(plist_path, MAX_PLIST, keep=True)
    try:
        info = plistlib.loads(raw)
    except (ValueError, TypeError, OverflowError, RecursionError, ExpatError):
        # plistlib may also raise XML parser-specific exceptions. Never expose
        # their user-controlled messages through the discovery API.
        raise ObservationError('APPLICATION_PLIST_INVALID') from None
    require(type(info) is dict, 'APPLICATION_PLIST_INVALID')
    bundle = info.get('CFBundleIdentifier')
    require(type(bundle) is str, 'APPLICATION_ID_INVALID')
    if bundle not in PRODUCTS and bundle not in EXCLUDED:
        return None
    executable = info.get('CFBundleExecutable')
    require(type(executable) is str and 0 < len(executable) <= 255
            and executable not in ('.', '..') and '/' not in executable
            and '\\' not in executable and not any(ord(c) < 32 or ord(c) == 127 for c in executable),
            'APPLICATION_EXECUTABLE_INVALID')
    main_path = path / 'Contents/MacOS' / executable
    main_sha, _, main_stat = read_regular(main_path, MAX_EXECUTABLE)
    require(plist_stat == fingerprint(plist_path.lstat())
            and main_stat == fingerprint(main_path.lstat())
            and snapshots == [fingerprint(no_links(p).lstat()) for p in directories],
            'APPLICATION_CHANGED')
    return Application(path, {
        'platform': 'macos', 'product': PRODUCTS.get(bundle, EXCLUDED.get(bundle)),
        'target_surface': bundle in PRODUCTS, 'bundle_id': bundle,
        'version': version(info.get('CFBundleShortVersionString')),
        'build': version(info.get('CFBundleVersion')),
        'plist_sha256': plist_sha, 'executable_sha256': main_sha,
    })


def revalidate(application):
    require(type(application) is Application, 'APPLICATION_OBSERVATION_REQUIRED')
    try:
        current = inspect_application(application.path)
    except OSError:
        raise ObservationError('APPLICATION_UNREADABLE') from None
    require(current is not None and current.identity == application.identity, 'APPLICATION_IDENTITY_CHANGED')
    return current


def scan_roots(roots):
    """Explicit shallow roots; fixtures can exercise this without macOS."""
    applications, diagnostics, seen = [], [], set()
    require(1 <= len(roots) <= 16, 'APPLICATION_ROOT_COUNT')
    for root in roots:
        try:
            root = no_links(root)
            require(root.is_dir(), 'APPLICATION_ROOT_REQUIRED')
            key = (root.stat().st_dev, root.stat().st_ino)
            if key in seen:
                continue
            seen.add(key)
            before = fingerprint(root.stat())
            children = sorted(root.iterdir())
            require(len(children) <= 4096, 'APPLICATION_ROOT_SIZE')
            root_apps = []
            for child in children:
                if child.suffix != '.app':
                    continue
                try:
                    application = inspect_application(child)
                    if application is not None:
                        root_apps.append(application)
                except ObservationError as error:
                    diagnostics.append(str(error))
                except OSError:
                    diagnostics.append('APPLICATION_UNREADABLE')
            require(before == fingerprint(root.stat()), 'APPLICATION_ROOT_CHANGED')
            applications.extend(root_apps)
        except ObservationError as error:
            diagnostics.append(str(error))
        except OSError:
            diagnostics.append('APPLICATION_ROOT_UNREADABLE')
    # The same application may be observed through overlapping explicit roots.
    unique = {app.path: app for app in applications}
    return tuple(unique.values()), tuple(diagnostics)


def receipt(applications, diagnostics):
    observed = sorted((app.receipt() for app in applications),
                      key=lambda item: (item['product'], item['identity_sha256']))
    states = {}
    for product in PRODUCTS.values():
        count = sum(row['target_surface'] and row['product'] == product for row in observed)
        states[product] = ('ambiguous' if count > 1 else 'metadata_observed' if count else
                           'unknown_due_to_scan_error' if diagnostics else 'not_observed')
    return {'schema': SCHEMA, 'platform': 'macos', 'observed': observed,
            'targets': states, 'scan_complete': not diagnostics,
            'diagnostics': sorted(diagnostics), 'desktop_acceptance': False,
            'mcp_acceptance': False, 'configuration_written': False}


def main():
    parser = argparse.ArgumentParser(description='Observe desktop package metadata without launching apps.')
    parser.add_argument('--root', type=Path, action='append', help='Explicit shallow Applications directory.')
    args = parser.parse_args()
    if sys.platform != 'darwin':
        print(json.dumps({'error': 'HOST_DISCOVERY_PLATFORM_UNIMPLEMENTED'}))
        return 2
    roots = args.root or [p for p in (Path('/Applications'), Path.home() / 'Applications') if p.exists()]
    try:
        apps, errors = scan_roots(roots)
        print(json.dumps(receipt(apps, errors), sort_keys=True, indent=2))
        return 0 if not errors else 1
    except (ObservationError, OSError):
        print(json.dumps({'error': 'HOST_DISCOVERY_FAILED'}))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
