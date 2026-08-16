"""Generic MCP soft-align: tool-name cleanup, schema arg strip, domain enrich."""

from __future__ import annotations

import json

from app.services.react_engine import (
    _enrich_mcp_failure,
    _mcp_tool_name,
    soft_align_mcp_line_to_tool_schema,
)

_HARD = ("禁止写表", "禁止 FINAL", "禁止写表/FINAL")

_OKX_HISTORY_TOOL = {
    "name": "okx_get_history_candles",
    "inputSchema": {
        "type": "object",
        "properties": {
            "instId": {"type": "string"},
            "bar": {"type": "string"},
            "limit": {"type": "string"},
        },
        "required": ["instId"],
    },
}


def test_mcp_tool_name_strips_backticks():
    line = 'MCP: `okx_get_history_candles` {"instId":"BTC-USDT"}'
    assert _mcp_tool_name(line) == "okx_get_history_candles"
    aligned, notes = soft_align_mcp_line_to_tool_schema(line, [_OKX_HISTORY_TOOL])
    assert _mcp_tool_name(aligned) == "okx_get_history_candles"
    assert "`" not in aligned.split("{", 1)[0]
    assert any("工具名" in n for n in notes)


def test_soft_strip_after_before_unknown_args():
    raw = (
        "MCP: okx_get_history_candles "
        + json.dumps(
            {
                "instId": "BTC-USDT",
                "bar": "1H",
                "after": "1786334400000",
                "before": "1786420800000",
            },
            ensure_ascii=False,
        )
    )
    aligned, notes = soft_align_mcp_line_to_tool_schema(raw, [_OKX_HISTORY_TOOL])
    args = json.loads(aligned.split(" ", 2)[2])
    assert "after" not in args
    assert "before" not in args
    assert args.get("instId") == "BTC-USDT"
    assert args.get("bar") == "1H"
    assert any("after" in n or "未知参数" in n for n in notes)
    blob = aligned + " ".join(notes)
    for snip in _HARD:
        assert snip not in blob


def test_okx_enrich_has_no_list_ads_views_coach():
    text = _enrich_mcp_failure(
        "okx_get_history_candles",
        (
            "MCP 错误: 1 validation error for call[okx_get_history_candles]\n"
            "after\n  Unexpected keyword argument"
        ),
        tool_schema_summary="okx_get_history_candles 参数: instId, bar, limit；required: instId",
    )
    assert "list_ads_views" not in text
    assert "describe_ads_view" not in text
    assert "tool schema" in text.lower() or "【tool schema】" in text
    assert "禁止 FINAL" not in text


def test_ads_enrich_still_can_include_ads_coach():
    text = _enrich_mcp_failure(
        "query_ads_view",
        'MCP 错误: {"error":"Unknown expression identifier `ghost_col`"}',
        view="",
    )
    assert "list_ads_views" in text
    assert "禁止 FINAL" not in text
