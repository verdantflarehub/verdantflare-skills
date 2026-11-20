"""Main-entry regression with synthetic sources, never actual permissions."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import plan_lint as pl
import delegated_policy as dp
from delegated_registry import REGISTRY_PATH
from goal_provider import SourceReadback, SourceError
from native_receipts import digest
from test_plan_lint import build_valid_complex_workspace, run_cli
from test_native_receipts import Capture
import test_goal_evidence as gf


class DelegatedGateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.files = build_valid_complex_workspace(self.root)
        self.plan = self.root / 'docs/plan/2026.11.alpha-gov.plan.md'
        self.plan.write_text(self.plan.read_text().replace('负责人：', '计划 ID：synthetic-main-plan\n负责人：', 1))
        # An actual local Git origin, rather than patching identity detection.
        subprocess.run(['git', 'init', '-q', str(self.root)], check=True, capture_output=True)
        subprocess.run(['git', '-C', str(self.root), 'remote', 'add', 'origin',
                        'https://example.invalid/unit/repository.git'], check=True, capture_output=True)
        self.goal = gf.GoalEvidenceTests()
        self.goal.setUp()
        self.capture = self.goal.meta + self.goal.history
        self.observation = gf.line(self.goal.goal)
        self.cap = Capture(self.root, plan=self.plan, tag='synthetic-scope')
        self.source = self.root / 'capture.jsonl'
        self.source.write_bytes(self.capture)
        self.obs = self.root / 'goal.json'
        self.obs.write_bytes(self.observation)
        self.policy = self.root / 'policy.json'
        self.envelope = self.root / 'envelope.json'
        self.payload = {
            'schema_version': 1, 'policy_id': 'synthetic-main-policy', 'repository_id': 'unit/repository',
            'plan_id': 'synthetic-main-plan', 'plan_path': self.cap.data['plan_path'],
            'archive_plan_path': 'docs/archive/plan/' + self.plan.name,
            'author_sessions': sorted(pl.extract_stage_authors(pl.parse_doc(self.plan), {1, 2})[1]),
            'authorization': {'provider': 'codex-user-goal-v1', 'session_id': self.goal.sid,
                              'event_sha256': digest(self.goal.auth),
                              'capture': self.cap.ref(self.source), 'observation': self.cap.ref(self.obs)},
            'reviewed_user_history': self.goal.history_hash(self.goal.history),
            'allowed_gates': ['CP0', 'CP1'], 'retained_gates': sorted(dp.RETAINED_GATES),
        }
        self.save_policy()
        self.provider = AsyncMock(return_value=SourceReadback(
            self.goal.sid, self.capture, self.observation, 'a' * 64, len(self.capture)))
        self.patch_provider = patch('plan_lint.read_codex_goal', self.provider)
        self.patch_provider.start()
        self.addCleanup(self.patch_provider.stop)

    def save_policy(self):
        self.policy.write_text(json.dumps(self.payload))
        self.cap.data['inputs'] = [self.cap.ref(p) for p in (self.policy, self.source, self.obs)]
        verdict = {'schema_version': 1, 'verdict': 'scope-confirmed',
                   'policy_sha256': digest(self.policy.read_bytes()), 'plan_id': self.payload['plan_id'],
                   'authorization_sha256': self.payload['authorization']['event_sha256'],
                   'reviewed_history_sha256': self.payload['reviewed_user_history'],
                   'provider_readback': True, 'retained_gates': sorted(dp.RETAINED_GATES)}
        response = json.dumps(verdict)
        self.cap.data['response_sha256'] = digest(response.encode())
        self.cap.sources['transcript']['steps'][-1]['content'] = response
        self.cap.sources['observation']['last_response'] = response
        self.cap.save()
        self.envelope.write_text(json.dumps({'schema_version': 1, 'policy_sha256': digest(self.policy.read_bytes()),
                                            'receipt': self.cap.ref(self.cap.path)}))
        self.registry = self.root / REGISTRY_PATH
        self.registry.write_text(json.dumps({'schema_version': 1, 'repository_id': 'unit/repository',
                                             'policies': [{'policy': self.cap.ref(self.policy),
                                                           'envelope': self.cap.ref(self.envelope)}]}))

    def check(self, gate='cp0', **kwargs):
        issues, rc = pl.check_plan(self.plan, gate, **kwargs)
        return {i.code for i in issues}, rc

    def test_main_cp0_runs_technical_checks_and_two_fresh_readbacks(self):
        self.assertEqual(self.check(), (set(), 0))
        self.assertEqual(self.provider.await_count, 2)
        for args in self.provider.await_args_list:
            self.assertEqual(args.args, (self.goal.sid, self.root.resolve()))

    def test_draft_is_structural_only_and_does_not_read_provider(self):
        self.assertEqual(self.check('draft'), (set(), 0))
        self.provider.assert_not_called()

    def test_bad_explicit_registry_blocks_all_early_returns(self):
        self.registry.write_text('{"schema_version":1,"schema_version":1}')
        for gate in ('draft', 'cp0', 'cp1', 'archive-preflight', 'archive'):
            with self.subTest(gate=gate):
                codes, rc = self.check(gate)
                self.assertNotEqual(rc, 0)
                self.assertIn('PL-DELEGATE-SCHEMA', codes)
        self.provider.assert_not_called()

    def test_no_registry_and_unselected_valid_registry_keep_legacy_behavior(self):
        self.registry.unlink()
        self.assertEqual(self.check(), (set(), 0))
        self.registry.write_text(json.dumps({'schema_version': 1, 'repository_id': 'unit/repository', 'policies': []}))
        self.assertEqual(self.check(), (set(), 0))
        self.provider.assert_not_called()

    def test_repo_id_override_does_not_spoof_actual_git_identity(self):
        subprocess.run(['git', '-C', str(self.root), 'remote', 'set-url', 'origin',
                        'https://example.invalid/other/repository.git'], check=True, capture_output=True)
        codes, rc = self.check(repo_id='unit/repository')
        self.assertEqual(rc, 1)
        self.assertIn('PL-DELEGATE-BINDING', codes)
        self.provider.assert_not_called()

    def test_missing_or_changed_plan_id_does_not_fall_back(self):
        original = self.plan.read_text()
        for replacement in ('', '计划 ID：other-plan\n', '计划 ID：synthetic-main-plan\n计划 ID：synthetic-main-plan\n'):
            with self.subTest(replacement=replacement):
                self.plan.write_text(original.replace('计划 ID：synthetic-main-plan\n', replacement))
                codes, rc = self.check('draft')
                self.assertEqual(rc, 1)
                self.assertIn('PL-DELEGATE-BINDING', codes)

    def test_new_history_and_provider_failure_are_not_legacy_approvals(self):
        self.provider.return_value = SourceReadback(self.goal.sid, self.capture + gf.user('stop'),
                                                   self.observation, 'a' * 64, 1)
        codes, rc = self.check()
        self.assertEqual(rc, 1)
        self.assertIn('PL-DELEGATE-SOURCE', codes)
        self.provider.side_effect = SourceError('Source unavailable')
        codes, rc = self.check()
        self.assertEqual(rc, 1)
        self.assertIn('PL-DELEGATE-SOURCE', codes)

    def test_source_change_after_technical_pass_invalidates_result(self):
        original = self.provider.return_value
        paused = copy.deepcopy(self.goal.goal)
        paused['goal']['status'] = 'paused'
        self.provider.side_effect = [original, SourceReadback(self.goal.sid, self.capture,
                                                            gf.line(paused), 'a' * 64, 1)]
        codes, rc = self.check()
        self.assertEqual(rc, 1)
        self.assertIn('PL-DELEGATE-SOURCE', codes)

    def test_registry_disappearance_during_gate_invalidates_result(self):
        original = pl._check_plan_technical
        def changed(*args, **kwargs):
            result = original(*args, **kwargs)
            self.registry.unlink()
            return result
        with patch('plan_lint._check_plan_technical', side_effect=changed):
            codes, rc = self.check()
        self.assertEqual(rc, 1)
        self.assertIn('PL-DELEGATE-BINDING', codes)

    def test_changed_plan_bytes_or_provider_binary_invalidate_success(self):
        original = pl._check_plan_technical
        def changed(*args, **kwargs):
            result = original(*args, **kwargs)
            self.plan.write_text(self.plan.read_text() + '\nChanged during gate.\n')
            return result
        with patch('plan_lint._check_plan_technical', side_effect=changed):
            codes, rc = self.check()
        self.assertEqual(rc, 1)
        self.assertIn('PL-DELEGATE-BINDING', codes)
        original_source = self.provider.return_value
        self.provider.side_effect = [original_source, SourceReadback(
            self.goal.sid, self.capture, self.observation, 'b' * 64, 1)]
        codes, rc = self.check()
        self.assertEqual(rc, 1)
        self.assertIn('PL-DELEGATE-BINDING', codes)

    def test_valid_scope_review_does_not_expand_gate_set(self):
        self.payload['allowed_gates'] = ['UI1']
        self.save_policy()
        codes, rc = self.check()
        self.assertEqual(rc, 1)
        self.assertIn('PL-DELEGATE-BINDING', codes)

    def test_missing_author_source_fails_even_with_passing_human_records(self):
        change = self.root / 'docs/archive/changes/change_20261115_t1-spec.md'
        change.write_text(change.read_text().replace('session_id = sess-author-01', 'missing source'))
        codes, rc = self.check()
        self.assertEqual(rc, 1)
        self.assertIn('PL-DELEGATE-BINDING', codes)

    def test_original_technical_failure_is_preserved(self):
        for image in (self.root / '.gdrive/managed/pending/design').glob('*.png'):
            image.unlink()
        codes, rc = self.check()
        self.assertNotEqual(rc, 0)
        self.assertIn('PL-CP0-IMG', codes)
        self.assertEqual(self.provider.await_count, 1)

    def test_cp1_cannot_use_human_cp0_as_missing_process_receipt(self):
        codes, rc = self.check('cp1')
        self.assertEqual(rc, 1)
        self.assertIn('PL-DELEGATE-RECEIPT', codes)

    def test_deprecated_archive_cannot_bypass_final_source_requirement(self):
        self.plan.write_text(self.plan.read_text().replace('计划状态：进行中', '计划状态：已废弃'))
        for gate in ('archive-preflight', 'archive'):
            codes, rc = self.check(gate)
            self.assertEqual(rc, 1)
            self.assertIn('PL-DELEGATE-H2' if gate == 'archive-preflight' else 'PL-DELEGATE-ARCHIVE', codes)
        self.provider.assert_not_called()

    def test_cli_reports_binding_failure_without_private_source(self):
        self.provider.side_effect = SourceError('Source unavailable')
        rc, output, _ = run_cli('check', str(self.plan), '--gate', 'cp0', '--json')
        result = json.loads(output)
        self.assertEqual(rc, 1)
        self.assertFalse(result['ok'])
        self.assertIn('PL-DELEGATE-SOURCE', result['codes'])
        self.assertNotIn(self.goal.objective, output)
        self.assertNotIn(str(self.root), output)


if __name__ == '__main__':
    unittest.main()
