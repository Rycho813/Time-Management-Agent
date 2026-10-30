import json
import tempfile
import unittest
from pathlib import Path
from backend.report_delivery import prepare, render_day
from backend.core import analyze
from backend.pipeline import Pipeline
from time_summary import build_feishu_report, build_activity_snapshot
from test_v12 import state, activity, rules, START, END


class ConciseReport(unittest.TestCase):
    def test_legacy_title_and_snapshot_body_without_duplicate_date(self):
        snapshot=build_activity_snapshot('2026-01-02',[])
        text=build_feishu_report('2026-01-02',snapshot,'summary')
        self.assertEqual(text.splitlines()[0],'2026年1月2日时间报告')
        self.assertIn('summary',text)
        self.assertNotIn('Activity Snapshot',text)
        self.assertNotIn('2026-01-02',text)
        self.assertNotIn('星期',text)
    def test_independent_report_and_single_part_have_one_plain_title(self):
        r=analyze(state(),[activity()],rules(),START,END,END)
        text=render_day(r,r['days'][0])
        self.assertEqual(text.splitlines()[0],'2026年10月28日时间报告')
        self.assertIn('09:00—12:00',text)
        for redundant in ('2026-10-28','独立测试报告','真实数据联调','规则版本','本报告不更新'):
            self.assertNotIn(redundant,text)
        with tempfile.TemporaryDirectory() as tmp:
            path=prepare(r,tmp)
            entries=json.loads(Path(path).read_text())['entries']
            self.assertEqual(len(entries),1)
            self.assertEqual(entries[0]['text'],text)
            self.assertEqual(entries[0]['status'],'prepared')
    def test_manual_uses_same_title_and_not_raw_millisecond_range(self):
        messages=[]
        pipeline=Pipeline(lambda *a:['synthetic'],lambda *a:[activity()],state,rules(),lambda text,key:(messages.append(text) or {'http_status':200,'code':0}))
        pipeline.manual(START,END,END)
        self.assertEqual(messages[0].splitlines()[0],'2026年10月28日时间报告')
        self.assertNotIn(str(START),messages[0])
