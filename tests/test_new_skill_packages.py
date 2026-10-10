import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKILLS = ("verdantflare-short-drama",)


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
            # Nested stage guides resolve links relative to themselves, not the router.
            for document in folder.rglob("*.md"):
                text = document.read_text(encoding="utf-8")
                for target in re.findall(r"\]\(([^)#]+)", text):
                    if target.startswith(("http://", "https://")):
                        continue
                    self.assertTrue((document.parent / target).resolve().exists(),
                                    f"{document.relative_to(ROOT)}: {target}")

    def test_short_drama_has_one_discoverable_entry(self):
        # A nested SKILL.md would recreate duplicate discovery after consolidation.
        root = ROOT / "skills"
        entries = [p.relative_to(root).as_posix() for p in root.rglob("SKILL.md")
                   if "short-drama" in p.relative_to(root).as_posix()]
        self.assertEqual(entries, ["verdantflare-short-drama/SKILL.md"])


if __name__ == "__main__":
    unittest.main()
