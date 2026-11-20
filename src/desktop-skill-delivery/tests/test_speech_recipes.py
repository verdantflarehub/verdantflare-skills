"""Public Speech closure and server-only experimental boundary, without inference."""
from pathlib import Path
import posixpath
import re
import subprocess
import sys
import tempfile
import unittest

TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))
import speech_recipes as s


class SpeechRecipeTests(unittest.TestCase):
    def test_links_close_and_developer_execution_is_not_distributed(self):
        for name in s.FILES:
            raw = (TOOL.parents[1] / s.source_key(name)).read_bytes()
            output = s.transform(name, raw)
            if s.transform_name(name) == 'copy':
                self.assertEqual(output, raw)
            if not name.endswith('.md'):
                continue
            text = output.decode()
            self.assertNotIn('github.com/verdantflarehub', text)
            self.assertNotIn('5090', text)
            self.assertNotIn('scripts/morph_voice_reference.py', text)
            for url in re.findall(r'\]\(([^)]+)\)', re.sub(r'`[^`]*`', '', text)):
                if url.startswith(('http:', 'https:', '#')):
                    continue
                target = posixpath.normpath(posixpath.join(s.SPEECH, posixpath.dirname(name), url.split('#')[0]))
                owner, relative = target.split('/', 1)
                with self.subTest(name=name, target=target):
                    if owner == s.SPEECH:
                        self.assertIn(relative, s.FILES)
                    else:
                        self.assertIn(owner, ('verdantflare-project', 'verdantflare-artifact'))
                        self.assertTrue((TOOL.parents[1] / 'skills' / target).is_file())
        morph = s.transform('references/voice-morph.md', (TOOL.parents[1] / s.source_key('references/voice-morph.md')).read_bytes()).decode()
        self.assertIn('未提供公开声谱融合 MCP', morph)
        self.assertIn('生成保持未完成', morph)
        self.assertIn('不因此删减适配计划', morph)
        self.assertNotIn('scripts/morph_voice_reference.py', s.FILES)

    def test_source_shape_changes_and_service_script_selection_fail(self):
        for name, changes in s.REPLACEMENTS.items():
            raw = (TOOL.parents[1] / s.source_key(name)).read_bytes()
            for before, _ in changes:
                with self.subTest(name=name, before=before[:10]), self.assertRaisesRegex(ValueError, 'PUBLIC_DOCUMENT_SOURCE_SHAPE_CHANGED'):
                    s.transform(name, raw.replace(before.encode(), b'changed', 1))
        name = 'references/voice-morph.md'
        raw = (TOOL.parents[1] / s.source_key(name)).read_bytes()
        with self.assertRaisesRegex(ValueError, 'PUBLIC_DOCUMENT_SOURCE_SHAPE_CHANGED'):
            s.transform(name, raw.replace(s.MORPH_START.encode(), b'changed'))
        with self.assertRaisesRegex(ValueError, 'PUBLIC_FILE_NOT_ALLOWED'):
            s.transform('scripts/morph_voice_reference.py', b'not allowed')

    def test_packaged_prepare_helper_is_exact_and_stdlib_entry_runs(self):
        name = 'scripts/prepare_voice_profile.py'
        raw = (TOOL.parents[1] / s.source_key(name)).read_bytes()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'prepare_voice_profile.py'
            path.write_bytes(s.transform(name, raw))
            self.assertEqual(path.read_bytes(), raw)
            result = subprocess.run([sys.executable, '-I', str(path), '--help'], capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn(b'--capabilities', result.stdout)


if __name__ == '__main__':
    unittest.main()
