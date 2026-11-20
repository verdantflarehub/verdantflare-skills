"""Private snapshot admission, races and no-write behavior of the packaged entry."""
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

TOOLS = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(TOOLS), str(TOOLS / 'installer_tools')]
import install_transaction as tx
import tool_name_diagnostic as diagnostic
import tool_name_profile as profile


class ToolNameDiagnosticTests(unittest.TestCase):
    def setUp(self):
        base = TOOLS.parents[2] / '.tmp/tool-name-diagnostic-tests'
        base.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=base)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / 'request.json'
        self.initial = {'_meta': {profile.PROFILE_KEY: profile.PROFILE}}
        mapping = b'project.list\tproject__list\n'
        self.directory = {'_meta': {profile.PROFILE_KEY: profile.PROFILE,
            profile.DIGEST_KEY: 'sha256:' + hashlib.sha256(mapping).hexdigest()},
            'tools': [{'name': 'project__list', 'description': 'VF canonical tool: project.list.',
                       'inputSchema': {'type': 'object'}}]}
        self.prepare()

    def write(self, name, raw):
        path = self.root / name
        path.write_bytes(raw)
        path.chmod(0o600)
        return path

    def prepare(self):
        raw_initial, raw_tools = (json.dumps(v).encode() for v in (self.initial, self.directory))
        self.write('initialize-result.json', raw_initial)
        self.write('tools-result.json', raw_tools)
        self.value = {'schema': diagnostic.SCHEMA, 'observed_prefix': 'mcp_vf_',
                      'initialize_sha256': hashlib.sha256(raw_initial).hexdigest(),
                      'tools_sha256': hashlib.sha256(raw_tools).hexdigest()}
        self.write('request.json', json.dumps(self.value).encode())

    def snapshot(self):
        return {p.name: (p.read_bytes(), p.stat().st_mode, p.stat().st_mtime_ns)
                for p in self.root.iterdir() if p.is_file()}

    def test_success_and_empty_directory_are_read_only_not_host_proof(self):
        for empty in (False, True):
            if empty:
                self.directory['tools'] = []
                self.directory['_meta'][profile.DIGEST_KEY] = 'sha256:' + hashlib.sha256(b'').hexdigest()
                self.prepare()
            before = self.snapshot()
            result = diagnostic.run(self.path)
            self.assertEqual(result['tool_count'], 0 if empty else 1)
            self.assertEqual(result['status'], 'local_snapshot_checked')
            for key in ('host_verified', 'server_authenticated', 'configuration_written',
                        'tool_invoked', 'desktop_acceptance', 'mcp_acceptance',
                        'installable', 'publisher_signature_verified', 'network_requested'):
                self.assertIs(result[key], False)
            self.assertEqual(before, self.snapshot())

    def test_old_service_is_refused_without_mutation(self):
        self.initial = {}
        self.prepare()
        before = self.snapshot()
        with self.assertRaisesRegex(profile.ProfileError, '^TOOL_NAME_PROFILE_UNSUPPORTED$'):
            diagnostic.run(self.path)
        self.assertEqual(before, self.snapshot())

    def test_raw_bytes_hash_mismatch_is_rejected(self):
        self.write('tools-result.json', b'{"private":"do-not-disclose"}')
        with self.assertRaisesRegex(tx.Conflict, '^TOOL_NAME_INPUT_HASH$'):
            diagnostic.run(self.path)

    def test_request_duplicates_unknown_fields_paths_and_size_are_rejected(self):
        raw = json.dumps(self.value)
        cases = [raw[:-1] + ',"schema":"' + diagnostic.SCHEMA + '"}',
                 json.dumps({**self.value, 'path': '/private-secret'}),
                 json.dumps({**self.value, 'observed_prefix': ''}),
                 json.dumps({**self.value, 'tools_sha256': 'bad'}),
                 '[' * 1000 + '0' + ']' * 1000, ' ' * 8193]
        for value in cases:
            with self.subTest(case=cases.index(value)):
                self.write('request.json', value.encode())
                before = self.snapshot()
                with self.assertRaises((tx.Conflict, profile.ProfileError)):
                    diagnostic.run(self.path)
                self.assertEqual(before, self.snapshot())

    def test_unsafe_file_metadata_and_links_are_refused(self):
        target = self.root / 'tools-result.json'
        original = target.read_bytes()
        for case in ('mode', 'hardlink', 'symlink', 'fifo', 'directory'):
            with self.subTest(case=case):
                target.unlink()
                if case == 'mode':
                    self.write(target.name, original).chmod(0o644)
                elif case == 'hardlink':
                    os.link(self.root / 'initialize-result.json', target)
                elif case == 'symlink':
                    target.symlink_to('initialize-result.json')
                elif case == 'fifo':
                    os.mkfifo(target, mode=0o600)
                else:
                    target.mkdir(mode=0o700)
                with self.assertRaises(tx.Conflict):
                    diagnostic.run(self.path)
                if case == 'directory':
                    target.rmdir()
                else:
                    target.unlink()
                self.write(target.name, original)

    def test_directory_mode_and_missing_directory_are_not_repaired(self):
        self.root.chmod(0o750)
        with self.assertRaisesRegex(tx.Conflict, '^PRIVATE_DIRECTORY_REQUIRED$'):
            diagnostic.run(self.path)
        self.assertEqual(self.root.stat().st_mode & 0o777, 0o750)
        self.root.chmod(0o700)
        missing = self.root / 'not-created' / 'request.json'
        with self.assertRaises(OSError):
            diagnostic.run(missing)
        self.assertFalse(missing.parent.exists())

    def test_change_between_result_reads_is_detected(self):
        actual = diagnostic._read
        reads = 0
        def raced(path, limit):
            nonlocal reads
            raw = actual(path, limit)
            reads += 1
            if reads == 3:
                self.write('initialize-result.json', b'{}')
            return raw
        with patch.object(diagnostic, '_read', side_effect=raced):
            with self.assertRaisesRegex(tx.Conflict, '^TOOL_NAME_INPUT_CHANGED$'):
                diagnostic.run(self.path)

    def test_fifo_swapped_before_open_cannot_block_or_be_read(self):
        original_open = os.open
        def raced(path, flags, *args, **kwargs):
            if Path(path) == self.path:
                self.path.unlink()
                os.mkfifo(self.path, mode=0o600)
            return original_open(path, flags, *args, **kwargs)
        with patch.object(diagnostic.os, 'open', side_effect=raced):
            with self.assertRaisesRegex(tx.Conflict, '^TOOL_NAME_INPUT_CHANGED$'):
                diagnostic.run(self.path)


if __name__ == '__main__':
    unittest.main()
