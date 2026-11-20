"""Configuration planning behavior only; never reads a real host config."""
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import host_config as h


class HostConfigTests(unittest.TestCase):
    def update(self, raw, desired, owned=None):
        return h.plan_update(raw, desired, expected_config_sha256=h.digest(raw), expected_owned_sha256=owned)

    def owned(self, raw):
        return h.digest(h.inspect(raw)[-1])

    def test_new_missing_file_and_missing_parent(self):
        for raw in (None, b'{}', b'{ \n }', b'{"theme": "dark"}\n'):
            with self.subTest(raw=raw):
                plan = self.update(raw, {'url': 'https://example.invalid/mcp'})
                result = json.loads(plan.after)
                self.assertEqual(result['mcpServers']['vf-studio']['url'], 'https://example.invalid/mcp')
                if raw and b'theme' in raw:
                    self.assertTrue(plan.after.startswith(b'{"theme": "dark",'))
                    self.assertTrue(plan.after.endswith(b'}\n'))
                self.assertFalse(plan.receipt()['disk_written'])
                self.assertFalse(plan.receipt()['host_verified'])

    def test_existing_user_bytes_crlf_unicode_and_spelling_preserved(self):
        raw = '{\r\n "note": "雪",\r\n "mcpServers" : { "other" : {"url":"https:\\/\\/example.invalid","n":1e2}  },\r\n "tail" : [true,null] }\r\n'.encode()
        plan = self.update(raw, {'url': 'https://vf.invalid/mcp'})
        insertion = b',"vf-studio":{"url":"https://vf.invalid/mcp"}'
        self.assertEqual(plan.after.replace(insertion, b''), raw)
        self.assertEqual(json.loads(plan.after)['mcpServers']['other'], json.loads(raw)['mcpServers']['other'])

    def test_replace_only_owned_value_then_noop_and_bool_number_distinction(self):
        raw = b'{ "mcpServers" : {"vf-studio" : { "enabled" : true }, "other":{} }, "tail":42 }'
        plan = self.update(raw, {'enabled': False}, self.owned(raw))
        self.assertEqual(plan.after, raw.replace(b'{ "enabled" : true }', b'{"enabled":false}'))
        same = self.update(raw, {'enabled': True}, self.owned(raw))
        self.assertFalse(same.changed)
        self.assertEqual(same.after, raw)
        number = self.update(raw, {'enabled': 1}, self.owned(raw))
        self.assertTrue(number.changed)
        self.assertIs(type(json.loads(number.after)['mcpServers']['vf-studio']['enabled']), int)

    def test_unowned_existing_entry_never_adopted_even_same_value(self):
        raw = b'{"mcpServers":{"vf-studio":{"url":"already-here"}}}'
        with self.assertRaisesRegex(h.ConfigConflict, '^OWNERSHIP_CONFLICT$'):
            self.update(raw, {'url':'already-here'})
        with self.assertRaisesRegex(h.ConfigConflict, '^OWNERSHIP_CONFLICT$'):
            self.update(b'{}', {}, h.digest(b'{}'))

    def test_file_and_owned_changes_both_rejected(self):
        before = b'{"mcpServers":{"vf-studio":{}}}'
        changed = b'{"mcpServers":{"vf-studio":{}},"user":1}'
        with self.assertRaisesRegex(h.ConfigConflict, '^CONFIG_CHANGED$'):
            h.plan_update(changed, {}, expected_config_sha256=h.digest(before), expected_owned_sha256=self.owned(before))
        changed_owned = b'{"mcpServers":{"vf-studio":{ "user":1 }}}'
        with self.assertRaisesRegex(h.ConfigConflict, '^OWNERSHIP_CONFLICT$'):
            self.update(changed_owned, {}, self.owned(before))

    def test_delete_first_middle_last_and_only_entry_preserves_other_spans(self):
        cases = [
            (b'{"mcpServers":{ "vf-studio":{}, "a" : 1 }}', b'{"mcpServers":{ "a" : 1 }}'),
            (b'{"mcpServers":{ "a":1, "vf-studio":{}, "b" : 2 }}', b'{"mcpServers":{ "a":1, "b" : 2 }}'),
            (b'{"mcpServers":{ "a":1, "vf-studio":{} }}', b'{"mcpServers":{ "a":1 }}'),
            (b'{"mcpServers":{ "vf-studio":{} }, "keep":true}', b'{"mcpServers":{  }, "keep":true}'),
        ]
        for raw, expected in cases:
            with self.subTest(raw=raw):
                plan = self.update(raw, None, self.owned(raw))
                self.assertEqual(plan.after, expected)
                self.assertIsNone(plan.owned_after_sha256)
                self.assertNotIn('vf-studio', json.loads(plan.after)['mcpServers'])

    def test_absent_delete_does_not_create_file_or_parent(self):
        for raw in (None, b'{}\n', b'{"other":1}', b'{"mcpServers":{}}'):
            plan = self.update(raw, None)
            self.assertFalse(plan.changed)
            self.assertEqual(plan.after, raw)

    def test_reject_ambiguous_invalid_or_unsupported_input(self):
        invalid = [b'', b'[]', b'null', b'{"mcpServers":null}', b'{"mcpServers":[]}',
                   b'{"a":1,"a":2}', b'{"a":{"b":1,"b":2}}',
                   b'{"mcpServers":{"vf-studio":{},"vf-\\u0073tudio":{}}}',
                   b'{"x":NaN}', b'{"x":Infinity}', b'{"x":1e999}', b'{"x":"\\ud800"}',
                   b'{"\\udfff":0}', b'{"x":1,}', b'{"x":[1,]}', b'{//comment\n}',
                   b'{} {}', b'\xef\xbb\xbf{}', b'{"x":"\xff"}', b'[mcp_servers]']
        for raw in invalid:
            with self.subTest(raw=raw), self.assertRaises(h.ConfigConflict):
                self.update(raw, {})

    def test_size_depth_and_invalid_desired_refused(self):
        for raw in (b' ' * (h.MAX_BYTES+1), b'{"x":'+b'['*66+b'0'+b']'*66+b'}'):
            with self.assertRaises(h.ConfigConflict):
                self.update(raw, {})
        for desired in ([], {'x': float('nan')}, {'x': object()}, {'x': '\udfff'}):
            with self.subTest(kind=type(desired).__name__), self.assertRaises(h.ConfigConflict):
                self.update(None, desired)
        with self.assertRaises(h.ConfigConflict):
            self.update(None, {'x': 'a' * h.MAX_BYTES})

    def test_receipt_repr_and_errors_do_not_disclose_config(self):
        secret = 'SYNTHETIC_DO_NOT_LOG_abc'
        raw = json.dumps({'mcpServers': {'other': {'token': secret}}}).encode()
        plan = self.update(raw, {'credential_ref': 'test-reference'})
        self.assertNotIn(secret, json.dumps(plan.receipt()))
        self.assertNotIn(secret, repr(plan))
        bad = ('{"x":"'+secret+'"}extra').encode()
        with self.assertRaises(h.ConfigConflict) as caught:
            self.update(bad, {})
        self.assertEqual(str(caught.exception), 'CONFIG_INVALID_JSON')
        self.assertIsNone(caught.exception.__cause__)

    def test_escaped_parent_name_is_updated_without_renaming(self):
        raw = b'{"mcp\\u0053ervers":{"vf-studio" : {"a":1}}, "mcpServersOther":{}}'
        plan = self.update(raw, {'a':2}, self.owned(raw))
        self.assertEqual(plan.after, raw.replace(b'{"a":1}', b'{"a":2}'))


if __name__ == '__main__':
    unittest.main()
