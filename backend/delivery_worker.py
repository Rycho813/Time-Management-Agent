"""Explicitly enabled durable sender. Uncertain attempts are never auto-reclaimed."""
import os
import signal
import threading
from .store import Store
from .adapters import Transport,feishu_sender_from_env

def send_next(store,sender):
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        row=db.execute("SELECT d.id,d.payload FROM deliveries d WHERE d.status='prepared' AND NOT EXISTS (SELECT 1 FROM deliveries earlier WHERE earlier.report_id=d.report_id AND earlier.rowid<d.rowid AND earlier.status!='sent') ORDER BY d.rowid LIMIT 1").fetchone()
        if not row:return False
        db.execute("UPDATE deliveries SET status='sending' WHERE id=?",(row[0],))
    try:
        response=sender(row[1],row[0]);code=response.get('code')
        status='sent' if response.get('http_status')==200 and code==0 else 'failed'
    except Exception:status='uncertain';code=None
    with store.connect() as db:db.execute('UPDATE deliveries SET status=?,business_code=? WHERE id=?',(status,code,row[0]))
    return True

def main():
    if os.environ.get('TIMEMAP_ALLOW_SEND')!='1':raise SystemExit('sending_disabled')
    sender=feishu_sender_from_env(Transport(allow_network=True));db=Store(os.environ['TIMEMAP_DB'])
    stop=threading.Event()
    for sig in (signal.SIGTERM,signal.SIGINT):signal.signal(sig,lambda *a:stop.set())
    while not stop.is_set():
        if not send_next(db,sender):stop.wait(2)
if __name__=='__main__':main()
