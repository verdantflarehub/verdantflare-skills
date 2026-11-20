"""Fixed bootstrap driver; isolated engine self-check only, never user install.

The native launcher must admit every resource before executing this file. Direct
execution is engineering diagnostics and does not establish publisher trust.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import tomllib


class Interrupted(BaseException):
    pass


def configure_bundle():
    if not sys.flags.isolated or not sys.dont_write_bytecode:
        raise ValueError('ISOLATED_BOOTSTRAP_REQUIRED')
    here = Path(__file__).resolve().parent
    core, vendor = here / 'core', here.parent / 'vendor'
    if not core.is_dir() or not vendor.is_dir():
        raise ValueError('BOOTSTRAP_MODULES_MISSING')
    sys.path[:0] = [str(core), str(vendor)]
    import tomlkit
    if tomlkit.__version__ != '0.15.2' or not Path(tomlkit.__file__).resolve().is_relative_to(vendor):
        raise ValueError('BOOTSTRAP_DEPENDENCY_MISMATCH')


def self_check(output):
    from install_operation import InstallOperation
    import install_transaction as tx

    tx.require(os.name == 'posix', 'WINDOWS_PRIVATE_STORAGE_UNVERIFIED')
    output = Path(output).absolute()
    tx.no_links(output)
    output = output.resolve()
    bundle = Path(__file__).resolve().parents[2]
    tx.require(not output.exists() and output.parent.is_dir() and not output.is_relative_to(bundle),
               'NEW_EXTERNAL_OUTPUT_REQUIRED')
    tx.require(output.parent.stat().st_uid == os.getuid(), 'OUTPUT_OWNER_REQUIRED')
    output.mkdir(mode=0o700)
    checks = []
    for fmt in ('json', 'toml'):
        root = output / fmt
        root.mkdir(mode=0o700)
        for name in ('state', 'skills', 'config'):
            (root / name).mkdir(mode=0o700)
        target = 'config.toml' if fmt == 'toml' else 'mcp.json'
        engine = InstallOperation(*(root / n for n in ('state', 'skills', 'config')), target, config_format=fmt)
        initial = b'# synthetic non-VF comment\ntheme="dark"\n' if fmt == 'toml' else b'{"theme":"dark"}\n'
        engine.config.path.write_bytes(initial)
        engine.config.path.chmod(0o640)
        before = engine.skills.snapshot(), engine.config.snapshot(), engine.config._file()
        desired = {'url': 'https://vf-bootstrap-selfcheck.invalid/mcp'}
        skill = {'vf-bootstrap-selfcheck/SKILL.md': b'# Synthetic engine self-check only\n'}

        def apply(checkpoint=lambda _: None):
            observed = engine.config.observe()
            return engine.apply('bootstrap.fixture', skill, desired,
                                expected_skill_revision=engine.skills.snapshot()[1],
                                expected_config_revision=observed['ledger_revision'],
                                expected_config_file=observed['file'], checkpoint=checkpoint)

        def interrupt(phase):
            if phase == 'config':
                raise Interrupted()
        try:
            apply(interrupt)
        except Interrupted:
            pass
        else:
            raise ValueError('EXPECTED_SELF_CHECK_INTERRUPTION')
        engine.recover()
        engine.recover()
        tx.require(before == (engine.skills.snapshot(), engine.config.snapshot(), engine.config._file()),
                   'SELF_CHECK_RECOVERY_MISMATCH')
        tx.require(engine.config.path.read_bytes() == initial and engine.config.path.stat().st_mode & 0o777 == 0o640,
                   'SELF_CHECK_ORIGINAL_CONFIG_CHANGED')
        apply()
        engine.recover()
        content = engine.config.path.read_bytes()
        parsed = tomllib.loads(content.decode('utf-8')) if fmt == 'toml' else json.loads(content)
        parent = 'mcp_servers' if fmt == 'toml' else 'mcpServers'
        tx.require(parsed['theme'] == 'dark' and parsed[parent]['vf-studio'] == desired,
                   'SELF_CHECK_CONFIG_MISMATCH')
        tx.require(all((engine.skills.root / name).read_bytes() == raw for name, raw in skill.items())
                   and engine._active()['state'] == 'cleaned', 'SELF_CHECK_COMMIT_MISMATCH')
        checks.append({'config_format': fmt, 'interrupted_operation_restored': True,
                       'subsequent_operation_committed': True, 'private_backups_cleaned': True,
                       'foreign_config_and_mode_preserved': True,
                       'config_sha256': hashlib.sha256(content).hexdigest()})
    receipt = {'schema': 'vf.installer-bootstrap-selfcheck/v1', 'status': 'isolated_engine_selfcheck_passed',
               'python_isolated': bool(sys.flags.isolated), 'checks': checks, 'synthetic_inputs_only': True,
               'user_config_changed': False, 'installable': False, 'host_verified': False,
               'windows_execution': False, 'desktop_acceptance': False, 'mcp_acceptance': False}
    path = output / 'selfcheck-receipt.json'
    with path.open('x', encoding='utf-8') as stream:
        path.chmod(0o600)
        stream.write(json.dumps(receipt, sort_keys=True, indent=2) + '\n')
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('self-check', 'offline-operation', 'tool-name-profile', 'observe-hosts'))
    parser.add_argument('--output', type=Path)
    parser.add_argument('--request', type=Path)
    args = parser.parse_args()
    configure_bundle()
    if args.command == 'observe-hosts':
        if args.output is not None or args.request is not None:
            raise ValueError('BOOTSTRAP_ARGUMENTS_INVALID')
        if sys.platform != 'darwin':
            raise ValueError('HOST_DISCOVERY_PLATFORM_UNIMPLEMENTED')
        from host_discovery import scan_roots, receipt
        roots = [p for p in (Path('/Applications'), Path.home() / 'Applications') if p.exists()]
        applications, errors = scan_roots(roots)
        result = receipt(applications, errors)
    elif args.command == 'self-check':
        if args.output is None or args.request is not None:
            raise ValueError('BOOTSTRAP_ARGUMENTS_INVALID')
        result = self_check(args.output)
    else:
        if args.request is None or args.output is not None:
            raise ValueError('BOOTSTRAP_ARGUMENTS_INVALID')
        if args.command == 'tool-name-profile':
            from tool_name_diagnostic import run
            result = run(args.request)
        else:
            from offline_operation import run
            result = run(Path(__file__).resolve().parents[2], args.request)
    print(json.dumps(result, sort_keys=True))
    return 1 if args.command == 'observe-hosts' and not result['scan_complete'] else 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as exc:
        # Core conflict errors are constant identifiers. Never include content or paths.
        code = str(exc)
        if not code or not all(c in 'ABCDEFGHIJKLMNOPQRSTUVWXYZ_' for c in code):
            code = 'BOOTSTRAP_ENGINE_SELF_CHECK_FAILED'
        print(json.dumps({'error': code, 'host_verified': False}), file=sys.stderr)
        raise SystemExit(2)
