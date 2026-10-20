# A2A 续聊写锁与失败状态修复验收 — 2026-10-15

> 后续配置更新：同日已按用户要求把两台测试机的上限改为 20，并通过九轮续聊验证。
> 见[轮次上限调整记录](a2a-limit20-2026-10-15.md)。本文保留修复验收时的五轮配置。

## 结果

已在 C端平台运营部 Mac、产品研发部公共 Windows 电脑的 `default` profile
部署可移除兼容层修复。两端 Hermes 均为 `29112bef09`（0.21.0），业务测试模型
实际记录为 `gpt-5.6-luna / medium`。未改动全局默认模型、凭据或既有配置文件。

最终版本通过双向各五轮真实 HTTPS A2A 续聊。每个方向由 QQ 分两次发起
（一次单轮、一次四轮顺序调用），完整结果回到指定的 **MWE QQ机器人大军** 群。
核对了源端工具调用参数、目标任务状态、目标运行记录和 QQ 客户端可见回复。
没有把群内回显、直接 HTTPS 或 MCP 就绪当作 QQ 验收。

## 写锁根因与修复

问题位于已有兼容层的会话生命周期，发生于串行请求，不是 TLS/鉴权失败。

1. **关闭对象再次使用**：旧 Agent 仍引用已关闭的 Codex session；再次使用会
   创建子进程，但 `_closed=True` 使下一次 `close()` 直接返回。现在运行入口会
   丢弃已关闭的 session，按原持久映射恢复线程。
2. **垃圾回收丢失持锁者**：Hermes 缓存签名变化可直接替换 Agent，没有调用
   `release_clients()`。旧 session 的引用环被 GC 回收后，弱引用登记消失，
   但 Codex client 的读取线程仍保留子进程。现在给 client 注册 session
   finalizer，session 回收时关闭所属 client；finalizer 不持有 session 本身。

只修复第一条时，真实 Windows 第五轮仍失败。因此补充了“丢弃循环引用会话、
强制 GC、恢复同线程”的回归测试，再部署第二条修复。该中间版本未被记作验收通过。

最终代码为 `codex-app-server-phase-hotfix` **1.8.11**，两端
`session_project.py` 的 SHA-256 一致：
`2b72b1525136634f031dd28dee6c17f7b9fe9e655380221e1e3d69f91a816179`。

原会话、线程 ID、项目映射、历史均保留。未删除线程锁或数据库，也未更换上下文
规避原失败。空闲 Gateway 正常停机重启后，原 Gateway 遗留 app-server 已退出；
没有另行手工终止桌面 Codex 进程。

## 失败状态传播

Hermes 运行结果中的 `failed/error/partial/interrupted/completed=False` 曾被转换为
普通通知文字；原 A2A adapter 将“通知送达”一律标为 completed。

`a2a-https-hotfix` **1.0.1** 现在在当前异步处理任务内保存结构化运行结果，
用原任务 ID 标记 `TASK_STATE_FAILED` 并保留失败通知。不会根据回复里的“Error”
等关键词判断任务失败，也不会错误完成同一上下文里排队的下一个任务。

共享发送路径把失败、拒绝、取消的终态转为工具错误；MCP 包装器再输出
`CallToolResult(isError=true)`，保留错误正文和 `{result: string}` 输出格式。
正常结果及 input-required 保持原语义。

## 验收证据

### 最终 QQ 多轮

维护前已经记住的口令没有在最终请求中再次提供；目标依据既有上下文继续计数。

| 方向 | 最终五轮远端正文 | 任务状态 | QQ 完整回传 |
|---|---|---|---|
| 平台运营 Mac → 产品研发 Windows | `RD-5` 至 `RD-9`，均附 `ORCHID-731` | 五个 completed | 通过 |
| 产品研发 Windows → 平台运营 Mac | `OPS-3` 至 `OPS-7`，均附 `COBALT-284` | 五个 completed | 通过 |

最终序列标记为 `WF-FINAL-A1`、`WF-FINAL-A2-5`、`WF-FINAL-B1`、`WF-FINAL-B2-5`。
没有自动重试、重置上下文或提高五轮上限。最终序列内没有 active writer 错误。
两端业务 `turn_context` 均确认 Luna/medium；`codex-auto-review / low` 是权限审核，
不作为业务模型记录。原本失败的 Windows 上下文恢复到 `RD-5`，也证明维护重启后
仍延续原线程历史。

### 真实负向与恢复

| 测试 | 结果 |
|---|---|
| 中间版本真实 Windows 写锁失败 | A2A 原始列表为 failed，QQ 收到 `Error: A2A task failed` |
| 最终版受控运行失败 | 独立诊断进程只恢复专用测试线程并持锁，不启动模型轮次；跨机请求返回 failed，原始 MCP `isError=true` |
| 第六轮防循环拒绝 | rejected，原始 MCP `isError=true` |
| 不带 bearer 的直接 URL 调用 | HTTP 401，原始 MCP `isError=true` |
| 诊断持锁者释放 | 只关闭本次创建的 client，子进程退出码 0 |
| 同一失败线程释放后续聊 | completed，MCP `isError=false`，完整回复 `WRITER_RELEASED` |

受控冲突同时证明：兼容层不会抢占其他进程持有的线程。未关闭鉴权、TLS 校验或
其他安全边界，也没有用错误凭据替换现有配置。

### 自动化检查

- 写锁回归：两个缓存 Agent 交替六轮，随后丢弃循环引用会话并强制 GC，再恢复线程；
  修复前稳定失败、修复后通过；也在 Windows 安装环境运行通过。
- 原有 phase/session/project/approval 回归及 model/reasoning 控制检查通过。
- 使用真实 Gateway 后台处理路径和 A2A futures 的状态回归通过：运行失败、正常
  文本引用 Error、排队任务隔离、处理结束后的状态清理。
- 五项 HTTPS 检查通过，包括证书/主机名校验、明文拒绝、重定向与跨源限制。
- MCP 包装器及 TLS fixture 集成共十二项通过，包括失败/拒绝/取消/RPC/401，
  正常文本和 input-required 未误判；真实 Codex app-server 发现和调用工具也通过。

## 观察到的模型/工具限制

- 早期正向 QQ 口令回显没有实际 A2A 调用，不计入通过；后续以原始调用和目标任务
  核对，而不是凭群内文字判断。
- 反向首轮出现 `tools...a2a_call is not a function`。当前 MCP `tools/list` 确认
  五项 A2A 工具存在；模型检查 `ALL_TOOLS` 并使用实际可调用函数后成功。该次没有
  发出 A2A 请求，不能算作网络或远端处理失败，也没有为此修改配置。
- 一次 QQ 转述把 context 头中的单词写错；实际工具参数及目标上下文正确。
  Agent 生成的结果摘要不能替代原始任务关联证据。
- 原生 `a2a_orchestrate(mode=all)` 仍是聚合报告，需要逐个检查 peer 错误；
  本次没有增加整体事务或自动重试语义。
- 五轮防循环限制、失败也计数、输入模型不自动继承等既有边界保持不变。
  本次未做并发、长期负载、长任务、取消、附件或断网重连验收。

## 配置保留、启用和回滚

两端 `config.yaml`、`.env`、Codex `config.toml` 与本次部署前备份逐字节哈希一致；
既有凭据、默认模型、思考强度和 A2A 参数均保留。最终 QQ/A2A 都 connected，
Gateway 无活动 Agent。测试产生的新聊天记录及 Hermes 自身的运行记录属于正常结果。

部署只更新 profile 持久插件及现有 MCP 启动脚本。具体启用步骤和回滚边界见：

- [Codex 会话生命周期修复](../../plugins/codex-app-server-phase-hotfix/README.md#closed-session-reuse-and-active-writers-1811)
- [A2A 运行失败状态](../../plugins/a2a-https-hotfix/README.md#runtime-failure-status-101)
- [MCP 错误结果](../../mcp/hermes-tools-a2a/README.md#error-results)

私有证据及原始文件保存在操作者备份目录
`a2a-writerfix-20261015T064959Z` 与 `a2a-writerfix-20261015T064959Z-r2`，未进入仓库。
回滚完整修复应恢复第一次备份中的原始插件/MCP 文件，并移除当时不存在的新文件，
在空闲时重启；不要只恢复 r2，因为那会退回尚有 GC 写锁缺陷的中间版本。
不要恢复配置整份文件覆盖之后的操作者修改；这次修复本身没有改配置。
回滚会重新引入原缺陷。TLS 必须保持启用。

本次没有提交或推送远端仓库。[上午跨机报告](a2a-cross-host-2026-10-15.md) 保留为
历史快照，其中“写锁和成功标志尚未修复”的状态由本文取代。
