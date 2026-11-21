#!/usr/bin/env python3
"""Unit tests for prompt_lint.py."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import prompt_lint  # noqa: E402


VALID_C1_PROMPT = """【人物形象】
一位23岁的成年东方女性，身材修长匀称，气质温柔知性。皮肤白皙通透，保留真实皮肤纹理、自然毛孔与细腻光泽，不要假面感。

【发型与妆容】
深黑色长发盘成优雅低发髻，发髻插一支温润青玉发簪。清透淡雅民国妆容，唇色为柔和玫瑰豆沙色。

【完整服装搭配】
主服装为淡青瓷绿色真丝民国改良旗袍，立领盘扣，白色兰花刺绣；外搭月白色薄款丝绸短披肩，下穿淡灰青色长袜与月白色绣花高跟鞋，手持浅米白色油纸伞。

【场景环境】
江南古镇青石板街道，背景有白墙黛瓦、古朴石桥、临水木窗与轻柔烟雨。

【人物姿态】
自然站立在青石桥旁，一手握油纸伞柄，另一手轻扶伞柄下方，面向镜头温柔从容。

【摄影构图】
9:16竖版全身摄影，人物从头顶到鞋底完整呈现，居中偏前景，平视机位，保留适当环境空间。

【画面质感】
真实高级民国时装摄影，85mm镜头，柔和阴天漫射光，真丝纹理与皮肤质感真实可见。

【严格要求】
完整全身入镜，头部、双腿、鞋子全部保留，不得裁切，不要半身照，不要过度浓妆，不要现代元素，不要塑料皮肤，不要拼图，不要文字，不要额外水印。
"""

VALID_C2_PROMPT = """【空间定位】
民国时期江南古镇临水街巷室外空景，清雅静谧。

【主体建筑】
白墙黛瓦双层木结构民居与单孔青石拱桥，灰瓦屋檐略有风化青苔痕迹。

【陈设与材质】
青石板湿润铺装、深褐色木质窗格、岸边石栏杆与几丛翠竹绿植。

【空间层次】
前景为湿润青石板阶梯，中景为石桥与临水窗台，远景为烟雨朦胧的河道与屋脊。

【光影氛围】
柔和阴天漫射光，整体冷青灰色调，水面带有柔和倒影与轻薄水雾。

【摄影构图】
16:9横版中远景建筑摄影，平视机位，垂直线条横平竖直，35mm镜头大景深。

【严格要求】
严格无人空景，不要出现人物、行人、剪影或手部入镜，不要现代元素，不要拼图，不要文字，不要额外水印。
"""

VALID_C3_PROMPT = """【人物形象】
主体人物A（左侧）：28岁成年东方男性，轮廓清朗，真实皮肤纹理。主体人物B（右侧）：25岁成年东方女性，眉眼温婉，真实皮肤纹理。

【发型与妆容】
人物A为民国偏分短发；人物B为低盘发髻配素雅玉簪与淡雅豆沙唇妆。

【完整服装搭配】
人物A穿深灰呢料中山装与黑色皮鞋；人物B穿月白色素雅旗袍与浅米色短披肩，手提深棕小皮箱。

【场景环境】
民国时期火车站站台，复古铸铁廊柱、木质长椅与青灰石砖地面，背景有蒸汽列车车厢轮廓。

【人物关系与姿态】
人物A侧身递出车票，人物B微微倾身接过，两人目光温和交汇，前后站位自然错开。

【摄影构图】
16:9横版双人全景剧照，平视中景机位，两位主体位于画面中央三分线，保留站台纵深。

【画面质感】
35mm电影胶片质感，50mm镜头，阴天柔和侧逆光，衣料纹理与皮肤细节清晰真实。

【严格要求】
人物数量严格锁定为2人，不增减人物，服装不互相混淆，不要现代元素，不要塑料皮肤，不要拼图，不要文字，不要额外水印。
"""

VALID_EN_PROMPT = """A 9:16 vertical full-body photograph of a 23-year-old adult East Asian woman with a slender figure, gentle scholarly bearing, natural skin texture, visible pores, and subtle sheen. Her dark hair is styled in a low chignon with a celadon jade hairpin and delicate pearls, paired with soft natural makeup and muted rose lips.

She wears a pale celadon-green silk qipao with white orchid embroidery, a moon-white silk short shawl, pale grey-cyan stockings, moon-white embroidered pointed heels, and holds an off-white oiled-paper umbrella. Standing beside a bluestone bridge in a misty Jiangnan water town with whitewashed walls and dark tiled roofs, shot eye-level with an 85mm lens under soft overcast diffused light.

Strict requirements: Full body from head to shoes strictly in frame, no cropping, no half-body shot, no plastic skin, no modern elements, no collage, no text, no watermark.
"""


class PromptLintTests(unittest.TestCase):
    def test_valid_c1_passes(self) -> None:
        res = prompt_lint.evaluate_prompt(VALID_C1_PROMPT, category="c1")
        self.assertEqual(res["status"], "PASS")
        self.assertEqual(res["aspect_ratio"], "9:16")
        self.assertEqual(len(res["sections"]), 8)
        self.assertEqual(res["errors"], [])

    def test_valid_c2_passes(self) -> None:
        res = prompt_lint.evaluate_prompt(VALID_C2_PROMPT, category="c2")
        self.assertEqual(res["status"], "PASS")
        self.assertEqual(res["aspect_ratio"], "16:9")
        self.assertEqual(len(res["sections"]), 7)

    def test_valid_c3_passes(self) -> None:
        res = prompt_lint.evaluate_prompt(VALID_C3_PROMPT, category="c3")
        self.assertEqual(res["status"], "PASS")
        self.assertEqual(len(res["sections"]), 8)

    def test_missing_section_and_aspect_fails(self) -> None:
        broken = VALID_C1_PROMPT.replace("【人物姿态】", "【错误标题】").replace("9:16", "")
        res = prompt_lint.evaluate_prompt(broken, category="c1")
        self.assertEqual(res["status"], "FAIL")
        joined = "\n".join(res["errors"])
        self.assertIn("RP-SECTION-MISSING", joined)
        self.assertIn("RP-ASPECT-REQ", joined)

    def test_fullbody_without_anti_crop_fails(self) -> None:
        broken = VALID_C1_PROMPT.replace("完整全身入镜，头部、双腿、鞋子全部保留，不得裁切，不要半身照，", "")
        res = prompt_lint.evaluate_prompt(broken, category="c1")
        self.assertEqual(res["status"], "FAIL")
        self.assertTrue(any("RP-FULLBODY-PAIR" in e for e in res["errors"]))

    def test_template_variant_extracts_variables(self) -> None:
        tpl = VALID_C1_PROMPT.replace("淡青瓷绿色真丝民国改良旗袍", "{{主服装}}")
        res_default = prompt_lint.evaluate_prompt(tpl, category="c1", variant="default")
        self.assertEqual(res_default["status"], "FAIL")
        res_tpl = prompt_lint.evaluate_prompt(tpl, category="c1", variant="template")
        self.assertEqual(res_tpl["status"], "PASS")
        self.assertEqual(res_tpl["variables"], ["主服装"])

    def test_watermark_pollution_detected(self) -> None:
        polluted = VALID_C1_PROMPT + "\n画面右下角带有小红书标识。"
        res = prompt_lint.evaluate_prompt(polluted, category="c1")
        self.assertEqual(res["status"], "FAIL")
        self.assertTrue(any("RP-POLLUTION" in e for e in res["errors"]))

    def test_english_variant_passes(self) -> None:
        res = prompt_lint.evaluate_prompt(VALID_EN_PROMPT, category="c1", variant="en")
        self.assertEqual(res["status"], "PASS")
        self.assertEqual(res["aspect_ratio"], "9:16")

    def test_cli_parse_json(self) -> None:
        script = Path(__file__).resolve().parent / "prompt_lint.py"
        with tempfile.TemporaryDirectory() as tmp:
            prompt_file = Path(tmp) / "c1.txt"
            prompt_file.write_text(VALID_C1_PROMPT, encoding="utf-8")
            proc = subprocess.run(
                [sys.executable, "-X", "utf8", str(script), "parse", str(prompt_file), "--category", "c1", "--json"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=False,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            data = json.loads(proc.stdout)
            self.assertEqual(data["status"], "PASS")
            self.assertEqual(data["aspect_ratio"], "9:16")
            self.assertIn("严格要求", data["sections"])


if __name__ == "__main__":
    unittest.main()
