"""Deterministic Beijing time arithmetic on literal evidence tokens extracted by AI."""
import re
from datetime import date,datetime,time,timedelta
from .core import TZ

DIGITS=dict(zip('零〇一二两三四五六七八九',[0,0,1,2,2,3,4,5,6,7,8,9]))
def number(text):
    if re.fullmatch(r'\d+(?:\.\d+)?',text):return float(text)
    if text=='十':return 10
    if '十' in text:
        a,b=text.split('十');return (DIGITS[a] if a else 1)*10+(DIGITS[b] if b else 0)
    if text in DIGITS:return DIGITS[text]
    raise ValueError('无法识别数字')

def duration(text):
    text=text.replace(' ','').replace('个','').lower()
    if text in ('半小时','半h'):return 30
    if text.endswith('小时半'):return number(text[:-3])*60+30
    if text.endswith('半小时'):return (number(text[:-3])+.5)*60
    tokens=list(re.finditer(r'([\d.零〇一二两三四五六七八九十]+)(小时|分钟|分|h|min)',text))
    if not tokens or ''.join(m.group() for m in tokens)!=text:raise ValueError('持续时长不明确')
    value=sum(number(m[1])*(60 if m[2] in ('小时','h') else 1) for m in tokens)
    if not 0<value<=7*1440:raise ValueError('持续时长超限')
    return value

def clock(text,day):
    text=text.replace(' ','').removesuffix('开始')
    text=re.sub(r'^(凌晨|早上|早晨|上午|中午|下午|傍晚|晚上|晚间)[，,、](?=\d)',r'\1',text)
    tomorrow=text.startswith(('次日','第二天'))
    text=re.sub(r'^(次日|第二天)','',text)
    m=re.fullmatch(r'(凌晨|早上|早晨|上午|中午|下午|傍晚|晚上|晚间)?([\d零〇一二两三四五六七八九十]+)(?:[:：]([0-9]{2})|点(?:(半|一刻|三刻)|([\d零〇一二两三四五六七八九十]+)分?)?)',text)
    if not m:raise ValueError('开始或结束时间格式不明确')
    period,hour,minute,fraction,cnminute=m.groups();hour=int(number(hour))
    minute=int(minute) if minute else {'半':30,'一刻':15,'三刻':45}.get(fraction, int(number(cnminute)) if cnminute else 0)
    if not period and ':' not in text and '：' not in text and hour not in (0,24) and hour<13:raise ValueError('缺少上午或下午，不能猜测')
    if period:
        explicit_24 = (':' in text or '：' in text) and 13<=hour<=23
        if explicit_24:
            if period not in ('下午','傍晚','晚上','晚间') or (period in ('晚上','晚间') and hour<18):raise ValueError('时段和小时冲突')
        elif not 0<=hour<=12:raise ValueError('时段和小时冲突')
        if explicit_24:pass
        elif period in ('下午','傍晚','晚上','晚间'):
            if hour in (0,12):raise ValueError('夜间十二点归属不明确，请写凌晨零点及日期')
            hour=hour%12+12
        elif period=='中午':
            if not 11<=hour<=12 and hour not in (1,2):raise ValueError('中午时间不明确')
            if hour in (1,2):hour+=12
        elif period=='凌晨':hour=hour%12
        elif hour==12:raise ValueError('上午或早晨十二点存在歧义')
    d=date.fromisoformat(day)+timedelta(days=tomorrow)
    if hour==24 and minute==0 and not period:d+=timedelta(days=1);hour=0
    if not 0<=hour<24 or not 0<=minute<60:raise ValueError('无效钟点')
    return int(datetime.combine(d,time(hour,minute),TZ).timestamp()*1000)

def normalize_time(day,start_text='',end_text='',duration_text=''):
    date.fromisoformat(day)
    result={'start':None,'end':None,'duration_min':None,'time_precision':'duration_only','time_warnings':[]}
    if duration_text:result['duration_min']=duration(duration_text)
    try:
        start=clock(start_text,day) if start_text else None
        if end_text:
            try:end=clock(end_text,day)
            except ValueError:
                # “早上九点到十点半” shares an explicit period, but never invent a next day.
                period=re.match(r'^(凌晨|早上|早晨|上午|中午|下午|傍晚|晚上|晚间)',start_text)
                if not period or not re.fullmatch(r'[零〇一二两三四五六七八九十\d]+点(?:半|一刻|三刻|[零〇一二两三四五六七八九十\d]+分?)?',end_text):raise
                end=clock(period[1]+end_text,day)
                if start is None or end<=start:raise ValueError('结束时间归属不明确，跨午夜须注明次日')
                result['time_warnings'].append('结束钟点沿用同条记录明确的时段')
        else:end=None
    except ValueError as exc:
        if result['duration_min'] is None:raise
        result.update(time_precision='ambiguous',time_warnings=[str(exc)])
        return result
    if start is not None and end is not None:
        if end<=start:
            result.update(temporal_issue='结束时间不晚于开始；跨午夜须明确次日',time_precision='conflict');return result
        minutes=(end-start)/60000
        if result['duration_min'] is not None and abs(minutes-result['duration_min'])>1e-9:
            result.update(temporal_issue='起止时间与持续时长冲突',time_precision='conflict');return result
        result.update(start=start,end=end,duration_min=minutes,time_precision='explicit_interval')
    elif start is not None and result['duration_min'] is not None:
        result.update(start=start,end=start+round(result['duration_min']*60000),time_precision='start_duration')
    elif result['duration_min'] is None:raise ValueError('缺少可靠时长或完整起止时间')
    return result
