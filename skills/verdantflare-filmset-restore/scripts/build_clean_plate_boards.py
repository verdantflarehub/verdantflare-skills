#!/usr/bin/env python3
"""Deterministic board generator for verdantflare-filmset-restore.

Builds:
1. Side-by-side comparison board (source frame / reference vs. restored clean plate).
2. Three-tier occlusion & inference annotation board:
   - Cyan (#00e5ff): V1..Vn directly visible in primary base frame.
   - Green (#00e676): R1..Rn revealed in adjacent frames or co-edge master plates.
   - Orange (#ff9100): M1..Mn occluded across all frames / inferred by model or topology.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont


COLOR_MAP = {
    "V": ("#00e5ff", None),
    "R": ("#00e676", (0, 230, 118, 34)),
    "M": ("#ff9100", (255, 145, 0, 38)),
}


def _load_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = (
        ["C:/Windows/Fonts/msyhbd.ttc", "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"]
        if bold
        else ["C:/Windows/Fonts/msyh.ttc", "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"]
    )
    for path in candidates:
        if Path(path).is_file():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def _trim_black_bars(
    im: Image.Image,
    luma_threshold: int = 12,
    max_trim_ratio: float = 0.18,
) -> Image.Image:
    """Detect and crop top/bottom/left/right letterbox black bars before canvas scaling."""
    gray = im.convert("L")
    w, h = gray.size
    if w < 16 or h < 16:
        return im

    px = gray.load()
    x_start = int(w * 0.10)
    x_end = max(x_start + 1, int(w * 0.90))
    span_w = x_end - x_start
    max_h_trim = int(h * max_trim_ratio)

    def _row_is_black(y: int) -> bool:
        total = sum(px[x, y] for x in range(x_start, x_end))
        return (total / span_w) <= luma_threshold

    top = 0
    while top < max_h_trim and _row_is_black(top):
        top += 1

    bottom = h
    while (h - bottom) < max_h_trim and bottom > top + 1 and _row_is_black(bottom - 1):
        bottom -= 1

    y_start = int(h * 0.10)
    y_end = max(y_start + 1, int(h * 0.90))
    span_h = y_end - y_start
    max_w_trim = int(w * max_trim_ratio)

    def _col_is_black(x: int) -> bool:
        total = sum(px[x, y] for y in range(y_start, y_end))
        return (total / span_h) <= luma_threshold

    left = 0
    while left < max_w_trim and _col_is_black(left):
        left += 1

    right = w
    while (w - right) < max_w_trim and right > left + 1 and _col_is_black(right - 1):
        right -= 1

    if (top > 0 or bottom < h or left > 0 or right < w) and (right - left >= w * 0.5) and (bottom - top >= h * 0.5):
        return im.crop((left, top, right, bottom))
    return im


def _boxes_overlap(a: tuple[int, int, int, int], b: tuple[int, int, int, int], pad: int = 2) -> bool:
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    return not (ax2 + pad <= bx1 or bx2 + pad <= ax1 or ay2 + pad <= by1 or by2 + pad <= ay1)


def _draw_labeled_box(
    draw: ImageDraw.ImageDraw,
    box: list[int],
    color: str,
    label: str,
    tag_xy: list[int],
    font: ImageFont.ImageFont,
    canvas_size: tuple[int, int] | None = None,
    placed_tags: list[tuple[int, int, int, int]] | None = None,
) -> tuple[int, int, int, int]:
    x1, y1, x2, y2 = box
    draw.rectangle([x1, y1, x2, y2], outline=color, width=4)
    tx, ty = int(tag_xy[0]), int(tag_xy[1])
    bbox = draw.textbbox((0, 0), label, font=font)
    tw = bbox[2] - bbox[0] + 12
    th = bbox[3] - bbox[1] + 8

    if canvas_size is not None:
        cw, ch = canvas_size
        tx = max(4, min(tx, cw - tw - 4))
        ty = max(4, min(ty, ch - th - 4))

    if placed_tags is not None:
        candidate = (tx, ty, tx + tw, ty + th)
        guard = 0
        while guard < 64:
            conflict = next((other for other in placed_tags if _boxes_overlap(candidate, other, pad=2)), None)
            if conflict is None:
                break
            ty = conflict[3] + 4
            if canvas_size is not None and ty + th > canvas_size[1] - 4:
                ty = max(4, conflict[1] - th - 4)
            candidate = (tx, ty, tx + tw, ty + th)
            guard += 1
        placed_tags.append(candidate)
    else:
        candidate = (tx, ty, tx + tw, ty + th)

    draw.rectangle([tx, ty, tx + tw, ty + th], fill="#111318", outline=color, width=2)
    draw.text((tx + 6, ty + 2), label, fill=color, font=font)
    return candidate


def render_boards(spec_path: Path) -> dict[str, object]:
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    width, height = spec.get("canvas_size", [1440, 1080])
    auto_trim = bool(spec.get("auto_trim_black_bars", True))
    luma_threshold = int(spec.get("black_bar_luma_threshold", 12))

    raw_source = Image.open(spec["source_image"]).convert("RGB")
    raw_clean = Image.open(spec["clean_image"]).convert("RGB")
    if auto_trim:
        raw_source = _trim_black_bars(raw_source, luma_threshold=luma_threshold)
        raw_clean = _trim_black_bars(raw_clean, luma_threshold=luma_threshold)

    source_im = raw_source.resize((width, height), Image.Resampling.LANCZOS)
    clean_im = raw_clean.resize((width, height), Image.Resampling.LANCZOS)

    font_xs = _load_font(17, bold=False)
    font_sm = _load_font(20, bold=False)
    font_lg = _load_font(28, bold=True)

    comp = Image.new("RGB", (width * 2 + 30, height + 120), "#16191f")
    d_comp = ImageDraw.Draw(comp)
    d_comp.text((20, 16), spec["comparison_title"], font=font_lg, fill="#ffffff")
    d_comp.text((20, 56), spec["comparison_subtitle"], font=font_sm, fill="#cfd6e6")
    comp.paste(source_im, (20, 96))
    comp.paste(clean_im, (width + 10, 96))
    d_comp.rectangle([20, 96, 20 + width, 96 + height], outline="#4a5568", width=2)
    d_comp.rectangle([width + 10, 96, width * 2 + 10, 96 + height], outline="#4a5568", width=2)
    comp_out = Path(spec["comparison_output"])
    comp_out.parent.mkdir(parents=True, exist_ok=True)
    comp.save(comp_out)

    ann = Image.new("RGB", (width + 40, height + 165), "#16191f")
    d_ann = ImageDraw.Draw(ann)
    d_ann.text((20, 14), spec["annotation_title"], font=font_lg, fill="#ffffff")
    d_ann.text((20, 52), spec["annotation_subtitle"], font=font_xs, fill="#e2e8f0")

    overlay = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    d_ov = ImageDraw.Draw(overlay)
    for region in spec.get("regions", []):
        tier = region["tier"]
        _, fill_rgba = COLOR_MAP[tier]
        if fill_rgba is not None:
            d_ov.rectangle(region["box"], fill=fill_rgba)

    marked = Image.alpha_composite(clean_im.convert("RGBA"), overlay).convert("RGB")
    d_mk = ImageDraw.Draw(marked)
    placed_tags: list[tuple[int, int, int, int]] = []
    for region in spec.get("regions", []):
        color, _ = COLOR_MAP[region["tier"]]
        tag_xy = region.get("tag_xy", [region["box"][0] + 8, region["box"][1] + 8])
        _draw_labeled_box(
            d_mk,
            region["box"],
            color,
            region["label"],
            tag_xy,
            font_xs,
            canvas_size=(width, height),
            placed_tags=placed_tags,
        )

    ann.paste(marked, (20, 90))
    footer_lines = spec.get("footer_lines", [])
    for idx, line in enumerate(footer_lines[:2]):
        d_ann.text((20, height + 102 + idx * 28), line, font=font_xs, fill="#cbd5e1")
    ann_out = Path(spec["annotation_output"])
    ann_out.parent.mkdir(parents=True, exist_ok=True)
    ann.save(ann_out)
    return {
        "comparison_output": str(comp_out),
        "annotation_output": str(ann_out),
        "placed_tags": [list(t) for t in placed_tags],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Build clean-plate comparison and 3-tier occlusion annotation boards.")
    parser.add_argument("spec_json", type=Path, help="Path to JSON board specification file.")
    args = parser.parse_args()
    out = render_boards(args.spec_json)
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
