import unittest
from verdantflare_agent_codex.snapshot import Snapshot, turn_result
from verdantflare_agent_codex.errors import BridgeError


class SnapshotTests(unittest.TestCase):
    def test_patch_sequence_and_gap(self):
        state = Snapshot()
        state.apply(dict(type='snapshot', revision=10, conversationState={'turns': []}))
        state.apply(dict(type='patches', baseRevision=10, revision=11,
                         patches=[{'op': 'add', 'path': ['turns', 0], 'value': {'turnId': 't', 'status': 'inProgress'}}]))
        state.apply(dict(type='patches', baseRevision=11, revision=12,
                         patches=[{'op': 'replace', 'path': ['turns', 0, 'status'], 'value': 'completed'}]))
        self.assertEqual('completed', state.turns()[0]['status'])
        with self.assertRaises(BridgeError):
            state.apply(dict(type='patches', baseRevision=11, revision=13, patches=[]))
        self.assertIsNone(state.state)

    def test_canonical_history_and_actual_exit_code(self):
        state = Snapshot()
        state.apply(dict(type='snapshot', revision=1, conversationState={
            'turnHistory': {'kind': 'canonical', 'history': {
                'islands': [{'entries': [{'value': 'a'}]}],
                'entitiesByKey': {'a': {'turnId': 'a', 'items': [
                    {'type': 'agentMessage', 'text': 'Tests passed', 'phase': 'final_answer'},
                    {'type': 'commandExecution', 'command': 'test', 'exitCode': 1}]}}}}}))
        result = turn_result(state.turns()[0])
        self.assertEqual(1, result['commands'][0]['exitCode'])
        self.assertEqual('Tests passed', result['answer'])

    def test_invalid_patch_invalidates_whole_snapshot(self):
        state = Snapshot()
        state.apply(dict(type='snapshot', revision=1, conversationState={'turns': []}))
        with self.assertRaises(BridgeError):
            state.apply(dict(type='patches', baseRevision=1, revision=2,
                             patches=[{'op': 'add', 'path': ['turns', 9], 'value': {}}]))
        self.assertIsNone(state.state)
