"""Antigravity CLI jobs with frozen request identity and exact native session."""
from ..worker import NotAccepted, Reconciliation, Observation
from ..store import Conflict
from .task_mcp import TaskMCPAdapter, prompt_for, unwrap, acknowledgment, ServiceError


class AntigravityAdapter(TaskMCPAdapter):
    prefix='agy_cli_'
    agent='antigravity'
    native_key='conversation_id'

    async def panel_call(self, name, arguments):
        return unwrap(await self.client.call(name,arguments))

    async def panel_status(self, endpoint):
        status=await self.panel_call('agy_get_conversation',{'conversation_id':endpoint['native_session_id'],'wait_sec':0})
        if status.get('conversation_id')!=endpoint['native_session_id']:
            raise Conflict('BACKEND_TARGET_MISMATCH')
        return status

    async def panel_scan(self, message, endpoint):
        journal=self.store.prepared_request(message['id'])
        request=journal['request']
        status=await self.panel_status(endpoint)
        total=status.get('num_total_steps')
        if type(total) is not int or total<request['baseline']:
            raise Conflict('TRAJECTORY_BASELINE_LOST')
        target=request['arguments']['prompt']
        matches=[];turn_steps=[];inside=False
        start=request['baseline']
        while start<total:
            page=await self.panel_call('agy_read_transcript',{'conversation_id':endpoint['native_session_id'],
                'start_step':start,'end_step':min(start+50,total),'include_thinking':False,'source':'hub'})
            if page.get('conversation_id')!=endpoint['native_session_id'] or page.get('source')!='hub_rpc' or page.get('start_step')!=start:
                raise Conflict('TRAJECTORY_IDENTITY_MISMATCH')
            end=page.get('end_step')
            if type(end) is not int or not start<end<=total or len(page.get('steps',[]))!=end-start:
                raise Conflict('RESULT_CURSOR_NOT_ADVANCING')
            for index,step in enumerate(page['steps'],start):
                if step.get('step_index')!=index:
                    raise Conflict('TRAJECTORY_IDENTITY_MISMATCH')
                if step.get('type')=='CORTEX_STEP_TYPE_USER_INPUT':
                    inside=step.get('content')==target
                    if inside:matches.append(index)
                elif inside:
                    turn_steps.append(step)
            start=end
        if len(matches)>1:
            raise Conflict('DUPLICATE_NATIVE_SUBMISSION')
        receipt=None if not matches else {'session_id':endpoint['native_session_id'],'task_id':endpoint['native_session_id'],
                                            'turn_id':f"step:{matches[0]}:{message['id']}"}
        return status,receipt,turn_steps

    async def deliver(self,message,endpoint,handoff):
        if endpoint['mode']!='panel':
            return await super().deliver(message,endpoint,handoff)
        if endpoint['agent']!=self.agent or message['body']['kind']=='cancel':
            raise NotAccepted('UNSUPPORTED_CAPABILITY')
        journal=self.store.prepared_request(message['id'])
        if journal is None:
            try:
                status=await self.panel_status(endpoint)
            except ServiceError as error:
                if error.code=='OFFLINE':
                    raise NotAccepted('OFFLINE') from None
                raise
            if status.get('status')!='CASCADE_RUN_STATUS_IDLE':
                raise NotAccepted('SESSION_BUSY')
            baseline=status.get('num_total_steps')
            if type(baseline) is not int or baseline<0:
                raise Conflict('TRAJECTORY_BASELINE_LOST')
            request={'tool':'agy_dispatch_task','baseline':baseline,'arguments':{
                'conversation_id':endpoint['native_session_id'],'workspace':endpoint['workspace'],'mode':'panel',
                'wait':False,'focus_panel':False,'strict_workspace':True,'prompt':prompt_for(message,endpoint,handoff)}}
            self.store.prepare_request(message['id'],request)
            accepted=await self.panel_call(request['tool'],request['arguments'])
            if not accepted.get('dispatched') or accepted.get('conversation_id')!=endpoint['native_session_id']:
                raise Conflict('BACKEND_TARGET_MISMATCH')
            self.store.accepted_request(message['id'],accepted)
        # Even without an RPC receipt, never resend: prove the exact prompt in native steps.
        _,receipt,_=await self.panel_scan(message,endpoint)
        if receipt is None:
            raise Conflict('BACKEND_SUBMISSION_UNRESOLVED')
        return receipt

    async def reconcile(self,message,endpoint):
        if endpoint['mode']!='panel':
            return await super().reconcile(message,endpoint)
        if not self.store.prepared_request(message['id']):
            return Reconciliation(retry_safe_evidence='No native panel request was prepared.')
        _,receipt,_=await self.panel_scan(message,endpoint)
        return Reconciliation(receipt=receipt)

    async def observe(self,message,endpoint):
        if endpoint['mode']!='panel':
            return await super().observe(message,endpoint)
        status,receipt,steps=await self.panel_scan(message,endpoint)
        if receipt!=message['receipt']:
            raise Conflict('RESULT_IDENTITY_MISMATCH')
        if status.get('status')!='CASCADE_RUN_STATUS_IDLE':
            return Observation(cursor=status.get('num_total_steps'))
        replies=[step.get('content','') for step in steps if step.get('type')=='CORTEX_STEP_TYPE_PLANNER_RESPONSE']
        failed=any('ERROR' in str(step.get('status','')) or 'ERROR' in str(step.get('type','')) for step in steps)
        if not replies and not failed:
            return Observation(cursor=status.get('num_total_steps'))
        result={'answer':replies[-1] if replies else '', 'steps':steps}
        return Observation(cursor=status.get('num_total_steps'),terminal='failed' if failed else 'completed',result=result,
                           acknowledgment=acknowledgment(message,result,receipt['turn_id']))

    async def prepare(self, message, endpoint, handoff):
        if endpoint['mode']!='cli' or message['body']['kind']=='cancel':
            raise NotAccepted('UNSUPPORTED_CAPABILITY')
        return {'tool':'agy_cli_task_start','arguments':{
            'request_id':self.request_id(message),'conversation_id':endpoint['native_session_id'],
            'cwd':endpoint['workspace'],'prompt':prompt_for(message,endpoint,handoff)}}


def create(argv, store):
    return AntigravityAdapter(argv,store)
