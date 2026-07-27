# macOS 多部门账号 Hermes + Codex 可复用部署手册

## 1. 文档目标

本文用于在一台新的 Mac mini 上建立统一的 Hermes + Codex 运行模板。
不同部门人员使用各自的 macOS 登录账号登录这台 Mac mini 后，可以重复执行同一套
安装和配置步骤，并获得相互隔离的：

- Codex 登录状态；
- Codex CLI 配置和 Skills；
- Hermes 配置、会话、记忆和消息快照；
- QQ Bot 凭据和群聊上下文；
- Hermes Gateway `launchd` 用户服务；
- macOS Keychain 和 Computer Use 授权。

本文不是旧 Mac 的备份或数据迁移手册，也不要求复制任何旧机器状态。

官方入口：

- [Hermes Agent](https://github.com/NousResearch/hermes-agent)
- [Codex CLI](https://learn.chatgpt.com/docs/codex/cli)
- [Codex 配置](https://learn.chatgpt.com/docs/config-file/config-basic)
- [Codex 权限与审批](https://learn.chatgpt.com/docs/agent-approvals-security)

## 2. 版本策略

1. Codex CLI 和 Codex App 始终安装部署时的最新稳定版。
2. Hermes 始终使用官方安装器安装部署时的最新稳定版。
3. 持久化热修复始终拉取 `mwe-support/hermes-dispatch` 的最新 `main`
   分支。
4. 不在部署命令中固定 Codex、Hermes、插件或 Git commit 版本。
5. 实际版本号和 Git commit 只写入部署记录，用于排障。
6. 更新后必须重新运行本文的兼容性测试，不能只以“安装成功”作为完成标准。

## 3. 多账号部署模型

### 3.1 全机共享与用户隔离

| 项目 | 范围 | 位置或说明 |
|---|---|---|
| Codex App 程序 | 全机共享 | `/Applications/ChatGPT.app`，管理员安装一次 |
| Apple 命令行工具 | 全机共享 | 管理员安装一次 |
| Codex CLI | 每个用户独立 | `$HOME/.local/bin/codex` |
| Codex 配置 | 每个用户独立 | `$HOME/.codex/config.toml` |
| Codex 登录状态 | 每个用户独立 | 当前用户 Keychain，不复制 `auth.json` |
| Codex Skills/插件状态 | 每个用户独立 | `$HOME/.codex` 及 App 用户数据 |
| Hermes 运行时 | 每个用户独立 | `$HOME/.hermes/hermes-agent` |
| Hermes 配置和凭据 | 每个用户独立 | `$HOME/.hermes/config.yaml`、`.env` |
| Hermes 热修复 | 每个用户独立 | `$HOME/.hermes/plugins` |
| Hermes Gateway | 每个用户独立 | 当前用户的 `launchd` LaunchAgent |
| 消息快照 | 每个用户独立 | `$HOME/.hermes/message-snapshots` |
| macOS 系统权限 | 每个用户单独批准 | 辅助功能、屏幕录制、自动化等 |

“复用配置”是指每个部门账号执行相同的标准步骤和参数模板，不是让不同账号共同
读写同一份 `~/.codex`、`~/.hermes` 或 SQLite 数据库。

### 3.2 推荐账号模型

为每个部门建立独立的标准 macOS 用户，例如：

```text
department-a
department-b
department-c
```

每个账号应使用：

- 自己的 ChatGPT/Codex 部门账号；
- 自己的 QQ Bot App ID 和 Client Secret；
- 自己的 QQ 用户和群聊允许列表；
- 自己的 Hermes Gateway；
- 自己的消息快照数据库。

如果多个 macOS 账号使用同一套 QQ Bot 凭据，则任何时候只能启动其中一个
Gateway，否则可能出现重复回复、事件抢占和会话状态分裂。

## 4. 管理员一次性准备

本节只需由 Mac mini 管理员执行一次。

### 4.1 创建部门 macOS 账号

在“系统设置 → 用户与群组”中为每个部门建立独立的标准用户。

不建议所有部门共用一个 macOS 账号，因为以下状态需要隔离：

- Keychain 登录凭据；
- Codex 授权策略；
- QQ Bot 密钥；
- Hermes 会话和消息快照；
- macOS Computer Use 权限。

### 4.2 安装 Apple 命令行工具

```bash
xcode-select --install
```

安装后验证：

```bash
git --version
python3 --version
```

### 4.3 安装 Codex App

Codex App 程序安装在 `/Applications`，全机只需安装一次。可以从官方入口安装，
也可以先在管理员账号安装 Codex CLI，然后执行：

```bash
codex app
```

验收：

```bash
test -d "/Applications/ChatGPT.app" && echo "Codex App installed"
```

管理员安装 App 不会替部门用户完成 Codex 登录。每个部门账号首次使用时仍需在
自己的图形会话中登录。

## 5. 每个部门账号的完整安装步骤

以下各节必须在目标部门的 macOS 账号中执行。切换到下一个部门账号后，从本节
重新开始。

### 5.1 确认当前用户

```bash
whoami
echo "$HOME"
id
```

验收门槛：

- `whoami` 必须是目标部门账号；
- `$HOME` 必须指向该部门自己的 `/Users/<账号名>`；
- 不要在管理员账号中代替其他部门执行用户级安装。

### 5.2 安装最新稳定版 Codex CLI

```bash
curl -fsSL https://chatgpt.com/codex/install.sh | sh
```

设置用户 PATH：

```bash
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"

touch "$HOME/.zprofile"

grep -q 'HOME/.local/bin' "$HOME/.zprofile" || \
  printf '\nexport PATH="$HOME/.local/bin:$PATH"\n' >> "$HOME/.zprofile"

source "$HOME/.zprofile"
hash -r
```

检查：

```bash
which -a codex
command -v codex
codex --version
```

验收门槛：

- 当前用户应优先使用 `$HOME/.local/bin/codex`；
- 不允许 Hermes Gateway 优先解析到 Homebrew、npm 或其他旧副本；
- 版本号只记入部署记录，不写入配置模板。

### 5.3 登录该部门的 Codex 账号

```bash
codex login
codex login status
```

要求：

- 必须登录当前部门自己的 ChatGPT/Codex 账号；
- 推荐使用 macOS Keychain 保存凭据；
- 不从其他 macOS 用户复制 `~/.codex/auth.json`；
- 不共享其他部门的浏览器登录状态或 API 凭据。

### 5.4 首次打开 Codex App

```bash
codex app
```

在 Codex App 中确认当前显示的是本部门账号。

Codex CLI 和 Codex App可以共享当前 macOS 用户的登录状态，但不会跨 macOS
用户共享 Keychain。

### 5.5 配置 Codex 默认策略

```bash
mkdir -p "$HOME/.codex"
chmod 700 "$HOME/.codex"
nano "$HOME/.codex/config.toml"
```

合并以下公共配置模板。不固定 Codex 版本或模型：

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

验证：

```bash
codex --strict-config doctor --summary --ascii
```

权限职责：

- Codex 负责默认 sandbox、命令和文件审批策略；
- Hermes 负责把仍需人工处理的审批发送到 QQ；
- Hermes 不建立第二套永久授权数据库；
- 不设置 Hermes `approvals.mode: off`；
- 不启用 `/yolo`。

### 5.6 安装或启用 Codex App 插件

每个部门用户在 Codex App 的插件设置中安装或启用当时的最新版本：

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

不要复制其他用户的 Codex 插件缓存或自动生成的 MCP 运行时。

### 5.7 授予当前用户的 macOS 权限

每个部门用户必须在自己的图形会话中单独授予：

- 辅助功能；
- 屏幕录制；
- 自动化；
- 必要时的文件和文件夹访问。

QQ 中的 Codex 审批不能替代 macOS 系统权限。

Computer Use 还要求：

- 该部门账号当前处于已登录状态；
- 图形桌面未被锁定；
- 需要操作界面时，该账号应是当前活动图形会话。

后台登录但非活动桌面的账号不应被假定可以可靠执行 GUI 自动化。

### 5.8 安装最新 Hermes

```bash
curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash
```

刷新环境：

```bash
source "$HOME/.zprofile"
hash -r
hermes --version
```

Hermes 必须安装在当前用户自己的：

```text
$HOME/.hermes
```

不要将多个部门的 Hermes 数据目录指向同一个共享目录。

### 5.9 配置 Hermes 模型

```bash
hermes setup
```

选择：

- Provider：`openai-codex`
- Runtime：`codex_app_server`
- Model：部署时 Codex 当前支持的目标模型

不在公共模板中固定模型或 Codex 版本。

### 5.10 配置当前部门的 QQ Bot

```bash
hermes gateway setup
```

使用当前部门自己的 QQ Bot 配置。

如果配置过程启动了 Gateway，先停止：

```bash
hermes gateway stop
```

在热修复、MCP 和 launchd PATH 验收完成前，不启动正式服务。

### 5.11 写入公共 Hermes 行为配置

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

关键行为：

- agent 中间 commentary 可以回传 QQ；
- final 回复只发送一次；
- QQ 不进行逐 token 输出；
- 群聊保持共享上下文；
- 被动群消息只进入上下文和快照，不自动唤醒 agent；
- mention 机器人、回复机器人或命令才触发 agent。

### 5.12 创建该部门的 Hermes 环境文件

```bash
install -m 600 /dev/null "$HOME/.hermes/.env"
nano "$HOME/.hermes/.env"
```

填写当前部门自己的值：

```dotenv
QQ_APP_ID=
QQ_CLIENT_SECRET=

QQ_ALLOWED_USERS=
QQ_GROUP_ALLOWED_USERS=

# 只有该部门明确允许全部用户使用时才设置 true
QQ_ALLOW_ALL_USERS=false

TERMINAL_TIMEOUT=60
TERMINAL_LIFETIME_SECONDS=300
BROWSER_INACTIVITY_TIMEOUT=120
BROWSER_SESSION_TIMEOUT=300

MESSAGE_SNAPSHOT_MEDIA_STORAGE=link
MESSAGE_SNAPSHOT_CONTEXT_MESSAGES=20
MESSAGE_SNAPSHOT_CONTEXT_TOKENS=4000
```

禁止：

- 将某部门 `.env` 复制给另一个部门；
- 将 `.env` 提交到 Git；
- 在安装日志中打印 Client Secret；
- 让多个同时运行的 Gateway 使用同一 QQ Bot 凭据。

### 5.13 获取最新持久化热修复

每个部门用户使用自己的 Git checkout，可以避免多用户写同一个仓库产生所有权和
更新冲突：

```bash
mkdir -p "$HOME/src"
cd "$HOME/src"

git clone --branch main --single-branch \
  https://github.com/mwe-support/hermes-dispatch.git

cd hermes-dispatch
git pull --ff-only origin main
```

如果该用户已经克隆：

```bash
cd "$HOME/src/hermes-dispatch"
git switch main
git pull --ff-only origin main
```

不指定 tag、commit 或插件版本。

### 5.14 安装三个必需插件

```bash
cd "$HOME/src/hermes-dispatch"

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

验收门槛：三个插件必须全部显示为已启用。

### 5.15 运行热修复回归测试

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

任何测试失败都不能继续启动部门 Gateway。

### 5.16 配置 Hermes Tools MCP

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

MCP 命令中的 `$HOME` 必须属于当前部门账号，不能指向管理员或其他部门目录。

### 5.17 注册当前用户的 launchd Gateway

先确保当前 shell 使用正确 Codex：

```bash
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"

command -v codex
codex --version
```

注册但暂不启动：

```bash
hermes gateway install \
  --force \
  --no-start-now \
  --start-on-login
```

检查当前用户的 LaunchAgent：

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

- launchd 解析到的 Codex 必须与 `command -v codex` 一致；
- 解析路径必须位于当前部门用户的目录；
- 不能解析到另一个用户的 `.local/bin/codex`；
- 不能错误地优先使用旧 Homebrew/npm Codex。

如果不一致，修正 PATH 或移除重复安装后重新生成：

```bash
hermes gateway install --force --no-start-now --start-on-login
```

### 5.18 启动当前部门 Gateway

```bash
hermes gateway start
hermes gateway status
```

观察日志：

```bash
tail -f "$HOME/.hermes/logs/gateway.log"
```

另一个终端观察错误：

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

## 6. QQ 群设置

### 6.1 获取全部群消息

每个部门的 QQ Bot 都应由目标群的群主在机器人设置中开启：

```text
获取全部群消息
```

这是 QQ 服务端权限，不是 Hermes 配置。

已经为当前 Bot 开启且仍然有效时，不需要重复开启。

以下情况需要重新确认：

- 更换 QQ App ID；
- 新建机器人；
- 使用另一个部门的 Bot；
- 将 Bot 加入新的群；
- QQ 后台重置机器人权限。

没有被 QQ 投递给 Bot 的事件，Hermes 和热修复无法捕获。

### 6.2 被动消息与唤醒规则

开启全群消息后：

- 未 mention 文本可以进入上下文和快照；
- 未 mention 多媒体可以在 QQ 实际提供附件字段时进入快照；
- 未 mention 消息不会自动唤醒 agent；
- mention Bot、回复 Bot 或命令才触发 agent；
- mention 其他群成员不能误触发 Bot。

## 7. 消息快照与多媒体

每个部门账号拥有自己的数据库：

```text
$HOME/.hermes/message-snapshots/snapshots.sqlite3
```

普通上下文默认：

```dotenv
MESSAGE_SNAPSHOT_CONTEXT_MESSAGES=20
MESSAGE_SNAPSHOT_CONTEXT_TOKENS=4000
```

超出普通上下文的消息仍保存在 SQLite 中，除非明确删除。

### 7.1 混合检索

```text
/message-snapshot stats
/message-snapshot search 关键词
/message-snapshot search message_id=<精确消息ID>
/message-snapshot search field_path=<字段路径> value=<精确值>
/message-snapshot get <snapshot-id-or-message-id>
```

检索结合：

- 结构化精确过滤；
- SQLite FTS5/BM25；
- 子串匹配；
- CJK n-gram 模糊召回；
- RRF 融合排序。

### 7.2 多媒体默认策略

```dotenv
MESSAGE_SNAPSHOT_MEDIA_STORAGE=link
```

默认永久保存：

- 平台消息 ID；
- 群、发送者和事件字段；
- 文件名、MIME 类型和已知哈希；
- QQ 提供的远程链接。

链接快照是永久元数据，不保证远程字节永久存在。QQ 链接可能过期或要求鉴权。

显式执行：

```text
/message-snapshot restore <snapshot-id-or-message-id>
```

才会尝试下载并固定附件。

只有已经评估存储、隐私和清理策略时，才使用：

```dotenv
MESSAGE_SNAPSHOT_MEDIA_STORAGE=mirror
```

## 8. 每个部门账号的上线验收

### 8.1 基础响应

- [ ] 私聊能够触发并收到回复；
- [ ] 群聊 mention Bot 能够触发回复；
- [ ] 最终回复只出现一次；
- [ ] 长任务能够看到 commentary 中间状态；
- [ ] 不出现重复 final。

### 8.2 未 mention 群消息

发送一条不 mention Bot 的普通文字：

- [ ] Bot 不主动回复；
- [ ] 消息进入快照；
- [ ] 后续 mention Bot 询问时，可从最近上下文或快照召回。

### 8.3 多媒体和文件

依次测试：

1. 不 mention 发送图片；
2. 不 mention 发送文件；
3. 引用图片并 mention Bot；
4. 引用文件并要求处理；
5. 执行 `/message-snapshot restore <id>`。

验收：

- [ ] QQ 已投递的多媒体事件进入快照；
- [ ] `link` 模式不主动下载全部附件；
- [ ] 显式恢复后才下载文件；
- [ ] Codex 能读取已恢复的本地文件；
- [ ] 链接失效时明确报告失败，不虚构恢复结果。

### 8.4 QQ 审批桥接

默认：

```toml
approvals_reviewer = "auto_review"
```

符合既定策略的低风险操作可能被 Codex 自动处理，因此 QQ 不一定出现审批卡。

人工验收时临时改为：

```toml
approvals_reviewer = "user"
```

然后：

```bash
hermes gateway restart
```

分别测试：

- 联网命令；
- 额外目录写入；
- 文件变更；
- Computer Use 操作 Notes 等非硬性禁止 App。

验收：

- [ ] QQ 收到审批请求；
- [ ] 本次允许可以继续当前请求；
- [ ] 会话允许可以在当前会话复用；
- [ ] 只有 Codex 提供持久策略修订时才显示“始终允许同类”；
- [ ] 非请求发起者不能批准共享群会话中的请求；
- [ ] 拒绝和超时均安全失败；
- [ ] 审批后原 Codex turn 继续执行。

测试结束后恢复：

```toml
approvals_reviewer = "auto_review"
```

再执行：

```bash
hermes gateway restart
```

## 9. 多部门账号并行运行边界

### 9.1 可以并行的情况

多个 macOS 用户的 Hermes Gateway 可以同时运行，前提是：

- 每个用户使用不同的 QQ Bot 凭据；
- 每个用户使用自己的 `$HOME/.hermes`；
- 每个用户使用自己的 Codex 登录状态；
- 每个用户的端口、浏览器或其他外部资源不存在冲突。

### 9.2 不允许并行的情况

以下情况只能保留一个 Gateway：

- 多个用户使用同一 QQ App ID 和 Client Secret；
- 多个用户监听同一 Bot 的同一事件流；
- 多个用户共同读写同一个 SQLite 快照数据库；
- 多个用户共同使用同一个可写 Hermes 数据目录。

### 9.3 Fast User Switching

macOS 快速用户切换可能让多个已登录用户的 LaunchAgent 同时运行。

因此：

- 不要认为“切换到另一个桌面”会自动停止前一个用户的 Gateway；
- 相同 QQ Bot 凭据禁止在多个登录用户中同时配置；
- 不需要后台运行的部门账号应执行：

```bash
hermes gateway stop
```

- Computer Use 只应在当前活动且未锁定的图形用户会话中使用。

## 10. 更新流程

以下操作应由每个部门账号分别执行。

### 10.1 更新 Codex CLI

```bash
curl -fsSL https://chatgpt.com/codex/install.sh | sh

source "$HOME/.zprofile"
hash -r

which -a codex
codex --version
codex --strict-config doctor --summary --ascii
codex login status
```

Codex App 使用应用内自动更新。

更新后必须重新验证 launchd PATH：

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

### 10.2 更新 Hermes

使用 Hermes 官方更新或重新运行官方安装器。更新后执行：

```bash
hermes --version
hermes gateway stop
```

然后重新安装最新热修复并运行全部测试，再启动 Gateway。

### 10.3 更新持久化热修复

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

1. 运行第 5.15 节的全部回归测试；
2. 测试通过后执行：

```bash
hermes gateway restart
hermes gateway status
```

### 10.4 记录部署状态

每个部门账号分别记录：

```bash
whoami
codex --version
hermes --version

cd "$HOME/src/hermes-dispatch"
git rev-parse HEAD
git status --short
```

不得在部署记录中保存 `.env` 内容、QQ Client Secret 或授权令牌。

## 11. 停用或删除某个部门账号

停用前先登录该部门账号并执行：

```bash
hermes gateway stop
hermes gateway uninstall
```

然后根据部门数据保留策略决定是否备份或删除：

```text
$HOME/.hermes
$HOME/.codex
$HOME/src/hermes-dispatch
```

删除 macOS 用户前还应处理：

- Keychain 中的 Codex 登录凭据；
- QQ Bot Client Secret 的轮换或撤销；
- 消息快照数据库的归档或销毁；
- 部门 Skills、会话和审计记录；
- Codex App 插件的部门数据。

不要通过删除某个用户的目录来更新或清理其他部门账号。

## 12. 单个部门账号完成判据

只有同时满足以下条件，该部门账号才算部署完成：

- [ ] 当前 shell、Codex App、Hermes 和 launchd 都属于正确的 macOS 用户；
- [ ] Codex CLI、Codex App 和 Hermes 使用部署时的最新稳定版本；
- [ ] Codex 登录的是该部门账号；
- [ ] Codex 严格配置检查通过；
- [ ] Hermes 使用 `openai-codex + codex_app_server`；
- [ ] QQ 使用该部门自己的 Bot 凭据；
- [ ] 三个持久化插件来自最新 `main` 分支并已启用；
- [ ] 全部插件回归测试通过；
- [ ] launchd 与当前 shell 使用同一个 Codex 可执行文件；
- [ ] QQ 文本、未 mention 上下文、文件和多媒体测试通过；
- [ ] 最终回复不重复且 commentary 可见；
- [ ] SQLite 快照和混合检索正常；
- [ ] QQ 审批能够显示、绑定正确用户并继续原任务；
- [ ] macOS 系统权限已在该部门用户下单独授予；
- [ ] 没有与其他部门共享可写 Hermes/Codex 数据目录；
- [ ] 没有与其他正在运行的 Gateway 重复使用同一 QQ Bot 凭据。
