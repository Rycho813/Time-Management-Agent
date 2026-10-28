"""Parse exact exported blocks; never treats pasted location logs as activities."""
import json
import re
from .core import validate_state,validate_range

def supplement(primary,text):
    records={s['id']:s for s in primary['segments']};warnings=[]
    for block in re.findall(r'```json\s*([\s\S]*?)```',text):
        data=json.loads(block)
        if data.get('format')!='time-map-log-v1.2':continue
        validate_range(data['start'],data['end'])
        validate_state({'schemaVersion':1,'places':[],'segments':data['segments']})
        for s in data['segments']:
            if s['id'] in records and records[s['id']]!=s: warnings.append(f'备用冲突:{s["id"]}')
            elif s['id'] not in records:records[s['id']]=s
    candidate={**primary,'segments':sorted(records.values(),key=lambda s:s['start'])}
    validate_state(candidate)
    return candidate,warnings
