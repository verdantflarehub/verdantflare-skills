#!/usr/bin/env python3
"""Render original/look/candidate crops without stretching or granting approval.

Requires Pillow. Regions are manually observed normalized [x0,y0,x1,y1].
This tool performs no face detection, face correction, or identity scoring.
"""
import argparse
import hashlib
import json
from pathlib import Path
from PIL import Image, ImageDraw, ImageOps


def crop_source(base, spec, region):
    path = base / spec['path']
    if hashlib.sha256(path.read_bytes()).hexdigest() != spec['sha256']:
        raise ValueError('Source digest changed')
    if (not isinstance(region, list) or len(region) != 4
            or any(type(v) not in (int, float) for v in region)
            or not (0 <= region[0] < region[2] <= 1 and 0 <= region[1] < region[3] <= 1)):
        raise ValueError('Region must be nonempty normalized coordinates')
    with Image.open(path) as opened:
        im = ImageOps.exif_transpose(opened).convert('RGB')
        box = tuple(round(v * (im.width if i % 2 == 0 else im.height))
                    for i, v in enumerate(region))
        if box[2] <= box[0] or box[3] <= box[1]:
            raise ValueError('Region has no source pixels')
        return im.crop(box)


def build(report, base):
    actors = report['actors']
    if not actors:
        raise ValueError('At least one actor required')
    roles = ['original_identity', 'approved_look', 'candidate']
    canvas = Image.new('RGB', (960, 410 * len(actors)), '#252525')
    draw = ImageDraw.Draw(canvas)
    for row, (actor, refs) in enumerate(actors.items()):
        regions = report['comparison_regions'][actor]
        for col, role in enumerate(roles):
            spec = report['candidate'] if role == 'candidate' else refs[role]
            crop = crop_source(base, spec, regions[role])
            tile = ImageOps.contain(crop, (312, 370))
            x = col * 320 + (320 - tile.width) // 2
            y = row * 410 + 30 + (370 - tile.height) // 2
            canvas.paste(tile, (x, y))
            draw.text((col * 320 + 6, row * 410 + 8), actor + ' / ' + role, fill='white')
    return canvas


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('report', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    if args.output.exists():
        parser.error('Output exists; retain prior evidence or explicitly remove only a temporary file')
    report = json.loads(args.report.read_text())
    image = build(report, args.report.parent)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    image.save(args.output)
    print('Comparison rendered; no face score or approval produced.')


if __name__ == '__main__':
    main()
