import asyncio
import sys
import json
import tempfile
from pathlib import Path
import unittest
from verdantflare_agent_codex.rpc import JsonlProcess, windows_tool_path_args
from verdantflare_agent_codex.errors import BridgeError

FAKE = '''import sys,json,time
for line in sys.stdin:
 r=json.loads(line)
 if r['method']=='exit': break
 if r['method']=='timeout': time.sleep(1); continue
 print(json.dumps({'id':r['id'],'method':'approval','params':{}}),flush=True)
 print(json.dumps({'id':r['id'],'result':r['params']}),flush=True)
'''


class RpcTests(unittest.IsolatedAsyncioTestCase):
    def test_windows_tools_extend_only_posix_path_override(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / 'config.toml'
            tool = Path(tmp) / 'git.exe'
            tool.touch()
            for value in ('/usr/bin:/bin', '', 'C:/Tools', 'C:/Tools;D:/Apps'):
                config.write_text('[shell_environment_policy.set]\nPath=' + json.dumps(value), encoding='utf8')
                args = windows_tool_path_args(config, [tool, Path(tmp) / 'missing.exe'])
                if value == '/usr/bin:/bin':
                    self.assertEqual(args, ['-c', 'shell_environment_policy.set.Path=' +
                                            json.dumps(value + ';' + tmp, ensure_ascii=False)])
                else:
                    self.assertEqual(args, [])

    async def test_separate_server_request_id_namespace(self):
        rpc = await JsonlProcess([sys.executable, '-u', '-c', FAKE]).start()
        try:
            self.assertEqual({'ok': True}, await rpc.call('echo', {'ok': True}))
            event = await rpc.events.get()
            self.assertEqual('approval', event['method'])
        finally:
            await rpc.close()

    async def test_disconnect_fails_pending(self):
        rpc = await JsonlProcess([sys.executable, '-u', '-c', FAKE]).start()
        try:
            with self.assertRaises(BridgeError) as caught:
                await rpc.call('exit', {})
            self.assertEqual('BACKEND_DISCONNECTED', caught.exception.code)
        finally:
            await rpc.close()

    async def test_timeout_is_unknown_not_retryable(self):
        rpc = await JsonlProcess([sys.executable, '-u', '-c', FAKE]).start()
        try:
            with self.assertRaises(BridgeError) as caught:
                await rpc.call('timeout', {}, timeout=0.1)
            self.assertEqual('BACKEND_TIMEOUT', caught.exception.code)
            self.assertFalse(caught.exception.retryable)
            self.assertEqual(1, rpc.sequence)
        finally:
            await rpc.close()
