import concurrent.futures
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
from agy_jobs import Jobs, JobError, run_job


class DurableCLITests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.jobs=Jobs(self.root/'jobs.sqlite')
        self.args={'request_id':'message-1','conversation_id':'11111111-2222-3333-4444-555555555555',
                   'cwd':str(self.root),'prompt':'read identity only'}

    def test_concurrent_replay_creates_one_job_and_one_claim(self):
        def submit(_):
            return Jobs(self.jobs.path).submit(self.args,launch=False)['task_id']
        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
            ids=list(pool.map(submit,range(12)))
        self.assertEqual(len(set(ids)),1)
        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
            claimed=list(pool.map(lambda _:Jobs(self.jobs.path).claim(ids[0]),range(12)))
        self.assertEqual(sum(x is not None for x in claimed),1)
        with self.assertRaisesRegex(JobError,'REQUEST_CONFLICT'):
            self.jobs.submit(dict(self.args,prompt='different'),launch=False)

    def test_nested_submission_is_rejected_before_launch(self):
        with patch.dict(os.environ,VERDANTFLARE_AGENT_ANTIGRAVITY_DEPTH='1'), patch('agy_jobs.subprocess.Popen') as launch:
            with self.assertRaisesRegex(JobError,'RECURSIVE_DELEGATION_DENIED'):
                self.jobs.submit(self.args)
            launch.assert_not_called()

    def test_real_process_result_survives_new_reader_and_paginates(self):
        task=self.jobs.submit(self.args,launch=False)
        answer='中'*40000
        code="import json; print(json.dumps({'event':'result','result':{'conversation_id':"+repr(self.args['conversation_id'])+",'status':'SUCCESS','response':'中'*40000}},ensure_ascii=True))"
        run_job(self.jobs,task['task_id'],command=[sys.executable,'-c',code])
        reader=Jobs(self.jobs.path)
        status=reader.status(task['task_id'])
        self.assertEqual(status['state'],'completed')
        cursor='0';parts=[]
        while cursor is not None:
            page=reader.result(task['task_id'],status['turn_id'],cursor)
            self.assertTrue(page['ready']);parts.append(page['content_json']);cursor=page['next_cursor']
        self.assertEqual(json.loads(''.join(parts))['answer'],answer)
        with patch('agy_jobs.subprocess.Popen') as launch:
            self.assertEqual(reader.submit(self.args)['task_id'],task['task_id'])
            launch.assert_not_called()

    def test_unknown_worker_keeps_lease_and_is_never_relaunched(self):
        task=self.jobs.submit(self.args,launch=False)
        self.jobs.claim(task['task_id'])
        with self.jobs.db() as db:
            db.execute('UPDATE jobs SET heartbeat=?',(time.time()-60,))
        self.assertEqual(self.jobs.status(task['task_id'])['state'],'interrupted_unknown')
        with patch('agy_jobs.subprocess.Popen') as launch:
            self.jobs.submit(self.args)
            launch.assert_not_called()
        with self.assertRaisesRegex(JobError,'SESSION_BUSY'):
            self.jobs.submit(dict(self.args,request_id='replacement'),launch=False)

    def test_large_prompt_uses_stdin_and_survives_communicate_timeout(self):
        prompt='x'*60000
        task=self.jobs.submit(dict(self.args,prompt=prompt),launch=False)
        code=("import sys,json,time; time.sleep(1.2); x=json.loads(sys.stdin.readline()); "
              "assert x['event']=='user'; assert len(x['message']['content'])==60000; "
              "print(json.dumps({'event':'result','result':{'conversation_id':"+
              repr(self.args['conversation_id'])+",'status':'SUCCESS','response':'stdin-ok'}}))")
        run_job(self.jobs,task['task_id'],command=[sys.executable,'-c',code])
        result=self.jobs.result(task['task_id'])
        self.assertEqual(result['state'],'completed')
        self.assertEqual(json.loads(result['content_json'])['answer'],'stdin-ok')

    def test_multiple_terminal_results_are_not_accepted(self):
        task=self.jobs.submit(self.args,launch=False)
        code="print('{\"event\":\"result\",\"result\":{}}\\n'*2)"
        run_job(self.jobs,task['task_id'],command=[sys.executable,'-c',code])
        self.assertEqual(self.jobs.status(task['task_id'])['state'],'interrupted_unknown')

    def test_wrong_native_session_cannot_be_completed(self):
        task=self.jobs.submit(self.args,launch=False)
        code="import json;print(json.dumps({'event':'result','result':{'conversation_id':'wrong','status':'SUCCESS','response':'done'}}))"
        run_job(self.jobs,task['task_id'],command=[sys.executable,'-c',code])
        self.assertEqual(self.jobs.status(task['task_id'])['state'],'interrupted_unknown')
        self.assertFalse(self.jobs.result(task['task_id'])['ready'])

    def test_stale_turn_and_sibling_workspace_protection(self):
        task=self.jobs.submit(self.args,launch=False)
        child=self.root/'child';child.mkdir()
        with self.assertRaisesRegex(JobError,'SESSION_BUSY'):
            self.jobs.submit(dict(self.args,request_id='child',cwd=str(child)),launch=False)
        self.jobs.claim(task['task_id'])
        with self.assertRaisesRegex(JobError,'STALE_TURN'):
            self.jobs.result(task['task_id'],'wrong-turn')

    def test_nonzero_cli_exit_is_failure_not_empty_success(self):
        task=self.jobs.submit(self.args,launch=False)
        run_job(self.jobs,task['task_id'],command=[sys.executable,'-c','import sys; sys.stderr.write("fatal backend auth failure\\n"); raise SystemExit(4)'])
        result=self.jobs.result(task['task_id'])
        self.assertEqual(result['state'],'failed')
        payload=json.loads(result['content_json'])
        self.assertEqual(payload['exit_code'],4)
        self.assertIn('fatal backend auth failure',payload['error'])
        self.assertTrue(any('fatal backend auth failure' in item for item in payload['unresolved']))

    def test_stream_json_error_before_conversation_reports_explicit_reason(self):
        task=self.jobs.submit(self.args,launch=False)
        code="import json; print(json.dumps({'event':'result','result':{'conversation_id':'','status':'ERROR','error':'Conversation quota exceeded'}}))"
        run_job(self.jobs,task['task_id'],command=[sys.executable,'-c',code])
        result=self.jobs.result(task['task_id'])
        self.assertEqual(result['state'],'failed')
        payload=json.loads(result['content_json'])
        self.assertIn('Conversation quota exceeded',payload['error'])

    def test_panel_busy_or_unreadable_never_sends(self):
        import agy_mcp_server as server
        hub={'address':'test','csrf_token':'test','summaries':{}}
        with patch.object(server,'select_hub',return_value=hub), patch.object(server,'lsrpc_call',return_value={'status':'CASCADE_RUN_STATUS_RUNNING'}) as rpc:
            with self.assertRaisesRegex(JobError,'SESSION_BUSY'):
                server.tool_agy_dispatch_task('test',conversation_id=self.args['conversation_id'],workspace=str(self.root),wait=False)
            self.assertEqual([c.args[2] for c in rpc.call_args_list],['GetCascadeTrajectory'])
