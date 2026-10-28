import asyncio
import copy
import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
import httpx
from backend.app import create_app
from backend.core import analyze, validate_state
from backend.store import Store,Conflict

def t(s): return int(datetime.fromisoformat(s+'+08:00').timestamp()*1000)
START=t('2026-10-28T00:00');END=t('2026-10-29T00:00')
def state():return {'schemaVersion':1,'places':[{'id':'p','name':'图书馆'}],'segments':[{'id':'s','kind':'place','placeId':'p','placeName':'图书馆','start':t('2026-10-28T09:00'),'end':t('2026-10-28T12:00')}]}
def activity():return {'id':'a','date':'2026-10-28','event_name':'阅读','category':'reading','duration_min':60,'start':t('2026-10-28T09:30'),'end':t('2026-10-28T10:30'),'evidence':'09:30至10:30阅读'}
def rules():return {'version':'v1','categories':{'reading':'effective'}}

class Analysis(unittest.TestCase):
    def test_partial_time_matching(self):
        r=analyze(state(),[activity()],rules(),t('2026-10-28T10:00'),t('2026-10-28T11:00'),END)
        self.assertEqual(r['locations'][0]['effective_ms'],1800000);self.assertEqual(r['locations'][0]['utilization'],.5)
    def test_duration_only_full_day_and_partial(self):
        a=activity();a.pop('start');a.pop('end');a['place_id']='p'
        self.assertEqual(analyze(state(),[a],rules(),START,END,END)['locations'][0]['utilization'],1/3)
        r=analyze(state(),[a],rules(),t('2026-10-28T10:00'),t('2026-10-28T11:00'),END)
        self.assertIsNone(r['locations'][0]['utilization']);self.assertTrue(r['days'][0]['unknown'])
    def test_duplicate_overlap_and_overflow(self):
        a=activity();r=analyze(state(),[a,a],rules(),START,END,END);self.assertIsNone(r['locations'][0]['utilization'])
        b={**a,'id':'b'};r=analyze(state(),[a,b],rules(),START,END,END);self.assertIsNone(r['locations'][0]['utilization'])
        a.pop('start');a.pop('end');a.update(place_id='p',duration_min=240)
        self.assertIsNone(analyze(state(),[a],rules(),START,END,END)['locations'][0]['utilization'])
    def test_midnight_clip_and_unknown_place(self):
        a=activity();a.update(start=t('2026-10-28T23:30'),end=t('2026-10-29T00:30'))
        r=analyze(state(),[a],rules(),START,END+86400000,END+86400000)
        self.assertEqual(len(r['days']),2);self.assertEqual(sum(x['duration_ms'] for d in r['days'] for x in d['unknown']),3600000)
    def test_mismatch_not_proportionally_allocated(self):
        a=activity();a['duration_min']=30
        r=analyze(state(),[a],rules(),START,END,END)
        self.assertIsNone(r['locations'][0]['utilization'])
    def test_explicit_unknown_location_not_repeated_on_other_days(self):
        a=activity();a['unmatched_place']='未确认地点'
        r=analyze(state(),[a],rules(),START,END+86400000,END+86400000)
        self.assertEqual(len(r['days'][0]['unknown']),1)
        self.assertEqual(r['days'][1]['unknown'],[])

    def test_unknown_buffer_does_not_hide_confirmed_effective_ratio(self):
        a=activity();buffer={'id':'buffer','date':'2026-10-28','category':'rest','duration_min':60,'event_name':'生活事务'}
        rule=rules();rule['categories']['rest']='buffer'
        buffer['category']='rest'
        r=analyze(state(),[a,buffer],rule,START,END,END)
        self.assertEqual(r['locations'][0]['utilization'],1/3)
        self.assertEqual(len(r['days'][0]['unknown']),1)
        self.assertEqual(r['days'][0]['gap_ms'],21*3600000)
    def test_unmapped_timed_effective_only_affects_overlapping_places(self):
        s=state();s['places'].append({'id':'home','name':'家'})
        s['segments'].insert(0,{'id':'home','kind':'place','placeId':'home','placeName':'家','start':START,'end':START+3600000})
        a=activity();unknown={**a,'id':'unknown','start':START,'end':START+1800000,'duration_min':30,'unmatched_place':'子地点'}
        r=analyze(s,[a,unknown],rules(),START,END,END)
        locations={p['id']:p for p in r['locations']}
        self.assertIsNone(locations['home']['utilization'])
        self.assertEqual(locations['p']['utilization'],1/3)

    def test_state_validation(self):
        s=state();s['segments'].append(copy.deepcopy(s['segments'][0]));self.assertRaises(ValueError,validate_state,s)

class Persistence(unittest.TestCase):
    def setUp(self):self.tmp=tempfile.TemporaryDirectory();self.db=Store(Path(self.tmp.name)/'db')
    def tearDown(self):self.tmp.cleanup()
    def test_idempotency_conflict_and_delete(self):
        b={'batch_id':'one','base_revision':0,'state':state()};self.assertEqual(self.db.sync(b),self.db.sync(b))
        self.assertRaises(Conflict,self.db.sync,{**b,'batch_id':'two'})
        empty={'schemaVersion':1,'places':[],'segments':[]};self.db.sync({'batch_id':'delete','base_revision':1,'state':empty})
        self.assertRaises(Conflict,self.db.sync,{'batch_id':'revive','base_revision':2,'state':state()})
        self.assertEqual(self.db.state(),(2,empty))
    def test_backup_restore_and_atomic_invalid(self):
        self.db.sync({'batch_id':'one','base_revision':0,'state':state()})
        invalid=state();invalid['segments'][0]['end']=1
        self.assertRaises(ValueError,self.db.sync,{'batch_id':'bad','base_revision':1,'state':invalid})
        path=Path(self.tmp.name)/'backup';self.db.backup(path);self.assertEqual(Store(path).state(),self.db.state())

class API(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.activities=[activity()];self.rules=rules()
        self.app=create_app(Path(self.tmp.name)/'db','local-test-token-123',self.activities,self.rules,clock=lambda:END+86400000)
        self.client=httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app),base_url='http://test',headers={'Authorization':'Bearer local-test-token-123'})
    async def asyncTearDown(self):await self.client.aclose();self.tmp.cleanup()
    async def test_auth_sync_range_and_settle(self):
        self.assertEqual((await self.client.get('/v1/segments',params={'start':START,'end':END},headers={'Authorization':'bad'})).status_code,401)
        r=await self.client.post('/v1/sync/batches',json={'batch_id':'b','base_revision':0,'state':state()});self.assertEqual(r.status_code,200,r.text)
        self.assertEqual((await self.client.get('/v1/segments',params={'start':START,'end':END+8*86400000})).status_code,422)
        r=await self.client.get('/v1/settlements/2026-10-28');self.assertEqual(r.json()['recorded_ms'],10800000)
    async def test_immutable_reports_latest_rules_no_external_calls(self):
        await self.client.post('/v1/sync/batches',json={'batch_id':'b','base_revision':0,'state':state()})
        request={'request_id':'old','mode':'efficiency','start':START,'end':END,'expected_revision':1}
        # Any production HTTP call is a test failure. Only the in-process ASGI transport is used.
        with patch('requests.sessions.Session.request',side_effect=AssertionError('external request forbidden')):
            old=(await self.client.post('/v1/analysis-jobs',json=request)).json()
            self.assertEqual(old['locations'][0]['effective_ms'],3600000)
            self.rules['version']='v2';self.rules['categories']['reading']='buffer'
            new=(await self.client.post('/v1/analysis-jobs',json={**request,'request_id':'new'})).json()
            self.assertEqual(new['locations'][0]['effective_ms'],0)
            self.assertEqual((await self.client.get('/v1/analysis-jobs/old')).json(),old)
            self.assertEqual((await self.client.post('/v1/analysis-jobs',json=request)).json(),old)
            self.assertEqual(self.app.state.store.state()[0],1)
    async def test_source_correction_creates_new_snapshot(self):
        await self.client.post('/v1/sync/batches',json={'batch_id':'b','base_revision':0,'state':state()})
        req={'request_id':'before-edit','mode':'efficiency','start':START,'end':END,'expected_revision':1}
        old=(await self.client.post('/v1/analysis-jobs',json=req)).json()
        self.activities[0]['duration_min']=120
        self.activities[0]['end']=t('2026-10-28T11:30')
        new=(await self.client.post('/v1/analysis-jobs',json={**req,'request_id':'after-edit'})).json()
        self.assertEqual(new['locations'][0]['effective_ms'],7200000)
        self.assertEqual((await self.client.get('/v1/analysis-jobs/before-edit')).json(),old)
        self.activities.clear()
        deleted=(await self.client.post('/v1/analysis-jobs',json={**req,'request_id':'after-delete'})).json()
        self.assertEqual(deleted['locations'][0]['effective_ms'],0)
        self.assertEqual((await self.client.get('/v1/analysis-jobs/before-edit')).json(),old)

    async def test_stale_and_location_no_efficiency(self):
        request={'request_id':'x','mode':'location','start':START,'end':END,'expected_revision':5}
        self.assertEqual((await self.client.post('/v1/analysis-jobs',json=request)).status_code,409)
        r=(await self.client.post('/v1/analysis-jobs',json={**request,'allow_stale':True})).json()
        self.assertTrue(r['stale_allowed']);self.assertNotIn('rule_version',r)


class SupplementAndScheduler(unittest.TestCase):
    def test_logs_deduplicate_and_conflicts(self):
        from backend.logs import supplement
        data={'format':'time-map-log-v1.2','start':START,'end':END,'segments':state()['segments']}
        text='```json\n'+json.dumps(data)+'\n```'
        result,warnings=supplement(state(),text)
        self.assertEqual(result,state());self.assertFalse(warnings)
        data['segments'][0]['end']-=1000
        result,warnings=supplement(state(),'```json\n'+json.dumps(data)+'\n```')
        self.assertEqual(result,state());self.assertTrue(warnings)
    def test_due_catchup_idempotent_recalculate(self):
        from backend.settlement import settle_due
        with tempfile.TemporaryDirectory() as tmp:
            db=Store(Path(tmp)/'db');db.sync({'batch_id':'a','base_revision':0,'state':state()})
            self.assertEqual(settle_due(db,t('2026-10-29T01:00'),'2026-10-28'),[])
            self.assertEqual(len(settle_due(db,t('2026-10-29T01:30'),'2026-10-28')),1)
            self.assertEqual(settle_due(db,t('2026-10-29T02:00'),'2026-10-28'),[])
            changed=state();changed['segments'][0]['end']-=3600000
            db.sync({'batch_id':'b','base_revision':1,'state':changed})
            self.assertEqual(settle_due(db,t('2026-10-29T03:00'),'2026-10-28')[0]['recorded_ms'],7200000)

if __name__=='__main__':unittest.main()
