"""Dependency-injected workflow. No network or legacy write imports.
Real transports must be supplied and authorized separately; tests use fakes only.
"""
from copy import deepcopy
import hashlib
from .core import analyze
from .store import encode
from time_summary import normalize_items, build_summary, build_activity_snapshot, build_feishu_report


def render_location(result):
    from decimal import Decimal, ROUND_HALF_UP
    import re

    def minutes(value):
        value = Decimal(str(value))
        if not value.is_finite() or value < 0:
            raise ValueError("时长必须是非负有限数")
        total = int(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))
        hours, remaining = divmod(total, 60)
        if not hours:
            return f"{remaining}min"
        return f"{hours}h{remaining}min" if remaining else f"{hours}h"

    def duration(milliseconds):
        return minutes(Decimal(str(milliseconds)) / Decimal("60000"))

    def percent(value):
        if value is None:
            return "无法准确确定"
        rounded = (Decimal(str(value)) * 100).quantize(
            Decimal("1"), rounding=ROUND_HALF_UP
        )
        return f"{rounded}%"

    def warning(text):
        match = re.fullmatch(
            r"地点日志存在(\d+(?:\.\d+)?)分钟空白(.*)", text
        )
        if match:
            return f"地点日志存在{minutes(match[1])}空白{match[2]}"
        return text

    def reason(text):
        match = re.fullmatch(
            r"(地点组合「.*」合计)(\d+(?:\.\d+)?)分钟"
            r"(，未记录各地点时长，不自动分摊)",
            text,
        )
        if match:
            return match[1] + minutes(match[2]) + match[3]
        return text

    lines = ["【地点效率】"]
    if result.get("location_unavailable"):
        return "\n".join(
            lines + [warning(w) for w in result["warnings"]]
        )

    for day in result["days"]:
        if len(result["days"]) > 1:
            lines.append(day["date"])
        for place in day["locations"]:
            lines.append(
                f'{place["name"]}：停留{duration(place["stay_ms"])}，'
                f'已归属有效{duration(place["effective_ms"])}，'
                f'利用率{percent(place["utilization"])}'
            )
            for activity in place["activities"]:
                if "matched_start" in activity:
                    elapsed = (
                        Decimal(str(activity["matched_end"]))
                        - Decimal(str(activity["matched_start"]))
                    )
                    displayed = duration(elapsed)
                else:
                    displayed = minutes(activity["duration_min"])
                label = {
                    "effective": "有效",
                    "buffer": "缓冲",
                    "ignored": "未计入",
                }[activity["time_type"]]
                lines.append(
                    f'  {activity["event_name"]}：{displayed}（{label}）'
                )
        lines.extend(warning(w) for w in day["warnings"])
        lines.extend(
            f'待确认：{a["event_name"]}，{reason(a["reason"])}'
            for a in day["unknown"]
        )
    lines.append(f'路程总计：{duration(result["transit_ms"])}')
    return "\n".join(lines)


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
