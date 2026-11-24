"""Synthetic process-chain tests; none of these fixtures authorizes real work."""
import copy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import uuid
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import plan_lint as pl
import process_receipts as pr
from delegated_policy import PolicyError
import test_delegated_gate as dg
import test_native_receipts as nf
from test_plan_lint import run_cli


class ProcessReceiptTests(unittest.TestCase):
    def setUp(self):
        self.base = dg.DelegatedGateTests()
        self.base.setUp()
        self.addCleanup(self.base.doCleanups)
        self.root = self.base.root
        self.plan = self.base.plan
        self.files = self.base.files
        # Use Codex authors and separate Gemini/Claude native reviewer fixtures.
        for change in (self.root / 'docs/archive/changes').glob('change_*.md'):
            change.write_text(change.read_text().replace('Gemini', 'Codex'))
        self.files['accept'].write_text(self.files['accept'].read_text()
            .replace('执行助手：Gemini', '执行助手：Codex').replace('验真助手：Codex', '验真助手：Gemini'))
        self.captures = []
        for stage in ('t1', 't2', 't4'):
            report = self.files[stage]
            report.write_text(report.read_text().replace('Codex', 'Gemini'))
            links = []
            for provider, agent, chapter in (('claude-cli', 'Claude', '2.'), ('antigravity-hub', 'Gemini', '3.')):
                if stage == 't2' and agent == 'Claude':
                    continue
                cap = nf.Capture(self.root, provider, stage, self.plan, tag=f'{stage}-{agent}')
                self.captures.append(cap)
                link = f'[native-receipt](../../{cap.path.relative_to(self.root).as_posix()})'
                nf.NativeIntegrationTests.replace_section(report, 2, chapter, agent + '：' + link)
                if stage == 't4':
                    links.append('- ' + agent + '：' + link)
            if stage == 't4':
                nf.NativeIntegrationTests.replace_section(self.files['accept'], 3, '4.5', '\n'.join(links))
        # There is deliberately no human CP0 approval in this test plan.
        nf.NativeIntegrationTests.replace_section(self.plan, 2, '7.', '暂无')
        self.observer_patch = patch('process_receipts.observe_check', side_effect=self.synthetic_observer)
        self.observer = self.observer_patch.start()
        self.addCleanup(self.observer_patch.stop)

    def synthetic_observer(self, root, state):
        # Simulates ONLY process observation; actual checker/source/technical
        # validators execute normally against clearly synthetic native evidence.
        issues, rc = pl.check_plan(root / state['plan_path'], state['gate'].lower(), indep_mode='d4a')
        if rc:
            raise PolicyError(pr.CODE, 'Synthetic checker rejected: ' + ','.join(i.code for i in issues))
        output = pr.canonical({'ok': True, 'exit_code': 0, 'gate': state['gate'].lower(),
                               'plan': state['plan_path'], 'codes': [], 'issues': []})
        now = datetime.now(timezone.utc).isoformat()
        observation = {'schema_version': 1, 'provider': 'plan-lint-subprocess-v1',
                       'run_id': str(uuid.uuid4()), 'started_at': now, 'finished_at': now,
                       'duration_ns': 1, 'exit_code': 0,
                       'argv_sha256': pr.sha(pr.canonical(pr.command_args(state['plan_path'], state['gate']))),
                       'state_sha256': pr.sha(pr.canonical(state)),
                       'python_version': 'synthetic-3.10', 'markdown_it_version': 'synthetic-test'}
        return output, observation

    def state(self, gate='CP0'):
        doc = pl.parse_doc(self.plan)
        registration, repo = pl._delegated_registration(self.root, doc)
        evidence, _ = pl._registered_policy_context(self.root, doc, registration, repo)
        return pl._process_state(self.root, doc, registration, repo, evidence, gate)

    def cp0(self):
        ref = pl.record_gate(self.plan, 'cp0')
        link = os.path.relpath(self.root / ref['path'], self.plan.parent).replace(os.sep, '/')
        self.plan.write_text(self.plan.read_text().replace('负责人：', f'CP0 技术收据：[执行结果]({link})\n负责人：', 1))
        return ref

    def codes(self):
        issues, rc = pl.check_plan(self.plan, 'cp1')
        return {i.code for i in issues}, rc

    def test_cp0_record_and_cp1_consume_without_human_cp0(self):
        ref = self.cp0()
        self.assertEqual(self.codes(), (set(), 0))
        pr.validate_run(self.root, ref, self.state())
        cp1 = pl.record_gate(self.plan, 'cp1')
        state = self.state('CP1')
        pr.validate_run(self.root, cp1, state)
        self.assertEqual(state['prior_cp0'], ref)
        issues, rc = pl.check_plan(self.plan, 'archive-preflight')
        self.assertEqual(rc, 1)
        self.assertIn('PL-DELEGATE-H2', {i.code for i in issues})

    def test_progress_and_evidence_append_do_not_change_frozen_cp0_scope(self):
        ref = self.cp0()
        self.plan.write_text(self.plan.read_text().replace('当前结论：待检查', '当前结论：已通过')
                            .replace('## 7. 审批记录\n', '## 7. 审批记录\n\n新的过程记录。\n'))
        self.files['accept'].write_text(self.files['accept'].read_text() + '\nNon-frozen closing notes.\n')
        pr.validate_run(self.root, ref, self.state())
        self.assertEqual(self.codes(), (set(), 0))

    def test_scope_change_cannot_reuse_old_receipt(self):
        self.cp0()
        self.plan.write_text(self.plan.read_text().replace('交付完整分级管控能力。', '交付新增未经冻结的业务。'))
        codes, rc = self.codes()
        self.assertEqual(rc, 1)
        self.assertIn(pr.CODE, codes)

    def test_status_like_requirements_and_code_are_not_removed_from_scope(self):
        self.plan.write_text(self.plan.read_text().replace('## 4. 计划说明',
            '```text\n当前结论：待提交\n```\n\n## 4. 计划说明'))
        self.cp0()
        self.plan.write_text(self.plan.read_text().replace('当前结论：待提交', '当前结论：已通过'))
        self.assertIn(pr.CODE, self.codes()[0])

    def test_normative_symlink_escape_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / 'contract.md'
            target.write_text('External contract')
            (self.root / 'docs/design/escape.md').symlink_to(target)
            self.plan.write_text(self.plan.read_text() + '\n- [Contract](../design/escape.md)\n')
            with self.assertRaisesRegex(PolicyError, 'escapes'):
                pl.record_gate(self.plan, 'cp0')

    def test_linked_contract_changes_and_newer_review_invalidate_cp0(self):
        contract = self.root / 'docs/design/contract.md'
        contract.write_text('# Contract\n\nFrozen interface.\n')
        self.plan.write_text(self.plan.read_text() + '\n- [Contract](../design/contract.md)\n')
        self.cp0()
        contract.write_text(contract.read_text() + 'Changed.\n')
        self.assertIn(pr.CODE, self.codes()[0])
        contract.write_text('# Contract\n\nFrozen interface.\n')
        newer = self.files['t2'].with_name('review_20261115_alpha-gov_t2-r2.md')
        newer.write_bytes(self.files['t2'].read_bytes())
        self.assertIn(pr.CODE, self.codes()[0])

    def test_unrelated_managed_link_does_not_require_external_normative_input(self):
        with tempfile.TemporaryDirectory() as tmp:
            external = Path(tmp) / 'evidence.txt'
            external.write_text('Managed evidence outside Git')
            (self.root / '.gdrive/external').symlink_to(Path(tmp), target_is_directory=True)
            self.plan.write_text(self.plan.read_text() + '\n- [Managed evidence](../../.gdrive/external/evidence.txt)\n')
            self.cp0()
            self.assertEqual(self.codes(), (set(), 0))

    def test_native_receipt_change_cannot_hide_behind_unchanged_report(self):
        self.cp0()
        cap = next(c for c in self.captures if c.data['stage'] == 't2')
        cap.data['inputs'].append(cap.ref(self.root / 'policy.json'))
        cap.save()
        self.assertIn(pr.CODE, self.codes()[0])

    def test_changed_tool_fingerprint_rejects_existing_receipt(self):
        self.cp0()
        altered = copy.deepcopy(pr.tool_fingerprint())
        altered['files']['plan_lint.py'] = '0' * 64
        with patch('process_receipts.tool_fingerprint', return_value=altered):
            self.assertIn(pr.CODE, self.codes()[0])

    def test_corrupt_receipt_never_falls_back_to_human_text(self):
        ref = self.cp0()
        (self.root / ref['path']).write_bytes(b'{}')
        self.assertIn(pr.CODE, self.codes()[0])
        self.plan.write_text(self.plan.read_text().replace('暂无',
            '- **A01（2026-11-18，Luna 批准）**：批准 CP0，锁卷哈希 `' + self.state()['lock_hash'] + '`。'))
        self.assertIn(pr.CODE, self.codes()[0])

    def test_malformed_observation_stdout_and_cross_scope_records_rejected(self):
        ref = self.cp0()
        state = self.state()
        receipt = json.loads((self.root / ref['path']).read_bytes())
        original_obs = json.loads(pr.read_content(self.root, receipt['observation']))
        mutations = [('exit_code', 1), ('exit_code', False), ('argv_sha256', '0' * 64),
                     ('state_sha256', '0' * 64), ('duration_ns', 0), ('run_id', 'not-a-run')]
        for key, value in mutations:
            with self.subTest(key=key, value=value):
                obs = {**original_obs, key: value}
                rec = {**receipt, 'observation': pr.store_bytes(self.root, pr.canonical(obs))}
                malformed = pr.store_bytes(self.root, pr.canonical(rec))
                with self.assertRaises(PolicyError):
                    pr.validate_run(self.root, malformed, state)
        for key, value in [('repository_id', 'other/repo'), ('plan_id', 'other'), ('gate', 'CP1'),
                           ('lock_hash', 'sha256:' + '0' * 64), ('reviewed_history_sha256', '0' * 64)]:
            with self.subTest(key=key), self.assertRaises(PolicyError):
                pr.validate_run(self.root, ref, {**state, key: value})
        bad_output = pr.store_bytes(self.root, pr.canonical({'ok': True}))
        obs = {**original_obs, 'stdout': bad_output}
        rec = {**receipt, 'observation': pr.store_bytes(self.root, pr.canonical(obs))}
        with self.assertRaises(PolicyError):
            pr.validate_run(self.root, pr.store_bytes(self.root, pr.canonical(rec)), state)

    def test_failed_or_drifting_run_does_not_write_success_record(self):
        self.observer.side_effect = PolicyError(pr.CODE, 'Synthetic nonzero child')
        with self.assertRaises(PolicyError):
            pl.record_gate(self.plan, 'cp0')
        self.assertFalse((self.root / pr.STORE).exists())
        def drift(root, state):
            result = self.synthetic_observer(root, state)
            self.plan.write_text(self.plan.read_text() + '\nChanged during child.\n')
            return result
        self.observer.side_effect = drift
        with self.assertRaises(PolicyError):
            pl.record_gate(self.plan, 'cp0')
        self.assertFalse((self.root / pr.STORE).exists())

    def test_original_cp0_and_cp1_technical_failures_remain_enforced(self):
        self.cp0()
        original_image = self.files['img'].read_bytes()
        self.files['img'].unlink()
        codes, rc = self.codes()
        self.assertEqual(rc, 1)
        self.assertIn('PL-CP0-IMG', codes)
        self.files['img'].write_bytes(original_image)
        self.plan.write_text(self.plan.read_text().replace('- [x] T3.1', '- [ ] T3.1'))
        self.assertIn('PL-CP1-TASKS', self.codes()[0])

    def test_cp1_detects_late_author_mutation_and_final_source_refusal(self):
        self.cp0()
        change = self.root / 'docs/archive/changes/change_20261115_t3-impl.md'
        original = pl._check_plan_technical
        original_text = change.read_text()
        def mutated(*args, **kwargs):
            result = original(*args, **kwargs)
            if args[1] == 'cp1':
                change.write_text(original_text.replace('Codex', 'Gemini'))
            return result
        with patch('plan_lint._check_plan_technical', side_effect=mutated):
            codes, rc = self.codes()
        self.assertEqual(rc, 1)
        self.assertIn('PL-DELEGATE-BINDING', codes)
        change.write_text(original_text)
        from goal_provider import SourceError
        self.base.provider.side_effect = [self.base.provider.return_value, SourceError('Final source refused')]
        self.assertIn('PL-DELEGATE-SOURCE', self.codes()[0])

    def test_record_cli_reports_only_technical_result_and_never_changes_plan(self):
        original = self.plan.read_bytes()
        rc, output, _ = run_cli('record-gate', str(self.plan), '--gate', 'cp0', '--json')
        data = json.loads(output)
        self.assertEqual(rc, 0, output)
        self.assertEqual(data['verdict'], 'technical-pass')
        self.assertEqual(self.plan.read_bytes(), original)
        self.assertNotIn('approved', data)
        self.assertNotIn(self.base.goal.objective, output)
        self.assertNotIn(str(self.root), output)

    def test_plain_legacy_review_cannot_issue_new_delegated_receipt(self):
        nf.NativeIntegrationTests.replace_section(self.files['t2'], 2, '3.',
            'Gemini：`session_id = synthetic`, `task_id = synthetic-task`, `turn_id = synthetic-turn`')
        with self.assertRaisesRegex(PolicyError, 'native review'):
            pl.record_gate(self.plan, 'cp0')

    def test_content_addressing_exclusive_store_and_path_escape(self):
        raw = pr.canonical({'test': 'immutable'})
        ref = pr.store_bytes(self.root, raw)
        self.assertEqual(pr.store_bytes(self.root, raw), ref)
        (self.root / ref['path']).write_bytes(b'bad')
        with self.assertRaises(PolicyError):
            pr.store_bytes(self.root, raw)
        with self.assertRaises(PolicyError):
            pr.content_ref('docs/acceptance/process/../other.json')
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'docs/acceptance').mkdir(parents=True)
            (root / pr.STORE).symlink_to(self.root / pr.STORE, target_is_directory=True)
            with self.assertRaises(PolicyError):
                pr.store_bytes(root, raw)

    def test_real_subprocess_observer_runs_fixed_checker_and_captures_exit(self):
        state = self.state()
        self.base.registry.unlink()  # Real child exercises unchanged legacy CP0, no live source call.
        self.observer_patch.stop()
        output, observation = pr.observe_check(self.root, state)
        self.assertEqual(observation['exit_code'], 0)
        self.assertEqual(json.loads(output)['gate'], 'cp0')
        self.assertNotIn(str(self.root), json.dumps(observation))
        self.files['img'].unlink()
        with self.assertRaises(PolicyError):
            pr.observe_check(self.root, state)

    def test_timeout_and_unsuccessful_cli_cannot_create_records(self):
        state = self.state()
        self.observer_patch.stop()
        self.base.registry.unlink()
        with patch('process_receipts.TIMEOUT', 0.001):
            with self.assertRaises(PolicyError):
                pr.observe_check(self.root, state)
        rc, output, _ = run_cli('record-gate', str(self.plan), '--gate', 'cp0', '--json')
        self.assertEqual(rc, 1)
        self.assertEqual(json.loads(output)['code'], pr.CODE)
        self.assertFalse((self.root / pr.STORE).exists())


if __name__ == '__main__':
    unittest.main()
