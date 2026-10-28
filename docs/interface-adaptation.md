# 接口适配与开始时间＋持续时长

## 本轮实现

- backend/activity_time.py：北京时间确定性解析；开始＋持续计算结束、显式起止计算时长、仅时长保留无精确区间。支持中文数字、上午下午、半点/一刻、半小时、一小时半、小时分钟组合与显式次日。
- backend/adapters.py：Notion原始日程只读GET及分页层级；Gemini REST结构化输出；Ollama schema；飞书业务码及超时适配；ActivityExtractor、ActivityProvider和make_pipeline用于组装已有分析链路。
- 活动模型必须摘取原文tokens及来源ID，代码核验来源后再做算术。没有明确时段的“九点”不猜上午下午；“09:00”按24小时制；显式23:30—00:30无次日说明时提示冲突。开始23:30＋1小时可以确定性算出次日00:30。
- 明确写了地点但别名未映射时，不将其当作地点缺失重新匹配。仅时长在完整自然日可汇总，部分日不能精确匹配；时间冲突不以仅时长为由伪造全天比例。
- 原始日程源按年份配置页面，匹配ISO或月.日标题；同日期重复块、同页面跨年模糊标题、分页异常、缺少主查询日期报错。查询会读取前一日以覆盖跨午夜，缺少前一日内容仍可能漏掉未记录的跨夜活动，须核查来源完整性。

## 组装方式（本轮不执行真实调用）

```python
from backend.adapters import (Transport, NotionSource, ActivityExtractor,
    GeminiModel, ActivityProvider, typed_rules)
from backend.app import create_app

# 从安全配置中读取，不在源码硬编码真实值。
# http = Transport(allow_network=True) 只在用户授权真实联调后启用。
# 本轮传入模拟HTTP对象，或保留Transport()默认关闭。
http = Transport()
rules = typed_rules({'version': 'v1.2-initial'})
# model = GeminiModel(key, model_name, http)
# provider = ActivityProvider(NotionSource({2026: source_page_id}, token, http),
#                             ActivityExtractor(model, {'北新桥图书馆': place_id}))
# app = create_app(test_db_path, device_token, rules=rules,
#                  activity_provider=provider, eager=False)
```

配置规则应读取config/activity_rules.json完整内容后调用typed_rules；示例省略字段不是正式分类规则。ActivityExtractor把模型time_type映射到统一category，原日报与地点章节复用一次分类。API仍标注模拟开发服务，尚未将真实provider开启为生产服务。

make_pipeline将NotionSource、ActivityExtractor与已有Pipeline组装，飞书通过FeishuSender作为sender注入。只有manual/daily被显式调用时才运行；构造、导入均无外部请求。手动分析不调用原Notion写回、项目累计或Life Calendar函数。原生产Actions与旧入口未改动，避免分支测试误触正式流程。

## 测试结果及限制

后端unittest总计37项通过（本轮新增16项）；前端原7项与新增11项继续通过。所有HTTP协议测试使用FakeHTTP/模型响应，默认真实Transport拒绝调用；覆盖权限错误、Notion分页与父级上下文、重复日期、两种模型JSON、证据编造、三类时间、歧义冲突、跨日与飞书失败/超时。适配provider经API生成报告后，再看历史不读取源数据。

这些测试证明接口请求格式和确定性计算，并不能证明真实Gemini对所有自然语言的语义准确率。含“九点左右”“大概半小时”等模糊tokens不产生精确计算；不支持的表达应保留错误或要求澄清。真实API版本、模型可用性、真实源页面标题/层级及Safari实机仍待获准联调。

## 真实联调前用户提供

1. 安全配置NOTION_TOKEN及权威源页面ID，按年份映射（含跨夜前一日所在年份）；把原始页面和备用页面授权给集成。NOTION_DATABASE_ID/PROJECT_DATABASE_ID在只读区间分析不是必需，保留原日报全链路时才需要。
2. GEMINI_API_KEY和实际GEMINI_MODEL；使用本地模型则提供受支持的OLLAMA_BASE_URL与OLLAMA_MODEL。
3. 指定日期10月26/27日，明确年份；几条可核验的“起止”或“开始＋时长”记录。不要为测试编造历史精确时间。
4. 对应日期地点日志的脱敏副本及地点ID/别名；没有真实地点日志可用明确标记的模拟地点，但不能称为完整真实匹配验证。
5. 测试飞书群的FEISHU_WEBHOOK_URL安全配置；真实发送需要另行明确批准。默认只读取、分析，不写Notion、不更新长期统计。

秘密不要发到聊天或Git；云环境、Actions、腾讯云各自安全注入。模型调用可能产生费用。授权范围应分别说明读取日期、模型测试与测试消息发送；不包含合并、部署或生产写入。
