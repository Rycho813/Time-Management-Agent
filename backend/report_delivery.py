"""Prepare readable independent reports; durable local outbox prevents blind resends.
This module never reads Notion, calls models or writes long-term activity statistics.
"""
import hashlib
import json
import os
from pathlib import Path
from datetime import datetime
from .core import TZ,clip
from time_summary import report_title

def duration(ms):
    seconds=int(ms/1000);hours,seconds=divmod(seconds,3600);minutes,seconds=divmod(seconds,60)
    return f'{hours:02}:{minutes:02}:{seconds:02}'
def stamp(ms):return datetime.fromtimestamp(ms/1000,TZ).strftime('%H:%M')

def render_day(result,day):
    lines=[report_title(day['date']), '', '地点与路程：']
    midnight=int(datetime.fromisoformat(day['date']).replace(tzinfo=TZ).timestamp()*1000)
    left=max(midnight,result['start']);right=min(midnight+86400000,result['end'])
    for s in clip(result['timeline'],left,right,result['as_of']):
        end_clock = '24:00' if s['displayEnd'] == midnight+86400000 else stamp(s['displayEnd'])
        lines.append(f'{stamp(s["displayStart"])}—{end_clock}｜{s.get("placeName","路程")}｜{duration(s["duration"])}')
    lines+=['','地点统计与活动：']
    for p in day['locations']:
        ratio='无法准确确定' if p['utilization'] is None else f'{p["utilization"]:.2%}'
        lines.append(f'{p["name"]}：停留{duration(p["stay_ms"])}；已确认有效{duration(p["effective_ms"])}；已确认占比{ratio}')
        for a in p['activities']:
            matched=(a['matched_end']-a['matched_start']) if 'matched_start' in a else a['duration_min']*60000
            label={'effective':'有效','buffer':'缓冲','ignored':'未计入'}[a['time_type']]
            lines.append(f'  {a["event_name"]}｜{label}｜归属{duration(matched)}')
    lines+=['',f'路程合计：{duration(day["transit_ms"])}']
    if day.get('gap_ms',0):lines.append(f'日志空白：{duration(day["gap_ms"])}（未补齐）')
    if day['unknown'] or day['warnings']:lines+=['', '待确认：']
    for a in day['unknown']:lines.append(f'{a["event_name"]}：{a["reason"]}'+(f'，{duration(a["duration_ms"])}' if a.get('duration_ms') else ''))
    lines+=day['warnings']
    return '\n'.join(lines)

def split_text(text,max_bytes=10000):
    if max_bytes<4:raise ValueError('分段上限过小')
    chunks=[];part=''
    for char in text:
        if len((part+char).encode())>max_bytes:chunks.append(part);part=''
        part+=char
    if part:chunks.append(part)
    return chunks

def write_private(path,data):
    path=Path(path);temp=path.with_suffix(path.suffix+'.tmp')
    fd=os.open(temp,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
    with os.fdopen(fd,'w') as f:json.dump(data,f,ensure_ascii=False,indent=2)
    os.replace(temp,path)

def prepare(result,directory):
    directory=Path(directory);directory.mkdir(mode=0o700,parents=True,exist_ok=True)
    manifest=directory/'outbox.json'
    if manifest.exists():raise ValueError('报告目录已存在，不覆盖既有发送状态')
    entries=[]
    for day in result['days']:
        text=render_day(result,day)
        (directory/(day['date']+'.md')).write_text(text)
        parts=split_text(text)
        for index,part in enumerate(parts):
            body=part if len(parts)==1 else part+f'\n（第{index+1}/{len(parts)}段）'
            entries.append({'date':day['date'],'part':index+1,'key':hashlib.sha256(body.encode()).hexdigest(),'text':body,'status':'prepared'})
    write_private(manifest,{'entries':entries})
    return manifest

def deliver(manifest,sender):
    manifest=Path(manifest);lock=manifest.with_suffix('.lock')
    try:fd=os.open(lock,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
    except FileExistsError:raise ValueError('发送锁存在，先核对回执再人工处理') from None
    os.close(fd)
    try:
        data=json.loads(manifest.read_text())
        if any(e['status'] in ('sending','uncertain','failed') for e in data['entries']):raise ValueError('存在失败或不确定发送，不自动重发，请先核对群消息')
        for e in data['entries']:
            if e['status']=='sent':continue
            if hashlib.sha256(e['text'].encode()).hexdigest()!=e['key']:raise ValueError('报告内容校验失败')
            e['status']='sending';write_private(manifest,data)
            try:
                response=sender(e['text'],e['key'])
                e['status']='sent' if response.get('http_status')==200 and response.get('code')==0 else 'failed'
                e['http_status']=response.get('http_status');e['business_code']=response.get('code')
            except Exception:
                e['status']='uncertain' # no exception text: it may contain a credential URL
            write_private(manifest,data)
            if e['status']!='sent':break
        return [{'date':e['date'],'part':e['part'],'status':e['status']} for e in data['entries']]
    finally:lock.unlink(missing_ok=True)
