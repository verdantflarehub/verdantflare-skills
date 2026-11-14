"""Validate migrated agent orchestration and meta governance skills in verdantflare-skills."""
from __future__ import annotations

import re
import unittest
from pathlib import Path
from urllib.parse import unquote, urlsplit

import yaml


ROOT = Path(__file__).resolve().parents[1]
SKILLS = (
    "verdantflare-agent-antigravity",
    "verdantflare-agent-claude",
    "verdantflare-agent-codex",
    "verdantflare-agent-grok",
    "verdantflare-meta-googledrive",
    "verdantflare-meta-markdown",
    "verdantflare-meta-skill",
    "verdantflare-meta-agent",
)
LEGACY_PREFIX_PATTERN = re.compile(r"(?:mk-agent-|mk-meta-|mk_agent_|MK_AGENT_|MK_PRETTIER|test_mk_markdown)")


class AgentAndMetaSkillPackageTests(unittest.TestCase):
    def test_metadata_openai_yaml_and_reachable_references(self):
        for name in SKILLS:
            with self.subTest(skill=name):
                folder = ROOT / "skills" / name
                entry = folder / "SKILL.md"
                self.assertTrue(entry.is_file(), f"Missing {entry}")
                parts = entry.read_text(encoding="utf-8").split("---", 2)
                self.assertEqual(len(parts), 3)
                metadata = yaml.safe_load(parts[1])
                self.assertEqual(metadata["name"], name)
                self.assertIsInstance(metadata["description"], str)
                self.assertTrue(metadata["description"].strip())

                ui_file = folder / "agents" / "openai.yaml"
                self.assertTrue(ui_file.is_file(), f"Missing {ui_file}")
                ui = yaml.safe_load(ui_file.read_text(encoding="utf-8"))
                self.assertIn("$" + name, ui["interface"]["default_prompt"])
                self.assertGreaterEqual(len(ui["interface"]["short_description"]), 25)
                self.assertLessEqual(len(ui["interface"]["short_description"]), 64)

                queue, visited = [entry], set()
                while queue:
                    current = queue.pop().resolve()
                    if current in visited:
                        continue
                    visited.add(current)
                    text = current.read_text(encoding="utf-8")
                    lines = text.splitlines()
                    in_fence = False
                    active_lines = []
                    for line in lines:
                        if line.lstrip().startswith(("```", "~~~")):
                            in_fence = not in_fence
                            continue
                        if not in_fence:
                            active_lines.append(re.sub(r"(`+).+?\1", "", line))
                    clean_text = "\n".join(active_lines)
                    for raw in re.findall(r"\]\(([^)]+)\)", clean_text):
                        url = urlsplit(raw.strip().strip("<>"))
                        if url.scheme or not url.path:
                            continue
                        target = (current.parent / unquote(url.path)).resolve()
                        self.assertTrue(target.is_relative_to(folder.resolve()), f"{name}: {raw}")
                        self.assertTrue(target.is_file(), f"{name}: {raw}")
                        if target.suffix == ".md" and "templates" not in target.parts:
                            queue.append(target)
                expected = {p.resolve() for p in (folder / "references").rglob("*.md")}
                self.assertTrue(expected.issubset(visited), f"{name}: undiscoverable reference")

    def test_no_legacy_mk_prefix_remains(self):
        for name in SKILLS:
            folder = ROOT / "skills" / name
            for path in folder.rglob("*"):
                if not path.is_file() or "__pycache__" in path.parts or path.suffix in {".pyc", ".pyo"}:
                    continue
                text = path.read_text(encoding="utf-8")
                match = LEGACY_PREFIX_PATTERN.search(text)
                self.assertIsNone(
                    match,
                    f"Legacy prefix {match.group(0) if match else ''} found in {path.relative_to(ROOT)}",
                )


if __name__ == "__main__":
    unittest.main()
