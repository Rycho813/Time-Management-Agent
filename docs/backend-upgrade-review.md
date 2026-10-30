# 后端8124316部署审核方案（尚未执行）

审核基线：81243163d7ae7a34be8fe3a509c9e3fe41c4467a。报告精简是此后的独立提交，8124316不含该修改；若要同时部署，须明确改选新的完整SHA，不能偷偷使用feature/v1.2分支最新值。

以下是供审核的后续命令模板，不能跳过服务器现状检查直接执行。当前授权仅检查，不切换或修改数据库，不启动新日报、timer、sender，不调用Notion/模型/飞书。所有路径、实际旧版本与镜像ID、备份有效性尚须服务器输出确认。

## 先只读检查

分步获取 /opt/timemap-test/current 指向、agent/frontend完整HEAD与干净状态、config/release.json与当前image.json的白名单字段、Docker实际容器镜像ID/状态/修订标签。禁止输出docker inspect完整结果或docker compose config展开内容，禁止cat runtime.env。配置仅输出TIMEMAP_ALLOW_MODEL、TIMEMAP_ALLOW_SEND、TIMEMAP_DAILY_V12的0/1/未设置/其他状态；对密钥只检查名称和是否存在，不输出值。

备份检查列出backups内备份文件大小和时间，核对.sidecar JSON中的SHA-256与只读SQLite integrity_check、schema版本。不能把“存在文件”当作恢复已验证；若没有可用备份，止步并另行审核创建备份。新旧源码当前均schema2，无新增schema迁移，但仍须在备份副本运行候选版本测试。

## 备份命令（未来获批后）

先确定唯一的新备份名称；下面只创建一致性备份，不执行settle/compact/recover-token。该进程不会领取分析任务。

```bash
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
sudo python3 /opt/timemap-test/current/agent/deploy/source_release.py compose run --rm --no-deps \
  -v /opt/timemap-test/backups:/backup worker python -m backend.maintenance backup \
  --db /data/timemap.sqlite3 --target "/backup/pre-upgrade-$STAMP.sqlite3"
```

还需把旧release.json、compose.env、current目标和旧镜像ID保存在新建的0700审核目录。配置副本含敏感信息，0600保存，不回传内容。旧镜像/源码/数据库与静态版本均保留；不能调用prepare覆盖现有环境，不重建旧标签或改image.json。

## 独立源码与构建命令（未来获批后）

审核人员设置三个变量：AGENT_SHA为上面的完整固定基线，FRONTEND_SHA为当前后端构建记录中的完整前端来源（不是预览页SHA），R为新的独立发布目录。每批命令重新明确变量，不依赖上个sudo终端残留。目录已存在即停止。

```bash
# 必须先填写审核确认的三个变量，未设置则停止。
: "${AGENT_SHA:?}" "${FRONTEND_SHA:?}" "${R:?}"
[[ "$AGENT_SHA" == 81243163d7ae7a34be8fe3a509c9e3fe41c4467a ]]
[[ "$FRONTEND_SHA" =~ ^[0-9a-f]{40}$ ]]
[[ "$R" == "/opt/timemap-test/releases/source-${AGENT_SHA:0:12}-${FRONTEND_SHA:0:12}" ]]
sudo test ! -e "$R"
sudo test ! -L "$R"
sudo install -d -m 0755 "$R"
sudo git clone --single-branch --branch feature/v1.2 https://github.com/Rycho813/Time-Management-Agent.git "$R/agent"
sudo git clone --single-branch --branch feature/v1.2 https://github.com/Rycho813/Time_map.git "$R/frontend"
sudo git -C "$R/agent" checkout --detach "$AGENT_SHA"
sudo git -C "$R/frontend" checkout --detach "$FRONTEND_SHA"
sudo python3 "$R/agent/deploy/source_release.py" build \
  --agent-sha "$AGENT_SHA" --frontend-sha "$FRONTEND_SHA" \
  --frontend "$R/frontend" --record "$R/image.json"
```

不在原构建检出git pull/checkout。服务器资源有限，不并行构建；不升级Docker、不prune、不影响其他服务。先在临时副本验证候选镜像导入、启动、schema及零外部开关，再审核切换。

重要边界：当前Dockerfile只打包backend、time_summary.py和config，不打包原Notion迁移/项目写回脚本；因此部署API/worker镜像不等于部署或启用原入口的新日报。该入口仍需独立运行包/环境审核，不能简单设置TIMEMAP_DAILY_V12就假定容器会执行完整日报。

## 更新命令骨架（检查后补齐切换脚本，暂不执行）

升级需要同时匹配current、config/release.json和compose.env中的镜像标签；原source_release工具只有首次prepare，没有现有实例upgrade子命令。不得拼凑只改一个链接的命令。拟议切换脚本必须先保存可恢复配置、验证新镜像及新源码，再仅停止timemap-test的api/worker，重新做最终一致性备份，原子替换配置文件/链接，且任何一步失败回到已保存配置。

```bash
# 停止范围仅新服务，不动其他项目或Docker守护进程。
sudo python3 /opt/timemap-test/current/agent/deploy/source_release.py compose stop api worker
# 此处的配置切换命令须依据只读检查结果单独生成审核，当前没有批准执行。
# 匹配配置准备完成后，先静态验证，再只启动api，检查通过后启动worker。
sudo python3 "$R/agent/deploy/source_release.py" compose config --quiet
sudo python3 "$R/agent/deploy/source_release.py" compose up -d api
curl --fail --silent --show-error --max-time 5 http://127.0.0.1:18120/health
sudo python3 "$R/agent/deploy/source_release.py" compose up -d worker
sudo python3 "$R/agent/deploy/source_release.py" compose ps
```

health应为production、external_calls=false；开关保持MODEL=0、SEND=0、DAILY_V12未设置或0。不调用默认写入型smoke-test、不生成新报告、不提交同步、不新增合成记录到现有库。api/worker切换短暂停机期间手机保留本地队列，不要求清数据或手动恢复。预览静态链接保持不变。

## 回退命令骨架（未来仅在需要时获批执行）

```bash
sudo python3 /opt/timemap-test/current/agent/deploy/source_release.py compose stop api worker
# 恢复审核目录保存的release.json、compose.env与旧current；不覆盖运行库。
# 具体恢复路径/镜像须与第一步观察证据匹配，后续提供可复制的命令。
sudo python3 /opt/timemap-test/current/agent/deploy/source_release.py compose config --quiet
sudo python3 /opt/timemap-test/current/agent/deploy/source_release.py compose up -d api
curl --fail --silent --show-error --max-time 5 http://127.0.0.1:18120/health
sudo python3 /opt/timemap-test/current/agent/deploy/source_release.py compose up -d worker
```

schema2且验证兼容时回退代码保留最新同步数据，不自动用旧备份覆盖现有库。若库不兼容或完整性异常则停止，备份当前库，在新目标恢复旧备份并单独审核数据损失/客户端revision处理；不能把恢复当成普通静态回退。旧镜像不得删除，保留失败候选及日志供诊断，日志不要直接回传可能含敏感信息的内容。
