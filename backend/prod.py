"""Real service factory: fail closed, no fixture loading, no network on import."""
import json
import os
from pathlib import Path
from .app import create_app
from .adapters import Transport,NotionSource,ActivityExtractor,ActivityProvider,GeminiModel,typed_rules

def build_app(environ=None):
    env=os.environ if environ is None else environ
    if any(not env.get(k) for k in ['TIMEMAP_DB','TIMEMAP_DEVICE_TOKEN','TIMEMAP_ORIGINS','TIMEMAP_RULES']):raise ValueError('缺少必需配置')
    path=Path(env['TIMEMAP_DB'])
    if not path.is_absolute() or not path.parent.is_dir():raise ValueError('数据目录必须显式存在')
    origins=env['TIMEMAP_ORIGINS'].split(',')
    from urllib.parse import urlparse
    for origin in origins:
        u=urlparse(origin)
        if u.scheme!='https' or not u.netloc or u.path or u.query or u.fragment or '*' in origin:raise ValueError('来源必须为精确HTTPS来源')
    rules=typed_rules(json.loads(Path(env['TIMEMAP_RULES']).read_text()))
    if not rules.get('version'):raise ValueError('缺少规则版本')
    enabled=env.get('TIMEMAP_ALLOW_MODEL')=='1';provider=None
    aliases={}
    if enabled:
        aliases=json.loads(env.get('TIMEMAP_PLACE_ALIASES','{}'))
        if not isinstance(aliases,dict) or any(not isinstance(k,str) or not k.strip() or not isinstance(v,str) or not v.strip() for k,v in aliases.items()):raise ValueError('地点别名配置无效')
        if any(not env.get(n) for n in ['NOTION_TOKEN','TIMEMAP_NOTION_PAGES','GEMINI_API_KEY','GEMINI_MODEL']):raise ValueError('缺少模型配置')
        transport=Transport(allow_network=True)
        source=NotionSource({int(y):p for y,p in json.loads(env['TIMEMAP_NOTION_PAGES']).items()},env['NOTION_TOKEN'],transport)
        provider=ActivityProvider(source,ActivityExtractor(GeminiModel(env['GEMINI_API_KEY'],env['GEMINI_MODEL'],transport),aliases))
    return create_app(path,env['TIMEMAP_DEVICE_TOKEN'],rules=rules,origins=origins,eager=False,mode='production',embedded_worker=False,activity_provider=provider,allow_efficiency=enabled,allow_send=env.get('TIMEMAP_ALLOW_SEND')=='1',place_aliases=aliases)
