"""Deterministic, unsigned engineering handoffs; never installs or publishes."""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import sys
import zipfile
import platform
import unicodedata

from recipes import VIDEO, VIDEO_FILES, source_key, transform, transform_name
from media_recipes import FILES as MEDIA_FILES
from music_recipes import MUSIC, FILES as MUSIC_FILES
from speech_recipes import SPEECH, FILES as SPEECH_FILES
from drama_recipes import DRAMA, FILES as DRAMA_FILES
from blender_recipes import BLENDER, FILES as BLENDER_FILES

VERSION = '0.2.8'
SCHEMA = 'vf.skills.handoff/v2'
TOOL_FILES = ('delivery.py', 'recipes.py', 'media_recipes.py', 'music_recipes.py', 'speech_recipes.py', 'drama_recipes.py', 'blender_recipes.py',
              'video_projection.py', 'handoff.schema.json', 'README.md',
              'public-docs/common-connection.md', 'public-docs/common-project-world.md',
              'public-docs/music-snapshots/voice-model.md',
              'public-docs/music-snapshots/voice-model.schema.json',
              'public-docs/music-snapshots/source-provenance.json',
              'public-docs/blender-set-export.md', 'blender_tools/export_set_package.py',
              'blender_tools/prepare_set_export.py', 'blender_tools/check_camera_evidence.py',
              'blender_tools/validate_set_package.py', 'blender_tools/source-provenance.json')
MAX_FILE = 4 * 1024 * 1024
MAX_ARCHIVE = 32 * 1024 * 1024
MAX_JSON = 1024 * 1024
IDS = (
    'verdantflare-artifact', 'verdantflare-project', 'verdantflare-blender',
    'verdantflare-image', 'verdantflare-video', 'verdantflare-music',
    'verdantflare-speech', 'verdantflare-character-face',
    'verdantflare-reference-learning', 'verdantflare-music-mv',
    'x-verdantflare-short-drama',
)
P, A = IDS[1], IDS[0]
REQUIRES = {
    IDS[0]: [], IDS[1]: [A], IDS[2]: [P, A], IDS[3]: [P, A],
    IDS[4]: [P, A], IDS[5]: [P, A], IDS[6]: [P, A],
    IDS[7]: [IDS[3], IDS[4], P, A], IDS[8]: [P, A],
    IDS[9]: [IDS[3], IDS[4], IDS[5], P, A],
    IDS[10]: [P, A, IDS[3], IDS[4], IDS[6], IDS[5], IDS[9], IDS[2]],
}
# Deliberate file selection, not recursive directory publication.
ALLOW = {
    BLENDER: BLENDER_FILES,
    DRAMA: DRAMA_FILES,
    SPEECH: SPEECH_FILES,
    MUSIC: MUSIC_FILES,
    **MEDIA_FILES,
    VIDEO: VIDEO_FILES,
    IDS[0]: ('SKILL.md', 'agents/openai.yaml', 'references/transfer-and-recovery.md'),
    IDS[1]: ('SKILL.md', 'agents/openai.yaml', 'references/revisions.md',
             'references/display-associations.md', 'references/workspace.md',
             'references/mcp-authority.md', 'scripts/check_video_display.py'),
    IDS[7]: ('SKILL.md', 'agents/openai.yaml', 'references/evidence.md',
             'references/delivery.md', 'references/prompts.md', 'scripts/audit_images.py'),
    IDS[8]: ('SKILL.md', 'agents/openai.yaml', 'references/video-evidence.md',
             'references/learning-contracts.generated.json', 'references/model-prompts.md',
             'references/analysis-service.md', 'references/analysis.md',
             'references/mcp-results.md', 'references/world-save.md', 'scripts/check_learning.py'),
}
ALLOW = {pkg: (*names, 'skill.json') for pkg, names in ALLOW.items()}
BLOCKERS = {}
HOSTS = ('codex', 'claude-code', 'antigravity', 'workbuddy', 'deepseek-desktop')


class Invalid(ValueError):
    pass


def need(condition, code):
    if not condition:
        raise Invalid(code)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + '\n').encode('utf-8')


def unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        need(key not in result, 'DUPLICATE_JSON_KEY')
        result[key] = value
    return result


def decode(data):
    need(len(data) <= MAX_JSON, 'JSON_TOO_LARGE')
    return json.loads(data.decode('utf-8'), object_pairs_hook=unique_pairs,
                      parse_constant=lambda _: (_ for _ in ()).throw(Invalid('NONFINITE_JSON')))


def safe_path(value):
    need(isinstance(value, str) and value and value.isascii(), 'INVALID_PATH')
    parts = value.split('/')
    reserved = {'con', 'prn', 'aux', 'nul'} | {f'{x}{n}' for x in ('com', 'lpt') for n in range(1, 10)}
    need(all(re.fullmatch(r'[A-Za-z0-9_.-]+', p) and p not in ('.', '..')
             and not p.endswith(('.', ' ')) and p.split('.')[0].lower() not in reserved
             for p in parts), 'INVALID_PATH')
    return PurePosixPath(value)


def safe_source_path(value):
    """Preserve exact source identities; never use this for archive paths."""
    need(isinstance(value, str) and value
         and unicodedata.normalize('NFC', value) == value
         and unicodedata.normalize('NFKC', value) == value, 'INVALID_SOURCE_PATH')
    reserved = {'con', 'prn', 'aux', 'nul'} | {f'{x}{n}' for x in ('com', 'lpt') for n in range(1, 10)}
    for part in value.split('/'):
        need(part and part not in ('.', '..') and part == part.strip()
             and not part.endswith('.') and part.split('.')[0].rstrip(' ').lower() not in reserved
             and all(c in '_.- &' or unicodedata.category(c)[0] in 'LNM' for c in part),
             'INVALID_SOURCE_PATH')
    return PurePosixPath(value)


def read_file(root, relative, limit=MAX_FILE, *, source=False):
    (safe_source_path if source else safe_path)(relative)
    root = Path(root)
    # Reject every component, including the user-supplied root, if it is a link.
    absolute = root.absolute() / relative
    for p in (absolute, *absolute.parents):
        need(not p.is_symlink(), 'SYMLINK_REJECTED')
    st = absolute.stat()
    need(stat.S_ISREG(st.st_mode) and st.st_size <= limit, 'FILE_TYPE_OR_SIZE')
    flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0)
    fd = os.open(absolute, flags)
    with os.fdopen(fd, 'rb') as stream:
        now = os.fstat(stream.fileno())
        need((st.st_dev, st.st_ino) == (now.st_dev, now.st_ino), 'SOURCE_CHANGED')
        data = stream.read(limit + 1)
    need(len(data) <= limit and len(data) == st.st_size, 'FILE_SIZE_CHANGED')
    return data


def exact(obj, keys):
    need(type(obj) is dict and set(obj) == set(keys), 'INVALID_FIELDS')


def source_state(root):
    def git(*args):
        return subprocess.check_output(['git', '-C', str(root), *args], stderr=subprocess.DEVNULL).decode().strip()
    need(Path(git('rev-parse', '--show-toplevel')).resolve() == Path(root).resolve(), 'SOURCE_REPO_ROOT_REQUIRED')
    commit = git('rev-parse', 'HEAD')
    need(re.fullmatch('[0-9a-f]{40}', commit), 'INVALID_COMMIT')
    return {'commit': commit, 'dirty': bool(git('status', '--porcelain'))}


def tool_hash():
    return digest(encoded(tool_records()))


def tool_records():
    root = Path(__file__).absolute().parent
    return {n: digest(read_file(root, n)) for n in TOOL_FILES}


def output_key(pkg, name):
    return f'{pkg}/{name}'


def record_bytes(data):
    return {'size': len(data), 'sha256': digest(data)}


def derive(sources, release_id):
    return {output_key(pkg, name): transform(pkg, name, sources[source_key(pkg, name)],
                                           release_id=release_id)
            for pkg, names in ALLOW.items() for name in names}


def output_records(sources, release_id):
    payload = derive(sources, release_id)
    return {output_key(pkg, name): {
        'source': source_key(pkg, name),
        'source_sha256': digest(sources[source_key(pkg, name)]),
        'transform': transform_name(pkg, name),
        **record_bytes(payload[output_key(pkg, name)])}
        for pkg, names in ALLOW.items() for name in names}


def validate_byte_record(record):
    exact(record, ('size', 'sha256'))
    need(type(record['size']) is int and 0 < record['size'] <= MAX_FILE
         and isinstance(record['sha256'], str)
         and re.fullmatch('[0-9a-f]{64}', record['sha256']), 'INVALID_SOURCE_RECORD')


def validate_lock(lock):
    exact(lock, ('schema_version', 'release_id', 'builder_version', 'builder_sha256',
                 'tool_files', 'python', 'source_repository', 'files', 'outputs'))
    need(lock['schema_version'] == SCHEMA and lock['builder_version'] == VERSION, 'UNSUPPORTED_LOCK')
    need(re.fullmatch(r'[a-z0-9][a-z0-9.-]{0,63}', lock['release_id'] or ''), 'INVALID_RELEASE_ID')
    need(lock['builder_sha256'] == tool_hash(), 'BUILDER_CHANGED')
    need(lock['tool_files'] == tool_records(), 'TOOLSET_CHANGED')
    exact(lock['python'], ('implementation', 'version'))
    need(lock['python']['implementation'] == 'CPython'
         and isinstance(lock['python']['version'], str)
         and re.fullmatch(r'3\.\d+\.\d+', lock['python']['version']), 'INVALID_PYTHON_RECORD')
    exact(lock['source_repository'], ('commit', 'dirty'))
    need(re.fullmatch('[0-9a-f]{40}', lock['source_repository']['commit'] or '')
         and type(lock['source_repository']['dirty']) is bool, 'INVALID_SOURCE_STATE')
    expected = {source_key(pkg, name) for pkg, names in ALLOW.items() for name in names}
    need(type(lock['files']) is dict and set(lock['files']) == expected, 'ALLOWLIST_MISMATCH')
    for name, record in lock['files'].items():
        safe_source_path(name)
        validate_byte_record(record)
    folded = {n.casefold() for n in expected}
    need(len(folded) == len(expected), 'SOURCE_PATH_COLLISION')
    need(all('/'.join(n.split('/')[:i]) not in folded
             for n in folded for i in range(1, len(n.split('/')))), 'SOURCE_PATH_COLLISION')
    expected_outputs = {output_key(pkg, name) for pkg, names in ALLOW.items() for name in names}
    need(type(lock['outputs']) is dict and set(lock['outputs']) == expected_outputs, 'OUTPUT_ALLOWLIST_MISMATCH')
    for pkg, names in ALLOW.items():
        for name in names:
            r = lock['outputs'][output_key(pkg, name)]
            exact(r, ('source', 'source_sha256', 'transform', 'size', 'sha256'))
            source = source_key(pkg, name)
            need(r['source'] == source and r['source_sha256'] == lock['files'][source]['sha256']
                 and r['transform'] == transform_name(pkg, name), 'OUTPUT_PROVENANCE_MISMATCH')
            validate_byte_record({'size': r['size'], 'sha256': r['sha256']})
            if r['transform'] == 'copy':
                need(record_bytes_from_output(r) == lock['files'][source], 'COPY_SOURCE_MISMATCH')


def record_bytes_from_output(r):
    return {'size': r['size'], 'sha256': r['sha256']}


def freeze(root, release_id):
    lock = {'schema_version': SCHEMA, 'release_id': release_id,
            'builder_version': VERSION, 'builder_sha256': tool_hash(),
            'tool_files': tool_records(),
            'python': {'implementation': platform.python_implementation(), 'version': platform.python_version()},
            'source_repository': source_state(root), 'files': {}, 'outputs': {}}
    sources = {}
    for pkg, names in ALLOW.items():
        for name in names:
            source = source_key(pkg, name)
            data = read_file(root, source, source=True)
            sources[source] = data
            lock['files'][source] = record_bytes(data)
    lock['outputs'] = output_records(sources, release_id)
    validate_lock(lock)
    return lock


def locked_sources(root, lock):
    result = {}
    for name, record in lock['files'].items():
        data = read_file(root, name, source=True)
        need({'size': len(data), 'sha256': digest(data)} == record, 'SOURCE_LOCK_MISMATCH')
        result[name] = data
    return result


def zip_bytes(files):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w', compression=zipfile.ZIP_STORED) as z:
        for name, data in sorted(files.items()):
            safe_path(name)
            info = zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = (stat.S_IFREG | 0o644) << 16
            info.compress_type = zipfile.ZIP_STORED
            z.writestr(info, data)
    return stream.getvalue()


def support_rows():
    return [{'host': h, 'os': os_name, 'status': 'not_run', 'app_version': None}
            for h in HOSTS for os_name in ('windows', 'macos')]


def build(root, lock, output):
    validate_lock(lock)
    need(lock['python'] == {'implementation': platform.python_implementation(),
                            'version': platform.python_version()}, 'BUILD_PYTHON_CHANGED')
    sources = locked_sources(root, lock)
    need(output_records(sources, lock['release_id']) == lock['outputs'], 'DERIVED_OUTPUT_MISMATCH')
    derived = derive(sources, lock['release_id'])
    need(source_state(root) == lock['source_repository'], 'SOURCE_REPO_STATE_CHANGED')
    files = {'source-lock.json': encoded(lock)}
    entries = []
    for pkg in IDS:
        entry = {'id': pkg, 'version': lock['release_id'], 'requires': REQUIRES[pkg],
                 'status': 'blocked', 'blockers': BLOCKERS.get(pkg, []), 'artifact': None}
        if pkg in ALLOW:
            payload = {f'{pkg}/{n}': derived[f'{pkg}/{n}'] for n in ALLOW[pkg]}
            leaf = {'skill_id': pkg, 'files': [
                {'path': n, **lock['outputs'][n]}
                for n, data in sorted(payload.items())]}
            archive = zip_bytes(payload)
            archive_name = f'packages/{pkg}.zip'
            leaf_name = f'manifests/{pkg}.json'
            files[archive_name], files[leaf_name] = archive, encoded(leaf)
            entry.update(status='assembled', artifact={
                'path': archive_name, 'size': len(archive), 'sha256': digest(archive),
                'file_manifest': leaf_name, 'file_manifest_sha256': digest(files[leaf_name])})
        entries.append(entry)
    manifest = {'schema_version': SCHEMA, 'release_id': lock['release_id'],
                'release_stage': 'integration-candidate', 'public_distribution': False,
                'installable': False, 'authentication': 'unsigned', 'content_review': 'pending',
                'source_lock_sha256': digest(files['source-lock.json']),
                'packages': entries, 'support_matrix': support_rows(),
                'components': {'installer': 'missing', 'profiles': 'missing', 'user_guides': 'missing'},
                'limitations': ['engineering_integrity_only', 'no_signature_or_download_authorization',
                                'runtime_closure_not_verified', 'no_desktop_acceptance']}
    files['release-manifest.json'] = encoded(manifest)
    files['SHA256SUMS'] = ''.join(f'{digest(b)}  {n}\n' for n, b in sorted(files.items())).encode()
    # Input changes during preparation fail before output creation.
    need(locked_sources(root, lock) == sources, 'SOURCE_CHANGED_DURING_BUILD')
    output = Path(output)
    need(not output.exists() and not output.is_symlink(), 'OUTPUT_EXISTS')
    for parent in output.absolute().parents:
        need(not parent.is_symlink(), 'OUTPUT_SYMLINK')
    output.mkdir(parents=False)
    try:
        for name, data in files.items():
            target = output / name
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open('xb') as stream:
                stream.write(data)
        result = verify(output)
    except BaseException:
        shutil.rmtree(output)
        raise
    return result


def verify_archive(data, leaf, lock, pkg):
    exact(leaf, ('skill_id', 'files'))
    need(leaf['skill_id'] == pkg and type(leaf['files']) is list, 'INVALID_FILE_MANIFEST')
    records = {}
    for r in leaf['files']:
        exact(r, ('path', 'source', 'source_sha256', 'transform', 'size', 'sha256'))
        safe_path(r['path'])
        need(r['path'] not in records and r['path'].startswith(pkg + '/'), 'DUPLICATE_OR_FOREIGN_FILE')
        need(r['path'] in lock['outputs'], 'UNLOCKED_SOURCE')
        need({k: v for k, v in r.items() if k != 'path'} == lock['outputs'][r['path']], 'FILE_SOURCE_MISMATCH')
        records[r['path']] = r
    need(set(records) == {f'{pkg}/{n}' for n in ALLOW[pkg]}, 'PACKAGE_ALLOWLIST_MISMATCH')
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        infos = z.infolist()
        names = [x.filename for x in infos]
        need(len(names) == len(set(n.casefold() for n in names)), 'DUPLICATE_ARCHIVE_ENTRY')
        need(set(names) == set(records), 'ARCHIVE_FILE_SET_MISMATCH')
        for info in infos:
            safe_path(info.filename)
            mode = info.external_attr >> 16
            need(stat.S_ISREG(mode) and stat.S_IMODE(mode) == 0o644 and not info.is_dir(), 'ARCHIVE_FILE_TYPE')
            need(info.compress_type == zipfile.ZIP_STORED and not info.flag_bits & 1, 'ARCHIVE_ENCODING')
            need(info.file_size <= MAX_FILE and info.file_size == records[info.filename]['size'], 'ARCHIVE_FILE_SIZE')
            need(digest(z.read(info)) == records[info.filename]['sha256'], 'ARCHIVE_HASH_MISMATCH')
        descriptor = z.read(f'{pkg}/skill.json')
        need(descriptor == transform(pkg, 'skill.json', b'', release_id=lock['release_id']),
             'SKILL_DESCRIPTOR_MISMATCH')


def verify(root):
    raw = read_file(root, 'release-manifest.json', MAX_JSON)
    m = decode(raw)
    exact(m, ('schema_version', 'release_id', 'release_stage', 'public_distribution', 'installable',
              'authentication', 'content_review', 'source_lock_sha256', 'packages', 'support_matrix',
              'components', 'limitations'))
    need(m['schema_version'] == SCHEMA and m['release_stage'] == 'integration-candidate', 'UNSUPPORTED_MANIFEST')
    need(m['public_distribution'] is False and m['installable'] is False
         and m['authentication'] == 'unsigned' and m['content_review'] == 'pending', 'CANDIDATE_PROMOTION_REJECTED')
    need(m['support_matrix'] == support_rows(), 'UNVERIFIED_SUPPORT_CLAIM')
    need(m['components'] == {'installer': 'missing', 'profiles': 'missing', 'user_guides': 'missing'}, 'UNVERIFIED_COMPONENT')
    need(m['limitations'] == ['engineering_integrity_only', 'no_signature_or_download_authorization',
                              'runtime_closure_not_verified', 'no_desktop_acceptance'], 'LIMITATION_REMOVED')
    lock_bytes = read_file(root, 'source-lock.json', MAX_JSON)
    need(digest(lock_bytes) == m['source_lock_sha256'], 'LOCK_HASH_MISMATCH')
    lock = decode(lock_bytes)
    validate_lock(lock)
    need(lock['release_id'] == m['release_id'], 'RELEASE_ID_MISMATCH')
    need(type(m['packages']) is list and [e.get('id') for e in m['packages']] == list(IDS), 'PACKAGE_SET_MISMATCH')
    expected = {'release-manifest.json': raw, 'source-lock.json': lock_bytes}
    count = 0
    for entry in m['packages']:
        exact(entry, ('id', 'version', 'requires', 'status', 'blockers', 'artifact'))
        pkg = entry['id']
        need(entry['version'] == m['release_id'] and entry['requires'] == REQUIRES[pkg], 'DEPENDENCY_OR_VERSION_MISMATCH')
        if pkg not in ALLOW:
            need(entry['status'] == 'blocked' and entry['blockers'] == BLOCKERS[pkg]
                 and entry['artifact'] is None, 'BLOCKED_PACKAGE_FORGED')
            continue
        need(entry['status'] == 'assembled' and entry['blockers'] == [], 'ASSEMBLED_PACKAGE_MISSING')
        a = entry['artifact']
        exact(a, ('path', 'size', 'sha256', 'file_manifest', 'file_manifest_sha256'))
        need(a['path'] == f'packages/{pkg}.zip' and a['file_manifest'] == f'manifests/{pkg}.json', 'ARTIFACT_PATH_MISMATCH')
        archive = read_file(root, a['path'], MAX_ARCHIVE)
        need(type(a['size']) is int and len(archive) == a['size'] and digest(archive) == a['sha256'], 'ARTIFACT_HASH_MISMATCH')
        leaf_raw = read_file(root, a['file_manifest'], MAX_JSON)
        need(digest(leaf_raw) == a['file_manifest_sha256'], 'FILE_MANIFEST_HASH_MISMATCH')
        verify_archive(archive, decode(leaf_raw), lock, pkg)
        expected[a['path']], expected[a['file_manifest']] = archive, leaf_raw
        count += 1
    sums = ''.join(f'{digest(b)}  {n}\n' for n, b in sorted(expected.items())).encode()
    need(read_file(root, 'SHA256SUMS', MAX_JSON) == sums, 'CHECKSUM_FILE_MISMATCH')
    actual = set()
    for f in Path(root).rglob('*'):
        need(not f.is_symlink(), 'SYMLINK_REJECTED')
        if f.is_dir():
            need(f.relative_to(root).as_posix() in ('packages', 'manifests'), 'UNEXPECTED_DIRECTORY')
        else:
            actual.add(f.relative_to(root).as_posix())
    need(actual == set(expected) | {'SHA256SUMS'}, 'UNEXPECTED_BUNDLE_FILE')
    return {'status': 'integrity_verified', 'release_id': m['release_id'], 'assembled_packages': count,
            'expected_packages': len(IDS), 'blocked_packages': len(IDS) - count,
            'authentication': 'unsigned', 'installable': False, 'public_distribution': False,
            'manifest_sha256': digest(raw)}


def handoff(root, output):
    """Export a self-contained engineering sample without executing payloads."""
    receipt = verify(root)
    names = ['release-manifest.json', 'source-lock.json', 'SHA256SUMS']
    names += [f'{folder}/{pkg}.{suffix}' for pkg in ALLOW
              for folder, suffix in (('packages', 'zip'), ('manifests', 'json'))]
    files = {f'candidate/{name}': read_file(root, name, MAX_ARCHIVE) for name in names}
    tooling = Path(__file__).absolute().parent
    for name in TOOL_FILES:
        files['tooling/' + name] = read_file(tooling, name)
    need(digest(encoded({n: digest(files['tooling/' + n]) for n in TOOL_FILES}))
         == decode(files['candidate/source-lock.json'])['builder_sha256'],
         'BUILDER_CHANGED')
    # Recheck the exact bytes captured for export; do not attest a later tree.
    need(verify(root) == receipt, 'BUNDLE_CHANGED_DURING_EXPORT')
    need(all(read_file(root, name, MAX_ARCHIVE) == files['candidate/' + name] for name in names),
         'BUNDLE_CHANGED_DURING_EXPORT')
    archive = zip_bytes(files)
    output = Path(output)
    for parent in output.absolute().parents:
        need(not parent.is_symlink(), 'OUTPUT_SYMLINK')
    need(not output.exists() and not output.is_symlink(), 'OUTPUT_EXISTS')
    # Exclusive create, and only remove a file created by this call on failure.
    with output.open('xb') as stream:
        try:
            stream.write(archive)
        except BaseException:
            stream.close()
            output.unlink()
            raise
    return {**receipt, 'handoff_size': len(archive), 'handoff_sha256': digest(archive)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    p = commands.add_parser('lock')
    p.add_argument('--source', required=True, type=Path)
    p.add_argument('--release', required=True)
    p.add_argument('--output', required=True, type=Path)
    p = commands.add_parser('build')
    p.add_argument('--source', required=True, type=Path)
    p.add_argument('--lock', required=True, type=Path)
    p.add_argument('--output', required=True, type=Path)
    p = commands.add_parser('verify')
    p.add_argument('bundle', type=Path)
    p = commands.add_parser('handoff')
    p.add_argument('--bundle', required=True, type=Path)
    p.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    try:
        if args.command == 'lock':
            result = freeze(args.source, args.release)
            with args.output.open('xb') as f:
                f.write(encoded(result))
            result = {'status': 'source_locked', 'files': len(result['files']), 'release_id': result['release_id']}
        elif args.command == 'build':
            result = build(args.source, decode(read_file(args.lock.parent, args.lock.name, MAX_JSON)), args.output)
        elif args.command == 'handoff':
            result = handoff(args.bundle, args.output)
        else:
            result = verify(args.bundle)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    except (Invalid, OSError, ValueError, KeyError, TypeError, AttributeError, zipfile.BadZipFile, subprocess.SubprocessError) as error:
        code = str(error) if isinstance(error, Invalid) else type(error).__name__
        print(json.dumps({'status': 'rejected', 'code': code}), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
