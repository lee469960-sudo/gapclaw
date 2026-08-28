"""McpSessionManager: per-run session reuse, failure recovery, close semantics.

Plus multi-MCP dispatch in ``execute_action`` (task 3.5).
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.services.mcp_client import McpSessionManager
from app.services.agent_tools import execute_action


class _MCP:
    def __init__(
        self,
        mid: str,
        protocol: str = "streamable",
        url: str = "http://example/mcp",
        headers: str = "{}",
        command: str | None = None,
        command_args=None,
        command_env=None,
    ):
        self.id = mid
        self.name = f"mcp-{mid}"
        self.protocol = protocol
        self.url = url
        self.headers = headers
        self.command = command
        self.command_args = command_args
        self.command_env = command_env


# ---- stdio fake ----

class _FakeStdioSession:
    instances: list["_FakeStdioSession"] = []

    def __init__(self, command, args, env):
        self.command = command
        self.started = False
        self.closed = False
        self.close_raises = False
        self.fail_next_tool_call = False
        self.tool_calls = 0
        _FakeStdioSession.instances.append(self)

    async def start(self):
        self.started = True

    async def close(self):
        self.closed = True
        if self.close_raises:
            raise RuntimeError("close boom")

    async def request(self, method, params=None):
        if method == "initialize":
            return {"result": {}}
        self.tool_calls += 1
        if self.fail_next_tool_call:
            self.fail_next_tool_call = False
            raise RuntimeError("stdio MCP 进程已退出 code=1")
        return {"result": {"content": [{"type": "text", "text": f"ok:{method}"}]}}

    async def notify(self, method, params=None):
        return None


# ---- streamable fake ----

class _FakeStreamableSession:
    instances: list["_FakeStreamableSession"] = []

    def __init__(self, url, headers):
        self.url = url
        self.initialize_calls = 0
        _FakeStreamableSession.instances.append(self)

    async def initialize(self, client):
        self.initialize_calls += 1
        return {"result": {}}

    async def rpc(self, client, method, params=None):
        if method == "tools/call":
            return {"result": {"content": [{"type": "text", "text": "ok"}]}}
        return {"result": {}}


def _stdio_mcp(mid="m1") -> _MCP:
    return _MCP(mid, protocol="stdio", command="npx", command_args=["-y", "x"], command_env={})


# ---- reuse + close + recovery ----

def test_stdio_session_reused_across_calls(tmp_path):
    _FakeStdioSession.instances = []
    mgr = McpSessionManager()
    mcp = _stdio_mcp()

    def _root(sid="default"):
        return tmp_path

    with patch("app.services.mcp_client._StdioSession", _FakeStdioSession), patch(
        "app.services.workplace.workplace_root", _root
    ):
        # Different args → no query-dedup hit; both calls reach the same session.
        r1 = asyncio.run(mgr.call_tool(mcp, "list_notes", {}))
        r2 = asyncio.run(mgr.call_tool(mcp, "list_notes", {"page": 1}))
        asyncio.run(mgr.close())
    assert r1 == "ok:tools/call"
    assert r2 == "ok:tools/call"
    assert len(_FakeStdioSession.instances) == 1  # spawn once
    assert _FakeStdioSession.instances[0].tool_calls == 2
    assert _FakeStdioSession.instances[0].closed is True


def test_streamable_session_initialized_once(tmp_path):
    _FakeStreamableSession.instances = []
    mgr = McpSessionManager()
    mcp = _MCP("m2", protocol="streamable")

    def _root(sid="default"):
        return tmp_path

    with patch("app.services.mcp_client._StreamableSession", _FakeStreamableSession), patch(
        "app.services.workplace.workplace_root", _root
    ):
        r1 = asyncio.run(mgr.call_tool(mcp, "query", {"x": 1}))
        r2 = asyncio.run(mgr.call_tool(mcp, "query", {"x": 2}))
        asyncio.run(mgr.close())
    assert r1 == "ok"
    assert r2 == "ok"
    assert len(_FakeStreamableSession.instances) == 1
    assert _FakeStreamableSession.instances[0].initialize_calls == 1


def test_stdio_failure_rebuilds_once_and_retries(tmp_path):
    _FakeStdioSession.instances = []
    mgr = McpSessionManager()
    mcp = _stdio_mcp("m3")

    def _root(sid="default"):
        return tmp_path

    with patch("app.services.mcp_client._StdioSession", _FakeStdioSession), patch(
        "app.services.workplace.workplace_root", _root
    ):
        # Prime the first session to fail its next tool call.
        # (We can't reach it before creation, so fail via a class flag.)
        _FakeStdioSession.instances = []
        # Force the first created instance to fail: monkeypatch request on first instance only.
        orig_request = _FakeStdioSession.request

        def _request(self, method, params=None):
            if method == "tools/call" and self is _FakeStdioSession.instances[0]:
                raise RuntimeError("stdio MCP 进程已退出 code=1")
            return orig_request(self, method, params)

        with patch.object(_FakeStdioSession, "request", _request):
            result = asyncio.run(mgr.call_tool(mcp, "list_notes", {}))
            asyncio.run(mgr.close())
    assert result == "ok:tools/call"  # retried on the rebuilt session
    assert len(_FakeStdioSession.instances) == 2  # rebuilt once
    assert _FakeStdioSession.instances[0].closed is True  # dead session dropped
    assert any(e["event"] == "rebuild" for e in mgr.events)


def test_close_swallows_per_session_errors(tmp_path):
    _FakeStdioSession.instances = []
    mgr = McpSessionManager()
    mcp = _stdio_mcp("m4")

    def _root(sid="default"):
        return tmp_path

    with patch("app.services.mcp_client._StdioSession", _FakeStdioSession), patch(
        "app.services.workplace.workplace_root", _root
    ):
        asyncio.run(mgr.call_tool(mcp, "list_notes", {}))
        _FakeStdioSession.instances[0].close_raises = True
        # must not raise
        asyncio.run(mgr.close())


def test_legacy_http_is_not_cached(tmp_path):
    calls = []

    async def _fake_legacy(url, headers, tool, args):
        calls.append(tool)
        return f"legacy:{tool}"

    mgr = McpSessionManager()
    mcp = _MCP("m5", protocol="http", url="http://example/call")

    def _root(sid="default"):
        return tmp_path

    with patch("app.services.mcp_client._legacy_call_tool", new=_fake_legacy), patch(
        "app.services.workplace.workplace_root", _root
    ):
        # Different args → no query-dedup hit; legacy is stateless (no session reuse).
        r1 = asyncio.run(mgr.call_tool(mcp, "x", {"a": 1}))
        r2 = asyncio.run(mgr.call_tool(mcp, "x", {"a": 2}))
    assert calls == ["x", "x"]  # called every time, no session caching
    assert r1 == "legacy:x" and r2 == "legacy:x"


# ---- multi-MCP dispatch (task 3.5) ----

class _FakeDB:
    def __init__(self, mcps):
        self._mcps = {m.id: m for m in mcps}

    def query(self, model):
        db = self

        class _Q:
            def __init__(self, mid=None):
                self._mid = mid

            def filter(self, cond):
                right = cond.right
                mid = getattr(right, "value", right)
                return _Q(mid)

            def first(self):
                return db._mcps.get(self._mid)

            def all(self):
                return list(db._mcps.values())

        return _Q()


class _Sessions:
    def __init__(self):
        self.called = []

    async def call_tool(self, mcp, tool, args):
        self.called.append((mcp.id, tool))
        return f"ok:{mcp.id}:{tool}"


def test_execute_action_dispatches_mcp_by_tool_name():
    mcp_a = _MCP("a")
    mcp_b = _MCP("b")
    db = _FakeDB([mcp_a, mcp_b])

    async def _get(mcp):
        return ([{"name": "list_ads_views"}], "") if mcp.id == "a" else ([{"name": "list_notes"}], "")

    sessions = _Sessions()
    with patch("app.services.agent_tools._get_mcp_tools_cached", new=_get):
        result = asyncio.run(
            execute_action(
                "mcp_tool_call",
                'MCP: list_notes {"since_id":0}',
                db,
                SimpleNamespace(allowed_actions="[]"),
                None,
                [],
                ["a", "b"],
                None,
                mcp_sessions=sessions,
            )
        )
    assert result == "ok:b:list_notes"
    assert sessions.called == [("b", "list_notes")]


def test_execute_action_mcp_dispatch_no_hit_message():
    mcp_a = _MCP("a")
    db = _FakeDB([mcp_a])

    async def _get(mcp):
        return [{"name": "other_tool"}], ""

    sessions = _Sessions()
    with patch("app.services.agent_tools._get_mcp_tools_cached", new=_get):
        result = asyncio.run(
            execute_action(
                "mcp_tool_call",
                "MCP: list_notes {}",
                db,
                SimpleNamespace(allowed_actions="[]"),
                None,
                [],
                ["a"],
                None,
                mcp_sessions=sessions,
            )
        )
    assert "未在绑定 MCP 中找到工具 list_notes" in result
    assert sessions.called == []


def test_execute_action_mcp_falls_back_to_call_mcp_tool_without_sessions():
    mcp_a = _MCP("a")
    db = _FakeDB([mcp_a])

    async def _get(mcp):
        return [{"name": "list_ads_views"}], ""

    async def _call(mcp, tool, args):
        return f"legacy:{tool}"

    with patch("app.services.agent_tools._get_mcp_tools_cached", new=_get):
        with patch("app.services.agent_tools.call_mcp_tool", new=_call):
            result = asyncio.run(
                execute_action(
                    "mcp_tool_call",
                    "MCP: list_ads_views {}",
                    db,
                    SimpleNamespace(allowed_actions="[]"),
                    None,
                    [],
                    ["a"],
                    None,
                )
            )
    assert result == "legacy:list_ads_views"
