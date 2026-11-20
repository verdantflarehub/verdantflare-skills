"""Protocol rejection and snapshot selection, without a network or live host."""
from dataclasses import FrozenInstanceError
import hashlib
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import tool_name_profile as p


def raw(value):
    return json.dumps(value, ensure_ascii=True).encode('utf-8')


def fixture(pairs=(('project.list', 'project__list'), ('a_b.c', 'a_ub__c'), ('a.b_c', 'a__b_uc'))):
    digest = 'sha256:' + hashlib.sha256(''.join(a + '\t' + b + '\n' for a, b in sorted(pairs)).encode()).hexdigest()
    initial = {'_meta': {p.PROFILE_KEY: 'portable-v1'}, 'protocolVersion': '2025-03-26'}
    listed = {'_meta': {p.PROFILE_KEY: 'portable-v1', p.DIGEST_KEY: digest},
              'tools': [{'name': b, 'description': 'Useful tool.\nVF canonical tool: ' + a + '.',
                         'inputSchema': {'type': 'object', 'properties': {'note': {'type': 'string'}}}}
                        for a, b in pairs]}
    return initial, listed


class ProfileTests(unittest.TestCase):
    def admit(self, initial=None, listed=None, prefix='mcp_vf_'):
        a, b = fixture()
        return p.admit(raw(a if initial is None else initial), raw(b if listed is None else listed),
                       observed_prefix=prefix)

    def test_real_names_and_collision_counterexample_are_exact(self):
        admitted = self.admit()
        self.assertEqual(admitted.resolve('project.list'), 'project__list')
        self.assertEqual(admitted.resolve('a_b.c'), 'a_ub__c')
        self.assertEqual(admitted.resolve('a.b_c'), 'a__b_uc')
        self.assertEqual(p.portable_name('music.generate_instrumental'), 'music__generate_uinstrumental')
        self.assertEqual(p.portable_name('a__.b__c'), 'a_u_u__b_u_uc')
        receipt = admitted.receipt()
        self.assertEqual(receipt['tool_count'], 3)
        for key in ('server_authenticated', 'host_verified', 'configuration_written', 'tool_invoked',
                    'desktop_acceptance', 'mcp_acceptance'):
            self.assertIs(receipt[key], False)

    def test_recorded_hundred_tool_catalogue_is_complete_and_pinned(self):
        snapshot = json.loads((Path(__file__).parent / 'fixtures/studio-tool-names.json').read_bytes())
        names = snapshot['names']
        self.assertEqual(len(names), 100)
        self.assertEqual(names, sorted(set(names)))
        self.assertEqual(hashlib.sha256(('\n'.join(names) + '\n').encode('ascii')).hexdigest(),
                         snapshot['names_sha256'])
        initial, listed = fixture(tuple((name, p.portable_name(name)) for name in names))
        self.assertEqual(listed['_meta'][p.DIGEST_KEY], snapshot['expected_portable_map_sha256'])
        admitted = self.admit(initial, listed)
        self.assertEqual(max(len(name) for _, name in admitted.entries), snapshot['expected_max_portable_length'])
        self.assertEqual(admitted.receipt()['tool_count'], 100)
        self.assertFalse(snapshot['desktop_acceptance'])

    def test_catalogue_order_does_not_change_map_or_edit_input(self):
        initial, listed = fixture()
        before = raw(listed)
        first = self.admit(initial, listed)
        self.assertEqual(raw(listed), before)
        listed['tools'].reverse()
        self.assertEqual(first, self.admit(initial, listed))

    def test_old_service_ignoring_header_and_mismatched_confirmations_refuse(self):
        for location in ('initial', 'list'):
            for metadata in (None, {}, {p.PROFILE_KEY: 'canonical'}, {p.PROFILE_KEY: ['portable-v1']}):
                initial, listed = fixture()
                (initial if location == 'initial' else listed)['_meta'] = metadata
                with self.subTest(location=location, metadata=metadata), self.assertRaisesRegex(
                        p.ProfileError, '^TOOL_NAME_PROFILE_UNSUPPORTED$'):
                    self.admit(initial, listed)

    def test_whole_directory_refuses_one_invalid_or_duplicate_tool(self):
        for tool, code in (({'name': 'bad'}, 'TOOL_NAME_MARKER_INVALID'),
                           ({'name': 'project.list', 'description': 'VF canonical tool: project.list.'},
                            'TOOL_NAME_MAPPING_MISMATCH'),
                           ({'name': 'project__list', 'description': 'VF canonical tool: project.list.'},
                            'TOOL_NAME_MAPPING_DUPLICATE')):
            _, listed = fixture()
            listed['tools'].append(tool)
            with self.subTest(code=code), self.assertRaisesRegex(p.ProfileError, '^' + code + '$'):
                self.admit(listed=listed)

    def test_digest_missing_wrong_case_and_stale_catalogue_refuse(self):
        for digest in (None, '', 'sha256:' + 'A' * 64, 'sha256:' + '0' * 64):
            _, listed = fixture(); listed['_meta'][p.DIGEST_KEY] = digest
            with self.subTest(digest=digest), self.assertRaises(p.ProfileError):
                self.admit(listed=listed)
        _, listed = fixture(); listed['tools'].pop()
        with self.assertRaisesRegex(p.ProfileError, '^TOOL_NAME_MAP_DIGEST_MISMATCH$'):
            self.admit(listed=listed)

    def test_markers_cannot_be_ambiguous_or_trailing_or_noncanonical(self):
        invalid = ('VF canonical tool: project.list.\n', 'VF canonical tool: project.list. extra',
                   'VF canonical tool: project.list.\nVF canonical tool: project.list.',
                   'xVF canonical tool: project.list.', 'VF canonical tool: Project.list.',
                   'VF canonical tool: a.b.c.', 'VF canonical tool: project.\u200blist.')
        for description in invalid:
            _, listed = fixture(); listed['tools'][0]['description'] = description
            with self.subTest(description=description), self.assertRaisesRegex(
                    p.ProfileError, '^TOOL_NAME_MARKER_INVALID$'):
                self.admit(listed=listed)

    def test_independent_portable_and_actual_host_length_limits(self):
        canonical = 'a.' + 'b' * 45
        initial, listed = fixture(((canonical, 'a__' + 'b' * 45),))
        self.assertEqual(self.admit(initial, listed, 'p' * 16).receipt()['max_visible_name_length'], 64)
        with self.assertRaisesRegex(p.ProfileError, '^TOOL_NAME_HOST_LIMIT$'):
            self.admit(initial, listed, 'p' * 17)
        with self.assertRaisesRegex(p.ProfileError, '^TOOL_NAME_PORTABLE_LIMIT$'):
            p.portable_name(canonical + 'b')
        for prefix in ('', 'p.' * 5, 'p' * 64, '\n', None):
            with self.subTest(prefix=prefix), self.assertRaisesRegex(
                    p.ProfileError, '^TOOL_NAME_HOST_PREFIX_REQUIRED$'):
                self.admit(prefix=prefix)

    def test_empty_directory_is_admitted_without_inventing_capability(self):
        initial, listed = fixture(())
        self.assertEqual(listed['_meta'][p.DIGEST_KEY],
                         'sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855')
        admitted = self.admit(initial, listed)
        self.assertEqual(admitted.receipt()['tool_count'], 0)
        with self.assertRaisesRegex(p.ProfileError, '^TOOL_NAME_NOT_DISCOVERED$'):
            admitted.resolve('project.list')

    def test_new_snapshot_does_not_reuse_removed_tool_or_decode_unknown_alias(self):
        old = self.admit()
        initial, listed = fixture((('a_b.c', 'a_ub__c'),))
        current = self.admit(initial, listed)
        self.assertNotEqual(old.mapping_sha256, current.mapping_sha256)
        for name in ('project.list', 'a.b_c'):
            with self.assertRaisesRegex(p.ProfileError, '^TOOL_NAME_NOT_DISCOVERED$'):
                current.resolve(name)
        with self.assertRaisesRegex(p.ProfileError, '^TOOL_NAME_CANONICAL_INVALID$'):
            current.resolve('project__list')
        with self.assertRaises(FrozenInstanceError):
            current.entries = old.entries

    def test_duplicate_json_keys_nonfinite_unicode_and_bounds_refuse(self):
        initial, listed = fixture()
        for broken in (b'{"tools":[],"tools":[]}', b'{"x":NaN}', b'{"x":Infinity}', b'{"x":1e400}',
                       b'\xff', b'{"x":"\\ud800"}', b'{"\\ud800":1}', b'[]', b'{}' * 2,
                       b'{' + b'"a":[' + b'[' * 65 + b'0' + b']' * 66 + b'}',
                       b' ' * (p.MAX_BYTES + 1)):
            with self.subTest(size=len(broken)), self.assertRaises(p.ProfileError) as error:
                p.admit(raw(initial), broken, observed_prefix='mcp_vf_')
            self.assertRegex(str(error.exception), r'^TOOL_NAME_[A-Z_]+$')

    def test_pagination_never_claims_complete_map(self):
        for cursor in ('next', '', None):
            _, listed = fixture(); listed['nextCursor'] = cursor
            with self.subTest(cursor=cursor), self.assertRaisesRegex(
                    p.ProfileError, '^TOOL_NAME_DIRECTORY_INCOMPLETE$'):
                self.admit(listed=listed)

    def test_diagnostics_do_not_echo_remote_description_or_sensitive_properties(self):
        _, listed = fixture()
        listed['tools'][0]['description'] = 'UNTRUSTED_REMOTE_SECRET_FIXTURE'
        with self.assertRaises(p.ProfileError) as error:
            self.admit(listed=listed)
        self.assertEqual(str(error.exception), 'TOOL_NAME_MARKER_INVALID')
        listed['tools'][0]['description'] = 'UNTRUSTED_REMOTE_SECRET_FIXTURE\nVF canonical tool: project.list.'
        admitted = self.admit(listed=listed)
        self.assertNotIn('UNTRUSTED_REMOTE_SECRET_FIXTURE', repr(admitted) + json.dumps(admitted.receipt()))


if __name__ == '__main__':
    unittest.main()
