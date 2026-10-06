import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKILLS = (
    "x-verdantflare-short-drama-character-performance",
    "x-verdantflare-short-drama-commercial-production",
    "x-verdantflare-short-drama-director",
    "x-verdantflare-short-drama-film-production",
    "x-verdantflare-short-drama-local-scene-breakdown",
)


class NewSkillPackageTests(unittest.TestCase):
    def test_each_skill_has_installable_metadata(self):
        for name in SKILLS:
            with self.subTest(name=name):
                folder = ROOT / "skills" / name
                skill = (folder / "SKILL.md").read_text(encoding="utf-8")
                self.assertRegex(skill, rf"(?m)^name: {re.escape(name)}$")
                self.assertRegex(skill, r"(?m)^description: .+\S$")
                metadata = (folder / "agents" / "openai.yaml").read_text(encoding="utf-8")
                self.assertRegex(metadata, r"(?m)^  display_name: \".+\"$")
                self.assertRegex(metadata, r"(?m)^  short_description: \".{1,120}\"$")
                self.assertIn(f"${name}", metadata)

    def test_referenced_local_files_exist(self):
        for name in SKILLS:
            folder = ROOT / "skills" / name
            text = (folder / "SKILL.md").read_text(encoding="utf-8")
            for target in re.findall(r"\]\(([^)#]+)", text):
                if target.startswith(("http://", "https://")):
                    continue
                self.assertTrue((folder / target).resolve().exists(), f"{name}: {target}")


if __name__ == "__main__":
    unittest.main()
