"""WorkBuddy adapter and isolated disk transactions, never real user roots."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))
import host_workbuddy as w
import host_config as config
import install_transaction as transaction

URL = 'https://studio.example.invalid/mcp/desktop/connection_1'
NEXT_URL = 'https://studio.example.invalid/mcp/desktop/connection_2'
BASE = TOOLS.parents[2] / '.tmp/workbuddy-config-tests'


def desired(url=URL):
    return w.desired_connection(url, admitted_url=url)


class WorkBuddyPlanTests(unittest.TestCase):
    def plan(self, raw, value, owned=None, url=URL):
        return w.plan_update(raw, value, admitted_url=url,
                             expected_config_sha256=config.digest(raw),
                             expected_owned_sha256=owned)

    def test_minimal_remote_fields_and_no_secret_or_command(self):
        self.assertEqual(desired(), {'url': URL, 'timeout': 30000, 'disabled': False})
        plan = self.plan(None, desired())
        self.assertEqual(json.loads(plan.after), {'mcpServers': {'vf-studio': desired()}})
        self.assertFalse(plan.receipt()['host_verified'])
        self.assertFalse(plan.receipt()['disk_written'])

    def test_url_rejects_ambiguous_and_non_connection_targets(self):
        bad = [None, 1, b'https://example.invalid', '', URL + '?', URL + '#',
               URL + '/', URL + '/child', URL + '%2fchild', URL + '\\child',
               URL + '\n', URL + '\x00', URL + '\x7f', URL + '${TOKEN}',
               URL.replace('https:', 'http:'), URL.replace('https:', 'HTTPS:'),
               URL.replace('studio.', 'Studio.'), URL.replace('/mcp/desktop/', '/mcp/'),
               URL.replace('/connection_1', '/..'), URL.replace('/connection_1', '/.'),
               URL.replace('studio.', 'user:secret@studio.'),
               URL.replace('.invalid/', '.invalid:0/'),
               URL.replace('.invalid/', '.invalid:65536/'),
               URL.replace('.invalid/', '.invalid:443/'),
               URL.replace('.invalid/', '.invalid:0443/'),
               URL.replace('.invalid/', '.invalid:/'),
               URL.replace('.invalid/', '.invalid./'),
               URL.replace('studio.', '-studio.'),
               URL.replace('studio.', 'stüdio.'),
               URL.replace('studio.example.invalid', '[not-ip]'),
               *[URL.replace('studio.example.invalid', host) for host in
                 ['0177.0.0.1', '127.0.0.01', '0x7f.0.0.1', '2130706433', '127.1', '0x7f000001', '0x']],
               URL + 'a' * w.MAX_URL]
        for url in bad:
            with self.subTest(index=bad.index(url)), self.assertRaisesRegex(
                    w.Conflict, '^WORKBUDDY_URL_INVALID$'):
                w.desired_connection(url, admitted_url=URL)

    def test_canonical_dns_ipv4_ipv6_and_explicit_port(self):
        for url in [URL, URL.replace('.invalid/', '.invalid:8443/'),
                    URL.replace('studio.example.invalid', '127.0.0.1'),
                    URL.replace('studio.example.invalid', '[::1]')]:
            self.assertEqual(w.desired_connection(url, admitted_url=url)['url'], url)

    def test_exact_connection_binding_not_just_origin(self):
        for url in [NEXT_URL, URL.replace('studio.', 'other.'),
                    URL.replace('.invalid/', '.invalid:8443/')]:
            with self.assertRaisesRegex(w.Conflict, '^WORKBUDDY_CONNECTION_MISMATCH$'):
                w.desired_connection(url, admitted_url=URL)

    def test_invalid_admitted_descriptor_never_becomes_trust(self):
        with self.assertRaisesRegex(w.Conflict, '^WORKBUDDY_URL_INVALID$'):
            self.plan(None, None, url='https://studio.example.invalid/mcp')

    def test_unknown_fields_and_type_confusion_rejected(self):
        variants = [[], {}, {**desired(), 'command': 'run'},
                    {**desired(), 'headers': {'Authorization': 'fixture-not-a-token'}},
                    {**desired(), 'env': {}}, {**desired(), 'type': 'sse'},
                    {**desired(), 'oauth': {}}, {**desired(), 'timeout': 30000.0},
                    {**desired(), 'timeout': True}, {**desired(), 'timeout': 30},
                    {**desired(), 'disabled': 0}, {**desired(), 'disabled': 'false'},
                    {**desired(), 'disabled': True}]
        for i, value in enumerate(variants):
            with self.subTest(index=i), self.assertRaisesRegex(
                    w.Conflict, '^WORKBUDDY_DESIRED_INVALID$'):
                self.plan(None, value)

    def test_no_adoption_even_when_entry_already_matches(self):
        raw = json.dumps({'mcpServers': {'vf-studio': desired()}}).encode()
        with self.assertRaisesRegex(config.ConfigConflict, '^OWNERSHIP_CONFLICT$'):
            self.plan(raw, desired())

    def test_replace_owned_entry_removes_old_execution_and_headers(self):
        raw = b'{ "mcpServers":{"other": {"command":"keep"},"vf-studio": {"command":"old","headers":{"Authorization":"fixture"}}},"tail":1e2}\r\n'
        owned = config.inspect(raw)[-1]
        plan = self.plan(raw, desired(), config.digest(owned))
        expected = json.dumps(desired(), separators=(',', ':')).encode()
        self.assertEqual(plan.after, raw.replace(owned, expected))
        self.assertNotIn('headers', json.loads(plan.after)['mcpServers']['vf-studio'])
        self.assertFalse(self.plan(plan.after, desired(), plan.owned_after_sha256).changed)

    def test_delete_and_duplicate_keys_keep_existing_protection(self):
        raw = b'{"mcpServers":{"other":{},"vf-studio":{}},"theme":true}'
        owned = config.digest(config.inspect(raw)[-1])
        self.assertEqual(self.plan(raw, None, owned).after,
                         b'{"mcpServers":{"other":{}},"theme":true}')
        with self.assertRaises(config.ConfigConflict):
            self.plan(b'{"mcpServers":{},"mcpServers":{}}', desired())

    def test_stale_whole_file_cas_rejected(self):
        with self.assertRaisesRegex(config.ConfigConflict, '^CONFIG_CHANGED$'):
            w.plan_update(b'{}\n', desired(), admitted_url=URL,
                          expected_config_sha256=config.digest(b'{}'),
                          expected_owned_sha256=None)


@unittest.skipUnless(os.name == 'posix', 'Windows storage is a separate plan')
class WorkBuddyDiskTests(unittest.TestCase):
    def setUp(self):
        BASE.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=BASE)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / 'mcp.json'
        self.original = b'{ "mcpServers": {"other":{"command":"keep"}}, "theme":"dark" }\r\n'
        self.path.write_bytes(self.original)
        self.path.chmod(0o640)
        self.engine = w.ConfigFile(self.root, admitted_url=URL)

    def apply(self, value, engine=None, **kwargs):
        engine = engine or self.engine
        observation = engine.observe()
        return engine.apply(value, expected_revision=observation['ledger_revision'],
                            expected_file=observation['file'], **kwargs)

    def test_disk_install_update_remove_preserves_user_edits_and_mode(self):
        self.assertFalse(self.apply(desired())['host_verified'])
        self.path.write_bytes(self.path.read_bytes().replace(b'dark', b'light'))
        updated = w.ConfigFile(self.root, admitted_url=NEXT_URL)
        self.apply(desired(NEXT_URL), updated)
        self.assertEqual(json.loads(self.path.read_bytes())['mcpServers']['vf-studio'], desired(NEXT_URL))
        self.apply(None, updated)
        self.assertEqual(self.path.read_bytes(), self.original.replace(b'dark', b'light'))
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o640)

    def test_wrong_connection_and_static_secret_rejected_without_config_change(self):
        snapshot = self.engine.snapshot()
        for value in [desired(NEXT_URL), {**desired(), 'headers': {'Authorization': 'fixture'}}]:
            with self.assertRaises(w.Conflict):
                self.apply(value)
            self.assertEqual(self.path.read_bytes(), self.original)
            self.assertEqual(self.engine.snapshot(), snapshot)

    def test_config_race_and_owned_external_edit_rejected(self):
        observed = self.engine.observe()
        self.path.write_bytes(self.original + b'\n')
        with self.assertRaisesRegex(transaction.Conflict, '^CONFIG_CHANGED$'):
            self.engine.apply(desired(), expected_revision=observed['ledger_revision'],
                              expected_file=observed['file'])
        self.apply(desired())
        edited = self.path.read_bytes().replace(b'connection_1', b'external')
        self.path.write_bytes(edited)
        with self.assertRaisesRegex(config.ConfigConflict, '^OWNERSHIP_CONFLICT$'):
            self.apply(None)
        self.assertEqual(self.path.read_bytes(), edited)

    def test_recover_same_transaction_after_actual_process_exit(self):
        ledger = self.engine.snapshot()
        script = '''import os,sys
sys.path.insert(0,sys.argv[1]);import host_workbuddy as w
url=sys.argv[3];e=w.ConfigFile(sys.argv[2],admitted_url=url);o=e.observe()
def stop(point):
 if point=='config':os._exit(77)
e.apply(w.desired_connection(url,admitted_url=url),expected_revision=o['ledger_revision'],expected_file=o['file'],checkpoint=stop)
'''
        result = subprocess.run([sys.executable, '-I', '-B', '-c', script,
                                 str(TOOLS), str(self.root), URL], capture_output=True, timeout=20)
        self.assertEqual(result.returncode, 77, 'Isolated transaction did not reach its crash checkpoint')
        self.assertNotEqual(self.path.read_bytes(), self.original)
        identifier = self.engine._active()['id']
        resumed = w.ConfigFile(self.root, admitted_url=URL)
        restored = resumed.recover(transaction_id=identifier)
        self.assertEqual(restored['transaction'], identifier)
        self.assertEqual(restored['status'], 'config_bytes_restored')
        self.assertEqual(self.path.read_bytes(), self.original)
        self.assertEqual(resumed.snapshot(), ledger)

    def test_committed_update_compensation_restores_previous_target_bytes(self):
        self.apply(desired())
        before = self.path.read_bytes()
        updated = w.ConfigFile(self.root, admitted_url=NEXT_URL)
        result = self.apply(desired(NEXT_URL), updated)
        restored = updated.rollback(result['transaction'], expected_revision=result['revision'])
        self.assertFalse(restored['host_verified'])
        self.assertEqual(self.path.read_bytes(), before)


@unittest.skipUnless(os.environ.get('VF_WORKBUDDY_CONNECTOR_SOURCE'),
                     'Fixed official consumer source must be supplied explicitly')
class WorkBuddyNativeTests(unittest.TestCase):
    def test_fixed_official_parser_reads_actual_adapter_output(self):
        source = Path(os.environ['VF_WORKBUDDY_CONNECTOR_SOURCE'])
        self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(),
                         '06756026779070ba66d04233a5507085cbb710689b34f8962b138393214dbe71')
        plan = w.plan_update(None, desired(), admitted_url=URL,
                             expected_config_sha256=None, expected_owned_sha256=None)
        node = json.loads(plan.after)['mcpServers']['vf-studio']
        result = subprocess.run(['node', str(Path(__file__).with_name('native_consumer.cjs')), str(source)],
                                input=json.dumps(node).encode(), capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, 'Pinned native consumer rejected adapter output')
        self.assertEqual(json.loads(result.stdout), {'native_config_consumed': True,
                         'desktop_acceptance': 'not_run', 'business_acceptance': 'not_run'})


if __name__ == '__main__':
    unittest.main()
