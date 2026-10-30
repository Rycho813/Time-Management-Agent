"""Beijing natural-day projection shared by daily summary and legacy writebacks."""
from copy import deepcopy
from datetime import datetime, timedelta
import math
from .core import TZ, integer


def day_bounds(day):
    left = datetime.fromisoformat(day).replace(tzinfo=TZ)
    if left.date().isoformat() != day:
        raise ValueError('日报日期必须为YYYY-MM-DD')
    return int(left.timestamp()*1000), int((left+timedelta(days=1)).timestamp()*1000)


def daily_activities(activities, day):
    start, end = day_bounds(day)
    out, warnings, seen = [], [], set()
    for original in activities:
        a = deepcopy(original)
        aid = a.get('id')
        if not aid or aid in seen:
            warnings.append('日报排除缺少来源ID或重复的活动')
            continue
        seen.add(aid)
        left, right = a.get('start'), a.get('end')
        if a.get('temporal_issue') or a.get('time_precision') in ('ambiguous', 'conflict'):
            if a.get('date') == day:
                warnings.append('日报排除时间冲突或归属不明确的活动：'+a['event_name'])
            continue
        if left is not None or right is not None:
            if not integer(left) or not integer(right) or right <= left:
                raise ValueError('日报活动区间无效')
            if right <= start or left >= end:
                continue
            a.update(start=max(left,start), end=min(right,end))
            a['duration_min'] = (a['end']-a['start'])/60000
            # Completion belongs only to the day containing the final instant.
            if a.get('project_status') == 'closed' and right > end:
                a['project_status'] = 'open'
        elif a.get('date') != day:
            continue  # Previous source day's duration-only records never leak into today.
        minutes = a.get('duration_min')
        if isinstance(minutes,bool) or not isinstance(minutes,(int,float)) or not math.isfinite(minutes) or minutes <= 0:
            raise ValueError('日报活动时长无效')
        # Legacy project/calendar records are minute-based; never silently truncate.
        if minutes != int(minutes):
            warnings.append('日报排除不足整分钟的活动，需确认精度：'+a['event_name'])
            continue
        a.update(date=day, duration_min=int(minutes))
        out.append(a)
    return out, warnings
