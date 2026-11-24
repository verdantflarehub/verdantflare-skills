"""Registry integrity fixtures do not register real delegations."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import delegated_registry as dr
from delegated_policy import PolicyError
import test_delegated_policy as fixture


class RegistryTests(unittest.TestCase):
    def setUp(self):
        self.f = fixture.PolicyTests(); self.f.setUp(); self.addCleanup(self.f.doCleanups)
        self.root = self.f.root
        self.path = self.root / dr.REGISTRY_PATH; self.path.parent.mkdir(parents=True, exist_ok=True)
        self.value = {'schema_version': 1, 'repository_id': 'unit/repository', 'policies': []}
        self.refresh()

    def refresh(self):
        self.value['policies'] = [{'policy': self.f.cap.ref(self.f.policy),
                                  'envelope': self.f.cap.ref(self.f.envelope)}]
        self.save()

    def save(self): self.path.write_text(json.dumps(self.value))
    def load(self): return dr.load_registry(self.root, repository_id='unit/repository')
    def reject(self, code):
        with self.assertRaises(PolicyError) as ctx: self.load()
        self.assertEqual(code, ctx.exception.code)

    def test_missing_registry_and_unregistered_plan_keep_original_path(self):
        entries = self.load()
        self.assertIsNone(dr.select_registration(entries, plan_id='other', plan_path='docs/plan/other.plan.md'))
        self.path.unlink(); self.assertEqual((), self.load())

    def test_select_active_and_archive_requires_same_stable_plan(self):
        entries = self.load(); p = self.f.payload
        for path in (p['plan_path'], p['archive_plan_path']):
            self.assertEqual(p['policy_id'], dr.select_registration(entries, plan_id=p['plan_id'], plan_path=path).policy_id)
        for plan_id, path in [('other', p['plan_path']), (p['plan_id'], 'docs/plan/other.plan.md')]:
            with self.assertRaises(PolicyError): dr.select_registration(entries, plan_id=plan_id, plan_path=path)

    def test_corrupt_explicit_registry_does_not_fall_back(self):
        for raw in [b'{}', b'{"schema_version":1,"schema_version":1}', b'\xff']:
            self.path.write_bytes(raw); self.reject('PL-DELEGATE-SCHEMA')
        self.save(); self.path.unlink(); self.path.symlink_to(self.root / 'missing')
        self.reject('PL-DELEGATE-BINDING')

    def test_foreign_repository_unknown_field_and_bool_version_rejected(self):
        self.value['repository_id'] = 'other'; self.save(); self.reject('PL-DELEGATE-BINDING')
        self.value['repository_id'] = 'unit/repository'
        self.value['schema_version'] = True; self.save(); self.reject('PL-DELEGATE-SCHEMA')
        self.value['schema_version'] = 1; self.value['approved'] = True; self.save(); self.reject('PL-DELEGATE-SCHEMA')

    def test_duplicate_policy_and_shared_source_selector_rejected(self):
        original = copy.deepcopy(self.value['policies'][0])
        self.value['policies'].append(original); self.save(); self.reject('PL-DELEGATE-BINDING')
        self.f.policy = self.root / 'policy2.json'; self.f.envelope = self.root / 'envelope2.json'
        self.f.payload.update(policy_id='different', plan_id='different', plan_path='docs/plan/other.plan.md',
                              archive_plan_path='docs/archive/plan/other.plan.md')
        self.f.save_review(); self.refresh()
        self.value['policies'].insert(0, original); self.save(); self.reject('PL-DELEGATE-BINDING')

    def test_changed_policy_or_envelope_cannot_be_hidden_in_an_unselected_entry(self):
        self.f.policy.write_bytes(self.f.policy.read_bytes() + b' '); self.reject('PL-DELEGATE-BINDING')
        self.f.save_review(); self.refresh()
        self.f.envelope.write_bytes(b'{}'); self.reject('PL-DELEGATE-REVIEW')

    def test_envelope_for_another_policy_rejected(self):
        e = json.loads(self.f.envelope.read_bytes()); e['policy_sha256'] = '0' * 64
        self.f.envelope.write_text(json.dumps(e)); self.refresh(); self.reject('PL-DELEGATE-BINDING')

    def test_external_registry_symlink_is_not_an_unregistered_repository(self):
        with tempfile.TemporaryDirectory() as directory:
            outside = Path(directory) / 'registry.json'; outside.write_bytes(self.path.read_bytes())
            self.path.unlink(); self.path.symlink_to(outside)
            self.reject('PL-DELEGATE-BINDING')

