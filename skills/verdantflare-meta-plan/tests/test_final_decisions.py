"""Synthetic final-review source tests, never real human approvals."""
import copy
from pathlib import Path
import sys
import unittest
import uuid
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import final_decisions as fd
from delegated_policy import PolicyError
from native_receipts import digest
import test_goal_evidence as gf


def assistant(text, *, phase='final_answer', status='completed'):
    return gf.line({'timestamp': '1970-01-01T00:00:11Z', 'type': 'response_item', 'payload': {
        'type': 'message', 'role': 'assistant', 'phase': phase, 'status': status,
        'content': [{'type': 'output_text', 'text': text}]}})


def human(text):
    return gf.user(text) + gf.line({'timestamp': '1970-01-01T00:00:12Z', 'type': 'event_msg',
                                  'payload': {'type': 'user_message', 'message': text}})


class FinalDecisionTests(unittest.TestCase):
    def setUp(self):
        self.g = gf.GoalEvidenceTests(); self.g.setUp()
        self.base = self.g.meta + self.g.history
        self.target = {'schema_version': 1, 'kind': 'final-delivery', 'repository_id': 'unit/repository',
                       'plan_id': 'synthetic-plan', 'plan_path': 'docs/plan/2026.11.synthetic.plan.md',
                       'request_id': str(uuid.uuid4()), 'release_id': 'synthetic-r1',
                       'manifest': {'path': 'delivery/manifest.json', 'sha256': '1' * 64},
                       'cp1': {'path': 'docs/acceptance/process/' + '2' * 64 + '.json', 'sha256': '2' * 64},
                       'authorization_sha256': digest(self.g.auth),
                       'reviewed_history_sha256': self.g.history_hash(self.g.history),
                       'preflight_capture_sha256': digest(self.base), 'preflight_capture_bytes': len(self.base)}

    def conversation(self, reply='通过', **request_options):
        # Both request and reply have nondecreasing native timestamps.
        return (self.base + assistant(fd.request_text(self.target), **request_options)
                + human(reply).replace(b'00:00:10Z', b'00:00:12Z'))

    def validate(self, conversation=None, **kwargs):
        conversation = self.conversation() if conversation is None else conversation
        args = {'session_id': self.g.sid, 'repository_id': 'unit/repository',
                'plan_id': 'synthetic-plan', 'plan_path': self.target['plan_path']}
        args.update(kwargs)
        return fd.validate_final_decision(self.target, conversation, fd.goal_projection(conversation),
                                          gf.line(self.g.goal), **args)

    def test_exact_native_final_request_and_user_reply_preserve_both_histories(self):
        result = self.validate()
        self.assertEqual(result.reviewed_prefix, self.base)
        self.assertNotEqual(result.current_goal.user_history_sha256, self.target['reviewed_history_sha256'])
        self.assertEqual(result.request_id, self.target['request_id'])
        self.assertNotIn(self.g.objective, repr(result))

    def test_allowed_affirmations_and_punctuation(self):
        for text in ('通过', '审核通过。', '同意', '批准！', ' 通过 '):
            with self.subTest(text=text):
                self.validate(self.conversation(text))

    def test_refusal_qualified_approval_and_silence_are_not_acceptance(self):
        for text in ('不同意', '不通过', '同意，但先修改', '通过\n扩大范围', '待审核', ''):
            with self.subTest(text=text), self.assertRaises(PolicyError):
                self.validate(self.conversation(text))
        with self.assertRaises(PolicyError):
            self.validate(self.base + assistant(fd.request_text(self.target)))

    def test_commentary_incomplete_and_assistant_reply_cannot_approve(self):
        for options in ({'phase': 'commentary'}, {'status': 'in_progress'}):
            with self.subTest(options=options), self.assertRaises(PolicyError):
                self.validate(self.conversation(**options))
        with self.assertRaises(PolicyError):
            self.validate(self.base + assistant(fd.request_text(self.target)) + assistant('通过'))

    def test_unbound_or_mismatched_human_companion_rejected(self):
        full = self.conversation()
        with self.assertRaises(PolicyError):
            self.validate(b''.join(full.splitlines(keepends=True)[:-1]))
        with self.assertRaises(PolicyError):
            self.validate(full.replace('"message": "通过"'.encode(), '"message": "不同意"'.encode()))

    def test_request_replay_duplicate_request_and_later_final_reply_rejected(self):
        full = self.conversation()
        for prefix in (assistant(fd.request_text(self.target)), assistant('An unrelated final answer')):
            with self.assertRaises(PolicyError):
                self.validate(full.replace(assistant(fd.request_text(self.target)), assistant(fd.request_text(self.target)) + prefix))
        self.target['request_id'] = str(uuid.uuid4())
        with self.assertRaises(PolicyError):
            self.validate(full)

    def test_cross_plan_repository_session_and_stale_manifest_rejected(self):
        for key, value in [('repository_id', 'other/repo'), ('plan_id', 'other'),
                           ('plan_path', 'docs/plan/other.plan.md'), ('session_id', 'other-session')]:
            with self.subTest(key=key), self.assertRaises(PolicyError):
                self.validate(**{key: value})
        full = self.conversation()
        self.target['manifest']['sha256'] = '3' * 64
        with self.assertRaises(PolicyError):
            self.validate(full)

    def test_new_human_input_before_or_after_reply_invalidates_target(self):
        before = self.base + human('Change the scope') + self.conversation()[len(self.base):]
        after = self.conversation() + human('Actually reject this').replace(b'00:00:10Z', b'00:00:12Z')
        for full in (before, after):
            with self.assertRaises(PolicyError):
                self.validate(full)

    def test_paused_goal_and_changed_projection_rejected(self):
        goal = copy.deepcopy(self.g.goal); goal['goal']['status'] = 'paused'
        with self.assertRaises(PolicyError):
            fd.validate_final_decision(self.target, self.conversation(), fd.goal_projection(self.conversation()),
                gf.line(goal), session_id=self.g.sid, repository_id='unit/repository',
                plan_id='synthetic-plan', plan_path=self.target['plan_path'])
        with self.assertRaises(PolicyError):
            fd.validate_final_decision(self.target, self.conversation(), self.base, gf.line(self.g.goal),
                session_id=self.g.sid, repository_id='unit/repository', plan_id='synthetic-plan',
                plan_path=self.target['plan_path'])

    def test_auto_continuations_preserved_but_human_paste_not_ignored(self):
        text = '<codex_internal_context source="goal">\nSynthetic continuation\n- Tokens used: 2\n</codex_internal_context>'
        template = text.replace('Tokens used: 2', 'Tokens used: <count>')
        auto = gf.user(text).replace(b'00:00:10Z', b'00:00:12Z')
        with patch('goal_evidence.AUTO_TEMPLATE_SHA256', digest(template.encode())):
            result = self.validate(self.conversation() + auto)
            self.assertEqual(result.current_goal.automatic_continuation_count, 1)
            with self.assertRaises(PolicyError):
                self.validate(self.conversation() + human(text).replace(b'00:00:10Z', b'00:00:12Z'))

    def test_preflight_prefix_and_unknown_fields_cannot_be_rewritten(self):
        full = self.conversation()
        self.target['preflight_capture_sha256'] = '0' * 64
        with self.assertRaises(PolicyError):
            self.validate(full)
        self.target['unexpected'] = True
        with self.assertRaises(PolicyError):
            self.validate(full)

    def test_cancellation_is_explicit_and_never_delivery_acceptance(self):
        self.target.update(kind='plan-cancellation', release_id=None, manifest=None, cp1=None)
        result = self.validate(self.conversation('确认取消计划'))
        self.assertEqual(result.kind, 'plan-cancellation')
        with self.assertRaises(PolicyError):
            self.validate(self.conversation('通过'))


if __name__ == '__main__':
    unittest.main()
