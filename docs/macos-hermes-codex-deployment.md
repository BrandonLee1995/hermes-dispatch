# macOS Hermes + Codex + QQ 完整部署操作手册

## 1. 目的与适用范围

本文用于在一台新的 Mac mini 上复刻以下运行环境：

- Hermes Gateway，由 macOS `launchd` 托管；
- 最新稳定版 Codex CLI；
- 最新稳定版 Codex App；
- Hermes `codex_app_server` 运行模式；
- QQ Bot 私聊和群聊接入；
- QQ 全群消息快照、混合检索和多媒体链接记录；
- Codex 命令、文件、网络和 Computer Use 授权经 Hermes 转发到 QQ；
- 可移除、可更新、不会因 Hermes 运行时更新而被覆盖的持久化热修复。

本文适用于原生 macOS 部署。Linux 和 Docker 部署应继续参考
[`operations.md`](operations.md) 以及相应部署目录中的说明。

官方入口：

- [Hermes Agent](https://github.com/NousResearch/hermes-agent)
- [Codex CLI](https://learn.chatgpt.com/docs/codex/cli)
- [Codex 配置](https://learn.chatgpt.com/docs/config-file/config-basic)
- [Codex 权限与审批](https://learn.chatgpt.com/docs/agent-approvals-security)

## 2. 不变原则

1. 不固定 Codex CLI、Codex App 或 Hermes 的版本号。部署时安装官方最新稳定版。
2. 不固定热修复版本号。部署和更新时拉取
   `mwe-support/hermes-dispatch` 的最新 `main` 分支。
3. 版本号和 Git commit 只写入部署记录，用于排障和回滚，不写进安装命令。
4. 不直接修改 `~/.hermes/hermes-agent` 中的 Hermes 源代码作为持久修复。
5. 热修复只部署到 `~/.hermes/plugins` 等持久化数据路径。
6. 不复制旧 Mac 的 Python venv、Hermes 运行时、Codex 插件缓存或
   `~/.codex/auth.json`。
7. 新旧两台机器不能同时使用同一套 QQ Bot 凭据运行 Gateway。
8. QQ App ID、Client Secret、用户标识、群标识和授权文件不得提交到 Git。
9. 每一阶段都必须通过对应验收门槛后才能继续。

## 3. 目标架构

```text
QQ 私聊/群聊
    |
    v
Hermes Gateway (launchd)
    |
    +-- QQ Connector
    +-- qqbot-connect-hotfix
    +-- message-snapshot-store
    +-- codex-app-server-phase-hotfix
            |
            v
       codex app-server
            |
            +-- Codex CLI 登录状态
            +-- Codex App 插件
            +-- Hermes Tools MCP
            +-- Computer Use / Browser / Files
```

必须启用的三个持久化插件：

- `codex-app-server-phase-hotfix`
  - 保留 commentary 中间消息；
  - 抑制重复 final 回复；
  - 将 Codex 命令、文件、网络和 Computer Use 授权送入 Hermes Gateway
    审批队列；
  - 将 Codex 图片生成结果转换为 QQ 可发送的本地媒体。
- `qqbot-connect-hotfix`
  - 处理 QQ 全群消息的被动上下文；
  - 修复共享群会话的审批发起者绑定；
  - 根据 Codex 实际提供的决策显示本次、会话、永久或拒绝选项。
- `message-snapshot-store`
  - 使用 SQLite 永久保存结构化消息快照；
  - 支持精确字段、FTS5/BM25、子串、CJK n-gram 和 RRF 混合检索；
  - 默认仅保留最近 20 条、约 4000 tokens 作为普通上下文；
  - 多媒体默认只保存远程链接和元数据。

## 4. 第一阶段：旧 Mac 备份

### 4.1 创建安全备份目录

```bash
umask 077

BACKUP="$HOME/Desktop/hermes-migration-$(date +%Y%m%d-%H%M%S)"
mkdir -p "$BACKUP/hermes" "$BACKUP/codex"
chmod 700 "$BACKUP"
```

### 4.2 停止旧 Gateway

为了获得一致的会话和数据库副本：

```bash
hermes gateway stop
hermes gateway status
```

验收门槛：状态必须显示 Gateway 已停止。

### 4.3 备份 Hermes 状态

```bash
for item in \
  config.yaml \
  SOUL.md \
  memories \
  sessions \
  skills \
  cron \
  kanban \
  state.db \
  state.db-wal \
  state.db-shm
do
  if [ -e "$HOME/.hermes/$item" ]; then
    rsync -a "$HOME/.hermes/$item" "$BACKUP/hermes/"
  fi
done
```

使用 SQLite 原生备份命令复制消息快照：

```bash
if [ -f "$HOME/.hermes/message-snapshots/snapshots.sqlite3" ]; then
  sqlite3 "$HOME/.hermes/message-snapshots/snapshots.sqlite3" \
    ".backup '$BACKUP/hermes/snapshots.sqlite3'"
fi
```

### 4.4 备份 Codex 自定义内容

旧 Codex 配置只保存为参考，不直接覆盖新机器自动生成的配置：

```bash
if [ -f "$HOME/.codex/config.toml" ]; then
  cp "$HOME/.codex/config.toml" "$BACKUP/codex/config.toml.reference"
fi

if [ -d "$HOME/.codex/skills" ]; then
  rsync -a "$HOME/.codex/skills/" "$BACKUP/codex/skills/"
fi
```

不要复制：

- `~/.codex/auth.json`
- Codex 插件缓存
- `node_repl` 运行时
- App 自动生成的 Computer Use MCP 配置
- 指向旧用户目录的 `notify` 脚本路径

### 4.5 处理机密文件

以下内容只能重新录入，或通过加密介质转移：

- `~/.hermes/.env`
- `~/.hermes/auth.json`
- QQ App ID 和 Client Secret
- QQ 用户、群、成员标识

推荐在新 Mac 重新执行 `codex login`，不要迁移 Codex 登录文件。

### 4.6 暂时恢复旧机服务

在新机准备完毕之前，可重新启动旧机：

```bash
hermes gateway start
```

## 5. 第二阶段：准备新 Mac

建议新 Mac 使用与旧机相同的 macOS 用户短名称，避免会话、技能或配置中的
绝对路径失效。

安装 Apple 命令行工具：

```bash
xcode-select --install
```

安装完成后验证：

```bash
git --version
python3 --version
```

## 6. 第三阶段：安装最新 Codex CLI 和 Codex App

### 6.1 安装最新稳定版 Codex CLI

```bash
curl -fsSL https://chatgpt.com/codex/install.sh | sh
```

设置 PATH，并保证用户安装目录优先：

```bash
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"

touch "$HOME/.zprofile"

grep -q 'HOME/.local/bin' "$HOME/.zprofile" || \
  printf '\nexport PATH="$HOME/.local/bin:$PATH"\n' >> "$HOME/.zprofile"

source "$HOME/.zprofile"
hash -r
```

检查 Codex：

```bash
which -a codex
command -v codex
codex --version
```

验收门槛：

- `command -v codex` 应优先解析为 `$HOME/.local/bin/codex`；
- 不允许保留会被 Hermes 优先调用的 Homebrew、npm 或其他旧 Codex 副本；
- 当前版本只记录到部署日志，不写回安装脚本。

### 6.2 登录 Codex

```bash
codex login
codex login status
```

推荐使用 macOS Keychain 保存凭据。

### 6.3 安装或打开最新 Codex App

```bash
codex app
```

在 App 中完成登录，并允许 App 使用自动更新。

### 6.4 配置 Codex 默认权限策略

```bash
mkdir -p "$HOME/.codex"
chmod 700 "$HOME/.codex"
nano "$HOME/.codex/config.toml"
```

将以下核心配置合并到文件中。不设置 Codex 版本，也不强制固定模型：

```toml
approval_policy = "on-request"
approvals_reviewer = "auto_review"
sandbox_mode = "workspace-write"
cli_auth_credentials_store = "keyring"
model_reasoning_effort = "high"

[features]
memories = true

[memories]
generate_memories = true
use_memories = true

[desktop]
preventSleepWhileRunning = true
followUpQueueMode = "steer"
keepRemoteControlAwakeWhilePluggedIn = true
```

验证配置：

```bash
codex --strict-config doctor --summary --ascii
```

不要执行：

```text
/yolo
```

也不要在 Hermes 中设置：

```yaml
approvals:
  mode: off
```

Codex 的 `on-request + auto_review + workspace-write` 是默认审批和 sandbox
边界；Hermes 负责把仍需人工处理的审批请求送到 QQ，而不是建立第二套独立权限策略。

### 6.5 恢复 Skills 和 App 插件

```bash
rsync -a "/迁移介质/codex/skills/" "$HOME/.codex/skills/"
```

在 Codex App 的插件设置中重新安装或启用部署时的最新版本：

- Computer Use
- Browser
- Chrome
- Visualize
- Documents
- PDF
- Spreadsheets
- Presentations
- Record & Replay
- Template Creator

检查：

```bash
codex plugin list
```

新 Mac 必须重新授予：

- 辅助功能；
- 屏幕录制；
- 自动化；
- 必要时的文件和文件夹访问。

QQ 中的 Codex 审批不能替代 macOS 系统权限。

## 7. 第四阶段：安装最新 Hermes

### 7.1 使用官方安装器

```bash
curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash
```

刷新并检查：

```bash
source "$HOME/.zprofile"
hash -r
hermes --version
```

### 7.2 配置模型

```bash
hermes setup
```

选择：

- Provider：`openai-codex`
- Runtime：`codex_app_server`
- Model：选择部署时 Codex 当前支持的目标模型

不在部署文档中固定模型或 Codex 版本。

### 7.3 配置 QQ

```bash
hermes gateway setup
```

如果配置过程启动了 Gateway，立即停止：

```bash
hermes gateway stop
```

此时旧机仍应继续提供 QQ 服务。

### 7.4 写入关键 Hermes 配置

```bash
hermes config set model.provider openai-codex
hermes config set model.openai_runtime codex_app_server

hermes config set agent.max_turns 150
hermes config set agent.reasoning_effort medium

hermes config set terminal.backend local
hermes config set terminal.cwd .
hermes config set terminal.home_mode auto

hermes config set compression.enabled true
hermes config set compression.threshold 0.5
hermes config set compression.target_ratio 0.2
hermes config set compression.protect_last_n 20
hermes config set compression.protect_first_n 3
hermes config set compression.codex_app_server_auto native

hermes config set display.interim_assistant_messages true
hermes config set display.streaming true
hermes config set display.platforms.qqbot.interim_assistant_messages true
hermes config set display.platforms.qqbot.streaming false

hermes config set group_sessions_per_user false
hermes config set platforms.qqbot.enabled true
hermes config set platforms.qqbot.extra.group_policy open
hermes config set session_reset.mode none
```

行为说明：

- `interim_assistant_messages=true` 用于恢复 agent 中间回复；
- QQ `streaming=false` 避免逐 token 刷屏；
- `group_sessions_per_user=false` 保持群聊共享上下文；
- 审批热修复会将审批按钮绑定到实际发起请求的群成员；
- 被动群消息只进入快照和上下文，不自动唤醒 agent。

## 8. 第五阶段：QQ 凭据和永久消息快照

### 8.1 创建环境文件

```bash
install -m 600 /dev/null "$HOME/.hermes/.env"
nano "$HOME/.hermes/.env"
```

填入实际凭据和配置：

```dotenv
QQ_APP_ID=
QQ_CLIENT_SECRET=

QQ_ALLOWED_USERS=
QQ_GROUP_ALLOWED_USERS=

# 只有确实允许所有用户时才设置为 true
QQ_ALLOW_ALL_USERS=false

TERMINAL_TIMEOUT=60
TERMINAL_LIFETIME_SECONDS=300
BROWSER_INACTIVITY_TIMEOUT=120
BROWSER_SESSION_TIMEOUT=300

MESSAGE_SNAPSHOT_MEDIA_STORAGE=link
MESSAGE_SNAPSHOT_CONTEXT_MESSAGES=20
MESSAGE_SNAPSHOT_CONTEXT_TOKENS=4000
```

### 8.2 QQ 全群消息边界

群主已经为当前机器人开启“获取全部群消息”时，同一 QQ Bot 身份迁移到新 Mac
通常不需要重复设置，因为这是 QQ 服务端权限。

以下情况需要重新确认：

- 更换 QQ App ID；
- 重新创建机器人；
- 将不同机器人加入群；
- QQ 后台重置了群机器人权限。

QQ 没有投递给机器人的事件，Hermes 和热修复都无法恢复。

### 8.3 多媒体存储边界

默认：

```dotenv
MESSAGE_SNAPSHOT_MEDIA_STORAGE=link
```

这会永久保存：

- QQ 消息 ID；
- 发送者和群聊字段；
- 文件名、MIME 类型、哈希等已知元数据；
- QQ 提供的远程媒体链接。

它不会保证远程文件字节永久可用。QQ 链接可能过期、需要鉴权或被服务端撤销。

显式执行：

```text
/message-snapshot restore <snapshot-id-or-message-id>
```

才会尝试下载并固定文件。

只有确实需要离线永久保留文件字节，且已经评估存储、隐私和清理策略时，才改为：

```dotenv
MESSAGE_SNAPSHOT_MEDIA_STORAGE=mirror
```

## 9. 第六阶段：安装最新持久化热修复

### 9.1 获取最新主分支

```bash
mkdir -p "$HOME/src"
cd "$HOME/src"

git clone --branch main --single-branch \
  https://github.com/mwe-support/hermes-dispatch.git

cd hermes-dispatch
git pull --ff-only origin main
```

不指定 tag、commit 或插件版本。

### 9.2 安装三个必需插件

```bash
scripts/install-plugins.sh "$HOME/.hermes" \
  codex-app-server-phase-hotfix \
  qqbot-connect-hotfix \
  message-snapshot-store
```

启用：

```bash
hermes plugins enable codex-app-server-phase-hotfix
hermes plugins enable qqbot-connect-hotfix
hermes plugins enable message-snapshot-store
```

检查：

```bash
hermes plugins list --plain --no-bundled
```

验收门槛：三个插件都必须显示为已启用。

### 9.3 运行插件回归测试

```bash
cd "$HOME/src/hermes-dispatch"

HERMES_PY="$HOME/.hermes/hermes-agent/venv/bin/python"

export PYTHONDONTWRITEBYTECODE=1
export PYTHONPATH="$HOME/.hermes/hermes-agent"

"$HERMES_PY" plugins/codex-app-server-phase-hotfix/test_hotfix.py
"$HERMES_PY" plugins/qqbot-connect-hotfix/test_hotfix.py
"$HERMES_PY" plugins/qqbot-connect-hotfix/test_media_reply.py
"$HERMES_PY" plugins/qqbot-connect-hotfix/test_group_roundtrip.py
"$HERMES_PY" plugins/message-snapshot-store/test_store.py
"$HERMES_PY" plugins/message-snapshot-store/test_capture.py
"$HERMES_PY" plugins/message-snapshot-store/test_materialize.py
"$HERMES_PY" plugins/message-snapshot-store/test_quoted_attachment.py
```

任何测试失败都不能继续正式切换。

## 10. 第七阶段：配置 Hermes Tools MCP

在 Hermes 安装完成后执行：

```bash
codex mcp add hermes-tools \
  --env HERMES_HOME="$HOME/.hermes" \
  --env HERMES_QUIET=1 \
  --env HERMES_REDACT_SECRETS=true \
  -- "$HOME/.hermes/hermes-agent/venv/bin/python" \
  -m agent.transports.hermes_tools_mcp_server
```

添加 OpenAI 官方开发文档 MCP：

```bash
codex mcp add openaiDeveloperDocs \
  --url https://developers.openai.com/mcp
```

检查：

```bash
codex mcp list
```

## 11. 第八阶段：恢复历史数据

Gateway 必须保持停止：

```bash
hermes gateway stop
```

### 11.1 恢复普通历史

根据实际备份内容执行：

```bash
rsync -a "/迁移介质/hermes/memories/" "$HOME/.hermes/memories/"
rsync -a "/迁移介质/hermes/skills/" "$HOME/.hermes/skills/"
rsync -a "/迁移介质/hermes/sessions/" "$HOME/.hermes/sessions/"
```

### 11.2 恢复消息快照

```bash
mkdir -p "$HOME/.hermes/message-snapshots"

cp "/迁移介质/hermes/snapshots.sqlite3" \
  "$HOME/.hermes/message-snapshots/snapshots.sqlite3"

chmod 600 "$HOME/.hermes/message-snapshots/snapshots.sqlite3"
```

### 11.3 可选恢复 Gateway 状态

仅在需要完整延续旧会话状态，且旧 Gateway 已停止时执行：

```bash
cp "/迁移介质/hermes/state.db" "$HOME/.hermes/state.db"
```

不要恢复：

- `~/.hermes/hermes-agent`
- Hermes venv
- `~/.hermes/bin`
- `~/.hermes/node`
- `~/.hermes/cache`
- `~/.hermes/logs`
- 锁文件
- Codex 插件缓存和运行时

如果新旧用户名不同，检查旧路径：

```bash
rg -n '/Users/旧用户名' "$HOME/.hermes" "$HOME/.codex/config.toml"
```

## 12. 第九阶段：注册 launchd

### 12.1 再次确认 Codex PATH

```bash
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"

command -v codex
codex --version
```

### 12.2 注册但不启动 Gateway

```bash
hermes gateway install \
  --force \
  --no-start-now \
  --start-on-login
```

### 12.3 验证 launchd 实际使用的 Codex

```bash
PLIST="$HOME/Library/LaunchAgents/ai.hermes.gateway.plist"

PLIST_PATH=$(
  /usr/libexec/PlistBuddy \
    -c 'Print :EnvironmentVariables:PATH' \
    "$PLIST"
)

env PATH="$PLIST_PATH" which codex
env PATH="$PLIST_PATH" codex --version
```

验收门槛：

- launchd 解析到的 Codex 必须与终端 `command -v codex` 一致；
- 如果 launchd 仍解析到其他旧副本，应先移除重复安装或纠正 PATH；
- 修正后重新运行：

```bash
hermes gateway install --force --no-start-now --start-on-login
```

不要把手工编辑 launchd plist 当作长期方案。

## 13. 第十阶段：正式切换

### 13.1 停止旧 Mac

在旧 Mac：

```bash
hermes gateway stop
hermes gateway status
```

确认旧 Gateway 已停止后，不要再次启动。

### 13.2 启动新 Mac

在新 Mac：

```bash
hermes gateway start
hermes gateway status
```

观察日志：

```bash
tail -f "$HOME/.hermes/logs/gateway.log"
```

另一个终端观察错误日志：

```bash
tail -f "$HOME/.hermes/logs/gateway.error.log"
```

预期看到：

- QQ WebSocket 已连接；
- QQ Gateway 已 Ready；
- Codex phase hotfix 已加载；
- approval gateway callback 已 patched；
- QQ approval session 和 choices sender 已 patched；
- message snapshot store 已加载。

## 14. 上线验收清单

### 14.1 基础文字回复

- [ ] QQ 私聊能够触发并收到回复；
- [ ] QQ 群 mention 机器人能够触发回复；
- [ ] 最终回复只出现一次；
- [ ] 长任务可以看到 commentary/中间状态；
- [ ] 不出现重复 final。

### 14.2 未 mention 群消息

发送一条不 mention 机器人的普通文字：

- [ ] 机器人不主动回复；
- [ ] 消息进入快照；
- [ ] 后续 mention 机器人询问时，可从最近上下文或快照召回。

### 14.3 永久快照和混合检索

```text
/message-snapshot stats
/message-snapshot search 关键词
/message-snapshot search message_id=<精确消息ID>
/message-snapshot search field_path=<字段路径> value=<精确值>
```

- [ ] SQLite 正常；
- [ ] FTS5 可用；
- [ ] 精确字段查询正常；
- [ ] 中文模糊查询正常；
- [ ] 数据库位于
  `~/.hermes/message-snapshots/snapshots.sqlite3`。

### 14.4 多媒体和文件

依次测试：

1. 不 mention 发送图片；
2. 不 mention 发送文件；
3. 引用图片并 mention 机器人；
4. 引用文件并要求处理；
5. 执行 `/message-snapshot restore <id>`。

验收标准：

- [ ] QQ 已投递的多媒体事件进入快照；
- [ ] `link` 模式只保存链接和元数据；
- [ ] 显式恢复后才下载文件；
- [ ] Codex 能读取已经恢复的本地文件；
- [ ] 链接失效时明确报告下载失败，不虚构恢复结果。

### 14.5 QQ 审批桥接

默认 `auto_review` 下，符合既定策略的低风险操作可能不会出现 QQ 审批卡，
这是正常行为。

手工验收时临时将 Codex 配置改为：

```toml
approvals_reviewer = "user"
```

然后：

```bash
hermes gateway restart
```

测试：

- 联网命令；
- 额外目录写入；
- 文件变更；
- Computer Use 操作 Notes 等非硬性禁止的 App。

验收标准：

- [ ] QQ 收到审批请求；
- [ ] 本次允许可以继续当前请求；
- [ ] 会话允许可以在当前会话复用；
- [ ] 只有 Codex 提供持久策略修订时才显示“始终允许同类”；
- [ ] 非请求发起者不能批准共享群会话中的请求；
- [ ] 拒绝和超时均安全失败；
- [ ] 审批后原 Codex turn 能继续执行。

测试结束后恢复：

```toml
approvals_reviewer = "auto_review"
```

并执行：

```bash
hermes gateway restart
```

## 15. 回滚

### 15.1 整体切回旧 Mac

新 Mac：

```bash
hermes gateway stop
```

旧 Mac：

```bash
hermes gateway start
```

任何时候都必须保证只有一台机器使用同一 QQ Bot 凭据运行。

### 15.2 单独禁用热修复

```bash
hermes plugins disable message-snapshot-store
hermes plugins disable qqbot-connect-hotfix
hermes plugins disable codex-app-server-phase-hotfix
hermes gateway restart
```

禁用 `message-snapshot-store` 不会删除 SQLite 数据。只有明确决定销毁数据时，
才单独删除 `~/.hermes/message-snapshots`。

## 16. 后续更新

### 16.1 更新 Codex

继续使用官方安装器获取当时的最新稳定版：

```bash
curl -fsSL https://chatgpt.com/codex/install.sh | sh

which -a codex
codex --version
codex --strict-config doctor --summary --ascii
codex login status
```

Codex App 使用应用内自动更新。

每次更新后都检查 `which -a codex`，避免终端与 Hermes Gateway 调用不同的
Codex 副本。

### 16.2 更新持久化热修复

```bash
cd "$HOME/src/hermes-dispatch"

git switch main
git pull --ff-only origin main

scripts/install-plugins.sh "$HOME/.hermes" \
  codex-app-server-phase-hotfix \
  qqbot-connect-hotfix \
  message-snapshot-store
```

随后：

1. 运行第 9.3 节的全部回归测试；
2. 测试全部通过后执行：

```bash
hermes gateway restart
hermes gateway status
```

### 16.3 记录实际部署状态

每次部署或更新后记录：

```bash
codex --version
hermes --version

cd "$HOME/src/hermes-dispatch"
git rev-parse HEAD
git status --short
```

这些值仅用于运维追踪，不用于锁定后续安装版本。

## 17. 最终完成判据

只有同时满足以下条件，迁移才算完成：

- [ ] Codex CLI、Codex App 和 Hermes 均为部署时的最新稳定版本；
- [ ] 终端与 launchd 使用同一个 Codex 可执行文件；
- [ ] Codex 已登录且严格配置检查通过；
- [ ] Hermes 使用 `openai-codex + codex_app_server`；
- [ ] 三个持久化插件来自最新 `main` 分支并已启用；
- [ ] 所有插件回归测试通过；
- [ ] QQ 文本、未 mention 上下文、文件和多媒体测试通过；
- [ ] 最终回复不重复，中间状态可见；
- [ ] SQLite 快照和混合检索正常；
- [ ] QQ 审批请求能够显示、绑定正确用户并继续原任务；
- [ ] 新旧 Gateway 不会同时运行；
- [ ] 旧机仍保留可用的短期回滚副本。
