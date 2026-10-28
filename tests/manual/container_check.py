import os,subprocess,json,time
prefix=['docker','--host=unix:///var/run/docker.sock'];env=dict(os.environ)
for key in ['DOCKER_HOST','DOCKER_CONTEXT','DOCKER_TLS','DOCKER_TLS_VERIFY','DOCKER_CERT_PATH']:env.pop(key,None)
def docker(*args):return subprocess.check_output(prefix+list(args),env=env,text=True,stderr=subprocess.STDOUT).strip()
suffix=__import__('uuid').uuid4().hex[:8];volume='timemap-hardening-local-'+suffix;api='timemap-hardening-api-'+suffix;worker='timemap-hardening-worker-'+suffix;image='timemap-local-hardening:test'
try:
 docker('volume','create',volume)
 docker('run','--rm','--network','none','--user','0:0','-v',volume+':/data',image,'chown','10001:10001','/data')
 common=['--network','none','--read-only','--cap-drop','ALL','--security-opt','no-new-privileges','--memory','384m','--tmpfs','/tmp:size=32m','-v',volume+':/data']
 values={'TIMEMAP_DB':'/data/test.sqlite3','TIMEMAP_RULES':'/app/config/activity_rules.json','TIMEMAP_ORIGINS':'https://timemap.rychoz.com','TIMEMAP_DEVICE_TOKEN':'test-only-random-device-token-1234567890','TIMEMAP_ALLOW_MODEL':'0','TIMEMAP_ALLOW_SEND':'0'}
 for key,value in values.items():common+=['-e',key+'='+value]
 docker('run','-d','--name',api,*common,image)
 check="import urllib.request,json; print(json.load(urllib.request.build_opener(urllib.request.ProxyHandler({})).open('http://127.0.0.1:8100/health')))"
 for _ in range(30):
  try:health=docker('exec',api,'python','-c',check);break
  except subprocess.CalledProcessError:time.sleep(.2)
 else:
  print(docker('logs',api));raise AssertionError('API did not start')
 script="""import urllib.request,json,os
assert os.getuid()==10001
try:open('/app/forbidden','w');raise AssertionError('writable root')
except OSError:pass
def post(path,value):
 req=urllib.request.Request('http://127.0.0.1:8100'+path,data=json.dumps(value).encode(),headers={'Authorization':'Bearer '+os.environ['TIMEMAP_DEVICE_TOKEN'],'Content-Type':'application/json'})
 return json.load(urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req))
post('/v1/sync/batches',{'batch_id':'x','base_revision':0,'state':{'schemaVersion':1,'places':[],'segments':[{'id':'s','kind':'transit','start':100,'end':200}]}})
r=post('/v1/analysis-jobs',{'request_id':'location','mode':'location','start':1,'end':1000,'expected_revision':1})
assert r['status']=='queued'
print('nonroot/read-only/API sync/queue passed')
"""
 print(docker('exec',api,'python','-c',script))
 docker('run','-d','--name',worker,*common,image,'python','-m','backend.worker')
 query="from backend.store import Store; import os; x=Store(os.environ['TIMEMAP_DB']).report('location'); assert x and x[1]['mock'] is False; print('separate worker passed')"
 for _ in range(30):
  try:print(docker('exec',api,'python','-c',query));break
  except subprocess.CalledProcessError:time.sleep(.2)
 else:raise AssertionError('worker did not complete')
 docker('exec',api,'python','-m','backend.maintenance','backup','--db','/data/test.sqlite3','--target','/data/backup.sqlite3')
 docker('exec',api,'python','-m','backend.maintenance','restore','--db','/data/backup.sqlite3','--target','/data/restored.sqlite3')
 print('container backup/restore passed; external networking disabled')
finally:
 for name in [worker,api]:
  try:docker('rm','-f',name)
  except subprocess.CalledProcessError:pass
 try:docker('volume','rm',volume)
 except subprocess.CalledProcessError:pass
