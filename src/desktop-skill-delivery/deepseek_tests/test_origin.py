"""Origin lifecycle gates; actual native roundtrips live in test_schema_graph."""
from __future__ import annotations

import copy
from dataclasses import replace
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import host_deepseek as h
import host_deepseek_origin as o
import test_host_deepseek as fixtures


class OriginTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.DeepSeekPlanningTests()
        self.fixture.setUp()
        f = self.fixture
        f.desc['user'] = {}
        f.desc['base'] = {'servers': {'other': copy.deepcopy(f.other)}}
        f.desc['value'] = copy.deepcopy(f.desc['base'])
        self.before = h.encode(f.desc)
        self.install = f.plan()
        self.installed = f.installed(self.install)
        self.installed['user']['servers']['other'] = copy.deepcopy(f.other)
        self.after = h.encode(self.installed)
        self.origin = o.bind_origin(self.install, f.identity, self.before, self.after,
                                    writable=True, registered=['plugin'])

    def plan(self, desired=None, origin=None, desc=None, identity=None, registered=None):
        return o.plan_owned(self.origin if origin is None else origin,
            identity or self.fixture.identity, self.after if desc is None else h.encode(desc),
            writable=True, registered=['plugin'] if registered is None else registered, desired=desired)

    def removed(self):
        desc = copy.deepcopy(self.installed)
        desc['revision'] += 1
        desc['user'] = {}
        del desc['value']['servers'][h.KEY]
        return desc

    def verify(self, plan, desc):
        return o.verify_and_advance(self.origin, plan, self.fixture.identity, self.after,
                                    h.encode(desc), writable=True, registered=['plugin'])

    def test_origin_contains_only_hashes_identity_and_exact_empty_flag(self):
        receipt = self.origin.receipt()
        self.assertEqual(receipt['original_user_sha256'], h.digest({}))
        self.assertTrue(receipt['original_user_empty'])
        self.assertFalse(receipt['persistent_ledger_written'])
        encoded = json.dumps(receipt)
        for private in ('PRIVATE_TEST_VALUE', 'unrelated', 'credential', 'backend-one', 'profile-one'):
            self.assertNotIn(private, encoded)
        self.assertNotIn('PRIVATE_TEST_VALUE', repr(self.origin))
        with self.assertRaises(AttributeError): self.origin.owned_sha256 = '0' * 64

    def test_first_install_rejects_known_irreversible_shapes_before_set(self):
        first = o.plan_first_install(self.fixture.identity, self.before, writable=True,
                                     registered=['plugin'], desired=self.fixture.target)
        self.assertEqual(first, self.install)
        explicit = h.read(self.before)
        explicit['user'] = copy.deepcopy(explicit['base'])
        with self.assertRaisesRegex(h.Conflict, 'DEEPSEEK_NATIVE_UNSET_REQUIRES_LAYER_RESTORE'):
            o.plan_first_install(self.fixture.identity, h.encode(explicit), writable=True,
                                 registered=['plugin'], desired=self.fixture.target)
        empty = h.read(self.before)
        empty['value'] = empty['base'] = {'servers': {}}
        allowed = o.plan_first_install(self.fixture.identity, h.encode(empty), writable=True,
                                       registered=['plugin'], desired=self.fixture.target)
        self.assertEqual(allowed.mutation()[1][0]['op'], 'set')
        empty['base'] = {'servers': {'hidden': copy.deepcopy(self.fixture.other)}}
        with self.assertRaisesRegex(h.Conflict, 'DEEPSEEK_EMPTY_ROOT_RESTORE_UNVERIFIED'):
            o.plan_first_install(self.fixture.identity, h.encode(empty), writable=True,
                                 registered=['plugin'], desired=self.fixture.target)

    def test_no_origin_keeps_legacy_guard_and_owned_origin_restores_exact_empty_layer(self):
        with self.assertRaisesRegex(h.Conflict, 'DEEPSEEK_NATIVE_UNSET_REQUIRES_LAYER_RESTORE'):
            h.plan_update(self.fixture.identity, self.after, writable=True, registered=['plugin'],
                          desired=None, expected_owned_sha256=self.origin.owned_sha256)
        plan = self.plan()
        self.assertEqual(plan.mutation()[1], [{'op': 'unset', 'path': h.PATH}])
        retired, receipt = self.verify(plan, self.removed())
        self.assertIsNone(retired)
        self.assertTrue(receipt['original_user_layer_restored'])
        self.assertFalse(receipt['origin_record_retained'])

    def test_origin_requires_first_install_and_verified_pair_not_existing_adoption(self):
        with self.assertRaises(h.Conflict):
            o.bind_origin(self.install, self.fixture.identity, self.after, self.after,
                          writable=True, registered=['plugin'])
        wrong = copy.deepcopy(self.installed)
        wrong['user']['servers'][h.KEY]['url'] = 'https://other.invalid/mcp'
        with self.assertRaises(h.Conflict):
            o.bind_origin(self.install, self.fixture.identity, self.before, h.encode(wrong),
                          writable=True, registered=['plugin'])
        with self.assertRaisesRegex(h.Conflict, 'DEEPSEEK_ORIGIN_FIRST_INSTALL_REQUIRED'):
            o.bind_origin(replace(self.install, changed=False), self.fixture.identity,
                          self.before, self.after, writable=True, registered=['plugin'])

    def test_origin_record_fields_and_missing_record_are_rejected(self):
        for bad in (None, {}, replace(self.origin, owned_sha256='0'),
                    replace(self.origin, original_user_empty=1),
                    replace(self.origin, original_user_empty=False),
                    replace(self.origin, identity=None)):
            with self.subTest(record=type(bad).__name__), self.assertRaises(h.Conflict):
                o.plan_owned(bad, self.fixture.identity, self.after, writable=True,
                             registered=['plugin'], desired=None)

    def test_origin_never_crosses_profile_entry_or_backend_generation(self):
        for field in ('profile', 'entry', 'backend_generation'):
            identity = replace(self.fixture.identity, **{field: 'different'})
            desc = copy.deepcopy(self.installed)
            desc['ns'] = identity.entry
            with self.subTest(field=field), self.assertRaisesRegex(h.Conflict, 'DEEPSEEK_ORIGIN_CONTEXT_CHANGED'):
                self.plan(identity=identity, desc=desc)

    def test_schema_inheritance_registry_and_secret_context_cannot_drift(self):
        for change in (lambda d: d['base']['servers']['other'].update(command='CHANGED'),
                       lambda d: d['schema'].update(description='CHANGED'),
                       lambda d: d['secrets'][0].update(set=False),
                       lambda d: d['value']['servers']['other'].update(command='CHANGED')):
            desc = copy.deepcopy(self.installed)
            change(desc)
            with self.assertRaisesRegex(h.Conflict, 'DEEPSEEK_ORIGIN_CONTEXT_CHANGED'): self.plan(desc=desc)
        with self.assertRaisesRegex(h.Conflict, 'DEEPSEEK_ORIGIN_CONTEXT_CHANGED'):
            self.plan(registered=['another-plugin'])

    def test_user_materialization_change_is_refused_even_if_effective_value_is_unchanged(self):
        for change in (lambda s: s['other'].update(enabled=True),
                       lambda s: s.pop('other'),
                       lambda s: s.update(another=copy.deepcopy(self.fixture.other))):
            desc = copy.deepcopy(self.installed)
            change(desc['user']['servers'])
            with self.assertRaisesRegex(h.Conflict, 'DEEPSEEK_ORIGIN_USER_CHANGED'): self.plan(desc=desc)

    def test_owned_node_or_effective_connection_cannot_be_changed(self):
        for layer in ('user', 'value'):
            desc = copy.deepcopy(self.installed)
            desc[layer]['servers'][h.KEY]['url'] = 'https://changed.invalid/mcp'
            with self.subTest(layer=layer), self.assertRaises(h.Conflict): self.plan(desc=desc)

    def test_original_explicit_empty_mapping_does_not_authorize_collapse(self):
        # Correctly shaped record for a nonempty original root must still refuse.
        explicit = replace(self.origin, original_user_sha256=h.digest({'servers': {}}),
                           original_user_empty=False)
        with self.assertRaisesRegex(h.Conflict, 'DEEPSEEK_NATIVE_UNSET_REQUIRES_LAYER_RESTORE'):
            self.plan(origin=explicit)

    def test_delete_failure_retains_origin_and_does_not_claim_exact_restoration(self):
        plan = self.plan()
        for user in ({'servers': {}}, {'servers': {'other': copy.deepcopy(self.fixture.other)}}):
            desc = self.removed()
            desc['user'] = user
            with self.assertRaisesRegex(h.Conflict, 'DEEPSEEK_ORIGINAL_USER_LAYER_NOT_RESTORED'):
                self.verify(plan, desc)
        self.assertEqual(self.origin.original_user_sha256, h.digest({}))
        self.assertTrue(self.origin.receipt()['original_user_empty'])

    def test_forged_plan_or_stale_submit_cannot_release_record(self):
        plan = self.plan()
        for bad in (replace(plan, restored_user_sha256=None), replace(plan, expected_revision=999),
                    replace(plan, protected_digest='0' * 64), replace(plan, changed=False)):
            with self.assertRaisesRegex(h.Conflict, 'DEEPSEEK_ORIGIN_PLAN_MISMATCH'):
                self.verify(bad, self.removed())
        changed = copy.deepcopy(self.installed)
        changed['revision'] += 1
        with self.assertRaisesRegex(h.Conflict, 'DEEPSEEK_PRECONDITION_CHANGED'):
            h.validate_submit(plan, self.fixture.identity, h.encode(changed),
                              writable=True, registered=['plugin'])

    def test_removal_readback_must_confirm_revision_and_effective_absence(self):
        plan = self.plan()
        desc = self.removed()
        desc['revision'] = self.installed['revision']
        with self.assertRaisesRegex(h.Conflict, 'DEEPSEEK_READBACK_REVISION'): self.verify(plan, desc)
        desc = self.removed()
        desc['value']['servers'][h.KEY] = copy.deepcopy(self.fixture.target)
        with self.assertRaisesRegex(h.Conflict, 'DEEPSEEK_REMOVAL_NOT_EFFECTIVE'): self.verify(plan, desc)

    def test_update_advance_requires_old_chain_and_preserves_original_digest(self):
        target = h.desired_connection('https://studio.invalid/updated-mcp', 'NEW_REF')
        update = self.plan(desired=target)
        after = copy.deepcopy(self.installed)
        after['revision'] += 1
        after['user']['servers'][h.KEY] = target
        after['value']['servers'][h.KEY] = {**copy.deepcopy(h.DEFAULTS), **target}
        advanced, receipt = self.verify(update, after)
        self.assertEqual(advanced.original_user_sha256, self.origin.original_user_sha256)
        self.assertNotEqual(advanced.owned_sha256, self.origin.owned_sha256)
        self.assertTrue(receipt['origin_record_retained'])
        with self.assertRaisesRegex(h.Conflict, 'DEEPSEEK_OWNERSHIP_CONFLICT'): self.plan(desc=after)
        remove = self.plan(origin=advanced, desc=after)
        final = copy.deepcopy(after)
        final['revision'] += 1
        final['user'] = {}
        del final['value']['servers'][h.KEY]
        cleared, receipt = o.verify_and_advance(advanced, remove, self.fixture.identity,
            h.encode(after), h.encode(final), writable=True, registered=['plugin'])
        self.assertIsNone(cleared)
        self.assertTrue(receipt['original_user_layer_restored'])

    def test_noop_update_does_not_forge_revision_or_skip_readback(self):
        plan = self.plan(desired=self.fixture.target)
        self.assertFalse(plan.changed)
        advanced, receipt = self.verify(plan, self.installed)
        self.assertEqual(advanced, self.origin)
        self.assertTrue(receipt['configuration_readback_verified'])
        changed = copy.deepcopy(self.installed)
        changed['revision'] += 1
        with self.assertRaisesRegex(h.Conflict, 'DEEPSEEK_READBACK_REVISION'): self.verify(plan, changed)

    def test_empty_origin_container_cleanup_is_one_mutation_and_exact_readback(self):
        before = h.read(self.before)
        before['base'] = before['value'] = {'servers': {}}
        first = o.plan_first_install(self.fixture.identity, h.encode(before), writable=True,
                                     registered=['plugin'], desired=self.fixture.target)
        installed = copy.deepcopy(before)
        installed['revision'] += 1
        installed['user'] = {'servers': {h.KEY: copy.deepcopy(self.fixture.target)}}
        installed['value'] = copy.deepcopy(installed['user'])
        ledger = o.bind_origin(first, self.fixture.identity, h.encode(before), h.encode(installed),
                               writable=True, registered=['plugin'])
        remove = o.plan_owned(ledger, self.fixture.identity, h.encode(installed),
                              writable=True, registered=['plugin'], desired=None)
        self.assertTrue(remove.empty_servers_cleanup)
        self.assertEqual(remove.mutation(), (self.fixture.identity.entry,
            [{'op': 'unset', 'path': h.PATH}, {'op': 'unset', 'path': ['servers']}], installed['revision']))
        final = copy.deepcopy(before)
        final['revision'] = installed['revision'] + 1
        retired, receipt = o.verify_and_advance(ledger, remove, self.fixture.identity,
            h.encode(installed), h.encode(final), writable=True, registered=['plugin'])
        self.assertIsNone(retired)
        self.assertTrue(receipt['empty_servers_cleanup_planned'])
        self.assertTrue(receipt['original_user_layer_restored'])
        with self.assertRaisesRegex(h.Conflict, 'DEEPSEEK_ORIGIN_PLAN_MISMATCH'):
            o.verify_and_advance(ledger, replace(remove, empty_servers_cleanup=False), self.fixture.identity,
                h.encode(installed), h.encode(final), writable=True, registered=['plugin'])

    def test_parent_cleanup_flag_cannot_be_used_for_nonempty_or_unbound_plan(self):
        remove = self.plan()
        for bad in (replace(remove, empty_servers_cleanup=True),
                    replace(self.install, empty_servers_cleanup=True),
                    replace(remove, empty_servers_cleanup=1)):
            with self.assertRaisesRegex(h.Conflict, 'DEEPSEEK_EMPTY_CONTAINER_PLAN_INVALID'):
                bad.mutation()

    def test_original_explicit_empty_user_layer_is_not_claimed_as_our_container(self):
        before = h.read(self.before)
        before['user'] = {'servers': {}}
        before['base'] = before['value'] = {'servers': {}}
        with self.assertRaisesRegex(h.Conflict, 'DEEPSEEK_EXPLICIT_EMPTY_LAYER_RESTORE_UNVERIFIED'):
            o.plan_first_install(self.fixture.identity, h.encode(before), writable=True,
                                 registered=['plugin'], desired=self.fixture.target)


if __name__ == '__main__':
    unittest.main()
