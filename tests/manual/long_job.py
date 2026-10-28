import sys,tempfile,threading,time
from pathlib import Path
sys.path.insert(0,str(__import__('pathlib').Path(__file__).resolve().parents[2]))
from backend.app import create_app
from backend.store import encode
with tempfile.TemporaryDirectory() as tmp:
 def slow(*args):time.sleep(63);return []
 app=create_app(Path(tmp)/'db','x'*40,rules={'version':'test','categories':{}},eager=False,embedded_worker=False,mode='production',activity_provider=slow)
 req={'request_id':'slow','mode':'efficiency','start':1,'end':1000,'expected_revision':0,'allow_stale':False,'send_report':False}
 app.state.store.enqueue('slow','digest',req)
 thread=threading.Thread(target=app.state.run_next);thread.start()
 time.sleep(61)
 assert app.state.store.claim() is None,'lease reclaimed despite heartbeat'
 thread.join(timeout=10)
 assert app.state.store.report('slow') is not None
 print('63-second fake model task: heartbeat prevented duplicate claim and report completed')
