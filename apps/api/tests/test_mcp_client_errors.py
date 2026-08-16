"""MCP client: non-empty transport errors, query retries; soft enrich has no hard gates."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import httpx

from app.services.export_column_plan import (
    build_query_graph,
    resolve_time_field_from_evidence,
)
from app.services.mcp_client import (
    _format_mcp_call_failure,
    _is_transport_error,
    call_mcp_tool,
)
from app.services.react_engine import _enrich_mcp_failure
from app.services.export_schema_discovery import ViewSchemaHint


def test_format_mcp_call_failure_never_empty_on_blank_timeout():
    exc = httpx.ReadTimeout("")
    msg = _format_mcp_call_failure(exc, url="http://example.com/mcp?token=secret")
    assert msg.startswith("MCP 调用失败:")
    assert "ReadTimeout" in msg
    assert msg.strip() != "MCP 调用失败:"
    assert "token=secret" not in msg
    assert "url=http://example.com/mcp" in msg


def test_is_transport_error():
    assert _is_transport_error(httpx.ReadTimeout("")) is True
    assert _is_transport_error(httpx.ConnectError("boom")) is True
    assert _is_transport_error(ValueError("business")) is False


def test_streamable_query_retries_then_succeeds():
    mcp = type(
        "M",
        (),
        {
            "protocol": "sse",
            "url": "http://example.com/mcp",
            "headers": "{}",
        },
    )()

    calls = {"n": 0}

    async def _once(url, headers, tool_name, args):
        calls["n"] += 1
        if calls["n"] < 3:
            raise httpx.ReadTimeout("")
        return '{"rows":[{"uid":"1"}]}'

    async def _run():
        with patch("app.services.mcp_client._streamable_call_once", new=AsyncMock(side_effect=_once)):
            with patch("app.services.mcp_client.asyncio.sleep", new=AsyncMock()):
                return await call_mcp_tool(mcp, "query_ads_view", {"view": "v"})

    out = asyncio.run(_run())
    assert calls["n"] == 3
    assert "rows" in out
    assert not out.startswith("MCP 调用失败")


def test_streamable_query_exhausts_retries_with_typed_error():
    mcp = type(
        "M",
        (),
        {
            "protocol": "sse",
            "url": "http://example.com/mcp",
            "headers": "{}",
        },
    )()

    async def _once(url, headers, tool_name, args):
        raise httpx.ReadTimeout("")

    async def _run():
        with patch("app.services.mcp_client._streamable_call_once", new=AsyncMock(side_effect=_once)):
            with patch("app.services.mcp_client.asyncio.sleep", new=AsyncMock()):
                return await call_mcp_tool(mcp, "query_ads_view", {"view": "v"})

    out = asyncio.run(_run())
    assert out.startswith("MCP 调用失败:")
    assert "ReadTimeout" in out


def test_enrich_transport_soft_hint_no_hard_gate():
    text = _enrich_mcp_failure("query_ads_view", "MCP 调用失败:")
    assert "传输层软提示" in text
    assert "禁止写表" not in text
    assert "硬门禁" not in text or "非业务硬门禁" in text
    assert "FINAL" not in text or "不阻止" in text or "勿因" in text


def test_enrich_business_error_soft_feeds_describe_schema():
    hints = {
        "view_result_gameuser_betstat_everyday_bygame": ViewSchemaHint(
            view="view_result_gameuser_betstat_everyday_bygame",
            fields=["uid", "stat_date", "bet_value", "game_id"],
        )
    }
    text = _enrich_mcp_failure(
        "query_ads_view",
        'MCP 错误: {"error":"Unknown expression identifier `create_time`"}',
        view="view_result_gameuser_betstat_everyday_bygame",
        schema_hints=hints,
    )
    assert "schema 软提示" in text
    assert "stat_date" in text
    assert "禁止猜列" in text
    assert "禁止 FINAL" not in text
    assert "硬挡" not in text


def test_resolve_time_field_prefers_plan_and_schema_evidence():
    assert (
        resolve_time_field_from_evidence(
            plan_time_field="stat_date",
            schema_fields=["uid", "stat_date", "bet_value"],
        )
        == "stat_date"
    )
    assert (
        resolve_time_field_from_evidence(
            plan_time_field="create_time",
            schema_fields=["uid", "stat_date", "bet_value"],
        )
        == "stat_date"
    )


def test_sequence_node_uses_plan_time_field_not_hardcoded_create_time():
    # pay sequence triggers companion bet sequence; bet view/time from plan + describe
    plan = [
        {
            "header": "连续充值次数",
            "fetch_mode": "sequence",
            "agg_spec": {
                "role": "pay",
                "view": "view_result_pay_order_log",
                "mode": "sequence",
                "select": "uid, create_time",
                "time_field": "create_time",
            },
        },
        {
            "header": "总下注金额",
            "fetch_mode": "agg",
            "time_field": "stat_date",
            "agg_spec": {
                "role": "bet",
                "view": "view_result_gameuser_betstat_everyday_bygame",
                "mode": "agg",
                "select": "uid, sum(bet_value) AS bet_sum",
                "group_by": "uid",
                "time_field": "stat_date",
            },
            "sources": [
                {
                    "role": "bet",
                    "view": "view_result_gameuser_betstat_everyday_bygame",
                }
            ],
        },
    ]
    nodes = build_query_graph(
        plan,
        {"start_ms": 1, "end_ms": 2, "cohort": "pay"},
        schema_fields_by_view={
            "view_result_gameuser_betstat_everyday_bygame": [
                "uid",
                "stat_date",
                "bet_value",
            ],
        },
    )
    bet_seq = [
        n
        for n in nodes
        if n.get("mode") == "sequence"
        and "everyday_bygame" in str(n.get("view") or "")
    ]
    assert bet_seq
    sql = bet_seq[0].get("sql") or ""
    assert "stat_date" in sql
    assert "SELECT uid, create_time" not in sql
    assert "type = 1" not in sql
