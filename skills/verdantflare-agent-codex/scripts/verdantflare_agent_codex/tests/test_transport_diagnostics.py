import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from verdantflare_agent_codex.rpc import AppServer, backend_rejection


class TransportDiagnosticsTests(unittest.TestCase):
    def test_owner_probe_exposes_only_known_reasons(self):
        error = dict(code=-32000, message='Plugin transport request failed', reason='pipe_missing')
        self.assertEqual('pipe_missing', backend_rejection('thread-owner-discovery', error).reason)
        error['reason'] = 'private data'
        self.assertNotIn('private data', str(backend_rejection('thread-owner-discovery', error).as_dict()))

    @unittest.skipUnless(os.name == 'nt', 'Windows managed tools')
    def test_missing_tools_added_without_replacing_parent_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for tool in ('Git/cmd/git.exe', 'extension/codex-path/rg.exe'):
                path = root / tool
                path.parent.mkdir(parents=True)
                path.touch()
            with patch.dict(os.environ, {'PATH': str(root), 'ProgramFiles': tmp}):
                server = AppServer(str(root / 'extension/codex.exe'))
                self.assertEqual([str(root), str(root / 'Git/cmd'), str(root / 'extension/codex-path')],
                                 server.env['PATH'].split(os.pathsep))
                self.assertEqual(tmp, os.environ['PATH'])
