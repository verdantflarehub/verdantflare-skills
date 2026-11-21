"""Dynamic Codex VS Code plugin IPC adapter without extension version locking."""
from pathlib import Path
import asyncio
import json
import os
import shutil
from .rpc import JsonlProcess
from .errors import BridgeError
from .permissions import default_vscode_database, read_selection, assert_selection_current, rollout_path, read_turn_permissions


def resolve_codex_extension(configured: str | Path | None = None) -> Path:
    root = Path(configured) if configured else Path.home() / '.vscode' / 'extensions'
    if root.name.lower() != 'extensions' or (root / 'package.json').is_file():
        return root
    candidates = {d.name: d for d in root.glob('openai.chatgpt-*') if (d / 'package.json').is_file()}
    index = root / 'extensions.json'
    if index.is_file():
        entries = json.loads(index.read_text('utf-8-sig'))
        names = {record.get('relativeLocation') for record in entries
                 if record.get('identifier', {}).get('id', '').lower() == 'openai.chatgpt'}
        candidates = {name: path for name, path in candidates.items() if name in names}
    obsolete = root / '.obsolete'
    if obsolete.is_file():
        retired = json.loads(obsolete.read_text('utf-8-sig'))
        candidates = {name: path for name, path in candidates.items() if not retired.get(name)}
    if len(candidates) != 1:
        raise FileNotFoundError('Cannot identify one active Codex extension in the VS Code extensions directory')
    return next(iter(candidates.values())).resolve()


def resolve_codex_binary(configured: str | Path | None = None, extension: str | Path | None = None) -> str:
    cfg_str = str(configured or '')
    if cfg_str:
        if Path(cfg_str).is_file() or not os.path.isabs(cfg_str):
            return cfg_str
    ext = resolve_codex_extension(extension or cfg_str)
    exe_name = 'codex.exe' if os.name == 'nt' else 'codex'
    direct = ext / 'bin' / 'windows-x86_64' / exe_name
    if direct.is_file():
        return str(direct.resolve())
    if ext.is_dir():
        found = sorted(ext.rglob(exe_name))
        if found:
            return str(found[0].resolve())
    if cfg_str and Path(cfg_str).is_file():
        return cfg_str
    which_bin = shutil.which('codex')
    return str(Path(which_bin).resolve()) if which_bin else cfg_str


def detect_codex_plugin_version(extension: str | Path | None = None) -> str:
    try:
        ext = resolve_codex_extension(extension)
    except (OSError, ValueError):
        return 'unknown'
    manifest_path = ext / 'package.json'
    if manifest_path.is_file():
        try:
            manifest = json.loads(manifest_path.read_text('utf-8'))
            ver = manifest.get('version')
            if isinstance(ver, str) and ver.strip():
                return ver.strip()
        except Exception:
            pass
    return 'unknown'


class Plugin(JsonlProcess):
    def __init__(self, node, extension):
        self.extension = resolve_codex_extension(extension)
        super().__init__([node, str(Path(__file__).with_name('plugin_transport.mjs')), str(self.extension)])

    async def start(self):
        if os.name != 'nt':
            raise BridgeError('UNSUPPORTED_VERSION', 'This plugin adapter requires Windows')
        if not (self.extension / 'package.json').is_file() or not (self.extension / 'out/extension.js').is_file():
            raise BridgeError('OWNER_UNAVAILABLE', 'Codex VS Code extension files not found')
        return await super().start()

    async def owner(self, thread_id):
        result = await self.call('thread-owner-discovery', {'payload': {'hostId': 'local', 'conversationId': thread_id}})
        if result.get('resultType') != 'success' or not result.get('handledByClientId'):
            raise BridgeError('OWNER_UNAVAILABLE', 'No reachable plugin owner for this thread', retryable=True)
        return result['handledByClientId']

    async def follower(self, method, thread_id, payload=None, *, expected_owner=None):
        owner = await self.owner(thread_id)
        if expected_owner and owner != expected_owner:
            raise BridgeError('OWNER_UNAVAILABLE', 'Plugin ownership changed')
        result = await self.call(method, {'owner': owner, 'payload': {'conversationId': thread_id, **(payload or {})}})
        if result.get('resultType') != 'success':
            raise BridgeError('BACKEND_ERROR', 'Plugin rejected the request')
        return result.get('result')

    async def settings(self, thread_id):
        result = await self.follower('thread-follower-read-model-settings', thread_id)
        if not result or not isinstance((result.get('settings') or {}).get('model'), str):
            raise BridgeError('OWNER_UNAVAILABLE', 'Plugin did not return model settings')
        return result['settings']

    async def permission_selection(self, thread_id, owner):
        # Wait for host-side settings updates and recheck the exact live owner.
        await self.follower('thread-follower-read-model-settings', thread_id, expected_owner=owner)
        database = await asyncio.to_thread(default_vscode_database, self.extension)
        return database, await asyncio.to_thread(read_selection, database)

    async def check_permission_selection(self, thread_id, owner, database, selection):
        if await self.owner(thread_id) != owner:
            raise BridgeError('OWNER_UNAVAILABLE', 'Plugin ownership changed before permission submission')
        await asyncio.to_thread(assert_selection_current, database, selection)

    async def effective_turn_permissions(self, thread_id, turn_id, cwd):
        path = await asyncio.to_thread(rollout_path, thread_id, cwd)
        deadline = asyncio.get_running_loop().time() + 5
        searched_history = False
        while True:
            actual = await asyncio.to_thread(read_turn_permissions, path, turn_id)
            if actual is None and not searched_history:
                searched_history = True
                actual = await asyncio.to_thread(read_turn_permissions, path, turn_id, full_history=True)
            if actual is not None:
                return actual
            if asyncio.get_running_loop().time() >= deadline:
                raise BridgeError('PERMISSIONS_UNAVAILABLE', 'Native turn permission evidence did not arrive')
            await asyncio.sleep(.1)

    async def follow(self, thread_id):
        owner = await self.owner(thread_id)
        await self.call('subscribe', {'thread_id': thread_id, 'owner': owner})
        self.bound_owner = owner
        return owner

    async def resnapshot(self, thread_id, owner):
        """Refresh observation only; never replay a turn after a malformed patch."""
        if await self.owner(thread_id) != owner:
            raise BridgeError('OWNER_UNAVAILABLE', 'Plugin ownership changed during state recovery')
        await self.call('unsubscribe', {'thread_id': thread_id})
        while not self.events.empty():
            event = self.events.get_nowait()
            if event.get('method') == 'bridge/disconnected':
                raise BridgeError('BACKEND_DISCONNECTED', 'Plugin disconnected during state recovery')
        await self.call('subscribe', {'thread_id': thread_id, 'owner': owner})
        deadline = asyncio.get_running_loop().time() + 10
        while True:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise BridgeError('BACKEND_TIMEOUT', 'Plugin recovery snapshot did not arrive')
            try:
                event = await asyncio.wait_for(self.events.get(), remaining)
            except TimeoutError:
                raise BridgeError('BACKEND_TIMEOUT', 'Plugin recovery snapshot did not arrive') from None
            if event.get('method') == 'bridge/disconnected':
                raise BridgeError('BACKEND_DISCONNECTED', 'Plugin disconnected during state recovery')
            if event.get('method') == 'plugin/state' and event.get('params', {}).get('conversationId') == thread_id:
                change = event['params']['change']
                if change.get('type') == 'snapshot':
                    if await self.owner(thread_id) != owner:
                        raise BridgeError('OWNER_UNAVAILABLE', 'Plugin ownership changed during state recovery')
                    return change

    async def interrupt(self, thread_id, turn_id, owner):
        if not turn_id:
            raise BridgeError('STALE_TURN', 'A nonempty expected turn is required')
        return await self.follower('thread-follower-interrupt-turn', thread_id,
                                   {'mode': 'system', 'expectedTurnId': turn_id}, expected_owner=owner)

    async def notify_unarchived(self, thread_id):
        return await self.call('notify_unarchived', {'thread_id': thread_id})
