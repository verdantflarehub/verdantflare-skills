"""Read-only acceptance preparation. No desktop or business acceptance is claimed.

The full matrix always contains 110 skill cells and 60 business rows. A scope
selects work, never removes obligations. Native drivers, locked-exam admission
and independent verification must be integrated before a formal pass is possible.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import stat
import sys
import uuid

MATRIX_SCHEMA = 'vf.desktop-skill-validation.matrix/v1'
HOSTS = ('codex', 'claude', 'antigravity', 'workbuddy', 'deepseek')
SYSTEMS = ('macos', 'windows')
SKILLS = (
    'verdantflare-artifact', 'verdantflare-project', 'verdantflare-blender',
    'verdantflare-image', 'verdantflare-video', 'verdantflare-music',
    'verdantflare-speech', 'verdantflare-character-face',
    'verdantflare-reference-learning', 'verdantflare-music-mv',
    'x-verdantflare-short-drama',
)
CASES = {
    'host': ('HOST-01',), 'build': ('BUILD-01',), 'package': ('PKG-01',),
    'install': ('INSTALL-01', 'INSTALL-02', 'INSTALL-03'), 'route': ('ROUTE-01',),
    'mcp': ('MCP-01', 'MCP-02'),
    'auth': ('AUTH-01', 'AUTH-02', 'AUTH-03', 'AUTH-04', 'AUTH-05'),
    'business': ('BIZ-01', 'BIZ-02'),
    'media': ('MEDIA-01', 'MEDIA-02', 'MEDIA-03'), 'rating': ('RATING-01',),
    'market': ('MARKET-01',), 'coverage': ('EVIDENCE-01',),
    'evidence': ('EVIDENCE-01',), 'journey': ('JOURNEY-01',),
    'onboarding': ('ONBOARD-01',), 'compatibility': ('COMPAT-01', 'COMPAT-02'),
    'recovery': ('RECOVERY-01',), 'docs': ('DOC-01',), 'operations': ('OPS-01',),
}
BUSINESS = ('JOURNEY-01', 'ONBOARD-01', 'COMPAT-01', 'COMPAT-02', 'RECOVERY-01', 'DOC-01')
ENV_FIELDS = (
    'arch', 'model', 'desktop_session_id', 'fixture_id', 'installation_id',
    'profile_scope', 'identity_context_ref', 'service_version',
)
JSON_LIMIT = 4 * 1024 * 1024
JSON_MAX_DEPTH = 64
JSON_MAX_INTEGER_DIGITS = 4300
OPAQUE = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z')
SHA256 = re.compile(r'[a-f0-9]{64}\Z')


class ValidationError(ValueError):
    """Only fixed, non-sensitive error codes may leave the preparation tool."""


def require(condition, code):
    if not condition:
        raise ValidationError(code)


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def strict_json(raw):
    # Bound nesting before the decoder allocates containers. Python versions
    # have different recursion limits; delimiters inside strings do not count.
    depth, in_string, escaped = 0, False, False
    for char in raw:
        if in_string:
            if escaped:
                escaped = False
            elif char == 92:
                escaped = True
            elif char == 34:
                in_string = False
        elif char == 34:
            in_string = True
        elif char in (91, 123):
            depth += 1
            require(depth <= JSON_MAX_DEPTH, 'JSON_NESTING_LIMIT')
        elif char in (93, 125):
            depth -= 1

    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, 'DUPLICATE_JSON_KEY')
            result[key] = value
        return result

    def constant(_):
        raise ValidationError('NONFINITE_JSON_NUMBER')

    def number(text):
        value = float(text)
        require(math.isfinite(value), 'NONFINITE_JSON_NUMBER')
        return value

    def integer(text):
        require(len(text.lstrip('-')) <= JSON_MAX_INTEGER_DIGITS, 'JSON_INTEGER_LIMIT')
        return int(text)

    try:
        return json.loads(raw.decode('utf-8'), object_pairs_hook=pairs, parse_constant=constant,
                          parse_float=number, parse_int=integer)
    except ValidationError:
        raise
    except (UnicodeError, ValueError, RecursionError):
        raise ValidationError('INVALID_JSON') from None


def no_links(path):
    path = Path(path).absolute()
    require('..' not in path.parts, 'UNSAFE_PATH')
    for entry in (*reversed(path.parents), path):
        if entry.exists() or entry.is_symlink():
            require(not entry.is_symlink(), 'LINK_NOT_ALLOWED')
    return path


def stamp(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns,
            info.st_ctime_ns, info.st_mode, info.st_nlink)


def parent_fd(path):
    """Traverse by descriptors so a checked directory cannot become a link."""
    path = no_links(path)
    require(hasattr(os, 'O_NOFOLLOW') and hasattr(os, 'O_DIRECTORY'), 'FILE_SAFETY_UNAVAILABLE')
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    fd = os.open(path.anchor, flags)
    try:
        for name in path.parts[1:-1]:
            child = os.open(name, flags, dir_fd=fd)
            os.close(fd)
            fd = child
        return fd, path.name
    except BaseException:
        os.close(fd)
        raise


def read_file(path, *, keep=True, limit=JSON_LIMIT):
    directory, name = parent_fd(path)
    try:
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        try:
            before = os.fstat(fd)
            require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1, 'REGULAR_FILE_REQUIRED')
            require(limit is None or before.st_size <= limit, 'INPUT_TOO_LARGE')
            chunks, size, h = [], 0, hashlib.sha256()
            while True:
                block = os.read(fd, 1024 * 1024)
                if not block:
                    break
                size += len(block)
                require(limit is None or size <= limit, 'INPUT_TOO_LARGE')
                h.update(block)
                if keep:
                    chunks.append(block)
            require(stamp(before) == stamp(os.fstat(fd))
                    == stamp(os.stat(name, dir_fd=directory, follow_symlinks=False)), 'INPUT_CHANGED')
            require(size == before.st_size, 'INPUT_CHANGED')
            return b''.join(chunks) if keep else None, size, h.hexdigest()
        finally:
            os.close(fd)
    finally:
        os.close(directory)


def exact_keys(value, keys, code):
    require(type(value) is dict and set(value) == set(keys), code)


def opaque_or_null(value):
    return value is None or type(value) is str and OPAQUE.fullmatch(value) is not None


def hash_or_null(value):
    return value is None or type(value) is str and SHA256.fullmatch(value) is not None


def exam_info(path):
    raw, _, sha = read_file(path)
    try:
        text = raw.decode('utf-8')
    except UnicodeError:
        raise ValidationError('INVALID_EXAM_ENCODING') from None
    start = re.search(r'^## 2\. ', text, re.M)
    end = re.search(r'^## 3\. ', text, re.M)
    require(start is not None and end is not None and start.end() < end.start(), 'EXAM_CHAPTERS_REQUIRED')
    body = text[start.end():end.start()]
    # Only actual assertion bullets in Chapter 2 count; examples inside fences do not.
    body = re.sub(r'^```[^\n]*\n.*?^```[^\n]*$', '', body, flags=re.M | re.S)
    found = re.findall(r'^(?:- )?([A-Z]+-[0-9]{2})[：:]', body, re.M)
    expected = set(case for values in CASES.values() for case in values)
    require(set(found) == expected and len(found) == len(expected), 'EXAM_CASESET_CHANGED')
    return {'sha256': sha, 'cases': sorted(expected), 'lock_verified': False}


def new_matrix(exam):
    environments = [
        {'environment_id': f'{system}-{host}', 'os': system, 'host': host,
         **dict.fromkeys(ENV_FIELDS)}
        for system in SYSTEMS for host in HOSTS
    ]
    return {
        'schema': MATRIX_SCHEMA, 'exam_sha256': exam['sha256'], 'release_id': None,
        'surface_release_sha256': None, 'guide_release_sha256': None,
        'package_sha256': dict.fromkeys(SKILLS),
        'environments': environments, 'skills': list(SKILLS),
        'skill_cells': [{'environment_id': e['environment_id'], 'skill_id': skill}
                        for e in environments for skill in SKILLS],
        'business_rows': [{'environment_id': e['environment_id'], 'case_id': case}
                          for e in environments for case in BUSINESS],
    }


def check_matrix(value, exam):
    expected = new_matrix(exam)
    exact_keys(value, expected, 'MATRIX_FIELDS_INVALID')
    require(value['schema'] == MATRIX_SCHEMA, 'MATRIX_SCHEMA_INVALID')
    require(value['exam_sha256'] == exam['sha256'], 'EXAM_DIGEST_CHANGED')
    require(opaque_or_null(value['release_id']), 'RELEASE_ID_INVALID')
    for field in ('surface_release_sha256', 'guide_release_sha256'):
        require(hash_or_null(value[field]), 'RELEASE_HASH_INVALID')
    require(type(value['skills']) is list and len(value['skills']) == len(SKILLS)
            and all(type(x) is str for x in value['skills'])
            and set(value['skills']) == set(SKILLS), 'SKILL_SET_CHANGED')
    exact_keys(value['package_sha256'], SKILLS, 'PACKAGE_SET_CHANGED')
    require(all(hash_or_null(x) for x in value['package_sha256'].values()), 'PACKAGE_HASH_INVALID')
    envs = value['environments']
    require(type(envs) is list and len(envs) == 10, 'ENVIRONMENT_SET_CHANGED')
    observed = set()
    for row in envs:
        exact_keys(row, expected['environments'][0], 'ENVIRONMENT_FIELDS_INVALID')
        require(type(row['os']) is str and row['os'] in SYSTEMS
                and type(row['host']) is str and row['host'] in HOSTS, 'ENVIRONMENT_INVALID')
        key = f"{row['os']}-{row['host']}"
        require(row['environment_id'] == key and key not in observed, 'ENVIRONMENT_DUPLICATE_OR_MISMATCH')
        observed.add(key)
        require(all(opaque_or_null(row[field]) for field in ENV_FIELDS), 'ENVIRONMENT_VALUE_INVALID')
    for field, key in (('skill_cells', 'skill_id'), ('business_rows', 'case_id')):
        rows = value[field]
        require(type(rows) is list and len(rows) == len(expected[field]), 'COVERAGE_COUNT_CHANGED')
        entries = set()
        for row in rows:
            exact_keys(row, ('environment_id', key), 'COVERAGE_FIELDS_INVALID')
            require(type(row['environment_id']) is str and type(row[key]) is str, 'COVERAGE_VALUE_INVALID')
            pair = row['environment_id'], row[key]
            require(pair not in entries, 'COVERAGE_DUPLICATE')
            entries.add(pair)
        require(entries == {(r['environment_id'], r[key]) for r in expected[field]}, 'COVERAGE_SET_CHANGED')
    return value


def evidence_info(path):
    raw, _, sha = read_file(path)
    value = strict_json(raw)
    exact_keys(value, ('schema', 'files'), 'EVIDENCE_MANIFEST_FIELDS_INVALID')
    require(value['schema'] == 'vf.desktop-skill-validation.evidence/v1', 'EVIDENCE_SCHEMA_INVALID')
    require(type(value['files']) is list and value['files'], 'EVIDENCE_FILES_REQUIRED')
    root, seen, total = no_links(Path(path).parent), set(), 0
    for row in value['files']:
        exact_keys(row, ('path', 'bytes', 'sha256'), 'EVIDENCE_FILE_FIELDS_INVALID')
        name = row['path']
        require(type(name) is str and re.fullmatch(r'[A-Za-z0-9_./-]+', name)
                and not name.startswith('/') and '//' not in name, 'EVIDENCE_PATH_INVALID')
        parts = name.split('/')
        require(all(p not in ('', '.', '..') for p in parts)
                and not re.match(r'^[A-Za-z]:', name), 'EVIDENCE_PATH_INVALID')
        normalized = str(PurePosixPath(name)).casefold()
        require(normalized not in seen, 'EVIDENCE_PATH_DUPLICATE')
        seen.add(normalized)
        require(type(row['bytes']) is int and 0 <= row['bytes'] <= 2**63 - 1
                and type(row['sha256']) is str and SHA256.fullmatch(row['sha256']), 'EVIDENCE_METADATA_INVALID')
        _, count, file_sha = read_file(root.joinpath(*parts), keep=False, limit=None)
        require(count == row['bytes'] and file_sha == row['sha256'], 'EVIDENCE_CONTENT_MISMATCH')
        total += count
    return {'manifest_sha256': sha, 'files_verified': len(seen), 'bytes_verified': total,
            'integrity_only': True, 'native_source_verified': False, 'independent_verdict_verified': False}


def selection(groups, scope_os, scope_hosts):
    selected = groups.split(',')
    hosts = scope_hosts.split(',')
    require(len(selected) == len(set(selected)) and all(g in CASES for g in selected), 'SELECTOR_INVALID')
    require(scope_os in (*SYSTEMS, 'all') and len(hosts) == len(set(hosts))
            and hosts and all(h in HOSTS for h in hosts), 'SCOPE_INVALID')
    require('claude' not in hosts, 'CLAUDE_DESKTOP_PAUSED')
    cases = {case for group in selected for case in CASES[group]}
    return cases, [h for h in HOSTS if h in hosts]


def report(matrix, exam, *, phase, scope_os, scope_hosts, selected, evidence=None):
    require(phase in ('candidate', 'production'), 'PHASE_INVALID')
    cases, hosts = selection(selected, scope_os, scope_hosts)
    now = datetime.now(timezone.utc).isoformat()
    run_id = str(uuid.uuid4())
    envs = {e['environment_id']: e for e in matrix['environments']}
    scope_envs = {key for key, e in envs.items()
                  if (scope_os == 'all' or e['os'] == scope_os) and e['host'] in hosts}
    blockers = ['NATIVE_DESKTOP_DRIVER_NOT_IMPLEMENTED', 'CP0_ADMISSION_NOT_VERIFIED',
                'EXAM_LOCK_NOT_VERIFIED', 'INDEPENDENT_REVIEW_NOT_VERIFIED']

    def row(kind, case_id, env_id, *, skill=None, required_cases=()):
        env = envs.get(env_id, {})
        in_scope = env_id in scope_envs or env_id is None
        in_group = bool(set(required_cases) & cases) if kind == 'skill_cell' else case_id in cases
        if env.get('host') == 'claude':
            status, reason = 'not_run', 'CLAUDE_DESKTOP_PAUSED_A21'
        elif env.get('os') == 'windows':
            status, reason = 'not_run', 'WINDOWS_DELEGATED_TO_MENGSK_A20'
        elif not in_scope:
            status, reason = 'not_run', 'OUTSIDE_SELECTED_SCOPE'
        elif not in_group:
            status, reason = 'not_run', 'OUTSIDE_SELECTED_GROUPS'
        else:
            status, reason = 'blocked', 'FORMAL_EXECUTION_NOT_ADMITTED'
        result = {
            'schema_version': 'vf.desktop-skill-validation.result/v1',
            'mode': 'preflight', 'record_type': kind, 'run_id': run_id,
            'scope_os': scope_os, 'scope_hosts': hosts, 'phase': phase,
            'case_id': case_id, 'subcase_id': skill, 'skill_id': skill,
            'environment_id': env_id, 'host_profile': env.get('host'), 'os': env.get('os'),
            **{field: env.get(field) for field in ENV_FIELDS},
            'exam_sha256': exam['sha256'], 'release_id': matrix['release_id'],
            'surface_release_sha256': matrix['surface_release_sha256'],
            'guide_release_sha256': matrix['guide_release_sha256'],
            'package_sha256': matrix['package_sha256'].get(skill),
            'request_id': None, 'task_id': None, 'expected': 'all_applicable_assertions_and_native_evidence',
            'actual': None, 'status': status, 'reason_code': reason,
            'started_at': now, 'finished_at': now, 'evidence_refs': [],
            # Inventory does not assert that every case applies to every skill.
            'assertion_inventory': list(required_cases), 'applicability_verified': False,
            'execution_admitted': False, 'desktop_acceptance': False, 'business_acceptance': False,
            'blockers': blockers if status == 'blocked' else [],
        }
        result['unobserved_fields'] = [field for field, val in result.items() if val is None]
        result['unobserved_reason'] = 'NO_FORMAL_DRIVER_OBSERVATION'
        return result

    all_cases = sorted(set(c for values in CASES.values() for c in values))
    skill_cases = [c for c in all_cases if c not in (*BUSINESS, 'OPS-01')]
    rows = [row('skill_cell', 'EVIDENCE-01', x['environment_id'], skill=x['skill_id'],
                required_cases=skill_cases) for x in matrix['skill_cells']]
    rows += [row('business_row', x['case_id'], x['environment_id']) for x in matrix['business_rows']]
    rows += [row('assertion', case, e['environment_id'])
             for e in matrix['environments'] for case in skill_cases]
    rows += [row('release_assertion', 'OPS-01', None)]
    summary = {
        'schema': 'vf.desktop-skill-validation.summary/v1', 'mode': 'preflight',
        'run_id': run_id, 'phase': phase, 'scope_os': scope_os, 'scope_hosts': hosts,
        'exam_sha256': exam['sha256'], 'selected_assertions': sorted(cases),
        'full_matrix': {'environments': 10, 'skill_cells': 110, 'business_rows': 60},
        'selected_scope': {'environments': len(scope_envs), 'skill_cells': len(scope_envs) * 11,
                           'business_rows': len(scope_envs) * 6},
        'status_counts': dict(Counter(r['status'] for r in rows)),
        'formal_skill_cells_passed': 0, 'formal_business_rows_passed': 0,
        'execution_admitted': False, 'desktop_acceptance': False, 'business_acceptance': False,
        'independent_verification': False, 'case_applicability_frozen': False,
        'blockers': blockers, 'evidence_integrity': evidence,
        'status': 'blocked', 'exit_code': 2,
    }
    return summary, rows


def encode(value):
    return (json.dumps(value, sort_keys=True, ensure_ascii=True, allow_nan=False, indent=2) + '\n').encode('ascii')


def write_new(path, raw):
    directory, name = parent_fd(path)
    try:
        require(os.fstat(directory).st_uid == os.getuid(), 'PRIVATE_OUTPUT_UNAVAILABLE')
        write_into(directory, name, raw)
    finally:
        os.close(directory)


def write_into(directory, name, raw):
    try:
        fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
    except FileExistsError:
        raise ValidationError('NEW_OUTPUT_REQUIRED') from None
    with os.fdopen(fd, 'wb') as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


def write_report(output, summary, rows):
    payloads = {
        'summary.json': encode(summary),
        'results.jsonl': b''.join((json.dumps(r, sort_keys=True, ensure_ascii=True, allow_nan=False) + '\n').encode('ascii')
                                   for r in rows),
    }
    manifest = {'schema': 'vf.desktop-skill-validation.evidence/v1',
                'files': [{'path': name, 'bytes': len(raw), 'sha256': digest(raw)} for name, raw in payloads.items()]}
    parent, name = parent_fd(output)
    try:
        require(os.fstat(parent).st_uid == os.getuid(), 'PRIVATE_OUTPUT_UNAVAILABLE')
        try:
            os.mkdir(name, mode=0o700, dir_fd=parent)
        except FileExistsError:
            raise ValidationError('NEW_OUTPUT_REQUIRED') from None
        directory = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
        try:
            require(stat.S_IMODE(os.fstat(directory).st_mode) & 0o077 == 0, 'PRIVATE_OUTPUT_UNAVAILABLE')
            for filename, raw in payloads.items():
                write_into(directory, filename, raw)
            write_into(directory, 'manifest.json', encode(manifest))
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        os.close(parent)


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ValidationError('ARGUMENTS_INVALID')


def main(argv=None):
    try:
        parser = Parser(description=__doc__)
        parser.add_argument('--matrix', type=Path)
        parser.add_argument('--init-matrix', type=Path)
        parser.add_argument('--exam', type=Path, required=True)
        parser.add_argument('--phase', default='candidate')
        parser.add_argument('--select', default=','.join(CASES))
        parser.add_argument('--scope-os', default='macos')
        parser.add_argument('--scope-hosts', default='workbuddy')
        parser.add_argument('--evidence-manifest', type=Path)
        parser.add_argument('--output', type=Path)
        args = parser.parse_args(argv)
        require(args.phase in ('candidate', 'production'), 'PHASE_INVALID')
        exam = exam_info(args.exam)
        if args.init_matrix is not None:
            require(args.matrix is None and args.output is None and args.evidence_manifest is None,
                    'INITIALIZATION_ARGUMENTS_CONFLICT')
            selection(args.select, args.scope_os, args.scope_hosts)
            write_new(args.init_matrix, encode(new_matrix(exam)))
            print(json.dumps({'status': 'matrix_prepared', 'execution_admitted': False,
                              'skill_cells': 110, 'business_rows': 60, 'exit_code': 2}))
            return 2
        require(args.matrix is not None and args.output is not None, 'MATRIX_AND_OUTPUT_REQUIRED')
        raw, _, matrix_sha = read_file(args.matrix)
        matrix = check_matrix(strict_json(raw), exam)
        evidence = evidence_info(args.evidence_manifest) if args.evidence_manifest is not None else None
        summary, rows = report(matrix, exam, phase=args.phase, scope_os=args.scope_os,
                               scope_hosts=args.scope_hosts, selected=args.select, evidence=evidence)
        summary['matrix_sha256'] = matrix_sha
        write_report(args.output, summary, rows)
        print(json.dumps(summary, sort_keys=True))
        return 2
    except ValidationError as error:
        print(json.dumps({'status': 'rejected', 'code': str(error), 'exit_code': 1}))
        return 1
    except (OSError, UnicodeError, RecursionError, OverflowError):
        print(json.dumps({'status': 'rejected', 'code': 'INPUT_OR_OUTPUT_UNAVAILABLE', 'exit_code': 1}))
        return 1


if __name__ == '__main__':
    sys.exit(main())
