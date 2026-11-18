"""JSONL process transport. Timed-out writes are never automatically retried."""
import asyncio
import json
import hashlib
import os
from .errors import BridgeError


def backend_rejection(method, error):
    """Classify explicit backend evidence; never infer ownership from UI visibility.

    Untrusted provider text may contain prompts, paths or secrets. Return a digest
    and numeric wire code instead of copying that text into public task logs.
    """
    error = error if isinstance(error, dict) else {}
    message = error.get('message')
    message = message if isinstance(message, str) else ''
    details = {'method': method,
               'backend_error_code': error.get('code') if type(error.get('code')) is int else None,
               'backend_message_sha256': hashlib.sha256(message.encode()).hexdigest()}
    if method != 'thread/resume':
        return BridgeError('BACKEND_ERROR', 'Backend rejected the request', details=details)
    normalized = message.strip().lower().rstrip('.')
    # Deliberately exact: generic busy/locked/already-loaded errors do not prove
    # that a plugin owns the thread. Unknown messages remain unclassified.
    if normalized in ('thread is owned by plugin', 'thread is owned by the vscode plugin'):
        return BridgeError('THREAD_OWNED_BY_PLUGIN',
            'Backend explicitly reports plugin ownership; no turn was submitted. '
            'Use the authorized plugin path after checking permission_alignment, or let the user '
            'release the panel ownership before resuming the original managed task.',
            reason='backend_reported_plugin_owner', details=details)
    if normalized in ('thread not found', 'thread does not exist'):
        reason = 'thread_not_found'
    else:
        reason = 'unclassified_backend_rejection'
    return BridgeError('THREAD_RESUME_REJECTED',
        'Backend rejected thread/resume; no turn was submitted. Plugin ownership is not established. '
        'Inspect the original task, native thread history and backend diagnostics; do not replay '
        'or change permissions automatically. Use the plugin path only when authorized and '
        'permission_alignment is match.', reason=reason, details=details)


class JsonlProcess:
    def __init__(self, argv, *, env=None):
        self.argv = argv
        self.env = env
        self.process = None
        self.pending = {}
        self.pending_methods = {}
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
                        future.set_exception(backend_rejection(self.pending_methods.get(message['id']), message['error']))
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
        self.pending_methods[request_id] = method
        try:
            await self.send({'id': request_id, 'method': method, 'params': params})
            return await asyncio.wait_for(future, timeout)
        except TimeoutError:
            raise BridgeError('BACKEND_TIMEOUT', 'Request outcome is unknown; do not replay writes') from None
        finally:
            self.pending.pop(request_id, None)
            self.pending_methods.pop(request_id, None)

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
