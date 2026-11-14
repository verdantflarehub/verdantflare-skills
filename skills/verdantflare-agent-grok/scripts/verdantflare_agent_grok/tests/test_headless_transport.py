import asyncio
import json
from pathlib import Path
import time
import unittest
from unittest.mock import AsyncMock, patch
from verdantflare_agent_grok.backend import run_headless_turn


class HeadlessTransportTests(unittest.IsolatedAsyncioTestCase):
    async def test_resume_uses_native_resume_flag_and_file_prompt(self):
        process=AsyncMock()
        process.returncode=0
        process.communicate.return_value=(json.dumps({'session_id':'existing-session','result':'DONE'}).encode(),b'')
        seen={}
        async def spawn(*args,**kwargs):
            seen['args']=args
            seen['prompt']=Path(args[args.index('--prompt-file')+1]).read_text('utf-8')
            return process
        with patch('verdantflare_agent_grok.backend.asyncio.create_subprocess_exec',side_effect=spawn):
            result=await run_headless_turn(grok_binary='grok',cwd='.',session_id='existing-session',prompt='x'*60000,
                deadline=time.time()+30,register_handle=lambda h:None,is_cancelling=lambda:False)
        self.assertEqual(result['session_id'],'existing-session')
        self.assertEqual(result['answer'],'DONE')
        self.assertEqual(seen['args'][-2:],('--resume','existing-session'))
        self.assertNotIn('--session-id',seen['args'])
        self.assertNotIn('-p',seen['args'])
        self.assertEqual(seen['prompt'],'x'*60000)
        self.assertFalse(Path(seen['args'][seen['args'].index('--prompt-file')+1]).exists())

    async def test_unstructured_or_wrong_identity_never_completes(self):
        from verdantflare_agent_grok.errors import BridgeError
        for output in [b'DONE',b'{}',b'{"session_id":"wrong","result":"DONE"}']:
            process=AsyncMock();process.returncode=0;process.communicate.return_value=(output,b'')
            with patch('verdantflare_agent_grok.backend.asyncio.create_subprocess_exec',return_value=process):
                with self.assertRaises(BridgeError):
                    await run_headless_turn(grok_binary='grok',cwd='.',session_id='expected',prompt='test',
                        deadline=time.time()+30,register_handle=lambda h:None,is_cancelling=lambda:False)
