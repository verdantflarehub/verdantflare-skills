"""Wire-schema graph safety and actual Settings/ConfigEditor/Loader consumers."""
from __future__ import annotations

import copy
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
import host_deepseek_origin as origin
import host_deepseek_journal as journal
import test_host_deepseek as fixtures
schema = fixtures.schema


def graph(tree, offset=1):
    refs = {}
    def visit(node):
        uid = len(refs) + offset
        refs[str(uid)] = None
        result = {k: copy.deepcopy(v) for k, v in node.items() if k not in ('dict', 'list', 'inner', 'sKey')}
        if 'dict' in node: result['dict'] = {k: visit(v) for k, v in node['dict'].items()}
        if 'list' in node: result['list'] = [visit(v) for v in node['list']]
        for key in ('inner', 'sKey'):
            if key in node: result[key] = visit(node[key])
        refs[str(uid)] = result
        return uid
    root = visit(tree)
    return {'uid': root, 'refs': refs}


class SchemaGraphTests(unittest.TestCase):
    def test_wire_graph_renumbering_is_semantic_noop_and_input_is_detached(self):
        f = fixtures.DeepSeekPlanningTests(); f.setUp()
        f.desc['schema'] = graph(schema())
        before = copy.deepcopy(f.desc)
        plan = f.plan()
        f.desc['schema'] = graph(schema(), 500)
        self.assertEqual(h.validate_submit(plan, f.identity, h.encode(f.desc), writable=True,
                                          registered=['plugin']), plan.mutation())
        after = f.installed(plan)
        after['schema'] = graph(schema(), 900)
        h.verify_readback(plan, f.identity, h.encode(after), writable=True, registered=['plugin'])
        self.assertNotEqual(before['schema'], f.desc['schema'])
        self.assertEqual(h.normalize_schema(before['schema']), schema())

    def test_semantic_schema_change_is_not_hidden_by_renumbering(self):
        f = fixtures.DeepSeekPlanningTests(); f.setUp(); f.desc['schema'] = graph(schema())
        plan = f.plan(); changed = schema(); changed['dict']['servers']['inner']['meta']['description'] = 'changed'
        f.desc['schema'] = graph(changed, 300)
        with self.assertRaisesRegex(h.Conflict, 'DEEPSEEK_PRECONDITION_CHANGED'):
            h.validate_submit(plan, f.identity, h.encode(f.desc), writable=True, registered=['plugin'])

    def test_missing_boolean_string_and_fractional_refs_are_rejected(self):
        for ref in (True, False, '2', 2.5, -1, h.SAFE_INT + 1, 888):
            g = graph(schema()); g['refs']['1']['dict']['servers'] = ref
            with self.subTest(ref=ref), self.assertRaisesRegex(h.Conflict, 'DEEPSEEK_SCHEMA_REFERENCE_INVALID'):
                h.normalize_schema(g)

    def test_invalid_graph_structure_unreachable_refs_and_cycles_are_rejected(self):
        for change in (lambda g: g.update(extra=True), lambda g: g.update(refs=[]),
                       lambda g: g['refs'].update({'01': {}}), lambda g: g['refs'].update({'4': None})):
            g = graph(schema()); change(g)
            with self.assertRaisesRegex(h.Conflict, 'DEEPSEEK_SCHEMA_INVALID'): h.normalize_schema(g)
        g = graph(schema()); g['refs']['999'] = {'meta': {'role': 'secret'}}
        with self.assertRaisesRegex(h.Conflict, 'DEEPSEEK_SCHEMA_UNREACHABLE'): h.normalize_schema(g)
        g = graph(schema()); g['refs']['3']['inner'] = 1
        with self.assertRaisesRegex(h.Conflict, 'DEEPSEEK_SCHEMA_CYCLE'): h.normalize_schema(g)

    def test_shared_dag_expansion_and_deep_graph_are_bounded(self):
        refs = {str(i): {'list': [i + 1, i + 1]} for i in range(15)}
        refs['15'] = {'type': 'string', 'meta': {}}
        with self.assertRaisesRegex(h.Conflict, 'DEEPSEEK_SCHEMA_LIMIT'):
            h.normalize_schema({'uid': 0, 'refs': refs})
        refs = {str(i): {'inner': i + 1} for i in range(65)}; refs['65'] = {}
        with self.assertRaisesRegex(h.Conflict, 'DEEPSEEK_SCHEMA_LIMIT'):
            h.normalize_schema({'uid': 0, 'refs': refs})

    def test_small_shared_graph_cannot_expand_large_metadata_past_byte_budget(self):
        for text in ('x' * 16384, '\u754c' * 8192):
            leaf = {'type': 'string', 'meta': {'description': text}}
            wire = {'uid': 0, 'refs': {'0': {'list': [1] * 128}, '1': leaf}}
            before = copy.deepcopy(wire)
            self.assertLess(len(h.encode(wire)), h.json_config.MAX_BYTES // 16)
            # Only 129 visits and depth one, but over 2 MiB of output metadata.
            with self.subTest(non_ascii=not text.isascii()), self.assertRaisesRegex(
                    h.Conflict, 'DEEPSEEK_SCHEMA_LIMIT'):
                h.normalize_schema(wire)
            self.assertEqual(wire, before)
            wire['refs']['0']['list'] = [1] * 4
            self.assertEqual(h.normalize_schema(wire), {'list': [leaf] * 4})

    def test_secret_role_in_referenced_union_child_or_dict_key_is_not_ignored(self):
        for location in ('ancestor', 'field', 'union', 'key'):
            tree = schema(); node = tree['dict']['servers']
            if location == 'ancestor': node['meta']['role'] = 'secret'
            if location == 'field': node['inner']['dict']['headers'] = {'type': 'string', 'meta': {'role': 'secret'}}
            if location == 'union': node['inner']['list'] = [{'meta': {'role': 'secret'}}]
            if location == 'key': node['sKey'] = {'meta': {'role': 'secret'}}
            f = fixtures.DeepSeekPlanningTests(); f.setUp(); f.desc['schema'] = graph(tree)
            with self.subTest(location=location), self.assertRaisesRegex(h.Conflict, 'DEEPSEEK_OWNED_SECRET_HIDDEN'): f.plan()

    def test_non_schema_numbers_and_callbacks_are_preserved_without_evaluation(self):
        tree = schema(); tree['dict']['servers']['inner']['meta']['default'] = {'threshold': 12, 'uid': 9, 'refs': {}}
        tree['dict']['servers']['inner']['callback'] = 'throw new Error("DO_NOT_EXECUTE")'
        self.assertEqual(h.normalize_schema(graph(tree)), tree)


class ActualNativeEditorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        extracted = os.environ.get('VF_DEEPSEEK_NATIVE_MODULES')
        if not extracted: raise AssertionError('Explicit pinned VF_DEEPSEEK_NATIVE_MODULES required')
        repo = TOOLS.parents[2]
        cls.temp = tempfile.TemporaryDirectory(prefix='deepseek-native-tests-', dir=repo / '.tmp/agent-skill-delivery')
        cls.addClassCleanup(cls.temp.cleanup)
        script = TOOLS / 'validation/probe_deepseek_editor.mjs'
        node = shutil.which('node')
        if not node: raise AssertionError('Node required')
        cmd = [node, '--experimental-permission', '--allow-fs-read=' + str(Path(extracted).resolve()),
               '--allow-fs-read=' + cls.temp.name, '--allow-fs-read=' + str(script),
               '--allow-fs-write=' + cls.temp.name, str(script), str(Path(extracted).resolve()),
               str(Path(cls.temp.name) / 'result')]
        sandbox = shutil.which('sandbox-exec')
        if not sandbox: raise AssertionError('macOS network-denial sandbox required')
        cmd = [sandbox, '-p', '(version 1)(allow default)(deny network*)', *cmd]
        cp = subprocess.run(cmd, cwd=repo, text=True, capture_output=True, timeout=90,
                            env={'PATH': str(Path(node).parent), 'LANG': 'en_US.UTF-8'})
        if cp.returncode: raise AssertionError('Pinned native editor probe failed: ' + cp.stderr)
        output = Path(cls.temp.name) / 'result'
        cls.receipt = json.loads((output / 'receipt.json').read_text())
        cls.descriptors = json.loads((output / 'descriptors.json').read_text())

    def identity(self):
        return h.Identity('com.deepseek.dsh', '0.2.2-rc.3', 'macos', 'arm64', h.DMG_SHA256,
                          h.SETTINGS_SHA256, 'isolated-native-fixture', 'probe-profile', 'probe')

    def plan(self, descriptor, desired, owned=None):
        return h.plan_update(self.identity(), h.encode(descriptor), writable=True, registered=[],
                             desired=desired, expected_owned_sha256=owned)

    def test_actual_schema_and_renumbered_preflight_match_native_mutation_and_readback(self):
        d = self.descriptors; target = d['set_arguments'][1][0]['value']
        self.assertIn('refs', d['before']['schema'])
        self.assertNotEqual(d['before']['schema']['uid'], d['fresh']['schema']['uid'])
        plan = self.plan(d['before'], target)
        namespace, operations, revision = h.validate_submit(plan, self.identity(), h.encode(d['fresh']), writable=True, registered=[])
        self.assertEqual([namespace, operations, revision], d['set_arguments'])
        result = h.verify_readback(plan, self.identity(), h.encode(d['after']), writable=True, registered=[])
        self.assertTrue(result['configuration_readback_verified'])
        self.assertFalse(result['desktop_acceptance'])

    def test_actual_native_collapse_remains_guarded_before_mutation(self):
        d = self.descriptors; target = d['set_arguments'][1][0]['value']
        install = self.plan(d['collapse_before'], target)
        h.verify_readback(install, self.identity(), h.encode(d['collapse_installed']), writable=True, registered=[])
        self.assertTrue(d['collapse_before']['user'])
        self.assertEqual(d['collapse_after']['user'], {})
        with self.assertRaisesRegex(h.Conflict, 'DEEPSEEK_NATIVE_UNSET_REQUIRES_LAYER_RESTORE'):
            self.plan(d['collapse_installed'], None, h.digest(target))

    def test_actual_explicit_user_dictionary_masks_inherited_and_survives_set_unset(self):
        d = self.descriptors; target = d['set_arguments'][1][0]['value']
        plan = self.plan(d['mixed_before'], target)
        h.verify_readback(plan, self.identity(), h.encode(d['mixed_after']), writable=True, registered=[])
        remove = self.plan(d['mixed_after'], None, h.digest(target))
        h.verify_readback(remove, self.identity(), h.encode(d['mixed_removed']), writable=True, registered=[])
        self.assertEqual(d['mixed_before']['user'], d['mixed_removed']['user'])

    def test_native_file_rollback_schema_overlay_revision_and_entry_guards_ran(self):
        self.assertEqual(len(self.receipt['cases']), 15)
        self.assertTrue(all(c['passed'] for c in self.receipt['cases']))
        self.assertFalse(self.receipt['manager_instantiated'])
        self.assertFalse(self.receipt['mcp_connections_started'])
        cases = {c['name']: c for c in self.receipt['cases']}
        self.assertTrue(cases['reconcile-failure-rollback']['native_write_observed_before_failure'])
        self.assertTrue(cases['reconcile-failure-rollback']['exact_bytes_restored'])
        self.assertTrue(cases['external-revision-rejection']['external_file_preserved'])
        self.assertTrue(cases['replaced-entry-before-write']['replaced_entry_rejected'])

    def bind(self, before, after):
        target = after['user']['servers'][h.KEY]
        plan = self.plan(before, target)
        return origin.bind_origin(plan, self.identity(), h.encode(before), h.encode(after),
                                  writable=True, registered=[])

    def test_actual_inherited_materialization_is_removed_only_with_verified_empty_origin(self):
        d = self.descriptors
        ledger = self.bind(d['before'], d['after'])
        self.assertTrue(ledger.original_user_empty)
        with self.assertRaisesRegex(h.Conflict, 'DEEPSEEK_NATIVE_UNSET_REQUIRES_LAYER_RESTORE'):
            self.plan(d['after'], None, ledger.owned_sha256)
        remove = origin.plan_owned(ledger, self.identity(), h.encode(d['after']),
                                   writable=True, registered=[], desired=None)
        self.assertEqual(remove.mutation()[1], [{'op': 'unset', 'path': h.PATH}])
        cleared, receipt = origin.verify_and_advance(ledger, remove, self.identity(),
            h.encode(d['after']), h.encode(d['materialization_removed']), writable=True, registered=[])
        self.assertIsNone(cleared)
        self.assertTrue(receipt['original_user_layer_restored'])
        self.assertFalse(receipt['persistent_ledger_written'])
        self.assertFalse(receipt['desktop_acceptance'])

    def test_actual_explicit_equal_layer_remains_refused_even_with_origin(self):
        d = self.descriptors
        ledger = self.bind(d['collapse_before'], d['collapse_installed'])
        self.assertFalse(ledger.original_user_empty)
        with self.assertRaisesRegex(h.Conflict, 'DEEPSEEK_NATIVE_UNSET_REQUIRES_LAYER_RESTORE'):
            origin.plan_owned(ledger, self.identity(), h.encode(d['collapse_installed']),
                              writable=True, registered=[], desired=None)
        self.assertEqual(d['collapse_replaced']['user'], {})
        self.assertTrue(next(c for c in self.receipt['cases']
                             if c['name'] == 'explicit-inherited-override-collapse')['native_replace_also_collapses'])

    def test_actual_first_install_preflight_rejects_irreversible_profiles(self):
        d = self.descriptors
        target = d['set_arguments'][1][0]['value']
        first = origin.plan_first_install(self.identity(), h.encode(d['before']),
                                          writable=True, registered=[], desired=target)
        self.assertEqual(first, self.plan(d['before'], target))
        for before, code in ((d['collapse_before'], 'DEEPSEEK_NATIVE_UNSET_REQUIRES_LAYER_RESTORE'),):
            with self.assertRaisesRegex(h.Conflict, code):
                origin.plan_first_install(self.identity(), h.encode(before), writable=True,
                                          registered=[], desired=target)

    def test_actual_update_advance_and_mixed_original_layer_roundtrips(self):
        d = self.descriptors['origin_update']
        ledger = self.bind(d['before'], d['installed'])
        update = origin.plan_owned(ledger, self.identity(), h.encode(d['installed']),
                                   writable=True, registered=[], desired=d['target'])
        advanced, receipt = origin.verify_and_advance(ledger, update, self.identity(),
            h.encode(d['installed']), h.encode(d['updated']), writable=True, registered=[])
        self.assertTrue(receipt['origin_record_retained'])
        self.assertNotEqual(advanced.owned_sha256, ledger.owned_sha256)
        remove = origin.plan_owned(advanced, self.identity(), h.encode(d['updated']),
                                   writable=True, registered=[], desired=None)
        _, receipt = origin.verify_and_advance(advanced, remove, self.identity(),
            h.encode(d['updated']), h.encode(d['removed']), writable=True, registered=[])
        self.assertTrue(receipt['original_user_layer_restored'])
        all_d = self.descriptors
        mixed = self.bind(all_d['mixed_before'], all_d['mixed_after'])
        remove = origin.plan_owned(mixed, self.identity(), h.encode(all_d['mixed_after']),
                                   writable=True, registered=[], desired=None)
        _, receipt = origin.verify_and_advance(mixed, remove, self.identity(),
            h.encode(all_d['mixed_after']), h.encode(all_d['mixed_removed']), writable=True, registered=[])
        self.assertTrue(receipt['original_user_layer_restored'])

    def test_actual_empty_servers_residue_is_not_reported_as_exact_restoration(self):
        d = self.descriptors['empty_inherited']
        ledger = self.bind(d['before'], d['installed'])
        remove = origin.plan_owned(ledger, self.identity(), h.encode(d['installed']),
                                   writable=True, registered=[], desired=None)
        with self.assertRaisesRegex(h.Conflict, 'DEEPSEEK_ORIGINAL_USER_LAYER_NOT_RESTORED'):
            origin.verify_and_advance(ledger, remove, self.identity(), h.encode(d['installed']),
                                      h.encode(d['removed']), writable=True, registered=[])
        self.assertEqual(d['removed']['user'], {'servers': {}})

    def test_actual_empty_container_roundtrip_for_both_raw_inherited_shapes(self):
        for name in ('empty_cleanup_absent', 'empty_cleanup_explicit'):
            d = self.descriptors[name]
            target = d['installed']['user']['servers'][h.KEY]
            first = origin.plan_first_install(self.identity(), h.encode(d['before']),
                                              writable=True, registered=[], desired=target)
            ledger = origin.bind_origin(first, self.identity(), h.encode(d['before']),
                                         h.encode(d['installed']), writable=True, registered=[])
            remove = origin.plan_owned(ledger, self.identity(), h.encode(d['installed']),
                                       writable=True, registered=[], desired=None)
            self.assertEqual(remove.mutation()[1], d['operations'])
            retired, receipt = origin.verify_and_advance(ledger, remove, self.identity(),
                h.encode(d['installed']), h.encode(d['removed']), writable=True, registered=[])
            self.assertIsNone(retired)
            self.assertTrue(receipt['original_user_layer_restored'])
        race = next(c for c in self.receipt['cases'] if c['name'] == 'empty-container-concurrent-insertion')
        self.assertTrue(race['stale_revision_rejected_inside_native_lock'])
        self.assertTrue(race['exact_external_bytes_preserved'])

    def test_actual_original_explicit_empty_layer_is_not_mistaken_for_tool_container(self):
        base_shapes = []
        for name in ('explicit-empty-user-absent-base', 'explicit-empty-user-explicit-base'):
            d = self.descriptors[name]
            base_shapes.append(d['before']['base'])
            with self.assertRaisesRegex(h.Conflict, 'DEEPSEEK_EXPLICIT_EMPTY_LAYER_RESTORE_UNVERIFIED'):
                origin.plan_first_install(self.identity(), h.encode(d['before']), writable=True,
                    registered=[], desired=d['installed']['user']['servers'][h.KEY])
            ledger = self.bind(d['before'], d['installed'])
            with self.assertRaisesRegex(h.Conflict, 'DEEPSEEK_EXPLICIT_EMPTY_LAYER_RESTORE_UNVERIFIED'):
                origin.plan_owned(ledger, self.identity(), h.encode(d['installed']),
                                  writable=True, registered=[], desired=None)
        self.assertEqual(base_shapes[0], base_shapes[1])
        self.assertEqual(self.descriptors['explicit-empty-user-absent-base']['removed']['user'], {'servers': {}})
        self.assertEqual(self.descriptors['explicit-empty-user-explicit-base']['removed']['user'], {})

    def test_durable_journal_matches_actual_native_install_update_and_exact_removal(self):
        chains = [('inherited', self.descriptors['before'], self.descriptors['after'],
                   None, None, self.descriptors['materialization_removed'])]
        for name in ('origin_update', 'empty_cleanup_absent', 'empty_cleanup_explicit'):
            d = self.descriptors[name]
            chains.append((name, d['before'], d['installed'], d.get('target'), d.get('updated'), d['removed']))
        for name, before, installed, new_target, updated, removed in chains:
            with self.subTest(chain=name):
                root = Path(self.temp.name) / ('journal-' + name); root.mkdir(mode=0o700)
                store = journal.OriginJournal(root, self.identity())
                common = dict(writable=True, registered=[])
                plan, op, revision = store.prepare(store.snapshot()[1], self.identity(), h.encode(before),
                    desired=installed['user']['servers'][h.KEY], **common)
                store = journal.OriginJournal(root, self.identity())
                store.resolve(revision, op, self.identity(), h.encode(installed), **common)
                if updated is not None:
                    plan, op, revision = store.prepare(store.snapshot()[1], self.identity(), h.encode(installed),
                                                       desired=new_target, **common)
                    store.resolve(revision, op, self.identity(), h.encode(updated), **common)
                plan, op, revision = store.prepare(store.snapshot()[1], self.identity(),
                    h.encode(installed if updated is None else updated), desired=None, **common)
                if name.startswith('empty_cleanup'):
                    self.assertTrue(plan.empty_servers_cleanup)
                store = journal.OriginJournal(root, self.identity())
                result = store.resolve(revision, op, self.identity(), h.encode(removed), **common)
                self.assertFalse(result['origin_record_retained'])
                self.assertIsNone(store.snapshot()[0]['pending'])
