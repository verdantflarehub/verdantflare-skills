"""Synthetic policy evidence only; these fixtures never authorize real work."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from dataclasses import replace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import delegated_policy as dp
from native_receipts import digest
import test_goal_evidence as goal_fixture
from test_goal_evidence import line, user
from test_native_receipts import Capture


class PolicyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.cap = Capture(self.root, tag='independent-policy-review')
        self.goal = goal_fixture.GoalEvidenceTests()
        self.goal.setUp()
        self.capture = self.goal.meta + self.goal.history
        self.observation = line(self.goal.goal)
        self.source_path = self.root / 'capture.jsonl'
        self.source_path.write_bytes(self.capture)
        self.observation_path = self.root / 'goal.json'
        self.observation_path.write_bytes(self.observation)
        self.payload = {
            'schema_version': 1, 'policy_id': 'synthetic-policy', 'repository_id': 'unit/repository',
            'plan_id': 'unit-plan', 'plan_path': self.cap.data['plan_path'],
            'archive_plan_path': 'docs/archive/plan/' + Path(self.cap.data['plan_path']).name,
            'author_sessions': ['synthetic-author-session'],
            'authorization': {'provider': 'codex-user-goal-v1', 'session_id': self.goal.sid,
                              'event_sha256': digest(self.goal.auth),
                              'capture': self.cap.ref(self.source_path),
                              'observation': self.cap.ref(self.observation_path)},
            'reviewed_user_history': self.goal.history_hash(self.goal.history),
            'allowed_gates': ['UI1', 'CP0', 'CP1'], 'retained_gates': sorted(dp.RETAINED_GATES),
        }
        self.policy = self.root / 'policy.json'
        self.envelope = self.root / 'envelope.json'
        self.save_review()

    def save_review(self):
        self.policy.write_text(json.dumps(self.payload), encoding='utf-8')
        self.cap.data['inputs'] = [self.cap.ref(p) for p in (self.policy, self.source_path, self.observation_path)]
        self.verdict = {'schema_version': 1, 'verdict': 'scope-confirmed',
                        'policy_sha256': digest(self.policy.read_bytes()), 'plan_id': self.payload['plan_id'],
                        'authorization_sha256': self.payload['authorization']['event_sha256'],
                        'reviewed_history_sha256': self.payload['reviewed_user_history'],
                        'provider_readback': True, 'retained_gates': sorted(dp.RETAINED_GATES)}
        self.save_response()

    def save_response(self, text=None):
        response = json.dumps(self.verdict) if text is None else text
        self.cap.data['response_sha256'] = digest(response.encode())
        self.cap.sources['transcript']['steps'][-1]['content'] = response
        self.cap.sources['observation']['last_response'] = response
        self.cap.save()
        self.envelope.write_text(json.dumps({'schema_version': 1, 'policy_sha256': digest(self.policy.read_bytes()),
                                            'receipt': self.cap.ref(self.cap.path)}), encoding='utf-8')

    def check(self, **changes):
        args = {'repository_id': 'unit/repository', 'plan_id': 'unit-plan',
                'plan_path': self.cap.data['plan_path'], 'current_capture': self.capture,
                'current_observation': self.observation, 'current_scope': self.cap.data['scope'],
                'current_review_hash': self.cap.data['review_hash'],
                'author_sessions': {'synthetic-author-session'}}
        args.update(changes)
        return dp.validate_policy_evidence(self.root, self.cap.ref(self.policy), self.cap.ref(self.envelope), **args)

    def rejected(self, code, **changes):
        with self.assertRaises(dp.PolicyError) as found:
            self.check(**changes)
        self.assertEqual(found.exception.code, code)

    def final_args(self, reply='通过'):
        import final_decisions as fd
        from test_final_decisions import FinalDecisionTests, assistant, human
        fixture = FinalDecisionTests(); fixture.setUp()
        target = dict(fixture.target, plan_id=self.payload['plan_id'], plan_path=self.payload['plan_path'])
        conversation = (self.capture + assistant(fd.request_text(target))
                        + human(reply).replace(b'00:00:10Z', b'00:00:12Z'))
        return dict(final_review=target, conversation_capture=conversation,
                    current_capture=fd.goal_projection(conversation))

    def test_final_reply_keeps_original_policy_and_full_current_history(self):
        args = self.final_args()
        result = self.check(**args)
        self.assertEqual(result.reviewed_history_sha256, self.payload['reviewed_user_history'])
        self.assertNotEqual(result.current_goal.user_history_sha256, result.reviewed_history_sha256)
        self.assertEqual(result.final_decision.kind, 'final-delivery')
        self.rejected('PL-DELEGATE-SOURCE', current_capture=args['current_capture'])

    def test_final_reply_cannot_replace_policy_or_independent_review(self):
        args = self.final_args()
        for key in ('authorization_sha256', 'reviewed_history_sha256'):
            bad = copy.deepcopy(args)
            bad['final_review'][key] = '0' * 64
            self.rejected('PL-DELEGATE-H2', **bad)
        self.cap.path.unlink()
        self.rejected('PL-DELEGATE-REVIEW', **args)

    def test_final_branch_rejects_new_scope_refusal_and_missing_source(self):
        import final_decisions as fd
        from test_final_decisions import human
        for reply in ('不同意', '同意，但是先改', '扩大支持范围'):
            self.rejected('PL-DELEGATE-H2', **self.final_args(reply))
        args = self.final_args()
        args['conversation_capture'] += human('撤回批准').replace(b'00:00:10Z', b'00:00:12Z')
        args['current_capture'] = fd.goal_projection(args['conversation_capture'])
        self.rejected('PL-DELEGATE-H2', **args)
        args = self.final_args(); args['conversation_capture'] = b''
        self.rejected('PL-DELEGATE-H2', **args)

    def test_complete_independent_evidence_returns_no_permission(self):
        result = self.check()
        self.assertEqual(result.allowed_gates, ('UI1', 'CP0', 'CP1'))
        self.assertFalse(hasattr(result, 'approved'))
        self.assertFalse(hasattr(result, 'authorized'))
        self.assertNotIn(self.goal.objective, repr(result))

    def test_other_repository_plan_or_path_cannot_replay_policy(self):
        for key, value in [('repository_id', ''), ('repository_id', 'other/repo'),
                           ('plan_id', 'other-plan'), ('plan_path', 'docs/plan/other.plan.md')]:
            with self.subTest(key=key):
                self.rejected('PL-DELEGATE-BINDING', **{key: value})

    def test_unknown_fields_versions_duplicate_json_and_oversize(self):
        original = self.policy.read_bytes()
        for raw in [original[:-1] + b',"schema_version":1}', b'{"unexpected":true}',
                    b'{"number":NaN}', b'\xff', b' ' * (dp.MAX_DOCUMENT + 1)]:
            with self.subTest(raw=raw[:30]):
                self.policy.write_bytes(raw)
                self.rejected('PL-DELEGATE-SCHEMA')
        for version in (True, 2):
            self.payload['schema_version'] = version
            self.save_review()
            self.rejected('PL-DELEGATE-SCHEMA')

    def test_unsupported_or_missing_retained_gates_cannot_be_reviewed_away(self):
        original = copy.deepcopy(self.payload)
        for key, value in [('allowed_gates', ['CP0', 'H2']), ('allowed_gates', ['CP0', 'CP0']),
                           ('retained_gates', ['H2']), ('retained_gates', ['H2', 'H2'])]:
            self.payload = copy.deepcopy(original)
            self.payload[key] = value
            self.save_review()
            self.rejected('PL-DELEGATE-BINDING')

    def test_noncanonical_and_escaping_paths_rejected(self):
        for path in ('../policy', '/policy', './policy', 'evidence//policy', 'C:/policy', 'p\\x', '.'):
            with self.subTest(path=path), self.assertRaises(dp.PolicyError):
                dp.path_name(path)
        self.payload['archive_plan_path'] = 'docs/archive/plan/different.plan.md'
        self.save_review()
        self.rejected('PL-DELEGATE-BINDING')

    def test_external_symlink_cannot_supply_policy(self):
        with tempfile.TemporaryDirectory() as outside:
            target = Path(outside) / 'policy.json'
            target.write_bytes(self.policy.read_bytes())
            link = self.root / 'alias.json'
            link.symlink_to(target)
            with self.assertRaises(dp.PolicyError) as found:
                dp.read_ref(self.root, {'path': 'alias.json', 'sha256': digest(target.read_bytes())}, 'PL-DELEGATE-BINDING')
            self.assertEqual(found.exception.code, 'PL-DELEGATE-BINDING')

    def test_mutated_policy_invalidates_review_even_if_envelope_rehashed(self):
        self.payload['allowed_gates'] = ['CP0']
        self.policy.write_text(json.dumps(self.payload), encoding='utf-8')
        self.save_response()  # refresh envelope; native reviewed inputs stay old
        self.rejected('PL-DELEGATE-REVIEW')

    def test_new_user_input_cannot_hide_behind_unchanged_historical_capture(self):
        self.rejected('PL-DELEGATE-SOURCE', current_capture=self.capture + user('Stop and change scope'))

    def test_current_pause_and_replacement_reject_old_policy(self):
        for field, value in [('status', 'paused'), ('objective', 'Different task')]:
            changed = copy.deepcopy(self.goal.goal)
            changed['goal'][field] = value
            self.rejected('PL-DELEGATE-SOURCE', current_observation=line(changed))
        wrapper, state, fresh = self.goal.state_pair('paused')
        self.rejected('PL-DELEGATE-SOURCE', current_capture=self.capture + wrapper + state,
                      current_observation=line(fresh))

    def test_missing_current_source_does_not_fall_back_to_history(self):
        for field in ('current_capture', 'current_observation'):
            self.rejected('PL-DELEGATE-SOURCE', **{field: b''})

    def test_missing_or_omitted_authors_and_self_review_rejected(self):
        for authors in (set(), {'other-author'}):
            self.rejected('PL-DELEGATE-BINDING', author_sessions=authors)
        self.payload['author_sessions'] = [self.cap.data['session_id']]
        self.save_review()
        self.rejected('PL-DELEGATE-REVIEW', author_sessions={self.cap.data['session_id']})

    def test_scope_review_cannot_point_at_other_core_documents(self):
        self.rejected('PL-DELEGATE-REVIEW', current_scope={'acceptance': 'other.md', 'design': 'different.md'})
        self.rejected('PL-DELEGATE-REVIEW', current_review_hash='sha256:' + '0' * 64)

    def test_source_inspection_is_required_in_the_native_response(self):
        for field, value in [('provider_readback', False), ('provider_readback', 1),
                             ('plan_id', 'other-plan'), ('policy_sha256', '0' * 64),
                             ('retained_gates', ['H2']), ('verdict', 'rejected'), ('schema_version', True)]:
            self.save_review()
            self.verdict[field] = value
            self.save_response()
            self.rejected('PL-DELEGATE-REVIEW')
        for response in ('Passed', '```json\n{}\n```', '{}\n{}'):
            self.save_response(response)
            self.rejected('PL-DELEGATE-REVIEW')

    def test_omitted_review_inputs_and_stale_sources_rejected(self):
        self.cap.data['inputs'] = [self.cap.ref(self.policy)]
        self.save_response()
        self.rejected('PL-DELEGATE-REVIEW')
        self.save_review()
        self.source_path.write_bytes(self.capture + user('New input'))
        self.rejected('PL-DELEGATE-SOURCE')

    def test_envelope_is_not_part_of_its_own_reviewed_payload(self):
        envelope = json.loads(self.envelope.read_bytes())
        envelope['policy_sha256'] = '0' * 64
        self.envelope.write_text(json.dumps(envelope), encoding='utf-8')
        self.rejected('PL-DELEGATE-BINDING')

    def test_source_only_result_cannot_supply_a_missing_native_review(self):
        self.cap.path.unlink()
        self.rejected('PL-DELEGATE-REVIEW')

    def test_live_capture_cannot_drop_reviewed_companions(self):
        companion = line({'timestamp': '1970-01-01T00:00:10Z', 'type': 'event_msg',
                          'payload': {'type': 'user_message', 'message': 'Synthetic prior context'}})
        historical = self.capture.replace(user('Synthetic prior context'), user('Synthetic prior context') + companion)
        self.source_path.write_bytes(historical)
        self.payload['authorization']['capture'] = self.cap.ref(self.source_path)
        self.save_review()
        self.rejected('PL-DELEGATE-SOURCE')
        self.assertEqual(self.check(current_capture=historical).current_goal.user_event_count, 2)

    def test_live_capture_cannot_drop_reviewed_automatic_continuations(self):
        template = '<codex_internal_context source="goal">\nSynthetic continuation\n- Tokens used: 2\n</codex_internal_context>'
        normalized = template.replace('Tokens used: 2', 'Tokens used: <count>')
        historical = self.capture + user(template)
        self.source_path.write_bytes(historical)
        self.payload['authorization']['capture'] = self.cap.ref(self.source_path)
        self.save_review()
        with patch('goal_evidence.AUTO_TEMPLATE_SHA256', digest(normalized.encode())):
            self.rejected('PL-DELEGATE-SOURCE')
            current = historical + user(template.replace('Tokens used: 2', 'Tokens used: 3'))
            result = self.check(current_capture=current)
            self.assertEqual(result.current_goal.automatic_continuation_count, 2)

    def test_current_goal_observation_cannot_regress_after_usage_update(self):
        newer = copy.deepcopy(self.goal.goal)
        newer['goal']['updatedAt'] = 25
        self.observation_path.write_bytes(line(newer))
        self.payload['authorization']['observation'] = self.cap.ref(self.observation_path)
        self.save_review()
        self.rejected('PL-DELEGATE-SOURCE')
        self.check(current_observation=line(newer))
        newer['goal']['updatedAt'] = 30
        self.check(current_observation=line(newer))

    def test_envelope_cannot_validate_a_receipt_replaced_between_reads(self):
        original = dp.validate_receipt
        def replaced_receipt(*args, **kwargs):
            return replace(original(*args, **kwargs), sha256='0' * 64)
        with patch('delegated_policy.validate_receipt', side_effect=replaced_receipt):
            self.rejected('PL-DELEGATE-REVIEW')


if __name__ == '__main__':
    unittest.main()
