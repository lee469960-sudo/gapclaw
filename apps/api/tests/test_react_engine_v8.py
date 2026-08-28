"""react-engine-v8 regression tests.

Covers the change's three requirement groups:
  R1 (agent-runtime) `_is_conversational` respects allowed_actions tool actions
  R2 (agent-runtime) `httpmcp_call` real action (parse + call by tool name)
  R3 (agent-runtime) `file_search` real action (parse + `_search_workplace`)
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.services.agent_runtime.runtime import AgentRuntime
from app.services.agent_tools import _search_workplace, execute_action
from app.services.tool_parser import extract_tool_steps


# ---- R1: routing (task 5.1) ----

def _ctx(*, mcp=(), rag=(), skill=(), httpmcp=(), actions=()):
    return SimpleNamespace(
        mcp_ids=list(mcp),
        rag_ids=list(rag),
        skill_ids=list(skill),
        httpmcp_ids=list(httpmcp),
        allowed_actions=list(actions),
    )


def test_is_conversational_shell_only_goes_modular():
    assert AgentRuntime._is_conversational(_ctx(actions=["shell"])) is False


def test_is_conversational_file_search_goes_modular():
    assert AgentRuntime._is_conversational(_ctx(actions=["file_search"])) is False


def test_is_conversational_pure_chat():
    assert AgentRuntime._is_conversational(_ctx(actions=[])) is True


def test_is_conversational_resource_action_only_still_conversational():
    # mcp_tool_call / rag_query / httpmcp_call / skill_* are resource-bound actions;
    # without an actual binding they do NOT force the modular loop.
    assert AgentRuntime._is_conversational(
        _ctx(actions=["mcp_tool_call", "rag_query", "httpmcp_call", "skill_read_md"])
    ) is True


def test_is_conversational_resource_binding_goes_modular():
    assert AgentRuntime._is_conversational(_ctx(mcp=["m1"])) is False
    assert AgentRuntime._is_conversational(_ctx(httpmcp=["h1"])) is False
    assert AgentRuntime._is_conversational(_ctx(rag=["r1"])) is False
    assert AgentRuntime._is_conversational(_ctx(skill=["s1"])) is False


# ---- R2: httpmcp_call (task 5.2) ----

class _HmQuery:
    def __init__(self, hm):
        self._hm = hm

    def filter(self, *a, **k):
        return self

    def first(self):
        return self._hm


class _FakeDB:
    def __init__(self, hm=None):
        self._hm = hm

    def query(self, model):
        if getattr(model, "__name__", "") == "HttpMcp":
            return _HmQuery(self._hm)
        return _HmQuery(None)


def _hm():
    return SimpleNamespace(
        id="h1", name="weather",
        _tools=lambda: [
            {"id": "w", "name": "get_weather", "description": "weather", "args": [{"key": "city"}]},
        ],
    )


def test_parse_httpmcp_step():
    steps = extract_tool_steps('HTTPMCP: get_weather {"city":"bj"}')
    assert len(steps) == 1
    assert steps[0].action == "httpmcp_call"
    assert steps[0].reply == 'HTTPMCP: get_weather {"city":"bj"}'


def test_httpmcp_call_resolves_by_tool_name():
    hm = _hm()
    with patch("app.services.agent_tools.call_httpmcp", new=AsyncMock(return_value="ok")) as call:
        out = asyncio.run(execute_action(
            "httpmcp_call", 'HTTPMCP: get_weather {"city":"bj"}',
            _FakeDB(hm=hm), None, None, [], [], [], httpmcp_ids=["h1"],
        ))
    assert out == "ok"
    call.assert_awaited_once()
    assert call.call_args.args[0] is hm
    assert call.call_args.args[1] == {"city": "bj", "tool": "get_weather"}


def test_httpmcp_call_unbound_degrades():
    out = asyncio.run(execute_action(
        "httpmcp_call", "HTTPMCP: get_weather {}", _FakeDB(hm=None),
        None, None, [], [], [], httpmcp_ids=[],
    ))
    assert out == "no http mcp configured"


def test_httpmcp_call_unknown_tool_degrades():
    out = asyncio.run(execute_action(
        "httpmcp_call", "HTTPMCP: missing {}", _FakeDB(hm=_hm()),
        None, None, [], [], [], httpmcp_ids=["h1"],
    ))
    assert "未在绑定 HttpMcp 中找到工具" in out


# ---- R3: file_search (task 5.3) ----

def test_parse_search_step():
    steps = extract_tool_steps("SEARCH: hello world")
    assert len(steps) == 1
    assert steps[0].action == "file_search"
    assert steps[0].reply == "SEARCH: hello world"


def test_search_workplace_hit(tmp_path):
    (tmp_path / "a.txt").write_text("no match here\nhello world line\n", encoding="utf-8")
    with patch("app.services.agent_tools.workplace_root", return_value=tmp_path):
        out = _search_workplace(None, "hello")
    assert "a.txt:2:" in out


def test_search_workplace_no_match(tmp_path):
    (tmp_path / "a.txt").write_text("nothing relevant\n", encoding="utf-8")
    with patch("app.services.agent_tools.workplace_root", return_value=tmp_path):
        out = _search_workplace(None, "zzz")
    assert out == "(无匹配)"


def test_search_workplace_skips_binary(tmp_path):
    (tmp_path / "a.txt").write_text("hello in text\n", encoding="utf-8")
    (tmp_path / "b.xlsx").write_bytes(b"\x00\x01hello\x02\x03")
    with patch("app.services.agent_tools.workplace_root", return_value=tmp_path):
        out = _search_workplace(None, "hello")
    assert "a.txt:1:" in out
    assert "b.xlsx" not in out


# ---- R2/R3: build_tool_schemas (task 5.4) ----

def test_build_tool_schemas_has_httpmcp_and_file_search():
    from app.services.agent_runtime.system_prompt import SystemPromptBuilder
    schemas = SystemPromptBuilder.build_tool_schemas(["httpmcp_call", "file_search", "shell"])
    names = {s["function"]["name"] for s in schemas}
    assert "httpmcp_call" in names
    assert "file_search" in names


def test_build_tool_schemas_gates_httpmcp_and_file_search():
    from app.services.agent_runtime.system_prompt import SystemPromptBuilder
    schemas = SystemPromptBuilder.build_tool_schemas(["shell"])
    names = {s["function"]["name"] for s in schemas}
    assert "httpmcp_call" not in names
    assert "file_search" not in names
