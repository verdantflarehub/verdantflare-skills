"""Real official authorization/Gateway/credential modules in an isolated carrier.

These checks do not obtain authority from, connect to, or accept a desktop.
The journal consumes the recorded native chain; it does not orchestrate it yet.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))
import host_deepseek as h
import host_deepseek_journal as journal


class OfficialManagementTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        supplied = os.environ.get('VF_DEEPSEEK_NATIVE_MODULES')
        if not supplied:
            raise AssertionError('Explicit pinned VF_DEEPSEEK_NATIVE_MODULES required')
        cls.modules = Path(supplied).resolve()
        cls.repo = TOOLS.parents[2]
        cls.script = TOOLS / 'validation/probe_deepseek_management.mjs'
        cls.node = shutil.which('node')
        cls.sandbox = shutil.which('sandbox-exec')
        if not cls.node or not cls.sandbox:
            raise AssertionError('macOS Node and external network-denial sandbox required')
        cls.temp = tempfile.TemporaryDirectory(prefix='deepseek-management-',
            dir=cls.repo / '.tmp/agent-skill-delivery')
        cls.addClassCleanup(cls.temp.cleanup)
        cls.root = Path(cls.temp.name)
        result = cls.invoke(cls.modules, cls.root / 'native')
        if result.returncode:
            raise AssertionError('Pinned official management chain failed: ' + result.stderr)
        cls.receipt = json.loads((cls.root / 'native/receipt.json').read_text())
        cls.descriptors = json.loads((cls.root / 'native/descriptors.json').read_text())

    @classmethod
    def invoke(cls, modules, output, *, write=True):
        command = [cls.sandbox, '-p', '(version 1)(allow default)(deny network*)', cls.node,
                   '--experimental-permission', '--allow-fs-read=' + str(modules),
                   '--allow-fs-read=' + str(cls.root), '--allow-fs-read=' + str(cls.script)]
        if write:
            command.append('--allow-fs-write=' + str(cls.root))
        command += [str(cls.script), str(modules), str(output)]
        return subprocess.run(command, cwd=cls.repo, text=True, capture_output=True, timeout=90,
                              env={'PATH': str(Path(cls.node).parent), 'LANG': 'en_US.UTF-8'})

    def test_official_authorization_config_credentials_and_replacement(self):
        self.assertEqual(self.receipt['vendor_files'], 18986)
        self.assertEqual(self.receipt['source_receipt_sha256'],
                         '38434fe1574d170064080ab6b13d09755fa77ac6a2478fadad979c1e0aa1fe0b')
        self.assertEqual(len(self.receipt['cases']), 9)
        self.assertTrue(all(row['passed'] is True for row in self.receipt['cases']))
        self.assertEqual(self.receipt['transport'], 'in-memory-http-carrier')
        self.assertTrue(self.receipt['official_credentials_provider'])
        for key in ('webserver_started', 'desktop_started', 'user_profile_accessed',
                    'mcp_manager_instantiated', 'desktop_acceptance'):
            self.assertIs(self.receipt[key], False)
        self.assertEqual(self.descriptors['removed']['user'], self.descriptors['before']['user'])

    def test_native_management_chain_matches_persistent_origin_rules(self):
        identity = h.Identity('com.deepseek.dsh', '0.2.2-rc.3', 'macos', 'arm64', h.DMG_SHA256,
            h.SETTINGS_SHA256, 'isolated-management-fixture', 'probe-profile', 'probe')
        root = self.root / 'journal'; root.mkdir(mode=0o700)
        ledger = journal.OriginJournal(root, identity)
        d = self.descriptors
        # Descriptors are obtained through the official Gateway and generated codecs.
        # Replay their observations through journal rules; no transport is executed here.
        for before, after in (('before', 'installed'), ('installed', 'updated'), ('updated', 'removed')):
            desired = d[after]['user'].get('servers', {}).get('vf-studio')
            plan, operation, pending = ledger.prepare(ledger.snapshot()[1], identity, h.encode(d[before]),
                writable=True, registered=[], desired=desired)
            self.assertTrue(plan.changed)
            result = ledger.resolve(pending, operation, identity,
                h.encode(d[after]), writable=True, registered=[])
            self.assertTrue(result['persistent_ledger_written'])
        self.assertIsNone(ledger.snapshot()[0]['origin'])

    def test_changed_source_receipt_is_rejected_before_output(self):
        altered = self.root / 'altered-source'; altered.mkdir()
        (altered / 'receipt.json').write_bytes((self.modules / 'receipt.json').read_bytes() + b'\n')
        output = self.root / 'rejected'
        result = self.invoke(altered, output)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stderr, 'DEEPSEEK_NATIVE_MANAGEMENT_PROBE_FAILED\n')
        self.assertFalse(output.exists())

    def test_missing_write_permission_does_not_create_profile(self):
        output = self.root / 'no-write-permission'
        result = self.invoke(self.modules, output, write=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stderr, 'DEEPSEEK_NATIVE_MANAGEMENT_PROBE_FAILED\n')
        self.assertFalse(output.exists())


if __name__ == '__main__':
    unittest.main()
