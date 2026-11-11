import asyncio
import sys
import unittest
from verdantflare_agent_codex.rpc import JsonlProcess
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
