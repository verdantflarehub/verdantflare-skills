import copy
import unittest
import test_codex_adapter as fixtures
from verdantflare_agent.adapters.grok import GrokAdapter
from verdantflare_agent.worker import NotAccepted
from verdantflare_agent.store import Conflict


class MockGrok(fixtures.MockCodex):
    async def call(self, name, args):
        result = await super().call(name.replace('grok_', 'codex_', 1), args)
        self.calls[-1] = (name, copy.deepcopy(args))
        value = result.get('structuredContent', {})
        if 'thread_id' in value:
            value['session_id'] = value.pop('thread_id')
        return result


class GrokAdapterTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        fixtures.CodexAdapterTests.setUp(self)
        self.endpoint.update(agent='grok', mode='acp')

    async def test_acp_uses_exact_session(self):
        client = MockGrok(mode='acp')
        receipt = await GrokAdapter([], self.store, client=client).deliver(self.message, self.endpoint, self.h)
        name, args = client.calls[0]
        self.assertEqual(name, 'grok_task_start')
        self.assertEqual(args['session_id'], 'native-worker')
        self.assertNotIn('thread_id', args)
        self.assertEqual(receipt['session_id'], 'native-worker')

    async def test_headless_without_task_binding_resumes_explicit_session(self):
        endpoint = dict(self.endpoint, mode='headless')
        client = MockGrok(mode='headless')
        await GrokAdapter([], self.store, client=client).deliver(self.message, endpoint, self.h)
        self.assertEqual(client.calls[0][1]['session_id'], endpoint['native_session_id'])
        self.assertEqual(client.calls[0][1]['mode'], 'headless')

    async def test_headless_binding_continues_original_turn(self):
        endpoint = dict(self.endpoint, mode='headless', binding={'task_id': 'backend-task', 'turn_id': 'old-turn'})
        client = MockGrok(mode='headless')
        await GrokAdapter([], self.store, client=client).deliver(self.message, endpoint, self.h)
        self.assertNotIn('grok_task_start', [name for name, _ in client.calls])
        args = next(args for name, args in client.calls if name == 'grok_task_continue')
        self.assertEqual(args['expected_turn_id'], 'old-turn')

    async def test_busy_and_stale_bindings_do_not_write(self):
        endpoint = dict(self.endpoint, mode='headless', binding={'task_id': 'backend-task', 'turn_id': 'old-turn'})
        client = MockGrok(mode='headless', state='running')
        adapter = GrokAdapter([], self.store, client=client)
        with self.assertRaises(NotAccepted):
            await adapter.deliver(self.message, endpoint, self.h)
        client.turn = 'another-turn'
        with self.assertRaisesRegex(Conflict, 'STALE_BINDING'):
            await adapter.deliver(self.message, endpoint, self.h)
        self.assertFalse(client.writes)

    async def test_saved_receipt_recovers_without_replay(self):
        client = MockGrok(mode='acp')
        adapter = GrokAdapter([], self.store, client=client)
        await adapter.deliver(self.message, self.endpoint, self.h)
        client.calls.clear()
        result = await adapter.reconcile(self.message, self.endpoint)
        self.assertEqual(result.receipt['turn_id'], 'new-turn')
        self.assertEqual([name for name, _ in client.calls], ['grok_task_status'])
