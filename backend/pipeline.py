"""Dependency-injected workflow. No network or legacy write imports.
Real transports must be supplied and authorized separately; tests use fakes only.
"""
from copy import deepcopy
import hashlib
from .core import analyze
from .store import encode
from time_summary import normalize_items, build_summary, build_activity_snapshot, build_feishu_report


def render_location(result):
    lines=['【地点效率】']
    if result.get('location_unavailable'):return '\n'.join(lines+result['warnings'])
    for day in result['days']:
        lines.append(day['date'])
        for p in day['locations']:
            ratio='无法准确确定' if p['utilization'] is None else f'{p["utilization"]:.1%}'
            lines.append(f'{p["name"]}：停留{p["stay_ms"]/60000:g}分钟，有效{p["effective_ms"]/60000:g}分钟，利用率{ratio}')
        lines.extend(day['warnings'])
        lines.extend(f'待确认：{a["event_name"]}，{a["reason"]}' for a in day['unknown'])
    lines.append(f'路程总计：{result["transit_ms"]/60000:g}分钟')
    return '\n'.join(lines)


def send_parts(text,sender,max_bytes=12000):
    """Transport receives text and deterministic part key; timeout is uncertain, no blind retry."""
    if max_bytes<4:raise ValueError('分段上限过小')
    parts=[];chunk=''
    for char in text:
        if len((chunk+char).encode())>max_bytes:parts.append(chunk);chunk=''
        chunk+=char
    if chunk:parts.append(chunk)
    outcomes=[]
    for index,part in enumerate(parts):
        key=hashlib.sha256((str(index)+text).encode()).hexdigest()
        try:
            response=sender(part,key)
            ok=response.get('http_status')==200 and response.get('code')==0
            outcomes.append({'key':key,'status':'sent' if ok else 'failed','part':index})
            if not ok:break
        except TimeoutError:
            outcomes.append({'key':key,'status':'uncertain','part':index});break
    return outcomes


class Pipeline:
    def __init__(self,source,extractor,location_source,rules,sender,legacy_sink=None):
        self.source=source;self.extractor=extractor;self.location_source=location_source
        self.rules=rules;self.sender=sender;self.legacy_sink=legacy_sink
    def analyze(self,start,end,now,allow_location_failure=False):
        raw=deepcopy(self.source(start,end)) # source must read authoritative original schedule
        rules=deepcopy(self.rules)
        activities=self.extractor(raw,rules) # exactly once; never separate AI calls per report section
        activities=[{**a,'time_type':rules['categories'].get(a['category'],'ignored')} for a in activities]
        try:result=analyze(self.location_source(),activities,rules,start,end,now)
        except (OSError,TimeoutError) as exc:
            if not allow_location_failure:raise
            result={'days':[],'transit_ms':0,'locations':[],'warnings':['地点章节暂不可用：'+type(exc).__name__],'location_unavailable':True}
        result['source_digest']=hashlib.sha256(encode(raw).encode()).hexdigest()
        return activities,result
    def manual(self,start,end,now):
        _,result=self.analyze(start,end,now)
        text=f'【独立区间分析｜{start}—{end}】\n'+render_location(result)
        return result,send_parts(text,self.sender)
    def daily(self,date,start,end,now,sleep_minutes=420):
        activities,result=self.analyze(start,end,now,allow_location_failure=True)
        # Same classification object drives old summary and new location chapter.
        items=normalize_items(activities)
        snapshot=build_activity_snapshot(date,items);summary=build_summary(date,items,sleep_minutes)
        if self.legacy_sink:self.legacy_sink(date,items,snapshot,summary)
        text=build_feishu_report(date,snapshot,summary)+'\n\n'+render_location(result)
        return result,send_parts(text,self.sender)
