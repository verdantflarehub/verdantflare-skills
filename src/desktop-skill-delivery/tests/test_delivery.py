"""Synthetic adversarial tests for the engineering handoff, not desktop acceptance."""
import importlib.util
import io
import json
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

MODULE = Path(__file__).resolve().parents[1] / 'delivery.py'
sys.path.insert(0, str(MODULE.parent))
spec = importlib.util.spec_from_file_location('delivery', MODULE)
d = importlib.util.module_from_spec(spec)
spec.loader.exec_module(d)


class HandoffTests(unittest.TestCase):
    def setUp(self):
        temporary_root = MODULE.parents[3] / '.tmp' / 'skill-delivery-tests'
        temporary_root.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=temporary_root)
        self.base = Path(self.temp.name).resolve()
        self.source = self.base / 'source'
        self.source.mkdir()
        for pkg, names in d.ALLOW.items():
            for name in names:
                p = self.source / d.source_key(pkg, name)
                p.parent.mkdir(parents=True, exist_ok=True)
                if pkg in (d.VIDEO, d.MUSIC, d.SPEECH, d.DRAMA, d.BLENDER) or pkg in d.MEDIA_FILES:
                    p.write_bytes((MODULE.parents[2] / d.source_key(pkg, name)).read_bytes())
                else:
                    p.write_text('synthetic unit-test fixture ' + name + '\n', encoding='utf-8')
        self.mock = patch.object(d, 'source_state', return_value={'commit': 'a' * 40, 'dirty': True})
        self.mock.start()
        self.lock = d.freeze(self.source, '0.1.0-integration.1')
        self.out = self.base / 'bundle'

    def tearDown(self):
        self.mock.stop()
        self.temp.cleanup()

    def build(self):
        return d.build(self.source, self.lock, self.out)

    def rewrite_manifest(self, mutate):
        path = self.out / 'release-manifest.json'
        m = d.decode(path.read_bytes())
        mutate(m)
        path.write_bytes(d.encoded(m))

    def test_real_byte_roundtrip_keeps_missing_deliverables(self):
        r = self.build()
        self.assertEqual((r['assembled_packages'], r['blocked_packages']), (11, 0))
        self.assertFalse(r['installable'])
        self.assertEqual(r['authentication'], 'unsigned')
        manifest = d.decode((self.out / 'release-manifest.json').read_bytes())
        self.assertEqual(len(manifest['support_matrix']), 10)
        self.assertTrue(all(x['status'] == 'not_run' for x in manifest['support_matrix']))
        self.assertEqual(manifest['components']['installer'], 'missing')
        character = next(p for p in manifest['packages'] if p['id'] == 'verdantflare-character-face')
        self.assertEqual(character['status'], 'assembled')
        blocked = {p['id'] for p in manifest['packages'] if p['status'] == 'blocked'}
        self.assertEqual(blocked, set())
        self.assertNotIn('verdantflare-music', blocked)
        self.assertTrue({'verdantflare-image', 'verdantflare-video'} <= set(character['requires']))
        self.assertIn('runtime_closure_not_verified', manifest['limitations'])

    def test_rebuild_bytes_are_identical(self):
        self.build()
        second = self.base / 'second'
        d.build(self.source, self.lock, second)
        one = {p.relative_to(self.out).as_posix(): p.read_bytes() for p in self.out.rglob('*') if p.is_file()}
        two = {p.relative_to(second).as_posix(): p.read_bytes() for p in second.rglob('*') if p.is_file()}
        self.assertEqual(one, two)

    def test_all_native_versions_match_package_release_without_rewriting_instructions(self):
        self.build()
        for pkg in d.IDS:
            with self.subTest(pkg=pkg), zipfile.ZipFile(self.out / 'packages' / (pkg + '.zip')) as z:
                self.assertEqual(d.decode(z.read(pkg + '/skill.json')),
                                 {'name': pkg, 'version': self.lock['release_id']})
                record = self.lock['outputs'][pkg + '/skill.json']
                self.assertEqual(record['source'], 'skills/' + pkg + '/SKILL.md')
                self.assertEqual(record['transform'], 'skill-version-descriptor-v1')
                raw = d.read_file(self.source, d.source_key(pkg, 'SKILL.md'), source=True)
                self.assertEqual(z.read(pkg + '/SKILL.md'), d.transform(pkg, 'SKILL.md', raw))

    def test_relabelled_release_cannot_reuse_old_native_descriptor(self):
        self.build()
        pkg = d.IDS[0]
        leaf = d.decode((self.out / 'manifests' / (pkg + '.json')).read_bytes())
        # Even when the original package bytes and file hashes match, an outer
        # release relabel must not silently leave the native version behind.
        changed = dict(self.lock, release_id='0.1.0-integration.2')
        with self.assertRaisesRegex(d.Invalid, 'SKILL_DESCRIPTOR_MISMATCH'):
            d.verify_archive((self.out / 'packages' / (pkg + '.zip')).read_bytes(), leaf, changed, pkg)

    def test_new_release_requires_new_derived_descriptor_hashes(self):
        self.lock['release_id'] = '0.1.0-integration.2'
        with self.assertRaisesRegex(d.Invalid, 'DERIVED_OUTPUT_MISMATCH'):
            self.build()
        self.assertFalse(self.out.exists())

    def test_handoff_verifies_without_source_repository(self):
        self.build()
        archive = self.base / 'handoff.zip'
        receipt = d.handoff(self.out, archive)
        second = self.base / 'second.zip'
        d.handoff(self.out, second)
        self.assertEqual(archive.read_bytes(), second.read_bytes())
        self.assertEqual(receipt['handoff_sha256'], d.digest(archive.read_bytes()))
        recipient = self.base / 'recipient'
        with zipfile.ZipFile(archive) as z:
            # Bytes were created here, not arbitrary user supplied archives.
            z.extractall(recipient)
        import shutil
        shutil.rmtree(self.source)
        shutil.rmtree(self.out)
        result = subprocess.run([sys.executable, 'tooling/delivery.py', 'verify', 'candidate'],
                                cwd=recipient, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['assembled_packages'], 11)
        self.assertFalse(json.loads(result.stdout)['installable'])

    def test_handoff_preserves_existing_output_and_rejects_tampering(self):
        self.build()
        archive = self.base / 'handoff.zip'
        archive.write_bytes(b'previous handoff')
        with self.assertRaisesRegex(d.Invalid, 'OUTPUT_EXISTS'):
            d.handoff(self.out, archive)
        self.assertEqual(archive.read_bytes(), b'previous handoff')
        self.rewrite_manifest(lambda m: m.__setitem__('installable', True))
        new = self.base / 'new.zip'
        with self.assertRaisesRegex(d.Invalid, 'CANDIDATE_PROMOTION_REJECTED'):
            d.handoff(self.out, new)
        self.assertFalse(new.exists())

    def test_export_root_symlink_rejected(self):
        self.build()
        link = self.base / 'linked'
        link.symlink_to(self.base, target_is_directory=True)
        with self.assertRaisesRegex(d.Invalid, 'OUTPUT_SYMLINK'):
            d.handoff(self.out, link / 'handoff.zip')
        self.assertFalse((self.base / 'handoff.zip').exists())

    def test_source_drift_rejected_without_output(self):
        (self.source / next(iter(self.lock['files']))).write_text('changed', encoding='utf-8')
        with self.assertRaisesRegex(d.Invalid, 'SOURCE_LOCK_MISMATCH'):
            self.build()
        self.assertFalse(self.out.exists())

    def test_transformed_source_and_output_are_distinct_and_bound(self):
        self.build()
        key = 'verdantflare-video/scripts/video_client.py'
        r = self.lock['outputs'][key]
        self.assertEqual(r['transform'], 'video-mcp-ast-v1')
        self.assertNotEqual(r['sha256'], r['source_sha256'])
        self.assertEqual(r['source_sha256'], self.lock['files'][r['source']]['sha256'])
        self.lock['outputs'][key]['sha256'] = '0' * 64
        with self.assertRaisesRegex(d.Invalid, 'DERIVED_OUTPUT_MISMATCH'):
            d.build(self.source, self.lock, self.base / 'tampered')
        self.assertFalse((self.base / 'tampered').exists())

    def test_transform_label_and_copy_tampering_are_rejected(self):
        key = 'verdantflare-video/scripts/video_client.py'
        self.lock['outputs'][key]['transform'] = 'copy'
        with self.assertRaisesRegex(d.Invalid, 'OUTPUT_PROVENANCE_MISMATCH'):
            self.build()
        self.lock['outputs'][key]['transform'] = 'video-mcp-ast-v1'
        key = 'verdantflare-artifact/SKILL.md'
        self.lock['outputs'][key]['sha256'] = '0' * 64
        with self.assertRaisesRegex(d.Invalid, 'COPY_SOURCE_MISMATCH'):
            self.build()

    def test_projection_dependency_change_rejected(self):
        original = d.read_file
        def changed(root, name, *args, **kwargs):
            data = original(root, name, *args, **kwargs)
            return data + b'\n# changed\n' if name == 'recipes.py' else data
        with patch.object(d, 'read_file', side_effect=changed):
            with self.assertRaisesRegex(d.Invalid, 'BUILDER_CHANGED'):
                self.build()

    def test_changed_build_python_rejected(self):
        with patch.object(d.platform, 'python_version', return_value='3.99.0'):
            with self.assertRaisesRegex(d.Invalid, 'BUILD_PYTHON_CHANGED'):
                self.build()

    def test_symlink_in_source_rejected(self):
        p = self.source / next(iter(self.lock['files']))
        real = self.base / 'outside'
        p.rename(real)
        p.symlink_to(real)
        with self.assertRaisesRegex(d.Invalid, 'SYMLINK_REJECTED'):
            self.build()
        self.assertFalse(self.out.exists())

    def test_existing_output_preserved(self):
        self.out.mkdir()
        (self.out / 'user.txt').write_text('keep', encoding='utf-8')
        with self.assertRaisesRegex(d.Invalid, 'OUTPUT_EXISTS'):
            self.build()
        self.assertEqual((self.out / 'user.txt').read_text(), 'keep')

    def test_failed_verification_cleans_only_new_output(self):
        keep = self.base / 'keep.txt'
        keep.write_text('keep', encoding='utf-8')
        with patch.object(d, 'verify', side_effect=d.Invalid('injected_failure')):
            with self.assertRaisesRegex(d.Invalid, 'injected_failure'):
                self.build()
        self.assertFalse(self.out.exists())
        self.assertTrue(keep.is_file())

    def test_unsigned_cannot_be_promoted(self):
        for field, value in [('installable', True), ('public_distribution', True), ('authentication', 'signed')]:
            with self.subTest(field=field):
                if self.out.exists():
                    import shutil
                    shutil.rmtree(self.out)
                self.build()
                self.rewrite_manifest(lambda m: m.__setitem__(field, value))
                with self.assertRaisesRegex(d.Invalid, 'CANDIDATE_PROMOTION_REJECTED'):
                    d.verify(self.out)

    def test_missing_package_cannot_shrink_scope(self):
        self.build()
        self.rewrite_manifest(lambda m: m['packages'].pop())
        with self.assertRaisesRegex(d.Invalid, 'PACKAGE_SET_MISMATCH'):
            d.verify(self.out)

    def test_short_drama_keeps_locked_blender_dependency(self):
        self.build()
        m = d.decode((self.out / 'release-manifest.json').read_bytes())
        drama = next(p for p in m['packages'] if p['id'] == d.DRAMA)
        self.assertIn('verdantflare-blender', drama['requires'])
        self.rewrite_manifest(lambda m: next(p for p in m['packages'] if p['id'] == d.DRAMA)['requires'].remove('verdantflare-blender'))
        with self.assertRaisesRegex(d.Invalid, 'DEPENDENCY_OR_VERSION_MISMATCH'):
            d.verify(self.out)

    def test_fake_host_pass_rejected(self):
        self.build()
        self.rewrite_manifest(lambda m: m['support_matrix'][0].__setitem__('status', 'pass'))
        with self.assertRaisesRegex(d.Invalid, 'UNVERIFIED_SUPPORT_CLAIM'):
            d.verify(self.out)

    def test_unexpected_file_rejected(self):
        self.build()
        (self.out / '.env').write_text('dummy synthetic data', encoding='utf-8')
        with self.assertRaisesRegex(d.Invalid, 'UNEXPECTED_BUNDLE_FILE'):
            d.verify(self.out)

    def test_archive_damage_rejected(self):
        self.build()
        p = self.out / 'packages' / (d.IDS[0] + '.zip')
        p.write_bytes(p.read_bytes() + b'changed')
        with self.assertRaisesRegex(d.Invalid, 'ARTIFACT_HASH_MISMATCH'):
            d.verify(self.out)

    def test_unsafe_paths(self):
        for path in ('../x', '/x', 'C:/x', 'x\\y', 'x//y', 'x/./y', 'NUL.txt', 'x.', 'x/COM1', 'x/\x00'):
            with self.subTest(path=path), self.assertRaises(d.Invalid):
                d.safe_path(path)

    def test_duplicate_json_rejected(self):
        with self.assertRaisesRegex(d.Invalid, 'DUPLICATE_JSON_KEY'):
            d.decode(b'{"a":1,"a":2}')

    def test_unicode_sources_never_relax_archive_paths(self):
        key = 'verdantflare-music/references/lyrics-rnb.md'
        source = self.lock['outputs'][key]['source']
        self.assertIn('歌词-R&B-审核规范.md', source)
        self.assertEqual(self.lock['outputs'][key]['source_sha256'], self.lock['files'][source]['sha256'])
        self.assertEqual(d.read_file(self.source, source, source=True), (self.source / source).read_bytes())
        with self.assertRaisesRegex(d.Invalid, 'INVALID_PATH'):
            d.read_file(self.source, source)
        with self.assertRaisesRegex(d.Invalid, 'INVALID_PATH'):
            d.zip_bytes({source: b'cannot publish a source path'})
        self.build()
        with zipfile.ZipFile(self.out / 'packages/verdantflare-music.zip') as z:
            self.assertTrue(all(n.isascii() for n in z.namelist()))
            self.assertIn(key, z.namelist())

    def test_unicode_traversal_controls_and_normalization_rejected(self):
        for name in ('中文/../secret', '中文//x', '/中文', '中文\\x', '中文/CON.txt',
                     '中文/\x00', '中文/\u202ex', '中文/\u200bx', '中文/\u007f',
                     '中文/e\u0301.md', '中文/ＣＯＮ.md', '中文/COM¹', '中文/..／secret',
                     '中文/末尾.', '中文/ leading', '中文/x ', '中文/x:y', '中文/x%2fy', '中文/con .txt'):
            with self.subTest(name=name), self.assertRaises(d.Invalid):
                d.safe_source_path(name)
        for name in ('中文/歌词-R&B-审核规范.md', '中文/City Pop.md'):
            self.assertEqual(str(d.safe_source_path(name)), name)

    def test_unicode_source_symlink_and_mutation_rejected(self):
        source = next(n for n in self.lock['files'] if not n.isascii())
        p = self.source / source
        real = self.base / 'private'
        p.rename(real)
        p.symlink_to(real)
        with self.assertRaisesRegex(d.Invalid, 'SYMLINK_REJECTED'):
            self.build()
        p.unlink()
        p.write_bytes(real.read_bytes() + b'\nchanged')
        with self.assertRaisesRegex(d.Invalid, 'SOURCE_LOCK_MISMATCH'):
            self.build()
        self.assertFalse(self.out.exists())

    def test_duplicate_zip_and_symlink_members_rejected(self):
        self.build()
        pkg = d.IDS[0]
        leaf = d.decode((self.out / 'manifests' / (pkg + '.json')).read_bytes())
        original = self.out / 'packages' / (pkg + '.zip')
        with zipfile.ZipFile(original) as z:
            payload = {n: z.read(n) for n in z.namelist()}
        for kind in ('duplicate', 'symlink', 'traversal'):
            with self.subTest(kind=kind):
                stream = io.BytesIO()
                with zipfile.ZipFile(stream, 'w') as z:
                    for name, data in payload.items():
                        info = zipfile.ZipInfo(name)
                        info.external_attr = (stat.S_IFREG | 0o644) << 16
                        z.writestr(info, data)
                    if kind == 'duplicate':
                        import warnings
                        with warnings.catch_warnings():
                            warnings.simplefilter('ignore')
                            z.writestr(next(iter(payload)), b'x')
                    elif kind == 'symlink':
                        info = zipfile.ZipInfo(pkg + '/link')
                        info.external_attr = (stat.S_IFLNK | 0o777) << 16
                        z.writestr(info, b'../../private')
                    else:
                        z.writestr('../private', b'x')
                with self.assertRaises(d.Invalid):
                    d.verify_archive(stream.getvalue(), leaf, self.lock, pkg)

    def test_source_lock_extra_file_and_builder_change_rejected(self):
        self.lock['files']['skills/internal-admin/SKILL.md'] = {'size': 1, 'sha256': 'a' * 64}
        with self.assertRaisesRegex(d.Invalid, 'ALLOWLIST_MISMATCH'):
            self.build()
        self.lock['files'].pop('skills/internal-admin/SKILL.md')
        self.lock['builder_sha256'] = '0' * 64
        with self.assertRaisesRegex(d.Invalid, 'BUILDER_CHANGED'):
            self.build()


if __name__ == '__main__':
    unittest.main()
