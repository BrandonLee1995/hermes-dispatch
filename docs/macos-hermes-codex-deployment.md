# macOS Hermes + Codex 快速部署手册

本文用于在一台新的 Mac mini 上，从零部署 Hermes、Codex CLI、Codex App、
QQ 渠道和持久化兼容插件。WhatsApp 当前不使用，部署时保持关闭。每个部门使用
独立的 macOS 登录账号；部门内
还可用 Hermes profile 建立多个小组专用 Agent。

本文不是旧 Mac 迁移或备份恢复手册。命令不固定 Hermes、Codex 或插件版本，
始终安装执行时的最新稳定版。

## 1. 部署边界

- 每个 macOS 部门账号独立保存 `~/.codex`、`~/.hermes`、Keychain、会话和消息快照。
- Codex App 可全机安装一次，但每个部门账号必须分别登录并授予 macOS 权限。
- 不同 Gateway 不得同时使用同一套 QQ Bot 身份，否则会抢事件或重复回复。
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
QQ，最终权限边界由 Codex 配置和每次审批决定。

取消 Codex app-server 墙钟限制和 session-project 映射都由 Hermes hotfix 的 `.env`
变量控制，不需要也不应向 `~/.codex/config.toml` 添加私有的 timeout、project 或
thread 配置键。

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
hermes config set display.platforms.qqbot.streaming true
hermes config set display.platforms.qqbot.tool_progress new

hermes config set group_sessions_per_user false
hermes config set session_reset.mode none
hermes config set approvals.mode smart
hermes config set approvals.mcp_reload_confirm false
hermes config set agent.gateway_timeout 7200
hermes config set agent.gateway_timeout_warning 900
hermes config set agent.restart_drain_timeout 300

hermes config set platforms.qqbot.enabled true
hermes config set platforms.qqbot.extra.group_policy open

hermes config set platforms.whatsapp.enabled false

hermes config check
```

关键点：

- `qqbot-connect-hotfix` 1.8.0 起，QQ 私聊通过官方
  `/v2/users/{openid}/stream_messages` 协议更新同一条消息，并在 turn final 时封口；群聊
  不使用该 C2C 端点，仍走原有被动回复路径。插件会把同一入站消息的 `input_notify`
  限制为一次，避免长任务在 final 前耗尽被动回复额度。
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

# WhatsApp 当前不使用；必须与 config.yaml 一起保持 false，避免环境变量反向覆盖
WHATSAPP_ENABLED=false

# QQ 长期消息快照
MESSAGE_SNAPSHOT_MEDIA_STORAGE=link
MESSAGE_SNAPSHOT_CONTEXT_MESSAGES=20
MESSAGE_SNAPSHOT_CONTEXT_TOKENS=4000
MESSAGE_SNAPSHOT_SEARCH_CANDIDATES=200

# Codex app-server 长周期任务（0 = 不设墙钟截止）
HERMES_CODEX_APP_SERVER_TURN_TIMEOUT_SECONDS=0

# Hermes session → Codex project/thread 持久映射
HERMES_CODEX_SESSION_PROJECTS_ENABLED=true
# 为安装 hotfix 前已存在的 channel session 自动补建项目（默认 true）
HERMES_CODEX_SESSION_PROJECTS_BACKFILL=true
# 将项目一次性注册到 Codex App 侧边栏（桌面账号部署时启用）
HERMES_CODEX_APP_REGISTER_PROJECTS=true
# 可选：Gateway 的 PATH 找不到 codex 时填写绝对路径
# HERMES_CODEX_APP_CLI=/绝对路径/codex
# 可选：仅这些平台用户可以通过提示词或命令切换项目
HERMES_CODEX_PROJECT_ADMIN_USERS=<QQ管理员openid，多个用逗号分隔>
# 可选：允许用户选择的项目别名和目录根
HERMES_CODEX_PROJECT_ALIASES={"finance":"/绝对路径/finance"}
HERMES_CODEX_PROJECT_ALLOWED_ROOTS=/绝对路径/部门项目根目录
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
- `WHATSAPP_ENABLED=false` 必须保留；仅设置 `config.yaml` 为 false 不足以抵消旧 `.env`
  中的 `WHATSAPP_ENABLED=true`。
- `MESSAGE_SNAPSHOT_MEDIA_STORAGE=link` 对 QQ 保存链接和元数据。
- Hermes 0.20.0 原生 Codex app-server 固定在 600 秒截止；上面的变量由
  `codex-app-server-phase-hotfix` 1.8.3 读取。多个聊天各自持有独立 Codex session，
  不共享 deadline 或 final 状态；同一聊天仍服从 `display.busy_input_mode`。
- 1.8.3 默认在 `$HERMES_HOME/codex-projects/<session_key>` 创建同名 Codex 项目；
  同一 `session_key` 后续经 `/new`、`/reset` 产生的新 `session_id` 会在该项目中创建
  同名 thread，Gateway 重启或缓存淘汰则恢复原 thread。1.6.x 的首次 session ID
  目录会在首次访问时自动迁移。
- `HERMES_CODEX_SESSION_PROJECTS_BACKFILL=true` 会在插件加载时读取 Hermes 自身的
  `sessions.json`，为安装前已有的 QQ 等 channel route 补建项目和映射；该
  route 下一条真实消息再按当前 session ID 创建 thread，不猜测或搬运归属不明的旧 thread。
- `HERMES_CODEX_APP_REGISTER_PROJECTS=true` 会在首个 Codex turn 后异步调用官方跨平台
  入口 `codex app <项目目录>`，使目录进入 Codex App 侧边栏；成功后写入映射数据库，
  避免每轮重复拉起 App，且 Desktop 启动不会阻塞消息回复。macOS 后台 Gateway 会通过
  `launchctl asuser` 进入当前登录账号的 Aqua 会话；Linux/Windows 则直接调用 CLI。
  不要在无桌面会话或容器内启用；容器部署应由宿主机同一桌面账号
  对宿主机映射目录执行 `codex app <路径>`。Windows 目录名会把非法的半角 `:` 替换为
  全角 `：`，数据库中的 `project_name` 仍为原始完整 session key。
- `HERMES_CODEX_PROJECT_ADMIN_USERS`、别名和允许根只在需要通过提示词切换项目时设置；
  不需要切换时可以省略。`QQ_ALLOW_ALL_USERS=true` 时不要把管理员列表设为 `*`。

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
  message-snapshot-store
```

启用插件和消息检索工具集：

```bash
hermes plugins enable openai-codex
hermes plugins enable codex-app-server-phase-hotfix --no-allow-tool-override
hermes plugins enable qqbot-connect-hotfix --no-allow-tool-override
hermes plugins enable message-snapshot-store --no-allow-tool-override
hermes plugins disable whatsapp-bridge-policy-hotfix

hermes tools enable --platform qqbot message_snapshot
hermes tools enable --platform qqbot codex_session_project
hermes tools list --platform qqbot
```

三项插件共同提供：

- Codex app-server 阶段消息、长周期等待、媒体回传和审批兼容；
- Codex 项目按稳定 channel session 归档，thread 按 Hermes session ID 命名并可恢复；
- QQ 单次 final、群被动消息上下文、引用媒体和审批按钮兼容；
- QQ SQLite 长期快照、精确过滤、FTS5/BM25、模糊召回和恢复。

## 8. 运行插件回归测试

```bash
cd "$HOME/src/hermes-dispatch"
HERMES_PY="$HOME/.hermes/hermes-agent/venv/bin/python"

"$HERMES_PY" plugins/codex-app-server-phase-hotfix/test_hotfix.py
"$HERMES_PY" plugins/qqbot-connect-hotfix/test_hotfix.py
"$HERMES_PY" plugins/qqbot-connect-hotfix/test_expired_reply.py
"$HERMES_PY" plugins/qqbot-connect-hotfix/test_media_reply.py
"$HERMES_PY" plugins/qqbot-connect-hotfix/test_group_roundtrip.py
"$HERMES_PY" plugins/qqbot-connect-hotfix/test_streaming.py
"$HERMES_PY" plugins/message-snapshot-store/test_store.py
"$HERMES_PY" plugins/message-snapshot-store/test_capture.py
"$HERMES_PY" plugins/message-snapshot-store/test_materialize.py
"$HERMES_PY" plugins/message-snapshot-store/test_quoted_attachment.py
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

安装并启动用户级服务：

```bash
hermes gateway install --force --no-start-now --start-on-login
hermes gateway start
hermes gateway status
hermes status
hermes logs -f
```

启动日志应显示：

- QQ adapter 已连接并到达 `Ready`；
- 没有 `Connecting to whatsapp` 或 WhatsApp reconnect 日志；
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
允许列表和端口：

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
hermes -p sales config set platforms.whatsapp.enabled false
hermes -p sales config set agent.gateway_timeout 7200
hermes -p sales config set agent.gateway_timeout_warning 900
hermes -p sales config set agent.restart_drain_timeout 300
hermes -p sales plugins enable codex-app-server-phase-hotfix --no-allow-tool-override
hermes -p sales plugins enable qqbot-connect-hotfix --no-allow-tool-override
hermes -p sales plugins enable message-snapshot-store --no-allow-tool-override
hermes -p sales plugins disable whatsapp-bridge-policy-hotfix
hermes -p sales tools enable --platform qqbot message_snapshot
hermes -p sales tools enable --platform qqbot codex_session_project
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
4. 从 QQ 触发联网、文件写入或 Computer Use：审批卡能回传并由发起人审批。
5. Codex 会话实际调用一次 `hermes-tools` 工具。
6. QQ 私聊运行一次超过 30 分钟的前台任务：10 分钟处不出现 600 秒 deadline，最终
   只回传一次；任务运行时从另一个群发起短任务，两个 session 分别完成且不串线。
7. QQ 私聊运行 `/codex-project status`：项目名等于完整 Hermes `session_key`，且
   `codex_app_registration.status` 为 `registered`；Codex App 侧边栏出现该项目。
   Gateway 重启后继续对话，thread ID 不变；执行 `/new` 后项目不变且出现以新
   `session_id` 命名的新 thread。
8. 如配置项目别名，由管理员要求“将当前会话关联到 finance 项目”，下一轮 cwd 切换
    且 thread 历史连续；非管理员执行同一操作必须被拒绝。
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
rg -n '^(WHATSAPP_ENABLED|HERMES_CODEX_APP_SERVER_TURN_TIMEOUT_SECONDS|HERMES_CODEX_SESSION_PROJECTS_ENABLED|HERMES_CODEX_SESSION_PROJECTS_BACKFILL|HERMES_CODEX_APP_REGISTER_PROJECTS)=' "$(hermes config env-path)"
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
hermes config unset agent.gateway_timeout
hermes config unset agent.gateway_timeout_warning
hermes config unset agent.restart_drain_timeout
nano "$(hermes config env-path)"  # 将 HERMES_CODEX_APP_SERVER_TURN_TIMEOUT_SECONDS 改为 600
hermes gateway restart
```

这样只恢复 Codex 600 秒墙钟和 Hermes 默认 Gateway 超时，不移除阶段消息、图片、审批
与 session-project 能力。

只停止新的 session-project 映射和 Desktop 注册：

```bash
nano "$(hermes config env-path)"
# 设置 HERMES_CODEX_SESSION_PROJECTS_ENABLED=false
# 设置 HERMES_CODEX_APP_REGISTER_PROJECTS=false
hermes gateway restart
```

禁用插件不会删除 `$HERMES_HOME/state/codex-session-projects.sqlite3`、
`$HERMES_HOME/codex-projects` 或 `PROJECT_MEMORY.md`；重新启用后可以继续恢复映射。

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
