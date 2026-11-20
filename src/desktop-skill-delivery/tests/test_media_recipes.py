"""Exercise explicit media document projections; no media or network calls."""
from pathlib import Path
import posixpath
import re
import sys
import unittest

TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))
import media_recipes as m


class MediaRecipeTests(unittest.TestCase):
    def test_public_documents_resolve_local_or_declared_skill_dependencies(self):
        root = TOOL.parents[1]
        deps = {m.IMAGE: {'verdantflare-project', 'verdantflare-artifact'},
                m.MV: {'verdantflare-project', 'verdantflare-artifact', 'verdantflare-image',
                       'verdantflare-video', 'verdantflare-music'}}
        for pkg, names in m.FILES.items():
            for name in names:
                raw = (root / m.source_key(pkg, name)).read_bytes()
                output = m.transform(pkg, name, raw)
                with self.subTest(pkg=pkg, name=name):
                    if m.transform_name(pkg, name) == 'copy':
                        self.assertEqual(raw, output)
                    if not name.endswith('.md'):
                        continue
                    text = output.decode()
                    self.assertNotIn('github.com/verdantflarehub', text)
                    if name == 'references/content-rating-rubric.md':
                        # Bundled full rubric retains its source attribution; no fetch is required.
                        text = text.replace('docs/design/contracts/content-rating-rubric.md', '')
                    self.assertNotIn('docs/design/', text)
                    self.assertNotIn('docs/archive/', text)
                    # Code examples are not Markdown dependencies.
                    text = re.sub(r'`[^`]*`', '', text)
                    for url in re.findall(r'\]\(([^)]+)\)', text):
                        if url.startswith(('https:', 'http:', '#')):
                            continue
                        target = posixpath.normpath(posixpath.join(pkg, posixpath.dirname(name), url.split('#')[0]))
                        owner, relative = target.split('/', 1)
                        if owner == pkg:
                            self.assertIn(relative, names)
                        else:
                            self.assertIn(owner, deps[pkg])
                            self.assertTrue((root / 'skills' / target).is_file())

    def test_source_shape_drift_and_unknown_file_rejected(self):
        for (pkg, name), changes in m.REPLACEMENTS.items():
            raw = (TOOL.parents[1] / m.source_key(pkg, name)).read_bytes()
            before = changes[0][0].encode()
            with self.subTest(pkg=pkg, name=name):
                with self.assertRaisesRegex(ValueError, 'PUBLIC_DOCUMENT_SOURCE_SHAPE_CHANGED'):
                    m.transform(pkg, name, raw.replace(before, b'changed', 1))
        with self.assertRaisesRegex(ValueError, 'PUBLIC_FILE_NOT_ALLOWED'):
            m.source_key(m.IMAGE, 'scripts/admin.py')


if __name__ == '__main__':
    unittest.main()
