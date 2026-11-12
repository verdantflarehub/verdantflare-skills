import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch
from verdantflare_agent_claude.backend import _extract_transcript_summary, rename_session, locate_transcript_path
from verdantflare_agent_claude.engine import Engine
from verdantflare_agent_claude.errors import BridgeError
from verdantflare_agent_claude.schema import validate


class NamingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.sid = '11111111-2222-3333-4444-555555555555'
        self.transcript = locate_transcript_path(self.root/'.claude', str(self.root), self.sid)
        self.transcript.parent.mkdir(parents=True)
        self.transcript.write_text(json.dumps({'type':'user','cwd':str(self.root),'message':{'content':'first'}})+'\n',encoding='utf-8')

    def append(self, record):
        with self.transcript.open('a',encoding='utf-8') as stream:
            stream.write(json.dumps(record)+'\n')

    def test_latest_native_custom_title_outweighs_generated_title(self):
        for _ in range(130):
            self.append({'type':'progress'})
        self.append({'type':'custom-title','customTitle':'old'})
        self.append({'type':'custom-title','customTitle':'Plan｜T3｜Claude'})
        self.append({'type':'ai-title','title':'generated'})
        self.assertEqual(_extract_transcript_summary(self.transcript)['title'],'Plan｜T3｜Claude')

    def test_path_traversal_id_rejected(self):
        with self.assertRaises(BridgeError):
            validate('claude_session_rename',{'session_id':'../another','cwd':str(self.root),'name':'X'})

    async def test_control_requests_do_not_submit_user_prompt(self):
        frames = []
        stream = asyncio.StreamReader()
        process = AsyncMock()
        process.returncode = None
        process.stdout = stream
        class Input:
            def write(inner, raw):
                frame = json.loads(raw)
                frames.append(frame)
                if frame['request']['subtype'] == 'rename_session':
                    self.append({'type':'custom-title','customTitle':frame['request']['title']})
                stream.feed_data((json.dumps({'type':'control_response','response':{'subtype':'success','request_id':frame['request_id']}})+'\n').encode())
            async def drain(inner):
                pass
        process.stdin = Input()
        process.terminate = lambda: setattr(process,'returncode',0)
        with patch('verdantflare_agent_claude.backend.asyncio.create_subprocess_exec',AsyncMock(return_value=process)):
            result = await rename_session(claude_binary='fake',claude_dir=self.root/'.claude',cwd=str(self.root),session_id=self.sid,name='Plan')
        self.assertEqual(result['naming'],'native')
        self.assertEqual([f['type'] for f in frames],['control_request','control_request'])
        self.assertEqual([f['request']['subtype'] for f in frames],['initialize','rename_session'])
        process.wait.assert_awaited_once()

    async def test_online_session_rejected_before_spawn(self):
        engine = Engine({'database':str(self.root/'service.db'),'allowed_roots':[str(self.root)],'claude_dir':str(self.root/'.claude')})
        with patch('verdantflare_agent_claude.engine.find_live_panel_sessions',return_value={self.sid:{}}), patch('verdantflare_agent_claude.engine.rename_session',AsyncMock()) as rename:
            with self.assertRaises(BridgeError) as error:
                await engine.call('claude_session_rename',{'session_id':self.sid,'cwd':str(self.root),'name':'Plan'})
            self.assertEqual(error.exception.code,'SESSION_BUSY')
            rename.assert_not_called()
