"""Build selected, release-bound Hub skill inputs without installing or publishing."""
from __future__ import annotations

import argparse
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import zipfile

SCHEMA = 'vf.skills.handoff/v3'
ID = re.compile(r'[a-z0-9][a-z0-9-]{1,62}')
RELEASE = re.compile(r'[a-z0-9][a-z0-9.-]{0,63}')
MAX_FILE = 4 * 1024 * 1024
MAX_ARCHIVE = 32 * 1024 * 1024
MAX_ENTRIES = 512
MAX_EXPANDED = 64 * 1024 * 1024


def require(condition, message):
    if not condition:
        raise ValueError(message)


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + '\n').encode()


def sha(data):
    return hashlib.sha256(data).hexdigest()


def record(data):
    return {'size': len(data), 'sha256': sha(data)}


def safe_path(name):
    require(isinstance(name, str) and bool(name) and '\\' not in name,
            'invalid relative path')
    parts = name.split('/')
    require(all(re.fullmatch(r'[A-Za-z0-9_.-]+', p) and p not in ('.', '..')
                for p in parts), 'unsafe relative path')
    return PurePosixPath(name)


def read_source(root, relative):
    parts = safe_path(relative).parts
    current = root
    for part in parts:
        current = current / part
        require(not current.is_symlink(), 'source symlink rejected')
    mode = current.stat()
    require(stat.S_ISREG(mode.st_mode) and mode.st_size <= MAX_FILE,
            'source type or size invalid')
    return current.read_bytes()


def zip_bytes(files):
    require(len(files) <= MAX_ENTRIES and sum(len(data) for data in files.values()) <= MAX_EXPANDED,
            'archive entry or expanded-size limit exceeded')
    require(all(len(data) <= 16 * 1024 * 1024 for data in files.values()), 'archive entry too large')
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in sorted(files.items()):
            safe_path(name)
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = (stat.S_IFREG | 0o644) << 16
            archive.writestr(info, data)
    require(len(stream.getvalue()) <= MAX_ARCHIVE, 'archive exceeds 32 MiB')
    return stream.getvalue()


def validate_recipe(recipe):
    require(set(recipe) == {'skills'} and isinstance(recipe['skills'], list)
            and 1 <= len(recipe['skills']) <= 32, 'expected 1 to 32 skills')
    skills = {}
    for item in recipe['skills']:
        require(set(item) == {'id', 'requires', 'files'}, 'invalid skill fields')
        sid = item['id']
        require(isinstance(sid, str) and ID.fullmatch(sid) and sid not in skills,
                'invalid or duplicate skill id')
        require(isinstance(item['files'], list) and 'SKILL.md' in item['files']
                and len(item['files']) == len(set(item['files'])), 'invalid file selection')
        for name in item['files']:
            safe_path(name)
            require(not any(p.startswith('.') for p in name.split('/')),
                    'hidden files cannot be published')
        deps = item['requires']
        require(isinstance(deps, list) and all(isinstance(d, str) and ID.fullmatch(d) for d in deps)
                and len(deps) == len(set(deps)), 'invalid dependencies')
        skills[sid] = item
    visiting, visited = set(), set()

    def visit(sid):
        require(sid in skills, 'missing dependency')
        require(sid not in visiting, 'dependency cycle')
        if sid in visited:
            return
        visiting.add(sid)
        for dep in skills[sid]['requires']:
            visit(dep)
        visiting.remove(sid)
        visited.add(sid)
    for sid in skills:
        visit(sid)
    return skills


def git_state(root):
    def git(*args):
        return subprocess.check_output(['git', '-C', str(root), *args], text=True).strip()
    require(Path(git('rev-parse', '--show-toplevel')).resolve() == root.resolve(),
            'source must be repository root')
    return {'commit': git('rev-parse', 'HEAD'), 'dirty': bool(git('status', '--porcelain'))}


def build(root, recipe_name, release_id, *, allow_dirty=False):
    require(RELEASE.fullmatch(release_id) and '..' not in release_id, 'invalid release id')
    require(not root.is_symlink(), 'source root symlink rejected')
    state = git_state(root)
    require(allow_dirty or not state['dirty'], 'source working tree is dirty')
    captured = {}

    def source(name):
        data = read_source(root, name)
        captured[name] = data
        return data

    recipe = json.loads(source(recipe_name))
    skills = validate_recipe(recipe)
    files, packages = {}, []
    for sid, item in sorted(skills.items()):
        payload = {sid + '/' + name: source('skills/' + sid + '/' + name)
                   for name in item['files']}
        require(payload[sid + '/SKILL.md'].strip(), 'empty SKILL.md')
        package = zip_bytes(payload)
        manifest = encoded({'skill_id': sid, 'files': [
            {'path': name, **record(data)} for name, data in sorted(payload.items())]})
        archive_path, manifest_path = 'packages/' + sid + '.zip', 'manifests/' + sid + '.json'
        files['candidate/' + archive_path] = package
        files['candidate/' + manifest_path] = manifest
        packages.append({'id': sid, 'version': release_id, 'requires': item['requires'],
                         'status': 'assembled', 'blockers': [], 'artifact': {
                             'path': archive_path, **record(package), 'file_manifest': manifest_path,
                             'file_manifest_sha256': sha(manifest)}})
    guides = {}
    selected = '\n'.join('- `' + sid + '`' for sid in sorted(skills))
    for name in ('Install.md', 'USER-SKILLS.md'):
        template = source('src/hub-skill-release/guides/' + name).decode()
        guides[name] = template.replace('{{release_id}}', release_id).replace('{{skills}}', selected).encode()
        require(b'{{' not in guides[name], 'unresolved guide placeholder')
    # Include the actual builder in the provenance snapshot without packaging credentials.
    source('src/hub-skill-release/build.py')
    lock = encoded({'schema_version': SCHEMA, 'release_id': release_id,
                    'source_repository': state,
                    'files': {name: record(data) for name, data in sorted(captured.items())}})
    files['candidate/source-lock.json'] = lock
    release = encoded({'schema_version': SCHEMA, 'release_id': release_id,
                       'release_stage': 'integration-candidate', 'authentication': 'unsigned',
                       'installable': False, 'public_distribution': False,
                       'source_lock_sha256': sha(lock), 'packages': packages})
    files['candidate/release-manifest.json'] = release
    for name, data in guides.items():
        files['guides/' + name] = data
    files['guides/guide-manifest.json'] = encoded({
        'release_id': release_id, 'candidate_manifest_sha256': sha(release),
        'files': {name: record(data) for name, data in guides.items()}})
    require(git_state(root) == state and all(read_source(root, name) == data
                                          for name, data in captured.items()), 'source changed during build')
    return zip_bytes(files)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True, type=Path)
    parser.add_argument('--recipe', default='src/hub-skill-release/recipes/artifact.json')
    parser.add_argument('--release', required=True)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--allow-dirty', action='store_true', help='Local development only; Control rejects dirty inputs')
    args = parser.parse_args()
    data = build(args.source, args.recipe, args.release, allow_dirty=args.allow_dirty)
    for parent in args.output.absolute().parents:
        require(not parent.is_symlink(), 'output parent symlink rejected')
    with args.output.open('xb') as output:
        output.write(data)
    print(json.dumps({'release_id': args.release, **record(data)}))


if __name__ == '__main__':
    main()
