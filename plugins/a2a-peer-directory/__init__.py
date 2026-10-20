"""Expose configured peer responsibilities through the existing A2A list tool."""


def a2a_list(args=None, **kwargs):
    from plugins.platforms.a2a import tools

    result = tools.a2a_list(args, **kwargs)
    peers = tools._load_config().get("a2a_agents") or {}
    descriptions = []
    for name, entry in peers.items():
        description = entry.get("description")
        if isinstance(description, str) and description.strip():
            descriptions.append(f"  - {name}: {' '.join(description.split())}")
    if descriptions:
        result += "\n\nPeer responsibilities (configured):\n" + "\n".join(descriptions)
    return result


def register(ctx):
    from plugins.platforms.a2a import tools

    schema = dict(tools._SCHEMAS["a2a_list"]["function"])
    schema["description"] = (
        "List configured A2A peers with names, responsibility descriptions, "
        "capability tags, persisted conversations, and metrics. "
        "Use the listed peer name as a2a_call's agent argument."
    )
    ctx.register_tool(
        name="a2a_list", toolset="a2a", schema=schema, handler=a2a_list,
        check_fn=tools._a2a_tools_available, description=schema["description"],
        emoji="🧩", override=True,
    )
