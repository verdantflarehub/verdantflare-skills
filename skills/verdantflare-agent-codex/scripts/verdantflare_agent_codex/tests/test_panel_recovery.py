import tempfile
import unittest
from unittest.mock import AsyncMock

from verdantflare_agent_codex.errors import BridgeError
from verdantflare_agent_codex.recovery import panel_turn_id
from verdantflare_agent_codex.store import canonical


class PanelRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_only_the_native_user_message_marker_can_identify_a_turn(self):
        with tempfile.TemporaryDirectory() as cwd:
            task = {'cwd': canonical(cwd), 'thread_id': 'th'}
            thread = {'thread': {'id': 'th', 'cwd': cwd}}
            first = {'id': 'old', 'items': [{'type': 'agentMessage', 'clientId': 'expected'}]}
            second = {'id': 'correct', 'items': [{'type': 'userMessage', 'clientId': 'expected'}]}
            app = AsyncMock()
            app.call.side_effect = [
                thread, {'data': [first], 'nextCursor': 'page2'}, {'data': [second], 'nextCursor': None}]
            self.assertEqual('correct', await panel_turn_id(app, task, 'expected'))
            for other in ([], [second, dict(second, id='collision')]):
                app.call.side_effect = [thread, {'data': other, 'nextCursor': None}]
                with self.assertRaises(BridgeError) as caught:
                    await panel_turn_id(app, task, 'expected')
                self.assertEqual('RECOVERY_UNCONFIRMED', caught.exception.code)
