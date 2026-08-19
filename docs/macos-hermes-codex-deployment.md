# macOS Hermes + Codex 快速部署手册

本文用于在一台新的 Mac mini 上，从零部署 Hermes、Codex CLI、Codex App、
QQ/WhatsApp 渠道和持久化兼容插件。每个部门使用独立的 macOS 登录账号；部门内
还可用 Hermes profile 建立多个小组专用 Agent。

本文不是旧 Mac 迁移或备份恢复手册。命令不固定 Hermes、Codex 或插件版本，
始终安装执行时的最新稳定版。

## 1. 部署边界

- 每个 macOS 部门账号独立保存 `~/.codex`、`~/.hermes`、Keychain、会话和消息快照。
- Codex App 可全机安装一次，但每个部门账号必须分别登录并授予 macOS 权限。
- 不同 Gateway 不得同时使用同一套 QQ Bot 或 WhatsApp 身份，否则会抢事件或重复回复。
- 不要提交 `.env`、真实手机号、QQ 标识、Bot 密钥或授权凭据。
- Hermes 更新后重新安装最新 hotfix 并执行回归测试；不要修改安装目录作为永久修复。

## 2. 新 Mac 前置安装

管理员先安装 Apple Command Line Tools：

```bash
xcode-select --install
```

安装 Homebrew：

```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
```

新 Mac mini 使用 Apple Silicon，配置 Homebrew PATH：

```bash
echo 'eval "$(/opt/homebrew/bin/brew shellenv)"' >> "$HOME/.zprofile"
eval "$(/opt/homebrew/bin/brew shellenv)"
```

如果是 Intel Mac，把 `/opt/homebrew/bin/brew` 改为 `/usr/local/bin/brew`。

安装 Node.js 和常用工具：

```bash
brew install node git ripgrep
node --version
npm --version
git --version
rg --version
```

Homebrew 可由管理员全机安装一次；每个部门账号仍需在自己的 `~/.zprofile` 中加入
上述 `brew shellenv` 行。

## 3. 每个部门账号安装 Codex

切换到目标部门的 macOS 账号后执行。先确认没有误用管理员账号：

```bash
whoami
echo "$HOME"
```

安装最新 Codex CLI：

```bash
curl -fsSL https://chatgpt.com/codex/install.sh | sh
echo 'export PATH="$HOME/.local/bin:$PATH"' >> "$HOME/.zprofile"
source "$HOME/.zprofile"
command -v codex
codex --version
```

登录该部门自己的 ChatGPT/Codex 账号，并安装或打开 Codex App：

```bash
codex login
codex login status
codex app
```

编辑 `~/.codex/config.toml`，保留已有内容并加入默认权限策略：

```toml
approval_policy = "on-request"
approvals_reviewer = "auto_review"
sandbox_mode = "workspace-write"
cli_auth_credentials_store = "keyring"
```

不要在 Hermes 中再设置一套宽松审批策略；Hermes 负责把 Codex 的审批请求转发到
QQ/WhatsApp，最终权限边界由 Codex 配置和每次审批决定。

在“系统设置 → 隐私与安全性”中，按实际启用能力分别为 Codex App/终端批准：

- 辅助功能；
- 屏幕与系统音频录制；
- 自动化；
- 文件与文件夹。

## 4. 安装并初始化 Hermes

```bash
curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash
source "$HOME/.zprofile"
command -v hermes
hermes --version
hermes setup
hermes gateway setup
hermes gateway stop
```

先运行向导，让当前 Hermes 生成完整配置结构，再用命令调整。不要直接复制旧版本的
整份 `config.yaml`。

## 5. 用命令配置 `config.yaml`

以下命令均写入当前部门账号的 `~/.hermes/config.yaml`：

```bash
hermes config migrate

hermes config set model.provider openai-codex
hermes config set model.base_url https://chatgpt.com/backend-api/codex
hermes config set model.openai_runtime codex_app_server
hermes config set agent.max_turns 150
hermes config set agent.reasoning_effort medium
hermes config set compression.codex_app_server_auto native

hermes config set display.interim_assistant_messages true
hermes config set display.streaming true
hermes config set display.platforms.qqbot.interim_assistant_messages true
hermes config set display.platforms.qqbot.streaming false
hermes config set display.platforms.qqbot.tool_progress false

hermes config set group_sessions_per_user false
hermes config set session_reset.mode none
hermes config set approvals.mode smart
hermes config set approvals.mcp_reload_confirm false
hermes config set agent.gateway_timeout 7200
hermes config set agent.gateway_timeout_warning 900
hermes config set agent.restart_drain_timeout 300

hermes config set platforms.qqbot.enabled true
hermes config set platforms.qqbot.extra.group_policy open

hermes config set platforms.whatsapp.enabled true
hermes config set platforms.whatsapp.dm_policy allowlist
hermes config set platforms.whatsapp.group_policy open
hermes config set platforms.whatsapp.require_mention true
hermes config set platforms.whatsapp.send_read_receipts true

hermes config check
```

关键点：

- QQ 开启 commentary，但关闭逐 token streaming，既能看到中间进度，又避免 final 重复发送。
- WhatsApp 的 mention 路由必须写在 `platforms.whatsapp.require_mention`；不要写到
  `display.platforms.whatsapp`。
- `group_sessions_per_user=false` 让同一群共用上下文；审批 hotfix 仍会校验发起人。
- `approvals.mode=smart` 让 Hermes 自动判断危险命令：低风险命令可自动放行，不确定的
  请求才发送人工审批；它不替代 Codex app-server 自身的审批策略。
- `agent.gateway_timeout=7200` 将 Gateway 的无活动保护延长到 2 小时；`/stop` 和消息
  interrupt/steer 仍可终止或调整任务。
- `agent.gateway_timeout_warning=900` 会在连续 15 分钟无活动时发送状态提醒；它不是
  Agent final，也不会释放会话。需要减少提醒时可改为 `3600`，设为 `0` 则关闭提醒。
- `agent.restart_drain_timeout=300` 是独立的重启排空窗口。Hermes 0.20.0 默认值为 0，
  显式重启会立即强制结束其他会话；设为 300 后先等待最多 5 分钟再强制退出。
- 标量使用 `hermes config set`。工具集列表使用第 7 节的 `hermes tools enable`，不要把
  JSON 字符串写进 `platform_toolsets`。

审批历史积累后，可生成命令 allowlist 建议。默认只展示建议，不写入配置：

```bash
hermes approvals suggest
```

人工审核编号后，再选择性应用，例如：

```bash
hermes approvals suggest --apply 1,2
```

`suggest` 是 `hermes approvals` 的子命令，不是 `approvals.mode` 的取值；破坏性命令
不会被加入建议列表。

## 6. 配置 `.env`

打开 Hermes 当前使用的环境文件：

```bash
hermes config env-path
nano "$(hermes config env-path)"
```

加入以下模板并替换占位值：

```dotenv
# QQ
QQ_APP_ID=<当前部门QQ机器人AppID>
QQ_CLIENT_SECRET=<当前部门QQ机器人密钥>
QQ_ALLOWED_USERS=
QQ_GROUP_ALLOWED_USERS=*
QQ_ALLOW_ALL_USERS=true
QQBOT_GROUP_RECEIVE_MODE=all
QQBOT_GROUP_MESSAGE_CREATE_MODE=mention
QQBOT_GROUP_CONTEXT_MESSAGES=20
QQBOT_GROUP_CONTEXT_BUFFER_MESSAGES=100
QQBOT_GROUP_CONTEXT_CHARS=4000
QQBOT_GROUP_CONTEXT_SUMMARY_CHARS=1200

# WhatsApp
WHATSAPP_ENABLED=true
WHATSAPP_MODE=bot
WHATSAPP_ALLOWED_USERS=<带国际区号的号码，逗号分隔且不加+号>
WHATSAPP_ALLOW_ALL_USERS=true

# QQ + WhatsApp 长期消息快照
MESSAGE_SNAPSHOT_MEDIA_STORAGE=link
MESSAGE_SNAPSHOT_CONTEXT_MESSAGES=20
MESSAGE_SNAPSHOT_CONTEXT_TOKENS=4000
MESSAGE_SNAPSHOT_SEARCH_CANDIDATES=200

# Codex app-server 长周期任务（0 = 不设墙钟截止）
HERMES_CODEX_APP_SERVER_TURN_TIMEOUT_SECONDS=0
```

保存后收紧权限：

```bash
chmod 600 "$(hermes config env-path)"
```

说明：

- `QQ_GROUP_ALLOWED_USERS=*` 是 QQ 群聊启用的关键本地设置。
- `QQ_ALLOW_ALL_USERS=true` 与空的 `QQ_ALLOWED_USERS` 表示不再使用 QQ 私聊用户白名单；
  机器人可接收所有 QQ 用户的私聊消息。
- QQ 群主还必须在群机器人设置中开启“获取全部群消息”。未送达 Gateway 的消息无法
  被任何 hotfix 或数据库捕获。
- `QQBOT_GROUP_MESSAGE_CREATE_MODE=mention` 表示未 mention 消息只进入上下文和快照，
  不触发 Agent。
- `WHATSAPP_ALLOW_ALL_USERS=true` 是 `group_policy=open` 的启动确认；
  `dm_policy=allowlist` 仍限制私聊用户。
- 不要在 `.env` 重复设置 `WHATSAPP_DM_POLICY`、`WHATSAPP_GROUP_POLICY` 或
  `WHATSAPP_REQUIRE_MENTION`，避免环境变量覆盖第 5 节的配置。
- `MESSAGE_SNAPSHOT_MEDIA_STORAGE=link` 对 QQ 保存链接和元数据；WhatsApp 经 Baileys
  解密后的媒体会被插件强制镜像到内容寻址归档，因为临时缓存路径不能长期恢复。
- Hermes 0.20.0 原生 Codex app-server 固定在 600 秒截止；上面的变量由
  `codex-app-server-phase-hotfix` 1.5.0 读取。多个聊天各自持有独立 Codex session，
  不共享 deadline 或 final 状态；同一聊天仍服从 `display.busy_input_mode`。

可选的 WhatsApp 管理命令权限变量：

```dotenv
WHATSAPP_ALLOW_ADMIN_FROM=<允许私聊管理命令的号码>
WHATSAPP_USER_ALLOWED_COMMANDS=<普通私聊用户允许的命令>
WHATSAPP_GROUP_ALLOW_ADMIN_FROM=<允许群管理命令的号码>
WHATSAPP_GROUP_USER_ALLOWED_COMMANDS=<普通群成员允许的命令>
```

## 7. 安装最新持久化插件

拉取 `hermes-dispatch` 最新 `main`：

```bash
mkdir -p "$HOME/src"
if [[ -d "$HOME/src/hermes-dispatch/.git" ]]; then
  git -C "$HOME/src/hermes-dispatch" pull --ff-only origin main
else
  git clone --branch main --single-branch \
    https://github.com/mwe-support/hermes-dispatch.git \
    "$HOME/src/hermes-dispatch"
fi
cd "$HOME/src/hermes-dispatch"
```

将兼容层复制到持久化目录：

```bash
scripts/install-plugins.sh "$HOME/.hermes" \
  codex-app-server-phase-hotfix \
  qqbot-connect-hotfix \
  message-snapshot-store \
  whatsapp-bridge-policy-hotfix
```

启用插件和消息检索工具集：

```bash
hermes plugins enable openai-codex
hermes plugins enable codex-app-server-phase-hotfix --no-allow-tool-override
hermes plugins enable qqbot-connect-hotfix --no-allow-tool-override
hermes plugins enable message-snapshot-store --no-allow-tool-override
hermes plugins enable whatsapp-bridge-policy-hotfix --no-allow-tool-override

hermes tools enable --platform qqbot message_snapshot
hermes tools enable --platform whatsapp message_snapshot
hermes tools list --platform qqbot
hermes tools list --platform whatsapp
```

四个兼容层分别用于：

- Codex app-server 阶段消息、长周期等待、媒体回传和审批兼容；
- QQ 单次 final、群被动消息上下文、引用媒体和审批按钮兼容；
- QQ/WhatsApp SQLite 长期快照、精确过滤、FTS5/BM25、模糊召回和恢复；
- WhatsApp DM allowlist、开放群聊、require-mention 与 Baileys bridge 兼容。

## 8. 运行插件回归测试

```bash
cd "$HOME/src/hermes-dispatch"
HERMES_PY="$HOME/.hermes/hermes-agent/venv/bin/python"

"$HERMES_PY" plugins/codex-app-server-phase-hotfix/test_hotfix.py
"$HERMES_PY" plugins/qqbot-connect-hotfix/test_hotfix.py
"$HERMES_PY" plugins/qqbot-connect-hotfix/test_expired_reply.py
"$HERMES_PY" plugins/qqbot-connect-hotfix/test_media_reply.py
"$HERMES_PY" plugins/qqbot-connect-hotfix/test_group_roundtrip.py
"$HERMES_PY" plugins/message-snapshot-store/test_store.py
"$HERMES_PY" plugins/message-snapshot-store/test_capture.py
"$HERMES_PY" plugins/message-snapshot-store/test_materialize.py
"$HERMES_PY" plugins/message-snapshot-store/test_quoted_attachment.py
"$HERMES_PY" plugins/message-snapshot-store/test_whatsapp_capture.py
"$HERMES_PY" plugins/whatsapp-bridge-policy-hotfix/test_hotfix.py
```

任一测试失败都先停止部署，不启动生产 Gateway。

## 9. 让 Codex app-server 接收 Hermes MCP

仅设置 `model.openai_runtime=codex_app_server` 不会自动保证 Codex 已注册 Hermes MCP。
从默认 profile 执行：

```bash
hermes profile use default
env -u HERMES_HOME hermes --cli
```

在 Hermes CLI 中输入：

```text
/codex-runtime on
/exit
```

验证 Codex 侧注册结果：

```bash
codex mcp get hermes-tools --json
codex mcp list
```

必须能看到 `hermes-tools`。然后新建一次 Codex 会话，实际调用 Hermes 提供的工具验证，
不要只检查配置文件。

多个 Hermes profile 共用当前 macOS 用户的 `~/.codex/config.toml`。因此只在默认 profile
完成一次上述迁移；不要从命名 profile 重复迁移并把 `hermes-tools` 的 `HERMES_HOME`
固定到某一个小组。Gateway 启动 Codex 时会把当前 profile 的环境传给 MCP 子进程。

## 10. 启动 Gateway

首次启用 WhatsApp 时先完成设备配对：

```bash
hermes whatsapp
```

随后安装并启动用户级服务：

```bash
hermes gateway install --force --no-start-now --start-on-login
hermes gateway start
hermes gateway status
hermes status
hermes logs -f
```

首次启用 WhatsApp 时，按 Hermes 提示扫码关联 WhatsApp 设备。启动日志应显示：

- QQ/WhatsApp adapter 已连接；
- WhatsApp hotfix 使用插件目录下的 runtime bridge；
- `message-snapshot-store` 已加载；
- Codex app-server 已启动且 `hermes-tools` 可用；
- 无重复 Bot 凭据、端口冲突或数据库权限错误。

## 11. 部门内创建多个小组 profile

Hermes profile 是同一 macOS 部门账号内的小组级隔离层。每个 profile 有独立的
`config.yaml`、`.env`、SOUL、会话、记忆、插件目录、日志和消息快照；它们仍共享该
macOS 用户的 Codex 登录和 `~/.codex/config.toml`。

先停止默认 Gateway，再从已配置的 default 克隆模板：

```bash
hermes gateway stop
hermes profile create sales --clone --description "销售小组专用 Agent"
hermes profile create finance --clone --description "财务小组专用 Agent"
hermes profile list
hermes profile show sales
```

`--clone` 会复制配置和 `.env`。在启动任何小组 Gateway 前，必须分别替换 Bot 凭据、
允许列表和端口；使用 WhatsApp 的 profile 还要分别执行 `hermes -p <名称> whatsapp`
完成独立配对：

```bash
nano "$HOME/.hermes/profiles/sales/.env"
nano "$HOME/.hermes/profiles/finance/.env"
```

为同时运行的独立 Gateway 设置不同端口，例如：

```dotenv
# sales
API_SERVER_PORT=8643
```

```dotenv
# finance
API_SERVER_PORT=8644
```

把最新版兼容插件安装到每个 profile：

```bash
cd "$HOME/src/hermes-dispatch"
scripts/install-plugins.sh "$HOME/.hermes/profiles/sales"
scripts/install-plugins.sh "$HOME/.hermes/profiles/finance"
```

用 `-p` 对指定 profile 执行配置和插件命令：

```bash
hermes -p sales config set platforms.qqbot.enabled true
hermes -p sales config set platforms.whatsapp.require_mention true
hermes -p sales config set agent.gateway_timeout 7200
hermes -p sales config set agent.gateway_timeout_warning 900
hermes -p sales config set agent.restart_drain_timeout 300
hermes -p sales plugins enable codex-app-server-phase-hotfix --no-allow-tool-override
hermes -p sales plugins enable qqbot-connect-hotfix --no-allow-tool-override
hermes -p sales plugins enable message-snapshot-store --no-allow-tool-override
hermes -p sales plugins enable whatsapp-bridge-policy-hotfix --no-allow-tool-override
hermes -p sales tools enable --platform qqbot message_snapshot
hermes -p sales tools enable --platform whatsapp message_snapshot
hermes -p sales config check
```

为每个小组安装并启动独立 LaunchAgent：

```bash
hermes -p sales gateway install --force --no-start-now --start-on-login
hermes -p sales gateway start
hermes -p sales gateway status

hermes -p finance gateway install --force --no-start-now --start-on-login
hermes -p finance gateway start
hermes -p finance gateway status
```

日常管理命令：

```bash
hermes profile list
hermes profile use sales
hermes -p sales logs -f
hermes -p sales gateway restart
hermes profile use default
```

如果各小组不需要独立机器人连接，不要启动多套 Gateway；可只保留 default Gateway，
再单独设计 Hermes 的 multiplex/profile route。该路由依赖实际群 ID，不应直接照抄模板。

## 12. 最小验收

按顺序完成以下实测：

1. QQ 群 mention Bot：Agent 响应一次，能看到必要的中间进度，final 不重复。
2. QQ 群不 mention：Agent 不响应；随后 mention 询问上一条消息，能从快照上下文恢复。
3. QQ 发送图片、文件并引用：`/message-snapshot stats` 有记录，按 ID 可检索或恢复。
4. WhatsApp 群不 mention：只入库不响应；mention 后能引用之前的文本上下文。
5. WhatsApp 图片/文件：SQLite 有附件记录，媒体归档存在且可恢复。
6. 从 QQ/WhatsApp 触发联网、文件写入或 Computer Use：审批卡能回传并由发起人审批。
7. Codex 会话实际调用一次 `hermes-tools` 工具。
8. QQ 私聊运行一次超过 30 分钟的前台任务：10 分钟处不出现 600 秒 deadline，最终
   只回传一次；任务运行时从另一个群发起短任务，两个 session 分别完成且不串线。
9. 重启 Mac 并登录该部门账号：Gateway 自动恢复，日志无重复实例和端口冲突。

常用检查：

```bash
hermes config check
hermes plugins list
hermes tools --summary
hermes gateway status
codex mcp list
hermes logs errors
hermes config get agent.gateway_timeout
hermes config get agent.gateway_timeout_warning
hermes config get agent.restart_drain_timeout
```

## 13. 更新与回滚

更新到当前最新版：

```bash
curl -fsSL https://chatgpt.com/codex/install.sh | sh
hermes update --yes
git -C "$HOME/src/hermes-dispatch" pull --ff-only origin main
cd "$HOME/src/hermes-dispatch"
scripts/install-plugins.sh "$HOME/.hermes"
```

命名 profile 也要分别重新安装插件，然后重复第 8、9、12 节并重启对应 Gateway。

只回滚 Codex 长任务机制：

```bash
hermes plugins disable codex-app-server-phase-hotfix
hermes config unset agent.gateway_timeout
hermes config unset agent.gateway_timeout_warning
hermes config unset agent.restart_drain_timeout
nano "$(hermes config env-path)"  # 删除 HERMES_CODEX_APP_SERVER_TURN_TIMEOUT_SECONDS
hermes gateway restart
```

这会同时移除该插件提供的 Codex 阶段消息、图片与审批兼容；若仍需要这些能力，不要只为
恢复 600 秒墙钟而停用整个插件，可把环境变量改为明确的正秒数并保留插件。

只回滚兼容层而保留消息数据：

```bash
hermes plugins disable codex-app-server-phase-hotfix
hermes plugins disable qqbot-connect-hotfix
hermes plugins disable message-snapshot-store
hermes plugins disable whatsapp-bridge-policy-hotfix
hermes gateway restart
```

禁用 `message-snapshot-store` 不会删除数据库。只有明确决定销毁历史快照时，才单独删除
`$HERMES_HOME/message-snapshots`。
