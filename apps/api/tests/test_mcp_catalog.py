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
    normalize_mcp_tool_args,
)


class _FakeMCP:
    def __init__(self, mid: str = "m1", name: str = "ads", modified_at: str = ""):
        self.id = mid
        self.name = name
        self.modified_at = modified_at


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


def test_get_mcp_tools_cache_invalidates_when_mcp_modified_at_changes():
    _mcp_tools_cache.clear()
    with patch("app.services.mcp_client.connect_mcp_detail", new=AsyncMock(side_effect=[
        {"tools": [{"name": "old"}], "error": ""},
        {"tools": [{"name": "new"}], "error": ""},
    ])) as connect:
        assert asyncio.run(_get_mcp_tools_cached(_FakeMCP("a", modified_at="v1")))[0][0]["name"] == "old"
        assert asyncio.run(_get_mcp_tools_cached(_FakeMCP("a", modified_at="v2")))[0][0]["name"] == "new"
    assert connect.await_count == 2


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
    tools = [{
        "name": "get_note",
        "description": "fetch a note",
        "inputSchema": {
            "type": "object",
            "properties": {"id": {"type": "string"}},
            "required": ["id"],
        },
    }]
    lines = _format_mcp_tools_for_prompt("notes", tools)
    joined = "\n".join(lines)
    assert "get_note" in joined
    assert "id:string" in joined
    assert "required=[id]" in joined
    assert "硬规则" not in joined  # no describe/query → no SOP hard-rule


def test_normalize_mcp_tool_args_uses_schema_without_tool_name_hardcoding():
    tool = {
        "name": "arbitrary_provider_tool",
        "inputSchema": {
            "type": "object",
            "properties": {
                "id": {"type": "string"},
                "cursor": {"oneOf": [{"type": "integer"}, {"type": "string"}]},
                "count": {"type": "integer"},
                "enabled": {"type": "string"},
                "nested": {
                    "type": "object",
                    "properties": {"parent_id": {"type": ["string", "null"]}},
                },
                "ids": {"type": "array", "items": {"type": "string"}},
            },
        },
    }
    huge = 1917542058656307880
    original = {
        "id": huge,
        "cursor": huge,
        "count": 20,
        "enabled": True,
        "nested": {"parent_id": huge},
        "ids": [huge, "2"],
        "unknown": huge,
    }

    normalized = normalize_mcp_tool_args(tool, original)

    assert normalized == {
        "id": str(huge),
        "cursor": str(huge),
        "count": 20,
        "enabled": True,
        "nested": {"parent_id": str(huge)},
        "ids": [str(huge), "2"],
        "unknown": huge,
    }
    assert original["id"] == huge


def test_normalize_mcp_tool_args_preserves_safe_integer_when_schema_accepts_it():
    tool = {
        "inputSchema": {
            "type": "object",
            "properties": {
                "cursor": {"anyOf": [{"type": "integer"}, {"type": "string"}]},
            },
        },
    }

    assert normalize_mcp_tool_args(tool, {"cursor": 123}) == {"cursor": 123}
