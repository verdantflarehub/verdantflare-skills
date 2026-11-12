import copy
import unittest
import test_codex_adapter as fixtures
from verdantflare_agent.adapters.antigravity import AntigravityAdapter
from verdantflare_agent.worker import NotAccepted


class MockAgy(fixtures.MockCodex):
    async def call(self,name,args):
        result=await super().call(name.replace('agy_cli_','codex_',1),args)
        self.calls[-1]=(name,copy.deepcopy(args))
        value=result.get('structuredContent',{})
        if 'thread_id' in value:
            value['conversation_id']=value.pop('thread_id')
        return result


class AntigravityAdapterTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        fixtures.CodexAdapterTests.setUp(self)
        self.endpoint.update(agent='antigravity',mode='cli')

    async def test_exact_cli_session_and_durable_request(self):
        client=MockAgy(mode='cli')
        adapter=AntigravityAdapter([],self.store,client=client)
        receipt=await adapter.deliver(self.message,self.endpoint,self.h)
        self.assertEqual(client.calls[0][0],'agy_cli_task_start')
        args=client.calls[0][1]
        self.assertEqual(args['conversation_id'],'native-worker')
        self.assertNotIn('skip_permissions',args)
        self.assertNotIn('model',args)
        client.calls.clear()
        recovered=await adapter.reconcile(self.message,self.endpoint)
        self.assertEqual(recovered.receipt,receipt)
        self.assertEqual([name for name,_ in client.calls],['agy_cli_task_status'])

    async def test_lost_response_reuses_same_request(self):
        client=MockAgy(mode='cli');client.drop_response=True
        adapter=AntigravityAdapter([],self.store,client=client)
        with self.assertRaises(TimeoutError):
            await adapter.deliver(self.message,self.endpoint,self.h)
        self.assertTrue((await adapter.reconcile(self.message,self.endpoint)).retry_safe_evidence)
        await adapter.deliver(self.message,self.endpoint,self.h)
        self.assertEqual(len(client.writes),1)

    async def test_unsupported_mode_and_cancel_never_fall_back_to_cli(self):
        client=MockAgy(mode='cli')
        adapter=AntigravityAdapter([],self.store,client=client)
        with self.assertRaises(NotAccepted):
            await adapter.deliver(self.message,dict(self.endpoint,mode='plugin'),self.h)
        self.message['body']['kind']='cancel'
        with self.assertRaises(NotAccepted):
            await adapter.deliver(self.message,self.endpoint,self.h)
        self.assertFalse(client.calls)

    async def test_panel_response_loss_recovers_exact_prompt_without_resend(self):
        endpoint=dict(self.endpoint,mode='panel')
        calls=[];steps=[]
        async def call(name,args):
            calls.append(name)
            if name=='agy_get_conversation':
                value={'conversation_id':'native-worker','status':'CASCADE_RUN_STATUS_IDLE','num_total_steps':len(steps)}
            elif name=='agy_dispatch_task':
                steps.append({'step_index':0,'type':'CORTEX_STEP_TYPE_USER_INPUT','content':args['prompt']})
                steps.append({'step_index':1,'type':'CORTEX_STEP_TYPE_PLANNER_RESPONSE','content':'done'})
                raise TimeoutError()
            elif name=='agy_read_transcript':
                self.assertEqual(args['source'],'hub')
                value={'conversation_id':'native-worker','source':'hub_rpc','start_step':0,'end_step':len(steps),'steps':steps}
            else:
                raise AssertionError(name)
            return {'structuredContent':value}
        client=MockAgy(mode='cli');client.call=call
        adapter=AntigravityAdapter([],self.store,client=client)
        with self.assertRaises(TimeoutError):
            await adapter.deliver(self.message,endpoint,self.h)
        receipt=(await adapter.reconcile(self.message,endpoint)).receipt
        self.assertEqual(receipt['session_id'],'native-worker')
        self.assertEqual(await adapter.deliver(self.message,endpoint,self.h),receipt)
        self.assertEqual(calls.count('agy_dispatch_task'),1)

    async def test_panel_busy_never_submits(self):
        client=MockAgy(mode='cli')
        async def call(name,args):
            self.assertEqual(name,'agy_get_conversation')
            return {'structuredContent':{'conversation_id':'native-worker','status':'CASCADE_RUN_STATUS_RUNNING','num_total_steps':1}}
        client.call=call
        with self.assertRaises(NotAccepted):
            await AntigravityAdapter([],self.store,client=client).deliver(self.message,dict(self.endpoint,mode='panel'),self.h)
        self.assertIsNone(self.store.prepared_request(self.message['id']))
