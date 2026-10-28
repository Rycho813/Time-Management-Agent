"""Mock development API. Structured activities are injected, NEVER read from production.
Factory requires explicit token; mock mode is labelled in every report.
"""
import hashlib
import hmac
import time
from datetime import datetime, timedelta
from fastapi import FastAPI, Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ConfigDict, Field
from .core import analyze, clip, validate_range, TZ
from .store import Store, Conflict, encode

class Batch(BaseModel):
    model_config=ConfigDict(extra='forbid')
    batch_id: str=Field(min_length=1,max_length=128)
    base_revision: int=Field(strict=True,ge=0)
    state: dict

class Request(BaseModel):
    model_config=ConfigDict(extra='forbid')
    request_id: str=Field(min_length=1,max_length=128)
    mode: str
    start: int=Field(strict=True,ge=0)
    end: int=Field(strict=True,ge=0)
    expected_revision: int=Field(strict=True,ge=0)
    allow_stale: bool=False


def create_app(path,token,activities=None,rules=None,origins=(),clock=None):
    if not token or len(token)<16: raise ValueError('显式设置至少16字符的本地测试凭据')
    db=Store(path);clock=clock or (lambda:int(time.time()*1000))
    activities=activities if activities is not None else []
    rules=rules if rules is not None else {'version':'mock-v1','categories':{'reading':'effective','coding':'effective','rest':'buffer'}}
    app=FastAPI(title='Time_map mock development API')
    app.add_middleware(CORSMiddleware,allow_origins=list(origins),allow_methods=['GET','POST'],allow_headers=['Authorization','Content-Type'])
    bearer=HTTPBearer(auto_error=False)
    def auth(credentials: HTTPAuthorizationCredentials | None=Depends(bearer)):
        if credentials is None or not hmac.compare_digest(credentials.credentials,token): raise HTTPException(401,'未授权')
    @app.exception_handler(Conflict)
    async def conflict(_,exc):
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=409,content={'detail':str(exc)})
    @app.exception_handler(ValueError)
    async def invalid(_,exc):
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=422,content={'detail':str(exc)})
    @app.get('/health')
    def health(): return {'status':'ok','mode':'mock','external_calls':False}
    @app.post('/v1/sync/batches',dependencies=[Depends(auth)])
    def sync(batch:Batch): return db.sync(batch.model_dump())
    @app.get('/v1/sync/changes',dependencies=[Depends(auth)])
    def changes(cursor:int=0):
        if cursor<0: raise ValueError('游标无效')
        rev,state=db.state()
        if cursor>rev: raise Conflict('游标超出当前版本')
        return {'revision':rev,'state':state if cursor<rev else None}
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
        digest=hashlib.sha256(encode(request.model_dump()).encode()).hexdigest()
        old=db.report(request.request_id)
        if old:
            if old[0]!=digest: raise Conflict('请求ID不能用于不同报告')
            return old[1]
        rev,state=db.state()
        if rev!=request.expected_revision and not request.allow_stale: raise Conflict('数据版本不同，请同步后重试')
        result=analyze(state,activities if request.mode=='efficiency' else [],rules,request.start,request.end,clock())
        result.update({'id':request.request_id,'status':'succeeded','mode':request.mode,'data_revision':rev,'mock':True,'delivery':'not_requested','stale_allowed':request.allow_stale})
        if request.mode=='location':
            for day in result['days']:
                for p in day['locations']:
                    for k in ('effective_ms','utilization','unconfirmed_ms','uncertain','activities'):p.pop(k,None)
            for p in result['locations']:
                for k in ('effective_ms','utilization','uncertain'):p.pop(k,None)
            result.pop('rule_version',None)
        return db.save_report(request.request_id,digest,result)
    @app.get('/v1/analysis-jobs/{rid}',dependencies=[Depends(auth)])
    def get_report(rid:str):
        old=db.report(rid)
        if not old: raise HTTPException(404,'报告不存在')
        return old[1]
    app.state.store=db
    return app
