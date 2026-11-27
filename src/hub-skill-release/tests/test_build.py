import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

MODULE = Path(__file__).resolve().parents[1] / 'build.py'
spec = importlib.util.spec_from_file_location('hub_skill_build', MODULE)
b = importlib.util.module_from_spec(spec)
spec.loader.exec_module(b)


class BuildTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.recipe = {'skills': [{'id': 'verdantflare-artifact', 'requires': [],
                                   'files': ['SKILL.md', 'references/use.md']}]}
        sources = {
            'recipe.json': json.dumps(self.recipe),
            'skills/verdantflare-artifact/SKILL.md': '# Artifact\n',
            'skills/verdantflare-artifact/references/use.md': 'Reference\n',
            'src/hub-skill-release/build.py': MODULE.read_text(),
            'src/hub-skill-release/guides/Install.md': '# Install {{release_id}}\n\n{{skills}}\n',
            'src/hub-skill-release/guides/USER-SKILLS.md': '# Skills {{release_id}}\n\n{{skills}}\n',
        }
        for name, content in sources.items():
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)

    def build(self, dirty=False, allow_dirty=False):
        with patch.object(b, 'git_state', return_value={'commit': 'c' * 40, 'dirty': dirty}):
            return b.build(self.root, 'recipe.json', '0.1.0-test.1', allow_dirty=allow_dirty)

    def test_deterministic_and_bound(self):
        data = self.build()
        self.assertEqual(data, self.build())
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            release_raw = archive.read('candidate/release-manifest.json')
            release = json.loads(release_raw)
            self.assertEqual(release['schema_version'], b.SCHEMA)
            self.assertEqual(len(release['packages']), 1)
            lock_raw = archive.read('candidate/source-lock.json')
            self.assertEqual(b.sha(lock_raw), release['source_lock_sha256'])
            self.assertFalse(json.loads(lock_raw)['source_repository']['dirty'])
            guides = json.loads(archive.read('guides/guide-manifest.json'))
            self.assertEqual(guides['candidate_manifest_sha256'], b.sha(release_raw))
            for name, expected in guides['files'].items():
                self.assertEqual(b.record(archive.read('guides/' + name)), expected)
            package = release['packages'][0]
            raw = archive.read('candidate/' + package['artifact']['path'])
            self.assertEqual(package['artifact']['sha256'], b.sha(raw))
            with zipfile.ZipFile(io.BytesIO(raw)) as inner:
                self.assertEqual(set(inner.namelist()), {'verdantflare-artifact/SKILL.md',
                                                        'verdantflare-artifact/references/use.md'})

    def test_dirty_never_becomes_clean(self):
        with self.assertRaisesRegex(ValueError, 'dirty'):
            self.build(dirty=True)
        raw = self.build(dirty=True, allow_dirty=True)
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            self.assertTrue(json.loads(archive.read('candidate/source-lock.json'))['source_repository']['dirty'])

    def test_recipe_rejections(self):
        for mutate in (
            lambda r: r['skills'].clear(),
            lambda r: r['skills'].append(r['skills'][0].copy()),
            lambda r: r['skills'][0].update(id='../bad'),
            lambda r: r['skills'][0].update(files=['../SKILL.md', 'SKILL.md']),
            lambda r: r['skills'][0].update(files=['SKILL.md', '.env']),
            lambda r: r['skills'][0].update(files=['references/use.md']),
            lambda r: r['skills'][0].update(requires=['missing-skill']),
            lambda r: r['skills'][0].update(requires=['verdantflare-artifact']),
            lambda r: r['skills'][0].update(requires=['missing-skill', 'missing-skill']),
        ):
            recipe = json.loads(json.dumps(self.recipe))
            mutate(recipe)
            with self.subTest(recipe=recipe), self.assertRaises(ValueError):
                b.validate_recipe(recipe)

    def test_dependency_closure(self):
        recipe = json.loads(json.dumps(self.recipe))
        recipe['skills'].append({'id': 'verdantflare-project', 'requires': ['verdantflare-artifact'], 'files': ['SKILL.md']})
        self.assertEqual(len(b.validate_recipe(recipe)), 2)
        recipe['skills'][0]['requires'] = ['verdantflare-project']
        with self.assertRaisesRegex(ValueError, 'cycle'):
            b.validate_recipe(recipe)

    def test_source_symlink(self):
        original = self.root / 'skills/verdantflare-artifact/SKILL.md'
        original.unlink()
        original.symlink_to(self.root / 'recipe.json')
        with self.assertRaisesRegex(ValueError, 'symlink'):
            self.build()

    def test_invalid_release(self):
        with self.assertRaisesRegex(ValueError, 'release id'):
            b.build(self.root, 'recipe.json', '../bad')

    def test_archive_limits(self):
        with patch.object(b, 'MAX_ENTRIES', 1), self.assertRaisesRegex(ValueError, 'limit'):
            b.zip_bytes({'a.md': b'a', 'b.md': b'b'})
        with patch.object(b, 'MAX_EXPANDED', 1), self.assertRaisesRegex(ValueError, 'limit'):
            b.zip_bytes({'a.md': b'ab'})

    def test_missing_or_empty_skill(self):
        leaf = self.root / 'skills/verdantflare-artifact/SKILL.md'
        leaf.write_text(' \n')
        with self.assertRaisesRegex(ValueError, 'empty SKILL'):
            self.build()

    def test_source_changes_are_not_hidden(self):
        with patch.object(b, 'git_state', side_effect=[{'commit': 'c'*40, 'dirty': False},
                                                     {'commit': 'd'*40, 'dirty': False}]), \
                self.assertRaisesRegex(ValueError, 'source changed'):
            b.build(self.root, 'recipe.json', '0.1.0')


if __name__ == '__main__':
    unittest.main()
