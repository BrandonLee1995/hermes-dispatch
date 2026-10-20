# 跨主机 A2A 验证与问题记录 — 2026-10-15

> 后续状态：当日下午已完成写锁与失败状态修复及重新验收，见
> [修复验收报告](a2a-active-writer-fix-2026-10-15.md)。本文保留上午的历史观察。

## 结论

C端平台运营部 Mac 与产品研发部公共 Windows 电脑的 `default` profile
已通过 HTTPS A2A 双向调用，并在操作者指定的 QQ 测试群完成真实请求、
远端处理、原群完整回包的验证。本次业务模型为 `gpt-5.6-luna`，思考强度为
`medium`；目标 Codex 的 `turn_context` 记录确认了实际取值。

**但不能据此认为长会话协作已经稳定：补充续聊测试在 Windows 目标上触发了
Codex 线程写锁冲突，并发现远端失败的成功标志不可靠。问题尚未修复。**

本文不包含真实域名、QQ 标识、令牌、私钥或业务聊天记录。详细关联证据与回滚
清单保存在操作者控制的本地及远端部署备份中。

## 环境与部署范围

| 角色 | 系统 | Hermes | Python | MCP | 原有全局模型 / 思考强度 |
|---|---|---|---|---|---|
| 平台运营 | macOS | 0.21.0 | 3.11.15 | 2.0.0 | gpt-5.6-terra / high |
| 产品研发 | Windows | 0.21.0 | 3.11.16 | 2.0.0 | gpt-5.6-sol / high |

两端均使用 `codex_app_server` runtime。A2A adapter、client tools、Hermes MCP
bridge 三个上游文件的哈希在两机之间一致。

- 复用现有 Tailscale 节点域名，地址形态为 `https://<节点域名>:19901/default`；
  监听只绑定本机 Tailscale IPv4 地址。
- 各端本地生成私钥，CSR 由已有私有 CA 签名。仅向远端分发 CA 公钥证书，
  CA 私钥未离开签发主机。证书验证及主机名验证均保持开启。
- 启用可移除的 `a2a-https-hotfix`，明确禁用预装的明文 `a2a-platform` 注册。
  双方使用不同的单向 bearer 凭据和明确的可信 peer 身份。
- Mac 修改已有 `hermes-tools` 启动入口；Windows 原来没有该 MCP 条目，本次新增。
  两端均显式转发 `HERMES_HOME`。
- Windows 新增专用 TCP 19901 入站规则，仅允许对端及验证工作站的 Tailscale 地址。
  未关闭防火墙或扩大到任意公网来源。
- 仅在空闲窗口重启 Gateway。Mac 保留原 launchd 启动方式；Windows 先请求正常退出，
  再运行既有交互式计划任务，新 Gateway 仍处于原交互会话。没有强杀忙碌 Agent。
- 仅关闭 A2A 通道的 `streaming` 和 `interim_assistant_messages`，以保留完整回包。
  QQ 等其他通道的流式设置保持不变。

### 模型设置范围

原有全局默认模型、全局思考强度和既有凭据均保留。

- 专用 A2A 测试 `context_id` 通过目标端 `channel_overrides` 固定为
  `gpt-5.6-luna`，并为该模型配置 `reasoning_overrides: medium`。
- QQ 验收会话通过原生 `/model ... --session` 和 `/reasoning medium` 设置；
  两端都在群内返回了会话级设置确认。
- 目标任务运行记录显示 `gpt-5.6-luna / medium`。权限自动审核另有
  `codex-auto-review / low` 记录，不将其误算成业务任务的模型。
- **未配置的全新 A2A 上下文仍遵循目标 profile 原有默认模型。** 调用方的 QQ
  模型设置不会自动传递到对端；`a2a_call` 本身没有 model/effort 参数。

## 已完成的验证

| 项目 | 平台运营 → 产品研发 | 产品研发 → 平台运营 |
|---|---|---|
| 实际 MCP 工具清单 | 五个 A2A 工具可用 | 五个 A2A 工具可用 |
| HTTPS Agent Card 发现 | 通过 | 通过 |
| 正确 peer 鉴权 | 通过 | 通过 |
| 错误 bearer | 401 拒绝 | 401 拒绝 |
| 未信任私有 CA | TLS 校验失败 | TLS 校验失败 |
| 明文 HTTP 请求 | 被拒绝 | 被拒绝 |
| 首轮短文本回包 | 完整 | 完整 |
| 同 context 第二轮口令回忆 | 正确 | 正确 |
| 新 context 查询旧口令 | UNKNOWN | UNKNOWN |
| a2a_history | 可回忆本端持久记录 | 可回忆本端持久记录 |
| 真实 QQ 发起、远端回复、原群回传 | 通过 | 通过 |

上下文隔离测试仅验证了“不调用额外工具时，新对话没有继承前一对话口令”。
同一 profile 的记忆、文件和工具权限仍然共享，不能将这个结果当成安全沙箱证明。

### 短文本调用耗时

使用 MCP 客户端单调时钟计时，包含工具派发、TLS/A2A 和远端模型处理，
**不是纯网络延迟**，也不是压力测试结果。

| 方向 | 首轮 | 同 context 第二轮 | 新 context |
|---|---:|---:|---:|
| 平台运营 → 产品研发 | 5.911 秒 | 5.451 秒 | 6.308 秒 |
| 产品研发 → 平台运营 | 10.266 秒 | 6.672 秒 | 6.968 秒 |

这六次调用及两次 QQ 请求未观察到 429。样本不足以保证未来并发或高频调用不会限流。
没有加入昨天验收时使用的 60 秒等待，也未重置额度或替换凭据。

### QQ 实际回包

| 发起方 | 目标 | 请求标记 | 原群可见的目标完整回复 |
|---|---|---|---|
| 平台运营机器人 | 产品研发 Windows | XH-QQ-OPS-1015 | RD received OPS cross-host QQ 1015 |
| 产品研发机器人 | 平台运营 Mac | XH-QQ-RD-1015 | OPS received RD cross-host QQ 1015 |

已关联实际 `a2a_call` 参数、指定 `context_id`、目标 A2A 会话、目标模型记录、
源 QQ 会话最终答案，并在 QQ 客户端确认可见回复。未用直接 HTTPS 测试冒充 QQ 验收。

## 实测问题与限制

### 1. Windows 续聊出现 active writer 冲突〔未修复〕

同一目标上下文最初两轮正常；后续复用该上下文，第 3 次请求返回 `TURN3`，
第 4、5 次请求失败，原始错误为：

```text
codex app-server startup failed
codex app-server error -32600:
thread <thread-id> already has an active writer
```

这是目标 Codex runtime 启动/线程恢复失败，不能归类为 HTTPS 或 bearer 鉴权失败。
请求是串行发起的，因此不能简单解释为调用方同时发送了两个请求。
目前未定位写锁持有者，也未区分 Hermes 生命周期、Codex 线程恢复或其他客户端
对锁的影响。没有删除锁、清空历史、强杀进程或重启机器来掩盖该问题。

### 2. 远端失败可能被标成成功〔已实测〕

- 两次写锁失败的任务，在原始 `ListTasks(includeArtifacts=true)` 中仍为
  `TASK_STATE_COMPLETED`；正文却明确写着“Codex 任务未完成”。
- MCP 的 `a2a_call` 对上述失败返回 `isError=false`。
- 防循环拒绝和 HTTP 401 鉴权拒绝，在 MCP 层也返回 `isError=false`。

因此，不能只依据 HTTP 200、`completed` 或 MCP 的 `isError` 判断业务成功。
当前工具将对端状态和正文装进字符串，自动编排需要明确检查实际结果；后续应修复
runtime 失败向 A2A Task 状态的传播，并提供可稳定判断的结构化结果。本次仅记录，
未重写协议结果或添加盲目自动重试。

### 3. 同一 context 默认最多五个入站轮次〔已实测〕

该测试上下文第 6 次请求被立即拒绝：

```text
Anti-loop protection: context <context-id> exceeded 5 turns.
Start a new context or increase A2A_MAX_PINGPONG_TURNS.
```

第 4、5 次虽然模型没有成功处理，仍消耗轮次。源码在派发任务前计数，默认 5、
可配置上限 20；计数器在进程内，空闲超过一小时的上下文会被清理。
这个限制会影响长期往返讨论，不能把 `context_id` 当作无限期聊天通道。
本次没有提高上限或重启以绕过保护。

### 4. 能力名录不是实际可调用能力清单〔发现差异〕

两端 Agent Card 均列出 34 个 Hermes 注册工具集，包括未在本次验收中证明可用的
平台/工具。它来自 Hermes 注册表，不等同于当前 Codex runtime 的工具清单，
也不完整反映单独配置在 Codex 中的第三方 MCP。

实际派工应结合目标的配置、工具可用性和一次真实调用验证。`a2a_list` / Agent Card
也不会自动提供对方所有 QQ 群的上下文、当前工作进度或持续在线监控。

### 5. 模型、上下文和重试各有独立边界

- 模型与思考强度由接收方配置决定，不随调用方模型自动继承。
- 相同 `context_id` 维护对话连续性；更换它会开启另一段对话，需要显式交接背景。
- `context_id` 不是业务幂等键。上游每次发送都会生成新的消息/任务标识；超时后
  直接重发有副作用的任务，不能据此保证只执行一次。本次没有测试业务幂等性。
- 本次接收端等待预算为 150 秒、peer 调用超时为 180 秒、MCP 调用超时至少 300 秒。
  未验证超过这些预算的任务、取消、断网重连及重启过程中的交付。

### 6. HTTPS 与部署限制

- Tailscale 联网不会自动配置 A2A peers、CA 信任、bearer 凭据或 Windows 防火墙。
- 对端仍需信任私有 CA；新增机器还需检查 Windows 入站规则覆盖范围。
- 证书链、主机名、HTTP、重定向和 Agent Card 跨源跳转限制均保留，没有使用
  `https+insecure` 或明文代理后端。
- 当前为了完整回包关闭了 A2A token/interim 流式投递；本次验证的是完整最终回复。
  SSE、push 回调、附件/多模态、跨框架兼容和高并发均未验收。
- 新签发的服务证书于 2027-10-15 到期，未部署自动续签。

## 配置保留、回滚与证据

已比对原有 `.env` 项、无关 Hermes YAML、其他 Codex MCP 设置及插件列表，保持一致。
新增的 A2A 配置、测试模型路由和 QQ 会话级模型设置属于本次授权变更。
两端 QQ/A2A 均仍连接，Windows Gateway 的交互会话保持不变。

部署前备份和详细证据位于各主机本次 `a2a-crosshost-*` 备份目录，验证工作站也保存
汇总清单。回滚应按备份逐项恢复本次新增字段与文件，并删除本次新增的专用 Windows
防火墙规则；不要覆盖之后的操作者变更。停用 TLS 组件时应同时停用 A2A，不能回退为
明文。QQ 会话级模型设置与全局配置分别管理，恢复时也应分别处理。

参考：[HTTPS 部署与回滚](../../plugins/a2a-https-hotfix/README.md)、
[MCP 入口](../../mcp/hermes-tools-a2a/README.md)、
[前次单机多 profile 验收](a2a-https-2026-10-14.md)。
