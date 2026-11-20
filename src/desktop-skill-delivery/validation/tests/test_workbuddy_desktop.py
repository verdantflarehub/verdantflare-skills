"""Adversarial checks for the read-only desktop observation boundary."""
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import probe_workbuddy_desktop as probe


class Response(io.BytesIO):
    status = 200

    def getheader(self, name, default=None):
        return 'application/json; charset=utf-8'


class DesktopObservationTests(unittest.TestCase):
    def call_response(self, raw, status=200):
        response = Response(raw)
        response.status = status
        with patch.object(probe.http.client, 'HTTPConnection') as factory:
            factory.return_value.getresponse.return_value = response
            result = probe.read_probe(probe.PORTS[0])
            factory.assert_called_once_with('127.0.0.1', probe.PORTS[0], timeout=3)
            factory.return_value.request.assert_called_once_with(
                'GET', probe.PROBE_PATH, headers={'Connection': 'close'})
            factory.return_value.close.assert_called_once()
        return result

    def test_redirect_never_followed(self):
        with self.assertRaisesRegex(probe.Error, 'DESKTOP_RESPONSE_STATUS'):
            self.call_response(b'', 302)

    def test_duplicate_response_identity_rejected(self):
        with self.assertRaisesRegex(probe.Error, 'DUPLICATE_KEY'):
            self.call_response(b'{"ok":true,"ok":false}')

    def test_wrong_product_version_and_boolean_rejected(self):
        valid = {'ok': True, 'app': 'workbuddy-desktop', 'version': '5.11.3', 'platform': 'darwin'}
        for field, value in [('ok', 1), ('app', 'other'), ('version', '5.11.4'), ('platform', 'linux')]:
            with self.subTest(field=field), self.assertRaisesRegex(probe.Error, 'MISMATCH'):
                self.call_response(json.dumps({**valid, field: value}).encode())
        self.assertEqual(len(self.call_response(json.dumps(valid).encode())), 64)

    def test_response_size_bounded(self):
        with self.assertRaisesRegex(probe.Error, 'RESPONSE_SIZE'):
            self.call_response(b' ' * (probe.MAX_BODY + 1))

    def test_only_fixed_candidate_ports(self):
        for port in [True, 0, 443, 18491, '18488']:
            with self.subTest(port=port), patch.object(probe.http.client, 'HTTPConnection') as factory:
                with self.assertRaises(probe.Error):
                    probe.read_probe(port)
                factory.assert_not_called()

    def test_multi_listener_and_wildcard_rejected(self):
        for raw in ['p1\nf1\nn*:18488\n', 'p1\nf1\nn127.0.0.1:18488\np2\nf1\nn127.0.0.1:18488\n']:
            with patch.object(probe, 'command', return_value=raw), self.assertRaises(probe.Error):
                probe.listener(18488)

    def test_lookalike_process_rejected(self):
        with patch.object(probe, 'command', return_value='p1\nftxt\nn/other/Electron\n'):
            with self.assertRaisesRegex(probe.Error, 'PROCESS_MISMATCH'):
                probe.process_snapshot('1', Path('/expected/Electron'))

    def test_signature_requires_vendor_and_failure_stops_network(self):
        with patch.object(probe, 'command', side_effect=probe.Error('REJECTED')) as command:
            with self.assertRaises(probe.Error):
                probe.verify_signature(Path('/example/WorkBuddy.app'))
            requirement = command.call_args.args[0][-2]
            self.assertIn('anchor apple generic', requirement)
            self.assertIn(probe.TEAM, requirement)
            self.assertIn(probe.BUNDLE, requirement)
        with patch.object(probe.platform, 'system', return_value='Darwin'), \
             patch.object(probe.discovery, 'inspect_application', return_value=self.fixture_application()), \
             patch.object(probe, 'verify_signature', side_effect=probe.Error('REJECTED')), \
             patch.object(probe, 'read_probe') as network:
            with self.assertRaises(probe.Error):
                probe.observe(Path('/example/WorkBuddy.app'))
            network.assert_not_called()

    def fixture_application(self):
        return probe.discovery.Application(Path('/example/WorkBuddy.app'), {
            'bundle_id': probe.BUNDLE, 'version': probe.VERSION, 'build': probe.BUILD,
            'plist_sha256': probe.PINNED_PLIST, 'executable_sha256': probe.PINNED_EXECUTABLE})

    def run_mock_observation(self, application, listeners, process_starts):
        with patch.object(probe.platform, 'system', return_value='Darwin'), \
             patch.object(probe.discovery, 'inspect_application', return_value=application), \
             patch.object(probe.discovery, 'read_regular', return_value=(None, b'plist', None)), \
             patch.object(probe.plistlib, 'loads', return_value={'CFBundleExecutable': 'Electron'}), \
             patch.object(probe.discovery, 'revalidate'), \
             patch.object(probe, 'verify_signature'), \
             patch.object(probe, 'listener', side_effect=listeners), \
             patch.object(probe, 'process_snapshot', side_effect=process_starts), \
             patch.object(probe, 'read_probe', return_value='a' * 64):
            return probe.observe(application.path)

    def test_process_changes_rejected(self):
        with self.assertRaisesRegex(probe.Error, 'PROCESS_CHANGED'):
            self.run_mock_observation(self.fixture_application(), ['1', '2'], ['start'])
        with self.assertRaisesRegex(probe.Error, 'PROCESS_CHANGED'):
            self.run_mock_observation(self.fixture_application(), ['1', '1'], ['start', 'changed'])

    def test_running_observation_never_becomes_acceptance(self):
        result = self.run_mock_observation(self.fixture_application(), ['1', '1', None, None], ['start', 'start'])
        self.assertEqual(result['status'], 'desktop_running_observed')
        for key in ['process_attested', 'host_verified', 'desktop_acceptance', 'mcp_acceptance',
                    'configuration_written', 'credentials_read', 'app_started']:
            self.assertIs(result[key], False)
        self.assertNotIn('/example', json.dumps(result))

    def test_receipt_never_overwrites_or_follows_symlink(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory).resolve() / 'receipt.json'
            probe.save_receipt(output, {'safe': True})
            self.assertEqual(output.stat().st_mode & 0o777, 0o600)
            with self.assertRaises(FileExistsError):
                probe.save_receipt(output, {'changed': True})
            target = Path(directory).resolve() / 'link.json'
            target.symlink_to(output)
            with self.assertRaises(FileExistsError):
                probe.save_receipt(target, {'changed': True})
            self.assertEqual(json.loads(output.read_bytes()), {'safe': True})


if __name__ == '__main__':
    unittest.main()
