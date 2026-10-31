"""Opt-in legacy entry adapter. Construction never calls external services."""
import json
import sqlite3
from pathlib import Path
from datetime import datetime
from .core import TZ, validate_state
from .daily import day_bounds
from .adapters import Transport, NotionSource, ActivityExtractor, GeminiModel, OllamaModel, typed_rules
from .pipeline import Pipeline


def configuration(env,day=None):
    if env.get('TIMEMAP_DAILY_V12') != '1' or env.get('TIMEMAP_ALLOW_MODEL') != '1':
        raise ValueError('新日报与模型开关必须显式开启')
    for name in ('NOTION_TOKEN','TIMEMAP_NOTION_PAGES','TIMEMAP_RULES','TIMEMAP_DB'):
        if not env.get(name):raise ValueError('缺少新日报配置：'+name)
    pages={int(y):p for y,p in json.loads(env['TIMEMAP_NOTION_PAGES']).items()}
    if not pages or any(not isinstance(p,str) or not p for p in pages.values()):raise ValueError('年份页面配置无效')
    if day is not None:
        from datetime import date, timedelta
        current=date.fromisoformat(day)
        day_bounds(day)
        if any(y not in pages for y in (current.year,(current-timedelta(days=1)).year)):
            raise ValueError('缺少日报及前一日的年份映射')
    rules=typed_rules(json.loads(Path(env['TIMEMAP_RULES']).read_text()))
    if not rules.get('version'):raise ValueError('缺少规则版本')
    aliases=json.loads(env.get('TIMEMAP_PLACE_ALIASES','{}'))
    if not isinstance(aliases,dict) or any(not isinstance(k,str) or not isinstance(v,str) for k,v in aliases.items()):
        raise ValueError('地点别名配置无效')
    provider=env.get('LLM_PROVIDER','gemini')
    required=('GEMINI_API_KEY','GEMINI_MODEL') if provider=='gemini' else ('OLLAMA_BASE_URL','OLLAMA_MODEL')
    if provider not in ('gemini','ollama') or any(not env.get(n) for n in required):raise ValueError('模型配置不完整')
    path=Path(env['TIMEMAP_DB'])
    if not path.is_absolute() or not path.is_file():raise ValueError('必须使用现有地点数据库绝对路径')
    return pages,rules,provider,path


def prepare_legacy_daily(day,sleep_minutes,complete,environ=None,transport=None,project_names=None):
    import os
    env=os.environ if environ is None else environ
    pages,rules,provider,path=configuration(env,day)
    http=transport if transport is not None else Transport(allow_network=True)
    model=(GeminiModel(env['GEMINI_API_KEY'],env['GEMINI_MODEL'],http) if provider=='gemini'
           else OllamaModel(env['OLLAMA_BASE_URL'],env['OLLAMA_MODEL'],http))
    def locations():
        # Read only: never initialize/migrate the sync DB or modify reports/revisions.
        try:
            with sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True) as db:
                value=json.loads(db.execute('SELECT payload FROM state WHERE id=1').fetchone()[0])
            validate_state(value)
            return value
        except (sqlite3.Error, ValueError, TypeError, IndexError):
            raise OSError('地点数据暂不可用') from None
    extractor=ActivityExtractor(model,json.loads(env.get('TIMEMAP_PLACE_ALIASES','{}')),project_names)
    pipeline=Pipeline(NotionSource(pages,env['NOTION_TOKEN'],http),extractor,locations,rules,None,legacy_complete=complete,place_aliases=json.loads(env.get('TIMEMAP_PLACE_ALIASES','{}')))
    start,end=day_bounds(day)
    return pipeline.prepare_daily(day,start,end,int(datetime.now(TZ).timestamp()*1000),sleep_minutes)
