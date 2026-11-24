"""Synthetic explicit-cancellation tests; never actual user cancellation."""
from dataclasses import replace
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import archive_relocation as ar
import final_decisions as fd
import plan_lint as pl
import process_receipts as pr
from delegated_policy import PolicyError
import test_delegated_gate as fixture
import test_archive_relocation as archive_fixture
from test_final_decisions import assistant, human
from test_plan_lint import run_cli


class CancellationTests(unittest.TestCase):
    def setUp(self):
        self.base = fixture.DelegatedGateTests(); self.base.setUp()
        self.addCleanup(self.base.doCleanups)
        self.root = self.base.root.resolve(); self.plan = self.base.plan.resolve()

    def cancel(self, reply='确认取消计划'):
        prepared = pl.prepare_cancellation(self.plan)
        link = os.path.relpath(self.root / prepared['object']['path'], self.plan.parent).replace(os.sep, '/')
        self.plan.write_text(self.plan.read_text().replace('负责人：', f'最终审核对象：[对象]({link})\n负责人：', 1)
                            .replace('计划状态：进行中', '计划状态：已废弃'))
        for stage in ('t1', 't2', 't4'):
            path = self.base.files[stage]
            path.write_text(path.read_text().replace('评审结论：已通过', '评审结论：已废弃'))
        acceptance = self.base.files['accept']
        acceptance.write_text(acceptance.read_text().replace('验收状态：智能体已验真', '验收状态：已废弃'))
        source = self.base.provider.return_value
        conversation = source.capture + assistant(prepared['request_text']) + human(reply).replace(b'00:00:10Z', b'00:00:12Z')
        self.base.provider.return_value = replace(source, capture=fd.goal_projection(conversation), conversation_capture=conversation)
        return prepared

    def prepare(self):
        return pl.prepare_archive(self.plan)

    def apply(self):
        return archive_fixture.ArchiveTests.apply(self)

    def check(self, gate='archive-preflight'):
        path = self.archived if gate == 'archive' else self.plan
        issues, rc = pl.check_plan(path, gate)
        return {i.code for i in issues}, rc

    def test_explicit_cancellation_archives_without_faking_technical_pass(self):
        self.base.files['img'].unlink()
        self.cancel()
        self.assertEqual(self.check(), (set(), 0))
        self.assertNotIn('CP0 技术收据', self.plan.read_text())
        _, mapping = self.apply()
        self.assertEqual(mapping['outcome'], 'cancellation')
        self.assertEqual(self.check('archive'), (set(), 0))
        self.assertIn('- [ ] T5.1', self.archived.read_text())
        self.assertIn('计划状态：已废弃', self.archived.read_text())

    def test_ordinary_approval_cannot_cancel(self):
        self.cancel('通过')
        self.assertIn('PL-DELEGATE-H2', self.check()[0])
        with self.assertRaises(PolicyError): self.prepare()

    def test_status_alone_and_unsynchronized_report_cannot_archive(self):
        original = self.plan.read_text()
        self.plan.write_text(original.replace('计划状态：进行中', '计划状态：已废弃'))
        self.assertIn('PL-DELEGATE-H2', self.check()[0])
        self.plan.write_text(original)
        self.cancel()
        report = self.base.files['t2']
        report.write_text(report.read_text().replace('评审结论：已废弃', '评审结论：已通过'))
        self.assertIn('PL-ARC-SYM', self.check()[0])

    def test_prepare_cancellation_is_material_only_and_needs_live_source(self):
        original = self.plan.read_bytes()
        rc, output, _ = run_cli('prepare-cancellation', str(self.plan), '--json')
        self.assertEqual(rc, 0, output)
        data = json.loads(output)
        target = json.loads(pr.read_content(self.root, data['object']))
        self.assertEqual(target['kind'], 'plan-cancellation')
        self.assertIsNone(target['manifest']); self.assertIsNone(target['cp1'])
        self.assertEqual(self.plan.read_bytes(), original)
        self.assertNotIn(str(self.root), output)
        self.base.registry.unlink()
        with self.assertRaises(PolicyError): pl.prepare_cancellation(self.plan)

    def test_withdrawal_after_cancellation_still_blocks_actual_archive(self):
        self.cancel(); self.apply()
        source = self.base.provider.return_value
        conversation = source.conversation_capture + human('继续计划，不取消').replace(b'00:00:10Z', b'00:00:12Z')
        self.base.provider.return_value = replace(source, capture=fd.goal_projection(conversation), conversation_capture=conversation)
        self.assertIn('PL-DELEGATE-H2', self.check('archive')[0])

    def test_cancelled_plan_cannot_be_relabelled_successful(self):
        self.cancel(); self.apply()
        self.archived.write_text(self.archived.read_text().replace('计划状态：已废弃', '计划状态：已完成'))
        self.assertIn(ar.CODE, self.check('archive')[0])

    def test_cancellation_source_change_during_preparation_is_rejected(self):
        source = self.base.provider.return_value
        changed = replace(source, capture=source.capture + human('Change scope'))
        self.base.provider.side_effect = [source, changed]
        with self.assertRaises(PolicyError): pl.prepare_cancellation(self.plan)


if __name__ == '__main__':
    unittest.main()
