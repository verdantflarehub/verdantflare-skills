import asyncio
import concurrent.futures
from contextlib import closing
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from verdantflare_agent_claude.recovery import PREFIX, NATIVE_PREFIX, NATIVE_SUFFIX, read_finished_panel_turn
from verdantflare_agent_claude.store import Store, canonical
from verdantflare_agent_claude.errors import BridgeError
from verdantflare_agent_claude.engine import Engine


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.cwd = canonical(self.root)
        self.path = self.root / 'session.jsonl'
        self.task = {'thread_id': 'session', 'turn_id': 'turn', 'cwd': self.cwd}
        self.live = {'session': {'panel_reachable': True, 'status': 'idle', 'cwd': self.cwd, 'pid': 123, 'updated_at': 42}}
        self.rows = [self.row('user', 'turn', 'old', PREFIX + 'review'),
                     self.row('assistant', 'a1', 'turn', [{'type': 'tool_use', 'id': 't1', 'name': 'Read', 'input': {'file_path': 'a.md'}}], 'tool_use'),
                     self.row('user', 'u1', 'a1', [{'type': 'tool_result', 'tool_use_id': 't1', 'content': 'ok'}]),
                     self.row('assistant', 'a2', 'u1', [{'type': 'text', 'text': 'actual review'}], 'end_turn')]

    def row(self, kind, uid, parent, content, stop=None):
        return {'type': kind, 'uuid': uid, 'parentUuid': parent, 'sessionId': 'session', 'cwd': self.cwd,
                'message': {'content': content, 'stop_reason': stop}}

    def recover(self, rows=None, live=None, suffix=''):
        self.path.write_text(''.join(json.dumps(r) + '\n' for r in (self.rows if rows is None else rows)) + suffix, encoding='utf-8')
        with patch('verdantflare_agent_claude.recovery.find_live_panel_sessions', return_value=self.live if live is None else live), patch('verdantflare_agent_claude.recovery.locate_transcript_path', return_value=self.path):
            return read_finished_panel_turn(self.root, self.task, 'review')

    def test_exact_finished_chain(self):
        result = self.recover()
        self.assertEqual('actual review', result['answer'])
        self.assertEqual([], result['file_changes'])
        self.assertEqual('a2', result['recovery_evidence']['assistant_message_uuid'])

    def test_invalid_evidence_is_not_completion(self):
        variants = []
        for field, value in [('sessionId', 'other'), ('cwd', str(self.root / 'other')), ('uuid', 'wrong')]:
            rows = copy.deepcopy(self.rows); rows[0][field] = value; variants.append(rows)
        rows = copy.deepcopy(self.rows); rows[0]['message']['content'] = PREFIX + 'different'; variants.append(rows)
        rows = copy.deepcopy(self.rows); rows[-1]['parentUuid'] = 'foreign'; variants.append(rows)
        rows = copy.deepcopy(self.rows); rows[-1]['message']['stop_reason'] = None; variants.append(rows)
        rows = copy.deepcopy(self.rows); rows[-1]['message']['content'] = [{'type': 'thinking', 'thinking': 'private'}]; variants.append(rows)
        rows = copy.deepcopy(self.rows); rows[-1]['message']['content'] = [{'type': 'text', 'text': ''}]; variants.append(rows)
        rows = copy.deepcopy(self.rows); rows[2]['message']['content'][0]['tool_use_id'] = 'foreign'; variants.append(rows)
        rows = copy.deepcopy(self.rows); rows.pop(2); rows[-1]['parentUuid'] = 'a1'; variants.append(rows)
        rows = copy.deepcopy(self.rows); rows.append(self.row('user', 'next', 'a2', 'new request')); variants.append(rows)
        variants.append(self.rows[:-1])
        for i, rows in enumerate(variants):
            with self.subTest(case=i): self.assertIsNone(self.recover(rows))
        self.assertIsNone(self.recover(suffix='{partial'))
        self.assertIsNone(self.recover(live={}))
        for key, val in [('status', 'busy'), ('panel_reachable', False), ('cwd', str(self.root / 'other'))]:
            live = copy.deepcopy(self.live); live['session'][key] = val
            self.assertIsNone(self.recover(live=live))

    def test_native_permission_wrapper_exact_match(self):
        rows = copy.deepcopy(self.rows)
        rows[0]['message']['content'] = NATIVE_PREFIX + PREFIX + 'review' + NATIVE_SUFFIX
        self.assertEqual('actual review', self.recover(rows)['answer'])
        rows[0]['message']['content'] += '\nextra instruction'
        self.assertIsNone(self.recover(rows))

    def test_observation_race_is_rejected(self):
        self.recover()
        changed = copy.deepcopy(self.live); changed['session']['updated_at'] += 1
        with patch('verdantflare_agent_claude.recovery.find_live_panel_sessions', side_effect=[self.live, changed]), patch('verdantflare_agent_claude.recovery.locate_transcript_path', return_value=self.path):
            self.assertIsNone(read_finished_panel_turn(self.root, self.task, 'review'))

    def setup_store(self):
        store = Store(self.root / 'tasks.sqlite')
        task = store.start({'request_id': 'r1', 'mode': 'panel', 'cwd': self.cwd, 'session_id': 'session', 'thread_id': 'session', 'prompt': 'review', 'timeout_seconds': 600}, owner='worker')
        command = store.claim('worker')
        store.transition(task['id'], 'worker', 'running', turn_id='turn', thread_id='session')
        store.command_done(command['id'], 'worker', {'accepted': True})
        store.append_event(task['id'], 'panel_injected', {'session_id': 'session', 'turn_id': 'turn'})
        store.mark_lost_owners(10**20)
        return store, store.task(task['id'])

    def test_restore_result_lease_and_idempotency(self):
        store, snapshot = self.setup_store()
        self.assertEqual('review', store.recovery_prompt(snapshot))
        result = self.recover()
        self.assertEqual('completed', store.recover_finished_panel(snapshot, result)['state'])
        self.assertEqual('completed', store.recover_finished_panel(snapshot, result)['state'])
        self.assertEqual('actual review', store.result(snapshot['id'])['result']['answer'])
        events = store.events(snapshot['id'], 0, 100)['events']
        self.assertEqual(1, sum(e['kind'] == 'native_turn_recovered' for e in events))
        next_task = store.enqueue('continue', {'task_id': snapshot['id'], 'request_id': 'r2', 'expected_turn_id': 'turn', 'prompt': 'next'}, owner='next-worker')
        self.assertEqual('queued', next_task['state'])
        with self.assertRaises(BridgeError): store.recover_finished_panel(snapshot, result)

    def test_status_uses_verified_recovery(self):
        store, snapshot = self.setup_store(); self.recover()
        engine = Engine({'database': store.path, 'claude_dir': str(self.root), 'allowed_roots': [self.cwd]})
        with patch('verdantflare_agent_claude.recovery.find_live_panel_sessions', return_value=self.live), patch('verdantflare_agent_claude.recovery.locate_transcript_path', return_value=self.path):
            status = asyncio.run(engine.call('claude_task_status', {'task_id': snapshot['id']}))
        self.assertEqual('completed', status['state'])

    def test_concurrent_recovery_is_once_only(self):
        store, snapshot = self.setup_store(); result = self.recover()
        def run(_): return Store(store.path).recover_finished_panel(snapshot, result)['state']
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as workers:
            self.assertEqual(['completed'] * 8, list(workers.map(run, range(8))))
        self.assertEqual(1, sum(e['kind'] == 'native_turn_recovered' for e in store.events(snapshot['id'], 0, 100)['events']))

    def test_stale_observation_preserves_unknown_and_lease(self):
        store, snapshot = self.setup_store(); result = self.recover()
        store.append_event(snapshot['id'], 'new_observation', {})
        with self.assertRaises(BridgeError): store.recover_finished_panel(snapshot, result)
        self.assertEqual('interrupted_unknown', store.task(snapshot['id'])['state'])
        with closing(store.connect()) as db:
            self.assertEqual(1, db.execute('SELECT count(*) FROM leases WHERE task_id=?', (snapshot['id'],)).fetchone()[0])

    def test_bad_proof_does_not_change_task(self):
        store, snapshot = self.setup_store(); result = self.recover()
        result['recovery_evidence']['turn_id'] = 'foreign'
        with self.assertRaises(BridgeError): store.recover_finished_panel(snapshot, result)
        self.assertEqual('interrupted_unknown', store.task(snapshot['id'])['state'])


if __name__ == '__main__':
    unittest.main()
