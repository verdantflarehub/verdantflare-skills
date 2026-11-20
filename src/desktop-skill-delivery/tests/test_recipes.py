"""Check real public projection and local documentation closure without network."""
from pathlib import Path
import posixpath
import re
import sys
import unittest

TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))
import recipes as r


class PublicRecipeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = {n: (TOOL.parents[1] / r.source_key(r.VIDEO, n)).read_bytes()
                      for n in r.VIDEO_FILES}
        cls.output = {n: r.transform(r.VIDEO, n, b) for n, b in cls.source.items()}

    def test_local_references_and_private_instructions(self):
        for n, b in self.output.items():
            if not n.endswith('.md'):
                continue
            text = b.decode()
            with self.subTest(file=n):
                for marker in ('github.com/verdantflarehub', 'install_higgsfield.py',
                               'scripts/higgsfield.py', 'du-0', 'config.py', 'task_store'):
                    self.assertNotIn(marker, text)
                for url in re.findall(r'\]\(([^)]+)\)', text):
                    if url.startswith(('https:', 'http:', '#')):
                        continue
                    target = posixpath.normpath(posixpath.join(posixpath.dirname(n), url.split('#')[0]))
                    self.assertIn(target, self.output, (n, url))

    def test_real_guide_shape_drift_fails(self):
        raw = self.source['SKILL.md']
        for changed in (raw.replace(r.ENV_URL.encode(), b'https://invalid.example'),
                        raw + r.ENV_URL.encode()):
            with self.assertRaisesRegex(ValueError, 'PUBLIC_DOCUMENT_SOURCE_SHAPE_CHANGED'):
                r.transform(r.VIDEO, 'SKILL.md', changed)

    def test_copy_and_recovery_semantics(self):
        for n in self.source:
            if r.transform_name(r.VIDEO, n) == 'copy':
                self.assertEqual(self.source[n], self.output[n])
        self.assertIn('不重新生成', self.output['references/workflow.md'].decode())
        self.assertIn('不能把旧任务转成新的收费任务', self.output['references/sd2-workflow.md'].decode())
        self.assertIn(b'STUDIO_VIDEO_CLIENT_CONTRACT', self.output['scripts/video_client.py'])


if __name__ == '__main__':
    unittest.main()
