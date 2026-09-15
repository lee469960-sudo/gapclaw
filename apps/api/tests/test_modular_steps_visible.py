"""Modular runtime: skill/mcp load steps + LLM rounds + meta.steps persist."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.agent_runtime.loop_state import AgentLoopState
from app.services.agent_runtime.runtime import AgentRuntime


def _fake_ctx(**overrides):
    base = dict(
        agent=SimpleNamespace(id="a1", llm_timeout=30, sandbox_id=None, name="t"),
        session_id="s1",
        chat_key="a1:s1",
        user_message="hello",
        username="u",
        db=MagicMock(),
        llm=SimpleNamespace(id="llm1"),
        sandbox=None,
        mcp_ids=["m1"],
        skill_ids=["sk1"],
        skill_names=["ads-export"],
        mcp_names=["ads-mcp"],
        skill_mds=[("ads-export", "# skill")],
        has_export_skill=True,
        httpmcp_ids=[],
        rag_ids=[],
        allowed_actions=["shell", "mcp"],
        save_dir="",
        im_source="",
        effective_message="hello",
        message_meta={},
        workplace_dir="",
        workplace_files=[],
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def test_append_step_writes_run_steps_and_publishes():
    import asyncio

    ctx = _fake_ctx()
    state = AgentLoopState()

    async def _run():
        with patch("app.services.agent_runtime.hub.hub") as hub:
            hub.publish = AsyncMock()
            await AgentRuntime._append_step(ctx, state, {
                "type": "info",
                "action": "mcp_loaded",
                "title": "已加载 MCPs: ads-mcp",
                "status": "done",
            })
            return hub

    hub = asyncio.run(_run())
    assert len(state.run_steps) == 1
    assert state.run_steps[0]["action"] == "mcp_loaded"
    hub.publish.assert_awaited_once()
    payload = hub.publish.await_args.args[1]
    assert payload["type"] == "step"
    assert payload["op"] == "append"
    assert payload["index"] == 0


def test_slim_and_save_assistant_meta_includes_steps():
    ctx = _fake_ctx()
    added = []

    class _DB:
        def add(self, row):
            added.append(row)

        def commit(self):
            pass

    ctx.db = _DB()
    long_body = ("确认 ZEC 无挂单。\n" * 40) + ("尾段数据 " * 50)
    assert len(long_body) > 200
    steps = [
        {"type": "info", "action": "skill_loaded", "title": "已加载 Skills: ads-export", "status": "done"},
        {
            "type": "llm",
            "iteration": 1,
            "title": "LLM 推理 (第 1 轮)",
            "status": "done",
            "preview": long_body[:200],
            "content": long_body,
            "hidden": False,
        },
        {
            "type": "tool",
            "action": "mcp_tool_call",
            "title": "[mcp] ok",
            "status": "done",
            "content": "x" * 900,
        },
    ]
    AgentRuntime._save_assistant_message(ctx, "final reply", steps=steps)
    assert len(added) == 1
    meta = json.loads(added[0].meta)
    assert meta["step_count"] >= 2
    actions = {s.get("action") for s in meta["steps"]}
    assert "skill_loaded" in actions
    assert any(s.get("type") == "llm" for s in meta["steps"])
    llm = next(s for s in meta["steps"] if s.get("type") == "llm")
    assert len(llm.get("preview") or "") <= 300
    assert len(llm.get("content") or "") > 200
    assert "确认 ZEC 无挂单" in (llm.get("content") or "")
    # Successful tool payload is retained for the expandable execution audit.
    tool = next(s for s in meta["steps"] if s.get("action") == "mcp_tool_call")
    assert "content" not in tool or len(tool.get("content") or "") <= 12_000


def test_slim_steps_keeps_long_llm_content_with_short_preview():
    long_body = "A" * 500 + "\n## 盘面\n" + "B" * 800
    steps = [{
        "type": "llm",
        "iteration": 34,
        "title": "LLM 推理 (第 34 轮)",
        "status": "done",
        "preview": AgentRuntime._llm_step_preview(long_body),
        "content": long_body,
    }]
    slim = AgentRuntime._slim_steps_for_meta(steps)
    assert len(slim) == 1
    assert slim[0]["type"] == "llm"
    assert len(slim[0]["preview"]) <= 300
    assert len(slim[0]["preview"]) < len(long_body)
    assert len(slim[0]["content"]) == len(long_body)
    assert slim[0]["content"].startswith("A" * 50)

def test_save_assistant_empty_reply_and_zero_steps_gets_placeholder():
    ctx = _fake_ctx(mcp_ids=[], has_export_skill=False)

    class _DB:
        def __init__(self):
            self.rows = []

        def add(self, row):
            self.rows.append(row)

        def commit(self):
            pass

    ctx.db = _DB()
    AgentRuntime._save_assistant_message(ctx, "   ", steps=[])
    meta = json.loads(ctx.db.rows[0].meta)
    assert meta["step_count"] >= 1
    assert "未产生文字回复" in (ctx.db.rows[0].content or "")
    assert meta["steps"]


def test_llm_step_preview_keeps_non_empty_for_collapse_guard():
    prev = AgentRuntime._llm_step_preview("FINAL: 完成导出\nMCP: list_ads_views {}")
    assert prev
    assert "MCP:" not in prev
    assert AgentRuntime._llm_step_preview("") == "模型返回空正文/不可执行工具调用"


def test_native_tool_step_preview_lists_tool_actions():
    steps = [
        SimpleNamespace(action="mcp_tool_call"),
        SimpleNamespace(action="shell"),
    ]
    prev = AgentRuntime._native_tool_step_preview(steps)
    assert prev == "工具调用: [mcp_tool_call], [shell]"
    assert "不可执行" not in prev


def test_llm_step_preview_strips_final_meta_reasoning():
    prev = AgentRuntime._llm_step_preview(
        "Wait, I notice my previous turn ended without the explicit FINAL marker.\n"
        "Let me re-output with proper FINAL marker.\n"
        "FINAL: sql有语法错误，请检查括号。"
    )
    assert "Wait" not in prev
    assert "Let me" not in prev
    assert prev == "sql有语法错误，请检查括号。"


def test_llm_step_preview_strips_minimax_final_glue():
    prev = AgentRuntime._llm_step_preview("[<FINAL: 当前盘面快照")
    assert prev == "当前盘面快照"
    assert "[<" not in prev


def test_save_assistant_meta_strips_protocol_leak_in_step_preview():
    ctx = _fake_ctx()
    added = []

    class _DB:
        def add(self, row):
            added.append(row)

        def commit(self):
            pass

    ctx.db = _DB()
    steps = [{
        "type": "llm",
        "iteration": 1,
        "title": "LLM 推理 (第 1 轮)",
        "status": "done",
        "preview": (
            "Actually I think the previous turn already had FINAL marker.\n"
            "FINAL: 查询失败，请修正 SQL。"
        ),
    }]
    AgentRuntime._save_assistant_message(ctx, "final reply", steps=steps)
    meta = json.loads(added[0].meta)
    assert meta["steps"][0]["preview"] == "查询失败，请修正 SQL。"
