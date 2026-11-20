"""Host identity observation and drift protection without starting applications."""
import json
import os
from pathlib import Path
import plistlib
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import host_discovery as h


class DiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[4] / '.tmp')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()

    def application(self, name='Misleading display.app', bundle='com.openai.codex', **values):
        app = self.root / name
        (app / 'Contents/MacOS').mkdir(parents=True)
        (app / 'Contents/MacOS/Main').write_bytes(b'Inert fixture, not an executable')
        info = {'CFBundleIdentifier': bundle, 'CFBundleShortVersionString': '1.0.0',
                'CFBundleVersion': '42', 'CFBundleExecutable': 'Main', **values}
        (app / 'Contents/Info.plist').write_bytes(plistlib.dumps(info))
        return app

    def test_five_products_follow_exact_identity_not_display_name(self):
        for index, (bundle, product) in enumerate(h.PRODUCTS.items()):
            self.application(name=f'unrelated-{index}.app', bundle=bundle)
        apps, errors = h.scan_roots([self.root])
        report = h.receipt(apps, errors)
        self.assertFalse(errors)
        self.assertEqual(set(report['targets'].values()), {'metadata_observed'})
        self.assertEqual(len(apps), 5)
        for row in report['observed']:
            self.assertFalse(row['signature_verified'])
            self.assertFalse(row['host_verified'])
        self.assertFalse(report['desktop_acceptance'])

    def test_ide_is_excluded_and_unknown_does_not_become_desktop(self):
        self.application(bundle='com.google.antigravity-ide')
        self.application('Codex.app', bundle='com.example.unrelated')
        apps, errors = h.scan_roots([self.root])
        report = h.receipt(apps, errors)
        self.assertEqual(len(apps), 1)
        self.assertFalse(report['observed'][0]['target_surface'])
        self.assertEqual(report['targets']['antigravity'], 'not_observed')
        self.assertEqual(report['targets']['codex'], 'not_observed')

    def test_duplicate_installations_remain_ambiguous_and_root_repeat_deduplicates(self):
        self.application('First.app')
        self.application('Second.app', CFBundleVersion='43')
        apps, errors = h.scan_roots([self.root, self.root])
        self.assertEqual(len(apps), 2)
        self.assertEqual(h.receipt(apps, errors)['targets']['codex'], 'ambiguous')

    def test_version_build_and_executable_drift_invalidate_previous_observation(self):
        app = self.application()
        original = h.inspect_application(app)
        plist = app / 'Contents/Info.plist'
        original_bytes = plist.read_bytes()
        for key in ('CFBundleVersion', 'CFBundleShortVersionString'):
            value = plistlib.loads(original_bytes)
            value[key] = '2.0'
            plist.write_bytes(plistlib.dumps(value))
            with self.assertRaisesRegex(h.ObservationError, 'APPLICATION_IDENTITY_CHANGED'):
                h.revalidate(original)
            plist.write_bytes(original_bytes)
        (app / 'Contents/MacOS/Main').write_bytes(b'Changed fixture')
        with self.assertRaisesRegex(h.ObservationError, 'APPLICATION_IDENTITY_CHANGED'):
            h.revalidate(original)

    def test_symlink_root_app_plist_and_executable_are_refused(self):
        app = self.application()
        root_link = self.root.parent / (self.root.name + '-link')
        root_link.symlink_to(self.root, target_is_directory=True)
        self.addCleanup(root_link.unlink)
        self.assertEqual(h.scan_roots([root_link]), ((), ('APPLICATION_LINK_REJECTED',)))
        linked_app = self.root / 'Linked.app'
        linked_app.symlink_to(app, target_is_directory=True)
        apps, errors = h.scan_roots([self.root])
        self.assertEqual(len(apps), 1)
        self.assertIn('APPLICATION_LINK_REJECTED', errors)
        for relative in ('Contents/Info.plist', 'Contents/MacOS/Main'):
            path = app / relative
            backup = path.with_suffix('.original')
            path.rename(backup)
            path.symlink_to(backup)
            with self.assertRaisesRegex(h.ObservationError, 'APPLICATION_LINK_REJECTED'):
                h.inspect_application(app)
            path.unlink()
            backup.rename(path)

    def test_nonregular_input_and_path_escape_do_not_block_or_read_outside(self):
        app = self.application(CFBundleExecutable='../Info.plist')
        with self.assertRaisesRegex(h.ObservationError, 'APPLICATION_EXECUTABLE_INVALID'):
            h.inspect_application(app)
        plist = app / 'Contents/Info.plist'
        plist.unlink()
        os.mkfifo(plist)
        with self.assertRaisesRegex(h.ObservationError, 'APPLICATION_FILE_REQUIRED'):
            h.inspect_application(app)

    def test_bad_metadata_and_unreadable_root_are_not_false_absence(self):
        app = self.application(CFBundleVersion='unsafe\nvalue')
        apps, errors = h.scan_roots([self.root, self.root / 'missing'])
        report = h.receipt(apps, errors)
        self.assertFalse(report['scan_complete'])
        self.assertEqual(report['targets']['codex'], 'unknown_due_to_scan_error')
        self.assertIn('APPLICATION_VERSION_INVALID', errors)
        (app / 'Contents/Info.plist').write_bytes(b'not a plist')
        with self.assertRaisesRegex(h.ObservationError, 'APPLICATION_PLIST_INVALID'):
            h.inspect_application(app)

    def test_receipts_do_not_contain_paths_names_or_configuration(self):
        self.application(name='PRIVATE-HOST-NAME.app', CFBundleExecutable='Main')
        apps, errors = h.scan_roots([self.root])
        encoded = json.dumps(h.receipt(apps, errors))
        for private in (str(self.root), 'PRIVATE-HOST-NAME', 'Main', 'Contents'):
            self.assertNotIn(private, encoded)
        self.assertFalse(h.receipt(apps, errors)['configuration_written'])

    def test_observed_directory_drift_discards_that_roots_candidates(self):
        self.application()
        original = h.inspect_application
        def changing(path):
            result = original(path)
            (self.root / 'new-entry').write_text('drift')
            return result
        with patch.object(h, 'inspect_application', side_effect=changing):
            apps, errors = h.scan_roots([self.root])
        self.assertFalse(apps)
        self.assertEqual(errors, ('APPLICATION_ROOT_CHANGED',))


if __name__ == '__main__':
    unittest.main()
