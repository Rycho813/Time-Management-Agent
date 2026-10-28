"""Pure interval analysis and strict validation; never imports legacy write workflows."""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import math

DAY = 86400000
TZ = ZoneInfo('Asia/Shanghai')

def integer(value):
    return type(value) is int and 0 <= value < 2**53

def validate_range(start, end):
    if not integer(start) or not integer(end) or not 0 < end-start <= 7*DAY:
        raise ValueError('区间必须为正且不超过7天')

def validate_state(state):
    if state.get('schemaVersion') != 1 or not isinstance(state.get('places'), list) or not isinstance(state.get('segments'), list):
        raise ValueError('无效V1状态')
    if len(state['places']) > 10000 or len(state['segments']) > 200000:
        raise ValueError('记录数量超限')
    def text(v, limit=None):
        return isinstance(v, str) and bool(v.strip()) and (limit is None or len(v) <= limit)
    ids = set()
    for p in state['places']:
        if not text(p.get('id')) or p['id'] in ids or not text(p.get('name'),36):
            raise ValueError('无效地点')
        ids.add(p['id'])
    ids = set(); previous = None
    for index,s in enumerate(state['segments']):
        if not text(s.get('id')) or s['id'] in ids or s.get('kind') not in ('place','transit') or not integer(s.get('start')) or 'end' not in s:
            raise ValueError('无效片段')
        if s['end'] is None:
            if index != len(state['segments'])-1: raise ValueError('进行中片段必须在最后')
        elif not integer(s['end']) or s['end'] < s['start']: raise ValueError('无效结束时间')
        if previous and (previous['end'] is None or previous['end'] > s['start']): raise ValueError('片段重叠')
        if s['kind']=='place' and (not text(s.get('placeId')) or not text(s.get('placeName'),36)): raise ValueError('缺少地点')
        previous=s; ids.add(s['id'])

def clip(segments,start,end,now):
    validate_range(start,end)
    result=[]
    for s in segments:
        left=max(start,s['start']); right=min(end, max(now,s['start']) if s['end'] is None else s['end'])
        if right>left: result.append({**s,'displayStart':left,'displayEnd':right,'duration':right-left})
    return result

def day_ranges(start,end):
    cursor=start
    while cursor<end:
        d=datetime.fromtimestamp(cursor/1000,TZ)
        midnight=d.replace(hour=0,minute=0,second=0,microsecond=0)+timedelta(days=1)
        right=min(end,int(midnight.timestamp()*1000))
        yield d.date().isoformat(),cursor,right
        cursor=right

def analyze(state,activities,rules,start,end,now):
    """Activities are structured provider output, not keyword-classified fiction."""
    validate_state(state); validate_range(start,end)
    parts=clip(state['segments'],start,end,now)
    def one(left,right):
        pieces=clip(state['segments'],left,right,now)
        locations={}; warnings=[]; unknown=[]; events=[]; allocated={}
        names={p['id']:p['name'] for p in state['places']}
        for s in pieces:
            if s['kind']=='place':
                p=locations.setdefault(s['placeId'],{'id':s['placeId'],'name':names.get(s['placeId'],s['placeName']),'stay_ms':0,'effective_ms':0,'activities':[],'uncertain':False})
                p['stay_ms']+=s['duration']
        date=datetime.fromtimestamp(left/1000,TZ).date().isoformat()
        full=left==int(datetime.fromisoformat(date).replace(tzinfo=TZ).timestamp()*1000) and right-left==DAY
        seen=set()
        for a in activities:
            if a.get('date') != date and not (a.get('start') is not None and a.get('end') is not None): continue
            aid=a.get('id')
            if not isinstance(aid,str) or not aid: raise ValueError('活动必须有来源ID')
            if aid in seen:
                warnings.append(f'重复活动:{aid}');
                for p in locations.values(): p['uncertain']=True
                continue
            seen.add(aid)
            minutes=a.get('duration_min')
            if not isinstance(minutes,(float,int)) or isinstance(minutes,bool) or not math.isfinite(minutes) or minutes<=0: raise ValueError('活动时长无效')
            classification=rules['categories'].get(a.get('category'),'ignored')
            if classification not in ('effective','buffer','ignored'): raise ValueError('分类规则无效')
            event={**a,'time_type':classification,'rule_version':rules['version']}
            warnings.extend(f'活动{aid}：{warning}' for warning in a.get('time_warnings',[]))
            if a.get('temporal_issue') or a.get('unmatched_place'):
                unknown.append({**event,'reason':a.get('temporal_issue') or '明确地点尚未确认别名，不能按时间改配到其他地点'})
                for p in locations.values():p['uncertain']=True
                continue
            timed=a.get('start') is not None and a.get('end') is not None
            if timed:
                astart,aend=a['start'],a['end']
                if not integer(astart) or not integer(aend) or aend<=astart: raise ValueError('活动区间无效')
                x,y=max(left,astart),min(right,aend)
                if x>=y: continue
                if abs((aend-astart)-minutes*60000)>1000:
                    unknown.append({**event,'reason':'跨度与活动时长不一致，不能精确分摊'})
                    for p in locations.values(): p['uncertain']=True
                    continue
                matches=[(s,max(x,s['displayStart']),min(y,s['displayEnd'])) for s in pieces if s['kind']=='place' and s['displayStart']<y and s['displayEnd']>x]
                if a.get('place_id') and any(s['placeId']!=a['place_id'] for s,_,_ in matches):
                    unknown.append({**event,'reason':'活动地点与时间日志冲突'})
                    for p in locations.values(): p['uncertain']=True
                    continue
                covered=sum(r-l for _,l,r in matches)
                if covered<y-x: unknown.append({**event,'reason':'活动部分时间没有地点记录','duration_ms':y-x-covered})
                for s,l,r in matches:
                    events.append((s['placeId'],l,r,event))
            elif full and a.get('place_id') in locations:
                p=locations[a['place_id']]
                p['activities'].append(event)
                if classification=='effective':
                    p['effective_ms']+=round(minutes*60000)
                    allocated.setdefault(p['id'],[]).append(None)
            else:
                unknown.append({**event,'reason':'缺少地点或部分日缺少精确起止时间'})
                for p in locations.values(): p['uncertain']=True
        for pid,l,r,event in events:
            p=locations[pid];p['activities'].append({**event,'matched_start':l,'matched_end':r})
            if event['time_type']=='effective':
                intervals=allocated.setdefault(pid,[])
                if any(v is not None and max(l,v[0])<min(r,v[1]) for v in intervals):
                    warnings.append(f'有效活动时间重叠:{event["id"]}');p['uncertain']=True
                intervals.append((l,r));p['effective_ms']+=r-l
        for p in locations.values():
            if p['effective_ms']>p['stay_ms']:
                warnings.append(f'有效时间超过停留:{p["name"]}');p['uncertain']=True
            if len(allocated.get(p['id'],[]))>1 and None in allocated[p['id']]:
                # Without intervals, disjointness cannot be verified. Keep full-day totals but disclose.
                warnings.append(f'含仅时长活动，无法校验时间重叠:{p["name"]}')
            p['utilization']=None if p['uncertain'] or not p['stay_ms'] else p['effective_ms']/p['stay_ms']
            p['unconfirmed_ms']=None if p['uncertain'] else p['stay_ms']-p['effective_ms']
        return {'date':date,'locations':list(locations.values()),'unknown':unknown,'warnings':warnings,'transit_ms':sum(s['duration'] for s in pieces if s['kind']=='transit')}
    days=[one(l,r) for _,l,r in day_ranges(start,end)]
    totals={}
    for day in days:
        for p in day['locations']:
            t=totals.setdefault(p['id'],{'id':p['id'],'name':p['name'],'stay_ms':0,'effective_ms':0,'uncertain':False})
            for k in ('stay_ms','effective_ms'): t[k]+=p[k]
            t['uncertain'] |= p['uncertain']
    for p in totals.values(): p['utilization']=None if p['uncertain'] or not p['stay_ms'] else p['effective_ms']/p['stay_ms']
    return {'start':start,'end':end,'as_of':now,'rule_version':rules['version'],'timeline':parts,'days':days,'locations':list(totals.values()),'transit_ms':sum(d['transit_ms'] for d in days),'warnings':['进行中记录以查询时刻裁剪'] if any(s['end'] is None for s in parts) else []}
