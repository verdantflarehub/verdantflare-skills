"""Explicit isolated offline operations; never installs into existing user roots.

Native admission precedes this module. External sample pins establish engineering
byte identity only, not publisher trust. No network, credentials or host discovery.
"""
from __future__ import annotations

from contextlib import contextmanager
import io
import os
from pathlib import Path
import re
import stat
import zipfile

import config_transaction as cfg
import install_transaction as tx
from install_operation import InstallOperation
from runtime_tools import runtime_store as store
from runtime_tools.runtime_lifecycle import RuntimeLifecycle

SCHEMA = 'vf.bootstrap-offline-operation/v1'
WORKBUDDY_SCHEMA = 'vf.bootstrap-offline-operation/v2'
SCOPE = 'vf.bootstrap-offline-scope/v2'
WORKBUDDY_SCOPE = 'vf.bootstrap-workbuddy-offline-scope/v1'
ENGINEERING_URL = 'https://vf-offline-operation.invalid/mcp'
WORKBUDDY_URL = 'https://vf-offline-operation.invalid/mcp/desktop/engineering'
COMMON = {'schema', 'action', 'scope', 'scope_id', 'config_format'}
APPLY = {'sample', 'sample_sha256', 'interrupt_after'}
COLLECT = {'runtime_tree_sha256', 'inventory_revision'}
MIGRATE = {'previous_bootstrap_manifest_sha256', 'inventory_revision', 'interrupt_after'}
CHILDREN = {'state', 'skills', 'config', 'runtime'}
WORKBUDDY_CHILDREN = {'state', 'workbuddy', 'runtime'}
MAX_SAMPLE = 16 * 1024 * 1024
USER_SKILLS = {'verdantflare-artifact', 'verdantflare-project', 'verdantflare-blender',
               'verdantflare-image', 'verdantflare-video', 'verdantflare-music',
               'verdantflare-speech', 'verdantflare-reference-learning',
               'verdantflare-music-mv', 'x-verdantflare-short-drama', 'verdantflare-character-face'}


def checked_file(path, limit, *, private=False):
    tx.no_links(path)
    info = path.stat()
    tx.require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_uid == os.getuid()
               and not stat.S_IMODE(info.st_mode) & 0o7022 and info.st_size <= limit,
               'OFFLINE_INPUT_METADATA')
    if private:
        cfg.private_file(path)
    with path.open('rb') as stream:
        opened = os.fstat(stream.fileno())
        tx.require((info.st_dev, info.st_ino) == (opened.st_dev, opened.st_ino), 'OFFLINE_INPUT_CHANGED')
        raw = stream.read(limit + 1)
    after = path.stat()
    fingerprint = lambda st: (st.st_dev, st.st_ino, st.st_size, st.st_mode, st.st_uid, st.st_nlink, st.st_mtime_ns)
    tx.require(len(raw) == info.st_size and len(raw) <= limit and fingerprint(after) == fingerprint(info),
               'OFFLINE_INPUT_CHANGED')
    return raw


def absolute(value):
    tx.require(type(value) is str and value and len(value) < 8192 and '\x00' not in value,
               'OFFLINE_ABSOLUTE_PATH_REQUIRED')
    path = Path(value)
    tx.require(path.is_absolute(), 'OFFLINE_ABSOLUTE_PATH_REQUIRED')
    tx.no_links(path)
    return path.resolve()


def request(path):
    tx.require(os.name == 'posix', 'WINDOWS_PRIVATE_STORAGE_UNVERIFIED')
    value = tx.decode(checked_file(Path(path).absolute(), 64 * 1024))
    tx.require(type(value) is dict and type(value.get('schema')) is str
               and value['schema'] in {SCHEMA, WORKBUDDY_SCHEMA}
               and type(value.get('action')) is str and value['action'] in
               {'apply', 'uninstall', 'recover', 'status', 'inventory', 'collect', 'recover-gc', 'migrate-binding'},
               'OFFLINE_REQUEST_SCHEMA')
    fields = common_fields(value) | {'apply': APPLY, 'uninstall': {'interrupt_after'},
                       'collect': COLLECT, 'migrate-binding': MIGRATE}.get(value['action'], set())
    tx.require(set(value) == fields and type(value['scope_id']) is str
               and re.fullmatch('[0-9a-f]{32}', value['scope_id'])
               and type(value['config_format']) is str and value['config_format'] in {'json', 'toml'},
               'OFFLINE_REQUEST_FIELDS')
    if is_workbuddy(value):
        tx.require(value['host_profile'] == 'workbuddy' and value['config_format'] == 'json',
                   'OFFLINE_WORKBUDDY_PROFILE_REQUIRED')
    absolute(value['scope'])
    if value['action'] == 'apply':
        absolute(value['sample'])
        tx.require(store.digest_ok(value['sample_sha256'])
                   and (value['interrupt_after'] is None or (type(value['interrupt_after']) is str
                        and value['interrupt_after'] in {'runtime', 'skills', 'config', 'committed'})),
                   'OFFLINE_APPLY_FIELDS')
    if value['action'] == 'collect':
        tx.require(all(store.digest_ok(value[key]) for key in COLLECT), 'OFFLINE_COLLECT_FIELDS')
    if value['action'] == 'uninstall':
        tx.require(value['interrupt_after'] is None or (type(value['interrupt_after']) is str
                   and value['interrupt_after'] in {'runtime', 'skills', 'config', 'committed'}),
                   'OFFLINE_UNINSTALL_FIELDS')
    if value['action'] == 'migrate-binding':
        tx.require(store.digest_ok(value['previous_bootstrap_manifest_sha256'])
                   and store.digest_ok(value['inventory_revision'])
                   and (value['interrupt_after'] is None or (type(value['interrupt_after']) is str
                        and value['interrupt_after'] in {'binding-prepared', 'binding-committed'})),
                   'OFFLINE_MIGRATION_FIELDS')
    return value


def is_workbuddy(value):
    return value['schema'] == WORKBUDDY_SCHEMA


def common_fields(value):
    return COMMON | {'host_profile'} if is_workbuddy(value) else COMMON


def resource_roots(root, value):
    if is_workbuddy(value):
        return root / 'state', root / 'workbuddy/skills', root / 'workbuddy'
    return tuple(root / name for name in ('state', 'skills', 'config'))


def make_engine(root, value, target):
    profile = {'config_profile': 'workbuddy', 'admitted_url': WORKBUDDY_URL} if is_workbuddy(value) else {}
    return InstallOperation(*resource_roots(root, value), target, runtime_root=root / 'runtime',
                            config_format=value['config_format'], **profile)


def zip_members(package, max_bytes):
    members = package.infolist()
    tx.require(0 < len(members) <= tx.MAX_FILES and len({m.filename for m in members}) == len(members)
               and sum(m.file_size for m in members) <= max_bytes, 'OFFLINE_ZIP_LIMIT_OR_DUPLICATE')
    for member in members:
        tx.relative(member.filename)
        tx.require(not member.is_dir() and not member.flag_bits & 1
                   and 0 <= member.file_size <= tx.MAX_FILE
                   and member.create_system == 3 and stat.S_IFMT(member.external_attr >> 16) == stat.S_IFREG
                   and stat.S_IMODE(member.external_attr >> 16) in (0o644, 0o755), 'OFFLINE_ZIP_MEMBER')
    return members


def skills(path, expected_sha):
    tx.require(store.digest_ok(expected_sha), 'OFFLINE_SAMPLE_PIN_REQUIRED')
    raw = checked_file(path, MAX_SAMPLE)
    tx.require(tx.sha(raw) == expected_sha, 'OFFLINE_SAMPLE_PIN_MISMATCH')
    payload, modes, packages = {}, {}, {}
    with zipfile.ZipFile(io.BytesIO(raw)) as outer:
        zip_members(outer, MAX_SAMPLE)
        manifest = tx.decode(outer.read('candidate/release-manifest.json'))
        tx.require(type(manifest) is dict and manifest.get('schema_version') == 'vf.skills.handoff/v2'
                   and manifest.get('installable') is False and manifest.get('public_distribution') is False
                   and type(manifest.get('packages')) is list and len(manifest['packages']) == 11,
                   'OFFLINE_ENGINEERING_CANDIDATE_REQUIRED')
        release = manifest.get('release_id')
        tx.require(type(release) is str and re.fullmatch('[a-z0-9][a-z0-9.-]{0,63}', release), 'OFFLINE_RELEASE_INVALID')
        for package in manifest['packages']:
            tx.require(type(package) is dict and type(package.get('id')) is str
                       and package['id'] in USER_SKILLS and package['id'] not in packages
                       and type(package.get('requires')) is list
                       and all(type(n) is str for n in package['requires']), 'OFFLINE_PACKAGE_INVALID')
            key, artifact = package['id'], package.get('artifact')
            tx.require(type(artifact) is dict and artifact.get('path') == 'packages/' + key + '.zip'
                       and artifact.get('file_manifest') == 'manifests/' + key + '.json'
                       and store.digest_ok(artifact.get('sha256')) and store.digest_ok(artifact.get('file_manifest_sha256'))
                       and type(artifact.get('size')) is int and 0 < artifact['size'] <= tx.MAX_FILE,
                       'OFFLINE_ARTIFACT_INVALID')
            content = outer.read('candidate/' + artifact['path'])
            tx.require(len(content) == artifact['size'] and tx.sha(content) == artifact['sha256'], 'OFFLINE_PACKAGE_HASH')
            listed = outer.read('candidate/' + artifact['file_manifest'])
            tx.require(tx.sha(listed) == artifact['file_manifest_sha256'], 'OFFLINE_FILE_MANIFEST_HASH')
            files = tx.decode(listed)
            tx.require(type(files) is dict and files.get('skill_id') == key and type(files.get('files')) is list,
                       'OFFLINE_FILE_MANIFEST')
            records = {}
            for record in files['files']:
                tx.require(type(record) is dict and type(record.get('path')) is str
                           and record['path'].startswith(key + '/') and record['path'] not in records
                           and store.digest_ok(record.get('sha256')) and type(record.get('size')) is int
                           and 0 <= record['size'] <= tx.MAX_FILE, 'OFFLINE_FILE_RECORD')
                tx.relative(record['path']); records[record['path']] = record
            with zipfile.ZipFile(io.BytesIO(content)) as inner:
                members = zip_members(inner, tx.MAX_TOTAL)
                tx.require({m.filename for m in members} == set(records) and key + '/SKILL.md' in records,
                           'OFFLINE_PACKAGE_FILE_SET')
                for member in members:
                    name = member.filename; data = inner.read(member); record = records[name]
                    tx.require(name not in payload and len(data) == record['size'] and tx.sha(data) == record['sha256'],
                               'OFFLINE_SKILL_FILE_CHANGED')
                    payload[name] = data; modes[name] = stat.S_IMODE(member.external_attr >> 16)
            packages[key] = package
    tx.require(set(packages) == USER_SKILLS, 'OFFLINE_SKILL_SET')
    visited, visiting = set(), set()
    def visit(name):
        tx.require(name in packages and name not in visiting, 'OFFLINE_DEPENDENCY_INVALID')
        if name in visited:
            return
        visiting.add(name)
        for dependency in packages[name]['requires']:
            visit(dependency)
        visiting.remove(name); visited.add(name)
    for name in packages:
        visit(name)
    tx.validate_ledger({'schema': tx.SCHEMA, 'platform': os.name, 'release': release,
                        'files': {n: tx.sha(b) for n, b in payload.items()}, 'modes': modes})
    tx.require(sum(map(len, payload.values())) <= tx.MAX_TOTAL, 'OFFLINE_SKILLS_TOO_LARGE')
    store.validate_manifest({'schema': store.SCHEMA, 'release': release, 'source_sha256': expected_sha,
        'entries': {n: {'type': 'file', 'size': len(b), 'sha256': tx.sha(b), 'mode': modes[n]}
                    for n, b in payload.items()}})
    return payload, modes, release, len(packages)


def bundle_runtime(bundle):
    raw = checked_file(bundle / 'resources/provenance/runtime-tree-manifest.json', tx.MAX_FILE)
    manifest = tx.decode(raw)
    store.validate_manifest(manifest)
    source = tx.decode(checked_file(bundle / 'resources/provenance/source-lock.json', tx.MAX_FILE))
    digest = tx.sha(tx.encode(manifest))
    tx.require(source['runtime_tree_sha256'] == digest and source['platform'] == 'macos-arm64',
               'OFFLINE_RUNTIME_SOURCE_MISMATCH')
    bootstrap = tx.decode(checked_file(bundle / 'bootstrap-manifest.json', tx.MAX_FILE))
    projected = {n.removeprefix('resources/runtime/'): r for n, r in bootstrap['entries'].items()
                 if n.startswith('resources/runtime/')}
    tx.require(projected == manifest['entries'], 'OFFLINE_RUNTIME_ENTRY_MISMATCH')
    sources = {n: bundle / 'resources/runtime' / n for n, r in manifest['entries'].items() if r['type'] == 'file'}
    return manifest, digest, sources


@contextmanager
def locked(root):
    import fcntl
    path = root / 'scope.lock';cfg.private_file(path)
    fd = os.open(path, os.O_RDWR | os.O_CREAT | getattr(os, 'O_NOFOLLOW', 0), 0o600)
    with os.fdopen(fd, 'r+b') as stream:
        cfg.private_file(path)
        tx.require(path.stat().st_ino == os.fstat(stream.fileno()).st_ino, 'OFFLINE_LOCK_CHANGED')
        try:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise tx.Conflict('OFFLINE_SCOPE_BUSY') from None
        try:
            yield
        finally:
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def bindings(bundle, value):
    current = {'schema': SCOPE, 'scope_id': value['scope_id'], 'config_format': value['config_format'],
               'bootstrap_manifest_sha256': tx.sha(checked_file(bundle / 'bootstrap-manifest.json', tx.MAX_FILE))}
    if is_workbuddy(value):
        current.update(schema=WORKBUDDY_SCOPE, host_profile='workbuddy')
    if value['action'] != 'migrate-binding':
        return current, None
    previous = {**current, 'bootstrap_manifest_sha256': value['previous_bootstrap_manifest_sha256']}
    tx.require(previous != current, 'OFFLINE_MIGRATION_SAME_BUNDLE')
    return current, previous


def scope(bundle, value, binding, previous):
    root = absolute(value['scope'])
    children = WORKBUDDY_CHILDREN if is_workbuddy(value) else CHILDREN
    tx.require(root != bundle and not root.is_relative_to(bundle) and not bundle.is_relative_to(root),
               'OFFLINE_SCOPE_OVERLAP')
    if not root.exists():
        tx.require(value['action'] == 'apply' and root.parent.is_dir(), 'OFFLINE_SCOPE_NOT_FOUND')
        parent = root.parent.stat()
        tx.require(parent.st_uid == os.getuid() and not stat.S_IMODE(parent.st_mode) & 0o7022,
                   'OFFLINE_PARENT_METADATA')
        root.mkdir(mode=0o700);tx.sync_dir(root.parent)
        for name in sorted(children):
            (root / name).mkdir(mode=0o700)
        if is_workbuddy(value):
            (root / 'workbuddy/skills').mkdir(mode=0o700)
        tx.atomic(root / 'scope.json', tx.encode(binding), mode=0o600)
    else:
        tx.require(root.is_dir(), 'OFFLINE_SCOPE_REQUIRED')
        # Never create/chmod metadata in an unrecognized existing directory.
        tx.require((root / 'scope.json').exists(), 'OFFLINE_SCOPE_NOT_MANAGED')
        tx.require(tx.decode(checked_file(root / 'scope.json', 4096, private=True)) in
                   ([binding, previous] if previous is not None else [binding]),
                   'OFFLINE_SCOPE_BINDING_MISMATCH')
    cfg.private_directory(root)
    tx.require({p.name for p in root.iterdir()} <= children | {'scope.json', 'scope.lock'}
               and all((root / name).is_dir() and not (root / name).is_symlink() for name in children),
               'OFFLINE_SCOPE_CONTENT_CHANGED')
    for name in children:
        cfg.private_directory(root / name)
    if is_workbuddy(value):
        tx.no_links(root / 'workbuddy/skills')
        tx.require((root / 'workbuddy/skills').is_dir(), 'OFFLINE_SCOPE_CONTENT_CHANGED')
        cfg.private_directory(root / 'workbuddy/skills')
    return root


def migrate_binding(root, value, lifecycle, binding, previous, observed_raw):
    """Change only the admitted tool binding, while all resource locks stay held."""
    with lifecycle.quiescent_snapshot(expected_revision=value['inventory_revision']):
        tx.require(checked_file(root / 'scope.json', 4096, private=True) == observed_raw,
                   'OFFLINE_SCOPE_BINDING_CHANGED')
        observed = tx.decode(observed_raw)
        intent = {'schema': 'vf.bootstrap-binding-migration/v1', 'before': previous,
                  'after': binding, 'inventory_revision': value['inventory_revision']}
        raw = tx.encode(intent);digest = tx.sha(raw)
        folder = root / 'state/.vf-bootstrap-binding'
        path = folder / (digest + '.json')
        tx.no_links(path)
        if observed == binding:
            # Matching the new descriptor alone does not prove the claimed origin.
            tx.require(folder.is_dir() and path.exists(), 'OFFLINE_MIGRATION_RECORD_REQUIRED')
        cfg.private_directory(folder)
        if path.exists():
            tx.require(checked_file(path, 16 * 1024, private=True) == raw,
                       'OFFLINE_MIGRATION_RECORD_CHANGED')
        else:
            tx.atomic(path, raw, mode=0o600)
        if value['interrupt_after'] == 'binding-prepared':
            os._exit(73)
        if observed == previous:
            tx.require(checked_file(root / 'scope.json', 4096, private=True) == observed_raw,
                       'OFFLINE_SCOPE_BINDING_CHANGED')
            tx.atomic(root / 'scope.json', tx.encode(binding), mode=0o600)
        if value['interrupt_after'] == 'binding-committed':
            os._exit(73)
        return summary(value, 'offline_binding_migrated' if observed == previous else 'offline_binding_already_migrated',
                       previous_bootstrap_manifest_sha256=previous['bootstrap_manifest_sha256'],
                       bootstrap_manifest_sha256=binding['bootstrap_manifest_sha256'],
                       migration_sha256=digest, inventory_revision=value['inventory_revision'],
                       lifecycle_managed=True, host_activity_verified=False, runtime_upgrade_acceptance=False)


def registered_scope(root, target, value=None):
    """Do not let engine constructors repair missing registration directories."""
    workbuddy = value is not None and is_workbuddy(value)
    config_root = root / ('workbuddy' if workbuddy else 'config')
    skill_meta = root / 'state/.vf-workbuddy-skill-files' if workbuddy else root / 'skills' / tx.META
    config_meta = config_root / cfg.META / tx.sha(target.casefold().encode('utf-8'))
    directories = [root / 'state/.vf-install-operation', skill_meta,
                   config_root / cfg.META, config_meta, config_meta / 'transactions',
                   root / 'runtime/versions', root / 'runtime/activation',
                   root / 'runtime/activation' / tx.META,
                   root / 'runtime/lifecycle', root / 'runtime/lifecycle/quarantine']
    for path in directories:
        tx.no_links(path)
        tx.require(path.is_dir(), 'OFFLINE_REGISTERED_SCOPE_REQUIRED')
        cfg.private_directory(path)
    seal = root / 'runtime/lifecycle/seal.json'
    tx.no_links(seal)
    tx.require(seal.is_file(), 'OFFLINE_REGISTERED_SCOPE_REQUIRED')
    cfg.private_file(seal)


def summary(value, status, **extra):
    host = {'host_profile': 'workbuddy', 'host_refresh': 'refresh_required', 'host_verified': False} \
        if is_workbuddy(value) else {}
    return {'schema': value['schema'], 'status': status, 'scope_id': value['scope_id'], 'config_format': value['config_format'],
            'installable': False, 'publisher_signature_verified': False, 'user_config_changed': False,
            'windows_execution': False, 'desktop_acceptance': False, 'mcp_acceptance': False, **extra, **host}


def run(bundle, request_path):
    value = request(request_path)
    bundle = Path(bundle).absolute().resolve()
    prepared = None
    if value['action'] == 'apply':
        sample = absolute(value['sample']);root = absolute(value['scope'])
        tx.require(not sample.is_relative_to(root), 'OFFLINE_SAMPLE_SCOPE_OVERLAP')
        # All admission happens before first scope creation.
        prepared = skills(sample, value['sample_sha256'])
        runtime = bundle_runtime(bundle)
    if value['action'] == 'migrate-binding':
        # Inspect the new admitted runtime, but do not stage or activate it.
        bundle_runtime(bundle)
    binding, previous = bindings(bundle, value)
    existing = absolute(value['scope']).exists()
    root = scope(bundle, value, binding, previous)
    if value['action'] == 'status':
        # Read-only; no engine constructors, locks or recovery side effects.
        path = root / 'state/.vf-install-operation/active.json'
        if not path.exists():
            state = 'no_operation'
        else:
            pointer = tx.decode(checked_file(path, 4096, private=True))
            tx.require(type(pointer) is dict and set(pointer) == {'id'} and type(pointer['id']) is str
                       and re.fullmatch('[0-9a-f]{32}', pointer['id']), 'INVALID_OPERATION_POINTER')
            record = tx.decode(checked_file(path.parent / (pointer['id'] + '.json'), tx.MAX_FILE, private=True))
            tx.require(type(record) is dict and record.get('id') == pointer['id']
                       and type(record.get('state')) is str and record['state'] in
                       {'preparing','runtime','skills','config','committed','aborting','aborted','cleaned','cleaned_aborted'},
                       'INVALID_OPERATION')
            state = record['state']
        return summary(value, 'offline_operation_observed', operation_state=state)
    with locked(root):
        # Never rely on the descriptor read before acquiring the outer lock.
        observed_raw = checked_file(root / 'scope.json', 4096, private=True)
        tx.require(tx.decode(observed_raw) in ([binding, previous] if previous is not None else [binding]),
                   'OFFLINE_SCOPE_BINDING_MISMATCH')
        target = 'config.toml' if value['config_format'] == 'toml' else 'mcp.json'
        if value['action'] in {'migrate-binding', 'uninstall'} or (is_workbuddy(value) and existing):
            registered_scope(root, target, value)
        engine = make_engine(root, value, target)
        lifecycle = RuntimeLifecycle(engine.runtime_store, [engine])
        if value['action'] == 'migrate-binding':
            return migrate_binding(root, value, lifecycle, binding, previous, observed_raw)
        lifecycle.initialize()
        scope_key = next(iter(lifecycle.scopes))
        if value['action'] == 'inventory':
            return summary(value, 'offline_runtime_inventory', inventory=lifecycle.inventory(),
                           lifecycle_managed=True, host_activity_verified=False)
        if value['action'] in {'collect', 'recover-gc'}:
            if value['action'] == 'collect':
                result = lifecycle.collect(value['runtime_tree_sha256'],
                                           expected_revision=value['inventory_revision'])
            else:
                result = lifecycle.recover_gc()
            return summary(value, 'offline_runtime_maintenance', maintenance=result,
                           lifecycle_managed=True, host_activity_verified=False)
        if value['action'] == 'recover':
            for _ in range(3):
                lifecycle.recover_operation(scope_key)
                active = engine._active()
                if active is None or active['state'] in {'cleaned', 'cleaned_aborted'}:
                    return summary(value, 'offline_operation_recovered',
                                   operation_state=active['state'] if active else 'no_operation',
                                   lifecycle_managed=True, host_activity_verified=False)
            raise tx.Conflict('OFFLINE_RECOVERY_INCOMPLETE')
        def checkpoint(phase):
            if value['interrupt_after'] is not None and phase == value['interrupt_after']:
                os._exit(73)
        if value['action'] == 'uninstall':
            observed = engine.config.observe()
            removed_files = len(engine.skills.snapshot()[0]['files'])
            result = lifecycle.uninstall(scope_key,
                expected_skill_revision=engine.skills.snapshot()[1], expected_config_revision=observed['ledger_revision'],
                expected_config_file=observed['file'], expected_runtime_revision=engine.runtime.snapshot()[1],
                checkpoint=checkpoint)
            lifecycle.recover_operation(scope_key)
            tx.require(engine._active()['state'] == 'cleaned', 'OFFLINE_CLEANUP_INCOMPLETE')
            return summary(value, 'offline_resources_removed', operation_state='cleaned',
                           skill_files_removed=removed_files, operation=result['operation'],
                           lifecycle_managed=True, host_activity_verified=False,
                           runtime_trees_collected=False, uninstall_acceptance=False)
        payload, modes, release, packages = prepared
        manifest, digest, sources = runtime
        # Fixed staging id resumes the original partial tree after process exit.
        lifecycle.stage(manifest, sources, expected_digest=digest, transaction_id=value['scope_id'])
        observed = engine.config.observe()
        desired = {'url': ENGINEERING_URL}
        if is_workbuddy(value):
            from host_workbuddy import desired_connection
            desired = desired_connection(WORKBUDDY_URL, admitted_url=WORKBUDDY_URL)
        result = lifecycle.apply(scope_key, release, payload, desired,
            expected_skill_revision=engine.skills.snapshot()[1], expected_config_revision=observed['ledger_revision'],
            expected_config_file=observed['file'], modes=modes, runtime_digest=digest,
            expected_runtime_revision=engine.runtime.snapshot()[1], checkpoint=checkpoint)
        lifecycle.recover_operation(scope_key)
        tx.require(engine._active()['state'] == 'cleaned', 'OFFLINE_CLEANUP_INCOMPLETE')
        engine.runtime_store.verify(digest)
        return summary(value, 'offline_resources_applied', operation_state='cleaned',
                       release=release, runtime_tree_sha256=digest, skill_packages=packages,
                       skill_files=len(payload), skill_bytes=sum(map(len, payload.values())),
                       operation=result['operation'], lifecycle_managed=True, host_activity_verified=False)
