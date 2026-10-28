"""SQLite transactions, immutable reports and idempotent single-device snapshots."""
import hashlib
import json
import sqlite3
from contextlib import contextmanager
from .core import validate_state
import time
import secrets
from pathlib import Path

def encode(value): return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'))

class Conflict(ValueError): pass

class Store:
    def __init__(self,path):
        self.path=str(path)
        with sqlite3.connect(self.path) as probe:
            version=probe.execute('PRAGMA user_version').fetchone()[0]
            if version>2:raise ValueError('数据库版本比应用新，拒绝启动')
            existing=probe.execute("SELECT 1 FROM sqlite_master WHERE name='state'").fetchone()
            if version<2 and existing:
                backup=Path(self.path+'.pre-v2.sqlite3')
                if not backup.exists():
                    dest=sqlite3.connect(str(backup))
                    try:probe.backup(dest)
                    finally:dest.close()
            probe.execute('PRAGMA journal_mode=WAL')
        with self.connect() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS state(id INTEGER PRIMARY KEY CHECK(id=1), revision INTEGER NOT NULL, payload TEXT NOT NULL);
            INSERT OR IGNORE INTO state VALUES(1,0,'{"schemaVersion":1,"places":[],"segments":[]}');
            CREATE TABLE IF NOT EXISTS batches(id TEXT PRIMARY KEY, digest TEXT NOT NULL, revision INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS changes(revision INTEGER PRIMARY KEY, payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS tombstones(id TEXT PRIMARY KEY, revision INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS reports(id TEXT PRIMARY KEY, request_digest TEXT NOT NULL, payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS settlements(date TEXT PRIMARY KEY, revision INTEGER NOT NULL, payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY, digest TEXT NOT NULL, request TEXT NOT NULL, status TEXT NOT NULL, lease TEXT, expires REAL, attempts INTEGER NOT NULL DEFAULT 0, error TEXT);
            CREATE TABLE IF NOT EXISTS credentials(digest TEXT PRIMARY KEY, revoked INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS delta_events(seq INTEGER PRIMARY KEY AUTOINCREMENT, revision INTEGER NOT NULL, payload TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS delta_revision ON delta_events(revision,seq);
            CREATE INDEX IF NOT EXISTS jobs_ready ON jobs(status,expires);
            CREATE TABLE IF NOT EXISTS deliveries(id TEXT PRIMARY KEY, report_id TEXT NOT NULL, payload TEXT NOT NULL, status TEXT NOT NULL, business_code INTEGER);
            CREATE TABLE IF NOT EXISTS staging(id TEXT NOT NULL, part INTEGER NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(id,part));
            CREATE TABLE IF NOT EXISTS staging_commits(id TEXT PRIMARY KEY, parts INTEGER, base INTEGER, restore INTEGER, revision INTEGER);
            CREATE TABLE IF NOT EXISTS sync_floor(id INTEGER PRIMARY KEY, revision INTEGER);
            INSERT OR IGNORE INTO sync_floor VALUES(1,0);
            CREATE TABLE IF NOT EXISTS quotas(bucket TEXT NOT NULL, window INTEGER NOT NULL, count INTEGER NOT NULL, PRIMARY KEY(bucket,window));

            ''')
            if version<2:
                old={'schemaVersion':1,'places':[],'segments':[]}
                for rev,payload in db.execute('SELECT revision,payload FROM changes ORDER BY revision').fetchall():
                    new=json.loads(payload)
                    self._delta(db,rev,old,new);old=new
                db.execute('DELETE FROM changes')
                db.execute('PRAGMA user_version=2')
    @staticmethod
    def _delta(db,revision,old,new):
        events=[]
        for table in ('places','segments'):
            a={v['id']:v for v in old[table]};b={v['id']:v for v in new[table]}
            events.extend({'table':table,'action':'delete','id':sid} for sid in a.keys()-b.keys())
            events.extend({'table':table,'action':'upsert','id':sid,'value':v} for sid,v in b.items() if a.get(sid)!=v)
        for op in events or [None]:db.execute('INSERT INTO delta_events(revision,payload) VALUES(?,?)',(revision,encode(op)))
    @contextmanager
    def connect(self):
        db=sqlite3.connect(self.path,timeout=10)
        try:
            db.execute('PRAGMA busy_timeout=10000')
            db.execute('PRAGMA synchronous=FULL')
            with db: yield db
        finally: db.close()
    def state(self):
        with self.connect() as db:
            rev,payload=db.execute('SELECT revision,payload FROM state WHERE id=1').fetchone()
            return rev,json.loads(payload)
    def sync(self,batch):
        if not isinstance(batch.get('batch_id'),str) or not 1<=len(batch['batch_id'])<=128: raise ValueError('无效批次ID')
        digest=hashlib.sha256(encode(batch).encode()).hexdigest()
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            previous=db.execute('SELECT digest,revision FROM batches WHERE id=?',(batch['batch_id'],)).fetchone()
            if previous:
                if previous[0]!=digest: raise Conflict('同批次ID内容改变')
                return {'revision':previous[1]}
            rev,payload=db.execute('SELECT revision,payload FROM state WHERE id=1').fetchone()
            if type(batch['base_revision']) is not int or batch['base_revision']!=rev: raise Conflict('云端版本已改变')
            old=json.loads(payload)
            new=batch.get('state')
            if new is None:
                new=json.loads(payload)
                maps={table:{v['id']:v for v in new[table]} for table in ('places','segments')}
                for op in batch['operations']:
                    if not isinstance(op,dict):raise ValueError('无效增量操作')
                    table=op.get('table');action=op.get('action');sid=op.get('id')
                    if table not in maps or action not in ('upsert','delete') or not isinstance(sid,str):raise ValueError('无效增量操作')
                    maps[table].pop(sid,None)
                    if action=='upsert':
                        value=op.get('value')
                        if not isinstance(value,dict) or value.get('id')!=sid:raise ValueError('记录ID不一致')
                        maps[table][sid]=value
                new={**new,**{table:list(values.values()) for table,values in maps.items()}}
                new['segments'].sort(key=lambda v:v['start'])
            validate_state(new)
            rev+=1
            old_ids={s['id'] for s in old['segments']};new_ids={s['id'] for s in new['segments']}
            for sid in new_ids-old_ids:
                if db.execute('SELECT 1 FROM tombstones WHERE id=?',(sid,)).fetchone() and not batch.get('restore_deleted',False): raise Conflict('已删除记录不能通过旧备份复活；请显式确认恢复')
            for sid in old_ids-new_ids: db.execute('INSERT OR REPLACE INTO tombstones VALUES(?,?)',(sid,rev))
            db.execute('UPDATE state SET revision=?,payload=? WHERE id=1',(rev,encode(new)))
            self._delta(db,rev,old,new)
            db.execute('INSERT INTO batches VALUES(?,?,?)',(batch['batch_id'],digest,rev))
            db.execute('DELETE FROM settlements') # correctness first: invalidate all derived days
            return {'revision':rev}
    def report(self,rid):
        with self.connect() as db:
            row=db.execute('SELECT request_digest,payload FROM reports WHERE id=?',(rid,)).fetchone()
            return (row[0],json.loads(row[1])) if row else None
    def save_report(self,rid,digest,result):
        with self.connect() as db:
            db.execute('INSERT OR IGNORE INTO reports VALUES(?,?,?)',(rid,digest,encode(result)))
        saved=self.report(rid)
        if saved[0]!=digest: raise Conflict('报告请求ID冲突')
        return saved[1]
    def backup(self,target):
        with self.connect() as source:
            dest=sqlite3.connect(str(target))
            try: source.backup(dest)
            finally: dest.close()

    def changes(self,cursor,limit=50,target=None,after=0):
        if cursor<0 or not 1<=limit<=1000 or after<0:raise ValueError('分页参数无效')
        with self.connect() as db:
            db.execute('BEGIN')
            current=db.execute('SELECT revision FROM state WHERE id=1').fetchone()[0]
            floor=db.execute('SELECT revision FROM sync_floor WHERE id=1').fetchone()[0]
            if cursor and cursor<floor:raise Conflict('snapshot_required:历史已归档，请重新全量恢复')
            target=current if target is None else target
            if not cursor<=target<=current or target<floor:raise Conflict('游标或目标版本无效；请重新获取检查点')
            rows=db.execute('SELECT seq,revision,payload FROM delta_events WHERE revision>? AND revision<=? AND seq>? ORDER BY seq LIMIT ?',(cursor,target,after,limit)).fetchall()
            items=[];budget=0;last=after
            for seq,rev,payload in rows:
                if budget+len(payload.encode())>256000 and items:break
                budget+=len(payload.encode());last=seq
                if not items or items[-1]['revision']!=rev:items.append({'revision':rev,'operations':[]})
                op=json.loads(payload)
                if op:items[-1]['operations'].append(op)
            more=db.execute('SELECT revision FROM delta_events WHERE revision>? AND revision<=? AND seq>? ORDER BY seq LIMIT 1',(cursor,target,last)).fetchone()
            next_cursor=(items[-1]['revision']-1 if more and items and more[0]==items[-1]['revision'] else items[-1]['revision']) if items else cursor
            if not more:next_cursor=target
            return {'items':items,'next_cursor':next_cursor,'after':last,'revision':target,'has_more':bool(more)}

    def stage(self,sid,part,operations):
        if not isinstance(sid,str) or not 1<=len(sid)<=128 or not 0<=part<1000 or not isinstance(operations,list) or len(operations)>500:raise ValueError('分批参数无效')
        payload=encode(operations)
        if len(payload.encode())>256000:raise ValueError('分批大小超限')
        with self.connect() as db:
            if db.execute('SELECT 1 FROM staging_commits WHERE id=?',(sid,)).fetchone():return {'part':part}
            if db.execute('SELECT count(*) FROM staging').fetchone()[0]>=1000 and not db.execute('SELECT 1 FROM staging WHERE id=? AND part=?',(sid,part)).fetchone():raise ValueError('暂存空间已满，请管理员检查未完成上传')
            row=db.execute('SELECT payload FROM staging WHERE id=? AND part=?',(sid,part)).fetchone()
            if row and row[0]!=payload:raise Conflict('同一分批内容不能改变')
            db.execute('INSERT OR IGNORE INTO staging VALUES(?,?,?)',(sid,part,payload))
        return {'part':part}

    def commit_stage(self,sid,parts,base_revision,restore_deleted=False):
        if not 1<=parts<=1000:raise ValueError('分批数量无效')
        with self.connect() as db:
            old=db.execute('SELECT parts,base,restore,revision FROM staging_commits WHERE id=?',(sid,)).fetchone()
            if old:
                if old[:3]!=(parts,base_revision,int(restore_deleted)):raise Conflict('提交元数据冲突')
                return {'revision':old[3]}
            rows=db.execute('SELECT part,payload FROM staging WHERE id=? ORDER BY part',(sid,)).fetchall()
        if [r[0] for r in rows]!=list(range(parts)):raise Conflict('分批不完整')
        operations=[op for _,payload in rows for op in json.loads(payload)]
        result=self.sync({'batch_id':sid,'base_revision':base_revision,'operations':operations,'restore_deleted':restore_deleted})
        with self.connect() as db:
            db.execute('INSERT OR IGNORE INTO staging_commits VALUES(?,?,?,?,?)',(sid,parts,base_revision,int(restore_deleted),result['revision']))
            db.execute('DELETE FROM staging WHERE id=?',(sid,))
        return result

    def quota(self,bucket,maximum,seconds=60,now=None):
        window=int(time.time() if now is None else now)//seconds
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('DELETE FROM quotas WHERE window<? AND bucket=?',(window-2,bucket))
            row=db.execute('SELECT count FROM quotas WHERE bucket=? AND window=?',(bucket,window)).fetchone()
            if row and row[0]>=maximum:return False
            db.execute('INSERT INTO quotas VALUES(?,?,1) ON CONFLICT(bucket,window) DO UPDATE SET count=count+1',(bucket,window))
            return True

    def renew(self,rid,lease,lease_seconds=60):
        with self.connect() as db:
            count=db.execute("UPDATE jobs SET expires=? WHERE id=? AND lease=? AND status='running'",(time.time()+lease_seconds,rid,lease)).rowcount
            if not count:raise Conflict('租约已失效')

    def enqueue(self,rid,digest,request):
        with self.connect() as db:
            db.execute('INSERT OR IGNORE INTO jobs(id,digest,request,status) VALUES(?,?,?,?)',(rid,digest,encode(request),'queued'))
            row=db.execute('SELECT digest,status,error FROM jobs WHERE id=?',(rid,)).fetchone()
            if row[0]!=digest: raise Conflict('请求ID冲突')
            return {'id':rid,'status':row[1],'error':row[2],'mock':True}

    def claim(self,now=None,lease_seconds=60):
        now=time.time() if now is None else now
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute("UPDATE jobs SET status='failed',error='attempt_limit' WHERE attempts>=3 AND (status='queued' OR (status='running' AND expires<?))",(now,))
            row=db.execute("SELECT id,request,digest FROM jobs WHERE status='queued' OR (status='running' AND expires<?) ORDER BY rowid LIMIT 1",(now,)).fetchone()
            if row is None:return None
            lease=secrets.token_hex(16)
            db.execute("UPDATE jobs SET status='running',lease=?,expires=?,attempts=attempts+1 WHERE id=?",(lease,now+lease_seconds,row[0]))
            return row[0],json.loads(row[1]),row[2],lease

    def complete(self,rid,digest,lease,result=None,error=None):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT lease,status FROM jobs WHERE id=?',(rid,)).fetchone()
            if row!=(lease,'running'):raise Conflict('任务租约失效')
            if result is not None:
                db.execute('INSERT OR IGNORE INTO reports VALUES(?,?,?)',(rid,digest,encode(result)))
                if result.get('delivery')=='queued':
                    from .report_delivery import render_day,split_text
                    for day in result['days']:
                        for index,text in enumerate(split_text(render_day(result,day))):
                            key=hashlib.sha256((rid+day['date']+str(index)).encode()).hexdigest()
                            db.execute("INSERT OR IGNORE INTO deliveries VALUES(?,?,?,'prepared',NULL)",(key,rid,text))
            db.execute('UPDATE jobs SET status=?,error=?,lease=NULL WHERE id=?',('failed' if error else 'succeeded',error,rid))

    def job(self,rid):
        with self.connect() as db:
            row=db.execute('SELECT status,error,attempts FROM jobs WHERE id=?',(rid,)).fetchone()
            return {'id':rid,'status':row[0],'error':row[1],'attempts':row[2],'mock':True} if row else None

    def history(self,offset=0,limit=20):
        if offset<0 or not 1<=limit<=100:raise ValueError('分页参数无效')
        with self.connect() as db:
            return [json.loads(r[0]) for r in db.execute('SELECT payload FROM reports ORDER BY rowid DESC LIMIT ? OFFSET ?',(limit,offset))]

    def initialize_token(self,token):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if not db.execute('SELECT 1 FROM credentials LIMIT 1').fetchone():db.execute('INSERT INTO credentials(digest) VALUES(?)',(hashlib.sha256(token.encode()).hexdigest(),))
    def authorized(self,token):
        with self.connect() as db:
            return db.execute('SELECT 1 FROM credentials WHERE digest=? AND revoked=0',(hashlib.sha256(token.encode()).hexdigest(),)).fetchone() is not None
    def rotate(self,old):
        new=secrets.token_urlsafe(32)
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            count=db.execute('UPDATE credentials SET revoked=1 WHERE digest=? AND revoked=0',(hashlib.sha256(old.encode()).hexdigest(),)).rowcount
            if not count:raise Conflict('旧凭据已撤销')
            db.execute('INSERT INTO credentials(digest) VALUES(?)',(hashlib.sha256(new.encode()).hexdigest(),))
        return new

    def compact(self):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            revision,payload=db.execute('SELECT revision,payload FROM state WHERE id=1').fetchone()
            db.execute('DELETE FROM delta_events')
            self._delta(db,revision,{'places':[],'segments':[]},json.loads(payload))
            db.execute('UPDATE sync_floor SET revision=? WHERE id=1',(revision,))
        # Tombstones, idempotency receipts, reports and credentials are retained.
