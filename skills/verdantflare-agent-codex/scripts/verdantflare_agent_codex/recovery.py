"""Read original native turn results without starting or resuming a thread."""
import time

from .errors import BridgeError
from .permissions import PermissionSelection, assert_effective_selection
from .snapshot import turn_result
from .store import canonical


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
