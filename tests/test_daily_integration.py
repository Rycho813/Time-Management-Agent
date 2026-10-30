import copy
import importlib
import os
import unittest
from contextlib import ExitStack
from unittest.mock import patch, Mock
from backend.daily import daily_activities, day_bounds
from time_summary import normalize_items
from backend.pipeline import Pipeline
from test_v12 import activity, rules, state, t, START, END


class NaturalDay(unittest.TestCase):
    def test_previous_duration_only_and_completed_crossnight(self):
        a={**activity(),'date':'2026-10-27','start':t('2026-10-27T23:30'),'end':t('2026-10-28T00:30'),'project_name':'project','project_status':'closed'}
        previous={**activity(),'id':'old','date':'2026-10-27','start':None,'end':None}
        data=[a,previous];before=copy.deepcopy(data)
        yesterday,_=daily_activities(data,'2026-10-27')
        today,_=daily_activities(data,'2026-10-28')
        self.assertEqual([v['duration_min'] for v in yesterday],[30,60])
        self.assertEqual([v['duration_min'] for v in today],[30])
        self.assertEqual(yesterday[0]['project_status'],'open')
        self.assertEqual(today[0]['project_status'],'closed')
        self.assertEqual(today[0]['date'],'2026-10-28')
        self.assertEqual(data,before)
    def test_midnight_end_duplicate_conflict_and_fraction(self):
        a={**activity(),'end':END,'start':END-1800000}
        after,_=daily_activities([a],'2026-10-29');self.assertEqual(after,[])
        own={**activity(),'start':None,'end':None}
        out,warnings=daily_activities([own,own,{**own,'id':'conflict','time_precision':'conflict'},{**own,'id':'seconds','duration_min':0.5}], '2026-10-28')
        self.assertEqual(len(out),1);self.assertEqual(len(warnings),3)
    def test_projection_feeds_legacy_and_no_send_when_preparing(self):
        a={**activity(),'date':'2026-10-27','start':t('2026-10-27T23:30'),'end':t('2026-10-28T00:30')}
        calls=[];sender=Mock(side_effect=AssertionError('must not send'))
        p=Pipeline(lambda *a:['synthetic'],lambda *_:[a],state,rules(),sender,legacy_complete=lambda day,items,sleep,**kw:(calls.append(items) or ('snapshot','comparison\nproject completion')))
        result,text=p.prepare_daily('2026-10-28',START,END,END)
        self.assertEqual(calls[0][0]['duration_min'],30)
        self.assertIn('comparison\nproject completion',text);self.assertIn('【地点效率】',text)
        sender.assert_not_called()
        with self.assertRaises(ValueError):p.prepare_daily('2026-10-28',START+1,END,END)


class LegacyEntry(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Synthetic source ID permits import; all I/O methods are replaced below.
        with patch.dict(os.environ,{'NOTION_SOURCE_PAGE_ID':'00000000-0000-0000-0000-000000000000'}):
            cls.module=importlib.import_module('migrate_and_summary')
    def test_shared_chapters_and_writeback_order(self):
        m=self.module;events=[];items=normalize_items([{**activity(),'time_type':'effective'}])
        with ExitStack() as stack:
            stack.enter_context(patch('requests.request',side_effect=AssertionError('no real network')))
            for name,ret in [('update_projects_for_day','project completion'),('build_comparison_sections','daily/weekly comparison'),('update_page_rich_text_properties',None),('update_life_calendar_from_items',None)]:
                stack.enter_context(patch.object(m,name,side_effect=lambda *a,_name=name,_ret=ret,**kw:(events.append((_name,a,kw)) or _ret)))
            snapshot,summary=m.complete_daily_items('2026-10-28','synthetic-page',items,420,6)
        self.assertEqual([v[0] for v in events],['update_projects_for_day','build_comparison_sections','update_page_rich_text_properties','update_life_calendar_from_items'])
        self.assertLess(summary.index('daily/weekly comparison'),summary.index('project completion'))
        self.assertEqual(events[2][1][1],{m.NOTION_SNAPSHOT_PROP:snapshot,m.NOTION_SUMMARY_PROP:summary})
        self.assertEqual(events[-1][2]['items'],items)
    def test_default_old_path_enabled_path_and_dry_run(self):
        m=self.module
        for enabled,dry in [(False,False),(True,False),(True,True)]:
            with ExitStack() as stack:
                stack.enter_context(patch.dict(os.environ,{'TIMEMAP_DAILY_V12':'1' if enabled else '0'}))
                migrate=stack.enter_context(patch.object(m,'migrate_single_day',return_value=('page','block')))
                old=stack.enter_context(patch.object(m,'generate_snapshot_and_summary',return_value=('snapshot','summary')))
                send=stack.enter_context(patch.object(m,'send_feishu_text'))
                validate=stack.enter_context(patch('backend.daily_bridge.configuration'))
                new=stack.enter_context(patch('backend.daily_bridge.prepare_legacy_daily',return_value=({},'prepared')))
                m.run_daily_workflow('2026-10-28',True,True,True,7,False,dry,6)
                self.assertEqual(old.call_count,int(not enabled and not dry))
                self.assertEqual(new.call_count,int(enabled and not dry))
                self.assertEqual(validate.call_count,int(enabled and not dry))
                send.assert_not_called();migrate.assert_called_once()
    def test_invalid_enabled_config_fails_before_migration(self):
        m=self.module
        with patch.dict(os.environ,{'TIMEMAP_DAILY_V12':'1','TIMEMAP_ALLOW_MODEL':'0'}),patch.object(m,'migrate_single_day') as migrate:
            with self.assertRaises(ValueError):m.run_daily_workflow('2026-10-28',True,True,True,7,False,False,6)
            migrate.assert_not_called()


class Bridge(unittest.TestCase):
    def test_real_adapter_wiring_with_fake_http_and_readonly_location_db(self):
        import json
        import tempfile
        from pathlib import Path
        from backend.store import Store
        from backend.daily_bridge import prepare_legacy_daily
        from test_adapters import FakeHTTP, Reply, block
        entry={'source_id':'overnight','event_name':'阅读','time_type':'effective','start_text':'23:30','end_text':'次日00:30','duration_text':'','place_text':'','evidence':'阅读','project_name':'测试项目','project_status':'closed'}
        old={**entry,'source_id':'old','start_text':'','end_text':'','duration_text':'60分钟','project_status':'open'}
        today={**entry,'source_id':'today','start_text':'','end_text':'','duration_text':'30分钟','project_status':'none'}
        def handler(method,url,kwargs):
            if method=='GET':
                parent=url.split('/')[-2]
                children={'page':[block('previous','10.27',True),block('current','10.28',True)],'previous':[block('overnight','23:30阅读至次日00:30'),block('old','阅读60分钟')],'current':[block('today','阅读30分钟')]}
                return Reply({'results':children[parent],'has_more':False})
            self.assertIn('已有项目名称',kwargs['json']['contents'][0]['parts'][0]['text'])
            return Reply({'candidates':[{'content':{'parts':[{'text':json.dumps({'items':[entry,old,today]})}]}}]})
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);path=root/'mock.sqlite3';db=Store(path)
            db.sync({'batch_id':'synthetic','base_revision':0,'state':state()})
            before=db.state()
            rulepath=root/'rules.json';rulepath.write_text(json.dumps(rules()))
            env={'TIMEMAP_DAILY_V12':'1','TIMEMAP_ALLOW_MODEL':'1','TIMEMAP_DB':str(path),'TIMEMAP_RULES':str(rulepath),'TIMEMAP_NOTION_PAGES':'{"2026":"page"}','NOTION_TOKEN':'synthetic-notion-token','GEMINI_API_KEY':'synthetic-model-key','GEMINI_MODEL':'synthetic-model','LLM_PROVIDER':'gemini'}
            http=FakeHTTP(handler);writes=[]
            with patch('requests.request',side_effect=AssertionError('no external requests')):
                result,text=prepare_legacy_daily('2026-10-28',420,lambda day,items,sleep,**kw:(writes.append(items) or ('snapshot','summary')),env,http,lambda:['测试项目'])
            self.assertEqual([a['duration_min'] for a in writes[0]],[30,30])
            self.assertEqual(sum(c[0]=='POST' for c in http.calls),1)
            self.assertEqual(db.state(),before)
            with db.connect() as conn:
                self.assertEqual(conn.execute('SELECT count(*) FROM reports').fetchone()[0],0)
                self.assertEqual(conn.execute('SELECT count(*) FROM deliveries').fetchone()[0],0)
            self.assertIn('【地点效率】',text)
