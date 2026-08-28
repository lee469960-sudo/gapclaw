"""react-engine-v4 regression tests: tool_result_clip clamp, leaked-token strip,
native tool_call_id backfill, and atomic group trimming.

No live LLM / DB; routers and helpers are exercised directly with fakes.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from app.models import Agent
from app.routers.agent import AgentBody, agent_post
from app.services.llm_client import (
    _build_chat_result,
    _tool_call_to_step,
    fit_messages_to_context,
    normalize_chat_messages,
    tool_steps_from_tool_calls,
)
from app.services.tool_parser import (
    _norm_path,
    extract_tool_steps,
    strip_leaked_tool_tokens,
)


# ---- 6.1 tool_result_clip config + route clamp ----


def test_agent_model_has_tool_result_clip_default():
    col = Agent.__table__.columns["tool_result_clip"]
    assert col.default.arg == 6000
    assert "tool_result_clip" in Agent(id="a1", name="t").to_dict()


def test_agent_body_defaults_tool_result_clip():
    assert AgentBody().tool_result_clip == 6000


class _FakeDB:
    def __init__(self):
        self.row = None

    def add(self, row):
        self.row = row

    def commit(self):
        pass


def _create_agent(clip) -> Agent:
    db = _FakeDB()
    user = SimpleNamespace(username="admin")
    body = AgentBody(action="create", name="x", tool_result_clip=clip)
    asyncio.run(agent_post(body, user, db))
    return db.row


def test_route_clamps_tool_result_clip_upper():
    assert _create_agent(999999).tool_result_clip == 100000


def test_route_clamps_tool_result_clip_negative_to_min():
    assert _create_agent(-5).tool_result_clip == 1


def test_route_defaults_tool_result_clip_when_unset():
    db = _FakeDB()
    user = SimpleNamespace(username="admin")
    body = AgentBody(action="create", name="x")  # omit tool_result_clip → default
    asyncio.run(agent_post(body, user, db))
    assert db.row.tool_result_clip == 6000


# ---- 6.2 leaked-token stripping ----


def test_strip_chatml_and_minimax_leaked_tokens():
    assert strip_leaked_tool_tokens("<|tool_call|>") == ""
    assert strip_leaked_tool_tokens("]<tool_call>[") == ""
    assert "hello" in strip_leaked_tool_tokens("hello <|tool_call|> world")


def test_norm_path_strips_trailing_bracket():
    assert _norm_path("xxx.py]") == "xxx.py"
    assert _norm_path("[report.csv") == "report.csv"


def test_extract_steps_strips_leaked_token_before_parse():
    steps = extract_tool_steps("<|tool_call|>SHELL: ls -la")
    assert steps and steps[0].action == "shell"
    assert steps[0].reply == "SHELL: ls -la"


# ---- 6.3 ToolStep.tool_call_id + native source ----


def test_tool_call_to_step_carries_id():
    step = _tool_call_to_step({
        "id": "call_1",
        "function": {"name": "shell", "arguments": '{"cmd": "ls -la"}'},
    })
    assert step.action == "shell"
    assert step.reply == "SHELL: ls -la"
    assert step.tool_call_id == "call_1"


def test_tool_steps_from_tool_calls_generates_directly():
    calls = [
        {"id": "c1", "function": {"name": "shell", "arguments": '{"cmd": "pwd"}'}},
        {"id": "c2", "function": {"name": "describe_ads_view", "arguments": '{"view_name": "v1"}'}},
    ]
    steps = tool_steps_from_tool_calls(calls)
    assert [s.tool_call_id for s in steps] == ["c1", "c2"]
    assert steps[0].action == "shell"
    # Unknown tool name falls back to a real-name MCP step (single source mapping).
    assert steps[1].action == "mcp_tool_call"
    assert "describe_ads_view" in steps[1].reply


def test_build_chat_result_native_path_has_tool_calls():
    res = _build_chat_result({"choices": [{"message": {
        "content": "prose preamble",
        "tool_calls": [{"id": "c1", "function": {"name": "shell", "arguments": '{"cmd": "ls"}'}}],
    }}]})
    assert res.has_tool_calls
    assert res.content == "prose preamble"
    # Native path leaves normalized text empty; steps come from tool_calls, not text.
    assert res.text == ""


def test_build_chat_result_drops_unexecutable_tool_calls():
    res = _build_chat_result({"choices": [{"message": {
        "content": "",
        "tool_calls": [{"id": "c1", "function": {"name": "shell", "arguments": "{}"}}],
    }}]})
    assert not res.has_tool_calls
    assert res.text == ""


# ---- 6.4 normalize passthrough + atomic group trim ----


def test_normalize_passes_through_native_pairing():
    out = normalize_chat_messages([
        {"role": "assistant", "content": "", "tool_calls": [{"id": "c1"}]},
        {"role": "tool", "tool_call_id": "c1", "content": "result"},
    ])
    assert out[0]["tool_calls"] == [{"id": "c1"}]
    assert out[1]["tool_call_id"] == "c1"
    assert out[1]["role"] == "tool"


def _has_orphaned_tool(msgs):
    expecting = False
    for m in msgs:
        role = m.get("role")
        if role == "assistant":
            expecting = bool(m.get("tool_calls"))
        elif role == "tool":
            if not expecting:
                return True
        else:
            expecting = False
    return False


def test_fit_messages_never_orphans_tool_messages():
    big = "x" * 4000
    msgs = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": big},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "c1", "function": {"name": "shell", "arguments": "{}"}},
            {"id": "c2", "function": {"name": "shell", "arguments": "{}"}},
        ]},
        {"role": "tool", "tool_call_id": "c1", "content": "r1"},
        {"role": "tool", "tool_call_id": "c2", "content": "r2"},
        {"role": "user", "content": big},
    ]
    out, _ = fit_messages_to_context(
        msgs, max_context_tokens=2048, max_output_tokens=256
    )
    assert not _has_orphaned_tool(out)


def test_fit_messages_preserves_pairing_when_fits():
    msgs = [
        {"role": "system", "content": "sys"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "c1"}]},
        {"role": "tool", "tool_call_id": "c1", "content": "ok"},
        {"role": "user", "content": "final answer"},
    ]
    out, _ = fit_messages_to_context(
        msgs, max_context_tokens=128000, max_output_tokens=4096
    )
    assert out[1].get("tool_calls") == [{"id": "c1"}]
    assert out[2].get("tool_call_id") == "c1"
    assert not _has_orphaned_tool(out)
