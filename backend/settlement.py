"""Injectable settlement tick for local tests. Does not start production scheduling."""
from datetime import datetime,timedelta
from .core import TZ,clip
from .store import encode

def settle_due(store,now,start_date,close_time='01:30'):
    hour,minute=map(int,close_time.split(':'))
    if not 0<=hour<24 or not 0<=minute<60:raise ValueError('无效结算时间')
    current=datetime.fromtimestamp(now/1000,TZ)
    last=current.date()-timedelta(days=1 if (current.hour,current.minute)>=(hour,minute) else 2)
    day=datetime.strptime(start_date,'%Y-%m-%d').date();results=[]
    if (last-day).days>366:raise ValueError('补算批次最多367日，请分批处理')
    revision,state=store.state()
    while day<=last:
        left=int(datetime.combine(day,datetime.min.time(),TZ).timestamp()*1000);right=left+86400000
        with store.connect() as db:
            old=db.execute('SELECT revision FROM settlements WHERE date=?',(day.isoformat(),)).fetchone()
            if not old or old[0]!=revision:
                parts=clip(state['segments'],left,right,now)
                result={'date':day.isoformat(),'revision':revision,'segments':parts,'recorded_ms':sum(s['duration'] for s in parts),'warnings':['仅结算已收到的数据；空白或未关闭记录不代表完整']}
                db.execute('INSERT OR REPLACE INTO settlements VALUES(?,?,?)',(day.isoformat(),revision,encode(result)))
                results.append(result)
        day+=timedelta(days=1)
    return results
