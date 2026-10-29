import os,subprocess,json,time,argparse
from pathlib import Path
parser=argparse.ArgumentParser();parser.add_argument('--image',default='timemap-local-hardening:test');args=parser.parse_args()
prefix=['docker','--host=unix:///var/run/docker.sock'];env=dict(os.environ)
for key in ['DOCKER_HOST','DOCKER_CONTEXT','DOCKER_TLS','DOCKER_TLS_VERIFY','DOCKER_CERT_PATH']:env.pop(key,None)
def docker(*args):return subprocess.check_output(prefix+list(args),env=env,text=True,stderr=subprocess.STDOUT).strip()
suffix=__import__('uuid').uuid4().hex[:8];volume='timemap-hardening-local-'+suffix;api='timemap-hardening-api-'+suffix;worker='timemap-hardening-worker-'+suffix;image=args.image
try:
 docker('volume','create',volume)
 docker('run','--rm','--pull','never','--network','none','--user','0:0','-v',volume+':/data',image,'chown','10001:10001','/data')
 common=['--network','none','--read-only','--cap-drop','ALL','--security-opt','no-new-privileges','--memory','384m','--tmpfs','/tmp:size=32m','-v',volume+':/data']
 values={'TIMEMAP_DB':'/data/test.sqlite3','TIMEMAP_RULES':'/app/config/activity_rules.json','TIMEMAP_ORIGINS':'https://timemap.rychoz.com','TIMEMAP_DEVICE_TOKEN':'test-only-random-device-token-1234567890','TIMEMAP_ALLOW_MODEL':'0','TIMEMAP_ALLOW_SEND':'0'}
 for key,value in values.items():common+=['-e',key+'='+value]
 docker('run','-d','--pull','never','--name',api,*common,image)
 check="import urllib.request,json; print(json.load(urllib.request.build_opener(urllib.request.ProxyHandler({})).open('http://127.0.0.1:8100/health')))"
 for _ in range(30):
  try:health=docker('exec',api,'python','-c',check);break
  except subprocess.CalledProcessError:time.sleep(.2)
 else:
  print(docker('logs',api));raise AssertionError('API did not start')
 script="import os; assert os.getuid()==10001\ntry:open('/app/forbidden','w');raise AssertionError('writable root')\nexcept OSError:pass"
 print(docker('exec',api,'python','-c',script))
 worker_common=common.copy();worker_common[worker_common.index('384m')]='256m'
 docker('run','-d','--pull','never','--name',worker,*worker_common,image,'python','-m','backend.worker')
 smoke=(Path(__file__).resolve().parents[2]/'deploy/smoke-test.py').read_text()
 def stage_smoke():
  docker('exec',api,'python','-c',"from pathlib import Path; Path('/tmp/smoke.py').write_text("+repr(smoke)+")")
  docker('exec',api,'python','-c',"import os; from pathlib import Path; Path('/tmp/smoke.env').write_text(chr(10).join(k+'='+os.environ[k] for k in ['TIMEMAP_DEVICE_TOKEN','TIMEMAP_ALLOW_MODEL','TIMEMAP_ALLOW_SEND']))")
 stage_smoke()
 print(docker('exec',api,'python','/tmp/smoke.py','--base-url','http://127.0.0.1:8100','--runtime-env','/tmp/smoke.env'))
 try:
  docker('exec',api,'python','/tmp/smoke.py','--base-url','http://127.0.0.1:8100','--runtime-env','/tmp/smoke.env')
  raise AssertionError('non-empty database was overwritten')
 except subprocess.CalledProcessError as exc:
  assert 'database is not empty' in exc.output
 print('non-empty database refused; separate worker passed')
 docker('exec',api,'python','-m','backend.maintenance','backup','--db','/data/test.sqlite3','--target','/data/backup.sqlite3')
 docker('exec',api,'python','-m','backend.maintenance','restore','--db','/data/backup.sqlite3','--target','/data/restored.sqlite3')
 docker('exec',api,'python','-c',"from backend.store import Store; a=Store('/data/test.sqlite3'); b=Store('/data/restored.sqlite3'); assert a.state()==b.state(); assert a.report('release-smoke-location')==b.report('release-smoke-location')")
 docker('restart',api,worker)
 stage_smoke()
 for _ in range(60):
  try:
   print(docker('exec',api,'python','/tmp/smoke.py','--base-url','http://127.0.0.1:8100','--runtime-env','/tmp/smoke.env','--verify-existing'));break
  except subprocess.CalledProcessError:time.sleep(.2)
 else:raise AssertionError('restart persistence failed')
 print('container backup/restore/restart passed; external networking disabled')
finally:
 for name in [worker,api]:
  try:docker('rm','-f',name)
  except subprocess.CalledProcessError:pass
 try:docker('volume','rm',volume)
 except subprocess.CalledProcessError:pass
