#!/usr/bin/env python3
"""Operator-run only after approval. Synthetic writes to an EMPTY test DB only."""
import json
from pathlib import Path
import time
import urllib.request
import urllib.error
import argparse

parser=argparse.ArgumentParser()
parser.add_argument('--base-url',default='http://127.0.0.1:18120')
parser.add_argument('--runtime-env',default='/opt/timemap-test/config/runtime.env')
parser.add_argument('--verify-existing',action='store_true')
args=parser.parse_args()
from urllib.parse import urlsplit
url=urlsplit(args.base_url)
if url.scheme!='http' or url.hostname!='127.0.0.1' or url.path or url.query or url.fragment or url.username or url.password:
    raise SystemExit('Only the local loopback HTTP test endpoint is allowed.')
values = dict(line.split('=',1) for line in Path(args.runtime_env).read_text().splitlines() if line and not line.startswith('#'))
if values.get('TIMEMAP_ALLOW_MODEL') != '0' or values.get('TIMEMAP_ALLOW_SEND') != '0':
    raise SystemExit('Model/send flags must remain disabled for this test.')
opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
def request(path, body=None, authenticated=True):
    headers={'Content-Type':'application/json'}
    if authenticated:
        headers['Authorization']='Bearer '+values['TIMEMAP_DEVICE_TOKEN']
    req=urllib.request.Request(args.base_url+path, headers=headers, data=None if body is None else json.dumps(body).encode())
    return json.load(opener.open(req,timeout=5))

def require(value, message):
    if not value: raise SystemExit(message)

require(request('/health',authenticated=False)=={'status':'ok','mode':'production','external_calls':False}, 'Production health or disabled flags differ.')
try:
    request('/v1/reports',authenticated=False)
    raise AssertionError('anonymous access unexpectedly allowed')
except urllib.error.HTTPError as exc:
    require(exc.code==401, 'Unexpected authentication status.')
if args.verify_existing:
    result=request('/v1/analysis-jobs/release-smoke-location')
    if not (result.get('status')=='succeeded' and result.get('mock') is False and result.get('delivery')=='not_requested' and result['locations'][0]['stay_ms']==1800000):
        raise SystemExit('Existing synthetic report does not match the expected result.')
    print('EXISTING_REPORT_OK: no sync or analysis task submitted.')
    raise SystemExit(0)
page=request('/v1/sync/changes')
if page['revision'] != 0:
    raise SystemExit('Test database is not empty. No writes performed; do not reset it.')
start=1793235600000
state={'schemaVersion':1,'places':[{'id':'release-test-place','name':'测试地点'}],'segments':[{'id':'release-test-segment','kind':'place','placeId':'release-test-place','placeName':'测试地点','start':start,'end':start+1800000}]}
batch={'batch_id':'release-smoke','base_revision':0,'state':state}
require(request('/v1/sync/batches',batch)['revision']==1, 'Synthetic sync revision mismatch.')
require(request('/v1/sync/batches',batch)['revision']==1, 'Synthetic sync revision mismatch.')
job={'request_id':'release-smoke-location','mode':'location','start':start,'end':start+1800000,'expected_revision':1}
require(request('/v1/analysis-jobs',job)['status']=='queued', 'Synthetic job was not queued.')
for _ in range(40):
    result=request('/v1/analysis-jobs/release-smoke-location')
    if result['status']=='succeeded': break
    if result['status']=='failed': raise SystemExit('Worker failed; inspect bounded logs.')
    time.sleep(.25)
else: raise SystemExit('Worker did not finish within the test window.')
require(result['mock'] is False and result['delivery']=='not_requested', 'Report source or delivery mode differs.')
require(result['locations'][0]['stay_ms']==1800000, 'Synthetic duration mismatch.')
require(len(request('/v1/reports')['items'])==1, 'Unexpected report history.')
print('SERVER_SMOKE_OK: auth, synthetic sync, idempotency, separate worker, 30-minute location report.')
print('Synthetic record retained in test DB; no model/send/Notion writes/long-term statistics.')
