import sys,time,tempfile,resource
sys.path.insert(0,str(__import__('pathlib').Path(__file__).resolve().parents[2]))
from backend.store import Store
from pathlib import Path
start=time.perf_counter()
with tempfile.TemporaryDirectory() as tmp:
 db=Store(Path(tmp)/'db')
 for part in range(400):
  ops=[{'table':'segments','action':'upsert','id':str(i),'value':{'id':str(i),'kind':'transit','start':i*2,'end':i*2+1}} for i in range(part*500,(part+1)*500)]
  db.stage('capacity',part,ops)
 assert db.state()[0]==0
 db.commit_stage('capacity',400,0)
 rev,state=db.state();assert rev==1 and len(state['segments'])==200000
 print({'records':200000,'seconds':round(time.perf_counter()-start,2),'peak_rss_MiB':round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024,1),'db_MiB':round(Path(tmp,'db').stat().st_size/1048576,1)})
