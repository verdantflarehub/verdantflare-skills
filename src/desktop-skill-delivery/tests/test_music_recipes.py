"""Check public Music closure and exact scientific script preservation offline."""
import ast
import hashlib
import json
from pathlib import Path
import posixpath
import re
import sys
import unittest
from urllib.parse import unquote

TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))
import music_recipes as m


class MusicRecipeTests(unittest.TestCase):
    def payload(self):
        return {n: m.transform(n, (TOOL.parents[1] / m.source_key(n)).read_bytes()) for n in m.FILES}

    def test_every_local_reference_and_alias_resolves(self):
        payload = self.payload()
        self.assertEqual(len(m.ALIASES), 24)
        self.assertEqual(len(payload), 61)
        for name, raw in payload.items():
            self.assertTrue(name.isascii())
            if not name.endswith('.md'):
                continue
            text = raw.decode()
            self.assertNotIn('github.com/verdantflarehub', text)
            self.assertNotIn('skill-development', text)
            for original in m.ALIASES.values():
                self.assertNotIn(original, text)
                self.assertNotIn(original.replace(' ', '%20'), text)
            for url in re.findall(r'\]\(([^)]+)\)', re.sub(r'`[^`]*`', '', text)):
                if url.startswith(('https:', 'http:', '#')):
                    continue
                target = posixpath.normpath(posixpath.join(posixpath.dirname(name), unquote(url.split('#')[0])))
                with self.subTest(name=name, url=url):
                    self.assertIn(target, payload)

    def test_scientific_scripts_remain_exact_and_local_imports_closed(self):
        payload = self.payload()
        scripts = {Path(n).stem for n in payload if n.endswith('.py')}
        third_party = {'numpy', 'scipy', 'soundfile', 'librosa'}
        seen = set()
        for name in payload:
            if not name.endswith('.py'):
                continue
            original = (TOOL.parents[1] / m.source_key(name)).read_bytes()
            self.assertEqual(payload[name], original)
            for node in ast.walk(ast.parse(original)):
                if isinstance(node, ast.Import):
                    seen.update(a.name.split('.')[0] for a in node.names)
                elif isinstance(node, ast.ImportFrom):
                    self.assertEqual(node.level, 0)
                    seen.add(node.module.split('.')[0])
        self.assertEqual(seen - scripts - sys.stdlib_module_names, third_party)
        self.assertNotIn('scripts/load_env.py', payload)

    def test_fixed_snapshot_provenance_and_drift_rejection(self):
        provenance = json.loads((TOOL / 'public-docs/music-snapshots/source-provenance.json').read_text())
        self.assertIn('cce3dc47f0954bbce9c83ebc9c4a3a1aba81697f', json.dumps(provenance))
        for name, digest in m.PINNED.items():
            original = (TOOL.parents[1] / m.source_key(name)).read_bytes()
            self.assertEqual(hashlib.sha256(original).hexdigest(), digest)
            self.assertIn(digest, json.dumps(provenance))
            with self.assertRaisesRegex(ValueError, 'PINNED_SNAPSHOT_CHANGED'):
                m.transform(name, original + b'\n')
        raw = (TOOL.parents[1] / m.source_key('SKILL.md')).read_bytes()
        with self.assertRaisesRegex(ValueError, 'PUBLIC_DOCUMENT_SOURCE_SHAPE_CHANGED'):
            m.transform('SKILL.md', raw.replace(m.PROJECT_URL.encode(), b'changed'))
        for name in ('references/skill-development.md', 'scripts/load_env.py', '../SKILL.md'):
            with self.assertRaisesRegex(ValueError, 'PUBLIC_FILE_NOT_ALLOWED'):
                m.source_key(name)


if __name__ == '__main__':
    unittest.main()
