"""Real data-only archive admission, not execution of synthetic interpreters."""
from pathlib import Path
import json
import os
import stat
import sys
import tempfile
import unittest
import zipfile

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS / 'installer_tools'))
import bootstrap_transfer as transfer

BASE = TOOLS.parents[2] / '.tmp/bootstrap-transfer-tests'


class BootstrapTransferTests(unittest.TestCase):
    def setUp(self):
        BASE.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=BASE); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).absolute(); self.bundle = self.root / 'bundle'; self.bundle.mkdir(mode=0o700)
        self.manifest = {'schema': 'vf.installer-bootstrap/v1', 'platform': 'macos-arm64',
                         'python': 'resources/runtime/python/bin/python3.12',
                         'driver': 'resources/installer/driver.py', 'entries': {}}
        payloads = {self.manifest['python']: (b'INTERPRETER_FIXTURE_NEVER_EXECUTE\n', 0o755),
                    self.manifest['driver']: (b'DRIVER_FIXTURE_NEVER_EXECUTE\n', 0o644),
                    'resources/vendor/package.py': (b'# data-only\n', 0o644)}
        for name, (raw, mode) in payloads.items():
            path = self.bundle / name; path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
            path.write_bytes(raw); path.chmod(mode)
            self.manifest['entries'][name] = {'type': 'file', 'size': len(raw), 'sha256': transfer.tx.sha(raw), 'mode': mode}
        link = 'resources/runtime/python/bin/python3'
        os.symlink('python3.12', self.bundle / link)
        self.manifest['entries'][link] = {'type': 'symlink', 'target': 'python3.12'}
        binary = self.bundle / 'vf-bootstrap'; binary.write_bytes(b'NATIVE_FIXTURE_NEVER_EXECUTE\n'); binary.chmod(0o755)
        self.binary_sha = transfer.tx.sha(binary.read_bytes())
        self.manifest_path = self.bundle / 'bootstrap-manifest.json'
        self.manifest_path.write_bytes(transfer.tx.encode(self.manifest)); self.manifest_path.chmod(0o644)
        self.manifest_sha = transfer.tx.sha(self.manifest_path.read_bytes())
        self.archive = self.root / 'bundle.zip'
        self.record = transfer.archive(self.bundle, self.manifest_sha, self.binary_sha, self.archive)

    def receive(self, archive=None, output=None):
        archive = archive or self.archive
        return transfer.receive(archive, transfer.composition.workspace.sdk.sha(archive), self.manifest_sha,
                                self.binary_sha, self.root / 'received' if output is None else output)

    def rewrite(self, callback):
        output = self.root / 'mutated.zip'
        with zipfile.ZipFile(self.archive) as source, zipfile.ZipFile(output, 'x') as target:
            for item in source.infolist():
                callback(item, source.read(item), target)
        return output

    def test_roundtrip_preserves_bytes_links_and_modes_without_execution(self):
        result = self.receive()
        self.assertTrue(result['received']); self.assertFalse(result['resource_code_executed'])
        self.assertFalse(result['installable']); self.assertFalse(result['publisher_signature_verified'])
        restored = self.root / 'received'
        for name, record in self.manifest['entries'].items():
            path = restored / name
            if record['type'] == 'symlink': self.assertEqual(os.readlink(path), record['target'])
            else:
                self.assertEqual(path.read_bytes(), (self.bundle / name).read_bytes())
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), record['mode'])
        self.assertFalse((self.root / 'MUST_NOT_EXECUTE').exists())

    def test_repeat_archive_is_byte_identical(self):
        second = self.root / 'second.zip'
        transfer.archive(self.bundle, self.manifest_sha, self.binary_sha, second)
        self.assertEqual(self.archive.read_bytes(), second.read_bytes())

    def test_external_archive_manifest_and_binary_pins_are_independent(self):
        for which in ('archive', 'manifest', 'binary'):
            with self.subTest(which=which):
                pins = [self.record['sha256'], self.manifest_sha, self.binary_sha]
                pins[('archive', 'manifest', 'binary').index(which)] = '0' * 64
                output = self.root / ('rejected-' + which)
                with self.assertRaises(transfer.tx.Conflict):
                    transfer.receive(self.archive, *pins, output)
                self.assertFalse(output.exists())

    def test_rehashed_zip_cannot_substitute_resource_binary_or_manifest(self):
        for target in ('resources/vendor/package.py', 'vf-bootstrap', 'bootstrap-manifest.json'):
            with self.subTest(target=target):
                archive = self.rewrite(lambda info, raw, out: out.writestr(info, raw + b' ' if info.filename == target else raw))
                output = self.root / 'rejected'
                with self.assertRaises(transfer.tx.Conflict): self.receive(archive, output)
                self.assertFalse(output.exists()); archive.unlink()

    def test_unknown_duplicate_traversal_and_mode_refuse_before_output(self):
        for case in ('extra', 'duplicate', 'traversal', 'mode', 'directory'):
            with self.subTest(case=case):
                changed = False
                def alter(info, raw, out):
                    nonlocal changed
                    if case == 'mode' and info.filename == 'resources/vendor/package.py': info.external_attr = (stat.S_IFREG | 0o777) << 16
                    out.writestr(info, raw)
                    if not changed:
                        changed = True
                        if case == 'extra': out.writestr('unknown', b'x')
                        if case == 'duplicate':
                            import warnings
                            with warnings.catch_warnings():
                                warnings.simplefilter('ignore', UserWarning); out.writestr(info, raw)
                        if case == 'traversal': out.writestr('../escape', b'x')
                        if case == 'directory': out.writestr('resources/unknown/', b'')
                archive = self.rewrite(alter); output = self.root / 'rejected'
                with self.assertRaises(transfer.tx.Conflict): self.receive(archive, output)
                self.assertFalse(output.exists()); archive.unlink()

    def test_source_unknown_directory_hardlink_and_mode_are_preserved(self):
        extra = self.bundle / 'unknown'; extra.mkdir()
        with self.assertRaises(transfer.tx.Conflict): transfer.verify_folder(self.bundle, self.manifest_sha, self.binary_sha)
        self.assertTrue(extra.is_dir()); extra.rmdir()
        path = self.bundle / 'resources/vendor/package.py'; alias = self.root / 'external-hardlink'; os.link(path, alias)
        with self.assertRaises(transfer.tx.Conflict): transfer.verify_folder(self.bundle, self.manifest_sha, self.binary_sha)
        self.assertTrue(alias.exists()); alias.unlink(); path.chmod(0o666)
        with self.assertRaises(transfer.tx.Conflict): transfer.verify_folder(self.bundle, self.manifest_sha, self.binary_sha)
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o666)

    def test_links_aliases_and_arbitrary_driver_refuse_even_with_new_manifest_pin(self):
        for case in ('escape', 'cycle', 'alias', 'prefix', 'driver'):
            with self.subTest(case=case):
                value = json.loads(self.manifest_path.read_bytes()); entries = value['entries']
                link = 'resources/runtime/python/bin/python3'
                if case == 'escape': entries[link]['target'] = '../../../../../../outside'
                if case == 'cycle': entries[link]['target'] = 'python3'
                if case == 'alias': entries['resources/Vendor/other.py'] = dict(entries['resources/vendor/package.py'])
                if case == 'prefix': entries['resources/vendor/package.py/child'] = dict(entries['resources/vendor/package.py'])
                if case == 'driver': value['driver'] = 'resources/vendor/package.py'
                raw = transfer.tx.encode(value)
                with self.assertRaises(transfer.tx.Conflict): transfer.parse_manifest(raw, transfer.tx.sha(raw))

    def test_output_protection_and_error_messages_do_not_disclose_paths(self):
        output = self.root / 'existing'; output.mkdir(); marker = output / 'keep'; marker.write_bytes(b'original')
        with self.assertRaises(transfer.tx.Conflict): self.receive(output=output)
        self.assertEqual(marker.read_bytes(), b'original')
        alias = self.root / 'alias'; os.symlink(output, alias)
        with self.assertRaises(transfer.tx.Conflict): self.receive(output=alias / 'new')
        self.assertFalse((output / 'new').exists())
        missing = self.root / 'SYNTHETIC_SECRET_PATH'
        with self.assertRaises(transfer.tx.Conflict) as caught:
            transfer.receive(missing, '0' * 64, self.manifest_sha, self.binary_sha)
        self.assertRegex(str(caught.exception), '^[A-Z_]+$')
        self.assertNotIn('SYNTHETIC_SECRET_PATH', str(caught.exception))


if __name__ == '__main__': unittest.main()
