"""Conflicts, generation binding, safe readback and native consumer interoperability."""
from __future__ import annotations

from dataclasses import replace
import copy
import json
from pathlib import Path
import os
import shutil
import subprocess
import sys
import unittest

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))
import host_deepseek as h


def schema():
    return {'type': 'object', 'meta': {}, 'dict': {'servers': {'type': 'dict', 'meta': {'volatile': True},
        'inner': {'type': 'object', 'meta': {}, 'dict': {}}}}}


class DeepSeekPlanningTests(unittest.TestCase):
    def setUp(self):
        self.identity = h.Identity('com.deepseek.dsh', '0.2.2-rc.3', 'macos', 'arm64', h.DMG_SHA256,
                                  h.SETTINGS_SHA256, 'backend-one', 'profile-one', 'mcp-manager:entry')
        self.other = {'transport': 'stdio', 'command': 'unrelated', 'env': {'KEY': 'PRIVATE_TEST_VALUE'}}
        self.desc = {'ns': self.identity.entry, 'schema': schema(), 'revision': 7, 'applies': 'live',
                     'value': {'servers': {'other': self.other}}, 'base': {},
                     'user': {'servers': {'other': self.other}}, 'autoGenerate': True,
                     'secrets': [{'path': ['servers', 'other', 'env', 'KEY'], 'set': True}]}
        self.target = h.desired_connection('https://studio.invalid/mcp', 'VF_STUDIO')

    def plan(self, desired=None, owned=None):
        return h.plan_update(self.identity, h.encode(self.desc), writable=True, registered=['plugin'],
                             desired=self.target if desired is None else desired, expected_owned_sha256=owned)

    def installed(self, plan):
        after = copy.deepcopy(self.desc)
        after['revision'] += 1
        after['user'].setdefault('servers', {})[h.KEY] = json.loads(plan.target)
        after['value']['servers'][h.KEY] = {**json.loads(plan.target), **copy.deepcopy(h.DEFAULTS)}
        return after

    def test_plan_contains_only_native_vf_path_and_redacted_receipt(self):
        original = copy.deepcopy(self.desc); plan = self.plan()
        namespace, ops, revision = plan.mutation()
        self.assertEqual((namespace, revision), (self.identity.entry, 7))
        self.assertEqual(ops, [{'op': 'set', 'path': ['servers', 'vf-studio'], 'value': self.target}])
        self.assertEqual(original, self.desc)
        receipt = json.dumps(plan.receipt())
        self.assertNotIn('PRIVATE_TEST_VALUE', receipt); self.assertNotIn('credential', receipt)
        self.assertFalse(plan.receipt()['mutation_executed'])
        ops[0]['value']['url'] = 'changed'
        self.assertEqual(plan.mutation()[1][0]['value'], self.target)

    def test_identity_generation_and_preflight_revision_do_not_cross(self):
        plan = self.plan()
        for field, value in [('backend_generation', 'backend-two'), ('profile', 'other'), ('entry', 'entry-two')]:
            other = replace(self.identity, **{field: value}); desc = copy.deepcopy(self.desc); desc['ns'] = other.entry
            with self.subTest(field=field), self.assertRaises(h.Conflict):
                h.validate_submit(plan, other, h.encode(desc), writable=True, registered=['plugin'])
        self.desc['revision'] += 1
        with self.assertRaisesRegex(h.Conflict, 'DEEPSEEK_PRECONDITION_CHANGED'):
            h.validate_submit(plan, self.identity, h.encode(self.desc), writable=True, registered=['plugin'])

    def test_known_profile_only_no_windows_or_name_heuristics(self):
        for field, value in [('platform', 'windows'), ('version', '0.2.2'), ('arch', 'amd64'),
                             ('settings_sha256', '0' * 64), ('package_sha256', '0' * 64)]:
            with self.subTest(field=field), self.assertRaisesRegex(h.Conflict, 'DEEPSEEK_PROFILE_UNVERIFIED'):
                replace(self.identity, **{field: value}).validate()

    def test_existing_or_inherited_or_registered_vf_is_never_adopted(self):
        for layer in ('user', 'value', 'base'):
            before = copy.deepcopy(self.desc)
            self.desc[layer].setdefault('servers', {})[h.KEY] = self.target
            with self.subTest(layer=layer), self.assertRaises(h.Conflict):self.plan()
            self.desc = before
        with self.assertRaisesRegex(h.Conflict, 'DEEPSEEK_REGISTERED_NAME_CONFLICT'):
            h.plan_update(self.identity, h.encode(self.desc), writable=True, registered=[h.KEY],
                          desired=self.target, expected_owned_sha256=None)

    def test_update_noop_delete_and_ownership(self):
        plan = self.plan(); self.desc = self.installed(plan); owned = h.digest(self.target)
        nochange = self.plan(owned=owned)
        self.assertEqual(nochange.mutation()[1], [])
        self.assertTrue(h.verify_readback(nochange, self.identity, h.encode(self.desc), writable=True,
                                        registered=['plugin'])['configuration_readback_verified'])
        new = h.desired_connection('https://studio.invalid/new-mcp', 'NEW_REF')
        update = self.plan(new, owned)
        self.assertEqual(update.mutation()[1][0]['value'], new)
        with self.assertRaisesRegex(h.Conflict, 'DEEPSEEK_OWNERSHIP_CONFLICT'):self.plan(owned='0' * 64)
        delete = h.plan_update(self.identity, h.encode(self.desc), writable=True, registered=['plugin'],
                               desired=None, expected_owned_sha256=owned)
        self.assertEqual(delete.mutation()[1], [{'op': 'unset', 'path': h.PATH}])
        after = copy.deepcopy(self.desc);after['revision'] += 1
        for layer in ('user','value'):del after[layer]['servers'][h.KEY]
        self.assertIsNone(h.verify_readback(delete, self.identity, h.encode(after), writable=True,
                                            registered=['plugin'])['owned_sha256'])

    def test_schema_or_sidecar_hidden_vf_secrets_refuse(self):
        for path in ([], ['servers'], h.PATH, [*h.PATH,'headers','Authorization']):
            self.desc['secrets'] = [{'path': path, 'set': False}]
            with self.subTest(path=path), self.assertRaisesRegex(h.Conflict, 'DEEPSEEK_OWNED_SECRET_HIDDEN'):self.plan()
        self.desc['secrets'] = []
        self.desc['schema']['dict']['servers']['inner']['dict']['headers'] = {'meta': {'role': 'secret'}}
        with self.assertRaisesRegex(h.Conflict, 'DEEPSEEK_OWNED_SECRET_HIDDEN'):self.plan()

    def test_readback_must_preserve_other_connections_and_confirm_effective_value(self):
        plan = self.plan();after = self.installed(plan)
        receipt = h.verify_readback(plan, self.identity, h.encode(after), writable=True, registered=['plugin'])
        self.assertEqual(receipt['owned_sha256'], h.digest(self.target))
        for mutate in (lambda d: d['user']['servers']['other'].update(command='CHANGED'),
                       lambda d: d['value']['servers'][h.KEY].update(enabled=False),
                       lambda d: d['value']['servers'][h.KEY].update(url='https://different.invalid/mcp'),
                       lambda d: d.update(revision=7),
                       lambda d: d['base'].update(servers={h.KEY: self.target}),
                       lambda d: d['value']['servers'][h.KEY].update(command='UNEXPECTED')):
            bad = copy.deepcopy(after);mutate(bad)
            with self.assertRaises(h.Conflict):h.verify_readback(plan, self.identity, h.encode(bad), writable=True, registered=['plugin'])

    def test_unset_never_reports_restored_inheritance_as_disconnected(self):
        self.desc = self.installed(self.plan())
        delete = h.plan_update(self.identity,h.encode(self.desc),writable=True,registered=['plugin'],desired=None,
                               expected_owned_sha256=h.digest(self.target))
        after = copy.deepcopy(self.desc);after['revision'] += 1
        del after['user']['servers'][h.KEY]
        with self.assertRaisesRegex(h.Conflict, 'DEEPSEEK_REMOVAL_NOT_EFFECTIVE'):
            h.verify_readback(delete,self.identity,h.encode(after),writable=True,registered=['plugin'])

    def test_url_secret_and_arbitrary_command_rejected(self):
        for url in ('http://studio.invalid/mcp','https://user:pass@studio.invalid/mcp','https://studio.invalid/mcp?token=x',
                    'https://studio.invalid/mcp#x','https://studio.invalid/mcp\n','https://studio.invalid:0/mcp'):
            with self.subTest(url=url), self.assertRaises(h.Conflict):h.desired_connection(url,'VF')
        for name in ('Bearer TOKEN','ref-name','${credential:VF}',''):
            with self.assertRaises(h.Conflict):h.desired_connection('https://studio.invalid/mcp',name)
        self.target['command'] = 'SHOULD_NOT_EXECUTE'
        with self.assertRaises(h.Conflict):self.plan()

    def test_malformed_duplicate_redacted_and_nonwritable_descriptors(self):
        raw = h.encode(self.desc).replace(b'{',b'{"revision":1,',1)
        with self.assertRaisesRegex(h.Conflict, 'DEEPSEEK_INVALID_DESCRIPTOR'):
            h.plan_update(self.identity,raw,writable=True,registered=[],desired=self.target,expected_owned_sha256=None)
        for field, value in [('revision', True),('revision', h.SAFE_INT+1),('applies','restart'),('user',None),('schema',{})]:
            before=copy.deepcopy(self.desc);self.desc[field]=value
            with self.subTest(field=field), self.assertRaises(h.Conflict):self.plan()
            self.desc=before
        with self.assertRaisesRegex(h.Conflict,'DEEPSEEK_NOT_WRITABLE'):
            h.plan_update(self.identity,h.encode(self.desc),writable=False,registered=[],desired=self.target,expected_owned_sha256=None)

    def test_preflight_also_detects_schema_registry_and_nonvf_changes(self):
        plan=self.plan()
        self.assertEqual(h.validate_submit(plan,self.identity,h.encode(self.desc),writable=True,registered=['plugin']),plan.mutation())
        for mutate in (lambda d:d['schema'].update(title='changed'),lambda d:d['value'].update(other='changed')):
            bad=copy.deepcopy(self.desc);mutate(bad)
            with self.assertRaises(h.Conflict):h.validate_submit(plan,self.identity,h.encode(bad),writable=True,registered=['plugin'])
        with self.assertRaises(h.Conflict):h.validate_submit(plan,self.identity,h.encode(self.desc),writable=True,registered=['different'])

    def test_absent_delete_is_noop_and_plan_is_detached(self):
        plan=h.plan_update(self.identity,h.encode(self.desc),writable=True,registered=['plugin'],desired=None,expected_owned_sha256=None)
        self.assertEqual(plan.mutation()[1],[])
        h.verify_readback(plan,self.identity,h.encode(self.desc),writable=True,registered=['plugin'])
        plan=self.plan();self.target['url']='changed'
        self.assertEqual(json.loads(plan.target)['url'],'https://studio.invalid/mcp')


class NativeConsumerTests(unittest.TestCase):
    def test_pinned_actual_settings_mutation_consumes_plans_without_replacing_other_nodes(self):
        source=os.environ.get('VF_DEEPSEEK_SETTINGS_SOURCE')
        self.assertTrue(source,'Explicit pinned source required for this integration test')
        node=shutil.which('node');self.assertIsNotNone(node)
        fixture=DeepSeekPlanningTests();fixture.setUp();plan=fixture.plan()
        def run(arguments,current,base):
            value={'arguments':arguments,'current':current,'base':base,'schema':schema(),'revision':7}
            cp=subprocess.run([node,str(TOOLS/'validation/probe_deepseek_settings.mjs'),source],
                               input=json.dumps(value),text=True,capture_output=True,timeout=15)
            self.assertEqual(cp.returncode,0,cp.stderr)
            return json.loads(cp.stdout)
        result=run(plan.mutation(),fixture.desc['user'],{})
        self.assertEqual(result['next']['servers']['other'],fixture.other)
        self.assertEqual(result['next']['servers'][h.KEY],fixture.target)
        delete=(fixture.identity.entry,[{'op':'unset','path':h.PATH}],7)
        removed=run(delete,result['next'],{})
        self.assertNotIn(h.KEY,removed['next']['servers'])
        inherited={'servers':{h.KEY:{'transport':'stdio','command':'inherited'}}}
        restored=run(delete,result['next'],inherited)
        self.assertEqual(restored['next']['servers'][h.KEY],inherited['servers'][h.KEY])
        self.assertEqual(restored['next']['servers']['other'],fixture.other)
        self.assertEqual(result['paths'],[h.PATH])


class MaterializationTests(unittest.TestCase):
    def setUp(self):
        self.f = DeepSeekPlanningTests()
        self.f.setUp()

    def test_native_materialization_retains_effective_connection_with_defaults(self):
        f = self.f
        raw_other = copy.deepcopy(f.other)
        f.desc['user'] = {}
        f.desc['base'] = {'servers': {'other': {**copy.deepcopy(h.DEFAULTS), **raw_other}}}
        f.desc['value'] = copy.deepcopy(f.desc['base'])
        plan = f.plan()
        value = {'arguments': plan.mutation(), 'current': {'servers': {'other': raw_other}},
                 'base': f.desc['base'], 'schema': schema(), 'revision': 7}
        cp = subprocess.run(['node', str(TOOLS / 'validation/probe_deepseek_settings.mjs'),
                             os.environ['VF_DEEPSEEK_SETTINGS_SOURCE']],
                            input=json.dumps(value), text=True, capture_output=True, timeout=15)
        self.assertEqual(cp.returncode, 0, cp.stderr)
        after = copy.deepcopy(f.desc)
        after['revision'] += 1
        after['user'] = json.loads(cp.stdout)['next']
        after['value']['servers'][h.KEY] = {**f.target, **copy.deepcopy(h.DEFAULTS)}
        result = h.verify_readback(plan, f.identity, h.encode(after), writable=True, registered=['plugin'])
        self.assertTrue(result['configuration_readback_verified'])

    def test_existing_user_layer_bytes_cannot_change_behind_unchanged_effective_layer(self):
        f = self.f
        plan = f.plan()
        after = f.installed(plan)
        after['user'] = json.loads(h.encode(after['user']))
        after['user']['servers']['other']['env']['KEY'] = 'different'
        with self.assertRaisesRegex(h.Conflict, 'DEEPSEEK_USER_LAYER_CHANGED'):
            h.verify_readback(plan, f.identity, h.encode(after), writable=True, registered=['plugin'])

    def test_unknown_materialized_server_rejected(self):
        f = self.f
        f.desc['user'] = {'servers': {}}
        plan = f.plan()
        after = f.installed(plan)
        after['user']['servers']['unknown'] = f.other
        with self.assertRaisesRegex(h.Conflict, 'DEEPSEEK_UNEXPECTED_MATERIALIZATION'):
            h.verify_readback(plan, f.identity, h.encode(after), writable=True, registered=['plugin'])

    def test_wrong_inherited_server_materialization_rejected(self):
        f = self.f
        f.desc['user'] = {'servers': {}}
        plan = f.plan()
        after = f.installed(plan)
        after['user']['servers']['other'] = {**f.other, 'command': 'different'}
        with self.assertRaisesRegex(h.Conflict, 'DEEPSEEK_UNEXPECTED_MATERIALIZATION'):
            h.verify_readback(plan, f.identity, h.encode(after), writable=True, registered=['plugin'])

    def test_unset_that_could_collapse_user_layer_is_refused_before_mutation(self):
        f = self.f
        f.desc = f.installed(f.plan())
        f.desc['base'] = {'servers': {'other': copy.deepcopy(f.other)}}
        with self.assertRaisesRegex(h.Conflict, 'DEEPSEEK_NATIVE_UNSET_REQUIRES_LAYER_RESTORE'):
            h.plan_update(f.identity, h.encode(f.desc), writable=True, registered=['plugin'], desired=None,
                          expected_owned_sha256=h.digest(f.target))

if __name__=='__main__':unittest.main()
