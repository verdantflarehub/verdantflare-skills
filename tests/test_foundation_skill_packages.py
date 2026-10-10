"""Validate foundation skill metadata and self-contained references from source."""
import re
import unittest
from pathlib import Path
from urllib.parse import unquote, urlsplit

import yaml


ROOT = Path(__file__).resolve().parents[1]
SKILLS = ('verdantflare-project', 'verdantflare-artifact', 'verdantflare-blender')


class FoundationSkillPackageTests(unittest.TestCase):
    def test_source_metadata_and_reachable_resources(self):
        for name in SKILLS:
            with self.subTest(skill=name):
                folder = ROOT / 'skills' / name
                entry = folder / 'SKILL.md'
                parts = entry.read_text(encoding='utf-8').split('---', 2)
                self.assertEqual(len(parts), 3)
                metadata = yaml.safe_load(parts[1])
                self.assertEqual(metadata['name'], name)
                self.assertIsInstance(metadata['description'], str)
                self.assertTrue(metadata['description'].strip())
                ui = yaml.safe_load((folder / 'agents/openai.yaml').read_text(encoding='utf-8'))
                self.assertIn('$' + name, ui['interface']['default_prompt'])
                self.assertGreaterEqual(len(ui['interface']['short_description']), 25)
                self.assertLessEqual(len(ui['interface']['short_description']), 64)
                self.assertIsNot(ui.get('policy', {}).get('allow_implicit_invocation'), False)

                queue, visited = [entry], set()
                while queue:
                    current = queue.pop().resolve()
                    if current in visited:
                        continue
                    visited.add(current)
                    text = current.read_text(encoding='utf-8')
                    for raw in re.findall(r'\]\(([^)]+)\)', text):
                        url = urlsplit(raw)
                        if url.scheme or not url.path:
                            continue
                        target = (current.parent / unquote(url.path)).resolve()
                        self.assertTrue(target.is_relative_to(folder.resolve()), raw)
                        self.assertTrue(target.is_file(), raw)
                        if target.suffix == '.md':
                            queue.append(target)
                expected = {p.resolve() for p in (folder / 'references').rglob('*.md')}
                self.assertTrue(expected.issubset(visited), 'Undiscoverable reference')


if __name__ == '__main__':
    unittest.main()
