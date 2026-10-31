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
        if len(result['days']) > 1:lines.append(day['date'])
        for p in day['locations']:
            ratio='无法准确确定' if p['utilization'] is None else f'{p["utilization"]:.1%}'
            lines.append(f'{p["name"]}：停留{p["stay_ms"]/60000:g}分钟，已归属有效{p["effective_ms"]/60000:g}分钟，利用率{ratio}')
            for activity in p['activities']:
                minutes=(activity['matched_end']-activity['matched_start'])/60000 if 'matched_start' in activity else activity['duration_min']
                label={'effective':'有效','buffer':'缓冲','ignored':'未计入'}[activity['time_type']]
                lines.append(f'  {activity["event_name"]}：{minutes:g}分钟（{label}）')
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
    def __init__(self,source,extractor,location_source,rules,sender,legacy_sink=None,legacy_complete=None,place_aliases=None):
        self.source=source;self.extractor=extractor;self.location_source=location_source
        self.rules=rules;self.sender=sender;self.legacy_sink=legacy_sink;self.legacy_complete=legacy_complete;self.place_aliases=place_aliases
    def analyze(self,start,end,now,allow_location_failure=False):
        raw=deepcopy(self.source(start,end)) # source must read authoritative original schedule
        rules=deepcopy(self.rules)
        activities=self.extractor(raw,rules) # exactly once; never separate AI calls per report section
        activities=[{**a,'time_type':rules['categories'].get(a['category'],'ignored')} for a in activities]
        try:result=analyze(self.location_source(),activities,rules,start,end,now,place_aliases=self.place_aliases)
        except (OSError,TimeoutError) as exc:
            if not allow_location_failure:raise
            result={'days':[],'transit_ms':0,'locations':[],'warnings':['地点章节暂不可用：'+type(exc).__name__],'location_unavailable':True}
        result['source_digest']=hashlib.sha256(encode(raw).encode()).hexdigest()
        return activities,result
    def manual(self,start,end,now):
        _,result=self.analyze(start,end,now)
        from .report_delivery import render_day
        text='\n\n'.join(render_day(result,day) for day in result['days'])
        return result,send_parts(text,self.sender)
    def prepare_daily(self,date,start,end,now,sleep_minutes=420):
        from .daily import day_bounds, daily_activities
        if (start,end) != day_bounds(date):raise ValueError("日报必须为北京时间完整自然日")
        activities,result=self.analyze(start,end,now,allow_location_failure=True)
        # Same classification object drives old summary and new location chapter.
        projected,warnings=daily_activities(activities,date)
        result["warnings"].extend(warnings)
        items=normalize_items(projected)
        snapshot=build_activity_snapshot(date,items);summary=build_summary(date,items,sleep_minutes)
        if self.legacy_complete:
            snapshot,summary=self.legacy_complete(date,items,sleep_minutes,warnings=warnings)
        elif self.legacy_sink:self.legacy_sink(date,items,snapshot,summary)
        text=build_feishu_report(date,snapshot,summary)+'\n\n'+render_location(result)
        if warnings and not self.legacy_complete:text+='\n'+'\n'.join(warnings)
        return result,text
    def daily(self,date,start,end,now,sleep_minutes=420):
        result,text=self.prepare_daily(date,start,end,now,sleep_minutes)
        return result,send_parts(text,self.sender)
