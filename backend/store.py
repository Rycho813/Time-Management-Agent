"""SQLite transactions, immutable reports and idempotent single-device snapshots."""
import hashlib
import json
import sqlite3
from contextlib import contextmanager
from .core import validate_state
import time
import secrets

def encode(value): return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'))

class Conflict(ValueError): pass

class Store:
    def __init__(self,path):
        self.path=str(path)
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
            ''')
    @contextmanager
    def connect(self):
        db=sqlite3.connect(self.path,timeout=10)
        try:
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
                for op in batch['operations']:
                    table=op.get('table'); action=op.get('action'); sid=op.get('id')
                    if table not in ('places','segments') or action not in ('upsert','delete') or not isinstance(sid,str): raise ValueError('无效增量操作')
                    new[table]=[v for v in new[table] if v['id']!=sid]
                    if action=='upsert':
                        value=op['value']
                        if value.get('id')!=sid: raise ValueError('记录ID不一致')
                        new[table].append(value)
                new['segments'].sort(key=lambda v:v['start'])
            validate_state(new)
            rev+=1
            old_ids={s['id'] for s in old['segments']};new_ids={s['id'] for s in new['segments']}
            for sid in new_ids-old_ids:
                if db.execute('SELECT 1 FROM tombstones WHERE id=?',(sid,)).fetchone() and not batch.get('restore_deleted',False): raise Conflict('已删除记录不能通过旧备份复活；请显式确认恢复')
            for sid in old_ids-new_ids: db.execute('INSERT OR REPLACE INTO tombstones VALUES(?,?)',(sid,rev))
            db.execute('UPDATE state SET revision=?,payload=? WHERE id=1',(rev,encode(new)))
            db.execute('INSERT INTO changes VALUES(?,?)',(rev,encode(new)))
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

    def changes(self,cursor,limit=50):
        if cursor<0 or not 1<=limit<=100: raise ValueError('分页参数无效')
        with self.connect() as db:
            current=db.execute('SELECT revision FROM state WHERE id=1').fetchone()[0]
            if cursor>current: raise Conflict('游标超出版本')
            rows=db.execute('SELECT revision,payload FROM changes WHERE revision>? ORDER BY revision LIMIT ?',(cursor,limit)).fetchall()
            previous=db.execute('SELECT payload FROM changes WHERE revision=?',(cursor,)).fetchone() if cursor else None
            old=json.loads(previous[0]) if previous else {'schemaVersion':1,'places':[],'segments':[]}
            items=[]
            for revision,payload in rows:
                new=json.loads(payload);ops=[]
                for table in ('places','segments'):
                    a={v['id']:v for v in old[table]};b={v['id']:v for v in new[table]}
                    ops += [{'table':table,'action':'delete','id':sid} for sid in a.keys()-b.keys()]
                    ops += [{'table':table,'action':'upsert','id':sid,'value':v} for sid,v in b.items() if a.get(sid)!=v]
                items.append({'revision':revision,'operations':ops});old=new
            next_cursor=items[-1]['revision'] if items else cursor
            return {'items':items,'next_cursor':next_cursor,'revision':current,'has_more':next_cursor<current}

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
            if result is not None:db.execute('INSERT OR IGNORE INTO reports VALUES(?,?,?)',(rid,digest,encode(result)))
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
            if not db.execute('SELECT 1 FROM credentials LIMIT 1').fetchone():db.execute('INSERT INTO credentials(digest) VALUES(?)',(hashlib.sha256(token.encode()).hexdigest(),))
    def authorized(self,token):
        with self.connect() as db:
            return db.execute('SELECT 1 FROM credentials WHERE digest=? AND revoked=0',(hashlib.sha256(token.encode()).hexdigest(),)).fetchone() is not None
    def rotate(self,old):
        new=secrets.token_urlsafe(32)
        with self.connect() as db:
            db.execute('UPDATE credentials SET revoked=1 WHERE digest=?',(hashlib.sha256(old.encode()).hexdigest(),))
            db.execute('INSERT INTO credentials(digest) VALUES(?)',(hashlib.sha256(new.encode()).hexdigest(),))
        return new
