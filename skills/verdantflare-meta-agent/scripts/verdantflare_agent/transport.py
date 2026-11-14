"""Persistent MCP stdio client. No shell, implicit downloads, or write replay."""
from __future__ import annotations

import asyncio
import json
import os


class TransportError(RuntimeError):
    pass


class MCPClient:
    def __init__(self, argv, *, cwd=None, env=None, timeout=30):
        if not isinstance(argv, list) or not argv or any(not isinstance(x, str) or not x for x in argv):
            raise ValueError("argv must be a non-empty string array")
        self.argv = argv
        self.cwd = cwd
        self.env = dict(os.environ if env is None else env)
        self.env.update(PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
        self.timeout = timeout
        self.process = None
        self.reader = None
        self.pending = {}
        self.counter = 0
        self.send_lock = asyncio.Lock()
        self.start_lock = asyncio.Lock()
        self.ready = False

    async def connect(self):
        async with self.start_lock:
            if self.ready and self.process and self.process.returncode is None:
                return
            if self.process is not None:
                await self.close()
            try:
                self.process = await asyncio.create_subprocess_exec(
                    *self.argv, cwd=self.cwd, env=self.env,
                    stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.DEVNULL, limit=16 * 1024 * 1024,
                    creationflags=0x08000000 if os.name == "nt" else 0)
                self.reader = asyncio.create_task(self._read())
                await self.request("initialize", {"protocolVersion": "2024-11-05", "capabilities": {},
                                                   "clientInfo": {"name": "verdantflare-meta-agent", "version": "0.1.0"}})
                await self.send({"jsonrpc": "2.0", "method": "notifications/initialized"})
                self.ready = True
            except Exception:
                await self.close()
                raise TransportError("MCP_CONNECT_FAILED") from None

    async def _read(self):
        try:
            while raw := await self.process.stdout.readline():
                value = json.loads(raw)
                if not isinstance(value, dict):
                    raise ValueError()
                if "method" in value:
                    if "id" in value:
                        if value["method"] == "ping":
                            await self.send({"jsonrpc": "2.0", "id": value["id"], "result": {}})
                        else:
                            # Never grant sampling, roots or permissions by implicit callback.
                            await self.send({"jsonrpc": "2.0", "id": value["id"],
                                             "error": {"code": -32601, "message": "Unsupported client method"}})
                    continue
                future = self.pending.pop(value.get("id"), None)
                if future and not future.done():
                    if "error" in value:
                        future.set_exception(TransportError("MCP_REQUEST_REJECTED"))
                    else:
                        future.set_result(value.get("result"))
        except (ValueError, OSError):
            pass
        finally:
            self.ready = False
            for future in self.pending.values():
                if not future.done():
                    future.set_exception(TransportError("MCP_DISCONNECTED"))
            self.pending.clear()

    async def send(self, value):
        if self.process is None or self.process.returncode is not None:
            raise TransportError("MCP_DISCONNECTED")
        async with self.send_lock:
            try:
                self.process.stdin.write((json.dumps(value, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8"))
                await self.process.stdin.drain()
            except (BrokenPipeError, ConnectionResetError):
                raise TransportError("MCP_DISCONNECTED") from None

    async def request(self, method, params):
        self.counter += 1
        request_id = self.counter
        future = asyncio.get_running_loop().create_future()
        self.pending[request_id] = future
        try:
            await self.send({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
            return await asyncio.wait_for(future, self.timeout)
        except asyncio.TimeoutError:
            raise TransportError("MCP_OUTCOME_UNKNOWN") from None
        finally:
            self.pending.pop(request_id, None)

    async def call(self, name, arguments):
        await self.connect()
        result = await self.request("tools/call", {"name": name, "arguments": arguments})
        if not isinstance(result, dict):
            raise TransportError("MCP_INVALID_RESULT")
        return result

    async def close(self):
        self.ready = False
        process = self.process
        if process and process.returncode is None:
            process.stdin.close()
            try:
                await asyncio.wait_for(process.wait(), 5)
            except asyncio.TimeoutError:
                process.terminate()  # Only this client's owned subprocess, never a host UI.
                await process.wait()
        if self.reader:
            await asyncio.gather(self.reader, return_exceptions=True)
        self.process = self.reader = None
