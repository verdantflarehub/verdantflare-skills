"""Non-secret compensation intents; original commit records remain immutable."""
from __future__ import annotations

import install_transaction as io

SCHEMA = 'vf.transaction-compensation/v1'


class Compensation:
    def __init__(self, journal, path, read, write):
        self.journal = journal
        self.path = path
        self.read = read
        self.write = write

    def _identity(self):
        # Cleanup may change only the journal state, never the transaction inputs.
        content = {k: v for k, v in self.journal.items() if k != 'state'}
        return {'schema': SCHEMA, 'transaction': self.journal['id'],
                'journal_sha256': io.sha(io.encode(content)),
                'committed_revision': io.sha(io.encode(self.journal['after']))}

    def state(self):
        raw = self.read(self.path)
        if raw is None:
            return None
        value = io.decode(raw)
        identity = self._identity()
        io.require(type(value) is dict and set(value) == set(identity) | {'state'}
                   and all(value.get(k) == v for k, v in identity.items())
                   and value['state'] in ('requested', 'completed'), 'INVALID_COMPENSATION')
        return value['state']

    def save(self, state):
        io.require(state in ('requested', 'completed'), 'INVALID_COMPENSATION')
        self.write(self.path, io.encode({**self._identity(), 'state': state}))
