# V1.2 日报兼容接入（默认关闭）

## 已实现的调用链

旧生产入口和 Actions 文件保留。migrate_and_summary.run_daily_workflow 仅在 TIMEMAP_DAILY_V12 精确等于 1 时使用新提取/地点附章；未设置或为0时仍调用旧 generate_snapshot_and_summary。没有部署、添加定时任务或设置任何运行开关。

开启后的链路：校验配置 → 原日程迁移 → NotionSource读取权威原文（含前一日）→ 一次模型提取及分类 → 北京时间自然日投影 → complete_daily_items 共用旧处理 → 原发送开关。dry-run不构造新提取器、不调用模型或生成新报告；--no-feishu仍禁止发送，但与旧行为一样会写Notion/项目/Life Calendar，不能当作只读测试。

complete_daily_items 从旧函数抽出，保持调用及章节顺序：项目日累计/完成报告 → Snapshot/基础摘要 → 日/周比较 → 追加项目完成章节 → Notion属性写回 → Life Calendar。新日报在旧完整文本后追加地点效率章节；Notion Summary保留旧章节，归属异常提示也写入，地点附章仅进入最终报告文本。新流程复用已有项目名提供给模型，不以地点关键词重做分类。Pipeline.prepare_daily只准备报告；不会调用发送器。原入口仍仅通过已有send_feishu参数决定是否发送一次，不启动delivery_worker，也不入发送队列。

## 自然日归属

- 新日报强制Asia/Shanghai完整自然日、左闭右开；默认日期在新开关启用时也按北京时间计算，不受主机或旧APP_TIMEZONE覆盖。旧分支时区行为不变。
- 明确起止/开始加时长的活动裁剪到当前自然日，跨夜两边按实际交集计入；结束恰好午夜只归前一日。
- 仅时长且无明确区间的活动仅归来源日期；读取前一日原文不会把前一日仅时长记录再计入当日。
- 项目closed只在含最后有效时刻的日期保留；跨夜前一日片段改为open，避免提前宣布完成。同一活动不修改原输入。
- 重复/缺少来源ID、时间歧义/冲突、非整分钟活动保留提示并不入旧日报数字。旧项目与Life Calendar格式是整分钟，因此目前不支持将秒级活动直接写入，不能据此称为完整秒级统计。
- 地点章节沿用core的区间匹配；活动有归属异常时不能把未确认利用率说成浪费。地点库读失败可降级为附章不可用，原日报章节仍可生成。

## 配置条件（供以后审核，本轮不执行）

除旧入口所需的Notion数据库/源页面、项目数据库、生日、睡眠/周总结等配置，还需显式提供：

| 名称 | 用途 |
| --- | --- |
| TIMEMAP_DAILY_V12 | 默认关闭，审核后精确值1才选择新链路 |
| TIMEMAP_ALLOW_MODEL | 新日报要求显式1；地点服务原开关不受本次代码更改 |
| TIMEMAP_NOTION_PAGES | JSON年份→权威源页面ID，覆盖目标日及前一日年份 |
| TIMEMAP_RULES | 带版本的规则JSON路径 |
| TIMEMAP_DB | 已存在的地点SQLite绝对路径；日报只读连接，不初始化/升级库 |
| TIMEMAP_PLACE_ALIASES | 可选的原文地点→同步地点ID映射 |
| LLM_PROVIDER | gemini或ollama；bridge未设置时选gemini |
| GEMINI_API_KEY / GEMINI_MODEL | Gemini模式实际可用模型及安全凭据 |
| OLLAMA_BASE_URL / OLLAMA_MODEL | Ollama模式受支持地址及已安装模型 |

基本配置和跨年映射在迁移前校验。旧生日、属性类型、Notion授权等仍是旧写回的前提，现有旧链路不具有跨服务事务；迁移/项目/Notion写回中途失败仍可能留下部分结果，不能承诺exactly-once。上线前需对副本验证幂等与故障恢复，禁止盲目重跑可能发送的日报。只启用一个日报调度来源，不能同时启用旧Actions和新timer/sender造成双跑。

没有把新代码装到腾讯服务器或修改现有镜像/数据库；生产配置需另行审核。代码可关闭开关回到旧流程，但不能撤销已经发生的外部写入。真实Notion/模型语义准确率、真实日期数据及手机端联调仍未在本轮验证。

## 本地验证

后端完整unittest包含新增的前一日/跨夜/午夜边界、项目完成日、重复冲突精度提示、旧入口默认分支/显式新分支/dry-run、配置失败先于迁移、共享章节及写回顺序，以及FakeHTTP经过真实Notion/Gemini适配器的组装。临时地点库revision不变，不写报告或发送队列；模型模拟响应只处理一次。所有外部请求、写回与发送均使用假对象。

```bash
PYTHONDONTWRITEBYTECODE=1 /workspace/.venvs/time-management/bin/python -m unittest discover -s tests -v
```
