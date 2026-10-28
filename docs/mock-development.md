# V1.2 模拟阶段：运行与边界

代码位于 feature/v1.2，完整需求见 time_map_spec.md。本阶段验证模拟业务闭环，不是生产上线授权。

## 启动

在本仓库根目录：

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m unittest discover -s tests -v
export TIMEMAP_DEV_TOKEN=local-only-mock-token
export TIMEMAP_DB=/tmp/timemap-mock.sqlite3
.venv/bin/python -m uvicorn backend.dev:app --host 127.0.0.1 --port 8100
```

使用专用本地测试凭据，不能使用真实秘密。开发API只监听本机；首次创建数据库时登记令牌哈希，轮换后旧令牌不可用且重启不会重新启用旧令牌。测试数据库不要导入个人生产数据。

另一个终端在Time_map仓库运行 `python3 -m http.server 8001 --bind 127.0.0.1`。在页面“连接本地模拟服务”填入本机API地址和上述测试凭据。模拟活动日期2026-10-28，09:00—10:00阅读；用同时间地点日志验证匹配。

后台执行器由FastAPI lifespan启动，SQLite任务先queued，再running，完成后生成不可变报告。测试工厂默认eager=True保留纯函数接口的确定性；浏览器开发服务明确eager=False。任务失败保留错误，可调用重试接口，最多3次显式尝试；过期租约可被恢复，旧租约不能提交。真实模型耗时、心跳及多进程负载需在生产适配阶段压测。

## 模拟闭环已实现

- 增量upsert/delete原子批次、快照兼容导入、批次幂等、分页游标、版本冲突、墓碑及显式恢复。
- 日期结算tick、跨午夜、迟到失效重算、漏跑补做、SQLite一致性备份及恢复。
- 可靠时间交集匹配、完整日仅时长归属、证据不足/重复/冲突/超额提示。
- 不可变报告与历史查询；新规则/原文修订只影响新报告。
- SQLite任务入队、领取、过期恢复、租约隔离及失败重试。
- 本地凭据哈希、轮换撤销、请求大小限制和精确CORS。
- pipeline.py以注入的原始日程读取器、提取器、地点读取器、发送器及长期统计sink验证完整调用顺序。一天只提取一次，旧日报指标和地点章共用分类；手动报告不调用sink。地点服务故障时保留原日报并注明附章不可用。
- 长消息按UTF-8字节分段，检查模拟HTTP与业务返回码，超时标记uncertain且不盲目重试。
- 备用Notion文本的结构化块解析、去重与冲突检查。

## 与真实联调的明确边界

当前读取器、模型和发送器均为注入的模拟实现；backend/dev.py使用固定结构化夹具，不把category映射冒充真实语义分类。pipeline.py未接入现有生产Actions或执行原日报写入。真实Notion分页/跨年、模型schema与prompt、飞书回执需要后续适配和获准的测试调用。备用日志解析尚未接真实Notion读取。

当前仅单设备令牌轮换，正式设备配对、限流、密钥托管及异常审计在生产加固阶段验收。任务mock同步计算很快，真实慢任务须校准租约并加心跳。changes内部为保留完整版本后计算差异，传输是分页增量；长期压缩、数据量上限及大于2MiB的首次导入分批尚需规模测试。自动结算提供tick，常驻调度的部署与COS自动备份不在本次模拟运行中启动。

这些边界不需要用户现在提供真实秘密，但不得据模拟测试宣称生产就绪。下一阶段先完成真实适配代码及配置检查，再在获准范围调用测试数据服务。iPhone主屏幕与Safari验收仍需真实设备。
