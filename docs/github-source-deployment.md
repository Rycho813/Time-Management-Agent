# 从 GitHub 固定提交准备独立测试服务

这是服务器操作审核材料，不是部署授权。先在 feature/v1.2 整理、测试和推送；用户批准后才在腾讯云执行。无需 GitHub Release、GitHub API 或附件下载。Git 拉取与 Docker 构建只访问代码和依赖源，不调用 Notion/Gemini/飞书，不运行原日报。

## 目录和版本

两个仓库按审核记录给出的完整 40 位提交检出，不能只用浮动分支。服务目录如下：

```text
/opt/timemap-test/
  releases/source-<后端前12位>-<前端前12位>/
    agent/                  后端 Git 仓库，detached HEAD
    frontend/               前端 Git 仓库，detached HEAD
    image.json              本机构建的两个提交及实际镜像 ID
  config/                   root 管理，0600 配置和 release.json
  data/                     0700，容器 UID/GID 10001
  backups/                  0700，容器 UID/GID 10001
  current -> releases/source-.../
```

新增 `deploy/source_release.py` 支持 build、prepare、compose。构建前后检查两个仓库的提交与干净状态，拒绝覆盖已有镜像标签及构建记录。prepare 验证镜像 ID、标签、平台、运行 UID 和源码提交，生成设备令牌，不打印秘密、不启动服务。运行中的代码、数据与配置分开。

`deploy/smoke-test.py` 仅请求回环接口；默认只接受空测试库，写入一条合成地点记录并生成地点报告，非空库拒绝重复写入。`--verify-existing` 只读取既有测试报告，用于重启后检查，不提交同步或新分析。

Time_map 的 `deploy/check-static.py` 只读检查固定前端、入口依赖、图标和相对 PWA scope；此阶段不把前端复制到 Caddy 目录，也不改变原 GitHub Pages 或手机主屏幕。

## 阶段 1：执行前复核（将来在服务器）

```bash
command -v git
python3 --version
sudo docker version --format '{{.Server.Version}}'
sudo docker compose version
df -h /
free -h
sudo ss -lntp
getent passwd 10001
```

缺工具则停止反馈，不自动安装。18120 须未占用；10001 若对应现有宿主用户先核对身份冲突。检查当前备份仍可用、实例未到期；已完成的应用文件恢复和数据库校验不能替代整机演练。快照仍按用户决定暂不创建。

在 2GB 主机上构建可能短时占用 CPU、内存和磁盘。选择低负载时段，保持另一个终端观察 docker stats/free/vmstat；现有 RSS 服务不因构建而主动停止。不把 BuildKit 的内存限制当成已生效保证，不进行并行构建。若影响现有业务，停止本次构建并汇报，不全局清理镜像或升级服务器。

## 阶段 2：拉取两个固定提交（批准后执行）

以下为模板，最终审核记录给出真实 SHA。占位值会在创建任何目录前被拒绝。使用 root 克隆，避免之后 sudo Git 检查产生所有者不匹配；现有公开仓库不需要把 GitHub 令牌写入服务器。

```bash
sudo bash -s <<'FETCH'
set -euo pipefail
AGENT_SHA='REPLACE_WITH_APPROVED_AGENT_SHA'
FRONTEND_SHA='REPLACE_WITH_APPROVED_FRONTEND_SHA'
[[ "$AGENT_SHA" =~ ^[0-9a-f]{40}$ ]]
[[ "$FRONTEND_SHA" =~ ^[0-9a-f]{40}$ ]]
R="/opt/timemap-test/releases/source-${AGENT_SHA:0:12}-${FRONTEND_SHA:0:12}"
test ! -e "$R"
test ! -L "$R"
install -d -m 0755 "$R"
git clone --single-branch --branch feature/v1.2 https://github.com/Rycho813/Time-Management-Agent.git "$R/agent"
git clone --single-branch --branch feature/v1.2 https://github.com/Rycho813/Time_map.git "$R/frontend"
git -C "$R/agent" checkout --detach "$AGENT_SHA"
git -C "$R/frontend" checkout --detach "$FRONTEND_SHA"
test "$(git -C "$R/agent" rev-parse HEAD)" = "$AGENT_SHA"
test "$(git -C "$R/frontend" rev-parse HEAD)" = "$FRONTEND_SHA"
python3 "$R/frontend/deploy/check-static.py" --expected-commit "$FRONTEND_SHA"
printf 'SOURCE_READY=%s\n' "$R"
FETCH
```

网络失败或目录已存在时先查看结果，不删除目录盲目重试。后续不在该目录执行 git pull 改变正在使用的版本。

## 阶段 3：本机构建并记录镜像（不启动服务）

将两个完整 SHA 和 R 设置为上一步的真实值后执行：

```bash
sudo python3 "$R/agent/deploy/source_release.py" build \
    --agent-sha "$AGENT_SHA" --frontend-sha "$FRONTEND_SHA" \
    --frontend "$R/frontend" --record "$R/image.json"
```

每一批命令都须重新设置或明确传入变量；上一步 sudo heredoc 内的变量不会留在外面的终端。最终审核记录会提供无需依赖遗留变量的命令。

服务器构建使用 Dockerfile 中固定的基础镜像摘要和依赖锁文件。开发云环境构建如需要代理 CA，可显式传 `--ca-bundle /etc/ssl/certs/ca-certificates.crt`；不关闭 TLS。普通腾讯云环境先使用默认信任，不复制开发环境代理配置。

本机重建会产生自己的镜像 ID，不能沿用旧离线包的镜像 ID 或归档 SHA。工具根据固定源码的 label 和实际 ID 生成 image.json，之后每次启动前复核；不使用 latest，不发布到镜像仓库。若镜像或记录已存在，停止核对，不覆盖已有构建。

## 阶段 4：初始化独立配置（仍不启动服务）

```bash
sudo python3 "$R/agent/deploy/source_release.py" prepare --record "$R/image.json"
sudo python3 /opt/timemap-test/current/agent/deploy/source_release.py compose config --quiet
```

prepare 默认在 /opt/timemap-test 生成配置，已有 config/data/backups/current 或源目录 runtime.env 会拒绝覆盖。中途失败也先反馈，不删除已有数据或重跑覆盖。

| 配置 | 初始值 |
| --- | --- |
| TIMEMAP_IMAGE | source 工具生成的唯一标签，记录实际 ID |
| TIMEMAP_DATA_DIR | /opt/timemap-test/data |
| TIMEMAP_DB | /data/timemap.sqlite3 |
| TIMEMAP_RULES | /app/config/activity_rules.json |
| TIMEMAP_ORIGINS | https://timemap.rychoz.com（只是允许来源，不配置 DNS） |
| TIMEMAP_DEVICE_TOKEN | 服务器随机生成，0600 私有文件，不提交 Git 或发到聊天 |
| TIMEMAP_ALLOW_MODEL / TIMEMAP_ALLOW_SEND | 均为 0 |

不需要 Notion/Gemini/飞书密钥。初期只运行地点模式，不能用有效活动报告代替这个阶段的验收。compose 工具会清除远程 Docker 选择器及环境中的发送 profile，固定本地 socket、项目名和配置文件，拉取策略固定为 never；config 只允许 quiet。

## 阶段 5：先 API，验证后再启动单 worker

```bash
sudo python3 /opt/timemap-test/current/agent/deploy/source_release.py compose up -d api
curl --fail --silent --show-error --max-time 5 http://127.0.0.1:18120/health
curl --silent --output /dev/null --write-out '%{http_code}\n' http://127.0.0.1:18120/v1/reports
```

API 可能需要数秒启动。health 应为 ok、production、external_calls=false，无授权 reports 应为 401。通过后再执行：

```bash
sudo python3 /opt/timemap-test/current/agent/deploy/source_release.py compose up -d worker
sudo python3 /opt/timemap-test/current/agent/deploy/smoke-test.py
sudo python3 /opt/timemap-test/current/agent/deploy/source_release.py compose ps
sudo docker stats --no-stream --format 'table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}'
free -h
vmstat 1 5
```

smoke 应输出 SERVER_SMOKE_OK，库中保留一条 30 分钟合成地点记录及报告。再次运行默认写入测试会拒绝非空库，不删库来重跑。检查原 FreshRSS、RSSHub，观察一个抓取高峰；API 384MiB、worker 256MiB 是上限，不是服务器共存稳定性证明。

重启只操作新服务，再读取既有报告：

```bash
sudo python3 /opt/timemap-test/current/agent/deploy/source_release.py compose restart api worker
# 等待 health 正常后：
sudo python3 /opt/timemap-test/current/agent/deploy/smoke-test.py --verify-existing
```

本阶段不启动 sender 或 timer，不自动读取 Notion，不发送飞书，不写长期统计或 Life Calendar，不替换手机原数据。保留的 systemd 样例不自动安装。

## 备份、问题处理和回退

使用维护 CLI 做一致备份，目标须为新文件：

```bash
sudo python3 /opt/timemap-test/current/agent/deploy/source_release.py compose run --rm --no-deps \
    -v /opt/timemap-test/backups:/backup worker python -m backend.maintenance backup \
    --db /data/timemap.sqlite3 --target /backup/first-source-test.sqlite3
```

目标已存在就用新名字。恢复使用 restore 到新目标，不覆盖运行库；schema 2 不能直接交给不兼容旧代码。升级另外准备匹配源码、镜像及数据库备份，本轮不是自动升级器。

出问题先查看有限日志、只停止新服务：

```bash
sudo python3 /opt/timemap-test/current/agent/deploy/source_release.py compose logs --tail 80 api worker
sudo python3 /opt/timemap-test/current/agent/deploy/source_release.py compose stop api worker
```

即使源码或镜像校验失败，logs/ps/stop 仍可使用，不因启动保护阻止回退。若配置文件本身丢失，先列出 `sudo docker ps --filter label=com.docker.compose.project=timemap-test`，核对后仅停止属于该项目的容器。保留库和日志，不执行 down -v、全局 prune、重启 Docker 或整机快照恢复。

通过内部验收后才另行批准前端复制、DNS、Caddy 和手机测试；当前源码拉取与构建不会修改现有网站，也不会运行 GitHub Actions。
