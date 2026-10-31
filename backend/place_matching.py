"""Evidence-based place attribution. Never invents activity start/end times."""
from copy import deepcopy
from datetime import datetime, timedelta
import re
import unicodedata
from zoneinfo import ZoneInfo

VERSION = 'place-duration-v1'
TZ = ZoneInfo('Asia/Shanghai')


def normalize(text):
    return re.sub(r'\s+', '', unicodedata.normalize('NFKC', text)).strip('【】[]（）()，,。;；:：')


def registry(state, aliases):
    names = {}
    for item in state['places']:
        names.setdefault(normalize(item['name']), set()).add(item['id'])
    for item in state['segments']:
        if item['kind'] == 'place':
            names.setdefault(normalize(item['placeName']), set()).add(item['placeId'])
    for name, pid in (aliases or {}).items():
        if not isinstance(name, str) or not normalize(name) or not isinstance(pid, str) or not pid:
            raise ValueError('地点别名配置无效')
        # Keep competing canonical names ambiguous instead of silently overriding them.
        names.setdefault(normalize(name), set()).add(pid)
    return names


def phrase_parts(phrase, names):
    phrase = normalize(phrase)
    if phrase in names:
        return [phrase]
    phrase = re.sub(r'^(?:位于|在|逛|到访|参观)', '', phrase)
    phrase = re.split(r'一共|共计|总计|花了|耗时', phrase, maxsplit=1)[0]
    if phrase in names:
        return [phrase]
    for suffix in ('里面','内','里'):
        if phrase.endswith(suffix) and phrase[:-len(suffix)] in names:
            return [phrase[:-len(suffix)]]
    return [part for part in re.split(r'[/、,，;；]|以及|和|及|与', phrase) if part]


def evidence(activity, names):
    """Prefer an explicit location in the activity over destinations mentioned later."""
    text = activity.get('source_text', '')
    if text:
        explicit = re.search(r'(?:^|[，,；;：:\s])(?:在|位于)([^，,；;。\n]+)', text)
        if explicit:
            return explicit[1], 'source_location'
        visit = re.search(r'(?:^|[，,；;：:\s])(?:逛|到访|参观)([^，,；;。\n]+)', text)
        if visit:
            return visit[1], 'source_place_group'
    if activity.get('place_text'):
        return activity['place_text'], 'extracted_place'
    for heading in reversed(activity.get('source_ancestors', [])):
        parts = phrase_parts(heading, names)
        if normalize(heading) in names or any(part in names for part in parts):
            return heading, 'parent_place'
    return '', ''


def day_visits(state, activity):
    if activity.get('start') is not None and activity.get('end') is not None:
        left, right = activity['start'], activity['end']
        if type(left) is not int or type(right) is not int or left<0 or right<=left:
            return set()
    else:
        raw = activity.get('date')
        if not isinstance(raw, str):
            return set()
        day = datetime.fromisoformat(raw).replace(tzinfo=TZ)
        left = int(day.timestamp()*1000)
        right = int((day+timedelta(days=1)).timestamp()*1000)
    return {row['placeId'] for row in state['segments']
            if row['kind'] == 'place' and row['start'] < right
            and (row['end'] is None or row['end'] > left)}


def resolve_places(state, activities, aliases=None):
    names = registry(state, aliases)
    out = []
    visits = {}
    for original in activities:
        activity = deepcopy(original)
        phrase, basis = evidence(activity, names)
        if not phrase:
            out.append(activity)
            continue
        text = activity.get('source_text','')
        denied = re.search(r'(?:不在|没在|未在|不去|没去|未去)\s*'+re.escape(phrase),text)
        planned = re.search(r'(?:准备|计划|打算|拟|想)(?:前往|去|到)\s*'+re.escape(phrase),text)
        if basis in ('extracted_place','parent_place') and (denied or planned):
            activity.pop('place_id',None)
            activity.update(unmatched_place=phrase,place_match_status='unmatched',place_match_version=VERSION,
                            place_match_reason='原文否定该地点或仅计划前往，不能作为活动发生地')
            out.append(activity)
            continue
        parts = phrase_parts(phrase, names)
        key = activity.get('date') if activity.get('start') is None and activity.get('end') is None else None
        if isinstance(key,str):
            if key not in visits:visits[key]=day_visits(state,activity)
            visited=visits[key]
        else:
            visited=day_visits(state,activity)
        resolved, unknown, ambiguous = set(), [], False
        for part in parts:
            key = normalize(part)
            # Common location suffixes are harmless only if the remaining name is exact.
            if key not in names:
                for suffix in ('里面', '内', '里'):
                    if key.endswith(suffix) and key[:-len(suffix)] in names:
                        key = key[:-len(suffix)]
                        break
            ids = set(names.get(key, ()))
            today = ids & visited
            if today:
                ids = today
            if not ids:
                unknown.append(part)
            else:
                resolved.update(ids)
                ambiguous |= len(ids) > 1
        # Existing explicit aliases supplied to the extractor remain compatible.
        if (not resolved and len(parts) == 1 and basis == 'extracted_place'
                and activity.get('place_id') and not activity.get('unmatched_place')):
            resolved = {activity['place_id']}
            unknown = []
        activity['place_match_version'] = VERSION
        activity['place_match_basis'] = basis
        activity['place_evidence'] = phrase
        activity.pop('place_id', None)
        activity.pop('unmatched_place', None)
        if len(parts) > 1 and (len(resolved)>1 or unknown or ambiguous):
            activity.update(place_match_status='group', place_ids=sorted(resolved),
                            unresolved_place_names=unknown,
                            place_match_reason=f'地点组合「{phrase}」合计{activity.get("duration_min")}分钟，未记录各地点时长，不自动分摊')
        elif ambiguous:
            activity.update(place_match_status='ambiguous', place_ids=sorted(resolved),
                            place_match_reason='地点名称对应多个地点，无法唯一归属')
        elif len(resolved) == 1 and not unknown:
            activity.update(place_id=next(iter(resolved)), place_match_status='matched')
        else:
            activity.update(unmatched_place=phrase, place_match_status='unmatched')
        out.append(activity)
    return out
