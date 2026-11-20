#!/usr/bin/env python3
"""Validate output-review evidence, never infer visual quality from records."""
import argparse
import hashlib
import json
from pathlib import Path


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate(report, base):
    errors = []

    def require(condition, message):
        if not condition:
            errors.append(message)

    def file(spec, label):
        try:
            if not isinstance(spec, dict) or not isinstance(spec.get('path'), str):
                raise TypeError('File specification required')
            path = base / spec['path']
            require(sha(path) == spec['sha256'], label + ': stale digest')
            return path
        except (OSError, KeyError, TypeError):
            errors.append(label + ': missing file or specification')
            return None

    def evidence(specs, label):
        require(isinstance(specs, list) and bool(specs), label + ': visual evidence missing')
        if isinstance(specs, list):
            for spec in specs:
                file(spec, label)

    if not isinstance(report, dict):
        return result(['Review object required'])
    for flag in ('contact_shot', 'join_required'):
        require(type(report.get(flag)) is bool, flag + ': explicit boolean required')
    candidate = file(report.get('candidate'), 'candidate')
    request = file(report.get('request'), 'actual request')
    current = file(report.get('current_review'), 'current review')
    if current:
        try:
            review = json.loads(current.read_text())
            status = review.get('status', review.get('decision'))
            bound = review.get('candidate_sha256', review.get('sha256', review.get('source_sha256')))
            require(candidate is not None and bound == sha(candidate),
                    'Current review is not bound to candidate bytes')
            require(request is not None and review.get('request_sha256') == sha(request),
                    'Current review is not bound to actual request file')
            require(status in {'candidate', 'in_review', 'pending_user_review',
                               'approved', 'approved_static_only', 'approved_video'},
                    'Current review rejects, withholds or does not identify candidate status')
            # User approval of other scopes cannot override a rejection here.
            require(not review.get('withdrawn_self_review'), 'Previous self-review withdrawn')
        except (OSError, ValueError, AttributeError, TypeError):
            errors.append('Current review is not a valid object')
    actors = report.get('actors')
    require(isinstance(actors, dict) and bool(actors), 'Actor baseline mapping required')
    if not isinstance(actors, dict):
        actors = {}
    required = {'space', 'props'}
    for actor, refs in actors.items():
        if not isinstance(actor, str) or not actor or '/' in actor or not isinstance(refs, dict):
            errors.append('Valid actor key and baseline object required')
            continue
        original = file(refs.get('original_identity'), actor + ' original identity')
        file(refs.get('approved_look'), actor + ' approved look')
        if candidate and original:
            require(sha(candidate) != sha(original), actor + ': candidate used as original identity')
        required.update({actor + '/identity', actor + '/wardrobe'})
        if report.get('contact_shot') is True:
            required.update({actor + '/left_arm', actor + '/right_arm'})
    require(report.get('media_kind') in {'image', 'video'}, 'media_kind must be image or video')
    if report.get('media_kind') == 'video':
        required.add('action')
        require(report.get('playback_reviewed') is True, 'Normal-speed playback not reviewed')
    if report.get('join_required') is True:
        required.add('join')
    checks = report.get('checks', {})
    if not isinstance(checks, dict):
        checks = {}
    for name in sorted(required):
        check = checks.get(name, {})
        if not isinstance(check, dict):
            check = {}
        require(check.get('outcome') == 'pass', name + ': failed, unknown or missing judgment')
        require(isinstance(check.get('observation'), str) and bool(check['observation'].strip()),
                name + ': specific observation missing')
        evidence(check.get('evidence'), name)
        if name.endswith('/identity'):
            comparison = check.get('comparison', {})
            refs = actors.get(name.split('/')[0], {})
            if not isinstance(refs, dict):
                refs = {}
            for role in ['original_identity', 'approved_look', 'candidate']:
                spec = comparison.get(role) if isinstance(comparison, dict) else None
                file(spec, name + '/' + role)
                expected = report.get('candidate') if role == 'candidate' else refs.get(role)
                require(isinstance(spec, dict) and isinstance(expected, dict) and
                        spec.get('sha256') == expected.get('sha256'),
                        name + ': comparison differs from ' + role)
        if name.endswith(('/left_arm', '/right_arm')):
            chain = check.get('chain', {})
            require(isinstance(chain, dict) and set(chain) == {'shoulder', 'elbow', 'wrist', 'hand'},
                    name + ': full shoulder-elbow-wrist-hand trace missing')
            if isinstance(chain, dict):
                require(all(isinstance(v, str) and bool(v.strip()) for v in chain.values()),
                        name + ': unobserved chain segment')
        if report.get('media_kind') == 'video':
            frames = check.get('source_frames')
            require(isinstance(frames, list) and bool(frames) and
                    all(type(f) is int and f >= 0 for f in frames),
                    name + ': source-frame evidence missing')
    return result(errors)


def result(errors):
    return {'evidence_complete': not errors, 'visual_quality_verified': False,
            'user_approved': False, 'errors': errors,
            'limit': 'Checks hashes and recorded evidence only; does not recognize faces, '
                     'judge anatomy, verify reviewer honesty, or grant creative approval.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('report', type=Path)
    args = parser.parse_args()
    try:
        output = validate(json.loads(args.report.read_text()), args.report.parent)
    except (OSError, ValueError, TypeError) as exc:
        output = result(['Cannot load review: ' + type(exc).__name__])
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0 if output['evidence_complete'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
