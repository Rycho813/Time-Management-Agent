"""Synthetic shapes model the user's duration-only habit; no personal source text."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from backend.adapters import ActivityExtractor, typed_rules, make_pipeline
from backend.core import analyze
from backend.daily import daily_activities
from backend.place_matching import resolve_places
from backend.pipeline import Pipeline
from backend.prod import build_app
from backend.report_delivery import render_day
from test_adapters import FakeHTTP, Reply, block
from test_v12 import START, END, state, t

RULES=typed_rules({'version':'test-duration','categories':{}})


def entry(source_id, event='开发示例项目', duration='25分钟', place='', kind='effective'):
    return {'source_id':source_id,'event_name':event,'time_type':kind,'start_text':'','end_text':'','duration_text':duration,'place_text':place,'evidence':event,'project_name':'','project_status':'none'}


def sample_state():
    places=[{'id':'library','name':'示例图书馆'},{'id':'home','name':'家'}]+[{'id':f'shop{i}','name':f'示例{letter}书店'} for i,letter in enumerate('甲乙丙丁')]
    rows=[('library','09:00','10:00'),('library','11:00','14:00'),('shop0','14:00','14:30'),('shop1','14:40','15:10'),('shop2','15:20','15:50'),('shop3','16:00','16:40'),('home','20:00','22:00')]
    names={p['id']:p['name'] for p in places}
    segments=[{'id':str(i),'kind':'place','placeId':pid,'placeName':names[pid],'start':t('2026-10-28T'+left),'end':t('2026-10-28T'+right)} for i,(pid,left,right) in enumerate(rows)]
    return {'schemaVersion':1,'places':places,'segments':segments}


class DurationPlaces(unittest.TestCase):
    def extracted(self):
        records=[
            {'id':'morning','date':'2026-10-28','text':'吃饭休息，花了50分钟','context':'起床时间 > 吃饭休息，花了50分钟','ancestors':['起床时间']},
            {'id':'work1','date':'2026-10-28','text':'上午，在示例图书馆，开发示例项目，进行了25分钟','context':'working > 上午，在示例图书馆，开发示例项目，进行了25分钟'},
            {'id':'todo','date':'2026-10-28','text':'上午，在示例图书馆，整理示例计划，花了1小时20分钟','context':'处理待办事项 > 上午，在示例图书馆，整理示例计划，花了1小时20分钟'},
            {'id':'work2','date':'2026-10-28','text':'上午，在示例图书馆，开发示例项目，进行了1小时','context':'working > 上午，在示例图书馆，开发示例项目，进行了1小时'},
            {'id':'shops','date':'2026-10-28','text':'逛示例甲书店/示例乙书店/示例丙书店/示例丁书店，一共花了2小时20分钟','context':'reading > 逛示例甲书店/示例乙书店/示例丙书店/示例丁书店，一共花了2小时20分钟'},
            {'id':'home','date':'2026-10-28','text':'晚上，在家，开发示例项目，进行了1小时6分钟','context':'working > 晚上，在家，开发示例项目，进行了1小时6分钟'},
        ]
        items=[entry('morning','吃饭休息','50分钟',kind='buffer'),entry('work1'),entry('todo','整理示例计划','1小时20分钟',kind='buffer'),entry('work2',duration='1小时'),entry('shops','逛示例甲书店','2小时20分钟',place='示例甲书店',kind='buffer'),entry('home',duration='1小时6分钟')]
        # The model intentionally omits locations, and under-reports the shop group.
        # Code must use source evidence, not require per-activity timestamps.
        return ActivityExtractor(lambda *_:{'items':items})(records,RULES)

    def test_full_recording_shape_no_clock_required_and_no_group_multiplication(self):
        activities=self.extracted();before=copy.deepcopy(activities);phone=sample_state()
        result=analyze(phone,activities,RULES,START,END,END)
        locations={p['id']:p for p in result['days'][0]['locations']}
        self.assertEqual(locations['library']['stay_ms'],240*60000)
        self.assertEqual(locations['library']['effective_ms'],85*60000)
        self.assertEqual(locations['library']['recorded_activity_ms'],165*60000)
        self.assertEqual(locations['home']['effective_ms'],66*60000)
        self.assertEqual(len(locations['library']['activities']),3)
        self.assertTrue(all('matched_start' not in a and 'matched_end' not in a for a in locations['library']['activities']))
        self.assertTrue(all(p['effective_ms']==0 for pid,p in locations.items() if pid.startswith('shop')))
        group=[a for a in result['days'][0]['unknown'] if a.get('place_match_status')=='group']
        self.assertEqual(len(group),1);self.assertEqual(group[0]['duration_min'],140)
        self.assertEqual(group[0]['place_ids'],['shop0','shop1','shop2','shop3'])
        self.assertFalse(any(a['source_id']=='morning' for a in result['days'][0]['unknown']))
        wake=[a for a in locations['home']['activities'] if a['source_id']=='morning'][0]
        self.assertEqual(wake['attribution'],'user_wake_home_duration')
        self.assertEqual(locations['home']['recorded_activity_ms'],116*60000)
        self.assertEqual(activities,before)
        self.assertEqual(sum(a['duration_min'] for a in daily_activities(activities,'2026-10-28')[0]),421)

    def test_parent_location_and_canonical_names_need_no_manual_aliases(self):
        records=[{'id':'event','date':'2026-10-28','text':'阅读30分钟','context':'示例图书馆 > 阅读30分钟','ancestors':['示例图书馆']}]
        activities=ActivityExtractor(lambda *_:{'items':[entry('event','阅读','30分钟')]})(records,RULES)
        result=analyze(sample_state(),activities,RULES,START,END,END)
        self.assertEqual(result['locations'][0]['effective_ms'],1800000)
        event=result['days'][0]['locations'][0]['activities'][0]
        self.assertEqual(event['place_match_basis'],'parent_place')
        self.assertEqual(event['attribution'],'reported_place_duration')

    def test_explicit_actual_location_wins_over_future_destinations(self):
        activity={**self.extracted()[1],'source_text':'上午，在示例图书馆，规划去示例甲书店/示例乙书店的路线','place_text':'示例甲书店'}
        resolved=resolve_places(sample_state(),[activity])[0]
        self.assertEqual(resolved['place_id'],'library')
        self.assertEqual(resolved['place_match_status'],'matched')

    def test_duplicate_names_and_partial_unknown_group_are_not_guessed(self):
        phone=sample_state();phone['places'][2]['name']='同名店';phone['places'][3]['name']='同名店'
        activity={**self.extracted()[1],'source_text':'下午，在同名店，阅读25分钟','place_text':'同名店'}
        resolved=resolve_places(phone,[activity])[0]
        self.assertEqual(resolved['place_match_status'],'ambiguous')
        activity.update(source_text='逛示例甲书店/未登记书店，一共25分钟',place_text='示例甲书店')
        resolved=resolve_places(sample_state(),[activity])[0]
        self.assertEqual(resolved['place_match_status'],'group')
        self.assertIn('未登记书店',resolved['unresolved_place_names'])
        self.assertNotIn('place_id',resolved)

    def test_all_categories_consume_capacity_without_clipping_or_reclassification(self):
        activities=self.extracted()[1:2]
        activities.append({**activities[0],'id':'buffer-extra','category':'type:buffer','duration_min':240})
        result=analyze(sample_state(),activities,RULES,START,END,END)
        library=result['days'][0]['locations'][0]
        self.assertEqual(library['effective_ms'],25*60000)
        self.assertEqual(library['recorded_activity_ms'],265*60000)
        self.assertIsNone(library['utilization'])
        self.assertTrue(any('活动总时长超过' in warning for warning in result['days'][0]['warnings']))
        self.assertEqual(library['activities'][1]['time_type'],'buffer')

    def test_partial_day_is_not_proportionally_filled_and_other_days_excluded(self):
        activities=self.extracted()[1:2]
        result=analyze(sample_state(),activities,RULES,t('2026-10-28T09:00'),t('2026-10-28T10:00'),END)
        self.assertEqual(result['locations'][0]['effective_ms'],0)
        self.assertIsNone(result['locations'][0]['utilization'])
        self.assertEqual(len(result['days'][0]['unknown']),1)
        result=analyze(sample_state(),activities,RULES,START,END+86400000,END+86400000)
        self.assertEqual(result['days'][1]['unknown'],[])

    def test_group_effective_only_marks_candidate_locations_uncertain(self):
        activities=self.extracted()
        activities[4]['category']='type:effective'
        result=analyze(sample_state(),activities,RULES,START,END,END)
        locations={p['id']:p for p in result['locations']}
        self.assertIsNotNone(locations['library']['utilization'])
        self.assertIsNotNone(locations['home']['utilization'])
        self.assertTrue(all(locations[f'shop{i}']['utilization'] is None for i in range(4)))

    def test_explicit_aliases_historical_names_and_no_substring_guess(self):
        activity={**self.extracted()[1],'source_text':'上午，在阅览馆，阅读25分钟','place_text':''}
        self.assertEqual(resolve_places(sample_state(),[activity],{'阅览馆':'library'})[0]['place_id'],'library')
        phone=sample_state();phone['places'][0]['name']='改名后的图书馆'
        self.assertEqual(resolve_places(phone,self.extracted()[1:2])[0]['place_id'],'library')
        activity.update(source_text='上午，在家具店，阅读25分钟',place_text='家')
        resolved=resolve_places(phone,[activity])[0]
        self.assertNotIn('place_id',resolved)
        self.assertEqual(resolved['unmatched_place'],'家具店')

    def test_daily_and_independent_report_show_activity_durations_and_group_once(self):
        activities=self.extracted();phone=sample_state();writes=[]
        pipeline=Pipeline(lambda *_:['synthetic'],lambda *_:activities,lambda:phone,RULES,None,legacy_sink=lambda *args:writes.append(args))
        result,text=pipeline.prepare_daily('2026-10-28',START,END,END)
        self.assertEqual(len(writes),1)
        self.assertEqual(sum(item['duration_min'] for item in writes[0][1]),421)
        self.assertIn('开发示例项目：25分钟',text)
        self.assertIn('不自动分摊',text)
        self.assertEqual(text.count('地点组合「'),1)
        independent=render_day(result,result['days'][0])
        self.assertIn('09:00—10:00',independent)
        self.assertNotIn('09:00—09:25',independent)
        self.assertIn('已归属有效',independent)

    def test_original_english_duration_tokens_and_invented_clock_rejected(self):
        for token,minutes in [('25min',25),('1h',60),('1h20min',80),('2h20min',140),('1h6min',66)]:
            records=[{'id':'e','date':'2026-10-28','text':f'上午，在示例图书馆，开发示例项目，进行了{token}'}]
            activities=ActivityExtractor(lambda *_:{'items':[entry('e',duration=token)]})(records,RULES)
            self.assertEqual(activities[0]['duration_min'],minutes)
            self.assertIsNone(activities[0]['start']);self.assertIsNone(activities[0]['end'])
            bad={**entry('e',duration=token),'start_text':'09:00'}
            with self.assertRaises(ValueError):ActivityExtractor(lambda *_:{'items':[bad]})(records,RULES)

    def test_one_name_visited_today_can_resolve_and_no_visit_is_not_reassigned(self):
        phone=sample_state();phone['places'].append({'id':'other','name':'示例图书馆'})
        # Same name has another ID but was not visited on this activity's source day.
        resolved=resolve_places(phone,self.extracted()[1:2])[0]
        self.assertEqual(resolved['place_id'],'library')
        activity={**self.extracted()[1],'source_text':'上午，在未到访地点，阅读25分钟','place_text':'未到访地点'}
        phone['places'].append({'id':'absent','name':'未到访地点'})
        result=analyze(phone,[activity],RULES,START,END,END)
        self.assertTrue(any('当天没有对应停留记录' in a['reason'] for a in result['days'][0]['unknown']))
        self.assertEqual(sum(p['effective_ms'] for p in result['locations']),0)

    def test_alias_collision_and_two_names_for_same_place(self):
        activity={**self.extracted()[1],'source_text':'上午，在家，阅读25分钟','place_text':'家'}
        resolved=resolve_places(sample_state(),[activity],{'家':'library'})[0]
        self.assertEqual(resolved['place_match_status'],'ambiguous')
        activity.update(source_text='逛示例图书馆/阅览馆，一共25分钟',place_text='示例图书馆')
        resolved=resolve_places(sample_state(),[activity],{'阅览馆':'library'})[0]
        self.assertEqual(resolved['place_id'],'library')
        self.assertEqual(resolved['place_match_status'],'matched')

    def test_protocol_pipeline_inherits_parent_and_routes_aliases(self):
        def handler(method,url,kwargs):
            if method=='GET':
                parent=url.split('/')[-2]
                children={'page':[block('day','10.28',True)],'day':[block('place','阅览馆',True)],'place':[block('event','阅读30min')]}
                return Reply({'results':children[parent],'has_more':False})
            self.assertEqual(method,'POST');self.assertIn('generativelanguage.googleapis.com',url)
            data={'items':[entry('event','阅读','30min')]}
            return Reply({'candidates':[{'content':{'parts':[{'text':json.dumps(data)}]}}]})
        from backend.adapters import GeminiModel
        http=FakeHTTP(handler)
        pipeline=make_pipeline({2026:'page'},'synthetic',GeminiModel('synthetic','synthetic',http),state,{'version':'synthetic'},None,http,{'阅览馆':'p'})
        result,text=pipeline.prepare_daily('2026-10-28',START,END,END)
        self.assertEqual(result['locations'][0]['effective_ms'],30*60000)
        self.assertEqual(sum(call[0]=='POST' for call in http.calls),1)
        self.assertIn('阅读：30分钟',text)

    def test_negated_or_planned_destination_does_not_become_location(self):
        for text in ('今天不在示例图书馆，开发示例项目25分钟','计划去示例图书馆，开发示例项目25分钟'):
            activity={**self.extracted()[1],'source_text':text,'place_text':'示例图书馆'}
            resolved=resolve_places(sample_state(),[activity])[0]
            self.assertNotIn('place_id',resolved)
            self.assertEqual(resolved['place_match_status'],'unmatched')
        phone=sample_state();phone['places'][0]['name']='和平书店'
        activity={**self.extracted()[1],'source_text':'上午，在和平书店里，阅读25分钟','place_text':''}
        self.assertEqual(resolve_places(phone,[activity])[0]['place_id'],'library')

    def test_coarse_morning_evening_does_not_drop_duration_or_invent_clock(self):
        for period in ('上午','晚上'):
            records=[{'id':'e','date':'2026-10-28','text':f'{period}，在示例图书馆，开发示例项目，进行了25min'}]
            item={**entry('e',duration='25min'),'start_text':period}
            activities=ActivityExtractor(lambda *_:{'items':[item]})(records,RULES)
            self.assertEqual(activities[0]['time_precision'],'duration_only')
            self.assertIsNone(activities[0]['start']);self.assertIsNone(activities[0]['end'])
            projected,_=daily_activities(activities,'2026-10-28')
            self.assertEqual(projected[0]['duration_min'],25)
            result=analyze(sample_state(),activities,RULES,START,END,END)
            self.assertEqual(result['locations'][0]['effective_ms'],25*60000)

    def test_wake_rule_handles_activity_without_requiring_home_in_source(self):
        records=[{'id':'wake','date':'2026-10-28','text':'起床洗漱30min'}]
        activities=ActivityExtractor(lambda *_:{'items':[entry('wake','起床洗漱','30min',kind='buffer')]})(records,RULES)
        resolved=resolve_places(sample_state(),activities)[0]
        self.assertEqual(resolved['place_id'],'home')
        self.assertEqual(resolved['place_match_basis'],'user_wake_home')
        self.assertIsNone(resolved['start']);self.assertIsNone(resolved['end'])
        bad={**entry('wake','起床洗漱','30min',kind='buffer'),'place_text':'家'}
        with self.assertRaises(ValueError):ActivityExtractor(lambda *_:{'items':[bad]})(records,RULES)

    def test_wake_rule_explicit_other_place_and_negation_take_priority(self):
        wake=self.extracted()[0]
        wake.update(source_text='在示例图书馆，吃饭休息50分钟',place_text='')
        self.assertEqual(resolve_places(sample_state(),[wake])[0]['place_id'],'library')
        wake.update(source_text='吃饭休息50分钟',source_ancestors=['示例图书馆','起床时间'])
        self.assertEqual(resolve_places(sample_state(),[wake])[0]['place_id'],'library')
        wake['source_ancestors']=['在未登记旅馆','起床时间']
        resolved=resolve_places(sample_state(),[wake])[0]
        self.assertNotIn('place_id',resolved);self.assertEqual(resolved['unmatched_place'],'未登记旅馆')
        wake.update(source_ancestors=['起床时间'],source_text='今天不在家，吃饭休息50分钟')
        self.assertNotIn('place_id',resolve_places(sample_state(),[wake])[0])

    def test_wake_rule_does_not_generalize_all_meals_or_research_to_home(self):
        for name in ('吃饭休息','研究起床习惯'):
            activity={**self.extracted()[0],'source_ancestors':['日常'],'event_name':name}
            resolved=resolve_places(sample_state(),[activity])[0]
            self.assertNotIn('place_id',resolved)
            self.assertNotEqual(resolved.get('place_match_basis'),'user_wake_home')

    def test_wake_home_requires_unique_mapping_and_observed_home_capacity(self):
        wake=self.extracted()[0];phone=sample_state()
        phone['places'].append({'id':'other-home','name':'家'})
        phone['segments'].insert(0,{'id':'other-home','kind':'place','placeId':'other-home','placeName':'家','start':START,'end':START+3600000})
        resolved=resolve_places(phone,[wake])[0]
        self.assertEqual(resolved['place_match_status'],'ambiguous')
        phone=sample_state();phone['places']=[p for p in phone['places'] if p['id']!='home'];phone['segments']=[s for s in phone['segments'] if s['placeId']!='home']
        self.assertNotIn('place_id',resolve_places(phone,[wake])[0])
        phone=sample_state();phone['segments'][-1]['end']=phone['segments'][-1]['start']+10*60000
        result=analyze(phone,[wake],RULES,START,END,END)
        home=next(p for p in result['days'][0]['locations'] if p['id']=='home')
        self.assertIsNone(home['utilization'])
        self.assertTrue(any('活动总时长超过' in warning for warning in result['days'][0]['warnings']))

    def test_production_factory_passes_aliases_to_analysis(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);rulepath=root/'rules.json';rulepath.write_text(json.dumps(RULES))
            env={'TIMEMAP_DB':str(root/'mock.sqlite3'),'TIMEMAP_DEVICE_TOKEN':'synthetic-device-token-000000000000','TIMEMAP_ORIGINS':'https://example.invalid','TIMEMAP_RULES':str(rulepath),'TIMEMAP_ALLOW_MODEL':'1','TIMEMAP_ALLOW_SEND':'0','NOTION_TOKEN':'synthetic-token','TIMEMAP_NOTION_PAGES':'{"2026":"page"}','GEMINI_API_KEY':'synthetic-key','GEMINI_MODEL':'synthetic-model','TIMEMAP_PLACE_ALIASES':'{"阅览馆":"p"}'}
            activities=[{'id':'a','date':'2026-10-28','event_name':'阅读','category':'type:effective','duration_min':30,'start':None,'end':None,'source_text':'上午，在阅览馆，阅读30分钟','place_text':''}]
            with patch('backend.prod.ActivityProvider',return_value=lambda *_:activities),patch('requests.request',side_effect=AssertionError('no real calls')):
                app=build_app(env);app.state.store.sync({'batch_id':'initial','base_revision':0,'state':state()})
                from backend.app import Request
                result=app.state.execute(Request(request_id='synthetic',mode='efficiency',start=START,end=END,expected_revision=1))
            self.assertEqual(result['locations'][0]['effective_ms'],1800000)
