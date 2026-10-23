"""Validate K09 post-generation content rating steps, rubric v1.0 sync, and moss-image notes across media skills."""

import re
import unittest
from pathlib import Path
from urllib.parse import unquote, urlsplit


ROOT = Path(__file__).resolve().parents[1]
MEDIA_SKILLS = {
    "verdantflare-image": "image.rate_content",
    "verdantflare-speech": "speech.rate_content",
    "verdantflare-music": "music.rate_content",
    "verdantflare-video": "video.rate_content",
    "verdantflare-music-mv": ".rate_content",
    "x-verdantflare-short-drama": ".rate_content",
}


class ContentRatingSkillTests(unittest.TestCase):
    def test_media_skills_have_consistent_rubric_and_step_references(self):
        rubric_texts = {}
        for skill_name, rate_tool in MEDIA_SKILLS.items():
            with self.subTest(skill=skill_name):
                skill_dir = ROOT / "skills" / skill_name
                skill_md = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
                rubric_path = skill_dir / "references" / "content-rating-rubric.md"
                step_path = skill_dir / "references" / "content-rating-step.md"

                self.assertTrue(rubric_path.is_file(), f"Missing {rubric_path}")
                self.assertTrue(step_path.is_file(), f"Missing {step_path}")

                self.assertIn("references/content-rating-rubric.md", skill_md)
                self.assertIn("references/content-rating-step.md", skill_md)
                self.assertIn(rate_tool, skill_md)

                rubric_text = rubric_path.read_text(encoding="utf-8")
                step_text = step_path.read_text(encoding="utf-8")
                self.assertIn("rubric v1.0", rubric_text.splitlines()[2])
                self.assertIn("S1 露骨性内容", rubric_text)
                self.assertIn("V1 极端血腥暴力", rubric_text)
                self.assertIn("不确定即标 nsfw", rubric_text)
                self.assertIn("rubric v1.0", step_text)
                self.assertIn(rate_tool, step_text)
                self.assertIn("不同步到 Studio 项目", step_text)
                rubric_texts[skill_name] = rubric_text

                for doc_path in (rubric_path, step_path):
                    text = doc_path.read_text(encoding="utf-8")
                    for raw in re.findall(r"\]\(([^)]+)\)", text):
                        url = urlsplit(raw)
                        if url.scheme or not url.path:
                            continue
                        target = (doc_path.parent / unquote(url.path)).resolve()
                        self.assertTrue(target.exists(), f"Broken link {raw} in {doc_path}")

        self.assertEqual(len(set(rubric_texts.values())), 1, "All skill rubric copies must be identical")

    def test_video_and_audio_specific_k09_requirements(self):
        video_step = (ROOT / "skills" / "verdantflare-video" / "references" / "content-rating-step.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("至少每 2 秒一帧", video_step)
        self.assertIn("不少于 8 帧", video_step)
        self.assertIn("镜头切换处的关键帧", video_step)

        for audio_skill in ("verdantflare-speech", "verdantflare-music"):
            step_text = (ROOT / "skills" / audio_skill / "references" / "content-rating-step.md").read_text(
                encoding="utf-8"
            )
            self.assertIn("未转写音频", step_text)

    def test_image_skill_includes_moss_image_engine_notes(self):
        image_dir = ROOT / "skills" / "verdantflare-image"
        skill_md = (image_dir / "SKILL.md").read_text(encoding="utf-8")
        moss_ref = image_dir / "references" / "moss-image.md"
        self.assertTrue(moss_ref.is_file())
        self.assertIn("moss-image", skill_md)
        self.assertIn("references/moss-image.md", skill_md)
        moss_text = moss_ref.read_text(encoding="utf-8")
        self.assertIn("abs_centralized", moss_text)
        self.assertIn("yxyx", moss_text)
        self.assertIn("gpu-task-moss-image", moss_text)
        self.assertIn("image.rate_content", moss_text)


if __name__ == "__main__":
    unittest.main()
