# V1.2 模拟开发检查点

当前工作在 feature/v1.2，完整目标见 time_map_spec.md。该实现是本地模拟联通版本，**不是可部署生产服务**。

## 启动与测试

在仓库根目录：

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m unittest discover -s tests -v
```

运行本机模拟 API（TIMEMAP_DEV_TOKEN 是专用本地测试凭据，不要使用真实密钥）：

```sh
export TIMEMAP_DEV_TOKEN=local-only-mock-token
export TIMEMAP_DB=/tmp/timemap-mock.sqlite3
.venv/bin/python -m uvicorn backend.dev:app --host 127.0.0.1 --port 8100
```

另一个终端在 Time_map 目录运行 `python3 -m http.server 8001 --bind 127.0.0.1`。打开本机页面，在“连接本地模拟服务”填写API地址及测试凭据。输入时间范围2026-10-28 09:00—12:00，用本地记录中同时间的图书馆片段匹配模拟阅读活动。不得向模拟数据库导入个人生产备份作为测试夹具。

`backend/mock_activities.json` 是显式结构化活动夹具，代码不请求Notion、Gemini、飞书。规则测试映射category，不声称已实现真实文本语义分类。模拟报告同步完成，API仍以analysis-jobs命名为后续持久化异步任务保留契约。

## 已验证

- SQLite同步事务、幂等重试、版本冲突拒绝、删除墓碑、非法批次回滚。
- 精确活动区间与地点匹配、仅时长完整日归属、部分日无法归属提示、跨午夜、重复/重叠/超额异常。
- 新报告使用当前规则；旧报告及其活动快照从SQLite读取且保持不变。
- 按日结算、01:30边界、补做及历史变更后重算；SQLite备份恢复。
- 备用日志JSON块解析、重复去重和同ID冲突。
- API认证、无外部HTTP调用；所有测试数据保存在临时目录。
- 原日报源码、项目累计、Notion写入、Life Calendar数据及生产Actions均未修改或执行。

## 必须继续完成，不能以密钥填写代替

生产联调前还需实现：有大小限制的API请求、设备配对/撤销/轮换、持久化异步任务及租约、规则管理与真实结构化提取适配、当前活动修订索引、报告历史列表、原日报追加章节适配及真实发送适配。当前结算提供可测试tick，尚无常驻调度进程。备用解析器尚未接Notion读取。

当前单设备同步采用完整快照批次（保证关联边界原子性），变更查询返回最新快照；不是最终的分页增量协议。所有结算在写入后失效，属于正确但粗粒度的策略。删除墓碑阻止复活，备份恢复冲突尚需显式解决界面。V2备份不包含凭据和服务配置。

生产前需要细化分页、增量操作、旧标签页协调、队列大小、失败诊断和恢复策略。当前仅静态令牌本地认证，不能直接暴露到公网。CORS仅允许指定本机来源。没有生产Docker/Caddy/COS配置，也没有部署。

真实联调使用测试Notion页面和测试飞书群；秘密通过安全配置注入。原数据有独立备份后才做迁移验收。Safari主屏幕PWA升级、Service Worker离线启动及真实设备验收尚未执行。
