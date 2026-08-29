"""Menu and page-route mapping aligned with GAP v1.9.0."""

PAGE_ROUTE_MAP: dict[str, str] = {
    "/pages/page_group.cgi": "/groups",
    "/pages/page_agent.cgi": "/agents",
    "/pages/page_code_project.cgi": "/code-projects",
    "/pages/page_sandbox.cgi": "/sandboxes",
    "/pages/page_skills.cgi": "/skills",
    "/pages/page_mcp.cgi": "/mcps",
    "/pages/page_llm.cgi": "/llms",
    "/pages/page_files.cgi": "/files",
    "/pages/page_rag.cgi": "/rag",
    "/pages/page_httpmcp.cgi": "/httpmcp",
    "/pages/page_channel.cgi": "/channels",
    "/pages/page_sql.cgi": "/sql",
    "/pages/page_terminal.cgi": "/terminals",
    "/pages/page_docker.cgi": "/docker",
    "/pages/page_monitor.cgi": "/monitor",
    "/pages/page_site.cgi": "/site",
    "/pages/system_me.cgi": "/me",
    "/pages/system_user.cgi": "/users",
    "/pages/system_role.cgi": "/roles",
}

PAGE_ALIASES: dict[str, str] = {
    "/pages/page_agent_chat.cgi": "/pages/page_agent.cgi",
    "/pages/page_group_chat.cgi": "/pages/page_group.cgi",
    "/pages/api_agent_cgi.cgi": "/pages/page_agent.cgi",
}


def resolve_page_permission_path(path: str) -> str:
    """Map request path (incl. subpaths like /upload) to RBAC menu page."""
    if path in PAGE_ALIASES:
        return PAGE_ALIASES[path]
    for alias, canonical in PAGE_ALIASES.items():
        if path.startswith(alias + "/"):
            return canonical
    return path

MENU_GROUPS: list[dict] = [
    {
        "title": "智能平台",
        "items": [
            ("/pages/page_group.cgi", "智能体群"),
            ("/pages/page_agent.cgi", "_智能体_"),
            ("/pages/page_code_project.cgi", "Code Projects"),
            ("/pages/page_sandbox.cgi", "沙箱管理"),
            ("/pages/page_skills.cgi", "Skills市场"),
            ("/pages/page_mcp.cgi", "MCP市场"),
            ("/pages/page_llm.cgi", "LLM市场"),
            ("/pages/page_files.cgi", "文件管理"),
            ("/pages/page_rag.cgi", "RAG知识库"),
            ("/pages/page_httpmcp.cgi", "HttpMCP"),
            ("/pages/page_channel.cgi", "消息渠道"),
        ],
    },
    {
        "title": "运维中台",
        "items": [
            ("/pages/page_sql.cgi", "SQL工具"),
            ("/pages/page_terminal.cgi", "远程终端"),
            ("/pages/page_docker.cgi", "Docker"),
            ("/pages/page_monitor.cgi", "监控"),
        ],
    },
    {
        "title": "系统管理",
        "items": [
            ("/pages/page_site.cgi", "站点配置"),
            ("/pages/system_me.cgi", "个人中心"),
            ("/pages/system_user.cgi", "用户管理"),
            ("/pages/system_role.cgi", "角色管理"),
        ],
    },
]

ALL_PAGES = [path for group in MENU_GROUPS for path, _ in group["items"]]


def __getattr__(name: str):
    if name == "APP_VERSION":
        from app.version import app_version
        return app_version()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
