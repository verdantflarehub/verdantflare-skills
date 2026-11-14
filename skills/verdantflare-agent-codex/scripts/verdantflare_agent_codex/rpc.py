"""JSONL process transport. Timed-out writes are never automatically retried."""
import asyncio
import json
import os
from .errors import BridgeError


class JsonlProcess:
    def __init__(self, argv, *, env=None):
        self.argv = argv
        self.env = env
        self.process = None
        self.pending = {}
        self.events = asyncio.Queue(maxsize=2048)
        self.sequence = 0
        self.reader_task = None
        self.write_lock = asyncio.Lock()
        self.closed = False

    async def start(self):
        self.process = await asyncio.create_subprocess_exec(
            *self.argv, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL, env=self.env,
            limit=16 * 1024 * 1024, creationflags=0x08000000 if os.name == 'nt' else 0)
        self.reader_task = asyncio.create_task(self._read())
        return self

    async def _read(self):
        try:
            while line := await self.process.stdout.readline():
                message = json.loads(line)
                # Requests from the backend may use the same numeric ID space.
                if 'method' not in message and message.get('id') in self.pending:
                    future = self.pending.pop(message['id'])
                    if 'error' in message:
                        future.set_exception(BridgeError('BACKEND_ERROR', 'Backend rejected the request'))
                    else:
                        future.set_result(message.get('result'))
                else:
                    self.events.put_nowait(message)
        except (ValueError, asyncio.QueueFull):
            pass
        finally:
            self.closed = True
            for future in self.pending.values():
                if not future.done():
                    future.set_exception(BridgeError('BACKEND_DISCONNECTED', 'Backend connection closed'))
            self.pending.clear()
            # An event gap is a loss of certainty; don't silently drop it.
            if self.events.full():
                self.events.get_nowait()
            self.events.put_nowait({'method': 'bridge/disconnected'})

    async def send(self, message):
        if self.closed or self.process is None:
            raise BridgeError('BACKEND_DISCONNECTED', 'Backend is unavailable')
        async with self.write_lock:
            try:
                self.process.stdin.write((json.dumps(message, ensure_ascii=False) + '\n').encode())
                await self.process.stdin.drain()
            except (BrokenPipeError, ConnectionResetError):
                raise BridgeError('BACKEND_DISCONNECTED', 'Backend connection closed') from None

    async def call(self, method, params, *, timeout=30):
        self.sequence += 1
        request_id = self.sequence
        future = asyncio.get_running_loop().create_future()
        self.pending[request_id] = future
        try:
            await self.send({'id': request_id, 'method': method, 'params': params})
            return await asyncio.wait_for(future, timeout)
        except TimeoutError:
            raise BridgeError('BACKEND_TIMEOUT', 'Request outcome is unknown; do not replay writes') from None
        finally:
            self.pending.pop(request_id, None)

    async def close(self):
        self.closed = True
        if self.process is not None and self.process.returncode is None:
            self.process.terminate()
            await self.process.wait()
        if self.reader_task:
            await self.reader_task


class AppServer(JsonlProcess):
    def __init__(self, binary):
        env = dict(os.environ, VERDANTFLARE_AGENT_CODEX_DEPTH='1')
        super().__init__([binary, 'app-server', '--listen', 'stdio://'], env=env)

    async def start(self):
        await super().start()
        try:
            await self.call('initialize', {'clientInfo': {'name': 'verdantflare_agent_codex', 'version': '0.1.0'},
                                          'capabilities': {'experimentalApi': False}})
            await self.send({'method': 'initialized'})
            return self
        except BaseException:
            await self.close()
            raise
