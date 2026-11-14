import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from verdantflare_agent_codex.engine import Engine
from verdantflare_agent_codex.errors import BridgeError


class RenameTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.engine = Engine(dict(database=str(self.root/'state.db'), codex='fake',
                                  allowed_roots=[str(self.root)], node='fake', extension='fake'))
        self.app = AsyncMock()
        self.factory = patch('verdantflare_agent_codex.engine.AppServer')
        self.factory.start().return_value.start = AsyncMock(return_value=self.app)
        self.args = dict(thread_id='dedicated', cwd=str(self.root), name='Plan｜T1｜Codex')

    async def asyncTearDown(self):
        self.factory.stop()
        self.temp.cleanup()

    def thread(self, **fields):
        return {'thread': dict(id='dedicated', cwd=str(self.root), **fields)}

    async def test_name_is_read_back_without_starting_turn(self):
        self.app.call.side_effect = [self.thread(), {}, self.thread(name=self.args['name'])]
        result = await self.engine.call('codex_session_rename', self.args)
        self.assertEqual(result['naming'], 'native')
        self.assertEqual([c.args[0] for c in self.app.call.call_args_list],
                         ['thread/read', 'thread/name/set', 'thread/read'])
        self.app.close.assert_awaited_once()

    async def test_other_workspace_never_written(self):
        other = self.root/'other'
        other.mkdir()
        self.app.call.return_value = {'thread': {'id': 'dedicated', 'cwd': str(other)}}
        with self.assertRaises(BridgeError):
            await self.engine.call('codex_session_rename', self.args)
        self.assertEqual(self.app.call.await_count, 1)
        self.app.close.assert_awaited_once()

    async def test_readback_mismatch_is_not_success(self):
        self.app.call.side_effect = [self.thread(), {}, self.thread(name='other')]
        with self.assertRaises(BridgeError):
            await self.engine.call('codex_session_rename', self.args)

    async def test_unknown_write_is_not_replayed(self):
        self.app.call.side_effect = [self.thread(), BridgeError('BACKEND_TIMEOUT', 'unknown')]
        with self.assertRaises(BridgeError):
            await self.engine.call('codex_session_rename', self.args)
        self.assertEqual(self.app.call.await_count, 2)
        self.app.close.assert_awaited_once()

    async def test_rename_persists_sqlite_name_and_notifies_live_owner(self):
        from contextlib import closing
        import sqlite3
        codex_home = self.root / '.codex'
        codex_home.mkdir()
        db_path = codex_home / 'state_5.sqlite'
        with closing(sqlite3.connect(db_path)) as conn:
            conn.execute('CREATE TABLE threads (id TEXT PRIMARY KEY, title TEXT, name TEXT, updated_at INTEGER, updated_at_ms INTEGER)')
            conn.execute('INSERT INTO threads VALUES (?, ?, ?, ?, ?)', ('dedicated', 'READY', None, 100, 100000))
            conn.commit()
        self.engine.config['codex_home'] = str(codex_home)
        self.app.call.side_effect = [self.thread(), {}, self.thread(name=self.args['name'])]
        fake_plugin = AsyncMock()
        fake_plugin.follow.return_value = 'owner-1'
        fake_plugin.resnapshot.return_value = {'conversationState': {'title': '确认 READY 状态'}}
        with patch('verdantflare_agent_codex.engine.Plugin') as plugin_cls:
            plugin_cls.return_value.start = AsyncMock(return_value=fake_plugin)
            result = await self.engine.call('codex_session_rename', self.args)
        self.assertEqual(result['name'], self.args['name'])
        self.assertEqual(fake_plugin.notify_unarchived.await_count, 2)
        fake_plugin.close.assert_awaited_once()
        with closing(sqlite3.connect(db_path)) as conn:
            row = conn.execute('SELECT title, name, updated_at FROM threads WHERE id = ?', ('dedicated',)).fetchone()
        self.assertEqual(row[0], self.args['name'])
        self.assertEqual(row[1], self.args['name'])
        self.assertGreater(row[2], 100)
