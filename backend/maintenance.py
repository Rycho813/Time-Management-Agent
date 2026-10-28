"""Operator-only backup and recovery; never starts network or sends reports."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import secrets
from .store import Store
from .settlement import settle_due
from datetime import datetime
from .core import TZ

def verify(path):
    with sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro',uri=True) as db:
        if db.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise ValueError('完整性检查失败')
        if db.execute('PRAGMA user_version').fetchone()[0]!=2:raise ValueError('数据库版本不匹配')
        db.execute('SELECT revision FROM state').fetchone()

def backup(source,target):
    target=Path(target);tmp=Path(str(target)+'.tmp')
    if target.exists() or tmp.exists():raise ValueError('备份目标已存在')
    fd=os.open(tmp,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600);os.close(fd)
    with sqlite3.connect(Path(source).resolve().as_uri()+'?mode=ro',uri=True) as src:
        dest=sqlite3.connect(tmp)
        try:src.backup(dest)
        finally:dest.close()
    os.chmod(tmp,0o600);verify(tmp)
    try:os.link(tmp,target) # atomic no-clobber publication, including concurrent restore
    finally:tmp.unlink(missing_ok=True)
    meta={'schema_version':2,'sha256':hashlib.sha256(target.read_bytes()).hexdigest()}
    Path(str(target)+'.json').write_text(json.dumps(meta));return meta

def restore(source,target):
    source=Path(source)
    if hashlib.sha256(source.read_bytes()).hexdigest()!=json.loads(Path(str(source)+'.json').read_text())['sha256']:raise ValueError('备份校验和不一致')
    verify(source)
    if Path(target).exists():raise ValueError('禁止覆盖恢复目标')
    backup(source,target)

def main():
    parser=argparse.ArgumentParser();parser.add_argument('action',choices=['backup','restore','settle','recover-token','compact'])
    parser.add_argument('--db',required=True);parser.add_argument('--target');parser.add_argument('--since');args=parser.parse_args()
    if args.action in ('backup','restore','compact','recover-token') and not args.target:parser.error('此操作必须指定新目标路径--target')
    if args.action=='settle' and not args.since:parser.error('必须显式指定结算起始日期--since')
    if args.action=='backup':backup(args.db,args.target)
    elif args.action=='restore':restore(args.db,args.target)
    elif args.action=='settle':settle_due(Store(args.db),int(datetime.now(TZ).timestamp()*1000),args.since)
    elif args.action=='compact':
        backup(args.db,args.target);Store(args.db).compact()
    else:
        target=Path(args.target);token=secrets.token_urlsafe(32)
        fd=os.open(target,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(fd,'w') as f:f.write(token)
        with Store(args.db).connect() as db:
            db.execute('BEGIN IMMEDIATE');db.execute('UPDATE credentials SET revoked=1')
            db.execute('INSERT INTO credentials(digest) VALUES(?)',(hashlib.sha256(token.encode()).hexdigest(),))
if __name__=='__main__':main()
