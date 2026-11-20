import asyncio
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from verdantflare_agent_codex.engine import Engine
from verdantflare_agent_codex.errors import BridgeError
from verdantflare_agent_codex.store import canonical


class InterruptedContinueTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.cwd = canonical(temp.name)
        self.config = dict(database=str(Path(temp.name) / 'tasks.db'), extension='fake',
                           node='fake', codex='fake', allowed_roots=[self.cwd])
        self.engine = Engine(self.config)
        self.store = self.engine.store
        self.start = dict(request_id='initial', thread_id='thread', mode='plugin',
                          cwd=self.cwd, prompt='test')
        self.task = self.store.start(self.start)['id']
        cmd = self.store.claim('first')
        self.store.transition(self.task, 'first', 'completed', turn_id='previous')
        self.store.command_done(cmd['id'], 'first', {})
        self.store.enqueue('continue', dict(task_id=self.task, expected_turn_id='previous',
                                            request_id='next', prompt='next'))

        self.plugin = AsyncMock()
        self.plugin.follow.return_value = self.plugin.owner.return_value = 'owner'
        self.plugin.resnapshot.return_value = dict(type='snapshot', revision=1, conversationState={
            'threadRuntimeStatus': {'type': 'idle'}, 'cwd': self.cwd, 'requests': [],
            'turns': [{'turnId': 'previous', 'status': 'completed'}]})
        self.app = AsyncMock()
        self.turns = [dict(id='previous', status='completed', items=[])]
        self.app.call.return_value = {'thread': dict(id='thread', cwd=self.cwd, turns=self.turns)}
        for name, instance in [('AppServer', self.app), ('Plugin', self.plugin)]:
            mock = self.enterContext(patch('verdantflare_agent_codex.engine.' + name))
            mock.return_value.start = AsyncMock(return_value=instance)
        self.enterContext(patch('verdantflare_agent_codex.engine.resolve_codex_binary', return_value='fake'))

    async def cancel(self):
        return await self.engine.call('codex_task_cancel', dict(task_id=self.task, expected_turn_id=None))

    async def test_worker_killed_after_claim_then_first_restart_cycle_releases(self):
        worker = """import sys,time
from verdantflare_agent_codex.store import Store
s=Store(sys.argv[1])
assert s.claim('lost')['operation']=='continue'
print('claimed',flush=True)
time.sleep(60)
"""
        env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[2]))
        with subprocess.Popen([sys.executable, '-c', worker, self.config['database']],
                              env=env, stdout=subprocess.PIPE, text=True) as process:
            try:
                self.assertEqual('claimed', process.stdout.readline().strip())
            finally:
                process.kill()
        restarted = Engine(self.config)
        await restarted.start()
        try:
            async with asyncio.timeout(5):
                while self.store.task(self.task)['state'] != 'failed':
                    await asyncio.sleep(.01)
            events = self.store.events(self.task)['events']
            self.assertEqual(1, sum(e['kind'] == 'recovery_attempt' for e in events))
            self.assertEqual('CONTINUE_NOT_SUBMITTED', events[-1]['payload']['code'])
        finally:
            await restarted.close()
        self.assertEqual('queued', self.store.start(dict(self.start, request_id='new'))['state'])
        self.plugin.follower.assert_not_awaited()

    async def test_queued_cancel(self):
        self.assertEqual('cancelled', (await self.cancel())['state'])
        self.assertIsNone(self.store.claim('other'))

    async def test_interrupted_cancel(self):
        self.store.claim('lost')
        self.store.mark_lost_owners(0)
        self.assertEqual('cancelled', (await self.cancel())['state'])

    async def test_later_native_turn_or_matching_input_preserves_lease(self):
        self.turns.append(dict(id='manual', status='completed', items=[]))
        with self.assertRaises(BridgeError):
            await self.cancel()
        self.turns.pop()
        self.turns[0]['items'].append(dict(type='userMessage', id='next'))
        with self.assertRaises(BridgeError):
            await self.cancel()
        with self.assertRaisesRegex(BridgeError, 'lease|leased|busy|task'):
            self.store.start(dict(self.start, request_id='new'))

    async def test_later_manual_turn_releases_only_with_consistent_evidence(self):
        from verdantflare_agent_codex.engine import DELEGATION_PREFIX
        text = dict(type='text', text=DELEGATION_PREFIX + 'next')
        self.turns.append(dict(id='manual', status='completed', items=[dict(type='userMessage', content=[text])]))
        snapshot = self.plugin.resnapshot.return_value['conversationState']
        snapshot['turns'].append(dict(turnId='manual', status='completed'))
        with self.assertRaises(BridgeError):
            await self.cancel()
        text['text'] = 'independent panel input'
        self.assertEqual('cancelled', (await self.cancel())['state'])

    async def test_concurrent_claim_prevents_cancellation(self):
        async def claim_during_read(*args):
            self.store.claim('other')
            return 'owner'
        self.plugin.owner.side_effect = claim_during_read
        with self.assertRaises(BridgeError) as error:
            await self.cancel()
        self.assertEqual('STALE_TURN', error.exception.code)
