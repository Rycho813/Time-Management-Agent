import copy
import json
import unittest
from unittest.mock import patch
from backend.activity_time import normalize_time
from backend.adapters import ActivityExtractor,NotionSource,GeminiModel,OllamaModel,FeishuSender,Transport,typed_rules,make_pipeline
from backend.core import analyze
from backend.pipeline import send_parts
from test_v12 import START,END,state,t

class Reply:
    def __init__(self,data,status=200):self.data=data;self.status_code=status
    def json(self):return self.data
class FakeHTTP:
    def __init__(self,handler):self.handler=handler;self.calls=[]
    def request(self,method,url,**kwargs):self.calls.append((method,url,kwargs));return self.handler(method,url,kwargs)

def record(text):return {'id':'block','date':'2026-10-28','text':text,'context':text}
def item(start='',end='',duration='半小时',place=''):
    return {'source_id':'block','event_name':'阅读','time_type':'effective','start_text':start,'end_text':end,'duration_text':duration,'place_text':place,'evidence':'阅读','project_name':'','project_status':'none'}
def extract(text,entry,aliases=None):
    return ActivityExtractor(lambda p,s:{'items':[entry]},aliases)([record(text)],typed_rules({'version':'v1'}))[0]

def block(i,text,children=False):return {'id':i,'type':'toggle','toggle':{'rich_text':[{'plain_text':text}]},'has_children':children}

class TimeForms(unittest.TestCase):
    def test_start_duration_natural_and_fixed(self):
        for start,duration in [('上午九点','半小时'),('09:00','30分钟'),('早上九点','0.5小时')]:
            a=normalize_time('2026-10-28',start,duration_text=duration)
            self.assertEqual((a['start'],a['end']),(t('2026-10-28T09:00'),t('2026-10-28T09:30')))
    def test_chinese_fractions_and_midnight(self):
        a=normalize_time('2026-10-28','晚上十一点半',duration_text='一小时')
        self.assertEqual(a['end'],t('2026-10-29T00:30'))
        self.assertEqual(normalize_time('2026-10-28','23:30','次日零点半')['duration_min'],60)
        a=normalize_time('2026-10-28','下午两点一刻','下午三点四十五分')
        self.assertEqual(a['duration_min'],90)
        self.assertEqual(normalize_time('2026-10-28','早上九点','十点半')['duration_min'],90)
        self.assertEqual(normalize_time('2026-10-28','23:30','次日00:30')['duration_min'],60)
    def test_duration_only_ambiguity_conflict(self):
        self.assertIsNone(normalize_time('2026-10-28',duration_text='一个半小时')['start'])
        a=normalize_time('2026-10-28','九点',duration_text='半小时');self.assertEqual(a['time_precision'],'ambiguous');self.assertIsNone(a['end'])
        self.assertEqual(normalize_time('2026-10-28','09:00','10:00','半小时')['time_precision'],'conflict')
        self.assertEqual(normalize_time('2026-10-28','23:30','00:30','一小时')['time_precision'],'conflict')
        for bad in ['25:00','下午十五点']:
            self.assertRaises(ValueError,normalize_time,'2026-10-28',bad,'10:00')
    def test_period_with_24_hour_clock_and_separator(self):
        for text,expected in [('下午14:55','14:55'),('下午16:50','16:50'),('晚上，19:40','19:40'),('晚上,19:40','19:40')]:
            a=normalize_time('2026-10-28',text,duration_text='半小时')
            self.assertEqual(a['start'],t('2026-10-28T'+expected))
        self.assertRaises(ValueError,normalize_time,'2026-10-28','上午14:55','15:25')
        self.assertRaises(ValueError,normalize_time,'2026-10-28','晚上14:55','15:25')

    def test_missing_and_invalid(self):
        for duration in ['0分钟','-1小时','很多小时']:
            self.assertRaises(ValueError,normalize_time,'2026-10-28',duration_text=duration)
        self.assertRaises(ValueError,normalize_time,'2026-10-28','上午九点')

class Extraction(unittest.TestCase):
    def test_no_place_time_match(self):
        a=extract('上午九点开始，阅读半小时',item('上午九点'))
        result=analyze(state(),[a],typed_rules({'version':'v1'}),START,END,END)
        self.assertEqual(result['locations'][0]['effective_ms'],1800000)
    def test_explicit_alias_and_duration_only(self):
        a=extract('北新桥图书馆阅读半小时',item(place='北新桥图书馆'),{'北新桥图书馆':'p'})
        self.assertEqual(analyze(state(),[a],typed_rules({'version':'v1'}),START,END,END)['locations'][0]['effective_ms'],1800000)
        self.assertIsNone(analyze(state(),[a],typed_rules({'version':'v1'}),t('2026-10-28T09:00'),t('2026-10-28T10:00'),END)['locations'][0]['utilization'])
    def test_unmatched_explicit_place_not_reassigned(self):
        a=extract('上午九点在家阅读半小时',item('上午九点',place='家'))
        self.assertIsNone(analyze(state(),[a],typed_rules({'version':'v1'}),START,END,END)['locations'][0]['utilization'])
    def test_conflict_not_used_as_full_day_duration(self):
        a=extract('09:00—10:00 阅读半小时',item('09:00','10:00'))
        result=analyze(state(),[a],typed_rules({'version':'v1'}),START,END,END)
        self.assertIsNone(result['locations'][0]['utilization']);self.assertTrue(result['days'][0]['unknown'])
    def test_hallucinated_evidence_and_schema_rejected(self):
        for entry in [{**item('上午九点'),'end_text':'上午九点半'},{**item(),'source_id':'invented'},{**item(),'time_type':'invented'}]:
            self.assertRaises(ValueError,extract,'上午九点阅读半小时',entry)
        self.assertRaises(ValueError,ActivityExtractor(lambda *a:{'items':'bad'}),[record('阅读')],{'version':'v1'})

class Protocols(unittest.TestCase):
    def test_network_default_closed(self):
        with patch('requests.request',side_effect=AssertionError('no network')):
            self.assertRaises(PermissionError,Transport().request,'GET','https://api.notion.com')
    def test_notion_original_page_paginated_context_and_readonly(self):
        def handler(method,url,kwargs):
            self.assertEqual(method,'GET');parent=url.split('/')[-2]
            if parent=='page':
                if 'start_cursor' not in kwargs['params']:return Reply({'results':[],'has_more':True,'next_cursor':'next'})
                return Reply({'results':[block('day','10.28',True)],'has_more':False})
            if parent=='day':return Reply({'results':[block('project','项目',True)],'has_more':False})
            return Reply({'results':[block('block','上午九点阅读半小时')],'has_more':False})
        http=FakeHTTP(handler);records=NotionSource({2026:'page'},'test',http)(START,END)
        self.assertEqual(records[-1]['context'],'项目 > 上午九点阅读半小时')
        self.assertTrue(all(c[0]=='GET' for c in http.calls))
    def test_duplicate_day_and_api_failure(self):
        http=FakeHTTP(lambda *a:Reply({'results':[block('a','10.28'),block('b','10.28')],'has_more':False}))
        self.assertRaises(ValueError,NotionSource({2026:'page'},'test',http),START,END)
        self.assertRaises(RuntimeError,NotionSource({2026:'page'},'test',FakeHTTP(lambda *a:Reply({},403))),START,END)
    def test_gemini_and_ollama_protocol(self):
        value={'items':[item('上午九点')]}
        http=FakeHTTP(lambda *a:Reply({'candidates':[{'content':{'parts':[{'text':json.dumps(value)}]}}]}))
        a=ActivityExtractor(GeminiModel('test','gemini-test',http))([record('上午九点阅读半小时')],{'version':'v1'})
        self.assertEqual(a[0]['duration_min'],30)
        self.assertIn('responseJsonSchema',http.calls[0][2]['json']['generationConfig'])
        http=FakeHTTP(lambda *a:Reply({'message':{'content':json.dumps(value)}}))
        self.assertEqual(OllamaModel('http://localhost:11434','test',http)('p',{})['items'],value['items'])
    def test_feishu_business_code_and_uncertain(self):
        import requests
        sender=FeishuSender('https://open.feishu.cn/open-apis/bot/v2/hook/test',FakeHTTP(lambda *a:Reply({'code':1})))
        self.assertEqual(send_parts('test',sender)[0]['status'],'failed')
        def timeout(*a):raise requests.Timeout()
        sender.http=FakeHTTP(timeout)
        self.assertEqual(send_parts('test',sender)[0]['status'],'uncertain')
        self.assertRaises(ValueError,FeishuSender,'https://example.com/hook',FakeHTTP(timeout))
    def test_composed_adapter_pipeline_no_real_calls_or_legacy_writes(self):
        def handler(method,url,kwargs):
            parent=url.split('/')[-2]
            if parent=='page':return Reply({'results':[block('day','10.28',True)],'has_more':False})
            return Reply({'results':[block('block','上午九点阅读半小时')],'has_more':False})
        sent=[]
        p=make_pipeline({2026:'page'},'test',lambda *a:{'items':[item('上午九点')]},state,{'version':'v1'},lambda text,key:(sent.append(text) or {'http_status':200,'code':0}),FakeHTTP(handler))
        with patch('requests.request',side_effect=AssertionError('no network')):
            result,status=p.manual(START,END,END)
        self.assertEqual(result['locations'][0]['effective_ms'],1800000);self.assertTrue(sent)


class APIAdapters(unittest.IsolatedAsyncioTestCase):
    async def test_provider_through_api_snapshots(self):
        import tempfile
        from pathlib import Path
        import httpx
        from backend.app import create_app
        from backend.adapters import ActivityProvider
        with tempfile.TemporaryDirectory() as tmp:
            source=lambda *args:[record('上午九点开始，阅读半小时')]
            provider=ActivityProvider(source,ActivityExtractor(lambda *args:{'items':[item('上午九点')]}))
            app=create_app(Path(tmp)/'db','adapter-test-token-long',rules=typed_rules({'version':'v1'}),activity_provider=provider,clock=lambda:END)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test',headers={'Authorization':'Bearer adapter-test-token-long'}) as client:
                await client.post('/v1/sync/batches',json={'batch_id':'b','base_revision':0,'state':state()})
                req={'request_id':'adapted','mode':'efficiency','start':START,'end':END,'expected_revision':1}
                result=(await client.post('/v1/analysis-jobs',json=req)).json()
                self.assertEqual(result['locations'][0]['effective_ms'],1800000)
                self.assertEqual(result['days'][0]['locations'][0]['activities'][0]['time_precision'],'start_duration')
                provider.source=lambda *args:(_ for _ in ()).throw(AssertionError('普通查看不读取Notion'))
                self.assertEqual((await client.get('/v1/analysis-jobs/adapted')).json(),result)
