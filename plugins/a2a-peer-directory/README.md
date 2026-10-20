# A2A peer responsibilities / A2A 通信目录职责描述

Hermes `29112bef09` lists each configured peer's alias, URL, auth type and
capability tags, but omits a responsibility description. This optional plugin
replaces only `a2a_list` through the public plugin registration API. It preserves
the native listing, conversation IDs and metrics, then appends responsibilities
from `a2a_agents.<alias>.description`. Missing, blank or non-string descriptions
are omitted. No credentials are added to the output. The tool schema advertises
descriptions so models know to consult the list.

本插件只扩展 `a2a_list`，保留原有目录、历史会话和统计，追加各 peer 的职责说明。
职责和标签是本地配置的分工声明，不表示已验证的能力或权限。原生工具和 Codex
`hermes-tools` MCP 使用同一注册项，无需修改 Hermes 核心源码。

## Configuration / 配置

Install beside the already-enabled `a2a-https-hotfix`, as the owning OS user:

```bash
scripts/install-plugins.sh /absolute/profile/home a2a-peer-directory
```

Back up `config.yaml` first. Merge these settings into its existing sections;
do not replace unrelated configuration or credentials:

```yaml
plugins:
  enabled:
    - a2a-https-hotfix
    - a2a-peer-directory
  entries:
    a2a-peer-directory:
      allow_tool_override: true

a2a_agents:
  product-support:
    url: https://node.example.ts.net:19901/default
    description: 产品支持：现有产品规格、说明书、使用配置、兼容性与故障排查。
    capabilities:
      - product-support
      - product-specifications
      - product-manuals
      - compatibility-check
      - troubleshooting
    timeout: 480
    # Keep the existing auth block locally; never commit bearer tokens.
```

`requires_plugins` orders registration after the transport plugin. The explicit
`allow_tool_override` opt-in is required by Hermes for replacement tools. No other
tool, transport or protocol field is overridden. The existing A2A availability
gate is retained. The default installer does not enable this plugin globally.

下表概述三个业务角色及现有标签；每个 profile 仅配置另外两个 peer。部署中的完整
职责描述可按内部岗位文件修订，来源与版本依据保存在本机，避免将内部原文加入仓库：

| Peer alias | 职责范围 / Responsibilities | Capability tags |
|---|---|---|
| `product-support` | 现有产品规格、说明书、使用配置、兼容性与故障排查 / Existing-product support | `product-support`, `product-specifications`, `product-manuals`, `compatibility-check`, `troubleshooting` |
| `product` | 新产品需求、功能定义、方案评估、选型与产品改进 / Product development | `product-development`, `requirements-analysis`, `solution-evaluation`, `product-improvement` |
| `procurement` | 供应商沟通、询价比价、采购订单跟进与交期确认 / Procurement coordination | `procurement`, `supplier-coordination`, `quotation-comparison`, `purchase-order-followup`, `delivery-tracking` |

`a2a_orchestrate` continues to exact-match these tags. This plugin does not add
semantic routing, online detection, or automatic Agent Card synchronization.

## Alias migration / 别名迁移

In the other profiles' `a2a_agents` maps, rename only the `default` key to
`product-support`, retaining its URL, auth, timeout and tenant. Update active
rules/scripts that explicitly call `agent="default"`; do not rewrite historical
conversation records. No hidden legacy alias is introduced: old calls should
refresh `a2a_list` and use the new name.

内部 profile ID 仍为 `default`；数据根目录、`HERMES_HOME`、`CODEX_HOME`、服务名、
`/default` URL 路径、Agent Card 名称、入站 bearer 身份和 trusted peers 均保持原值。
本地 peer 别名是出站查表的键，不是入站鉴权身份。不能把 `.env` 中所有 `default`
一并替换，否则可能破坏信任关系。此次不改 profile 显示名。

## Verify / 验证

```bash
python plugins/a2a-peer-directory/test_directory.py
HERMES_SOURCE_DIR=/absolute/hermes/source \
  /absolute/hermes/venv/bin/python mcp/hermes-tools-a2a/test_integration.py -v
```

The real MCP/TLS fixture tests check profile isolation, the new tool schema,
description/tag visibility, credential omission, renamed-peer calls, rejection
of the removed alias, capability fan-out, and preservation of history/metrics.
They do not send tasks to live agents.

Start fresh MCP children after installation. Restart idle Gateways through their
normal drain/restart path to refresh native registrations and cached Codex clients.
Check all five tools remain available, and `a2a_list` shows only the intended peers
with responsibilities and tags. Send one authorized marker call through each
renamed route and verify target-side completion, not just the outer MCP status.
A directory read alone does not prove network reachability.

## Rollback / 回滚

Remove this plugin from `plugins.enabled` and remove only its own entry, or
restore the backed-up configuration if no subsequent edits would be lost.
Restore the former `default` peer key and capability/description values together
with any caller changes. Restart idle Gateways/MCP clients and confirm the native
listing and old route work before removing the plugin directory. Preserve all
credentials and conversation history. Once upstream exposes configured peer
descriptions itself, remove this override after rerunning the same checks.
