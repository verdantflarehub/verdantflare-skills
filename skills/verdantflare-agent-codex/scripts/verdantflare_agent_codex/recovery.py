"""Read original native turn results without starting or resuming a thread."""
import time

from .errors import BridgeError
from .permissions import PermissionSelection, assert_effective_selection
from .snapshot import turn_result
from .store import canonical


async def panel_turn_id(app, task, marker):
    """Resolve a persisted queue message to one native turn, including after reconnect."""
    if not marker or not task.get('thread_id'):
        raise BridgeError('RECOVERY_UNCONFIRMED', 'Missing native submission identity')
    thread = (await app.call('thread/read', {'threadId': task['thread_id'], 'includeTurns': False}))['thread']
    if thread.get('id') != task['thread_id'] or not thread.get('cwd') or canonical(thread['cwd']) != task['cwd']:
        raise BridgeError('RECOVERY_UNCONFIRMED', 'Native thread workspace differs')
    found = set()
    cursor, seen = None, set()
    while True:
        page = await app.call('thread/turns/list', {'threadId': task['thread_id'],
            'limit': 100, 'itemsView': 'full', **({'cursor': cursor} if cursor else {})})
        for turn in page['data']:
            if any(item.get('type') == 'userMessage' and item.get('clientId') == marker
                   for item in turn.get('items', [])):
                found.add(turn['id'])
        cursor = page.get('nextCursor')
        if not cursor:
            break
        if cursor in seen:
            raise BridgeError('RECOVERY_UNCONFIRMED', 'Native turn cursor repeated')
        seen.add(cursor)
    if not found:
        raise BridgeError('RECOVERY_UNCONFIRMED', 'Native message is not visible yet', reason='message_not_visible')
    if len(found) != 1:
        raise BridgeError('RECOVERY_UNCONFIRMED', 'No unique native turn for the queue message')
    return found.pop()


async def read_native_result(app, plugin, task, submission):
    if not task['thread_id'] or not task['turn_id'] or not submission.get('permission_mode'):
        raise BridgeError('RECOVERY_UNCONFIRMED', 'Exact turn and captured permissions are required')
    thread = (await app.call('thread/read', {'threadId': task['thread_id'], 'includeTurns': False}))['thread']
    if thread.get('id') != task['thread_id'] or not thread.get('cwd') or canonical(thread['cwd']) != task['cwd']:
        raise BridgeError('RECOVERY_UNCONFIRMED', 'Native history identity differs')
    cursor, seen = None, set()
    while True:
        reply = await app.call('thread/turns/list', {'threadId': task['thread_id'],
            'limit': 100, 'itemsView': 'full', **({'cursor': cursor} if cursor else {})})
        turn = next((t for t in reply['data'] if t.get('id') == task['turn_id']), None)
        if turn:
            state = {'completed': 'completed', 'failed': 'failed', 'interrupted': 'cancelled'}.get(turn.get('status'))
            if turn.get('status') == 'inProgress':
                raise BridgeError('RECOVERY_UNCONFIRMED', 'Native turn is running', reason='turn_in_progress')
            if not state or not isinstance(turn.get('items'), list):
                raise BridgeError('RECOVERY_UNCONFIRMED', 'Original native turn terminal result is incomplete')
            selection = PermissionSelection(submission['permission_mode'], '')
            actual = assert_effective_selection(selection, await plugin.effective_turn_permissions(
                task['thread_id'], task['turn_id'], task['cwd']))
            if state == 'cancelled' and time.time() >= task['deadline']:
                state = 'timed_out'
            return state, turn_result(turn), actual
        cursor = reply.get('nextCursor')
        if not cursor or cursor in seen:
            raise BridgeError('RECOVERY_UNCONFIRMED', 'Original native turn is absent from history')
        seen.add(cursor)
