"""Apply plugin Immer patches with revision checks; gaps invalidate state."""
from copy import deepcopy
from .errors import BridgeError


class Snapshot:
    def __init__(self):
        self.revision = None
        self.state = None

    def apply(self, change):
        if change.get('type') == 'snapshot':
            self.state = deepcopy(change['conversationState'])
            self.revision = change['revision']
            return self.state
        if change.get('type') != 'patches' or self.state is None or change.get('baseRevision') != self.revision:
            self.state = None
            raise BridgeError('BACKEND_DISCONNECTED', 'Plugin state revision gap')
        try:
            state = deepcopy(self.state)
            for patch in change['patches']:
                path, operation = patch['path'], patch['op']
                if not isinstance(path, list) or operation not in ('add', 'replace', 'remove'):
                    raise ValueError('unsupported patch')
                if not path:
                    if operation == 'remove':
                        raise ValueError('cannot remove root')
                    state = deepcopy(patch['value'])
                    continue
                parent = state
                for key in path[:-1]:
                    parent = parent[key]
                key = path[-1]
                if isinstance(parent, list):
                    if type(key) is not int or key < 0:
                        raise ValueError('bad array index')
                    if operation == 'add':
                        if key > len(parent):
                            raise ValueError('bad insertion')
                        parent.insert(key, deepcopy(patch['value']))
                    elif operation == 'remove':
                        parent.pop(key)
                    else:
                        parent[key] = deepcopy(patch['value'])
                else:
                    if operation != 'add' and key not in parent:
                        raise KeyError(key)
                    if operation == 'remove':
                        del parent[key]
                    else:
                        parent[key] = deepcopy(patch['value'])
            self.state = state
            self.revision = change['revision']
            return state
        except (KeyError, IndexError, TypeError, ValueError):
            self.state = None
            raise BridgeError('BACKEND_DISCONNECTED', 'Plugin state patch could not be applied') from None

    def turns(self):
        if self.state is None:
            return []
        history = self.state.get('turnHistory') or {}
        if history.get('kind') == 'canonical':
            content = history['history']
            return [content['entitiesByKey'][entry['value']]
                    for island in content['islands'] for entry in island['entries']
                    if entry['value'] in content['entitiesByKey']]
        return self.state.get('turns', [])

    def assert_idle(self, cwd):
        from .store import canonical
        if self.state is None or self.state.get('threadRuntimeStatus', {}).get('type') not in ('idle',):
            raise BridgeError('SESSION_BUSY', 'Plugin thread is active or its state is uncertain',
                              reason='state_unknown' if self.state is None else 'thread_active')
        if canonical(self.state['cwd']) != cwd:
            raise BridgeError('WORKSPACE_DENIED', 'Plugin thread workspace differs from requested workspace')
        if self.state.get('requests') or any(t.get('status') == 'inProgress' for t in self.turns()):
            raise BridgeError('SESSION_BUSY', 'Plugin thread has active work or pending requests',
                              reason='pending_request' if self.state.get('requests') else 'thread_active')


def turn_result(turn):
    items = turn.get('items', [])
    return {
        'answer': '\n'.join(item.get('text', '') for item in items
                            if item.get('type') == 'agentMessage' and item.get('phase') in (None, 'final_answer')),
        'file_changes': [item for item in items if item.get('type') == 'fileChange'],
        'commands': [{key: item.get(key) for key in ('command', 'cwd', 'status', 'exitCode', 'aggregatedOutput')}
                     for item in items if item.get('type') == 'commandExecution'],
        'unresolved': [turn['error']] if turn.get('error') else [],
    }
