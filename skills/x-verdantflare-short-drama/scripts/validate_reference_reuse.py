#!/usr/bin/env python3
"""Check reference provenance against the actual import manifest, not image quality."""
import argparse
import hashlib
import json
from pathlib import Path


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def validate(imports, audit, base):
    errors, warnings = [], []
    records = audit.get('references', [])
    if not isinstance(imports, list) or not isinstance(records, list):
        return {'errors': ['Reference lists required'], 'warnings': [], 'record_check_passed': False}
    expected = [r.get('key') for r in imports]
    actual = [r.get('key') for r in records]
    if not expected or any(not isinstance(k, str) or not k for k in expected + actual):
        errors.append('Nonempty reference keys required')
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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('imports', type=Path, help='Actual key/sha256 import manifest')
    parser.add_argument('audit', type=Path, help='Reference provenance and scoped reviews')
    args = parser.parse_args()
    try:
        result = validate(json.loads(args.imports.read_text()), json.loads(args.audit.read_text()),
                          args.audit.resolve().parent)
    except (OSError, ValueError, AttributeError, TypeError):
        result = {'record_check_passed': False, 'artistic_approval': False,
                  'errors': ['Unreadable or invalid reference manifest'], 'warnings': []}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result['record_check_passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
