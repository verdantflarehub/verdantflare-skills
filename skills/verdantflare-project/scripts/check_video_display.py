"""Read-only consumer-slot audit for the existing Project video category."""
import argparse
import json
from pathlib import Path

SUPPORTED = {
    'image': {'image/png', 'image/jpeg', 'image/webp', 'image/gif'},
    'video': {'video/mp4', 'video/webm', 'video/ogg'},
}


def audit(manifest, content, versions, *, require_output=False, require_covers=False,
          expect_units=(), expect_cast=()):
    manifest = manifest.get('manifest', manifest)
    errors = []
    if manifest.get('category') != 'video' or content.get('category') != 'video':
        return ['category: this audit supports existing video projects only']
    files = {f['file_id']: f for f in manifest['files']}
    video = content.get('video') or {}

    def check(value, location, kind=None, required=False):
        if not value:
            if required:
                errors.append(f'{location}: missing display reference')
            return
        if value not in files:
            errors.append(f'{location}: file absent from this revision')
            return
        if kind:
            version = versions.get(value)
            if not version:
                errors.append(f'{location}: actual Artifact metadata not verified')
            elif any(version.get(k) != v for k, v in files[value]['content_ref'].items()):
                errors.append(f'{location}: metadata is for a different ContentRef')
            elif version.get('mime', '').split(';')[0].strip().lower() not in SUPPORTED[kind]:
                errors.append(f'{location}: MIME is incompatible with {kind} preview')

    check(content.get('cover_file_id'), 'cover_file_id', 'image')
    check(video.get('output_file_id'), 'video.output_file_id', 'video', require_output)
    boards = video.get('storyboards') or []
    cast = video.get('cast') or []
    for label, entries, expected in [('storyboards', boards, expect_units), ('cast', cast, expect_cast)]:
        keys = [e['key'] for e in entries]
        if len(keys) != len(set(keys)):
            errors.append(f'video.{label}: duplicate object key')
        for key in expected:
            if key not in keys:
                errors.append(f'video.{label}.{key}: expected object missing')
    for b in boards:
        root = f"video.storyboards.{b['key']}"
        check(b.get('image_file_id'), root+'.image_file_id', 'image', require_covers)
        for field, kind in [('reference_file_ids', 'image'), ('result_file_ids', 'video'), ('file_ids', None)]:
            for i, value in enumerate(b.get(field) or []):
                check(value, f'{root}.{field}[{i}]', kind)
    for c in cast:
        check(c.get('portrait_file_id'), f"video.cast.{c['key']}.portrait_file_id", 'image', c['key'] in expect_cast)
    return errors


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ['manifest', 'content', 'versions']:
        p.add_argument('--'+name, required=True, type=Path)
    p.add_argument('--require-output', action='store_true')
    p.add_argument('--require-covers', action='store_true')
    p.add_argument('--expect-unit', action='append', default=[])
    p.add_argument('--expect-cast', action='append', default=[])
    args = p.parse_args()
    errors = audit(*(json.loads(getattr(args, n).read_text(encoding='utf-8')) for n in ['manifest','content','versions']),
                   require_output=args.require_output, require_covers=args.require_covers,
                   expect_units=args.expect_unit, expect_cast=args.expect_cast)
    print(json.dumps({'ok': not errors, 'errors': errors}, ensure_ascii=False, indent=2))
    return int(bool(errors))


if __name__ == '__main__':
    raise SystemExit(main())
