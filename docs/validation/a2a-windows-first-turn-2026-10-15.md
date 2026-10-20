# Windows 重启后首轮 QQ 缺失 A2A 工具：修复与验收

## 结论

已将 Windows 产品研发测试机的 `a2a-https-hotfix` 更新为 **1.0.2**。
一次真实冷启动验收通过：Gateway 重启后只发送一条 QQ 请求，Windows 直接调用
平台运营 Mac 的 HTTPS A2A，完整回复返回原群，无需重新发送用户消息。

补充的第二次冷启动验收被 Hermes provider 层的 **429 `usage_limit_reached`**
阻断，没有到达远端 A2A，**不计为成功**。服务端当时报告重置时间为
2026-10-15 **19:50:53（北京时间）**。没有为此更换模型、凭据或清除额度状态。

## 原因

测试机运行 Hermes `29112bef09`（0.21.0）和 Codex **0.159.2**。
`hermes-tools` 在 Codex 配置中是可选 MCP，没有设置 required。
Codex 构建初始工具目录时，可选 MCP 默认共享 1000 ms 启动宽限期；Windows 上
该服务初始化约需 2.7–3 秒。因此首轮推理已开始时，实际代码执行环境的
`ALL_TOOLS` 仍没有 `a2a_call`。下一轮服务已就绪，工具才出现。

这不是 A2A 鉴权、HTTPS 或轮数限制问题，也不是工具未安装。独立 MCP
`tools/list` 能列出五个 A2A 工具，不能证明模型首轮已经拿到它们。

## 定位和回归

使用真实 Windows app-server、本地 Responses 测试服务和实际代码执行环境，
让首轮代码执行查询 `ALL_TOOLS`。模型响应由本地 fixture 提供，没有调用线上模型。

| 对照条件 | 首次推理开始时间（约） | 首轮实际发现 `a2a_call` |
|---|---:|---|
| 原始可选 MCP 启动 | 1.500 秒 | 否，数组为空 |
| 等待该线程的 MCP 状态/工具清单后启动推理 | 3.234 秒 | 是 |
| 原生 required 配置覆盖 | 3.297 秒 | 是 |

以实际代码执行结果判断工具可用性，不以 Responses 请求顶层 `tools` 数量判断：
该运行模式使用代码执行工具目录，顶层字段并不直接列出所有 MCP 工具。

持久回归测试使用延迟两秒启动的本地 MCP fixture，稳定重现原始首轮缺失，并
验证修复后首轮具备工具；另验证 required 服务真正失败时，不启动缺少工具的模型。
测试中的延时仅用于模拟慢服务，生产修复没有固定睡眠。

## 最小修复及范围

新增 `plugins/a2a-https-hotfix/mcp_startup.py`，沿用现有客户端构造包装方式。
只有解析后的主 `CODEX_HOME/config.toml` 已存在启用的 `hermes-tools` 条目时，
才给 **Hermes 创建的 Codex 子进程**追加：

```text
-c mcp_servers.hermes-tools.required=true
```

使用 Codex 自带的启动等待机制；保留原有参数、环境变量及启动超时。
已经 required、显式禁用、未配置的条目保持原路径；其他 MCP 启动策略不变。
项目目录中单独定义的 MCP 配置不在本次适配范围内。

没有改写共享 Codex 配置，没有修改 Hermes 上游源码，没有新增依赖。
本次只部署 Windows 插件；Mac 对端没有部署变更。

## 真实 QQ 验收

### 第一次：通过

- 正常重启空闲 Windows Gateway，确认 QQ/A2A connected。
- 发送请求前确认新 Gateway 尚无 Codex 子进程，没有针对该会话预热。
- 唯一用户消息标记：`COLD-A2A-FIRST-1-1015`。
- 此消息触发的实际 Codex 子进程携带 required 覆盖参数。
- 源端记录仅有一条用户输入，执行了原生 A2A 工具，没有终端回退或用户重发。
- Mac 端对应且仅有一个 completed 任务，实际回复：

```text
MAC received Windows cold-start FIRST-1
```

已在 QQ 客户端确认完整回复可见。源端和目标端运行记录均为
`gpt-5.6-luna / medium`。验收走既有 HTTPS peer、原有上下文和现有凭据。

### 第二次：额度阻断

再次正常重启后发送唯一消息 `COLD-A2A-FIRST-2-1015`。QQ 返回 provider
限流通知；诊断记录为 `usage_limit_reached`，属于五小时额度窗口。
对应 QQ Codex 线程没有新增执行轮次，远端也没有 `FIRST-2` 任务。
这次未用于声称重复验收成功，未通过更换凭据、模型或清理冷却记录绕过它。

## 配置和凭据保留

Windows `.env`、Hermes `config.yaml`、Codex `config.toml`、Codex `auth.json`
与本次部署前 SHA-256 一致。既有 A2A 上限 **20** 保留。
Mac 对端的配置、环境及 Codex 凭据文件也与既有备份校验一致。

Hermes 凭据池自行写入的 429/额度状态属于运行记录，本次没有清除、回滚或替换。
敏感配置与调用原始证据保留在操作者控制的私有目录，未写入仓库。

## 已完成的自动化检查

- 配置/参数/环境保留、缺失/禁用/已有 required 条目处理：通过。
- 真实 Codex 首轮代码执行回归和 required 服务失败检查：本机、Windows 均通过。
- 原有 HTTPS 五项检查、MCP 十二项集成检查及运行失败状态回归：通过。
- Python 编译与 `git diff --check`：通过。

## 启用、回滚和证据

部署、启用及回滚说明见[插件 README](../../plugins/a2a-https-hotfix/README.md#first-turn-codex-mcp-readiness-102)。
私有备份名称为 `a2a-coldstart-20261015T094847Z`，包含原插件文件、校验清单、
原始冷启动与首次 QQ 成功证据，以及第二次额度阻断记录。

回滚只恢复本次插件入口/元数据等受影响文件，移除本次新增模块，再正常重启空闲
Gateway；保留 TLS、失败状态修复及上限 20。无需恢复配置或凭据文件。
回滚会重新暴露首轮可选 MCP 启动时序问题。

本次未提交/推送仓库，未做长期或并发压测，也未把额度阻断的重复测试算作通过。

官方依据：[MCP 启动行为](https://learn.chatgpt.com/docs/extend/mcp)、
[required 配置](https://learn.chatgpt.com/docs/config-file/config-reference)、
[app-server 状态与工具接口](https://learn.chatgpt.com/docs/app-server)。
