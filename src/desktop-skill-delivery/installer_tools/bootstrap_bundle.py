"""Assemble fixed engineering resources for the native bootstrapper.

No downloads, source builds, user roots, publisher admission or GUI installation.
Compilation/signing and independent receiver verification are separate steps.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import sys

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))
sys.path.insert(0, str(TOOLS / 'runtime_tools'))
import install_transaction as tx
import runtime_composition as composition
from runtime_tools import runtime_store

CORE = ('config_transaction.py', 'host_config.py', 'host_toml.py', 'toml_transaction.py',
        'install_transaction.py', 'transaction_compensation.py', 'install_operation.py',
        'native_install_operation.py', 'host_deepseek.py', 'host_deepseek_origin.py',
        'host_deepseek_journal.py', 'host_deepseek_operation.py', 'tool_name_profile.py',
        'host_discovery.py', 'host_workbuddy.py')
WHEEL_SHA = '05562412163a3037b94497385fb7f9204c637bc605af7a48199e41cf2272f437'
WHEEL_SIZE = 52225
LICENSE_PATH = 'tomlkit-0.15.2.dist-info/licenses/LICENSE'
LICENSE_SHA = 'f2f9b460ba719da6626add264d3782f275a4ff7aab677beda08b330911e23adb'


def source_resources(tree):
    """Freeze the complete authored resource closure before assembly."""
    authored = {'resources/installer/driver.py': (Path(__file__).parent / 'driver.py').read_bytes(),
                'resources/installer/core/offline_operation.py': (Path(__file__).parent / 'offline_operation.py').read_bytes(),
                'resources/installer/core/tool_name_diagnostic.py': (Path(__file__).parent / 'tool_name_diagnostic.py').read_bytes(),
                'resources/installer/core/runtime_tools/runtime_store.py': (TOOLS / 'runtime_tools/runtime_store.py').read_bytes(),
                'resources/installer/core/runtime_tools/runtime_lifecycle.py': (TOOLS / 'runtime_tools/runtime_lifecycle.py').read_bytes(),
                'resources/installer/core/runtime_tools/native_lifecycle.py': (TOOLS / 'runtime_tools/native_lifecycle.py').read_bytes(),
                'resources/provenance/runtime-tree-manifest.json': tx.encode(tree)}
    authored.update({'resources/installer/core/' + name: (TOOLS / name).read_bytes() for name in CORE})
    native = Path(__file__).parent / 'native'
    authored.update({'resources/build-source/native/' + path.name: path.read_bytes()
                     for path in sorted(native.iterdir()) if path.suffix == '.go' or path.name == 'go.mod'})
    authored['resources/build-source/bootstrap_bundle.py'] = Path(__file__).read_bytes()
    authored['resources/provenance/host-config-dependencies.lock.json'] = (TOOLS / 'host-config-dependencies.lock.json').read_bytes()
    return authored


def assemble(candidate, expected_sha, wheel_path, output):
    """Return externally pin-able manifest digest; leave output unsigned."""
    candidate, wheel_path, output = map(lambda p: Path(p).absolute(), (candidate, wheel_path, output))
    for path in (candidate, wheel_path, output):
        tx.no_links(path)
    candidate, wheel_path, output = candidate.resolve(), wheel_path.resolve(), output.resolve()
    tx.require(not output.exists() and output.parent.is_dir() and output != candidate
               and candidate not in output.parents, 'NEW_BOOTSTRAP_OUTPUT_REQUIRED')
    checked = composition.verify_folder(candidate, expected_sha)
    wheel = composition.workspace.read(wheel_path, WHEEL_SIZE)
    tx.require(len(wheel) == WHEEL_SIZE and tx.sha(wheel) == WHEEL_SHA, 'BOOTSTRAP_WHEEL_HASH_MISMATCH')
    wheel_files = composition.portable.wheel_files(wheel)
    tx.require(tx.sha(wheel_files[LICENSE_PATH]) == LICENSE_SHA, 'BOOTSTRAP_LICENSE_MISMATCH')
    tree = composition.decode((candidate / 'tree-manifest.json').read_bytes())
    runtime_store.validate_manifest(tree)
    authored = source_resources(tree)
    source_lock = {'schema': 'vf.bootstrap-source-inputs/v1', 'runtime_descriptor_sha256': expected_sha,
                   'runtime_tree_sha256': checked['tree_sha256'], 'tomlkit_wheel_sha256': WHEEL_SHA,
                   'source_files': {name: tx.sha(raw) for name, raw in sorted(authored.items())},
                   'platform': checked['platform'], 'installable': False, 'public_distribution': False,
                   'publisher_signature_verified': False, 'windows_execution': False, 'desktop_acceptance': False}
    authored['resources/provenance/source-lock.json'] = tx.encode(source_lock)
    expected = {'resources/runtime/' + name: record for name, record in tree['entries'].items()}
    expected.update({name: {'type': 'file', 'size': len(raw), 'sha256': tx.sha(raw), 'mode': 0o644}
                     for name, raw in authored.items()})
    expected.update({'resources/vendor/' + name: {'type': 'file', 'size': len(raw), 'sha256': tx.sha(raw), 'mode': 0o644}
                     for name, raw in wheel_files.items()})
    # Validate all paths/aliases/link targets together using the already-reviewed
    # runtime rules; don't defer portable path checks to the native executable.
    runtime_store.validate_manifest({'schema': runtime_store.SCHEMA, 'release': 'bootstrap.engineering',
                                     'source_sha256': expected_sha, 'entries': expected})
    output.mkdir(mode=0o700)

    def parent(path):
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        # pathlib's intermediate mkdir honors umask rather than supplied mode.
        for directory in path.parents:
            if directory == output:
                break
            directory.chmod(0o700)

    for name, record in sorted(expected.items(), key=lambda pair: (pair[1]['type'] != 'file', pair[0])):
        target = output / name
        parent(target)
        if record['type'] == 'symlink':
            os.symlink(record['target'], target)
            continue
        if name.startswith('resources/runtime/'):
            source = candidate / 'objects' / record['sha256']
            with source.open('rb') as inp, target.open('xb') as dest:
                shutil.copyfileobj(inp, dest, 1024 * 1024)
        else:
            raw = authored[name] if name in authored else wheel_files[name.removeprefix('resources/vendor/')]
            with target.open('xb') as stream:
                stream.write(raw)
        target.chmod(record['mode'])
        tx.require(target.stat().st_size == record['size'] and composition.workspace.sdk.sha(target) == record['sha256'],
                   'BOOTSTRAP_RESOURCE_CHANGED')
    python = 'resources/runtime/python/bin/python3.12' if checked['platform'] == 'macos-arm64' else 'resources/runtime/python/python.exe'
    manifest = {'schema': 'vf.installer-bootstrap/v1', 'platform': checked['platform'], 'python': python,
                'driver': 'resources/installer/driver.py', 'entries': expected}
    raw = tx.encode(manifest)
    tx.require(len(raw) <= 8 * 1024 * 1024, 'BOOTSTRAP_MANIFEST_TOO_LARGE')
    (output / 'bootstrap-manifest.json').write_bytes(raw)
    (output / 'bootstrap-manifest.json').chmod(0o644)
    composition.verify_folder(candidate, expected_sha)
    tx.require(composition.workspace.sdk.sha(wheel_path) == WHEEL_SHA, 'BOOTSTRAP_WHEEL_CHANGED')
    return {'status': 'bootstrap_resources_assembled', 'manifest_sha256': tx.sha(raw),
            'entries': len(expected), 'file_bytes': sum(v['size'] for v in expected.values() if v['type'] == 'file'),
            'platform': checked['platform'], 'native_binary_built': False, 'installable': False,
            'publisher_signature_verified': False, 'windows_execution': False, 'desktop_acceptance': False}


SDK_SHA = 'e3a42e67a7a8acf450ebada2b074c7ca863000ca514907b7185b45e8f5cdff57'
SDK_VERSION = 'go1.26.11'


def build(candidate, expected_sha, wheel_path, sdk_archive, output, work):
    """Offline compile the manifest-bound source snapshot, not the live repo."""
    import platform
    import subprocess
    tx.require(platform.system() == 'Darwin' and platform.machine() == 'arm64', 'BOOTSTRAP_BUILD_HOST_UNSUPPORTED')
    output, work = map(lambda p: Path(p).absolute(), (output, work))
    for path in (output, work):
        tx.no_links(path)
    output, work = output.resolve(), work.resolve()
    tx.require(not output.exists() and not work.exists() and output.parent.is_dir() and work.parent.is_dir()
               and output != work and output not in work.parents and work not in output.parents,
               'NEW_DISJOINT_BUILD_ROOTS_REQUIRED')
    sdk_lock = composition.workspace.sdk.read_sdk_lock(TOOLS / 'workspace_tools/go-toolchain-lock.json')
    tx.require(sdk_lock['version'] == SDK_VERSION and sdk_lock['sha256'] == SDK_SHA,
               'BOOTSTRAP_TOOLCHAIN_UNVERIFIED')
    work.mkdir(mode=0o700)
    sdk = work / 'sdk'
    sdk_receipt = composition.workspace.sdk.extract_sdk(Path(sdk_archive), sdk, sdk_lock)
    assembled = assemble(candidate, expected_sha, wheel_path, output)
    manifest = composition.decode((output / 'bootstrap-manifest.json').read_bytes())
    extra = {'resources/provenance/go-toolchain-lock.json': tx.encode(sdk_lock)}
    for source in sorted(sdk.rglob('*')):
        if source.is_file() and source.name.upper().startswith(('LICENSE', 'LICENCE', 'COPYING', 'PATENTS')):
            name = 'resources/licenses/go-sdk/' + source.relative_to(sdk).as_posix()
            runtime_store.relative(name)
            extra[name] = composition.workspace.read(source)
    tx.require('resources/licenses/go-sdk/LICENSE' in extra, 'BOOTSTRAP_TOOLCHAIN_LICENSE_MISSING')
    provenance_path = output / 'resources/provenance/source-lock.json'
    provenance = composition.decode(provenance_path.read_bytes())
    provenance['go_sdk_source_sha256'] = SDK_SHA
    provenance['go_sdk_version'] = SDK_VERSION
    provenance['source_files'].update({name: tx.sha(raw) for name, raw in extra.items()})
    extra['resources/provenance/source-lock.json'] = tx.encode(provenance)
    for name, raw in extra.items():
        target = output / name
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        for parent in target.parents:
            if parent == output:
                break
            parent.chmod(0o700)
        # Only the builder-created provenance document is replaced here.
        tx.require(not target.exists() or name == 'resources/provenance/source-lock.json', 'BOOTSTRAP_MATERIAL_COLLISION')
        target.write_bytes(raw)
        target.chmod(0o644)
        manifest['entries'][name] = {'type': 'file', 'size': len(raw), 'sha256': tx.sha(raw), 'mode': 0o644}
    runtime_store.validate_manifest({'schema': runtime_store.SCHEMA, 'release': 'bootstrap.engineering',
                                     'source_sha256': expected_sha, 'entries': manifest['entries']})
    raw = tx.encode(manifest)
    tx.require(len(raw) <= 8 * 1024 * 1024, 'BOOTSTRAP_MANIFEST_TOO_LARGE')
    (output / 'bootstrap-manifest.json').write_bytes(raw)
    pin = tx.sha(raw)
    for name in ('temp', 'cache', 'gopath', 'modules', 'logs'):
        (work / name).mkdir(mode=0o700)
    env = {'PATH': str(sdk / 'bin'), 'LANG': 'C', 'LC_ALL': 'C', 'GOENV': 'off', 'GOWORK': 'off',
           'GOTOOLCHAIN': 'local', 'GOPROXY': 'off', 'GOSUMDB': 'off', 'GOFLAGS': '', 'CGO_ENABLED': '0',
           'GOROOT': str(sdk), 'GOPATH': str(work / 'gopath'), 'GOMODCACHE': str(work / 'modules'),
           'GOCACHE': str(work / 'cache'), 'GOTMPDIR': str(work / 'temp'), 'TMPDIR': str(work / 'temp')}
    platform_id = manifest['platform']
    goos, goarch, binary_name = ('darwin', 'arm64', 'vf-bootstrap') if platform_id == 'macos-arm64' else ('windows', 'amd64', 'vf-bootstrap.exe')
    env.update(GOOS=goos, GOARCH=goarch)
    source = output / 'resources/build-source/native'
    frozen_sources = {p.name: tx.sha(p.read_bytes()) for p in source.iterdir()}
    binary = output / binary_name
    cp = subprocess.run([str(sdk / 'bin/go'), 'build', '-mod=readonly', '-trimpath', '-buildvcs=false', '-p', '2',
                         '-ldflags=-s -w -X main.manifestSHA256=' + pin, '-o', str(binary), '.'],
                        cwd=source, env=env, capture_output=True, timeout=300)
    for label, content in (('compile.stdout', cp.stdout), ('compile.stderr', cp.stderr)):
        sanitized = content.replace(str(TOOLS.parents[2]).encode(), b'%WORKSPACE%').replace(str(Path.home()).encode(), b'%USERPROFILE%')
        (work / 'logs' / label).write_bytes(sanitized)
    tx.require(cp.returncode == 0, 'BOOTSTRAP_NATIVE_BUILD_FAILED')
    tx.require(frozen_sources == {p.name: tx.sha(p.read_bytes()) for p in source.iterdir()}, 'BOOTSTRAP_NATIVE_SOURCE_CHANGED')
    native_verified = False
    if platform_id == 'macos-arm64':
        cp = subprocess.run([str(binary), 'verify'], cwd=work, env={'PATH': '', 'LANG': 'C', 'LC_ALL': 'C'},
                            capture_output=True, timeout=90)
        (work / 'logs/native-verify.stdout').write_bytes(cp.stdout)
        tx.require(cp.returncode == 0 and json.loads(cp.stdout)['status'] == 'bootstrap_resources_verified',
                   'BOOTSTRAP_NATIVE_VERIFICATION_FAILED')
        native_verified = True
    receipt = {'schema': 'vf.installer-bootstrap-build/v1', 'status': 'unsigned_bootstrap_built',
               'manifest_sha256': pin, 'platform': platform_id, 'entries': len(manifest['entries']),
               'file_bytes': sum(v['size'] for v in manifest['entries'].values() if v['type'] == 'file'),
               'binary': {'name': binary_name, 'size': binary.stat().st_size, 'sha256': composition.workspace.sdk.sha(binary)},
               'go_sdk_source_sha256': SDK_SHA, 'sdk_extraction': sdk_receipt,
               'native_source_files': frozen_sources, 'native_resource_verifier_executed': native_verified,
               'toolchain_license_files': len(extra) - 2, 'build_from_manifest_bound_source': True,
               'network_enabled': False, 'global_toolchain_changed': False, 'installable': False,
               'public_distribution': False, 'publisher_signature_verified': False,
               'windows_execution': False, 'desktop_acceptance': False, 'mcp_acceptance': False,
               'independent_review': 'pending'}
    (work / 'build-receipt.json').write_bytes(tx.encode(receipt))
    return receipt
