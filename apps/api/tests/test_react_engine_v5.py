"""react-engine-v5 regression tests.

Covers the eight defects fixed in the change: non-FINAL exit persistence (D1),
native tool_calls full pairing (D2), dynamic-layer truncation protection (D3),
completion-review rejection convergence + fix_list backfill (D4/D5), bounded
mcp_results (D6), and native role:tool in-loop trimming (D7).
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.services.agent_runtime.context_manager import ContextManager
from app.services.agent_runtime.loop_state import AgentLoopState
from app.services.agent_runtime.runtime import (
    AgentRuntime,
    _load_run_state,
    _save_run_state,
)
from app.services.llm_client import ChatResult, fit_messages_to_context
from app.services.mcp_client import McpSessionManager


def _make_db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _fake_ctx(db, *, max_iterations=10, allowed_actions=("shell",), memory=""):
    return SimpleNamespace(
        agent=SimpleNamespace(
            id="a1", llm_timeout=30, sandbox_id=None, name="t",
            max_iterations=max_iterations, prompt="You are a test agent.", memory=memory,
        ),
        session_id="s1",
        chat_key="a1:s1",
        user_message="导出报表",
        username="u",
        db=db,
        llm=SimpleNamespace(id="llm1"),
        sandbox=None,
        mcp_ids=["m1"],
        skill_ids=[],
        skill_names=[],
        mcp_names=["ads-mcp"],
        skill_mds=[],
        httpmcp_ids=[],
        rag_ids=[],
        allowed_actions=list(allowed_actions),
        save_dir="",
        im_source="",
        note_content="",
        message_meta={},
    )


def _run(ctx, chat):
    async def _go():
        with patch("app.services.llm_client.chat_completion", new=chat), patch(
            "app.services.agent_runtime.system_prompt.SystemPromptBuilder.build_tools_desc",
            new=AsyncMock(return_value="- shell: SHELL: <cmd>"),
        ):
            return await AgentRuntime().run(ctx)

    return asyncio.run(_go())


# ---- 9.1: non-FINAL exit persistence (budget exhaustion / LLM failure) ----


def test_budget_exhaustion_persists_latest_state():
    db = _make_db()
    ctx = _fake_ctx(db, max_iterations=2)
    _save_run_state(ctx, AgentLoopState(
        goal="导出报表",
        subtasks=[{"text": "步骤1", "status": "pending"}],
        last_reply="",
    ))

    async def _chat(llm, messages, **kwargs):
        if kwargs.get("collect_native"):
            return ChatResult(text="仍在处理中，未结束")
        return "已尽力完成部分"  # distill summary

    result = _run(ctx, _chat)

    assert "已尽力完成部分" in result
    # The budget-exhaustion branch re-saved the checkpoint with the latest reply.
    loaded = _load_run_state(ctx)
    assert loaded is not None
    assert loaded.last_reply == "仍在处理中，未结束"
    assert loaded.subtasks == [{"text": "步骤1", "status": "pending"}]


def test_llm_failure_persists_and_reports_kept():
    db = _make_db()
    ctx = _fake_ctx(db, max_iterations=3)
    _save_run_state(ctx, AgentLoopState(
        goal="导出报表",
        subtasks=[{"text": "步骤1", "status": "pending"}],
    ))

    result = _run(ctx, AsyncMock(side_effect=RuntimeError("boom")))

    # Persisted (resumable state present) → the copy says "已保留".
    assert "已保留" in result
    assert _load_run_state(ctx) is not None


def test_llm_failure_without_state_reports_paused():
    db = _make_db()
    ctx = _fake_ctx(db, max_iterations=3)

    result = _run(ctx, AsyncMock(side_effect=RuntimeError("boom")))

    # Nothing resumable → no checkpoint written, copy says "已暂停".
    assert "已暂停" in result
    assert _load_run_state(ctx) is None


# ---- 9.2: native tool_calls blocked / skipped also backfill role:tool ----


def test_native_blocked_backfills_synthetic_result():
    db = _make_db()
    ctx = _fake_ctx(db, allowed_actions=["shell"])  # mcp_tool_call NOT allowed
    main_calls: list[list[dict]] = []

    async def _chat(llm, messages, **kwargs):
        if kwargs.get("collect_native"):
            main_calls.append(list(messages))
            if len(main_calls) == 1:
                return ChatResult(content="", tool_calls=[{
                    "id": "call_1",
                    "function": {"name": "mcp_tool_call",
                                 "arguments": '{"tool_name": "q", "arguments": {}}'},
                }])
            return ChatResult(text="FINAL: 完成")
        return "PASS"  # reflect

    result = _run(ctx, _chat)
    assert result == "完成"

    second = main_calls[1]
    tool_msgs = [m for m in second if m.get("role") == "tool"]
    assert any("已阻止未启用工具" in (m.get("content") or "") for m in tool_msgs)
    # assistant tool_calls remains paired with its synthetic role:tool result.
    asst = [m for m in second if m.get("role") == "assistant" and m.get("tool_calls")]
    assert len(asst) == 1
    assert asst[0]["tool_calls"][0]["id"] == "call_1"
    assert any(m.get("tool_call_id") == "call_1" for m in tool_msgs)


def test_native_skipped_backfills_final_first_result():
    db = _make_db()
    ctx = _fake_ctx(db, allowed_actions=["shell"])
    # Completed subtasks so FINAL still reaches the Verifier; this test
    # covers FINAL-wins same-round tool skip, not the open-subtask evidence gate.
    _save_run_state(ctx, AgentLoopState(
        goal="导出报表", subtasks=[{"text": "步骤1", "status": "done"}],
    ))
    main_calls: list[list[dict]] = []
    reflect_calls = 0

    async def _chat(llm, messages, **kwargs):
        nonlocal reflect_calls
        if kwargs.get("collect_native"):
            main_calls.append(list(messages))
            if len(main_calls) == 1:
                return ChatResult(content="", tool_calls=[
                    {"id": "call_f", "function": {"name": "done", "arguments": '{"answer": "完成"}'}},
                    {"id": "call_s", "function": {"name": "shell", "arguments": '{"cmd": "ls"}'}},
                ])
            return ChatResult(text="FINAL: 完成")
        reflect_calls += 1
        if reflect_calls == 1:
            return "FAIL: 还缺东西\n修复清单：\n- 补全"
        return "PASS"

    result = _run(ctx, _chat)
    assert result == "完成"

    second = main_calls[1]
    tool_msgs = [m for m in second if m.get("role") == "tool"]
    assert any("FINAL 优先，未执行" in (m.get("content") or "") for m in tool_msgs)
    assert any(m.get("tool_call_id") == "call_s" for m in tool_msgs)


def test_native_final_rejected_by_reflect_backfills_tool_result():
    db = _make_db()
    ctx = _fake_ctx(db, allowed_actions=["shell"])
    _save_run_state(ctx, AgentLoopState(
        goal="导出报表", subtasks=[{"text": "步骤1", "status": "done"}],
    ))
    main_calls: list[list[dict]] = []
    reflect_calls = 0

    async def _chat(llm, messages, **kwargs):
        nonlocal reflect_calls
        if kwargs.get("collect_native"):
            main_calls.append(list(messages))
            if len(main_calls) == 1:
                return ChatResult(content="", tool_calls=[
                    {"id": "call_f", "function": {"name": "done", "arguments": '{"answer": "完成"}'}},
                ])
            return ChatResult(text="FINAL: 完成")
        reflect_calls += 1
        if reflect_calls == 1:
            return "FAIL: 还缺校验\n修复清单：\n- 补校验"
        return "PASS"

    result = _run(ctx, _chat)

    assert result == "完成"
    second = main_calls[1]
    tool_msgs = [m for m in second if m.get("role") == "tool"]
    assert any(m.get("tool_call_id") == "call_f" for m in tool_msgs)
    assert any("完成度复核未通过" in (m.get("content") or "") for m in tool_msgs)


# ---- 9.3: dynamic-layer truncation protection (D3) ----


def test_fit_messages_protects_volatile_layers():
    static = "【可用工具】\n" + ("TOOL_DESC " * 4000)  # large static catalog
    progress = "【本轮进度】已写入 task/1/a.json"
    coach = "【完成度反思】还缺 view_map"
    msgs = [
        {"role": "system", "content": static},
        {"role": "system", "content": progress, "volatile": True},
        {"role": "system", "content": coach, "volatile": True},
        {"role": "user", "content": "hello"},
    ]
    out, _ = fit_messages_to_context(
        msgs, max_context_tokens=100000, max_output_tokens=4000
    )
    system = out[0]["content"]
    assert coach in system
    assert progress in system
    # Volatile layers land at the tail, fully intact; the static catalog is trimmed.
    assert system.endswith(coach)
    assert system.count("TOOL_DESC ") < static.count("TOOL_DESC ")


def test_fit_messages_still_trims_plain_system():
    static = "【可用工具】\n" + ("X" * 40000)
    out, _ = fit_messages_to_context(
        [{"role": "system", "content": static}, {"role": "user", "content": "hi"}],
        max_context_tokens=100000, max_output_tokens=4000,
    )
    assert len(out[0]["content"]) < len(static)


# ---- 9.4: rejection convergence + fix_list backfill (D4/D5) ----


def test_reject_convergence_and_fix_list_backfill():
    db = _make_db()
    ctx = _fake_ctx(db, max_iterations=10)
    # R2: keep this a long task so the reject-convergence path still runs.
    _save_run_state(ctx, AgentLoopState(
        goal="导出报表", subtasks=[{"text": "步骤1", "status": "done"}],
    ))
    main_calls: list[list[dict]] = []

    async def _chat(llm, messages, **kwargs):
        if kwargs.get("collect_native"):
            main_calls.append(list(messages))
            return ChatResult(text="FINAL: 完成")
        return (
            "FAIL: 缺最终 SQL\n"
            "修复清单：\n- 补最终 SQL\n- 生成 xlsx\n"
            "修订 PLAN：\n- [ ] 补 SQL"
        )

    result = _run(ctx, _chat)

    assert result == "完成"
    assert len(main_calls) == 3  # 3 consecutive rejects → converge, accept candidate
    # fix_list carried into the coach hint on the following rounds.
    round2_system = "\n".join(
        m["content"] for m in main_calls[1] if m["role"] == "system"
    )
    assert "补最终 SQL" in round2_system
    assert "修复清单" in round2_system


# ---- 9.5: mcp_results bounded + native in-loop trim (D6/D7) ----


class _FakeMCP:
    def __init__(self, mid="m1"):
        self.id = mid
        self.protocol = "http"
        self.url = "http://example/call"
        self.headers = "{}"


def test_mcp_results_bounded_keeps_most_recent(tmp_path):
    mgr = McpSessionManager(mcp_results=[], run_ts="1700000000000", max_mcp_results=2)
    mcp = _FakeMCP()

    def _root(sid="default"):
        return tmp_path

    with patch("app.services.workplace.workplace_root", _root):
        for i in range(5):
            mgr._materialize(mcp, "tool", {"v": i}, f"data{i}", f"key{i}")

    assert len(mgr.mcp_results) == 2
    # Monotonic seq survives trimming (no file-name reuse), latest two kept.
    assert [r["seq"] for r in mgr.mcp_results] == [3, 4]
    assert (tmp_path / "task" / "1700000000000" / "mcp_result_4.json").exists()


def test_trim_tool_results_drops_old_native_groups():
    cm = ContextManager()
    for i in range(3):
        cm.push_assistant_native("", [{
            "id": f"c{i}", "function": {"name": "shell", "arguments": '{"cmd": "ls"}'},
        }])
        cm.push_native_tool_result(f"c{i}", f"result{i}")

    assert len(cm.messages) == 6
    cm.trim_tool_results(keep_recent=2)

    # 2 groups kept (assistant + role:tool each); oldest group dropped atomically.
    assert len(cm.messages) == 4
    assert [m["role"] for m in cm.messages] == ["assistant", "tool", "assistant", "tool"]
    ids = [m.get("tool_call_id") for m in cm.messages if m.get("role") == "tool"]
    assert ids == ["c1", "c2"]  # c0 dropped, no orphaned tool messages
    # Every assistant tool_calls still has its paired role:tool.
    for m in cm.messages:
        if m.get("role") == "assistant":
            assert m["tool_calls"][0]["id"] not in ("c0",)
