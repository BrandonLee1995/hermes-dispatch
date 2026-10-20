# 本机 A2A 通信目录更新 / Local peer directory — 2026-10-20

## Result / 结果

已在本机 default、product、procurement 三个 profile 部署
`a2a-peer-directory` 1.0.0。产品和采购目录中的 `default` 出站别名已改为
`product-support`；内部 profile ID 仍为 `default`。

`a2a_list` 保留原有名称、URL、鉴权类型、能力标签、历史对话及进程内统计，追加
按 peer 名称列出的中文职责说明。工具 schema 也明确说明包含职责描述。
目录现在区分产品支持、产品开发、采购协作，细分标签与角色边界见
[插件配置说明](../../plugins/a2a-peer-directory/README.md)。

English: The new directory plugin is deployed on the three local profiles.
`product-support` replaces the outbound `default` alias in product/procurement;
all internal profile IDs and authenticated endpoint identities are retained.
Real Codex sessions received the enriched directory through the existing MCP tool.

## Scope and preservation / 范围与保留

- Hermes source remains `29112bef09`; no core edits.
- Existing local A2A transport 1.0.0 and phase hotfix 1.8.10 were not upgraded.
- Only `a2a_agents`, the new plugin's enablement and its explicit tool-override
  permission were changed in each profile's YAML. Other top-level YAML text was
  preserved; parsed comparisons confirm unrelated configuration is unchanged.
- Every peer URL, auth block, timeout and tenant is unchanged. In particular,
  `product-support` still reaches `/default` on the original listener.
- SHA-256 comparisons confirm `.env`, profile metadata, SOUL, Hermes/Codex auth,
  Codex config, existing transport/phase plugin Python files and host scripts
  unchanged. Absent protected files stayed absent.
- Inbound token labels and trusted identities still use their original values;
  changing a local outbound alias does not require changing those identities.
- Active profile rules/scripts/skills/cron files were searched for explicit old
  A2A alias references; none were found in the searched files. Historical
  conversations and previous test records were not rewritten.
- No hidden `default` alias fallback was added. Existing conversations that
  remember the old alias must refresh `a2a_list` before calling again.

## Verification / 验证

- Three directory unit tests passed: metadata validation/normalization,
  preservation of native output, and public scoped tool registration.
- Five real MCP/TLS integration tests passed, covering unchanged tools, opt-in,
  profile isolation, credential omission, the renamed route, exact capability
  dispatch, removed-alias rejection, conversation persistence and error handling.
- The optional isolated Codex app-server fixture was skipped (no `CODEX_BINARY`
  supplied for that fixture run). Actual deployed Codex model sessions were
  separately exercised below; fixture coverage and live evidence are distinct.
- Fresh deployed MCP inventories expose all five A2A tools on all profiles,
  with the new description in the `a2a_list` schema. Each profile sees only its
  other two peers, with the expected Chinese descriptions and capability tags.
- Each idle Gateway was refreshed sequentially via native SIGUSR1 drain/restart.
  Final Gateway state reports running, A2A connected and QQ connected on all three.

| Real A2A route | Check | Exact final reply |
|---|---|---|
| product → product-support | Renamed alias reaches internal default | `DIRECTORY-SUPPORT-FROM-PRODUCT` |
| procurement → product-support | Second caller's alias/auth route works | `DIRECTORY-SUPPORT-FROM-PROCUREMENT` |
| default → product | Target model calls a2a_list once and reads new alias, responsibility and troubleshooting tag | `DIRECTORY-PRODUCT-OK` |
| default → procurement | Target model performs the same directory check | `DIRECTORY-PROCUREMENT-OK` |

All four target Codex completion events have no terminal error. For the two
model-driven directory checks, the session trace contains one actual dynamically
resolved `a2a_list` invocation and its tool output contains the new alias,
Chinese responsibility and tag. Success was not inferred only from outer MCP
`isError=false` or a generated acknowledgment. Calls took 7.526–13.647 seconds.

## Backup and rollback / 备份与回滚

Private owner-owned backup: `~/.hermes/backups/a2a-peer-directory-20261020T020433Z/`.
It contains before/after configs, protected-file hashes, catalog, restart records,
redacted list outputs, four call results, target evidence and final verification.
Config backups contain credentials and must never be committed.

Rollback: restore the three backed-up YAML files only if no later unrelated edits
would be lost; otherwise reverse only the peer metadata/alias and the new plugin's
enablement/permission entries. Refresh idle Gateways/MCP clients, verify the old
`default` route/list, then remove the new plugin directory. Do not restore or
modify `.env`, credentials, internal profile IDs or historical conversations.

No remote hosts were changed. No QQ/WhatsApp messages were sent, and no commit,
push or publication was performed. This verifies directory delivery and A2A
routing, not broader business competence, automatic semantic selection, or channel
recipient delivery. Pre-existing unrelated WhatsApp connection errors were not
part of this change.

## Source-based responsibility refinement / 岗位文件核对后的描述修订

同日后续按用户要求，从已挂载的公司文件盘查阅对应岗位职责文档、采购岗位分工表
和采购作业指导书，将前述通用职责替换为有来源依据的三段业务协作描述。
本次只改六条 peer 记录的 `description`，别名、能力标签、内部 profile ID、路由、
凭据和插件代码均不变。完整业务描述、来源位置、文件哈希和版本选择说明保存在
本机私有证据目录，不将公司原始文件或个人岗位资料加入仓库。

Private evidence and rollback backup:
`~/.hermes/backups/a2a-role-descriptions-20261020T030240Z/`.
`catalog.json` records the three summaries and five cited sources;
`verification.json` records six successful description checks across three
profiles. Each MCP process read the directory before and after the update without
being restarted, proving config cache invalidation for this path. No Gateway
restart or model/message call was needed. Only local read-only `a2a_list` calls
were made. Source hashes and protected runtime-file hashes remained unchanged.

For this refinement alone, restore the before-config backups only if no subsequent
edits would be lost, or replace only the six description values from those backups.
Keep the `product-support` alias and the directory plugin. Refresh `a2a_list` to
verify rollback; no code or credential rollback is required.

## SOUL synchronization / Profile 身份职责同步

同日按用户要求，将来源核对后的各自职责同步到本机三个 profile 的 `SOUL.md`。
保留原有通用人格/表达风格原文，追加职责与协作段落，明确内部 profile ID 与
A2A peer 别名的对应关系。每段职责与另外两个 profile 的目录副本逐字一致；
对外沟通及审批等动作仍遵循用户授权和现有权限。

Backup: `~/.hermes/backups/a2a-soul-sync-20261020T031344Z/`.
All three files passed the native `load_soul_md(home_override=...)` loader with
no content rejected or truncated. Before/after checks confirm original SOUL
content preserved, one exact responsibility copy per file, and unchanged config,
environment, auth and profile metadata. No Gateway restart, forced session reset
or model/message call was performed. Existing sessions may retain previously
assembled prompt state; this check proves file persistence and loader acceptance,
not a live replacement of their prompts. Roll back only the three SOUL files
from this backup after checking for subsequent edits; leave the A2A directory
and credentials intact.

**Subsequent runtime acceptance failed:** the fresh-session model test found no
new SOUL identity/role text in any of the three Codex inputs. File persistence and
loader acceptance above remain valid, but do not imply live model loading, even
for a new conversation. See [SOUL loading test](a2a-soul-loading-2026-10-20.md).
