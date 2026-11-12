"""Durable, explicitly targeted CLI jobs; no replay after process submission."""
from __future__ import annotations
import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import time
import uuid

TERMINAL = {'completed', 'failed', 'cancelled', 'timed_out'}
UUID = r'^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$'


class JobError(RuntimeError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def default_database():
    configured = os.environ.get('VERDANTFLARE_AGENT_ANTIGRAVITY_STATE')
    return Path(configured) if configured else Path(os.environ.get('AGY_GEMINI_HOME', '~/.gemini')).expanduser()/'antigravity'/'agent-jobs.sqlite'


def overlaps(a, b):
    try:
        return os.path.commonpath([a, b]) in {a, b}
    except ValueError:
        return False


class Jobs:
    def __init__(self, database=None):
        self.path = Path(database or default_database()).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.db() as db:
            db.execute('PRAGMA journal_mode=WAL')
            db.execute('''CREATE TABLE IF NOT EXISTS jobs(
                id TEXT PRIMARY KEY, request_id TEXT UNIQUE NOT NULL, digest TEXT NOT NULL,
                arguments TEXT NOT NULL, cwd TEXT NOT NULL, session_id TEXT NOT NULL,
                state TEXT NOT NULL, owner_pid INTEGER, heartbeat REAL, result TEXT,
                created REAL NOT NULL, updated REAL NOT NULL)''')

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        try:
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def submit(self, args, *, launch=True):
        if os.environ.get('VERDANTFLARE_AGENT_ANTIGRAVITY_DEPTH'):
            raise JobError('RECURSIVE_DELEGATION_DENIED')
        if set(args)-{'request_id','conversation_id','cwd','prompt','timeout_seconds'} or not {'request_id','conversation_id','cwd','prompt'} <= set(args):
            raise JobError('INVALID_ARGUMENT')
        args = dict(args)
        for key in ('request_id','conversation_id','cwd','prompt'):
            if not isinstance(args[key],str) or not args[key].strip() or '\x00' in args[key]:
                raise JobError('INVALID_ARGUMENT')
        if len(args['request_id'])>256 or not re.fullmatch(UUID,args['conversation_id']) or len(args['prompt'].encode('utf-8'))>65536:
            raise JobError('INVALID_ARGUMENT')
        timeout = args.setdefault('timeout_seconds',3600)
        if type(timeout) is not int or not 30<=timeout<=86400:
            raise JobError('INVALID_ARGUMENT')
        workspace = Path(args['cwd'])
        if not workspace.is_absolute() or not workspace.is_dir():
            raise JobError('WORKSPACE_DENIED')
        args['cwd'] = os.path.normcase(str(workspace.resolve()))
        digest = hashlib.sha256(encode(args).encode()).hexdigest()
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM jobs WHERE request_id=?',(args['request_id'],)).fetchone()
            if row:
                if row['digest'] != digest:
                    raise JobError('REQUEST_CONFLICT')
                job = dict(row)
            else:
                for active in db.execute("SELECT cwd,session_id FROM jobs WHERE state NOT IN ('completed','failed','cancelled','timed_out')"):
                    if overlaps(active['cwd'],args['cwd']) or active['session_id']==args['conversation_id']:
                        raise JobError('SESSION_BUSY')
                now=time.time(); jid=str(uuid.uuid4())
                db.execute('INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                           (jid,args['request_id'],digest,encode(args),args['cwd'],args['conversation_id'],'queued',None,None,None,now,now))
                job=dict(db.execute('SELECT * FROM jobs WHERE id=?',(jid,)).fetchone())
        if launch and job['state']=='queued':
            # Duplicate launch attempts are harmless: claim is transactional.
            subprocess.Popen([sys.executable,str(Path(__file__).resolve()),'--database',str(self.path),'--run',job['id']],
                             stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
                             creationflags=0x08000000 if os.name=='nt' else 0)
        return self.status(job['id'])

    def claim(self, job_id):
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            changed=db.execute("UPDATE jobs SET state='running',owner_pid=?,heartbeat=?,updated=? WHERE id=? AND state='queued'",
                               (os.getpid(),time.time(),time.time(),job_id)).rowcount
            return dict(db.execute('SELECT * FROM jobs WHERE id=?',(job_id,)).fetchone()) if changed else None

    def heartbeat(self, job_id):
        with self.db() as db:
            db.execute("UPDATE jobs SET heartbeat=? WHERE id=? AND owner_pid=? AND state='running'",(time.time(),job_id,os.getpid()))

    def finish(self, job_id, state, result):
        with self.db() as db:
            db.execute("UPDATE jobs SET state=?,result=?,updated=? WHERE id=? AND owner_pid=? AND state IN ('running','interrupted_unknown')",
                       (state,encode(result),time.time(),job_id,os.getpid()))

    def status(self, job_id):
        with self.db() as db:
            row=db.execute('SELECT * FROM jobs WHERE id=?',(job_id,)).fetchone()
            if not row:
                raise JobError('TASK_NOT_FOUND')
            # A stalled heartbeat means uncertain outcome, never permission to replay.
            if row['state']=='running' and time.time()-(row['heartbeat'] or 0)>30:
                db.execute("UPDATE jobs SET state='interrupted_unknown',updated=? WHERE id=? AND state='running'",(time.time(),job_id))
                row=db.execute('SELECT * FROM jobs WHERE id=?',(job_id,)).fetchone()
            return {'task_id':row['id'],'conversation_id':row['session_id'],'turn_id':row['request_id'] if row['state']!='queued' else None,
                    'mode':'cli','state':row['state'],'next_event':0,'has_more':False}

    def result(self, job_id, turn_id=None, cursor='0'):
        status=self.status(job_id)
        if turn_id is not None and turn_id!=status['turn_id']:
            raise JobError('STALE_TURN')
        if not isinstance(cursor,str) or not re.fullmatch(r'[0-9]{1,20}',cursor):
            raise JobError('INVALID_ARGUMENT')
        with self.db() as db:
            raw=db.execute('SELECT result FROM jobs WHERE id=?',(job_id,)).fetchone()[0]
        ready=status['state'] in TERMINAL and raw is not None
        raw=raw if ready else 'null';offset=int(cursor)
        return dict(status,ready=ready,content_json=raw[offset:offset+32768],
                    next_cursor=str(offset+32768) if offset+32768<len(raw) else None)


def call_tool(name, args):
    jobs=Jobs()
    if name=='agy_cli_task_start':
        return jobs.submit(args)
    allowed={'task_id','after_event','limit'} if name=='agy_cli_task_status' else {'task_id','turn_id','cursor'}
    if set(args)-allowed or not isinstance(args.get('task_id'),str) or not re.fullmatch(UUID,args['task_id']):
        raise JobError('INVALID_ARGUMENT')
    if name=='agy_cli_task_status':
        if type(args.get('after_event',0)) is not int or args.get('after_event',0)<0 or type(args.get('limit',50)) is not int or not 1<=args.get('limit',50)<=200:
            raise JobError('INVALID_ARGUMENT')
        return jobs.status(args['task_id'])
    return jobs.result(args['task_id'],args.get('turn_id'),args.get('cursor','0'))


def tool_schemas():
    text={'type':'string','minLength':1,'maxLength':65536}
    identifier={'type':'string','minLength':1,'maxLength':256}
    specs={
        'agy_cli_task_start':({'request_id':identifier,'conversation_id':{'type':'string','pattern':UUID},
                              'cwd':text,'prompt':text,'timeout_seconds':{'type':'integer','minimum':30,'maximum':86400}},
                             ['request_id','conversation_id','cwd','prompt']),
        'agy_cli_task_status':({'task_id':identifier,'after_event':{'type':'integer','minimum':0},
                               'limit':{'type':'integer','minimum':1,'maximum':200}},['task_id']),
        'agy_cli_task_result':({'task_id':identifier,'turn_id':identifier,'cursor':{'type':'string','pattern':'^[0-9]+$'}},['task_id'])}
    return [{'name':name,'description':name.replace('_',' '),'inputSchema':{'type':'object','properties':properties,
             'required':required,'additionalProperties':False}} for name,(properties,required) in specs.items()]


def run_job(jobs, job_id, *, command=None):
    job=jobs.claim(job_id)
    if not job:
        return
    args=json.loads(job['arguments'])
    if command is None:
        from agy_mcp_server import _agy_binary
        command=[str(_agy_binary()),'--input-format','stream-json','--output-format','stream-json',
                 '--conversation',args['conversation_id'],'--add-dir',args['cwd']]
    payload=(encode({'event':'user','message':{'role':'user','content':args['prompt']}})+'\n').encode('utf-8')
    process=None
    try:
        process=subprocess.Popen(command,cwd=args['cwd'],stdin=subprocess.PIPE,
                                 stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                                 env=dict(os.environ,VERDANTFLARE_AGENT_ANTIGRAVITY_DEPTH='1'),
                                 creationflags=0x08000000 if os.name=='nt' else 0)
        deadline=time.monotonic()+args['timeout_seconds']
        while True:
            jobs.heartbeat(job_id)
            try:
                stdout,stderr=process.communicate(input=payload,timeout=1)
                break
            except subprocess.TimeoutExpired:
                # communicate retains its buffered input across timeout retries.
                payload=None
                if time.monotonic()>=deadline:
                    process.terminate();process.communicate()
                    jobs.finish(job_id,'interrupted_unknown',{'answer':'','unresolved':['CLI deadline elapsed; native terminal outcome is unverified.']})
                    return
        if process.returncode!=0:
            jobs.finish(job_id,'failed',{'answer':'','exit_code':process.returncode,'unresolved':['CLI exited unsuccessfully.']})
            return
        events=[json.loads(line) for line in stdout.decode('utf-8-sig').splitlines() if line.strip()]
        results=[event['result'] for event in events if event.get('event')=='result']
        if len(results)!=1:
            raise JobError('BACKEND_OUTCOME_UNVERIFIED')
        output=results[0]
        if output.get('conversation_id')!=args['conversation_id']:
            raise JobError('BACKEND_TARGET_MISMATCH')
        if output.get('status')!='SUCCESS' or not isinstance(output.get('response'),str):
            raise JobError('BACKEND_OUTCOME_UNVERIFIED')
        jobs.finish(job_id,'completed',{'answer':output['response'],'native_result':output,'exit_code':0})
    except Exception as error:
        if process is not None and process.poll() is None:
            process.terminate();process.communicate()
        jobs.finish(job_id,'interrupted_unknown' if process else 'failed',
                    {'answer':'','unresolved':[error.code if isinstance(error,JobError) else type(error).__name__]})


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--database',required=True)
    parser.add_argument('--run',required=True)
    options=parser.parse_args()
    run_job(Jobs(options.database),options.run)
