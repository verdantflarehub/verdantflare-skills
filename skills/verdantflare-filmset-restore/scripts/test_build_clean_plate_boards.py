#!/usr/bin/env python3
"""Minimal unit tests for build_clean_plate_boards.py."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from PIL import Image

from build_clean_plate_boards import _boxes_overlap, _trim_black_bars, render_boards


class TestBuildCleanPlateBoards(unittest.TestCase):
    def test_trim_black_bars(self) -> None:
        im = Image.new("RGB", (800, 450), "#000000")
        body = Image.new("RGB", (800, 390), "#3a6ea5")
        im.paste(body, (0, 30))
        trimmed = _trim_black_bars(im, luma_threshold=12)
        self.assertEqual(trimmed.size, (800, 390))

    def test_label_right_clamp_and_collision_shift(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            src = tmp / "src.png"
            clean = tmp / "clean.png"
            Image.new("RGB", (1600, 800), "#2b303c").save(src)
            Image.new("RGB", (1600, 800), "#3b4252").save(clean)

            comp_out = tmp / "comp.png"
            ann_out = tmp / "ann.png"
            spec_path = tmp / "spec.json"
            spec = {
                "canvas_size": [1600, 800],
                "auto_trim_black_bars": True,
                "source_image": str(src),
                "clean_image": str(clean),
                "comparison_title": "Test Comparison",
                "comparison_subtitle": "Subtitle",
                "comparison_output": str(comp_out),
                "annotation_title": "Test Annotation",
                "annotation_subtitle": "Subtitle",
                "annotation_output": str(ann_out),
                "regions": [
                    {
                        "tier": "V",
                        "label": "V1 右侧边缘超长标签边界钳制测试",
                        "box": [1200, 20, 1590, 260],
                        "tag_xy": [1520, 24],
                    },
                    {
                        "tier": "R",
                        "label": "R1 同坐标垂直碰撞自动下移测试",
                        "box": [1220, 30, 1580, 280],
                        "tag_xy": [1520, 24],
                    },
                ],
                "footer_lines": ["Footer 1", "Footer 2"],
            }
            spec_path.write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
            result = render_boards(spec_path)
            self.assertTrue(comp_out.is_file())
            self.assertTrue(ann_out.is_file())

            placed = [tuple(x) for x in result["placed_tags"]]
            self.assertEqual(len(placed), 2)
            tag1, tag2 = placed[0], placed[1]
            self.assertGreaterEqual(tag1[0], 4)
            self.assertLessEqual(tag1[2], 1596)
            self.assertGreaterEqual(tag2[0], 4)
            self.assertLessEqual(tag2[2], 1596)
            self.assertFalse(_boxes_overlap(tag1, tag2, pad=2))
            self.assertGreaterEqual(tag2[1], tag1[3] + 4)


if __name__ == "__main__":
    unittest.main()
