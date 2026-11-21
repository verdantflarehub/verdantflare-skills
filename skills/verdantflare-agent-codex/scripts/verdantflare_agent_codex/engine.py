"""Task execution on owned App Server sessions and explicitly selected plugin threads."""
import asyncio
from contextlib import closing, suppress
import json
import os
from pathlib import Path
import sqlite3
import time
import uuid
import threading
import sys
import traceback

from .errors import BridgeError
from .build_info import SERVICE_BUILD
from .recovery import panel_turn_id, read_native_result
from .plugin import Plugin, detect_codex_plugin_version, resolve_codex_binary
from .panel import PanelConnection, connect_panel, create_panel_session, current_window_has_tab, open_panel_tab, probe_panel_hosts
from .rpc import AppServer
from .schema import validate
from .snapshot import Snapshot, turn_result
from .store import Store, TERMINAL, allowed_cwd, canonical
from .approval_ui import ApprovalUI, decisions
from .permissions import PermissionSelection, assert_effective_selection, assert_supported_thread, effective_permissions, assert_no_pending_permission_conflict, permission_comparison


DELEGATION_PREFIX = '[Delegated by verdantflare-agent-codex; do not call verdantflare-agent-codex recursively.]\n'


def public_task(task):
    return {'task_id': task['id'], 'state': task['state'], 'mode': task['mode'],
            'thread_id': task['thread_id'], 'turn_id': task['turn_id'],
            'created_at': task['created'], 'deadline_at': task['deadline']}


class Engine:
    def __init__(self, config):
        self.config = config
        self.store = Store(config['database'])
        self.owner = str(uuid.uuid4())
        self.runs = {}
        self.connections = {}
        self.pending_wire = {}
        self.db_calls = set()
        self.worker = None
        self.heartbeat_worker = None
        self.heartbeat_stop = threading.Event()
        self.heartbeat_interval = 5
        self.heartbeat_thread = None
        self.stopping = False
        self.approval_ui = None
        self.approval_lock = asyncio.Lock()
        self.health_timeout = 20
        self.loop_progress = {'work': 0.0, 'recovery': 0.0}
        self.loop_restarts = {'work': 0, 'recovery': 0}
        self.loop_failures = {'work': 0, 'recovery': 0}
        self.restart_handles = []
        self.pending_command = None

    async def start(self):
        self.store.heartbeat(self.owner)
        self.heartbeat_thread = threading.Thread(target=self._pulse, daemon=True)
        self.heartbeat_thread.start()
        self._spawn_loop('work')
        self._spawn_loop('recovery')

    async def close(self):
        self.stopping = True
        self.heartbeat_stop.set()
        for handle in self.restart_handles:
            handle.cancel()
        if self.approval_ui:
            await self.approval_ui.close()
        if self.heartbeat_worker:
            self.heartbeat_worker.cancel()
            with suppress(asyncio.CancelledError):
                await self.heartbeat_worker
        if self.worker:
            self.worker.cancel()
            with suppress(asyncio.CancelledError):
                await self.worker
        # A disconnected client is not authorization to kill an active plugin turn.
        for task_id, run in list(self.runs.items()):
            run.cancel()
        for run in list(self.runs.values()):
            with suppress(asyncio.CancelledError, BridgeError):
                await run
        for connection in list(self.connections.values()):
            await connection.close()
        if self.db_calls:
            await asyncio.gather(*list(self.db_calls), return_exceptions=True)
        self.heartbeat_stop.set()
        if self.heartbeat_thread:
            await asyncio.to_thread(self.heartbeat_thread.join)


    async def call(self, name, arguments):
        args = validate(name, arguments)
        if name == 'codex_capabilities':
            return await self.capabilities(args)
        if name == 'codex_session_create':
            if os.environ.get('VERDANTFLARE_AGENT_CODEX_DEPTH'):
                raise BridgeError('WORKSPACE_DENIED', 'Recursive delegation to this service is disabled')
            args['cwd'] = allowed_cwd(args['cwd'], self.config['allowed_roots'])
            if not self.config.get('panel_transport'):
                raise BridgeError('OWNER_UNAVAILABLE', 'Configure a project-bound plugin window for session creation')
            return await create_panel_session(self.config['panel_transport'], self.store, args)
        if name == 'codex_session_rename':
            return await self.rename_session(args)
        if name == 'codex_task_start':
            if os.environ.get('VERDANTFLARE_AGENT_CODEX_DEPTH'):
                raise BridgeError('WORKSPACE_DENIED', 'Recursive delegation to this service is disabled')
            args['cwd'] = allowed_cwd(args['cwd'], self.config['allowed_roots'])
            replay = self.store.replay('start', args)
            if replay:
                return public_task(replay)
            if args['mode'] == 'plugin' and self.config.get('panel_transport'):
                cfg = self.config['panel_transport']
                if any(canonical(host['cwd']) == args['cwd'] for host in cfg['hosts']):
                    return public_task(self.store.start(args, panel_queue=True))
            if args['mode'] == 'plugin':
                connection = await Plugin(self.config['node'], self.config['extension']).start()
                try:
                    await connection.follow(args['thread_id'])
                    snapshot = await self._snapshot(connection, args['thread_id'])
                    snapshot.assert_idle(args['cwd'])
                finally:
                    await connection.close()
            task = self.store.start(args)
            return public_task(task)
        if name == 'codex_task_status':
            task = self.store.task(args['task_id'])
            return {**public_task(task), **self.store.events(task['id'], args['after_event'], args['limit']),
                    'pending': self.store.pending_list(task['id']), 'last_event_at': task['updated']}
        if name == 'codex_task_result':
            result = self.store.result(args['task_id'], args.get('turn_id'))
            # Deterministic text chunks bound each response, even with large tool outputs.
            payload = json.dumps(result.pop('result'), ensure_ascii=False)
            offset = int(args.get('cursor', '0'))
            result.update(content_json=payload[offset:offset + 32768],
                          next_cursor=str(offset + 32768) if offset + 32768 < len(payload) else None)
            return result
        operation = name.removeprefix('codex_task_')
        if operation in ('continue', 'respond') and self.store.task(args['task_id'])['mode'] != 'plugin':
            raise BridgeError('MODE_RETIRED', 'Standalone tasks are historical records')
        if operation == 'cancel' and args['expected_turn_id'] is None:
            task = self.store.task(args['task_id'])
            candidate = self.store.queued_continue(task)
            if task['state'] not in ('queued', 'interrupted_unknown') or not candidate:
                raise BridgeError('STALE_TURN', 'No unsubmitted continuation to verify')
            connection = await Plugin(self.config['node'], self.config['extension']).start()
            try:
                owner = await connection.follow(task['thread_id'])
                await self._confirm_unsubmitted(connection, owner, task, candidate)
                return public_task(self.store.finish_unsubmitted(task, candidate, 'cancelled'))
            finally:
                await connection.close()
        if operation == 'respond':
            pending = self.store.pending_list(args['task_id'])
            entry = next((p for p in pending if p['id'] == args['pending_request_id']), None)
            if entry and entry['kind'] == 'input':
                response = args['response']
                if set(response) != {'answers'} or not isinstance(response['answers'], dict):
                    raise BridgeError('INVALID_ARGUMENT', 'User input response requires answers')
                questions = entry['payload'].get('params', {}).get('questions', [])
                if set(response['answers']) != {q['id'] for q in questions}:
                    raise BridgeError('INVALID_ARGUMENT', 'Answers must match pending questions')
                for answer in response['answers'].values():
                    if not isinstance(answer, dict) or set(answer) != {'answers'} or not isinstance(answer['answers'], list) or not all(isinstance(v, str) for v in answer['answers']):
                        raise BridgeError('INVALID_ARGUMENT', 'Invalid answer format')
            elif entry and entry['kind'] != 'approval':
                raise BridgeError('APPROVAL_REQUIRES_UI', 'This request must be answered in the host UI')
        return public_task(self.store.enqueue(operation, args))

    async def rename_session(self, args):
        cwd = allowed_cwd(args['cwd'], self.config['allowed_roots'])
        binary = resolve_codex_binary(self.config.get('codex'), self.config.get('extension'))
        app = await AppServer(binary).start()
        try:
            params = {'threadId': args['thread_id'], 'includeTurns': False}
            def check(result):
                thread = result.get('thread', {})
                if thread.get('id') != args['thread_id'] or not thread.get('cwd'):
                    raise BridgeError('WORKSPACE_DENIED', 'Thread identity could not be verified')
                if allowed_cwd(thread['cwd'], self.config['allowed_roots']) != cwd:
                    raise BridgeError('WORKSPACE_DENIED', 'Thread belongs to another workspace')
                return thread
            check(await app.call('thread/read', params))
            await app.call('thread/name/set', {'threadId': args['thread_id'], 'name': args['name']})
            thread = check(await app.call('thread/read', params))
            if thread.get('name') != args['name']:
                raise BridgeError('BACKEND_ERROR', 'Native name readback did not match; inspect before retrying')
            await self._refresh_renamed_session(args['thread_id'], args['name'])
            return {'thread_id': args['thread_id'], 'name': thread['name'], 'naming': 'native'}
        finally:
            await app.close()

    def _sync_sqlite_thread_name(self, thread_id, name):
        codex_home = Path(self.config.get('codex_home') or (Path.home() / '.codex'))
        if not codex_home.is_dir():
            return
        now = int(time.time())
        for db_path in sorted(codex_home.glob('state_*.sqlite')):
            with suppress(Exception), closing(sqlite3.connect(db_path, timeout=2.0)) as conn:
                conn.execute(
                    'UPDATE threads SET title = ?, name = ?, '
                    'updated_at = CASE WHEN updated_at >= ? THEN updated_at + 1 ELSE ? END, '
                    'updated_at_ms = CASE WHEN COALESCE(updated_at_ms, 0) >= ? THEN updated_at_ms + 1000 ELSE ? END '
                    'WHERE id = ?',
                    (name, name, now, now, now * 1000, now * 1000, thread_id))
                conn.commit()

    async def _refresh_renamed_session(self, thread_id, name):
        await asyncio.to_thread(self._sync_sqlite_thread_name, thread_id, name)
        plugin = None
        with suppress(Exception):
            plugin = await Plugin(self.config['node'], self.config['extension']).start()
            old_title = None
            with suppress(Exception):
                owner = await plugin.follow(thread_id)
                snap = await plugin.resnapshot(thread_id, owner)
                old_title = (snap.get('conversationState') or {}).get('title')
            if isinstance(old_title, str) and old_title.strip() and old_title != name:
                await asyncio.to_thread(self._sync_sqlite_thread_name, thread_id, old_title)
                await plugin.notify_unarchived(thread_id)
                await asyncio.sleep(0.15)
                await asyncio.to_thread(self._sync_sqlite_thread_name, thread_id, name)
            await plugin.notify_unarchived(thread_id)
        if plugin:
            with suppress(Exception):
                await plugin.close()

    async def capabilities(self, args):
        cwd = allowed_cwd(args['cwd'], self.config['allowed_roots']) if args.get('cwd') else None
        codex_bin = resolve_codex_binary(self.config.get('codex'), self.config.get('extension'))
        app = await AppServer(codex_bin).start()
        plugin = None
        try:
            tab_confirmed = False
            panel_hosts = (await probe_panel_hosts(self.config['panel_transport'], cwd, self.config['allowed_roots'], args.get('thread_id'))
                           if self.config.get('panel_transport') else {})
            if args.get('thread_id') in panel_hosts:
                result = {'data': [panel_hosts[args['thread_id']][0][0]], 'nextCursor': None}
            elif args.get('thread_id'):
                metadata = await app.call('thread/read', {'threadId': args['thread_id'], 'includeTurns': False})
                thread = metadata['thread']
                if cwd and thread['id'] == args['thread_id'] and thread.get('cwd') and canonical(thread['cwd']) == cwd:
                    tab_confirmed = await current_window_has_tab(cwd, thread['id'])
                matches = (thread['id'] == args['thread_id'] and
                           (tab_confirmed or thread.get('source') == 'vscode' or self.store.known_panel_session(thread['id'])))
                result = {'data': [thread] if matches else [], 'nextCursor': None}
            else:
                result = await app.call('thread/list', {'cwd': cwd, 'limit': args['limit'],
                    'sourceKinds': [] if self.config.get('panel_transport') else ['vscode'],
                    'modelProviders': [], 'useStateDbOnly': True,
                    'cursor': args.get('cursor')})
            try:
                plugin = await Plugin(self.config['node'], self.config['extension']).start()
            except (BridgeError, OSError):
                pass
            rows = []
            filtered_out_count = 0
            for thread in result['data']:
                if (self.config.get('panel_transport') and thread.get('source') != 'vscode'
                        and not tab_confirmed and thread['id'] not in panel_hosts and not self.store.known_panel_session(thread['id'])):
                    continue
                if not thread.get('cwd'):
                    continue
                try:
                    thread_cwd = allowed_cwd(thread['cwd'], self.config['allowed_roots'])
                    if args.get('thread_id') and cwd and thread_cwd != cwd:
                        continue
                except BridgeError:
                    filtered_out_count += 1
                    continue
                reachable = False
                alignment = {'status': 'unknown', 'reason': 'owner_unavailable'}
                live_hosts = panel_hosts.get(thread['id'], [])
                if live_hosts:
                    reachable = len(live_hosts) == 1
                    alignment = {'status': 'unknown', 'reason': 'panel_permissions_unverified' if reachable else 'multiple_panel_hosts'}
                    if reachable and args.get('thread_id'):
                        from .permissions import read_selection
                        try:
                            bound = await open_panel_tab(live_hosts[0][1]['socket'], thread_cwd, thread['id'], inspect_only=True)
                            selection = await asyncio.to_thread(read_selection, bound['permission_database'])
                            alignment = {'status': 'host_selected', 'source': 'bound_panel_selection',
                                         'mode': selection.mode, 'expected': selection.request_fields(),
                                         'effective_check': 'before_submission_and_at_native_turn'}
                        except (BridgeError, OSError, KeyError) as error:
                            alignment['reason'] = getattr(error, 'code', 'permission_read_failed')
                elif plugin:
                    try:
                        await plugin.settings(thread['id'])
                        reachable = True
                        try:
                            owner = await plugin.follow(thread['id'])
                            snapshot = await self._snapshot(plugin, thread['id'])
                            _, selection = await plugin.permission_selection(thread['id'], owner)
                            assert_supported_thread(snapshot.state)
                            alignment = permission_comparison(snapshot.state, selection)
                        except Exception as error:
                            # A diagnostic read failure must not remove this or later rows.
                            alignment = {'status': 'unknown', 'reason': error.code if isinstance(error, BridgeError)
                                         else 'permission_read_failed'}
                    except Exception as error:
                        alignment = {'status': 'unknown', 'reason': error.code if isinstance(error, BridgeError)
                                     else 'permission_read_failed'}
                        if isinstance(error, BridgeError) and error.code == 'OWNER_UNAVAILABLE' and error.reason:
                            alignment['transport_reason'] = error.reason
                rows.append({'thread_id': thread['id'], 'title': thread.get('name') or thread.get('preview'),
                             **({'current_window_tab_open': True} if tab_confirmed else {}),
                             'cwd': thread['cwd'], 'owner_reachable': reachable,
                             'permission_alignment': alignment})
            plugin_ver = detect_codex_plugin_version(self.config.get('extension'))
            return {'version': '0.2.2', 'plugin_version': plugin_ver,
                    'service_build': dict(SERVICE_BUILD), 'worker_instance': self.owner,
                    'worker_health': self.worker_health(),
                    'plugin_concurrency': {'scope': 'thread', 'limit': None, 'automatic_queue': False},
                    'recovery_protocol': 'thread-recovery-v1',
                    'plugin': 'read_verified' if any(r['owner_reachable'] for r in rows) else ('discovered' if plugin else 'unavailable'),
                    'threads': rows, 'filtered_out_count': filtered_out_count,
                    'next_cursor': result.get('nextCursor'),
                    'operations': ['capabilities', *(['session_create'] if self.config.get('panel_transport') else []), 'session_rename', 'start', 'status', 'result', 'continue', 'cancel', 'respond'],
                    'permission_approval': 'human_ui_required',
                    'permission_inheritance': 'host-selection-native-turn-context-v1',
                    }
        finally:
            if plugin:
                await plugin.close()
            await app.close()

    def worker_health(self):
        now = time.monotonic()
        ages = {name: (now - tick if tick else None) for name, tick in self.loop_progress.items()}
        tasks = {'work': self.worker, 'recovery': self.heartbeat_worker}
        healthy = not self.stopping and all(
            tasks[name] is not None and not tasks[name].done()
            and age is not None and age < self.health_timeout for name, age in ages.items())
        return {'healthy': healthy, 'progress_age_seconds': ages,
                'timeout_seconds': self.health_timeout, 'restarts': dict(self.loop_restarts)}

    def _loop_error(self, name, error):
        # No exception message or locals: provider errors may contain private input.
        frames = [{'file': os.path.basename(f.filename), 'line': f.lineno, 'function': f.name}
                  for f in traceback.extract_tb(error.__traceback__)]
        print(json.dumps({'event': 'worker_loop_failed', 'worker_instance': self.owner,
                          'loop': name, 'exception': type(error).__name__, 'frames': frames}),
              file=sys.stderr, flush=True)

    def _spawn_loop(self, name):
        if self.stopping:
            return
        task = asyncio.create_task(self._supervise_loop(name), name='codex-' + name)
        setattr(self, 'worker' if name == 'work' else 'heartbeat_worker', task)
        def done(future):
            if self.stopping:
                return
            self.loop_progress[name] = 0.0
            error = (RuntimeError('Supervisor cancelled') if future.cancelled()
                     else future.exception() or RuntimeError('Supervisor exited'))
            self._loop_error(name, error)
            self.loop_restarts[name] += 1
            self.restart_handles.append(asyncio.get_running_loop().call_later(1, self._spawn_loop, name))
        task.add_done_callback(done)

    async def _supervise_loop(self, name):
        while not self.stopping:
            try:
                await (self._work() if name == 'work' else self._heartbeat())
                raise RuntimeError('Worker loop returned unexpectedly')
            except asyncio.CancelledError:
                raise
            except Exception as error:
                self.loop_progress[name] = 0.0
                self.loop_restarts[name] += 1
                self.loop_failures[name] += 1
                self._loop_error(name, error)
                await asyncio.sleep((1, 2, 5, 10, 30)[min(self.loop_failures[name] - 1, 4)])

    def _progress(self, name):
        self.loop_progress[name] = time.monotonic()
        self.loop_failures[name] = 0

    async def _work(self):
        while True:
            if self.pending_command is not None:
                pending = self.pending_command
                current = await self._db(self.store.task, pending['task_id'])
                if current['state'] not in TERMINAL and current['state'] != 'interrupted_unknown':
                    await self._db(self.store.transition, pending['task_id'], self.owner, 'interrupted_unknown')
                self.pending_command = None
            claim = asyncio.create_task(self._db(self.store.claim, self.owner))
            try:
                command = await asyncio.shield(claim)
            except asyncio.CancelledError:
                # A committed claim must not disappear when its waiter is cancelled.
                self.pending_command = await claim
                raise
            if command:
                self.pending_command = command
                task_id = command['task_id']
                try:
                    if command['operation'] in ('start', 'continue'):
                        previous = self.runs.get(task_id)
                        if previous:
                            await previous
                        self._track(task_id, self._execute(command))
                    else:
                        await self._control(command)
                except BridgeError as error:
                    self.store.command_done(command['id'], self.owner, error.as_dict())
                    self.store.append_event(task_id, 'control_error', error.as_dict())
            self.pending_command = None
            self._progress('work')
            await asyncio.sleep(0.1)

    async def _db(self, operation, *args):
        future = asyncio.create_task(asyncio.to_thread(operation, *args))
        self.db_calls.add(future)
        future.add_done_callback(self.db_calls.discard)
        return await asyncio.shield(future)

    def _pulse(self):
        # Thread liveness is insufficient: both supervised loops must make progress.
        while not self.heartbeat_stop.wait(self.heartbeat_interval):
            try:
                if self.worker_health()['healthy']:
                    self.store.heartbeat(self.owner, lambda: self.worker_health()['healthy'])
            except Exception:
                # Recovery fencing still prevents a stale worker from committing.
                continue

    def _track(self, task_id, coroutine):
        run = asyncio.create_task(coroutine)
        self.runs[task_id] = run
        def done(future):
            if self.runs.get(task_id) is future:
                self.runs.pop(task_id, None)
            if not future.cancelled():
                future.exception()  # Retrieve errors; execution records their task outcome.
        run.add_done_callback(done)

    async def _heartbeat(self):
        while True:
            await self._db(self.store.mark_lost_owners, time.time() - 30)
            for task in await self._db(self.store.recoverable):
                if task['id'] in self.runs:
                    continue
                if await self._db(self.store.claim_recovery, task['id'], self.owner):
                    self._track(task['id'], self._recover(self.store.task(task['id'])))
            self._progress('recovery')
            await asyncio.sleep(1)

    @staticmethod
    def _state_event(event, thread_id):
        return (event.get('method') == 'plugin/state'
                and event.get('params', {}).get('conversationId') == thread_id)

    async def _snapshot(self, connection, thread_id):
        snapshot = Snapshot()
        deadline = asyncio.get_running_loop().time() + 15
        while snapshot.state is None:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise BridgeError('BACKEND_TIMEOUT', 'Matching plugin snapshot did not arrive')
            event = await asyncio.wait_for(connection.events.get(), remaining)
            if event.get('method') == 'bridge/disconnected':
                raise BridgeError('BACKEND_DISCONNECTED', 'Plugin disconnected before snapshot')
            if self._state_event(event, thread_id):
                snapshot.apply(event['params']['change'])
        return snapshot

    async def _confirm_unsubmitted(self, connection, owner, task, candidate):
        thread_id = task['thread_id']
        snapshot = Snapshot()
        snapshot.apply(await connection.resnapshot(thread_id, owner))
        snapshot.assert_idle(task['cwd'])
        turns = snapshot.turns()
        plugin_ids = [turn.get('turnId') for turn in turns]
        if (plugin_ids.count(candidate[2]) != 1
                or any(not turn.get('turnId') or (turn.get('params') or {}).get('clientUserMessageId')
                       == candidate[1] for turn in turns)):
            raise BridgeError('RECOVERY_UNCONFIRMED', 'Plugin history does not confirm previous turn')
        app = await AppServer(resolve_codex_binary(self.config.get('codex'), self.config.get('extension'))).start()
        try:
            thread = (await app.call('thread/read', {'threadId': thread_id, 'includeTurns': True}))['thread']
            history = thread.get('turns')
            if (thread.get('id') != thread_id or not thread.get('cwd') or canonical(thread['cwd']) != task['cwd']
                    or not history
                    or history[-1].get('status') not in ('completed', 'failed', 'interrupted')
                    or any(not turn.get('id') or not isinstance(turn.get('items'), list) for turn in history)
                    or any(item.get('type') == 'userMessage' and candidate[1] in
                           (item.get('id'), item.get('clientUserMessageId'))
                           for turn in history for item in turn['items'])
                    or await connection.owner(thread_id) != owner):
                raise BridgeError('RECOVERY_UNCONFIRMED', 'Native history does not confirm previous turn')
            native_ids = [turn['id'] for turn in history]
            prior = candidate[2]
            if native_ids.count(prior) != 1 or native_ids[native_ids.index(prior):] != plugin_ids[plugin_ids.index(prior):]:
                raise BridgeError('RECOVERY_UNCONFIRMED', 'Native and plugin continuation histories differ')
            for later in history[native_ids.index(prior) + 1:]:
                messages = [item.get('content') for item in later['items'] if item.get('type') == 'userMessage']
                if later.get('status') not in ('completed', 'failed', 'interrupted') or len(messages) != 1:
                    raise BridgeError('RECOVERY_UNCONFIRMED', 'Later turn has no verifiable terminal input')
                parts = messages[0]
                if (not isinstance(parts, list) or len(parts) != 1 or parts[0].get('type') != 'text'
                        or not isinstance(parts[0].get('text'), str) or not parts[0]['text'].strip()
                        or DELEGATION_PREFIX.strip() in parts[0]['text']):
                    raise BridgeError('RECOVERY_UNCONFIRMED', 'Later input could be a delegated submission')
        finally:
            await app.close()

    async def _recover(self, task):
        connection = None
        try:
            captured = self.store.submission(task['id']) or {}
            if task['mode'] == 'plugin' and captured.get('transport') == 'panel_queue' and self.config.get('panel_transport'):
                connection, _ = await connect_panel(self.config['panel_transport'], task)
                self.connections[task['id']] = connection
                marker = captured.get('client_message_id')
                original = task['turn_id'] or await panel_turn_id(connection, task, marker)
                selection = PermissionSelection(captured['permission_mode'], '')
                self.store.transition(task['id'], self.owner, 'running', turn_id=original)
                await self._watch_panel_queue(connection, task, marker, selection, original)
                return
            if task['mode'] == 'plugin':
                connection = await Plugin(self.config['node'], self.config['extension']).start()
                self.connections[task['id']] = connection
                owner = await connection.follow(task['thread_id'])
                candidate = self.store.queued_continue(task)
                if candidate:
                    current = self.store.task(task['id'])
                    if current['state'] != 'recovering' or current['owner'] != self.owner:
                        raise BridgeError('STALE_TURN', 'Recovery ownership changed')
                    await self._confirm_unsubmitted(connection, owner, current, candidate)
                    self.store.finish_unsubmitted(current, candidate, 'failed')
                    return
                snapshot = await self._snapshot(connection, task['thread_id'])
                submission = self.store.submission(task['id']) or {}
                marker = submission.get('client_message_id')
                selection = (PermissionSelection(submission['permission_mode'], '')
                             if submission.get('permission_mode') else None)
                if selection is None:
                    raise BridgeError('RECOVERY_UNCONFIRMED', 'Captured permission selection is absent')
                turn = next((t for t in snapshot.turns() if
                    (task['turn_id'] and t.get('turnId') == task['turn_id']) or
                    (not task['turn_id'] and marker and (t.get('params') or {}).get('clientUserMessageId') == marker)), None)
                if turn is None:
                    await connection.follower('thread-follower-load-complete-history', task['thread_id'], expected_owner=owner)
                    snapshot = Snapshot()
                    snapshot.apply(await connection.resnapshot(task['thread_id'], owner))
                    turn = next((t for t in snapshot.turns() if
                        (task['turn_id'] and t.get('turnId') == task['turn_id']) or
                        (not task['turn_id'] and marker and (t.get('params') or {}).get('clientUserMessageId') == marker)), None)
                if not turn or not turn.get('turnId'):
                    raise BridgeError('BACKEND_DISCONNECTED', 'Cannot correlate uncertain submission; no replay performed')
                self.store.transition(task['id'], self.owner, 'running', turn_id=turn['turnId'])
                # Reuse the exact captured snapshot, then follow subsequent revisions.
                connection.events.put_nowait({'method': 'plugin/state', 'params': {'conversationId': task['thread_id'], 'change': {
                    'type': 'snapshot', 'revision': snapshot.revision, 'conversationState': snapshot.state}}})
                await self._watch_plugin(task['id'], connection, Snapshot(), marker, owner, selection)
            else:
                codex_bin = resolve_codex_binary(self.config.get('codex'), self.config.get('extension'))
                connection = await AppServer(codex_bin).start()
                self.connections[task['id']] = connection
                if not task['thread_id'] or not task['turn_id']:
                    raise BridgeError('BACKEND_DISCONNECTED', 'No reliable backend turn ID; no replay performed')
                reply = await connection.call('thread/turns/list', {'threadId': task['thread_id'], 'limit': 100, 'itemsView': 'full'})
                turn = next((t for t in reply['data'] if t['id'] == task['turn_id']), None)
                if not turn or turn.get('status') not in ('completed', 'failed', 'interrupted'):
                    raise BridgeError('BACKEND_DISCONNECTED', 'Managed turn terminal state is unconfirmed')
                state = {'completed': 'completed', 'failed': 'failed', 'interrupted': 'cancelled'}[turn['status']]
                self.store.transition(task['id'], self.owner, state, result=turn_result(turn))
        except asyncio.CancelledError:
            with suppress(BridgeError):
                self.store.transition(task['id'], self.owner, 'interrupted_unknown', error={'code': 'WORKER_STOPPED'})
            raise
        except Exception as error:
            recovered = False
            if task['mode'] == 'plugin':
                if isinstance(error, BridgeError) and error.code == 'OWNER_UNAVAILABLE':
                    self.store.append_event(task['id'], 'plugin_owner_unavailable', {'thread_id': task['thread_id']})
                app = None
                try:
                    current = self.store.task(task['id'])
                    if current['owner'] != self.owner or current['state'] in TERMINAL:
                        return
                    captured = self.store.submission(task['id']) or {}
                    if not current['thread_id'] or not captured.get('permission_mode'):
                        raise BridgeError('RECOVERY_UNCONFIRMED', 'Exact turn or captured permissions are unavailable')
                    app = await AppServer(resolve_codex_binary(self.config.get('codex'), self.config.get('extension'))).start()
                    if not current['turn_id'] and captured.get('transport') == 'panel_queue':
                        original = await panel_turn_id(app, current, captured.get('client_message_id'))
                        current = {**current, 'turn_id': original}
                    probe = Plugin(self.config['node'], self.config['extension'])
                    state, result, actual = await read_native_result(app, probe, current, captured)
                    self.store.append_event(task['id'], 'result_recovered', {'source': 'native_history',
                        'thread_id': current['thread_id'], 'turn_id': current['turn_id'], 'actual_permissions': actual})
                    self.store.transition(task['id'], self.owner, state, turn_id=current['turn_id'], result=result)
                    recovered = True
                except Exception as fallback_error:
                    self.store.append_event(task['id'], 'recovery_unconfirmed', {
                        'code': getattr(fallback_error, 'code', 'RECOVERY_UNCONFIRMED')})
                finally:
                    if app:
                        await app.close()
            if not recovered:
                with suppress(BridgeError):
                    self.store.transition(task['id'], self.owner, 'interrupted_unknown', error={
                        'code': error.code if isinstance(error, BridgeError) else 'RECOVERY_UNCONFIRMED'})
        finally:
            if connection:
                await connection.close()
            self.connections.pop(task['id'], None)

    async def _execute(self, command):
        task_id, payload = command['task_id'], command['payload']
        task = self.store.task(task_id)
        submitted = False
        connection = None
        try:
            self.store.transition(task_id, self.owner, 'starting')
            if task['mode'] != 'plugin':
                raise BridgeError('MODE_RETIRED', 'Standalone dispatch has been removed')
            prompt = DELEGATION_PREFIX + payload['prompt']
            if payload.get('panel_queue'):
                from .permissions import read_selection, assert_selection_current
                connection, host = await connect_panel(self.config['panel_transport'], task)
                self.connections[task_id] = connection
                window = await open_panel_tab(host['socket'], task['cwd'], task['thread_id'], inspect_only=True)
                database = window.get('permission_database')
                if not database:
                    raise BridgeError('PERMISSIONS_UNAVAILABLE', 'Missing bound panel permission database')
                selection = await asyncio.to_thread(read_selection, database)
                thread_id = task['thread_id']
                resume = await connection.call('thread/resume', {'threadId': thread_id, 'excludeTurns': True})
                if resume['thread']['id'] != thread_id or canonical(resume['thread']['cwd']) != task['cwd']:
                    raise BridgeError('OWNER_UNAVAILABLE', 'Panel thread changed')
                await asyncio.to_thread(assert_selection_current, database, selection)
                unattended = selection.mode == 'full-access'
                if unattended:
                    profile = resume.get('activePermissionProfile') or {}
                    mode = {':workspace': 'unattended-workspace', ':read-only': 'unattended-read-only',
                            ':danger-full-access': 'full-access'}.get(profile.get('id'))
                    if not mode or profile.get('extends') or resume.get('approvalsReviewer') != 'user':
                        raise BridgeError('PERMISSIONS_UNAVAILABLE', 'Unrecognized native thread policy')
                    selection = PermissionSelection(mode, selection.fingerprint)
                    assert_effective_selection(selection, {**resume, 'approvalPolicy': 'never'})
                else:
                    assert_effective_selection(selection, resume)
                if time.time() >= task['deadline']:
                    self.store.transition(task_id, self.owner, 'timed_out')
                    self.store.command_done(command['id'], self.owner, {'submitted': False})
                    return
                marker = str(uuid.uuid4())
                self.store.append_event(task_id, 'submission', {'client_message_id': marker,
                    'permission_mode': selection.mode, 'transport': 'panel_queue'})
                submitted = True
                if unattended:
                    reply = await connection.call('turn/start', {'threadId': thread_id,
                        'clientUserMessageId': marker, 'approvalPolicy': 'never',
                        'input': [{'type': 'text', 'text': prompt, 'text_elements': []}]})
                    turn_id = reply['turn']['id']
                    self.store.transition(task_id, self.owner, 'running', turn_id=turn_id)
                    self.store.command_done(command['id'], self.owner, {'turn_id': turn_id})
                    probe = Plugin(self.config['node'], self.config['extension'])
                    actual = await probe.effective_turn_permissions(thread_id, turn_id, task['cwd'])
                    try:
                        assert_effective_selection(selection, actual)
                    except BridgeError:
                        await connection.call('turn/interrupt', {'threadId': thread_id, 'turnId': turn_id})
                        raise
                    await self._watch_panel_queue(connection, task, marker, selection, turn_id)
                    return
                reply = await connection.call('thread/queue/add', {'threadId': thread_id,
                    'clientUserMessageId': marker, 'input': [{'type': 'text', 'text': prompt, 'text_elements': []}]})
                receipt = reply['queuedSubmission']
                if receipt.get('clientUserMessageId') != marker or not receipt.get('id'):
                    raise BridgeError('RECOVERY_UNCONFIRMED', 'Panel queue receipt differs from submission')
                self.store.command_done(command['id'], self.owner, {'queued_submission_id': receipt['id']})
                await self._watch_panel_queue(connection, task, marker, selection)
            elif task['mode'] == 'plugin':
                connection = await Plugin(self.config['node'], self.config['extension']).start()
                self.connections[task_id] = connection
                owner = await connection.follow(task['thread_id'])
                snapshot = await self._snapshot(connection, task['thread_id'])
                snapshot.assert_idle(task['cwd'])
                assert_supported_thread(snapshot.state)
                database, selection = await connection.permission_selection(task['thread_id'], owner)
                # Model-settings read waits for the owner's pending settings
                # writes. Apply their queued broadcasts before validating again.
                while not connection.events.empty():
                    event = connection.events.get_nowait()
                    if event.get('method') == 'bridge/disconnected':
                        raise BridgeError('BACKEND_DISCONNECTED', 'Plugin disconnected before permission submission')
                    if self._state_event(event, task['thread_id']):
                        snapshot.apply(event['params']['change'])
                snapshot.assert_idle(task['cwd'])
                assert_supported_thread(snapshot.state)
                assert_no_pending_permission_conflict(snapshot.state, snapshot.turns(), selection)
                self.store.append_event(task_id, 'permission_selection', {
                    'source': 'vscode-local-explicit-selection', 'mode': selection.mode,
                    'expected': selection.request_fields()})
                marker = str(uuid.uuid4())
                await connection.check_permission_selection(task['thread_id'], owner, database, selection)
                self.store.append_event(task_id, 'submission', {
                    'client_message_id': marker, 'permission_mode': selection.mode})
                submitted = True
                await connection.follower('thread-follower-start-turn', task['thread_id'], {
                    'turnStart': {'request': {'threadId': task['thread_id'],
                        'input': [{'type': 'text', 'text': prompt, 'text_elements': []}],
                        'clientUserMessageId': marker, **selection.request_fields()},
                        'context': {'inheritThreadSettings': True}}}, expected_owner=owner)
                self.store.command_done(command['id'], self.owner, {'accepted': True})
                await self._watch_plugin(task_id, connection, snapshot, marker, owner, selection)
        except asyncio.CancelledError:
            current = self.store.task(task_id)
            if current['state'] not in TERMINAL:
                self.store.transition(task_id, self.owner, 'interrupted_unknown', error={'code': 'WORKER_STOPPED'})
            raise
        except Exception as error:
            current = self.store.task(task_id)
            if current['state'] not in TERMINAL and current['state'] != 'interrupted_unknown':
                detail = error.as_dict() if isinstance(error, BridgeError) else {'code': 'INTERNAL_ERROR', 'message': type(error).__name__}
                self.store.transition(task_id, self.owner, 'interrupted_unknown' if submitted else 'failed', error=detail)
                self.store.command_done(command['id'], self.owner, detail)
        finally:
            if connection:
                await connection.close()
            self.connections.pop(task_id, None)

    async def _watch_panel_queue(self, connection, task, marker, selection, turn_id=None):
        native = Plugin(self.config['node'], self.config['extension'])
        while time.time() < task['deadline']:
            if turn_id is None:
                try:
                    turn_id = await panel_turn_id(connection, task, marker)
                except BridgeError as error:
                    if error.reason != 'message_not_visible':
                        raise
                if turn_id:
                    self.store.transition(task['id'], self.owner, 'running', turn_id=turn_id)
                    actual = await native.effective_turn_permissions(task['thread_id'], turn_id, task['cwd'])
                    try:
                        assert_effective_selection(selection, actual)
                    except BridgeError:
                        await connection.call('turn/interrupt', {'threadId': task['thread_id'], 'turnId': turn_id})
                        raise
            if turn_id:
                try:
                    state, result, _ = await read_native_result(connection, native,
                        {**task, 'turn_id': turn_id}, {'permission_mode': selection.mode})
                except BridgeError as error:
                    if error.reason != 'turn_in_progress':
                        raise
                    thread = (await connection.call('thread/read', {'threadId': task['thread_id']}))['thread']
                    flags = thread.get('status', {}).get('activeFlags', [])
                    state = ('waiting_approval' if 'waitingOnApproval' in flags else
                             'waiting_input' if 'waitingOnUserInput' in flags else 'running')
                    if self.store.task(task['id'])['state'] not in (state, 'cancelling'):
                        self.store.transition(task['id'], self.owner, state)
                else:
                    self.store.transition(task['id'], self.owner, state, turn_id=turn_id, result=result)
                    return
            await asyncio.sleep(.5)
        raise BridgeError('BACKEND_TIMEOUT', 'Native queue outcome is unconfirmed')

    async def _control(self, command):
        task = self.store.task(command['task_id'])
        if task['turn_id'] != command['payload']['expected_turn_id'] or task['state'] in TERMINAL:
            raise BridgeError('STALE_TURN', 'Control no longer matches an active turn')
        connection = self.connections.get(task['id'])
        if connection is None:
            raise BridgeError('BACKEND_DISCONNECTED', 'Task connection is unavailable')
        if isinstance(connection, PanelConnection):
            if command['operation'] != 'cancel':
                raise BridgeError('APPROVAL_REQUIRES_UI', 'Respond in the bound Codex panel')
            if not task['turn_id'] or task['thread_id'] not in await connection.loaded():
                raise BridgeError('STALE_TURN', 'The bound panel no longer holds this turn')
            await connection.call('turn/interrupt', {'threadId': task['thread_id'], 'turnId': task['turn_id']})
            self.store.command_done(command['id'], self.owner, {'accepted': True})
            return
        plugin_owner = None
        if task['mode'] == 'plugin':
            plugin_owner = await connection.owner(task['thread_id'])
            if plugin_owner != getattr(connection, 'bound_owner', None):
                change = await connection.resnapshot(task['thread_id'], plugin_owner)
                checked = Snapshot()
                checked.apply(change)
                turn = next((t for t in checked.turns() if t.get('turnId') == task['turn_id']), None)
                if not turn or turn.get('status') != 'inProgress':
                    raise BridgeError('STALE_TURN', 'Reconnected owner has no matching active turn')
                if command['operation'] == 'respond':
                    wire = self.pending_wire.get(command['payload']['pending_request_id'])
                    if not any(r.get('id') == wire and r.get('params', {}).get('turnId') == task['turn_id']
                               for r in checked.state.get('requests', [])):
                        raise BridgeError('STALE_TURN', 'Pending input no longer belongs to the reconnected turn')
                connection.bound_owner = plugin_owner
                connection.events.put_nowait({'method': 'plugin/state', 'params': {
                    'conversationId': task['thread_id'], 'change': change}})
            current = self.store.task(task['id'])
            if current['owner'] != self.owner or current['turn_id'] != task['turn_id'] or current['state'] in TERMINAL:
                raise BridgeError('STALE_TURN', 'Task changed while reconnecting control')
        if command['operation'] == 'cancel':
            if task['mode'] == 'plugin':
                await connection.interrupt(task['thread_id'], task['turn_id'], plugin_owner)
            else:
                await connection.call('turn/interrupt', {'threadId': task['thread_id'], 'turnId': task['turn_id']})
        elif command['operation'] == 'respond':
            pending_id = command['payload']['pending_request_id']
            wire_id = self.pending_wire.get(pending_id)
            if wire_id is None:
                raise BridgeError('APPROVAL_REQUIRES_UI', 'Response requires the original host UI')
            if task['mode'] == 'plugin':
                await connection.follower('thread-follower-submit-user-input', task['thread_id'], {
                    'requestId': wire_id, 'response': command['payload']['response']}, expected_owner=plugin_owner)
            else:
                await connection.send({'id': wire_id, 'result': command['payload']['response']})
            self.store.pending_done(pending_id)
            self._refresh_waiting(task['id'])
        self.store.command_done(command['id'], self.owner, {'accepted': True})

    async def _next(self, task_id, connection):
        if not connection.events.empty():
            return connection.events.get_nowait()
        task = self.store.task(task_id)
        timeout = task['deadline'] - time.time()
        if timeout <= 0:
            if task['turn_id'] and task['state'] != 'cancelling':
                self.store.enqueue('cancel', {'task_id': task_id, 'expected_turn_id': task['turn_id']})
            # Give cancellation one bounded observation window.
            timeout = 10
        try:
            return await asyncio.wait_for(connection.events.get(), min(timeout, 60))
        except TimeoutError:
            if time.time() > task['deadline'] + 10:
                raise BridgeError('BACKEND_TIMEOUT', 'Could not confirm task stopped after deadline')
            return {}

    async def _watch_managed(self, task_id, connection):
        items = {}
        while True:
            event = await self._next(task_id, connection)
            method, params = event.get('method'), event.get('params', {})
            task = self.store.task(task_id)
            if method == 'bridge/disconnected':
                raise BridgeError('BACKEND_DISCONNECTED', 'App Server disconnected')
            if params.get('threadId') != task['thread_id'] or (params.get('turnId') and params['turnId'] != task['turn_id']):
                continue
            if 'id' in event:
                kind = 'approval' if 'Approval' in method or 'approval' in method else 'input' if method == 'item/tool/requestUserInput' else 'other'
                pending_id = self.store.pending_add(task_id, task['turn_id'], kind, {'method': method, 'params': params})
                self.pending_wire[pending_id] = event['id']
                if kind == 'approval':
                    try:
                        if self.approval_ui is None:
                            self.approval_ui = ApprovalUI(self)
                        url = self.approval_ui.register(task_id, pending_id)
                        self.store.pending_url(pending_id, url)
                    except OSError:
                        self.store.append_event(task_id, 'approval_ui_unavailable', {'pending_request_id': pending_id})
                self._refresh_waiting(task_id)
            elif method == 'serverRequest/resolved':
                for pending in self.store.pending_list(task_id):
                    if self.pending_wire.get(pending['id']) == params.get('requestId'):
                        self.store.pending_done(pending['id'])
                        self.pending_wire.pop(pending['id'], None)
                self._refresh_waiting(task_id)
            elif method == 'item/completed':
                item = params['item']
                items[item['id']] = item
                self.store.append_event(task_id, 'item_completed', item)
            elif method == 'turn/completed' and params['turn']['id'] == task['turn_id']:
                turn = params['turn']
                turn['items'] = list(items.values())
                state = {'completed': 'completed', 'interrupted': 'cancelled', 'failed': 'failed'}.get(turn['status'])
                if not state:
                    raise BridgeError('BACKEND_ERROR', 'Unknown terminal status')
                if state == 'cancelled' and time.time() >= task['deadline']:
                    state = 'timed_out'
                self.store.transition(task_id, self.owner, state, result=turn_result(turn))
                return

    def _refresh_waiting(self, task_id):
        task = self.store.task(task_id)
        if task['state'] in TERMINAL or task['state'] in ('interrupted_unknown', 'cancelling'):
            return
        pending = self.store.pending_list(task_id)
        state = 'waiting_approval' if any(p['kind'] == 'approval' for p in pending) else 'waiting_input' if pending else 'running'
        self.store.transition(task_id, self.owner, state)

    async def human_approval(self, task_id, pending_id, choice=None):
        """Only the local human UI invokes this; MCP respond never enters this path."""
        async with self.approval_lock:
            task = self.store.task(task_id)
            pending = next((p for p in self.store.pending_list(task_id) if p['id'] == pending_id), None)
            connection = self.connections.get(task_id)
            if (self.stopping or task['owner'] != self.owner or task['mode'] != 'managed'
                    or task['state'] != 'waiting_approval' or task['deadline'] <= time.time()
                    or not pending or pending['state'] != 'waiting' or pending['kind'] != 'approval'
                    or pending['turn_id'] != task['turn_id'] or connection is None
                    or pending_id not in self.pending_wire):
                raise BridgeError('STALE_TURN', 'Approval is no longer active')
            if choice is None:
                return pending
            options = decisions(pending['payload'])
            if choice not in options:
                raise BridgeError('INVALID_ARGUMENT', 'Unsupported approval decision')
            # Consume before awaiting I/O: an uncertain send must never be replayed.
            self.store.pending_done(pending_id)
            wire_id = self.pending_wire.pop(pending_id)
            try:
                await connection.send({'id': wire_id, 'result': options[choice]})
            except Exception:
                self.store.transition(task_id, self.owner, 'interrupted_unknown', error={'code': 'APPROVAL_SEND_UNCONFIRMED'})
                raise
            self.store.append_event(task_id, 'human_approval_sent', {'pending_request_id': pending_id, 'decision': choice})
            self._refresh_waiting(task_id)
            return pending

    async def _watch_plugin(self, task_id, connection, snapshot, marker, owner, selection=None):
        last_status = None
        bound_turn = self.store.task(task_id)['turn_id']
        permission_checked = False
        permission_error = None
        snapshot_recovered = False
        while True:
            event = await self._next(task_id, connection)
            if event.get('method') == 'bridge/disconnected':
                raise BridgeError('BACKEND_DISCONNECTED', 'Plugin disconnected')
            if not self._state_event(event, self.store.task(task_id)['thread_id']):
                if not event and await connection.owner(self.store.task(task_id)['thread_id']) != owner:
                    raise BridgeError('OWNER_UNAVAILABLE', 'Plugin ownership changed')
                continue
            try:
                snapshot.apply(event['params']['change'])
            except BridgeError as error:
                if snapshot_recovered or error.code != 'BACKEND_DISCONNECTED':
                    raise
                snapshot_recovered = True
                self.store.append_event(task_id, 'snapshot_recovery', {'reason': error.code})
                snapshot.apply(await connection.resnapshot(self.store.task(task_id)['thread_id'], owner))
                self.store.append_event(task_id, 'snapshot_recovered', {'revision': snapshot.revision})
            task = self.store.task(task_id)
            turns = snapshot.turns()
            turn = next((t for t in turns if marker and (t.get('params') or {}).get('clientUserMessageId') == marker), None)
            if turn and bound_turn and turn.get('turnId') != bound_turn:
                raise BridgeError('RECOVERY_UNCONFIRMED', 'Submission marker changed turn identity')
            if turn is None and bound_turn:
                turn = next((t for t in turns if t.get('turnId') == bound_turn), None)
            if not turn or not turn.get('turnId'):
                continue
            turn_id = turn['turnId']
            bound_turn = turn_id
            status = turn.get('status')
            if task['turn_id'] != turn_id:
                self.store.transition(task_id, self.owner, 'running', turn_id=turn_id)
            if selection is not None and not permission_checked:
                permission_checked = True
                try:
                    actual = assert_effective_selection(selection, await connection.effective_turn_permissions(
                        task['thread_id'], turn_id, task['cwd']))
                    self.store.append_event(task_id, 'effective_permissions', {
                        'source': 'plugin-native-turn-context', 'actual': actual})
                except BridgeError as error:
                    permission_error = error.as_dict()
                    self.store.append_event(task_id, 'permission_mismatch', permission_error)
                    if status not in ('completed', 'failed', 'interrupted'):
                        # A submitted turn can already have produced side effects.
                        # Keep the lease until the native terminal state is observed.
                        self.store.transition(task_id, self.owner, 'cancelling')
                        await connection.interrupt(task['thread_id'], turn_id, owner)
            if status != last_status:
                self.store.append_event(task_id, 'plugin_turn_status', {'status': status})
                last_status = status
            if status in ('completed', 'failed', 'interrupted'):
                state = {'completed': 'completed', 'failed': 'failed', 'interrupted': 'cancelled'}[status]
                if state == 'cancelled' and time.time() >= task['deadline']:
                    state = 'timed_out'
                if permission_error:
                    result = turn_result(turn)
                    result['unresolved'].append(permission_error)
                    self.store.transition(task_id, self.owner, 'failed', result=result, error=permission_error)
                else:
                    self.store.transition(task_id, self.owner, state, result=turn_result(turn))
                return
            requests = [r for r in snapshot.state.get('requests', [])
                        if r.get('params', {}).get('turnId') == turn_id]
            if requests:
                existing = self.store.pending_list(task_id)
                known = {self.pending_wire.get(p['id']) for p in existing}
                for request in requests:
                    if request['id'] not in known:
                        method = request['method']
                        kind = 'input' if method == 'item/tool/requestUserInput' else 'approval' if 'Approval' in method else 'other'
                        pending_id = self.store.pending_add(task_id, turn_id, kind,
                            {'method': method, 'params': request['params']})
                        self.pending_wire[pending_id] = request['id']
                current_ids = {r['id'] for r in requests}
                for pending in existing:
                    if self.pending_wire.get(pending['id']) not in current_ids:
                        self.store.pending_done(pending['id'])
                waiting = 'waiting_approval' if any('Approval' in r['method'] for r in requests) else 'waiting_input'
                if task['state'] != waiting:
                    self.store.transition(task_id, self.owner, waiting)
            elif task['state'] in ('waiting_approval', 'waiting_input'):
                for pending in self.store.pending_list(task_id):
                    self.store.pending_done(pending['id'])
                self.store.transition(task_id, self.owner, 'running')
