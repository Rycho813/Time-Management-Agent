"""Mock development API. Structured activities are injected, NEVER read from production.
Factory requires explicit token; mock mode is labelled in every report.
"""
import hashlib
import time
import asyncio
import threading
from copy import deepcopy
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from fastapi import FastAPI, Depends, HTTPException, Request as HTTPRequest
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ConfigDict, Field
from .core import analyze, clip, validate_range, TZ
from .store import Store, Conflict, encode

class Batch(BaseModel):
    model_config=ConfigDict(extra='forbid')
    batch_id: str=Field(min_length=1,max_length=128)
    base_revision: int=Field(strict=True,ge=0)
    state: dict | None=None
    operations: list[dict] | None=None
    restore_deleted: bool=False

class Request(BaseModel):
    model_config=ConfigDict(extra='forbid')
    request_id: str=Field(min_length=1,max_length=128)
    mode: str
    start: int=Field(strict=True,ge=0)
    end: int=Field(strict=True,ge=0)
    expected_revision: int=Field(strict=True,ge=0)
    allow_stale: bool=False
    send_report: bool=False


def create_app(path,token,activities=None,rules=None,origins=(),clock=None,eager=True,activity_provider=None,mode="mock",embedded_worker=True,allow_efficiency=True,allow_send=False,place_aliases=None):
    if not token or len(token)<16: raise ValueError('显式设置至少16字符的本地测试凭据')
    if mode not in ('mock','production'):raise ValueError('未知运行模式')
    if mode=='production' and (len(token)<32 or rules is None or activities is not None or eager or embedded_worker):raise ValueError('真实模式配置不完整或包含模拟数据')
    db=Store(path);clock=clock or (lambda:int(time.time()*1000))
    activities=activities if activities is not None else []
    rules=rules if rules is not None else {'version':'mock-v1','categories':{'reading':'effective','coding':'effective','rest':'buffer'}}
    db.initialize_token(token)
    def execute(request):
        validate_range(request.start,request.end)
        if request.send_report and not allow_send:raise ValueError('sending_disabled')
        rev,state=db.state()
        if rev!=request.expected_revision and not request.allow_stale: raise Conflict('数据版本不同，请同步后重试')
        active_rules=deepcopy(rules)
        if request.mode=='efficiency' and (not allow_efficiency or (mode=='production' and activity_provider is None)):raise ValueError('efficiency_disabled')
        extracted = (activity_provider(request.start,request.end,active_rules) if activity_provider else activities) if request.mode=='efficiency' else []
        result=analyze(state,extracted,active_rules,request.start,request.end,clock(),place_aliases=place_aliases)
        result.update({'id':request.request_id,'status':'succeeded','mode':request.mode,'data_revision':rev,'mock':mode=='mock','delivery':'queued' if request.send_report else 'not_requested','stale_allowed':request.allow_stale})
        if request.mode=='location':
            for day in result['days']:
                for p in day['locations']:
                    for k in ('effective_ms','utilization','unconfirmed_ms','uncertain','activities'):p.pop(k,None)
            for p in result['locations']:
                for k in ('effective_ms','utilization','uncertain'):p.pop(k,None)
            result.pop('rule_version',None)
        return result
    def run_next():
        job=db.claim()
        if not job:return False
        rid,request,digest,lease=job
        result=None;error=None
        stopped=threading.Event()
        def heartbeat():
            while not stopped.wait(15):
                try:db.renew(rid,lease)
                except Conflict:return
        pulse=threading.Thread(target=heartbeat,daemon=True);pulse.start()
        try:
            if mode=='production' and request['mode']=='efficiency' and not db.quota('model_calls',20,86400):raise ValueError('model_daily_limit')
            result=execute(Request(**request))
        except Exception as exc:error=type(exc).__name__ if mode=='production' else str(exc)
        finally:stopped.set();pulse.join(timeout=2)
        try:db.complete(rid,digest,lease,result=result,error=error)
        except Conflict:pass # another worker reclaimed the expired lease; never overwrite it
        return True
    @asynccontextmanager
    async def lifespan(app):
        async def worker():
            while True:
                await asyncio.to_thread(run_next)
                await asyncio.sleep(.2)
        task=asyncio.create_task(worker()) if not eager and embedded_worker else None
        try:yield
        finally:
            if task:
                task.cancel()
                try:await task
                except asyncio.CancelledError:pass
    app=FastAPI(title='Time_map mock development API',lifespan=lifespan)
    app.add_middleware(CORSMiddleware,allow_origins=list(origins),allow_methods=['GET','POST','PUT'],allow_headers=['Authorization','Content-Type'])
    @app.middleware('http')
    async def bounded_body(request:HTTPRequest,call_next):
        from fastapi.responses import JSONResponse
        if not db.quota('http:'+str(request.client.host if request.client else 'local'),120):return JSONResponse(status_code=429,headers={'Retry-After':'60'},content={'detail':'rate_limited'})
        body=bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body)>2*1024*1024:return JSONResponse(status_code=413,content={'detail':'请求超过2MiB，请分批同步'})
        if body and request.headers.get('content-type','').startswith('application/json'):
            try:
                import json
                value=json.loads(body)
                def depth(v,n=0):
                    if n>24:raise ValueError('json_depth')
                    if isinstance(v,dict):
                        for child in v.values():depth(child,n+1)
                    elif isinstance(v,list):
                        for child in v:depth(child,n+1)
                depth(value)
            except (ValueError,RecursionError):return JSONResponse(status_code=422,content={'detail':'invalid_json'})
        request._body=bytes(body)
        return await call_next(request)
    bearer=HTTPBearer(auto_error=False)
    def auth(credentials: HTTPAuthorizationCredentials | None=Depends(bearer)):
        if credentials is None or not db.authorized(credentials.credentials): raise HTTPException(401,'未授权')
    from fastapi.exceptions import RequestValidationError
    @app.exception_handler(RequestValidationError)
    async def invalid_schema(_,exc):
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=422,content={'detail':'invalid_schema'})
    @app.exception_handler(Conflict)
    async def conflict(_,exc):
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=409,content={'detail':str(exc)})
    @app.exception_handler(ValueError)
    async def invalid(_,exc):
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=422,content={'detail':'invalid_request' if mode=='production' else str(exc)})
    @app.get('/health')
    def health(): return {'status':'ok','mode':mode,'external_calls':mode=='production' and allow_efficiency}
    @app.post('/v1/sync/batches',dependencies=[Depends(auth)])
    def sync(batch:Batch):
        if (batch.state is None)==(batch.operations is None):raise ValueError('必须且只能提供state或operations')
        if batch.operations is not None and len(batch.operations)>500:raise ValueError('请分批提交')
        return db.sync(batch.model_dump(exclude_none=True))
    @app.post('/v1/device/rotate',dependencies=[Depends(auth)])
    def rotate(credentials:HTTPAuthorizationCredentials=Depends(bearer)):
        return {'token':db.rotate(credentials.credentials)}
    @app.get('/v1/sync/changes',dependencies=[Depends(auth)])
    def changes(cursor:int=0,limit:int=50,target:int|None=None,after:int=0):return db.changes(cursor,limit,target,after)
    @app.post('/v1/sync/stage/{sid}/{part}',dependencies=[Depends(auth)])
    def stage(sid:str,part:int,operations:list[dict]):return db.stage(sid,part,operations)
    @app.post('/v1/sync/commit/{sid}',dependencies=[Depends(auth)])
    def commit(sid:str,body:dict):
        return db.commit_stage(sid,body['parts'],body['base_revision'],body.get('restore_deleted',False))
    @app.get('/v1/segments',dependencies=[Depends(auth)])
    def segments(start:int,end:int):
        rev,state=db.state()
        return {'revision':rev,'segments':clip(state['segments'],start,end,clock())}
    @app.get('/v1/settlements/{day}',dependencies=[Depends(auth)])
    def settle(day:str):
        d=datetime.strptime(day,'%Y-%m-%d').replace(tzinfo=TZ)
        if d.date()>=datetime.fromtimestamp(clock()/1000,TZ).date(): raise ValueError('仅结算已结束自然日')
        start=int(d.timestamp()*1000);end=int((d+timedelta(days=1)).timestamp()*1000)
        rev,state=db.state();parts=clip(state['segments'],start,end,clock())
        result={'date':day,'revision':rev,'segments':parts,'recorded_ms':sum(s['duration'] for s in parts),'warnings':['结算仅表示已收到记录；不保证整日记录完整']}
        with db.connect() as conn:
            conn.execute('INSERT OR REPLACE INTO settlements VALUES(?,?,?)',(day,rev,encode(result)))
        return result
    @app.post('/v1/analysis-jobs',dependencies=[Depends(auth)])
    def report(request:Request):
        validate_range(request.start,request.end)
        if request.mode not in ('location','efficiency'): raise ValueError('未知分析模式')
        if request.mode=='efficiency' and not allow_efficiency:raise ValueError('efficiency_disabled')
        if request.send_report and not allow_send:raise ValueError('sending_disabled')
        digest=hashlib.sha256(encode(request.model_dump()).encode()).hexdigest()
        old=db.report(request.request_id)
        if old:
            if old[0]!=digest: raise Conflict('请求ID不能用于不同报告')
            return old[1]
        existing=db.job(request.request_id)
        if not existing and not db.quota('analysis',20,86400):raise HTTPException(429,'daily_analysis_limit')
        if not eager:return db.enqueue(request.request_id,digest,request.model_dump())
        return db.save_report(request.request_id,digest,execute(request))
    @app.post('/v1/analysis-jobs/{rid}/retry',dependencies=[Depends(auth)])
    def retry(rid:str):
        with db.connect() as conn:
            row=conn.execute('SELECT status,attempts FROM jobs WHERE id=?',(rid,)).fetchone()
            if not row:raise HTTPException(404,'任务不存在')
            if row[0]!='failed' or row[1]>=3:raise Conflict('只允许失败任务重试，最多3次尝试')
            conn.execute("UPDATE jobs SET status='queued',error=NULL WHERE id=?",(rid,))
        return db.job(rid)
    @app.get('/v1/reports',dependencies=[Depends(auth)])
    def history(offset:int=0,limit:int=20):return {'items':db.history(offset,limit)}
    @app.get('/v1/analysis-jobs/{rid}',dependencies=[Depends(auth)])
    def get_report(rid:str):
        old=db.report(rid)
        if old:return old[1]
        job=db.job(rid)
        if job:return job
        raise HTTPException(404,'报告不存在')
    app.state.execute=execute
    app.state.run_next=run_next
    app.state.store=db
    return app
