#!/usr/bin/env python3
"""Check reference provenance against the actual import manifest, not image quality."""
import argparse
import hashlib
import json
import re
from pathlib import Path


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def validate(imports, audit, base, repair_target_key=None):
    errors, warnings = [], []
    if not isinstance(audit, dict):
        return {'errors': ['Audit object required'], 'warnings': [], 'record_check_passed': False}
    records = audit.get('references', [])
    if (not isinstance(imports, list) or not isinstance(records, list)
            or not all(isinstance(r, dict) for r in imports + records)):
        return {'errors': ['Reference lists required'], 'warnings': [], 'record_check_passed': False}
    expected = [r.get('key') for r in imports]
    actual = [r.get('key') for r in records]
    if not expected or any(not isinstance(k, str) or not k for k in expected + actual):
        return {'errors': ['Nonempty reference keys required'], 'warnings': [], 'record_check_passed': False}
    if len(set(expected)) != len(expected) or len(set(actual)) != len(actual):
        errors.append('Duplicate reference key')
    if set(expected) != set(actual):
        errors.append('Audit coverage differs from actual import manifest')
    imported = {r.get('key'): r.get('sha256') for r in imports}

    def read_file(spec):
        path = base / spec['path']
        if digest(path) != spec['sha256']:
            raise ValueError('File digest mismatch')
        return path

    for item in records:
        key = item.get('key', '<missing>')
        try:
            role = item['role']
            if not isinstance(role, str) or not role.strip():
                raise ValueError('Reference role required')
            read_file(item['input'])
            if item['input']['sha256'] != imported.get(key):
                raise ValueError('Input differs from actual imported bytes')
            read_file(item['source'])
            review = json.loads(read_file(item['source_review']).read_text())
            decision = review.get('decision')
            if decision not in {'approved', 'in_review', 'candidate', 'needs_revision', 'rejected'}:
                raise ValueError('Unknown or missing source review decision')
            if decision in {'needs_revision', 'rejected'}:
                if key == repair_target_key and role == 'repair_target':
                    warnings.append(f'{key}: failed source is an edit target, not a reusable reference')
                    continue
                # Calling a full rejected frame "spatial reference" cannot remove its defects.
                if item.get('derivation') not in {'crop', 'masked_repair'}:
                    raise ValueError('Rejected full frame cannot be reused by changing its role')
                if item['input']['sha256'] == item['source']['sha256']:
                    raise ValueError('Derived input is byte-identical to rejected source')
                scoped = json.loads(read_file(item['scope_review']).read_text())
                if (scoped.get('decision') != 'usable_for_scope'
                        or scoped.get('input_sha256') != item['input']['sha256']
                        or scoped.get('source_sha256') != item['source']['sha256']
                        or scoped.get('role') != role
                        or not isinstance(scoped.get('excluded_defects'), list)
                        or not scoped['excluded_defects']
                        or scoped.get('remaining_conflicts') != []):
                    raise ValueError('Derived reference lacks matching independent scope review')
                warnings.append(f'{key}: scope record checked; pixels and transformation still need visual review')
            elif decision != 'approved':
                warnings.append(f'{key}: source remains {decision}; record check is not creative approval')
        except (KeyError, TypeError, ValueError, OSError) as exc:
            # Never echo local absolute paths from filesystem exceptions.
            reason = str(exc) if isinstance(exc, ValueError) else type(exc).__name__
            errors.append(f'{key}: {reason}')
    return {'record_check_passed': not errors, 'artistic_approval': False,
            'errors': errors, 'warnings': warnings}


def request_digest(request):
    """Bind the exact saved request, including the tool name when present."""
    return hashlib.sha256(json.dumps(request, sort_keys=True, separators=(',', ':'),
                                    ensure_ascii=False, allow_nan=False).encode('utf-8')).hexdigest()


def input_slots(request):
    """Read actual Image/Video fields; import manifest order has no semantics."""
    slots = []
    source = None
    face_fields = {'target_artifact_id', 'source_identity_artifact_id'}
    image_fields = {'source_artifact_id', 'reference_artifact_ids', 'mask_artifact_id'}
    if not isinstance(request, dict):
        raise ValueError('Actual request object required')
    if 'references' in request:
        if (face_fields | image_fields).intersection(request):
            raise ValueError('Mixed Image and Video input fields')
        references = request['references']
        if not isinstance(references, dict) or set(references) - {'images', 'videos', 'audios'}:
            raise ValueError('Video reference groups required')
        for group in ('images', 'videos', 'audios'):
            items = references.get(group, [])
            if not isinstance(items, list):
                raise ValueError('Video reference list required')
            for index, item in enumerate(items):
                if not isinstance(item, dict) or not isinstance(item.get('purpose'), str) or not item['purpose']:
                    raise ValueError('Video reference purpose required')
                slots.append({'slot': f'references.{group}.{index}',
                              'artifact_id': item.get('artifact_id'), 'purpose': item['purpose']})
    elif face_fields.intersection(request):
        if not face_fields.issubset(request) or image_fields.intersection(request):
            raise ValueError('Incomplete or mixed face-repair input fields')
        source = request['target_artifact_id']
        for field in ('target_artifact_id', 'source_identity_artifact_id'):
            slots.append({'slot': field, 'artifact_id': request[field]})
        if source == request['source_identity_artifact_id']:
            raise ValueError('Face-repair target cannot substitute for source identity')
    else:
        source = request.get('source_artifact_id')
        if 'source_artifact_id' in request:
            slots.append({'slot': 'source_artifact_id', 'artifact_id': source})
        references = request.get('reference_artifact_ids', [])
        if not isinstance(references, list):
            raise ValueError('Image reference list required')
        slots.extend({'slot': f'reference_artifact_ids.{i}', 'artifact_id': item}
                     for i, item in enumerate(references))
        if 'mask_artifact_id' in request:
            slots.append({'slot': 'mask_artifact_id', 'artifact_id': request['mask_artifact_id']})
    if not slots or not all(isinstance(x['artifact_id'], str) and x['artifact_id'] for x in slots):
        raise ValueError('Actual media artifact references required')
    return slots, source


def validate_request(imports, request, locks, require_bindings=False):
    """Check actual inputs; production mode also binds slots and the frozen request."""
    errors = []
    if not isinstance(request, dict):
        return ['Actual request object required'], None
    saved_request = request
    request = request.get('arguments', request)
    try:
        slots, source = input_slots(request)
    except ValueError as exc:
        return [str(exc)], None
    actual = [x['artifact_id'] for x in slots]
    if not isinstance(imports, list) or not all(isinstance(x, dict) for x in imports):
        return ['Import records required'], None
    mapped = [x.get('artifact_id') for x in imports]
    keys = [x.get('key') for x in imports]
    if any(not isinstance(k, str) or not k for k in keys) or len(set(keys)) != len(keys):
        return ['Unique nonempty import keys required'], None
    if any(not isinstance(x, str) or not x for x in mapped):
        return ['Every imported input needs its actual artifact_id'], None
    elif set(actual) != set(mapped) or len(set(mapped)) != len(mapped):
        errors.append('Import mapping differs from actual request inputs')
    if not isinstance(locks, dict) or not isinstance(locks.get('required_references'), list):
        return errors + ['Scoped fixed reference list required'], None
    if not locks['required_references']:
        errors.append('Scoped fixed reference list cannot be empty')
    if not locks.get('project_id') or request.get('project_id') != locks['project_id']:
        errors.append('Request project differs from fixed reference project')
    by_id = {x.get('artifact_id'): x for x in imports}
    for anchor in locks['required_references']:
        if not isinstance(anchor, dict):
            errors.append('Invalid fixed reference record')
            continue
        key = anchor.get('key', '<missing>')
        artifact = anchor.get('artifact_id')
        sha = anchor.get('sha256')
        if not isinstance(sha, str) or not re.fullmatch(r'[0-9a-f]{64}', sha) or not anchor.get('basis'):
            errors.append(f'{key}: fixed digest and source basis required')
        if not artifact or artifact not in actual:
            errors.append(f'{key}: fixed reference missing from actual request')
        elif by_id.get(artifact, {}).get('sha256') != sha:
            errors.append(f'{key}: fixed reference differs from imported bytes')
        elif by_id.get(artifact, {}).get('key') != key:
            errors.append(f'{key}: fixed role key differs from imported reference')
    bindings = locks.get('input_bindings')
    expected = [{**slot, 'key': by_id.get(slot['artifact_id'], {}).get('key'),
                 'sha256': by_id.get(slot['artifact_id'], {}).get('sha256')} for slot in slots]
    if len(set(actual)) != len(actual) and bindings != expected:
        errors.append('Duplicate actual input without matching frozen slot bindings')
    if bindings is not None or require_bindings:
        if bindings != expected:
            errors.append('Frozen input slots differ from actual order, purpose, role or bytes')
        if any(not isinstance(x['sha256'], str) or not re.fullmatch(r'[0-9a-f]{64}', x['sha256'])
               for x in expected):
            errors.append('Every bound input requires a SHA-256 digest')
    frozen_digest = locks.get('request_sha256')
    if frozen_digest is not None or require_bindings:
        try:
            if frozen_digest != request_digest(saved_request):
                errors.append('Actual request differs from frozen request')
        except (TypeError, ValueError):
            errors.append('Actual request is not canonical JSON')
    target = next((x.get('key') for x in imports if x.get('artifact_id') == source), None)
    # The failed-source exception is only valid after full request/anchor verification.
    return errors, target if not errors else None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('imports', type=Path, help='Actual key/sha256 import manifest')
    parser.add_argument('audit', type=Path, help='Reference provenance and scoped reviews')
    parser.add_argument('--request', type=Path, help='Actual Image tool arguments or saved journal')
    parser.add_argument('--locks', type=Path, help='Project and required fixed references for this shot')
    parser.add_argument('--require-request', action='store_true',
                        help='Require actual request, frozen input slots and request digest for production')
    args = parser.parse_args()
    try:
        imports = json.loads(args.imports.read_text())
        request_errors, target = [], None
        actual_request = None
        if args.require_request and not (args.request and args.locks):
            request_errors = ['Production input check requires --request and --locks']
        elif bool(args.request) != bool(args.locks):
            request_errors = ['--request and --locks must be supplied together']
        elif args.request:
            actual_request = json.loads(args.request.read_text())
            request_errors, target = validate_request(
                imports, actual_request, json.loads(args.locks.read_text()),
                require_bindings=args.require_request)
        result = validate(imports, json.loads(args.audit.read_text()),
                          args.audit.resolve().parent, repair_target_key=target)
        result['errors'].extend(request_errors)
        result['record_check_passed'] = not result['errors']
        result['actual_request_checked'] = bool(args.request and args.locks)
        result['request_check_passed'] = result['actual_request_checked'] and not request_errors
        result['production_input_check_passed'] = bool(args.require_request and result['record_check_passed'])
        if result['production_input_check_passed']:
            result['verified_request_sha256'] = request_digest(actual_request)
    except (OSError, ValueError, AttributeError, TypeError):
        result = {'record_check_passed': False, 'artistic_approval': False,
                  'actual_request_checked': False, 'request_check_passed': False,
                  'production_input_check_passed': False,
                  'errors': ['Unreadable or invalid reference manifest'], 'warnings': []}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result['record_check_passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
