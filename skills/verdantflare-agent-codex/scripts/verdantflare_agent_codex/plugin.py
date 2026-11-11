"""Dynamic Codex VS Code plugin IPC adapter without extension version locking."""
from pathlib import Path
import json
import os
import shutil
from .rpc import JsonlProcess
from .errors import BridgeError


def _latest_vscode_extension(prefix: str) -> Path | None:
    ext_root = Path.home() / '.vscode' / 'extensions'
    if not ext_root.is_dir():
        return None
    obsolete: dict = {}
    obs_file = ext_root / '.obsolete'
    if obs_file.is_file():
        try:
            loaded = json.loads(obs_file.read_text('utf-8'))
            if isinstance(loaded, dict):
                obsolete = loaded
        except Exception:
            obsolete = {}
    dirs = [d for d in ext_root.glob(f'{prefix}*') if d.is_dir() and (d / 'package.json').is_file()]
    active = [d for d in dirs if not obsolete.get(d.name)] or dirs
    if not active:
        return None
    return max(active, key=lambda p: p.stat().st_mtime).resolve()


def resolve_codex_extension(configured: str | Path | None = None) -> Path:
    cfg_str = str(configured or '')
    if not cfg_str or '.vscode' in cfg_str.lower():
        latest = _latest_vscode_extension('openai.chatgpt-')
        if latest is not None:
            return latest
    return Path(cfg_str) if cfg_str else Path('.')


def resolve_codex_binary(configured: str | Path | None = None, extension: str | Path | None = None) -> str:
    cfg_str = str(configured or '')
    if cfg_str and '.vscode' not in cfg_str.lower():
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
    ext = resolve_codex_extension(extension)
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

    async def follow(self, thread_id):
        owner = await self.owner(thread_id)
        await self.call('subscribe', {'thread_id': thread_id, 'owner': owner})
        return owner

    async def interrupt(self, thread_id, turn_id, owner):
        if not turn_id:
            raise BridgeError('STALE_TURN', 'A nonempty expected turn is required')
        return await self.follower('thread-follower-interrupt-turn', thread_id,
                                   {'mode': 'system', 'expectedTurnId': turn_id}, expected_owner=owner)
