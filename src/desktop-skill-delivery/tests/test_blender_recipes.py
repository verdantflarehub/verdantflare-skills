"""Verify the standalone public payload and its executable local preparation path."""
import ast
import json
from pathlib import Path
import posixpath
import re
import subprocess
import sys
import tempfile
import unittest
from urllib.parse import unquote

TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))
sys.path.insert(0, str(TOOL / 'blender_tests'))
import blender_recipes as b
from fixture_plan import make_plan


class BlenderRecipeTests(unittest.TestCase):
    def payload(self):
        return {n: b.transform(n, (TOOL.parents[1] / b.source_key(n)).read_bytes()) for n in b.FILES}

    def test_package_navigation_closes_without_private_repository(self):
        payload = self.payload()
        for name, raw in payload.items():
            if not name.endswith('.md'):
                continue
            text = raw.decode()
            self.assertNotIn('scripts/set-package/', text)
            self.assertNotIn('github.com/verdantflarehub', text)
            for link in re.findall(r'\]\(([^)]+)\)', re.sub(r'`[^`]*`', '', text)):
                if link.startswith(('https:', 'http:')):
                    continue
                relative, _, fragment = unquote(link).partition('#')
                target = posixpath.normpath(posixpath.join(b.BLENDER, posixpath.dirname(name), relative)) if relative else b.BLENDER+'/'+name
                owner, path = target.split('/', 1)
                with self.subTest(source=name, target=target):
                    if owner == b.BLENDER:
                        self.assertIn(path, payload)
                        if fragment:
                            headings = re.findall(r'^#{1,6} (.+)$', payload[path].decode(), re.M)
                            slugs = {re.sub(r'[^\w\- ]', '', h.lower()).replace(' ', '-') for h in headings}
                            self.assertIn(fragment, slugs)
                    else:
                        self.assertIn(owner, {'verdantflare-project', 'verdantflare-artifact'})
                        self.assertTrue((TOOL.parents[1] / 'skills' / target).is_file())

    def test_actual_extracted_preparer_runs_and_preserves_input(self):
        base = TOOL.parents[2] / '.tmp' / 'blender-public-tests'
        base.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=base) as temporary:
            root = Path(temporary)
            for name, raw in self.payload().items():
                p = root / b.BLENDER / name
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes(raw)
            package = root / b.BLENDER
            plan = package / 'plan.json'
            raw = json.dumps(make_plan('1'*64)).encode()
            plan.write_bytes(raw)
            command = [sys.executable, 'scripts/prepare_set_export.py', 'plan.json', '--output', 'prepared.py']
            result = subprocess.run(command, cwd=package, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(json.loads(result.stdout)['executed'])
            self.assertEqual(plan.read_bytes(), raw)
            script = (package / 'prepared.py').read_bytes()
            compile(script, '<packaged-export>', 'exec')
            self.assertLess(len(script), 4*1024*1024)
            denied = subprocess.run(command, cwd=package, capture_output=True, text=True)
            self.assertNotEqual(denied.returncode, 0)
            self.assertEqual((package / 'prepared.py').read_bytes(), script)

    def test_helpers_are_exact_and_dependency_imports_are_bounded(self):
        payload = self.payload()
        script_modules = {Path(n).stem for n in payload if n.endswith('.py')}
        dependencies = set()
        for name, raw in payload.items():
            if not name.startswith('scripts/'):
                continue
            self.assertEqual(raw, (TOOL.parents[1] / b.source_key(name)).read_bytes())
            if name.endswith('.py'):
                for node in ast.walk(ast.parse(raw)):
                    if isinstance(node, ast.Import):
                        dependencies.update(item.name.split('.')[0] for item in node.names)
                    elif isinstance(node, ast.ImportFrom):
                        dependencies.add(node.module.split('.')[0])
        self.assertEqual(dependencies - script_modules - sys.stdlib_module_names, {'bpy', 'mathutils', 'PIL'})

    def test_unexpected_case_script_source_change_fails_closed(self):
        for name, marker in (('SKILL.md', 'scripts/set-package/blender_export_set_package.py'),
                             ('references/project-save.md', 'scripts/set-package/check_camera_alignment.py')):
            raw = (TOOL.parents[1] / b.source_key(name)).read_bytes()
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, 'PUBLIC_DOCUMENT_SOURCE_SHAPE_CHANGED'):
                b.transform(name, raw.replace(marker.encode(), b'new-unknown-behavior.py'))
        with self.assertRaisesRegex(ValueError, 'PUBLIC_FILE_NOT_ALLOWED'):
            b.source_key('scripts/unknown.py')


if __name__ == '__main__':
    unittest.main()
