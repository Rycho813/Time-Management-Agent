"""SQLite transactions, immutable reports and idempotent single-device snapshots."""
import hashlib
import json
import sqlite3
from contextlib import contextmanager
from .core import validate_state

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
        validate_state(batch['state'])
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
            old=json.loads(payload);new=batch['state']; rev+=1
            old_ids={s['id'] for s in old['segments']};new_ids={s['id'] for s in new['segments']}
            for sid in new_ids-old_ids:
                if db.execute('SELECT 1 FROM tombstones WHERE id=?',(sid,)).fetchone(): raise Conflict('已删除记录不能通过旧备份复活')
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
