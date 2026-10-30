"""Offline regressions: UTC instants, Beijing day cuts and legacy text reuse."""
import unittest
from datetime import datetime
from backend.core import analyze, day_ranges, TZ
from backend.pipeline import Pipeline
from backend.report_delivery import render_day
from time_summary import normalize_items, build_summary, build_activity_snapshot, build_feishu_report
from test_v12 import rules, state, activity, START, END


def utc(text):
    return int(datetime.fromisoformat(text.replace('Z', '+00:00')).timestamp() * 1000)


class BeijingCompatibility(unittest.TestCase):
    def test_utc_instants_split_at_beijing_midnight_and_year_boundary(self):
        self.assertEqual(TZ.key, 'Asia/Shanghai')
        for before, after, left, right in [
            ('2026-10-30', '2026-10-31', '2026-10-30T15:30:00Z', '2026-10-30T16:30:00Z'),
            ('2026-12-31', '2027-01-01', '2026-12-31T15:30:00Z', '2026-12-31T16:30:00Z'),
        ]:
            start, end = utc(left), utc(right)
            ranges = list(day_ranges(start, end))
            self.assertEqual([r[0] for r in ranges], [before, after])
            self.assertEqual([r[2]-r[1] for r in ranges], [1800000, 1800000])
            data = state()
            data['segments'][0].update(start=start, end=end)
            result = analyze(data, [], rules(), start, end, end)
            self.assertEqual([d['locations'][0]['stay_ms'] for d in result['days']], [1800000,1800000])
            text = render_day(result, result['days'][1])
            self.assertIn('00:00—00:30', text)
            self.assertNotIn('00:00:00—', text)
            self.assertNotIn('23:30—', text)
            # A range ending exactly at midnight has no next-day report.
            self.assertEqual(len(list(day_ranges(start, start+1800000))), 1)

    def test_daily_prefix_matches_shared_legacy_formatter_and_one_extraction(self):
        extracted, messages, writes = [], [], []
        def extractor(raw, config):
            extracted.append(raw)
            return [activity()]
        def sender(text, key):
            messages.append(text)
            return {'http_status':200, 'code':0}
        pipeline = Pipeline(lambda *a:['synthetic'], extractor, state, rules(), sender, lambda *a:writes.append(a))
        pipeline.daily('2026-10-28', START, END, END, sleep_minutes=480)
        items = normalize_items([{**activity(), 'time_type':'effective'}])
        snapshot = build_activity_snapshot('2026-10-28', items)
        summary = build_summary('2026-10-28', items, 480)
        prefix = build_feishu_report('2026-10-28', snapshot, summary)
        self.assertTrue(messages[0].startswith(prefix+'\n\n【地点效率】'))
        self.assertEqual(len(extracted), 1)
        self.assertEqual(writes, [('2026-10-28', items, snapshot, summary)])
