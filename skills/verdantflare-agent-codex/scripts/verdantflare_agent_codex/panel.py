"""Observe threads loaded by explicitly configured VS Code panel hosts."""
import asyncio
import json
import os
import subprocess
import uuid
from pathlib import Path

from .errors import BridgeError
from .rpc import JsonlProcess
from .store import allowed_cwd, canonical


def current_window_mailbox():
    appdata = os.environ.get("APPDATA")
    if os.name != "nt" or not appdata:
        raise ValueError("Current-window discovery requires a local Windows VS Code host")
    command = """
    $p = Get-CimInstance Win32_Process -Filter "ProcessId=$PID"
    while ($p -and $p.Name -ne 'codex.exe') {
        $p = Get-CimInstance Win32_Process -Filter "ProcessId=$($p.ParentProcessId)"
    }
    if (!$p -or $p.CommandLine -notmatch '\\bapp-server\\b') { exit 1 }
    $parent = Get-CimInstance Win32_Process -Filter "ProcessId=$($p.ParentProcessId)"
    if ($parent.Name -eq 'PanelTransport.exe') {
        $parent = Get-CimInstance Win32_Process -Filter "ProcessId=$($parent.ParentProcessId)"
    }
    if ($parent.Name -ne 'Code.exe') { exit 1 }
    $parent.ProcessId
    """
    powershell = Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    host_pid = subprocess.check_output(
        [str(powershell), "-NoProfile", "-NonInteractive", "-Command", command],
        text=True, timeout=15,
    ).strip()
    mailbox = Path(appdata) / "Code/User/globalStorage/verdantflare.codex-panel-window/windows" / str(int(host_pid))
    binding = mailbox.with_suffix(".binding")
    if binding.is_file():
        record = json.loads(binding.read_text("utf-8"))
        if record.get("pid") == int(host_pid) and Path(record["socket"]).is_absolute():
            return Path(record["socket"])
    return mailbox


async def current_window_has_tab(cwd, thread_id):
    if os.name != 'nt' or not os.environ.get('CODEX_THREAD_ID'):
        return False
    try:
        mailbox = await asyncio.to_thread(current_window_mailbox)
        await open_panel_tab(mailbox, cwd, thread_id, inspect_only=True)
        return True
    except (OSError, ValueError, subprocess.SubprocessError, BridgeError):
        return False


def validate_panel_config(config):
    if not isinstance(config, dict) or set(config) != {'client', 'hosts'}:
        raise ValueError('panel_transport requires client and hosts')
    if not isinstance(config['client'], str) or not Path(config['client']).is_absolute():
        raise ValueError('panel_transport client must be an absolute path')
    if not isinstance(config['hosts'], list) or not config['hosts']:
        raise ValueError('panel_transport requires one or more workspace hosts')
    for host in config['hosts']:
        if not isinstance(host, dict) or not {'cwd', 'socket'} <= set(host) or set(host) - {'cwd', 'socket'}:
            raise ValueError('Panel host requires cwd and socket')
        if any(not isinstance(v, str) or not Path(v).is_absolute() for v in host.values()):
            raise ValueError('Panel host paths must be absolute')


class PanelConnection(JsonlProcess):
    async def start(self):
        await super().start()
        try:
            await self.call('initialize', {
                'clientInfo': {'name': 'verdantflare_panel_client', 'version': '1'},
                'capabilities': {'experimentalApi': True},
            }, timeout=8)
            await self.send({'method': 'initialized'})
            return self
        except BaseException:
            await self.close()
            raise

    async def loaded(self):
        ids = set()
        cursor = None
        seen = set()
        while True:
            page = await self.call('thread/loaded/list', {'cursor': cursor, 'limit': 100})
            ids.update(page['data'])
            cursor = page.get('nextCursor')
            if cursor is None:
                return ids
            if cursor in seen:
                raise BridgeError('BACKEND_ERROR', 'Panel repeated its loaded-thread cursor')
            seen.add(cursor)


async def probe_panel_hosts(config, cwd, allowed_roots, thread_id=None):
    """Return confirmed live metadata. A stale or unavailable host cannot confirm an owner."""
    matches = {}
    for host in config['hosts']:
        try:
            scope = allowed_cwd(host['cwd'], allowed_roots)
        except BridgeError:
            continue
        if cwd and cwd != scope:
            continue
        client = PanelConnection([config['client'], host['socket']])
        try:
            await client.start()
            loaded = await client.loaded()
            selected = loaded if thread_id is None else loaded.intersection({thread_id})
            for loaded_id in selected:
                try:
                    reply = await client.call('thread/read', {'threadId': loaded_id, 'includeTurns': False})
                    metadata = reply['thread']
                    if metadata['id'] == loaded_id and canonical(metadata['cwd']) == scope:
                        matches.setdefault(loaded_id, []).append((metadata, host))
                except (BridgeError, OSError):
                    continue
        except (BridgeError, OSError):
            pass
        finally:
            if client.process is not None:
                await client.close()
    return matches

async def connect_panel(config, task):
    """Bind a write to one configured host, without loading a thread to claim ownership."""
    candidates = []
    try:
        for host in config['hosts']:
            try:
                workspace = canonical(host['cwd'])
            except OSError:
                continue
            if workspace != task['cwd'] or not Path(host['socket']).exists():
                continue
            client = await PanelConnection([config['client'], host['socket']]).start()
            try:
                if task['thread_id'] in await client.loaded():
                    thread = (await client.call('thread/read', {'threadId': task['thread_id']}))['thread']
                    if thread['id'] == task['thread_id'] and canonical(thread['cwd']) == task['cwd']:
                        candidates.append((client, host))
                        continue
            except BaseException:
                await client.close()
                raise
            await client.close()
        if len(candidates) != 1:
            raise BridgeError('OWNER_UNAVAILABLE', 'A unique live panel host is required')
        return candidates.pop()
    finally:
        for client, _ in candidates:
            await client.close()

async def create_panel_session(config, store, args):
    hosts = [host for host in config['hosts'] if canonical(host['cwd']) == args['cwd']]
    if len(hosts) != 1:
        raise BridgeError('OWNER_UNAVAILABLE', 'Select a uniquely bound project window before creating a session')
    host = hosts[0]
    client = PanelConnection([config['client'], host['socket']])
    await client.start()
    try:
        first, thread_id, opened = store.reserve_panel_creation(args)
        if first:
            result = await client.call('thread/start', {'cwd': args['cwd']})
            thread = result['thread']
            thread_id = str(uuid.UUID(thread['id']))
            if canonical(thread['cwd']) != args['cwd']:
                raise BridgeError('WORKSPACE_DENIED', 'Panel created the session in a different workspace')
            store.record_panel_creation(args['request_id'], thread_id)
        if not thread_id:
            raise BridgeError('RECOVERY_UNCONFIRMED', 'Session creation is pending confirmation; reuse the same request')
        if not opened:
            # The native backend materializes an empty thread when its name is saved.
            await client.call('thread/name/set', {'threadId': thread_id, 'name': args.get('name', '新会话')})
        # An earlier receipt proves creation, but the window or tab may since have closed.
        await open_panel_tab(host['socket'], args['cwd'], thread_id)
        async with asyncio.timeout(15):
            while thread_id not in await client.loaded():
                await asyncio.sleep(.2)
        if not opened:
            store.record_panel_creation(args['request_id'], thread_id, opened=True)
        return {'thread_id': thread_id, 'cwd': args['cwd'], 'mode': 'plugin', 'opened': True}
    except TimeoutError:
        raise BridgeError('OWNER_UNAVAILABLE', 'Session tab opened but host load is unconfirmed') from None
    finally:
        await client.close()


async def open_panel_tab(socket, cwd, thread_id, *, inspect_only=False):
    endpoint = Path(socket)
    request = endpoint.with_name(f'{endpoint.name}.open.{uuid.uuid4()}')
    temporary = request.with_suffix('.tmp.' + str(uuid.uuid4()))
    receipt = request.with_name(request.name + '.reply')
    try:
        temporary.write_text(json.dumps({'cwd': cwd, 'thread_id': thread_id, 'action': 'inspect' if inspect_only else 'open'}), encoding='utf8')
        temporary.replace(request)
        async with asyncio.timeout(20):
            while not receipt.is_file():
                await asyncio.sleep(.1)
        reply = json.loads(receipt.read_text(encoding='utf8'))
        if reply.get('opened') is not True or reply.get('thread_id') != thread_id:
            raise BridgeError('OWNER_UNAVAILABLE', 'VS Code did not confirm the requested session tab')
        return reply
    except TimeoutError:
        raise BridgeError('OWNER_UNAVAILABLE', 'No window receipt; retry creation with the original request ID') from None
    finally:
        temporary.unlink(missing_ok=True)
        request.unlink(missing_ok=True)
        receipt.unlink(missing_ok=True)
