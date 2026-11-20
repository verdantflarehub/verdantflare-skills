"""Data-only transfer of unsigned bootstrap trees, with explicit external pins."""
from __future__ import annotations

from functools import wraps
import hashlib
import os
from pathlib import Path
import re
import shutil
import stat
import sys
import zipfile

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))
sys.path.insert(0, str(TOOLS / 'runtime_tools'))
import install_transaction as tx
from runtime_tools import runtime_store as store
import runtime_composition as composition

MAX_ARCHIVE = 512 * 1024 * 1024
MAX_MANIFEST = 8 * 1024 * 1024
MAX_BINARY = 16 * 1024 * 1024


def guarded(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except tx.Conflict:
            raise
        except (OSError, ValueError, TypeError, KeyError, UnicodeError, RecursionError, RuntimeError, zipfile.BadZipFile):
            raise tx.Conflict('BOOTSTRAP_TRANSFER_INVALID_INPUT') from None
    return wrapped


def pin(value):
    tx.require(type(value) is str and re.fullmatch('[0-9a-f]{64}', value), 'BOOTSTRAP_EXTERNAL_PIN_REQUIRED')


def file_info(path, limit, mode=None):
    tx.no_links(path)
    info = path.stat()
    tx.require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_size <= limit,
               'BOOTSTRAP_TRANSFER_FILE_INVALID')
    if os.name == 'posix':
        tx.require(info.st_uid == os.getuid() and not stat.S_IMODE(info.st_mode) & 0o7022,
                   'BOOTSTRAP_TRANSFER_FILE_METADATA')
        tx.require(mode is None or stat.S_IMODE(info.st_mode) == mode, 'BOOTSTRAP_TRANSFER_FILE_MODE')
    return info


def parse_manifest(raw, expected):
    pin(expected)
    tx.require(len(raw) <= MAX_MANIFEST and tx.sha(raw) == expected, 'BOOTSTRAP_MANIFEST_PIN_MISMATCH')
    manifest = composition.decode(raw)
    tx.require(type(manifest) is dict and set(manifest) == {'schema', 'platform', 'python', 'driver', 'entries'}
               and manifest['schema'] == 'vf.installer-bootstrap/v1'
               and manifest['platform'] in ('macos-arm64', 'windows-amd64'), 'BOOTSTRAP_MANIFEST_SCHEMA')
    python = 'resources/runtime/python/bin/python3.12' if manifest['platform'] == 'macos-arm64' else 'resources/runtime/python/python.exe'
    tx.require(manifest['python'] == python and manifest['driver'] == 'resources/installer/driver.py',
               'BOOTSTRAP_ENTRYPOINT_INVALID')
    entries = manifest['entries']
    store.validate_manifest({'schema': store.SCHEMA, 'release': 'bootstrap.transfer',
                             'source_sha256': expected, 'entries': entries})
    tx.require(all(name.startswith('resources/') for name in entries), 'BOOTSTRAP_RESOURCE_PREFIX')
    for name in (manifest['python'], manifest['driver']):
        tx.require(name in entries and entries[name]['type'] == 'file', 'BOOTSTRAP_ENTRYPOINT_INVALID')
    tx.require(entries[python]['mode'] == 0o755, 'BOOTSTRAP_ENTRYPOINT_INVALID')
    return manifest


def records(manifest, raw, binary_sha, binary_size):
    pin(binary_sha)
    tx.require(type(binary_size) is int and 0 < binary_size <= MAX_BINARY, 'BOOTSTRAP_BINARY_SIZE')
    name = 'vf-bootstrap' if manifest['platform'] == 'macos-arm64' else 'vf-bootstrap.exe'
    return {**manifest['entries'], 'bootstrap-manifest.json': {'type': 'file', 'size': len(raw), 'sha256': tx.sha(raw), 'mode': 0o644},
            name: {'type': 'file', 'size': binary_size, 'sha256': binary_sha, 'mode': 0o755}}


def receipt(manifest, manifest_sha, binary_sha):
    return {'status': 'bootstrap_transfer_integrity_verified', 'platform': manifest['platform'],
            'manifest_sha256': manifest_sha, 'binary_sha256': binary_sha,
            'entries': len(manifest['entries']), 'resource_code_executed': False,
            'installable': False, 'publisher_signature_verified': False,
            'windows_execution': False, 'desktop_acceptance': False, 'mcp_acceptance': False}


@guarded
def verify_folder(folder, manifest_sha, binary_sha):
    folder = Path(folder).absolute()
    tx.no_links(folder)
    manifest_path = folder / 'bootstrap-manifest.json'
    file_info(manifest_path, MAX_MANIFEST, 0o644)
    raw = manifest_path.read_bytes()
    manifest = parse_manifest(raw, manifest_sha)
    binary_name = 'vf-bootstrap' if manifest['platform'] == 'macos-arm64' else 'vf-bootstrap.exe'
    binary_info = file_info(folder / binary_name, MAX_BINARY, 0o755)
    expected = records(manifest, raw, binary_sha, binary_info.st_size)
    directories = {'.'}
    for name in expected:
        directories.update(str(p).replace('\\', '/') for p in Path(name).parents if str(p) != '.')
    seen = set()
    pending = [folder]
    while pending:
        current = pending.pop()
        info = current.lstat()
        rel = current.relative_to(folder).as_posix()
        if stat.S_ISDIR(info.st_mode):
            tx.require(rel in directories, 'BOOTSTRAP_UNKNOWN_DIRECTORY')
            if os.name == 'posix':
                tx.require(info.st_uid == os.getuid() and not stat.S_IMODE(info.st_mode) & 0o7022,
                           'BOOTSTRAP_DIRECTORY_METADATA')
            pending.extend(sorted(current.iterdir()))
            continue
        tx.require(rel in expected, 'BOOTSTRAP_UNKNOWN_MEMBER')
        entry = expected[rel]
        seen.add(rel)
        if entry['type'] == 'symlink':
            tx.require(stat.S_ISLNK(info.st_mode) and os.readlink(current) == entry['target'], 'BOOTSTRAP_LINK_CHANGED')
        else:
            actual = file_info(current, store.MAX_FILE, entry['mode'])
            tx.require(actual.st_size == entry['size'] and composition.workspace.sdk.sha(current) == entry['sha256'],
                       'BOOTSTRAP_RESOURCE_CHANGED')
    tx.require(seen == set(expected), 'BOOTSTRAP_MISSING_MEMBER')
    return receipt(manifest, manifest_sha, binary_sha)


@guarded
def archive(folder, manifest_sha, binary_sha, output):
    folder, output = Path(folder).absolute(), Path(output).absolute()
    verify_folder(folder, manifest_sha, binary_sha)
    tx.no_links(output)
    tx.require(not output.exists() and output.parent.is_dir() and folder.resolve() not in output.resolve().parents,
               'NEW_EXTERNAL_ARCHIVE_REQUIRED')
    raw = (folder / 'bootstrap-manifest.json').read_bytes()
    manifest = parse_manifest(raw, manifest_sha)
    binary = folder / ('vf-bootstrap' if manifest['platform'] == 'macos-arm64' else 'vf-bootstrap.exe')
    entries = records(manifest, raw, binary_sha, binary.stat().st_size)
    with zipfile.ZipFile(output, 'x', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as package:
        for name, item in sorted(entries.items()):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = ((stat.S_IFLNK | 0o777) if item['type'] == 'symlink' else (stat.S_IFREG | item['mode'])) << 16
            if item['type'] == 'symlink':
                package.writestr(info, item['target'].encode('utf-8'))
            else:
                with (folder / name).open('rb') as source, package.open(info, 'w') as target:
                    shutil.copyfileobj(source, target, 1024 * 1024)
    tx.require(output.stat().st_size <= MAX_ARCHIVE, 'BOOTSTRAP_ARCHIVE_TOO_LARGE')
    verify_folder(folder, manifest_sha, binary_sha)
    return {'status': 'unsigned_bootstrap_archived', 'sha256': composition.workspace.sdk.sha(output),
            'size': output.stat().st_size, **{k: v for k, v in receipt(manifest, manifest_sha, binary_sha).items() if k != 'status'}}


@guarded
def receive(archive_path, archive_sha, manifest_sha, binary_sha, output=None):
    pin(archive_sha)
    archive_path = Path(archive_path).absolute()
    file_info(archive_path, MAX_ARCHIVE)
    tx.require(composition.workspace.sdk.sha(archive_path) == archive_sha, 'BOOTSTRAP_ARCHIVE_PIN_MISMATCH')
    if output is not None:
        output = Path(output).absolute()
        tx.no_links(output)
        output = output.resolve()
        tx.require(not output.exists() and output.parent.is_dir(), 'NEW_RECEIVER_OUTPUT_REQUIRED')
    with zipfile.ZipFile(archive_path) as package:
        members = package.infolist()
        names = [m.filename for m in members]
        tx.require(0 < len(members) <= store.MAX_ENTRIES + 2 and len(names) == len(set(names))
                   and sum(m.file_size for m in members) <= store.MAX_TOTAL + MAX_MANIFEST + MAX_BINARY,
                   'BOOTSTRAP_ARCHIVE_LIMIT_OR_DUPLICATE')
        tx.require(all(not m.is_dir() and not m.flag_bits & 1 and 0 <= m.file_size <= store.MAX_FILE for m in members),
                   'BOOTSTRAP_ARCHIVE_MEMBER')
        tx.require('bootstrap-manifest.json' in names, 'BOOTSTRAP_ARCHIVE_MANIFEST_MISSING')
        item = package.getinfo('bootstrap-manifest.json')
        tx.require(item.file_size <= MAX_MANIFEST, 'BOOTSTRAP_MANIFEST_TOO_LARGE')
        raw = package.read(item)
        manifest = parse_manifest(raw, manifest_sha)
        binary_name = 'vf-bootstrap' if manifest['platform'] == 'macos-arm64' else 'vf-bootstrap.exe'
        tx.require(binary_name in names, 'BOOTSTRAP_BINARY_MISSING')
        expected = records(manifest, raw, binary_sha, package.getinfo(binary_name).file_size)
        tx.require(set(names) == set(expected), 'BOOTSTRAP_ARCHIVE_SET_MISMATCH')
        # First pass validates every member before creating any output directory.
        for member in members:
            record = expected[member.filename]
            wanted = (stat.S_IFLNK | 0o777) if record['type'] == 'symlink' else stat.S_IFREG | record['mode']
            tx.require(member.create_system == 3 and member.external_attr >> 16 == wanted, 'BOOTSTRAP_ARCHIVE_MODE')
            if record['type'] == 'symlink':
                target = record['target'].encode('utf-8')
                tx.require(member.file_size == len(target) and package.read(member) == target, 'BOOTSTRAP_ARCHIVE_LINK')
            else:
                tx.require(member.file_size == record['size'], 'BOOTSTRAP_ARCHIVE_FILE_SIZE')
                h = hashlib.sha256()
                with package.open(member) as stream:
                    for block in iter(lambda: stream.read(1024 * 1024), b''):
                        h.update(block)
                tx.require(h.hexdigest() == record['sha256'], 'BOOTSTRAP_ARCHIVE_FILE_HASH')
        if output is not None:
            output.mkdir(mode=0o700)
            for name, record in sorted(expected.items(), key=lambda pair: (pair[1]['type'] != 'file', pair[0])):
                target = output / name
                target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                for parent in target.parents:
                    if parent == output:
                        break
                    parent.chmod(0o700)
                if record['type'] == 'symlink':
                    os.symlink(record['target'], target)
                else:
                    with package.open(name) as source, target.open('xb') as dest:
                        shutil.copyfileobj(source, dest, 1024 * 1024)
                    target.chmod(record['mode'])
            verify_folder(output, manifest_sha, binary_sha)
    tx.require(composition.workspace.sdk.sha(archive_path) == archive_sha, 'BOOTSTRAP_ARCHIVE_CHANGED')
    return {**receipt(manifest, manifest_sha, binary_sha), 'archive_sha256': archive_sha,
            'received': output is not None}
