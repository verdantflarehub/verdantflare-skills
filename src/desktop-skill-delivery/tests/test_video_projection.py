"""Projection checks plus existing MCP regression suites; no media/service calls."""
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / 'skills/verdantflare-video/scripts/video_client.py'
SPEC = importlib.util.spec_from_file_location('video_projection', Path(__file__).resolve().parents[1] / 'video_projection.py')
p = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(p)


class ProjectionTests(unittest.TestCase):
    def setUp(self):
        self.raw = SOURCE.read_bytes()
        self.digest = hashlib.sha256(self.raw).hexdigest()

    def test_projection_has_no_admin_import_and_keeps_public_commands(self):
        output, record = p.project(self.raw, self.digest)
        self.assertFalse({'config', 'task_store'} & set(record['imports']))
        self.assertNotIn(b'du-0', output)
        self.assertIn(b'fasth3-nvfp4', output)
        self.assertIn(b"STUDIO_VIDEO_CLIENT_CONTRACT = 'channel-capabilities-v1'", output)
        self.assertEqual(output, p.project(self.raw, self.digest)[0])
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / 'video_client.py'
            target.write_bytes(output)
            for name in ('channel_contract.py', 'load_env.py'):
                (Path(tmp) / name).write_bytes((SOURCE.parent / name).read_bytes())
            help_result = subprocess.run([sys.executable, str(target), '--help'], capture_output=True, text=True)
            self.assertEqual(help_result.returncode, 0, help_result.stderr)
            self.assertIn('regenerate-2k', help_result.stdout)
            for command in ('recover', 'list'):
                result = subprocess.run([sys.executable, str(target), command], capture_output=True, text=True)
                self.assertEqual(result.returncode, 2)
            denied = subprocess.run([sys.executable, str(target), 'generate', '--prompt', 'test',
                                     '--quality-profile', 'du-0'], capture_output=True, text=True)
            self.assertEqual(denied.returncode, 2)

    def test_changed_source_and_unknown_admin_dependency_fail_closed(self):
        with self.assertRaisesRegex(ValueError, 'SOURCE_LOCK_MISMATCH'):
            p.project(self.raw + b'\n', self.digest)
        changed = self.raw.replace(b'    values = load_env()', b'    import config\n    values = load_env()')
        with self.assertRaisesRegex(ValueError, 'NESTED_IMPORT'):
            p.project(changed, hashlib.sha256(changed).hexdigest())
        changed = self.raw.replace(b'    values = load_env()', b'    values = __import__("config").load_config()')
        with self.assertRaisesRegex(ValueError, 'DYNAMIC_SOURCE'):
            p.project(changed, hashlib.sha256(changed).hexdigest())

    def test_existing_mcp_regressions_use_generated_module(self):
        output, _ = p.project(self.raw, self.digest)
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / 'video_client.py'
            target.write_bytes(output)
            for name in ('channel_contract.py', 'load_env.py'):
                (Path(tmp) / name).write_bytes((SOURCE.parent / name).read_bytes())
            # Import projection first: historical test path insertion must not select original.
            runner = '''import sys, pathlib, unittest
sys.path.insert(0, sys.argv[1])
import video_client
assert pathlib.Path(video_client.__file__).resolve() == pathlib.Path(sys.argv[1], 'video_client.py').resolve()
assert 'config' not in sys.modules and 'task_store' not in sys.modules
sys.path.insert(0, sys.argv[2])
suite=unittest.defaultTestLoader.loadTestsFromNames(['test_video_client_fal','test_channel_contract','test_regenerate_2k_client','test_interpolation_delivery'])
result=unittest.TextTestRunner(verbosity=1).run(suite)
assert 'config' not in sys.modules and 'task_store' not in sys.modules
sys.exit(0 if result.wasSuccessful() else 1)
'''
            result = subprocess.run([sys.executable, '-c', runner, tmp, str(SOURCE.parent.parent / 'tests')],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('OK', result.stderr)


if __name__ == '__main__':
    unittest.main()
