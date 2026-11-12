"""Task execution on owned App Server sessions and explicitly selected plugin threads."""
import asyncio
from contextlib import suppress
import json
import os
import time
import uuid

from .errors import BridgeError
from .plugin import Plugin, detect_codex_plugin_version, resolve_codex_binary
from .rpc import AppServer
from .schema import validate
from .snapshot import Snapshot, turn_result
from .store import Store, TERMINAL, allowed_cwd


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
        self.worker = None
        self.heartbeat_worker = None
        self.recovery_attempted = set()
        self.stopping = False

    async def start(self):
        self.store.heartbeat(self.owner)
        self.worker = asyncio.create_task(self._work())
        self.heartbeat_worker = asyncio.create_task(self._heartbeat())

    async def close(self):
        self.stopping = True
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
            with suppress(asyncio.CancelledError):
                await run
        for connection in list(self.connections.values()):
            await connection.close()

    async def call(self, name, arguments):
        args = validate(name, arguments)
        if name == 'codex_capabilities':
            return await self.capabilities(args)
        if name == 'codex_session_rename':
            return await self.rename_session(args)
        if name == 'codex_task_start':
            if os.environ.get('VERDANTFLARE_AGENT_CODEX_DEPTH'):
                raise BridgeError('WORKSPACE_DENIED', 'Recursive delegation to this service is disabled')
            args['cwd'] = allowed_cwd(args['cwd'], self.config['allowed_roots'])
            replay = self.store.replay('start', args)
            if replay:
                return public_task(replay)
            if args['mode'] == 'plugin':
                connection = await Plugin(self.config['node'], self.config['extension']).start()
                try:
                    await connection.follow(args['thread_id'])
                    snapshot = await self._snapshot(connection)
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
            return {'thread_id': args['thread_id'], 'name': thread['name'], 'naming': 'native'}
        finally:
            await app.close()

    async def capabilities(self, args):
        cwd = allowed_cwd(args['cwd'], self.config['allowed_roots']) if args.get('cwd') else None
        codex_bin = resolve_codex_binary(self.config.get('codex'), self.config.get('extension'))
        app = await AppServer(codex_bin).start()
        plugin = None
        try:
            result = await app.call('thread/list', {'cwd': cwd, 'limit': args['limit'],
                'sourceKinds': ['vscode'], 'modelProviders': [], 'useStateDbOnly': True,
                'cursor': args.get('cursor')})
            try:
                plugin = await Plugin(self.config['node'], self.config['extension']).start()
            except (BridgeError, OSError):
                pass
            rows = []
            filtered_out_count = 0
            for thread in result['data']:
                if not thread.get('cwd'):
                    continue
                try:
                    allowed_cwd(thread['cwd'], self.config['allowed_roots'])
                except BridgeError:
                    filtered_out_count += 1
                    continue
                reachable = False
                if plugin:
                    try:
                        await plugin.settings(thread['id'])
                        reachable = True
                    except BridgeError:
                        pass
                rows.append({'thread_id': thread['id'], 'title': thread.get('name') or thread.get('preview'),
                             'cwd': thread['cwd'], 'owner_reachable': reachable})
            plugin_ver = detect_codex_plugin_version(self.config.get('extension'))
            return {'version': '0.1.0', 'plugin_version': plugin_ver,
                    'plugin': 'read_verified' if any(r['owner_reachable'] for r in rows) else ('discovered' if plugin else 'unavailable'),
                    'managed': 'read_verified', 'threads': rows, 'filtered_out_count': filtered_out_count,
                    'next_cursor': result.get('nextCursor'),
                    'operations': ['capabilities', 'session_rename', 'start', 'status', 'result', 'continue', 'cancel', 'respond'],
                    'permission_approval': 'human_ui_required'}
        finally:
            if plugin:
                await plugin.close()
            await app.close()

    async def _work(self):
        while True:
            command = self.store.claim(self.owner)
            if command:
                task_id = command['task_id']
                try:
                    if command['operation'] in ('start', 'continue'):
                        previous = self.runs.get(task_id)
                        if previous:
                            await previous
                        self.runs[task_id] = asyncio.create_task(self._execute(command))
                    else:
                        await self._control(command)
                except BridgeError as error:
                    self.store.command_done(command['id'], self.owner, error.as_dict())
                    self.store.append_event(task_id, 'control_error', error.as_dict())
            else:
                await asyncio.sleep(0.1)

    async def _heartbeat(self):
        while True:
            self.store.heartbeat(self.owner)
            self.store.mark_lost_owners(time.time() - 30)
            for task in self.store.recoverable():
                if task['id'] in self.recovery_attempted or task['id'] in self.runs:
                    continue
                self.recovery_attempted.add(task['id'])
                if self.store.claim_recovery(task['id'], self.owner):
                    self.runs[task['id']] = asyncio.create_task(self._recover(task))
            await asyncio.sleep(5)

    async def _snapshot(self, connection):
        snapshot = Snapshot()
        while snapshot.state is None:
            event = await asyncio.wait_for(connection.events.get(), 15)
            if event.get('method') == 'bridge/disconnected':
                raise BridgeError('BACKEND_DISCONNECTED', 'Plugin disconnected before snapshot')
            if event.get('method') == 'plugin/state':
                snapshot.apply(event['params']['change'])
        return snapshot

    async def _recover(self, task):
        connection = None
        try:
            if task['mode'] == 'plugin':
                connection = await Plugin(self.config['node'], self.config['extension']).start()
                self.connections[task['id']] = connection
                owner = await connection.follow(task['thread_id'])
                snapshot = await self._snapshot(connection)
                marker = self.store.submission_marker(task['id'])
                turn = next((t for t in snapshot.turns() if marker and (t.get('params') or {}).get('clientUserMessageId') == marker), None)
                if not turn or not turn.get('turnId'):
                    raise BridgeError('BACKEND_DISCONNECTED', 'Cannot correlate uncertain submission; no replay performed')
                self.store.transition(task['id'], self.owner, 'running', turn_id=turn['turnId'])
                # Reuse the exact captured snapshot, then follow subsequent revisions.
                connection.events.put_nowait({'method': 'plugin/state', 'params': {'change': {
                    'type': 'snapshot', 'revision': snapshot.revision, 'conversationState': snapshot.state}}})
                await self._watch_plugin(task['id'], connection, Snapshot(), marker, owner)
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
        except Exception as error:
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
            prompt = '[Delegated by verdantflare-agent-codex; do not call verdantflare-agent-codex recursively.]\n' + payload['prompt']
            if task['mode'] == 'plugin':
                connection = await Plugin(self.config['node'], self.config['extension']).start()
                self.connections[task_id] = connection
                owner = await connection.follow(task['thread_id'])
                snapshot = Snapshot()
                while snapshot.state is None:
                    event = await asyncio.wait_for(connection.events.get(), 15)
                    if event.get('method') == 'bridge/disconnected':
                        raise BridgeError('BACKEND_DISCONNECTED', 'Plugin disconnected before start')
                    if event.get('method') == 'plugin/state':
                        snapshot.apply(event['params']['change'])
                snapshot.assert_idle(task['cwd'])
                marker = str(uuid.uuid4())
                self.store.append_event(task_id, 'submission', {'client_message_id': marker})
                submitted = True
                await connection.follower('thread-follower-start-turn', task['thread_id'], {
                    'turnStart': {'request': {'threadId': task['thread_id'],
                        'input': [{'type': 'text', 'text': prompt, 'text_elements': []}],
                        'clientUserMessageId': marker}, 'context': {'inheritThreadSettings': True}}}, expected_owner=owner)
                self.store.command_done(command['id'], self.owner, {'accepted': True})
                await self._watch_plugin(task_id, connection, snapshot, marker, owner)
            else:
                codex_bin = resolve_codex_binary(self.config.get('codex'), self.config.get('extension'))
                connection = await AppServer(codex_bin).start()
                self.connections[task_id] = connection
                if task['thread_id']:
                    await connection.call('thread/resume', {'threadId': task['thread_id']})
                    thread_id = task['thread_id']
                else:
                    reply = await connection.call('thread/start', {'cwd': task['cwd']})
                    thread_id = reply['thread']['id']
                self.store.transition(task_id, self.owner, 'starting', thread_id=thread_id)
                submitted = True
                reply = await connection.call('turn/start', {'threadId': thread_id,
                    'input': [{'type': 'text', 'text': prompt, 'text_elements': []}]})
                turn_id = reply['turn']['id']
                self.store.transition(task_id, self.owner, 'running', turn_id=turn_id)
                self.store.command_done(command['id'], self.owner, {'accepted': True})
                await self._watch_managed(task_id, connection)
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

    async def _control(self, command):
        task = self.store.task(command['task_id'])
        if task['turn_id'] != command['payload']['expected_turn_id'] or task['state'] in TERMINAL:
            raise BridgeError('STALE_TURN', 'Control no longer matches an active turn')
        connection = self.connections.get(task['id'])
        if connection is None:
            raise BridgeError('BACKEND_DISCONNECTED', 'Task connection is unavailable')
        if command['operation'] == 'cancel':
            if task['mode'] == 'plugin':
                owner = await connection.owner(task['thread_id'])
                await connection.interrupt(task['thread_id'], task['turn_id'], owner)
            else:
                await connection.call('turn/interrupt', {'threadId': task['thread_id'], 'turnId': task['turn_id']})
        elif command['operation'] == 'respond':
            pending_id = command['payload']['pending_request_id']
            wire_id = self.pending_wire.get(pending_id)
            if wire_id is None:
                raise BridgeError('APPROVAL_REQUIRES_UI', 'Response requires the original host UI')
            if task['mode'] == 'plugin':
                await connection.follower('thread-follower-submit-user-input', task['thread_id'], {
                    'requestId': wire_id, 'response': command['payload']['response']})
            else:
                await connection.send({'id': wire_id, 'result': command['payload']['response']})
            self.store.pending_done(pending_id)
            self.store.transition(task['id'], self.owner, 'running')
        self.store.command_done(command['id'], self.owner, {'accepted': True})

    async def _next(self, task_id, connection):
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
                self.store.transition(task_id, self.owner, 'waiting_approval' if kind == 'approval' else 'waiting_input')
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

    async def _watch_plugin(self, task_id, connection, snapshot, marker, owner):
        last_status = None
        bound_turn = None
        while True:
            event = await self._next(task_id, connection)
            if event.get('method') == 'bridge/disconnected':
                raise BridgeError('BACKEND_DISCONNECTED', 'Plugin disconnected')
            if event.get('method') != 'plugin/state':
                continue
            snapshot.apply(event['params']['change'])
            task = self.store.task(task_id)
            turns = snapshot.turns()
            turn = next((t for t in turns if (t.get('params') or {}).get('clientUserMessageId') == marker), None)
            if turn is None and bound_turn:
                turn = next((t for t in turns if t.get('turnId') == bound_turn), None)
            if not turn or not turn.get('turnId'):
                continue
            turn_id = turn['turnId']
            bound_turn = turn_id
            status = turn.get('status')
            if task['turn_id'] != turn_id:
                self.store.transition(task_id, self.owner, 'running', turn_id=turn_id)
            if status != last_status:
                self.store.append_event(task_id, 'plugin_turn_status', {'status': status})
                last_status = status
            if status in ('completed', 'failed', 'interrupted'):
                state = {'completed': 'completed', 'failed': 'failed', 'interrupted': 'cancelled'}[status]
                if state == 'cancelled' and time.time() >= task['deadline']:
                    state = 'timed_out'
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
