"""Tests for the MCP tool-catalog "tell the truth" helpers.

Covers the Q1/Q3 fix: ``_get_mcp_tools_cached`` now returns ``(tools, error)``
so the system prompt can surface a soft "数据源不可达" warning instead of
hardcoding tool names when ``tools/list`` fails. ``_format_mcp_tools_for_prompt``
emits a neutral catalog — live tool names + description + required — with no
ads SOP hard-rule (that now lives in the ads-sync-hub Skill references).
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

from app.services.agent_runtime.utils import (
    _format_mcp_tools_for_prompt,
    _get_mcp_tools_cached,
    _mcp_tools_cache,
)


class _FakeMCP:
    def __init__(self, mid: str = "m1", name: str = "ads"):
        self.id = mid
        self.name = name


def test_get_mcp_tools_cached_success_returns_empty_error():
    _mcp_tools_cache.clear()
    detail = {"tools": [{"name": "list_ads_views", "description": "list views"}], "error": ""}
    with patch("app.services.mcp_client.connect_mcp_detail", new=AsyncMock(return_value=detail)):
        tools, err = asyncio.run(_get_mcp_tools_cached(_FakeMCP("a")))
    assert tools == [{"name": "list_ads_views", "description": "list views"}]
    assert err == ""


def test_get_mcp_tools_cached_connect_exception_returns_error():
    _mcp_tools_cache.clear()

    async def boom(_mcp):
        raise TimeoutError("dial timeout")

    with patch("app.services.mcp_client.connect_mcp_detail", new=boom):
        tools, err = asyncio.run(_get_mcp_tools_cached(_FakeMCP("b")))
    assert tools == []
    assert "TimeoutError" in err


def test_get_mcp_tools_cached_detail_error_is_surfaced():
    _mcp_tools_cache.clear()
    detail = {"tools": [], "error": "tools/list connect timeout"}
    with patch("app.services.mcp_client.connect_mcp_detail", new=AsyncMock(return_value=detail)):
        tools, err = asyncio.run(_get_mcp_tools_cached(_FakeMCP("c")))
    assert tools == []
    assert err == "tools/list connect timeout"


def test_format_mcp_tools_for_prompt_is_neutral():
    tools = [
        {"name": "list_ads_views", "description": "list ads views"},
        {"name": "describe_ads_view", "description": "describe a view",
         "inputSchema": {"required": ["view_name"]}},
        {"name": "query_ads_view", "description": "query a view",
         "inputSchema": {"required": ["view"]}},
    ]
    lines = _format_mcp_tools_for_prompt("ads", tools)
    joined = "\n".join(lines)
    assert "list_ads_views" in joined
    assert "describe_ads_view" in joined
    assert "query_ads_view" in joined
    assert "view_name" in joined  # required field surfaced
    # Neutral catalog (design D12): no ads SOP hard-rule, no hardcoded tool examples.
    assert "硬规则" not in joined
    assert "SOP" not in joined
    assert "MCP: list_ads_views {}" not in joined
    assert "<待确认资源>" not in joined  # no hardcoded describe example
    assert "<已绑定资源>" not in joined  # no hardcoded query example
    assert "<待确认指标>" not in joined  # no hardcoded metric example


def test_format_mcp_tools_for_prompt_lists_live_tool_names_only():
    tools = [{"name": "get_note", "description": "fetch a note"}]
    lines = _format_mcp_tools_for_prompt("notes", tools)
    joined = "\n".join(lines)
    assert "get_note" in joined
    assert "硬规则" not in joined  # no describe/query → no SOP hard-rule
