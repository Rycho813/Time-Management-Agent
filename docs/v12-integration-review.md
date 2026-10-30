# V1.2 北京时间、日报接入及真实配置审核（2026-10-30）

> 后续开发更新：已新增默认关闭的旧入口接入、自然日投影和共享写回；当前实现及限制见 [新日报接入说明](daily-v12-opt-in.md)。下文“尚未接入”等内容记录149336b时的审核基线，不代表后续开发已部署。手机迁移条件继续适用。

本轮仅开发分支测试与推送。无真实业务请求、飞书发送、定时器、腾讯服务器操作或手机迁移。

## 时间核对

backend/core.py 的 TZ 固定 ZoneInfo('Asia/Shanghai')，day_ranges 按该时区分日，左闭右开；activity_time.py 和 report_delivery.py 也使用该时区。前端输入、报告区间、历史按钮与日志导出已有 UTC+8 语义；此次同步状态单独通过 Intl 显式指定 Asia/Shanghai，ISO UTC 存储不变。北京时间现在不取决于服务器或手机时区。

新增离线测试：UTC 15:30—16:30 对应北京时间 23:30—次日00:30，每天各30分钟；跨年同理；结束于北京时间午夜不生成次日报告；独立报告文本从次日00:00裁剪。仅地点和可靠时间交集是已验证的区间能力，不能推导模型语义准确率。

旧入口 migrate_and_summary.py 的 APP_TIMEZONE 默认 Asia/Singapore，与现代北京时间同为 UTC+8，但名称不同且受配置覆盖；未改旧生产入口。将来接入前统一部署配置 APP_TIMEZONE=Asia/Shanghai；无效时区当前会回退主机日期，应在获批接入前校验，而非依赖此回退。Actions cron 的平台时区行为也需单独核实，本轮不启用或改动。

## 新报告尚未接入原日报入口

- 原入口是 .github/workflows/daily_time_report.yml → migrate_and_summary.py → run_daily_workflow。仍迁移/写 Notion、生成摘要、比较、项目累计和 Life Calendar，并按开关发送旧报告。
- 新入口是 backend.prod + API analysis-jobs + backend.worker，生成独立不可变区间报告。worker 不自动发飞书。delivery_worker 是另一个显式启用的发送器。
- backend.pipeline.Pipeline.daily 是可注入的组装接口，make_pipeline 提供适配，但旧入口与 Actions 没有调用它。它不是已上线的原日报替代品。
- time_summary.py 相对 main 无差异。Pipeline.daily 复用 normalize_items/build_activity_snapshot/build_summary/build_feishu_report；测试核对基础日报前缀逐字相同、仅提取一次，末尾增加地点章。默认睡眠420分钟仍与原默认7小时一致。
- 这不等于完整旧日报兼容：Pipeline.daily 未包含原 generate_snapshot_and_summary 的日/周比较与项目完成报告，也未自动连接 Notion写回、项目累计、Life Calendar；legacy_sink 需显式适配。API 独立报告及 report_delivery 的测试报告使用自己的标题与章节，不是原日报格式。
- NotionSource 会额外读取前一日以找跨夜活动；Pipeline.daily 当前把全部 extractor 结果交给旧摘要，未按报告自然日裁剪全部活动。因此包含前一日/跨夜输入时，基础日报总量不能据区间地点统计推定兼容。本次测试验证格式复用，不宣称这类汇总已兼容。正式接入前需定义仅时长记录/跨夜裁剪规则并加测试。

接入须单独审核：保留旧日报章节/顺序及副作用，统一一次分类与日期归属，地点章失败允许降级，并确保旧 Actions、新定时器、sender 不会双跑/双发；发送超时先人工查回执，不盲重试。本轮未擅自接入。

## 真实配置条件（只列名称，不复制现有值）

| 工作流 | 必需条件 | 开关与限制 |
| --- | --- | --- |
| 现有地点 API/worker | TIMEMAP_DB、TIMEMAP_DEVICE_TOKEN、TIMEMAP_ORIGINS、TIMEMAP_RULES；持久目录权限、规则版本、备份 | 不需 Notion/模型；保持 TIMEMAP_ALLOW_MODEL=0、TIMEMAP_ALLOW_SEND=0 |
| 新活动效率报告 | NOTION_TOKEN；TIMEMAP_NOTION_PAGES（年份→权威原始日程页面ID，覆盖查询前一日/跨年）；GEMINI_API_KEY、实际可用 GEMINI_MODEL；地点ID/别名 TIMEMAP_PLACE_ALIASES | production 工厂当前选择 Gemini；代码有 Ollama 适配器但不能仅设置 LLM_PROVIDER 就切换 prod。获批真实读取与费用后才开启 MODEL=1，SEND继续0 |
| 原完整日报 | NOTION_TOKEN、NOTION_DATABASE_ID、NOTION_SOURCE_PAGE_ID、PROJECT_DATABASE_ID、LIFE_BIRTH_DATE；实际数据库属性名/类型、源页面层级、APP_TIMEZONE、睡眠/周总结配置 | Summary/summary 默认存在差异，须核对实际属性；LLM_PROVIDER=gemini 或 ollama，后者需 OLLAMA_BASE_URL/MODEL 与可用模型服务。生日用于 Life Calendar，新只读区间分析不需要 |
| 新发送器 | FEISHU_WEBHOOK_DIRECT_URL 或旧完整 FEISHU_WEBHOOK_URL；测试机器人可用、明确发送授权 | 必须是安全注入的完整HTTPS地址，代理占位符不能作目的URL。TIMEMAP_ALLOW_SEND=1 只是开关，还需获批启动 sender/发起发送；本轮均不做 |

Notion 集成需具有对应原始页面只读权限；旧日报还需数据库写权限和正确属性。Gemini 实际模型、配额/计费、网络目的地均待真实授权后验证。代理密钥适合固定域名认证头：api.notion.com、generativelanguage.googleapis.com；飞书完整URL采用安全运行变量，不能绕开TLS/代理。原 feishu_report.py 只识别 FEISHU_WEBHOOK_URL，新 DIRECT_URL 优先逻辑不自动升级旧入口。云开发、腾讯云、GitHub Actions 分别配置，不假定互通；不得输出秘密或提交 .env。

## 手机数据迁移待审核方案

1. 原 GitHub Pages 与 preview 是不同 origin，localStorage/计时独立；单纯更新 preview 静态资源不需要迁移。不卸载原PWA、不清Safari数据、不运行恢复按钮；保留原站正在进行的计时。
2. 获批迁移时，先确定唯一权威设备/来源及切换时间。原站导出JSON，在“文件”确认保存，保留离线副本；新预览页也导出自己的备份；服务器做一致性备份并记录当前revision。备份含个人记录，不进Git或公开目录。
3. 先对副本离线检查schema、地点/记录ID、起止时间、重叠、正在进行的null结束段、记录数和北京时间抽样，不改原件。运行中片段是时间戳，不依赖网页累加；不得为迁移伪造结束时间。
4. 当前云端已有同步数据，不能直接把手机快照作为空云端首次导入。冲突时采用云端会覆盖预览页未上传记录，采用本机会修改/删除云端记录；两者都不是自动合并。先比对备份、revision、唯一ID及墓碑，提交合并清单供用户确认；本轮不选择恢复方向。
5. 获批后先在独立测试库用副本演练，核对总数/每日时长/跨天/进行中计时，明确单设备写入。正式切换不能让原站和预览页同时继续产生两套权威日志；停止旧站录入的时间需用户确认，本轮保留旧计时。
6. 验收后保留备份与审计记录；静态回退仅恢复页面代码，不回退已同步数据。若数据异常先停止新预览页写入，保留双方副本并审核恢复，不删库、清队列或覆盖手机数据。
