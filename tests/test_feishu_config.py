import tempfile
import unittest
from pathlib import Path
from backend.adapters import feishu_sender_from_env
from backend.report_delivery import prepare,deliver,split_text
from backend.core import analyze
from test_v12 import state,activity,rules,START,END
class FeishuConfig(unittest.TestCase):
    def test_proxy_placeholder_fails_before_network_and_never_leaks(self):
        secret='proxy-placeholder-private'
        with self.assertRaises(ValueError) as cm:feishu_sender_from_env(object(),{'FEISHU_WEBHOOK_URL':secret})
        self.assertNotIn(secret,str(cm.exception));self.assertIn('FEISHU_WEBHOOK_DIRECT_URL',str(cm.exception))
    def test_direct_precedence_and_legacy_compatibility(self):
        url='https://open.feishu.cn/open-apis/bot/v2/hook/test-bot'
        self.assertEqual(feishu_sender_from_env(object(),{'FEISHU_WEBHOOK_DIRECT_URL':url,'FEISHU_WEBHOOK_URL':'placeholder'}).webhook,url)
        self.assertEqual(feishu_sender_from_env(object(),{'FEISHU_WEBHOOK_URL':url}).webhook,url)
    def test_send_once_and_timeout_does_not_retry(self):
        r=analyze(state(),[activity()],rules(),START,END,END)
        with tempfile.TemporaryDirectory() as tmp:
            path=prepare(r,tmp);calls=[]
            def sender(text,key):calls.append(text);return {'http_status':200,'code':0}
            self.assertTrue(all(e['status']=='sent' for e in deliver(path,sender)))
            deliver(path,sender);self.assertEqual(len(calls),1)
        with tempfile.TemporaryDirectory() as tmp:
            path=prepare(r,tmp)
            def timeout(*a):raise TimeoutError('private url')
            self.assertEqual(deliver(path,timeout)[0]['status'],'uncertain')
            with self.assertRaises(ValueError):deliver(path,timeout)
    def test_daily_timeline_clips_midnight(self):
        from backend.report_delivery import render_day
        data=state();data['segments'][0].update(start=END-3600000,end=END+3600000)
        r=analyze(data,[],rules(),START,END+86400000,END+86400000)
        first=render_day(r,r['days'][0]);second=render_day(r,r['days'][1])
        self.assertIn('23:00—24:00',first)
        self.assertNotIn('00:00—01:00',first)
        self.assertIn('00:00—01:00',second)

    def test_business_error_and_splitting(self):
        r=analyze(state(),[activity()],rules(),START,END,END)
        with tempfile.TemporaryDirectory() as tmp:
            path=prepare(r,tmp)
            self.assertEqual(deliver(path,lambda *a:{'http_status':200,'code':19024})[0]['status'],'failed')
        parts=split_text('测试'*100,32)
        self.assertEqual(''.join(parts),'测试'*100);self.assertTrue(all(len(x.encode())<=32 for x in parts))
