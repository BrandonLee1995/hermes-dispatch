"""Register upstream A2A through TLS; the bundled plaintext plugin stays disabled."""


def register(ctx):
    from plugins.platforms.a2a import register as register_upstream
    from plugins.platforms.a2a import adapter, tools
    from .https_transport import install

    install(adapter, tools, {
        name: ctx.get_config(name, "")
        for name in ("cert_file", "key_file", "ca_file", "path_prefix")
    })
    from .result_status import install as install_result_status
    install_result_status(adapter, tools)
    from .mcp_startup import install as install_mcp_startup
    install_mcp_startup()
    register_upstream(ctx)
