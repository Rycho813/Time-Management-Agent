import concurrent.futures
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
import sqlite3
import httpx
from backend.app import create_app
from backend.prod import build_app
from backend.store import Store,Conflict
from backend.maintenance import backup,restore,verify
from backend.delivery_worker import send_next
from test_v12 import START,END,state,rules

class Hardening(unittest.TestCase):
    def test_schema_backup_and_future_refusal(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'db';db=Store(path)
            db.sync({'batch_id':'a','base_revision':0,'state':state()})
            with sqlite3.connect(path) as conn:conn.execute('PRAGMA user_version=0')
            Store(path);self.assertTrue(Path(str(path)+'.pre-v2.sqlite3').exists())
            with sqlite3.connect(path) as conn:conn.execute('PRAGMA user_version=99')
            self.assertRaises(ValueError,Store,path)
    def test_pagination_pins_target_during_mutation(self):
        with tempfile.TemporaryDirectory() as tmp:
            db=Store(Path(tmp)/'db');db.sync({'batch_id':'a','base_revision':0,'state':state()})
            page=db.changes(0,1);target=page['revision']
            new=state();new['segments'][0]['end']-=1000
            db.sync({'batch_id':'b','base_revision':1,'state':new})
            page2=db.changes(page['next_cursor'],100,target,page['after'])
            self.assertEqual(page2['revision'],1);self.assertFalse(page2['has_more'])
            self.assertEqual(page2['items'][0]['operations'][0]['value']['end'],state()['segments'][0]['end'])
    def test_stage_restart_and_atomic_rejection(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'db';db=Store(path)
            ops=[{'table':table,'action':'upsert','id':v['id'],'value':v} for table in ('places','segments') for v in state()[table]]
            db.stage('upload',0,ops[:1]);self.assertEqual(db.state()[0],0)
            self.assertRaises(Conflict,db.commit_stage,'upload',2,0)
            db=Store(path);db.stage('upload',1,ops[1:]);db.commit_stage('upload',2,0)
            self.assertEqual(db.commit_stage('upload',2,0),{'revision':1})
            with db.connect() as conn:self.assertEqual(conn.execute('SELECT count(*) FROM changes').fetchone()[0],0)
    def test_rotation_race_and_quota(self):
        with tempfile.TemporaryDirectory() as tmp:
            db=Store(Path(tmp)/'db');db.initialize_token('old')
            def rotate(_):
                try:return db.rotate('old')
                except Conflict:return None
            with concurrent.futures.ThreadPoolExecutor(2) as pool:out=list(pool.map(rotate,range(2)))
            self.assertEqual(sum(v is not None for v in out),1);self.assertFalse(db.authorized('old'))
            self.assertTrue(db.quota('test',1,now=0));self.assertFalse(db.quota('test',1,now=1));self.assertTrue(db.quota('test',1,now=61))
    def test_lease_heartbeat_and_attempt_limit(self):
        with tempfile.TemporaryDirectory() as tmp:
            db=Store(Path(tmp)/'db');db.enqueue('j','digest',{})
            job=db.claim(now=0,lease_seconds=1);db.renew('j',job[-1])
            self.assertIsNone(db.claim(now=2))
            with db.connect() as conn:conn.execute("UPDATE jobs SET expires=0,attempts=3")
            self.assertIsNone(db.claim(now=4));self.assertEqual(db.job('j')['status'],'failed')
    def test_compaction_checkpoint_and_tombstone_preservation(self):
        with tempfile.TemporaryDirectory() as tmp:
            db=Store(Path(tmp)/'db');db.sync({'batch_id':'a','base_revision':0,'state':state()})
            empty={'schemaVersion':1,'places':[],'segments':[]}
            db.sync({'batch_id':'b','base_revision':1,'state':empty});db.compact()
            self.assertRaises(Conflict,db.changes,1)
            self.assertRaises(Conflict,db.changes,0,100,1,0)
            page=db.changes(0,100);self.assertEqual(page['revision'],2);self.assertFalse(page['has_more'])
            self.assertRaises(Conflict,db.sync,{'batch_id':'revive','base_revision':2,'state':state()})
    def test_staging_commit_cleans_chunks_without_losing_ack(self):
        with tempfile.TemporaryDirectory() as tmp:
            db=Store(Path(tmp)/'db');db.stage('upload',0,[])
            self.assertEqual(db.commit_stage('upload',1,0),{'revision':1})
            with db.connect() as conn:self.assertEqual(conn.execute('SELECT count(*) FROM staging').fetchone()[0],0)
            db.stage('upload',0,[]);self.assertEqual(db.commit_stage('upload',1,0),{'revision':1})
    def test_sender_blocks_later_parts_after_uncertain(self):
        with tempfile.TemporaryDirectory() as tmp:
            db=Store(Path(tmp)/'db')
            with db.connect() as conn:
                conn.execute("INSERT INTO deliveries VALUES('one','report','first','sending',NULL)")
                conn.execute("INSERT INTO deliveries VALUES('two','report','second','prepared',NULL)")
            self.assertFalse(send_next(db,lambda *a:(_ for _ in ()).throw(AssertionError('no send'))))

    def test_verified_restore_and_corruption(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'db';db=Store(path);db.sync({'batch_id':'a','base_revision':0,'state':state()})
            saved=Path(tmp)/'backup';backup(path,saved);verify(saved)
            restored=Path(tmp)/'restore';restore(saved,restored)
            self.assertEqual(Store(restored).state(),db.state());self.assertRaises(ValueError,restore,saved,restored)
            saved.write_bytes(b'bad');self.assertRaises(ValueError,restore,saved,Path(tmp)/'corrupt')
    def test_prod_startup_fail_closed_and_no_external_call(self):
        self.assertRaises(ValueError,build_app,{})
        with tempfile.TemporaryDirectory() as tmp,patch('requests.request',side_effect=AssertionError('no network')):
            conf=Path(tmp)/'rules';conf.write_text(json.dumps(rules()))
            env={'TIMEMAP_DB':str(Path(tmp)/'db'),'TIMEMAP_DEVICE_TOKEN':'a'*40,'TIMEMAP_ORIGINS':'https://timemap.rychoz.com','TIMEMAP_RULES':str(conf)}
            app=build_app(env);self.assertIsNotNone(app)
            self.assertRaises(ValueError,build_app,{**env,'TIMEMAP_ALLOW_MODEL':'1'})
            self.assertRaises(ValueError,build_app,{**env,'TIMEMAP_ORIGINS':'*'})
    def test_outbox_queued_atomically_and_uncertain_never_repeated(self):
        from backend.core import analyze
        with tempfile.TemporaryDirectory() as tmp:
            db=Store(Path(tmp)/'db');db.enqueue('j','digest',{});job=db.claim()
            result=analyze(state(),[],rules(),START,END,END);result['delivery']='queued'
            db.complete('j','digest',job[-1],result=result)
            self.assertIsNotNone(db.report('j'))
            def timeout(*a):raise TimeoutError()
            self.assertTrue(send_next(db,timeout));self.assertFalse(send_next(db,timeout))
            with db.connect() as conn:self.assertEqual(conn.execute('SELECT status FROM deliveries').fetchone()[0],'uncertain')

class HardeningAPI(unittest.IsolatedAsyncioTestCase):
    async def test_production_labels_quotas_and_error_redaction(self):
        with tempfile.TemporaryDirectory() as tmp:
            app=create_app(Path(tmp)/'db','x'*40,rules=rules(),mode='production',eager=False,embedded_worker=False,allow_efficiency=False)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test',headers={'Authorization':'Bearer '+'x'*40}) as c:
                self.assertEqual((await c.get('/health')).json()['mode'],'production')
                self.assertEqual((await c.post('/v1/sync/batches',json={'unexpected':'PRIVATE'})).json()['detail'],'invalid_schema')
                response=await c.post('/v1/sync/batches',content=json.dumps({'x':[[[[1]]]]}),headers={'Content-Type':'application/json'})
                self.assertNotIn('PRIVATE',response.text)
                for _ in range(120):last=await c.get('/health')
                self.assertEqual(last.status_code,429)
