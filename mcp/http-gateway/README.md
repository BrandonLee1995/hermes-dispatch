# Hermes HTTP MCP Experiment

本目录保存 Hermes 自身以 HTTP MCP 方式对外提供能力时的薄封装、临时修复和测试代码。它是实验/扩展区，不是默认生产部署包的一部分。

## Files

```text
docker-compose.hermes-mcp-http.yml       # 单实例 HTTP MCP 测试 Compose
mcp-http.env.example                     # 通用实例参数模板
hermes-mcp-http.py                       # Streamable HTTP MCP 薄封装
hermes_mcp_http_auth.py                  # HTTP Bearer Token 鉴权中间件
hermes_mcp_qqbot_target_patch.py         # QQBot target 兼容修复实验
test_mcp_streamable.py                   # HTTP MCP 流式协议测试客户端
test_hermes_mcp_qqbot_target_patch.py    # QQBot target patch 单元测试
caddy-hermes-lan.Caddyfile               # 局域网反向代理示例
caddy-hermes-lan-test.Caddyfile          # 测试用反向代理示例
```

## Generic Instance Model

本实验目录不再绑定某个固定 Hermes 实例。部署一个实例时，复制 `mcp-http.env.example` 为 Compose 项目的 `.env`，再按目标部门或测试实例填写：

```text
HERMES_MCP_DATA_DIR=/public/hermes/<dept>
HERMES_MCP_ENV_FILE=/public/hermes/<dept>/.env
HERMES_MCP_HTTP_PORT=<unique-loopback-port>
HERMES_MCP_PUBLIC_SITE=http://<dept>-mcp.hermes.lan:18080
HERMES_MCP_PROXY_BIND=<lan-ip>
HERMES_MCP_UPSTREAM=127.0.0.1:<unique-loopback-port>
HERMES_MCP_HTTP_BEARER_TOKEN=<long-random-token>
```

同一台机器部署多个 HTTP MCP wrapper 时，每个实例应有独立的 Compose project name、容器名、`HERMES_MCP_HTTP_PORT`、局域网域名和 Bearer Token。`HERMES_MCP_DATA_DIR` 指向该实例自己的 Hermes `/opt/data` 挂载目录，不共享。

## HTTP Bearer Token

`hermes-mcp-http.py` 会在启动时读取 `HERMES_MCP_HTTP_BEARER_TOKEN`。该变量为空时进程直接退出，避免把 Hermes MCP 工具面无鉴权地暴露到局域网。

客户端必须传入：

```text
Authorization: Bearer <HERMES_MCP_HTTP_BEARER_TOKEN>
```

测试客户端示例：

```bash
MCP_URL=http://<dept>-mcp.hermes.lan:18080/mcp \
MCP_HOST_HEADER=<dept>-mcp.hermes.lan:18080 \
MCP_BEARER_TOKEN='<token>' \
python /opt/data/scripts/test_mcp_streamable.py
```

Caddy 仍然只负责局域网入口和反向代理；Bearer Token 校验在 Python wrapper 内完成，因此本机直接访问 `127.0.0.1:8765` 也需要鉴权。

## Optional QQBot Hotfix

QQBot target 兼容修复不再默认启用。需要该修复的实例设置：

```text
HERMES_MCP_ENABLE_QQBOT_HOTFIX=1
```

同时确保 `hermes_mcp_qqbot_target_patch.py` 与 `hermes-mcp-http.py` 一起放在该实例的 `/opt/data/scripts/` 下。未启用时，HTTP MCP wrapper 不依赖 QQBot hotfix 文件。

## Production Boundary

这些文件当前用于验证 Hermes 内置 MCP 网关能力、局域网访问方式和 QQ adaptor 修复思路。要进入生产部署时，不应修改官方容器内部代码，而应转换为以下持久化形态之一：

- `plugins/<plugin>/`
- `mcp/http-gateway/`
- `mcp/servers/`
- `deploy/<instance>/`

生产启用前还需要补充运维文档、回滚方式和权限边界说明。
