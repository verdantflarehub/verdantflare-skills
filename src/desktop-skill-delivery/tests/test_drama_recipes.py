"""Short-drama public closure, dependency ownership and immutable helpers."""
import ast
from pathlib import Path
import posixpath
import re
import sys
import unittest
from urllib.parse import unquote

TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))
import drama_recipes as d
import blender_recipes as b

DEPENDENCIES = {'verdantflare-project', 'verdantflare-artifact', 'verdantflare-image',
                'verdantflare-video', 'verdantflare-music', 'verdantflare-music-mv',
                'verdantflare-speech', 'verdantflare-blender'}


def anchors(text):
    counts, result = {}, set()
    for line in text.splitlines():
        if not re.match(r'^#{1,6} ', line):
            continue
        slug = re.sub(r'[^\w\- ]', '', re.sub(r'^#+ ', '', line).lower()).replace(' ', '-')
        count = counts.get(slug, 0); counts[slug] = count + 1
        result.add(slug if not count else f'{slug}-{count}')
    return result


class DramaRecipeTests(unittest.TestCase):
    def payload(self):
        return {n: d.transform(n, (TOOL.parents[1] / d.source_key(n)).read_bytes()) for n in d.FILES}

    def test_public_links_anchors_and_explicit_blender_dependency(self):
        payload = self.payload()
        self.assertEqual(len(payload), 41)
        self.assertFalse(any(n.startswith('tests/') for n in payload))
        for name, data in payload.items():
            if not name.endswith('.md'):
                continue
            text = data.decode()
            self.assertNotIn('github.com/verdantflarehub', text)
            # Skip inline code examples, not actual navigation links.
            for url in re.findall(r'\]\(([^)]+)\)', re.sub(r'`[^`]*`', '', text)):
                if url.startswith(('http:', 'https:')):
                    continue
                rel, _, fragment = unquote(url).partition('#')
                target = posixpath.normpath(posixpath.join(d.DRAMA, posixpath.dirname(name), rel)) if rel else d.DRAMA+'/'+name
                owner, relative = target.split('/', 1)
                with self.subTest(name=name, target=target, fragment=fragment):
                    if owner == d.DRAMA:
                        self.assertIn(relative, payload)
                        if fragment:
                            self.assertIn(fragment, anchors(payload[relative].decode()))
                    else:
                        self.assertIn(owner, DEPENDENCIES)
                        if owner == b.BLENDER:
                            self.assertIn(relative, b.FILES)
                        else:
                            self.assertTrue((TOOL.parents[1] / 'skills' / target).is_file())
        self.assertIn('Blender', payload['SKILL.md'].decode())
        self.assertIn('保持未完成', payload['references/production/blender-whitebox.md'].decode())
        self.assertNotIn('## 3. 维护说明', payload['references/migration.md'].decode())

    def test_all_helpers_and_local_schema_stay_exact(self):
        payload = self.payload()
        scripts = {Path(n).stem for n in payload if n.endswith('.py')}
        seen = set()
        self.assertEqual(len(scripts), 6)
        self.assertIn('references/production/generation-preparation.schema.json', payload)
        for name in payload:
            if not name.endswith(('.py', '.json')):
                continue
            raw = (TOOL.parents[1] / d.source_key(name)).read_bytes()
            self.assertEqual(raw, payload[name])
            if name.endswith('.py'):
                for node in ast.walk(ast.parse(raw)):
                    if isinstance(node, ast.Import):
                        seen.update(a.name.split('.')[0] for a in node.names)
                    elif isinstance(node, ast.ImportFrom):
                        self.assertEqual(node.level, 0)
                        seen.add(node.module.split('.')[0])
        self.assertEqual(seen - scripts - sys.stdlib_module_names, {'PIL', 'jsonschema'})

    def test_unknown_file_and_known_reference_drift_fail_closed(self):
        for name, changes in d.REPLACEMENTS.items():
            raw = (TOOL.parents[1] / d.source_key(name)).read_bytes()
            for before, _ in changes:
                with self.subTest(name=name), self.assertRaisesRegex(ValueError, 'PUBLIC_DOCUMENT_SOURCE_SHAPE_CHANGED'):
                    d.transform(name, raw.replace(before.encode(), b'changed', 1))
        with self.assertRaisesRegex(ValueError, 'PUBLIC_FILE_NOT_ALLOWED'):
            d.source_key('tests/test_breakdown.py')


if __name__ == '__main__':
    unittest.main()
