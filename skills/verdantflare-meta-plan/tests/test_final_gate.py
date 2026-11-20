"""Synthetic end-to-end final review gates; never human or product acceptance."""
from dataclasses import replace
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import final_decisions as fd
import plan_lint as pl
import process_receipts as pr
from delegated_policy import PolicyError
from test_final_decisions import assistant, human
import test_process_receipts as process_fixture
from test_plan_lint import run_cli


class FinalGateTests(unittest.TestCase):
    def setUp(self):
        self.f = process_fixture.ProcessReceiptTests(); self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.root = self.f.root; self.plan = self.f.plan
        self.base = self.f.base
        acceptance = self.f.files['accept']
        acceptance.write_text(acceptance.read_text().replace('验收状态：智能体已验真', '验收状态：CP1已通过'))
        self.manifest = self.root / 'delivery.json'
        self.manifest.write_text(json.dumps({'release_id': 'synthetic-r1', 'artifacts': []}))
        self.cap = next(c for c in self.f.captures if c.data['stage'] == 't4' and c.data['role'] == 'detail')
        self.cap.data['inputs'].append(self.cap.ref(self.manifest)); self.cap.save()
        self.f.cp0()
        self.cp1 = pl.record_gate(self.plan, 'cp1')
        self.header('CP1 技术收据', self.cp1)

    def header(self, key, ref):
        link = os.path.relpath(self.root / ref['path'], self.plan.parent).replace(os.sep, '/')
        self.plan.write_text(self.plan.read_text().replace('负责人：', f'{key}：[记录]({link})\n负责人：', 1))

    def prepare(self):
        return pl.prepare_final_review(self.plan, self.manifest, 'synthetic-r1')

    def approve(self, reply='通过'):
        prepared = self.prepare()
        self.header('最终审核对象', prepared['object'])
        source = self.base.provider.return_value
        conversation = (source.capture + assistant(prepared['request_text'])
                        + human(reply).replace(b'00:00:10Z', b'00:00:12Z'))
        self.base.provider.return_value = replace(source, capture=fd.goal_projection(conversation),
                                                   conversation_capture=conversation)
        return prepared

    def check(self, gate='archive-preflight'):
        issues, rc = pl.check_plan(self.plan, gate)
        return {i.code for i in issues}, rc

    def test_preparation_changes_no_plan_and_does_not_count_as_approval(self):
        original = self.plan.read_bytes()
        prepared = self.prepare()
        self.assertEqual(self.plan.read_bytes(), original)
        target = json.loads(pr.read_content(self.root, prepared['object']))
        self.assertEqual(target['cp1'], self.cp1)
        self.assertEqual(target['manifest']['sha256'], pr.sha(self.manifest.read_bytes()))
        self.header('最终审核对象', prepared['object'])
        self.assertIn('PL-DELEGATE-H2', self.check()[0])

    def test_exact_user_reply_passes_active_preflight_but_not_post_move_archive(self):
        self.approve()
        self.assertEqual(self.check(), (set(), 0))
        self.assertIn('PL-DELEGATE-ARCHIVE', self.check('archive')[0])
        # The added human event cannot silently reactivate ordinary process gates.
        self.assertIn('PL-DELEGATE-SOURCE', self.check('cp0')[0])

    def test_rejected_or_qualified_reply_never_passes(self):
        self.approve('同意，但是先修复')
        self.assertIn('PL-DELEGATE-H2', self.check()[0])

    def test_changed_manifest_and_stale_receipt_cannot_reuse_approval(self):
        self.approve()
        self.manifest.write_text(json.dumps({'release_id': 'synthetic-r2'}))
        self.assertNotEqual(self.check()[1], 0)

    def test_unreviewed_manifest_cannot_be_presented_for_approval(self):
        self.cap.data['inputs'] = [ref for ref in self.cap.data['inputs'] if ref['path'] != 'delivery.json']
        self.cap.save()
        # Reissue CP1 honestly without this manifest: preparation must still reject.
        cp1 = pl.record_gate(self.plan, 'cp1')
        text = self.plan.read_text()
        self.plan.write_text(text.replace(self.cp1['sha256'], cp1['sha256']))
        with self.assertRaisesRegex(PolicyError, 'manifest'):
            self.prepare()

    def test_no_process_receipt_wrong_release_or_registry_cannot_prepare(self):
        with self.assertRaises(PolicyError):
            pl.prepare_final_review(self.plan, self.manifest, 'other-release')
        (self.root / self.cp1['path']).write_text('{}')
        with self.assertRaises(PolicyError): self.prepare()
        self.base.registry.unlink()
        with self.assertRaises(PolicyError): self.prepare()

    def test_final_preflight_rechecks_original_technical_requirements(self):
        self.approve()
        self.f.files['img'].unlink()
        self.assertIn('PL-CP0-IMG', self.check()[0])

    def test_final_readback_rejects_new_human_input(self):
        self.approve()
        source = self.base.provider.return_value
        conversation = source.conversation_capture + human('撤回批准').replace(b'00:00:10Z', b'00:00:12Z')
        changed = replace(source, capture=fd.goal_projection(conversation), conversation_capture=conversation)
        self.base.provider.side_effect = [source, changed]
        self.assertIn('PL-DELEGATE-H2', self.check()[0])

    def test_prepare_final_reread_rejects_manifest_drift(self):
        original = pl._check_plan_technical
        def drift(*args, **kwargs):
            result = original(*args, **kwargs)
            if args[1] == 'cp1': self.manifest.write_text('{"release_id":"changed"}')
            return result
        with patch('plan_lint._check_plan_technical', side_effect=drift), self.assertRaises(PolicyError):
            self.prepare()

    def test_cli_returns_review_material_not_approval(self):
        rc, output, _ = run_cli('prepare-final-review', str(self.plan), '--manifest', str(self.manifest),
                                '--release-id', 'synthetic-r1', '--json')
        self.assertEqual(rc, 0, output)
        data = json.loads(output)
        self.assertTrue(data['ok'])
        self.assertNotIn('approved', data)
        self.assertNotIn(str(self.root), output)
        self.assertNotIn(self.base.goal.objective, output)


if __name__ == '__main__':
    unittest.main()
