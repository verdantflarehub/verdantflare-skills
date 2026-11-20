"""Independent TOML parser oracles and preservation of unrelated byte slices."""
from pathlib import Path
import datetime
import sys
import tomllib
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import host_toml as t

DESIRED = {'url': 'https://mcp.invalid/v1', 'bearer_token_env_var': 'VF_TEST_TOKEN',
           'env_http_headers': {'X-Test': 'VF_TEST_HEADER'}, 'enabled_tools': ['project.list'],
           'startup_timeout_sec': 10, 'enabled': True}


class TomlPlanTests(unittest.TestCase):
    def plan(self, raw, desired=DESIRED, owned=None):
        return t.plan_update(raw, desired, expected_config_sha256=t.digest(raw), expected_owned_sha256=owned)

    def owned(self, raw):
        return t.digest(t.inspect(raw))

    def test_missing_empty_and_comments_create_without_foreign_rewrite(self):
        for raw in (None, b'', b'# user configuration\n', b'# user\r\nmodel = "test"\r\n'):
            with self.subTest(raw=raw):
                plan = self.plan(raw)
                self.assertTrue(plan.changed)
                if raw:
                    self.assertTrue(plan.after.startswith(raw))
                self.assertEqual(tomllib.loads(plan.after.decode())['mcp_servers']['vf-studio'], DESIRED)
                self.assertFalse(plan.receipt()['disk_written'])
                self.assertNotIn('https:', str(plan.receipt()))

    def test_dotted_quoted_and_inline_parent_layouts_preserve_other_servers(self):
        cases = [
            b'model = "test" # keep\n[mcp_servers.other]\nurl = "https://other.invalid" # keep\n',
            b'mcp_servers.other = { url = "https://other.invalid" } # keep\n',
            b'"mcp_servers"."other".url = "https://other.invalid" # keep\n',
            b'mcp_servers = { other = {url = "https://other.invalid"} } # keep\n',
            b'["mcp_servers"] # parent comment\nother = { url = "https://other.invalid" }\n',
        ]
        for raw in cases:
            with self.subTest(raw=raw):
                plan = self.plan(raw)
                parsed = tomllib.loads(plan.after.decode())
                self.assertEqual(parsed['mcp_servers']['other'], {'url': 'https://other.invalid'})
                self.assertEqual(parsed['mcp_servers']['vf-studio'], DESIRED)
                self.assertIn(b'# keep', plan.after) if b'# keep' in raw else None
                self.assertIn(b'https://other.invalid', plan.after)

    def test_update_owned_tables_and_inline_values(self):
        for raw in (
            b'[mcp_servers."vf-studio"] # owned\nurl = "old"\n\n[other]\nkeep = [1,2] # retain\n',
            b'mcp_servers.vf-studio = { url = "old" }\n# trailing user comment\n',
            b'mcp_servers = {other = {url="other"}, "vf-studio" = {url="old"}} # root\n',
        ):
            with self.subTest(raw=raw):
                plan = self.plan(raw, owned=self.owned(raw))
                self.assertEqual(tomllib.loads(plan.after.decode())['mcp_servers']['vf-studio'], DESIRED)
                if b'[other]' in raw:
                    self.assertIn(b'[other]\nkeep = [1,2] # retain\n', plan.after)
                if b'# trailing user comment' in raw:
                    self.assertIn(b'# trailing user comment', plan.after)

    def test_split_tables_update_delete_and_fragment_drift(self):
        for raw in (
            b'[mcp_servers.vf-studio.http_headers]\nX="x"\n[mcp_servers.other]\nurl="o" # keep\n[mcp_servers.vf-studio]\nurl="old"\n',
            b'[mcp_servers.vf-studio]\nurl="old"\n[mcp_servers.other]\nurl="o" # keep\n[mcp_servers.vf-studio.env_http_headers]\nX="X"\n',
        ):
            with self.subTest(raw=raw):
                original_owned = self.owned(raw)
                plan = self.plan(raw, owned=original_owned)
                self.assertEqual(tomllib.loads(plan.after.decode())['mcp_servers']['vf-studio'], DESIRED)
                self.assertIn(b'[mcp_servers.other]\nurl="o" # keep\n', plan.after)
                removed = self.plan(raw, None, owned=original_owned)
                self.assertEqual(tomllib.loads(removed.after.decode()), {'mcp_servers': {'other': {'url': 'o'}}})
                self.assertIn(b'[mcp_servers.other]\nurl="o" # keep\n', removed.after)
                drift = raw.replace(b'X="', b'X="changed-')
                with self.assertRaisesRegex(t.ConfigConflict, 'OWNERSHIP_CONFLICT'):
                    self.plan(drift, owned=original_owned)

    def test_noop_preserves_exact_node_and_file_format(self):
        raw = b"# keep\n[mcp_servers.'vf-studio']\nurl='https://mcp.invalid/v1' # owned comment\n"
        plan = self.plan(raw, {'url': 'https://mcp.invalid/v1'}, owned=self.owned(raw))
        self.assertFalse(plan.changed)
        self.assertEqual(plan.after, raw)
        self.assertEqual(plan.owned_after_sha256, self.owned(raw))

    def test_no_adoption_even_when_same_semantics_and_stale_hashes_refused(self):
        raw = b'[mcp_servers.vf-studio]\nurl="existing"\n'
        with self.assertRaisesRegex(t.ConfigConflict, 'OWNERSHIP_CONFLICT'):
            self.plan(raw, {'url': 'existing'})
        with self.assertRaisesRegex(t.ConfigConflict, 'CONFIG_CHANGED'):
            t.plan_update(raw, DESIRED, expected_config_sha256='0'*64, expected_owned_sha256=self.owned(raw))
        with self.assertRaisesRegex(t.ConfigConflict, 'OWNERSHIP_CONFLICT'):
            self.plan(raw, owned='0'*64)

    def test_delete_retains_other_servers_root_fields_and_comments(self):
        raw = b'# top\nmodel="test"\n[mcp_servers.other]\nurl="other" # retain\n[mcp_servers.vf-studio]\nurl="old"\n'
        plan = self.plan(raw, None, owned=self.owned(raw))
        parsed = tomllib.loads(plan.after.decode())
        self.assertEqual(parsed, {'model': 'test', 'mcp_servers': {'other': {'url': 'other'}}})
        self.assertTrue(plan.after.startswith(b'# top\nmodel="test"\n[mcp_servers.other]\nurl="other" # retain\n'))
        self.assertIsNone(plan.owned_after_sha256)
        self.assertFalse(self.plan(plan.after, None).changed)
        self.assertIsNone(self.plan(None, None).after)

    def test_nested_headers_quotes_unicode_arrays_and_empty_values(self):
        desired = {'url': 'https://mcp.invalid/"\\\n中文', 'http_headers': {'X-Quoted': 'a"b\\c'},
                   'enabled_tools': [], 'enabled': False, 'startup_timeout_sec': 0, 'tool_timeout_sec': 1.25}
        plan = self.plan(b'# keep\n', desired)
        self.assertEqual(tomllib.loads(plan.after.decode())['mcp_servers']['vf-studio'], desired)
        empty = self.plan(plan.after, {}, owned=plan.owned_after_sha256)
        self.assertEqual(tomllib.loads(empty.after.decode())['mcp_servers']['vf-studio'], {})

    def test_toml_lookalikes_multiline_dates_infinity_and_nan_are_preserved(self):
        raw = b'''title="""[mcp_servers.vf-studio]
url='fake'
"""
date=1979-05-27T07:32:00Z
value=nan
infinity=inf
literal=''' + b"'''mcp_servers.vf-studio = {url='fake'}'''\n"
        plan = self.plan(raw)
        self.assertTrue(plan.after.startswith(raw))
        parsed = tomllib.loads(plan.after.decode())
        self.assertEqual(parsed['date'], datetime.datetime(1979, 5, 27, 7, 32, tzinfo=datetime.timezone.utc))
        self.assertIn("url='fake'", parsed['title'])
        self.assertEqual(parsed['mcp_servers']['vf-studio'], DESIRED)

    def test_invalid_utf8_duplicate_keys_redefinitions_and_non_tables_refused(self):
        for raw in (b'\xff', b'x=1\nx=2\n', b'[mcp_servers.vf-studio]\nurl="a"\nurl="b"\n',
                    b'mcp_servers=1\n', b'mcp_servers={vf-studio=1}\n', b'[[mcp_servers.vf-studio]]\nurl="a"\n',
                    b'[mcp_servers.vf-studio]\nurl="secret-value"\n[mcp_servers.vf-studio]\n'):
            with self.subTest(raw=raw), self.assertRaises(t.ConfigConflict) as raised:
                self.plan(raw)
            self.assertNotIn('secret-value', str(raised.exception))

    def test_unknown_desired_types_nonfinite_integer_limits_and_cycles_refused(self):
        cyclic = {}; cyclic['cycle'] = cyclic
        for desired in ({'x': None}, {'x': float('nan')}, {'x': float('inf')}, {1: 'value'},
                        {'x': 2**63}, {'x': datetime.date.today()}, cyclic, []):
            with self.subTest(kind=type(desired).__name__), self.assertRaises(t.ConfigConflict):
                self.plan(None, desired)
        with self.assertRaisesRegex(t.ConfigConflict, 'TOML_SIZE_OR_TYPE'):
            self.plan(b'#'*(t.MAX_BYTES+1))

    def test_external_vf_extension_and_missing_or_wrong_dependency_do_not_edit(self):
        from unittest.mock import patch
        original = self.plan(None, {'url': 'old'})
        changed = original.after + b'[mcp_servers.vf-studio.http_headers]\nX-User="keep"\n'
        with self.assertRaisesRegex(t.ConfigConflict, 'OWNERSHIP_CONFLICT'):
            self.plan(changed, owned=original.owned_after_sha256)
        library = t._library()
        with patch.object(library, '__version__', 'unreviewed'):
            with self.assertRaisesRegex(t.ConfigConflict, 'TOML_DEPENDENCY_VERSION_UNVERIFIED'):
                self.plan(None)
        with patch.dict(sys.modules, {'tomlkit': None}):
            with self.assertRaisesRegex(t.ConfigConflict, 'TOML_DEPENDENCY_REQUIRED'):
                self.plan(None)

    def test_subtable_before_explicit_parent_updates_without_losing_dotted_siblings(self):
        fixtures = [
            b'[mcp_servers.vf-studio.http_headers]\nX = "1"\n[mcp_servers]\nother.url = "o" # preserve other\nvf-studio.url = "old"\n',
            b'[mcp_servers.vf-studio.http_headers]\r\nX = "1"\r\n[mcp_servers]\r\nother.url = "o" # preserve other\r\nvf-studio.url = "old"\r\n',
            b'[mcp_servers.vf-studio.http_headers]\nX = "1"\n[mcp_servers]\nother.url = "o" # preserve other\nvf-studio.url = "old"\n[mcp_servers.third]\nurl="third" # preserve third\n',
            b'mcp_servers.other.url = "o" # preserve other\nmcp_servers.vf-studio.url = "old"\n[mcp_servers.vf-studio.http_headers]\nX="1"\n',
        ]
        for raw in fixtures:
            for desired in ({'url': 'https://new.invalid'}, {'url': 'new', 'http_headers': {'X': 'next'}}, {}):
                with self.subTest(raw=raw[:60], desired=desired):
                    before = tomllib.loads(raw.decode('utf-8'))
                    plan = t.plan_update(raw, desired, expected_config_sha256=t.digest(raw),
                                         expected_owned_sha256=t.digest(t.inspect(raw)))
                    parsed = tomllib.loads(plan.after.decode('utf-8'))
                    self.assertEqual(parsed['mcp_servers']['vf-studio'], desired)
                    self.assertEqual(parsed['mcp_servers']['other'], before['mcp_servers']['other'])
                    self.assertIn(b'other.url = "o" # preserve other', plan.after)
                    if 'third' in before['mcp_servers']:
                        self.assertEqual(parsed['mcp_servers']['third'], before['mcp_servers']['third'])
                        self.assertIn(b'url="third" # preserve third', plan.after)
                    no_op = t.plan_update(plan.after, desired, expected_config_sha256=t.digest(plan.after),
                                          expected_owned_sha256=plan.owned_after_sha256)
                    self.assertFalse(no_op.changed)
                    self.assertEqual(no_op.after, plan.after)


if __name__ == '__main__':
    unittest.main()
