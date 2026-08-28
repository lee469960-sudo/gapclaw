"""MCP 全量落盘（task 1.6/1.7）+ 查询去重软提示（task 6.1/6.2）。

Covers the >6000-gate removal: every non-empty MCP result materializes to disk
and enters the dedup cache, with an element/row-count summary (D13). Small
results still return inline content; only oversized results are replaced by the
"written to file" reference.
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import patch

from app.services.mcp_client import McpSessionManager, _result_shape


class _FakeMCP:
    def __init__(self, mid="m1"):
        self.id = mid
        self.protocol = "http"
        self.url = "http://example/call"
        self.headers = "{}"


def _make_fake_legacy(result_text):
    calls: list[tuple] = []

    async def _fake_legacy(url, headers, tool, args):
        calls.append((tool, args))
        return result_text

    return _fake_legacy, calls


def _root(tmp_path):
    def _r(sid="default"):
        return tmp_path
    return _r


# ---- task 6.1: `_dedup_key` normalization ----

def test_dedup_key_ignores_key_order():
    mgr = McpSessionManager()
    mcp = _FakeMCP()
    k1 = mgr._dedup_key(mcp, "q", {"view": "x", "date": "2024"})
    k2 = mgr._dedup_key(mcp, "q", {"date": "2024", "view": "x"})
    assert k1 == k2


def test_dedup_key_values_participate():
    mgr = McpSessionManager()
    mcp = _FakeMCP()
    k1 = mgr._dedup_key(mcp, "q", {"view": "x"})
    k2 = mgr._dedup_key(mcp, "q", {"view": "y"})
    assert k1 != k2


# ---- `_result_shape` (D13) ----

def test_result_shape_list_and_dict():
    assert _result_shape(json.dumps([{"a": 1}, {"a": 2}, {"a": 3}])) == "[3 项]"
    assert _result_shape(json.dumps({"rows": [1, 2, 3, 4], "total": 4})) == "[4 行]"
    assert _result_shape(json.dumps({"a": 1, "b": 2})) == "[2 键]"
    assert _result_shape("not json") == ""


# ---- task 1.6 / 8.5: small result also materializes + enters cache ----

def test_small_result_materializes_and_keeps_inline(tmp_path):
    text = json.dumps([{"name": "v1"}, {"name": "v2"}])
    fake, calls = _make_fake_legacy(text)
    query_cache: dict = {}
    mcp_results: list = []

    mgr = McpSessionManager(query_cache=query_cache, mcp_results=mcp_results, run_ts="1700000000000")
    mcp = _FakeMCP()

    with patch("app.services.mcp_client._legacy_call_tool", new=fake), patch(
        "app.services.workplace.workplace_root", _root(tmp_path)
    ):
        r1 = asyncio.run(mgr.call_tool(mcp, "query", {"view": "x"}))
        asyncio.run(mgr.close())

    assert r1 == text  # small result stays inline, not a "written to file" reference
    assert calls == [("query", {"view": "x"})]
    assert len(mcp_results) == 1  # still materialized on disk
    assert mcp_results[0]["shape"] == "[2 项]"
    assert (tmp_path / "task" / "1700000000000" / "mcp_result_0.json").read_text() == text
    assert len(query_cache) == 1  # entered dedup cache


# ---- task 1.7 / 6.2 / 8.6: dedup hit returns path + shape ----

def test_same_query_hits_cache_with_path_and_shape(tmp_path):
    text = json.dumps([{"a": 1}, {"a": 2}, {"a": 3}])
    fake, calls = _make_fake_legacy(text)
    query_cache: dict = {}
    mcp_results: list = []

    mgr = McpSessionManager(query_cache=query_cache, mcp_results=mcp_results, run_ts="1700000000000")
    mcp = _FakeMCP()

    with patch("app.services.mcp_client._legacy_call_tool", new=fake), patch(
        "app.services.workplace.workplace_root", _root(tmp_path)
    ):
        r1 = asyncio.run(mgr.call_tool(mcp, "query", {"view": "x"}))
        r2 = asyncio.run(mgr.call_tool(mcp, "query", {"view": "x"}))
        asyncio.run(mgr.close())

    assert len(calls) == 1  # second call did NOT re-hit the MCP service
    assert "该结果已缓存/已落盘" in r2
    assert "mcp_result_0.json" in r2  # soft hint carries the path
    assert "[3 项]" in r2  # shape surfaced in the soft hint


def test_different_args_do_not_hit_dedup(tmp_path):
    text = "ok"
    fake, calls = _make_fake_legacy(text)
    query_cache: dict = {}
    mcp_results: list = []

    mgr = McpSessionManager(query_cache=query_cache, mcp_results=mcp_results, run_ts="1700000000000")
    mcp = _FakeMCP()

    with patch("app.services.mcp_client._legacy_call_tool", new=fake), patch(
        "app.services.workplace.workplace_root", _root(tmp_path)
    ):
        r1 = asyncio.run(mgr.call_tool(mcp, "query", {"view": "x"}))
        r2 = asyncio.run(mgr.call_tool(mcp, "query", {"view": "y"}))
        asyncio.run(mgr.close())

    assert len(calls) == 2  # different args → both executed
    assert "该结果已缓存/已落盘" not in r1
    assert "该结果已缓存/已落盘" not in r2


def test_oversized_result_still_returns_reference(tmp_path):
    big = "BIG" * 4000  # 12000 chars > 6000
    fake, calls = _make_fake_legacy(big)
    query_cache: dict = {}
    mcp_results: list = []

    mgr = McpSessionManager(query_cache=query_cache, mcp_results=mcp_results, run_ts="1700000000000")
    mcp = _FakeMCP()

    with patch("app.services.mcp_client._legacy_call_tool", new=fake), patch(
        "app.services.workplace.workplace_root", _root(tmp_path)
    ):
        r1 = asyncio.run(mgr.call_tool(mcp, "query", {"view": "x"}))
        asyncio.run(mgr.close())

    assert "已全量写入" in r1  # oversized still replaced by reference
    assert "mcp_result_0.json" in r1
    assert len(mcp_results) == 1
