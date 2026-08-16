"""End-to-end tests for the modular ReAct loop with mocked LLM and tools."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.agent_runtime.context import AgentContext
from app.services.agent_runtime.decision_engine import DecisionEngine
from app.services.agent_runtime.result_types import Decision
from app.services.agent_runtime.runtime import AgentRuntime
from app.services.agent_runtime.types import ActionType
from app.services.intent_router import TurnIntent


def _make_mock_agent():
    agent = MagicMock()
    agent.id = "test-agent-id"
    agent.name = "TestAgent"
    agent.prompt = "You are a helpful assistant."
    agent.max_iterations = 10
    agent.llm_timeout = 30
    agent.sandbox_id = None
    agent.allowed_actions = '["shell", "file_read", "file_write", "mcp_tool_call"]'
    agent.skills = "[]"
    return agent


def _make_mock_llm():
    llm = MagicMock()
    llm.id = "test-llm-id"
    llm.model = "test-model"
    return llm


def _make_context(agent, llm, **kwargs):
    defaults = dict(
        db=MagicMock(),
        agent=agent,
        session_id="test-session",
        user_message="test message",
        username="testuser",
        llm=llm,
        mcp_ids=["test-mcp"],
        skill_ids=[],
        rag_ids=[],
        httpmcp_ids=[],
        allowed_actions=["shell", "file_read", "file_write", "mcp_tool_call"],
    )
    defaults.update(kwargs)
    return AgentContext.from_params(**defaults)


# ---- Single-turn conversational ----


def test_conversational_fast_path():
    agent = _make_mock_agent()
    llm = _make_mock_llm()
    ctx = _make_context(agent, llm, mcp_ids=[])

    async def _run():
        with patch(
            "app.services.llm_client.chat_completion",
            new=AsyncMock(return_value="Hello! How can I help you?"),
        ):
            runtime = AgentRuntime()
            result = await runtime.run(ctx)
            assert "Hello" in result
            assert "FINAL:" not in result

    asyncio.run(_run())


def test_bound_mcp_chat_intent_still_uses_conversational_path():
    agent = _make_mock_agent()
    llm = _make_mock_llm()
    ctx = _make_context(
        agent,
        llm,
        turn_intent=TurnIntent(intent="chat", reason="greeting"),
    )

    assert AgentRuntime._is_conversational(ctx) is True


def test_bound_mcp_data_query_intent_uses_tool_path():
    agent = _make_mock_agent()
    llm = _make_mock_llm()
    ctx = _make_context(
        agent,
        llm,
        turn_intent=TurnIntent(intent="data_query", reason="metric query"),
    )

    assert AgentRuntime._is_conversational(ctx) is False


def test_data_query_routes_to_contract_engine():
    agent = _make_mock_agent()
    llm = _make_mock_llm()
    ctx = _make_context(
        agent,
        llm,
        turn_intent=TurnIntent(intent="data_query", reason="metric query"),
    )

    async def _run():
        with patch(
            "app.services.react_engine.run_react_loop",
            new=AsyncMock(return_value="注册人数为 42"),
        ) as legacy:
            with patch.object(
                AgentRuntime,
                "_run_modular",
                new=AsyncMock(side_effect=AssertionError("must not use modular loop")),
            ):
                result = await AgentRuntime().run(ctx)
        assert result == "注册人数为 42"
        assert legacy.await_count == 1
        assert legacy.await_args.kwargs["persist_user_message"] is False

    asyncio.run(_run())


def test_mcp_protocol_stall_falls_back_instead_of_growing_iterations():
    agent = _make_mock_agent()
    llm = _make_mock_llm()
    ctx = _make_context(
        agent,
        llm,
        turn_intent=TurnIntent(intent="other_tools", reason="generic tool task"),
    )
    llm_calls = 0

    async def text_only(*_args, **_kwargs):
        nonlocal llm_calls
        llm_calls += 1
        return "我正在考虑如何使用 MCP，但还没有执行。"

    async def _run():
        with patch("app.services.llm_client.chat_completion", side_effect=text_only):
            with patch(
                "app.services.react_engine.run_react_loop",
                new=AsyncMock(return_value="已切换契约引擎"),
            ) as legacy:
                result = await AgentRuntime().run(ctx)
        assert result == "已切换契约引擎"
        assert legacy.await_count == 1
        assert llm_calls <= 4

    asyncio.run(_run())


def test_conversational_fallback_on_llm_failure():
    agent = _make_mock_agent()
    llm = _make_mock_llm()
    ctx = _make_context(agent, llm, mcp_ids=[])

    async def _run():
        with patch(
            "app.services.llm_client.chat_completion",
            side_effect=RuntimeError("LLM down"),
        ):
            runtime = AgentRuntime()
            result = await runtime.run(ctx)
            assert len(result) > 0

    asyncio.run(_run())


def test_modular_session_summary_uses_current_session_history():
    class _Query:
        def __init__(self, rows):
            self.rows = rows

        def filter(self, *_args, **_kwargs):
            return self

        def order_by(self, *_args, **_kwargs):
            return self

        def all(self):
            return list(self.rows)

    class _Db:
        def __init__(self):
            self.rows = [
                SimpleNamespace(role="user", content="任务一：导出游戏用户", meta="{}"),
                SimpleNamespace(role="assistant", content="已导出 980 人", meta="{}"),
            ]

        def query(self, *_args, **_kwargs):
            return _Query(self.rows)

        def add(self, row):
            self.rows.append(row)

        def commit(self):
            return None

    agent = _make_mock_agent()
    agent.description = "数据库管理员 Agent"
    llm = _make_mock_llm()
    db = _Db()
    ctx = _make_context(
        agent,
        llm,
        db=db,
        user_message="复盘当前会话中的导出任务与修复结果",
        turn_intent=TurnIntent(
            intent="chat",
            conversation_action="summarize_session",
            source="llm",
        ),
    )

    async def _run():
        with patch(
            "app.services.llm_client.chat_completion",
            new=AsyncMock(return_value="## 聊天记录总结\n\n- 游戏用户导出：980 人。"),
        ) as completion:
            result = await AgentRuntime().run(ctx)
        sent = "\n".join(
            message["content"] for message in completion.await_args.args[1]
        )
        assert "任务一：导出游戏用户" in sent
        assert "已导出 980 人" in sent
        assert "会话滚动总结" not in sent
        assert "980 人" in result
        assert any(
            '"session_summary_reply": true' in str(getattr(row, "meta", "")).lower()
            for row in db.rows
        )

    asyncio.run(_run())


def test_tool_loop_recovers_from_one_llm_call_failure():
    agent = _make_mock_agent()
    llm = _make_mock_llm()
    ctx = _make_context(
        agent,
        llm,
        turn_intent=TurnIntent(intent="other_tools", reason="generic tool task"),
    )
    replies = [
        RuntimeError("temporary transport failure"),
        "FINAL: recovered after retry",
    ]

    async def mock_llm(*_args, **_kwargs):
        value = replies.pop(0)
        if isinstance(value, Exception):
            raise value
        return value

    async def _run():
        with patch(
            "app.services.llm_client.chat_completion",
            side_effect=mock_llm,
        ):
            runtime = AgentRuntime()
            result = await runtime.run(ctx)
            assert "recovered after retry" in result
            assert "任务已暂停" not in result

    asyncio.run(_run())


# ---- Modular tool loop ----


def test_modular_loop_final_on_first_turn():
    agent = _make_mock_agent()
    llm = _make_mock_llm()
    ctx = _make_context(agent, llm)

    async def _run():
        with patch(
            "app.services.llm_client.chat_completion",
            new=AsyncMock(return_value="FINAL: Here is the report. Task complete."),
        ):
            runtime = AgentRuntime()
            result = await runtime.run(ctx)
            assert "Here is the report" in result
            assert "FINAL:" not in result

    asyncio.run(_run())


def test_modular_loop_tool_then_final():
    agent = _make_mock_agent()
    llm = _make_mock_llm()
    ctx = _make_context(agent, llm)

    replies = [
        'MCP: query_ads_view {"view":"test"}',
        "FINAL: Data fetched successfully. 100 rows returned.",
    ]
    call_count = [0]

    async def mock_llm(*args, **kwargs):
        idx = call_count[0]
        call_count[0] += 1
        if idx < len(replies):
            return replies[idx]
        return "FINAL: done"

    async def _run():
        with patch(
            "app.services.llm_client.chat_completion",
            side_effect=mock_llm,
        ):
            with patch(
                "app.services.agent_runtime.tool_executor.ToolExecutor.execute",
                new=AsyncMock(return_value='{"data":[{"a":1}]}'),
            ):
                runtime = AgentRuntime()
                result = await runtime.run(ctx)
                assert "Data fetched" in result or "successfully" in result

    asyncio.run(_run())


def test_modular_loop_blocks_shell_when_not_allowed():
    agent = _make_mock_agent()
    agent.allowed_actions = '["file_read", "file_write", "mcp_tool_call"]'
    llm = _make_mock_llm()
    ctx = _make_context(
        agent,
        llm,
        allowed_actions=["file_read", "file_write", "mcp_tool_call"],
        turn_intent=TurnIntent(intent="other_tools", reason="generic tool task"),
    )
    replies = [
        "SHELL: python3 /workplace/task/report.py",
        "FINAL: 已阻止未授权命令，任务已结束并给出明确处理结果。",
    ]

    async def mock_llm(*_args, **_kwargs):
        return replies.pop(0) if replies else "FINAL: 任务已结束并给出明确处理结果。"

    async def _run():
        from app.services.agent_runtime.hub import _running

        _running[ctx.chat_key] = True
        with patch(
            "app.services.llm_client.chat_completion",
            side_effect=mock_llm,
        ):
            with patch(
                "app.services.agent_runtime.tool_executor.ToolExecutor.execute",
                new=AsyncMock(return_value="must not run"),
            ) as execute:
                result, steps, _saved, _written = await AgentRuntime()._run_modular(ctx)
        _running[ctx.chat_key] = False

        assert "任务已结束" in result
        assert execute.await_count == 0
        assert any(s.get("action") == "permission_denied" for s in steps)
        assert all(s.get("action") != "shell" for s in steps)

    asyncio.run(_run())


def test_modular_loop_stalling_detection():
    agent = _make_mock_agent()
    agent.max_iters = 5
    llm = _make_mock_llm()
    ctx = _make_context(
        agent,
        llm,
        turn_intent=TurnIntent(intent="other_tools", reason="generic tool task"),
    )

    async def mock_llm_empty(*args, **kwargs):
        return ""

    async def _run():
        with patch(
            "app.services.llm_client.chat_completion",
            side_effect=mock_llm_empty,
        ):
            with patch(
                "app.services.react_engine.run_react_loop",
                new=AsyncMock(return_value="fallback result"),
            ):
                runtime = AgentRuntime()
                result = await runtime.run(ctx)
                assert len(result) > 0

    asyncio.run(_run())


# ---- DecisionEngine integration in loop ----


def test_decide_pipeline_integration():
    reply = 'MCP: query_ads_view {"view":"test"}'
    decision = DecisionEngine.decide(
        reply,
        action="mcp_tool_call",
        normalized=reply,
        tool_result='{"data":[{"col":1}]}',
    )
    assert decision == Decision.CONTINUE

    reply2 = "FINAL: task completed successfully"
    decision2 = DecisionEngine.decide(reply2)
    assert decision2 == Decision.FINISH

    aa = DecisionEngine.decide_next_action(reply2)
    assert aa.type == ActionType.FINISH


def test_full_pipeline_tool_error_recovery():
    d1 = DecisionEngine.decide(
        "MCP: query_ads_view",
        action="mcp_tool_call",
        tool_result="error: timeout — MCP 调用失败",
    )
    assert d1 == Decision.SWITCH_APPROACH

    d2 = DecisionEngine.decide(
        'MCP: describe_ads_view {"view_name":"test"}',
        action="mcp_tool_call",
        tool_result="schema: fields=[id, name, date]",
    )
    assert d2 == Decision.CONTINUE

    d3 = DecisionEngine.decide("FINAL: schema discovered, ready for query")
    assert d3 == Decision.FINISH


# ---- Phase transition ----


def test_phase_transitions_in_sequence():
    from app.services.agent_runtime.loop_state import AgentLoopState
    from app.services.agent_runtime.phase_manager import PhaseManager

    state = AgentLoopState(export_like=True, export_phase="discover")
    PhaseManager.enter_export_plan(state)
    assert state.export_phase == "plan"

    PhaseManager.enter_export_fetch(state, budget=10)
    assert state.export_phase == "fetch"

    PhaseManager.enter_export_analyze(state)
    assert state.export_phase == "analyze"

    PhaseManager.enter_export_finalize(state)
    assert state.export_phase == "finalize"


def test_phase_hints_generated():
    from app.services.agent_runtime.loop_state import AgentLoopState
    from app.services.agent_runtime.phase_manager import PhaseManager

    state = AgentLoopState(export_like=True, export_phase="finalize")
    hint = PhaseManager.format_finalize_hint(state, save_dir="/tmp/test")
    assert len(hint) > 0


def test_phase_finalize_hint_respects_no_shell_mode():
    from app.services.agent_runtime.loop_state import AgentLoopState
    from app.services.agent_runtime.phase_manager import PhaseManager

    state = AgentLoopState(export_like=True, export_phase="finalize")
    hint = PhaseManager.format_finalize_hint(
        state,
        save_dir="/tmp/test",
        shell_enabled=False,
    )
    assert "不要等待或请求 SHELL" in hint


# ---- BudgetManager ----


def test_budget_computation_for_export():
    from app.services.agent_runtime.budget_manager import BudgetManager
    budget, hint = BudgetManager.compute_adaptive_export_budget(
        type_b=False, prior_state=None,
    )
    assert budget > 0
    assert isinstance(hint, str)


def test_budget_computation_for_type_b():
    from app.services.agent_runtime.budget_manager import BudgetManager
    budget, hint = BudgetManager.compute_adaptive_export_budget(
        type_b=True, prior_state=None,
    )
    assert budget > 0


# ---- ContextManager full lifecycle ----


def test_context_manager_all_layers():
    from app.services.agent_runtime.context_manager import ContextManager
    from app.services.agent_runtime.types import VerificationResult

    cm = ContextManager()
    cm.set_base(system_prompt="You are an assistant.")
    cm.set_task_anchor("Task: fetch user data")
    cm.set_tools_catalog("Tools: MCP query_ads_view")
    cm.push_user_message("fetch data")
    cm.push_assistant_reply("MCP: query_ads_view")
    cm.push_tool_result('{"data":[]}', action="mcp_tool_call")
    verif = VerificationResult(success=True, confidence=0.9, reason="ok", next_action="continue")
    cm.push_observation(verif)
    cm.set_progress_block(["step 1 done"])
    cm.push_coach_hint("try next page")
    cm.trim_tool_results()

    summary = cm.layer_summary()
    assert summary["base"]["present"]
    assert summary["task_anchor"]["present"]
    assert summary["tools_catalog"]["present"]
    assert summary["coach_hint"]["present"]
    assert summary["observation"]["present"]
    assert summary["history"]["messages"] >= 2
    assert summary["tool_result"]["messages"] >= 1
    assert cm.token_estimate() > 0
