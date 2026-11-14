import unittest
from verdantflare_agent_grok.backend import _extract_update_event


class ToolEvidenceTests(unittest.TestCase):
    def test_read_locations_are_not_file_changes(self):
        changes=[]
        for extra in [{'kind':'read'},{'_meta':{'x.ai/tool':{'read_only':True}}}]:
            _extract_update_event(dict(sessionUpdate='tool_call_update',toolCallId='read',
                locations=[{'path':'docs/plan.md'}],rawInput={'path':'docs/plan.md'},**extra),[],[],changes,[])
        self.assertEqual(changes,[])
        _extract_update_event({'sessionUpdate':'tool_call_update','toolCallId':'write','kind':'edit',
            'locations':[{'path':'fixture.json'}]},[],[],changes,[])
        self.assertEqual(changes,['fixture.json'])
