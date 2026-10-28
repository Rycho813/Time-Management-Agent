# 本地加固实现与验收

本次只在开发云环境使用合成数据、临时数据库和无外网本机容器；未连接腾讯云、未调用Notion/Gemini/飞书、未修改生产数据或合并部署。

## 已完成

- prod.build_app真实入口，强令牌/精确HTTPS来源/规则与持久化路径必填；禁止加载模拟夹具，缺配置拒绝启动。模型、发送默认关闭，生产任务错误脱敏。
- 单API、单独worker；15秒续租、旧租约隔离、最多3次尝试；数据库限流和每日20次模型调用预算（包含重试），API每日分析任务上限20。
- 原子凭据轮换，旧令牌撤销；仅本机operator CLI可恢复凭据到0600文件，不打印凭据。用户设备是单用户权限，不实现多用户角色系统。
- SQLite schema v2迁移：旧库先生成pre-v2副本；拒绝更新版本数据库；WAL/FULL、busy timeout和索引。历史全量快照一次转换为增量事件，不继续按每次修改保存全库。
- 固定目标修订与操作游标分页；原子分批上传、重启后续传、完成收据保留且清除暂存；暂存总量限制。维护compact先备份，再生成检查点；墓碑、幂等收据和旧报告保留，旧游标明确冲突，全量重建从0开始。
- 原子报告+SQLite发送outbox，sending崩溃状态不自动重发，失败前段阻止后段；发送worker显式开启。手动分析不引入原项目累计/Notion写回/Life Calendar。
- 一致备份、完整性/校验和验证、恢复只允许新目标，恢复不启动任何服务；operator compact、settle和recover-token。
- 前端大批次暂存、目标版本固定、429受控退避；恢复前确认备份已保存；同会话刷新保留连接并启动补传。会话结束后需重新提供设备凭据，不宣称实现了iOS系统级安全凭据存储。
- 非root、只读根目录、无Docker socket、drop ALL capabilities、no-new-privileges、内存/CPU/日志限制的Compose；实际源文件0600导致非root读取失败已在镜像COPY时用chown修复。

## 本地证据

- 后端58项测试通过，前端20项通过；Chromium手机视口、异步报告、历史、离线重载通过。
- 真实63秒假模型任务跨过60秒租约，心跳防止二次领取且完成（结果见最终验证记录）。
- 20万合成记录分400批导入，一次原子提交成功；约3.14秒，峰值RSS约270.7MiB，库约63.1MiB。不是手机性能或服务器共存压测。
- 原API192MiB预算低于测得峰值，候选改为384MiB；worker256MiB；sender96MiB默认不启动。需在2GB主机检查实际余量与RSS抓取峰值，不能据此保证最大规模并发可部署。
- 锁定已验证依赖；官方Python基础镜像以digest固定。本机镜像构建与无外网运行验证API、独立worker、非root/只读根目录、备份恢复通过；Compose语法验证通过。

## 运行材料

- deploy/Dockerfile：仅明确COPY后端、纯汇总函数、规则；.dockerignore排除秘密、备份、.git、venv及个人附件。云环境构建使用BuildKit CA secret，TLS始终验证。
- deploy/compose.yaml：新测试项目，不改任何现有服务。runtime.env.example是无秘密模板；实际runtime.env不可进Git。
- deploy/Caddyfile.example：新增站点草案，不覆盖FreshRSS。实际Caddy版本/路径/域名仍需服务器只读检查。
- deploy/timemap-settle.*.example：地点结算systemd timer样例，部署前审核日期及实际二进制路径；不启用原正式日报。
- backend.maintenance：backup/restore/compact要求显式目标；settle要求--since。COS异机上传及服务快照仍属于服务器阶段配置，本轮没有创建云资源。

## 尚需真实环境验收

- 服务器现有资源、端口、FreshRSS备份方法、Caddy reload、COS权限和恢复时间；不得直接把候选限制当已验证生产容量。
- Safari/iPhone主屏幕全关闭后的凭据体验、正式站点准确URL和同源存储；先用独立预览站，不替换原PWA。
- 生产运行的真实慢模型耗时/上限、费用及限流预算；发送故障需人工核对，不能承诺webhook exactly-once。
- 初始原日报Actions未自动切换新链路，防止双发；正式接入仍需独立批准验收。schema v2不能让旧代码直接打开，回退需对应旧库备份和旧镜像。
- 定期归档和超期未完成暂存清理由operator审核，不自动删除用户历史或墓碑。

以上是本地测试成果，不是合并或服务器部署批准。

## 复现命令（仅开发机）

```sh
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python tests/manual/capacity.py
.venv/bin/python tests/manual/long_job.py
```

long_job耗时约63秒，无真实模型调用。container_check.py仅在已构建本机镜像后运行，使用本地Docker socket、唯一名称、合成数据、无外网容器，结束删除其测试容器和卷。不能指向远程Docker。测试结束后使用的是固定镜像摘要，不代表镜像已推送到注册表或安装到腾讯云。

数据库v2的发布回退：先停止新任务与外发，保护当前库，使用升级前备份在新目录恢复并配套旧版本应用；不要把v2库直接交给v1代码。恢复后人工核对outbox及客户端游标，避免重复发送和误覆盖。
