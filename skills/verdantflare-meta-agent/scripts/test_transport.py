import asyncio
import sys
import unittest

from verdantflare_agent.transport import MCPClient, TransportError


SERVER = r'''
import json, os, sys, time
counter = 0
for line in sys.stdin:
    request = json.loads(line)
    if 'id' not in request:
        continue
    if request['method'] == 'initialize':
        result = {'protocolVersion': '2024-11-05', 'capabilities': {'tools': {}},
                  'serverInfo': {'name':'test-server','version':'1'}}
    else:
        counter += 1
        if request['params']['name'] == 'slow':
            time.sleep(.2)
        result = {'content': [], 'structuredContent': {'counter': counter, 'pid': os.getpid(),
                  'echo': request['params']['arguments']}}
    print(json.dumps({'jsonrpc':'2.0','id':request['id'],'result':result}), flush=True)
'''


class TransportTests(unittest.IsolatedAsyncioTestCase):
    async def test_one_process_persists_for_multiple_tool_calls(self):
        client = MCPClient([sys.executable, "-u", "-c", SERVER])
        try:
            first = (await client.call("first", {"input": "中文"}))["structuredContent"]
            second = (await client.call("second", {}))["structuredContent"]
            self.assertEqual(first["pid"], second["pid"])
            self.assertEqual(first["echo"]["input"], "中文")
            self.assertEqual(second["counter"], 2)
            self.assertIsNone(client.process.returncode)
        finally:
            await client.close()
        self.assertIsNone(client.process)

    async def test_timed_out_write_is_not_replayed(self):
        client = MCPClient([sys.executable, "-u", "-c", SERVER])
        try:
            await client.connect()
            client.timeout = .02
            with self.assertRaisesRegex(TransportError, "MCP_OUTCOME_UNKNOWN"):
                await client.call("slow", {})
            await asyncio.sleep(.3)
            client.timeout = 1
            response = await client.call("read", {})
            self.assertEqual(response["structuredContent"]["counter"], 2)
        finally:
            await client.close()


if __name__ == "__main__":
    unittest.main()
