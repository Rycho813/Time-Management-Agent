import copy
import hashlib
import tempfile
import unittest
from pathlib import Path
import httpx
from backend.app import create_app
from backend.store import Store,Conflict,encode
from backend.pipeline import Pipeline,send_parts
from test_v12 import state,activity,rules,START,END

class Remaining(unittest.TestCase):
    def test_delta_pagination_restore(self):
        with tempfile.TemporaryDirectory() as tmp:
            db=Store(Path(tmp)/'db')
            ops=[{'table':table,'action':'upsert','id':v['id'],'value':v} for table in ('places','segments') for v in state()[table]]
            db.sync({'batch_id':'1','base_revision':0,'operations':ops})
            db.sync({'batch_id':'2','base_revision':1,'operations':[{'table':'segments','action':'delete','id':'s'}]})
            page=db.changes(0,1);self.assertTrue(page['has_more']);self.assertEqual(page['next_cursor'],0)
            self.assertGreater(page['after'],0)
            self.assertEqual(db.changes(1,1)['items'][0]['operations'][0]['action'],'delete')
            self.assertRaises(Conflict,db.sync,{'batch_id':'3','base_revision':2,'operations':ops})
            db.sync({'batch_id':'4','base_revision':2,'operations':ops,'restore_deleted':True})
            self.assertEqual(db.state()[1],state())
    def test_expired_lease_restart_fencing(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'db';db=Store(path);db.enqueue('j','digest',{'a':1})
            first=db.claim(now=0,lease_seconds=1)
            recovered=Store(path);second=recovered.claim(now=2)
            self.assertRaises(Conflict,db.complete,'j','digest',first[-1],result={'old':True})
            recovered.complete('j','digest',second[-1],result={'new':True})
            self.assertEqual(db.report('j')[1],{'new':True});self.assertEqual(db.job('j')['attempts'],2)
    def test_mock_workflow_and_side_effect_boundaries(self):
        reads=[];extracts=[];writes=[];messages=[]
        def source(a,b):reads.append((a,b));return ['原始日程：09:30—10:30 阅读']
        def extractor(raw,r):extracts.append(raw);return [activity()]
        def sender(text,key):messages.append(text);return {'http_status':200,'code':0}
        p=Pipeline(source,extractor,state,rules(),sender,lambda *args:writes.append(args))
        _,delivery=p.manual(START,END,END);self.assertEqual(writes,[]);self.assertEqual(len(reads),1)
        result,_=p.daily('2026-10-28',START,END,END)
        self.assertEqual(len(writes),1);self.assertEqual(len(extracts),2)
        self.assertEqual(writes[0][1][0]['time_type'],'effective')
        self.assertEqual(result['locations'][0]['effective_ms'],3600000)
        self.assertIn('【地点效率】',messages[-1]);self.assertIn('有效利用时间',messages[-1])
    def test_daily_location_failure_preserves_original(self):
        messages=[];writes=[]
        def unavailable():raise OSError('offline')
        p=Pipeline(lambda *a:['原始记录'],lambda *a:[activity()],unavailable,rules(),lambda text,key:(messages.append(text) or {'http_status':200,'code':0}),lambda *a:writes.append(a))
        result,_=p.daily('2026-10-28',START,END,END)
        self.assertTrue(result['location_unavailable']);self.assertEqual(len(writes),1)
        self.assertIn('有效利用时间',messages[0]);self.assertIn('暂不可用',messages[0])

    def test_message_splitting_business_failure_uncertain(self):
        parts=[]
        def sender(text,key):parts.append(text);return {'http_status':200,'code':0}
        result=send_parts('中文'*20,sender,12);self.assertEqual(''.join(parts),'中文'*20)
        self.assertTrue(all(len(p.encode())<=12 for p in parts))
        self.assertTrue(all(r['status']=='sent' for r in result))
        self.assertEqual(send_parts('x',lambda *a:{'http_status':200,'code':1})[0]['status'],'failed')
        def timeout(*args):raise TimeoutError()
        self.assertEqual(send_parts('x',timeout)[0]['status'],'uncertain')

class AsyncAPI(unittest.IsolatedAsyncioTestCase):
    async def test_failed_job_retry_and_body_limit(self):
        with tempfile.TemporaryDirectory() as tmp:
            app=create_app(Path(tmp)/'db','long-local-test-token',eager=False,clock=lambda:END)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test',headers={'Authorization':'Bearer long-local-test-token'}) as c:
                req={'request_id':'bad','mode':'efficiency','start':START,'end':END,'expected_revision':1}
                await c.post('/v1/analysis-jobs',json=req);app.state.run_next()
                self.assertEqual((await c.get('/v1/analysis-jobs/bad')).json()['status'],'failed')
                self.assertEqual((await c.post('/v1/analysis-jobs/bad/retry')).json()['status'],'queued')
                self.assertEqual((await c.post('/v1/sync/batches',content=b'x'*(2*1024*1024+1))).status_code,413)

    async def test_queue_restart_history_and_rotation(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'db';token='local-test-token-long'
            app=create_app(path,token,[activity()],rules(),eager=False,clock=lambda:END)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test',headers={'Authorization':'Bearer '+token}) as c:
                await c.post('/v1/sync/batches',json={'batch_id':'b','base_revision':0,'state':state()})
                req={'request_id':'j','mode':'efficiency','start':START,'end':END,'expected_revision':1}
                self.assertEqual((await c.post('/v1/analysis-jobs',json=req)).json()['status'],'queued')
                restarted=create_app(path,token,[activity()],rules(),eager=False,clock=lambda:END)
                self.assertTrue(restarted.state.run_next())
                self.assertEqual((await c.get('/v1/analysis-jobs/j')).json()['status'],'succeeded')
                self.assertEqual(len((await c.get('/v1/reports')).json()['items']),1)
                new=(await c.post('/v1/device/rotate')).json()['token']
                self.assertEqual((await c.get('/v1/reports')).status_code,401)
                c.headers['Authorization']='Bearer '+new
                self.assertEqual((await c.get('/v1/reports')).status_code,200)
