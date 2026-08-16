"""AgentRuntime integration tests — pipeline composition and loop logic."""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.agent_runtime.context_manager import ContextManager
from app.services.agent_runtime.decision_engine import DecisionEngine
from app.services.agent_runtime.loop_state import AgentLoopState
from app.services.agent_runtime.result_types import Decision, ObservationOutcome
from app.services.agent_runtime.runtime import AgentRuntime
from app.services.agent_runtime.types import (
    ActionType,
    AgentAction,
    FailureType,
    PlanStep,
    VerificationResult,
)
from app.services.agent_runtime.verifier import Verifier
from app.services.agent_runtime.replanner import Replanner, ReplanContext


# ---- DecisionEngine + Verifier integration ----

def test_decide_with_verified_tool_success():
    """DecisionEngine.decide() should return CONTINUE when tool succeeds with verification."""
    reply = 'MCP: query_ads_view {"view":"test"}'
    result = '{"data":[{"col":1}]}'
    verif = Verifier.verify_tool_result(
        action_type="mcp_tool_call",
        tool_name="query_ads_view",
        tool_args={"view": "test"},
        tool_output=result,
    )
    assert verif.success
    decision = DecisionEngine.decide(
        reply, action="mcp_tool_call",
        tool_result=result, verification=verif,
    )
    assert decision == Decision.CONTINUE


def test_decide_with_verification_replan():
    """DecisionEngine.decide() should return REPLAN when verification says replan."""
    reply = 'MCP: query_ads_view {"view":"bad"}'
    verif = VerificationResult(
        success=False,
        confidence=0.9,
        reason="goal not achieved",
        next_action="replan",
        suggestion="view not found — try list_ads_views first",
    )
    decision = DecisionEngine.decide(
        reply, action="mcp_tool_call",
        tool_result="error: view not found",
        verification=verif,
    )
    assert decision == Decision.REPLAN


def test_decide_with_param_error():
    """DecisionEngine should detect PARAM_ERROR for missing-view failures."""
    reply = 'MCP: query_ads_view'
    decision = DecisionEngine.decide(
        reply, action="mcp_tool_call",
        tool_result="error: 缺少 view — MCP 调用失败",
    )
    assert decision == Decision.REPAIR_PARAMS


# ---- DecisionEngine + Replanner integration ----

def test_replan_triggers_on_stalling():
    """When DecisionEngine detects stalling, it should return REPLAN."""
    state = AgentLoopState(empty_llm_streak=5, no_progress=0)
    decision = DecisionEngine.decide("", state=state)
    assert decision == Decision.REPLAN


def test_replanner_hint_integration():
    """Replanner should generate a coaching hint from verification results."""
    verif = VerificationResult(
        success=False,
        confidence=0.8,
        reason="step goal not met",
        next_action="replan",
        suggestion="try alternative view",
        missed_criteria=["at least 100 rows"],
    )
    ctx = ReplanContext(
        verification=verif,
        remaining_steps=[
            PlanStep(id="step-2", goal="analyze data"),
        ],
        completed_step_ids={"step-1"},
        budget_remaining=5,
        consecutive_failures=3,
    )
    hint = Replanner.generate_replan_hint(ctx)
    assert "需要调整计划" in hint
    assert "step-1" in hint
    assert "alternative view" in hint
    assert "at least 100 rows" in hint


def test_replanner_should_replan():
    """Replanner.should_replan() should detect replan conditions."""
    verif = VerificationResult(
        success=False, confidence=0.9,
        reason="fail", next_action="replan",
    )
    assert Replanner.should_replan(verif)

    assert Replanner.should_replan(None, consecutive_failures=4)
    assert Replanner.should_replan(None, step_retry_count=3, max_retries=3)
    assert Replanner.should_replan(None, budget_remaining=0)
    assert not Replanner.should_replan(None, consecutive_failures=0, budget_remaining=50)


# ---- DecisionEngine + AgentLoopState integration ----

def test_is_stalling_reads_from_state():
    """is_stalling should read empty_llm_streak from AgentLoopState."""
    state = AgentLoopState(empty_llm_streak=4, no_progress=2, ran_any_tool=False)
    assert DecisionEngine.is_stalling(state)

    state2 = AgentLoopState(empty_llm_streak=1, no_progress=1, ran_any_tool=True)
    assert not DecisionEngine.is_stalling(state2)


def test_is_task_complete_reads_from_state():
    """is_task_complete should use state flags for completion detection."""
    state = AgentLoopState(had_explicit_final=True)
    assert DecisionEngine.is_task_complete("some reply", had_explicit_final=state.had_explicit_final)

    state2 = AgentLoopState(finalize_hint_injected=True)
    assert DecisionEngine.is_task_complete(
        "", export_like=True, export_phase="finalize",
        finalize_hint_injected=state2.finalize_hint_injected,
    )


# ---- ContextManager + DecisionEngine integration ----

def test_context_manager_lifecycle():
    """ContextManager should manage message layers through a typical iteration."""
    cm = ContextManager()

    # Setup
    cm.set_base(system_prompt="You are a helpful assistant.")
    cm.push_user_message("query the database")
    assert len(cm.messages) >= 2

    # Simulate LLM reply
    cm.push_assistant_reply("MCP: query_ads_view")
    assert len(cm.messages) >= 3

    # Simulate tool result + observation
    verif = VerificationResult(
        success=True, confidence=0.85,
        reason="query returned 100 rows",
        next_action="continue",
    )
    cm.push_observation(verif)
    cm.push_tool_result('{"data":[...]}', action="mcp_tool_call")

    # Coach hint
    cm.push_coach_hint("try offset=100 for next page")
    assert cm.token_estimate() > 0

    # Trim
    cm.trim_tool_results()
    summary = cm.layer_summary()
    assert summary["base"]["present"]
    assert summary["observation"]["present"]


def test_context_manager_coach_hint_replacement():
    """push_coach_hint should replace, not append."""
    cm = ContextManager()

    cm.set_base(system_prompt="base")
    cm.push_coach_hint("hint 1")
    v1 = cm.version
    cm.push_coach_hint("hint 2")
    v2 = cm.version

    assert v2 > v1
    # Should still only have base + one coach hint
    coach_info = cm.layer_summary()["coach_hint"]
    assert coach_info["messages"] == 1


# ---- AgentRuntime structural tests ----

def test_agent_runtime_has_modular_path():
    """AgentRuntime should have the _run_modular method."""
    runtime = AgentRuntime()
    assert hasattr(runtime, "_run_modular")
    assert hasattr(runtime, "run")


def test_agent_runtime_imports_all_components():
    """All modular components should be importable from agent_runtime."""
    from app.services.agent_runtime import (
        AgentRuntime,
        AgentContext,
        AgentLoopState,
        ContextManager,
        DecisionEngine,
        Decision,
        ObservationOutcome,
        Verifier,
        Replanner,
        ToolExecutor,
        ActionType,
        AgentAction,
        PlanStep,
        VerificationResult,
        FailureType,
    )
    # Verify types are correct
    assert ActionType.TOOL.value == "tool"
    assert ActionType.FINISH.value == "finish"
    assert Decision.CONTINUE is not None
    assert ObservationOutcome.SUCCESS is not None


# ---- Verifier + DecisionEngine integration ----

def test_verifier_mcp_rows_detection():
    """Verifier should correctly parse row counts from MCP output."""
    result_json = '{"data":[{"a":1},{"a":2},{"a":3}]}'
    verif = Verifier.verify_tool_result(
        action_type="mcp_tool_call",
        tool_name="query_ads_view",
        tool_args={"view": "test"},
        tool_output=result_json,
        acceptance_criteria=[">= 1 rows"],
    )
    assert verif.success
    assert "3 rows" in verif.reason


def test_verifier_failure_classification():
    """Verifier should classify MCP failures correctly."""
    verif = Verifier.verify_tool_result(
        action_type="mcp_tool_call",
        tool_name="query_ads_view",
        tool_args={"view": "test"},
        tool_output="error: timeout — MCP 调用失败",
    )
    assert not verif.success
    assert verif.next_action == "retry"


def test_verifier_shell_error():
    """Verifier should detect shell errors."""
    verif = Verifier.verify_tool_result(
        action_type="shell",
        tool_name="shell",
        tool_args={},
        tool_output="Traceback (most recent call last):\n  File \"test.py\", line 1\nModuleNotFoundError: No module named 'xxx'",
    )
    assert not verif.success
    assert verif.next_action == "retry"


# ---- Replanner plan parsing ----

def test_replanner_parse_replan():
    """Replanner should parse PLAN blocks from LLM replies."""
    reply = """PLAN:
- [step-1] fetch user data from view_user_info | at least 100 rows
- [step-2] analyze churn rate | file written to task/result.xlsx

FINAL:"""
    steps = Replanner.parse_replan_reply(reply, [])
    assert steps is not None
    assert len(steps) == 2
    assert steps[0].id == "step-1"
    assert "fetch user data" in steps[0].goal


def test_replanner_merge():
    """Replanner.merge_replan should keep completed steps."""
    old = [
        PlanStep(id="step-1", goal="discover schema", status="completed"),
        PlanStep(id="step-2", goal="fetch data", status="failed"),
    ]
    new = [
        PlanStep(id="step-2", goal="fetch data from alternatives", plan_version=2),
    ]
    merged = Replanner.merge_replan(old, new, completed_step_ids={"step-1"})
    assert len(merged) == 2
    assert merged[0].id == "step-1"  # completed step preserved
    assert "alternatives" in merged[1].goal  # new plan for step-2


# ---- ToolRouter integration ----

def test_tool_router_filter_tool_names():
    """ToolRouter should filter actions by phase."""
    from app.services.agent_runtime.tool_router import ToolRouter

    actions = ["shell", "file_read", "file_write", "mcp_tool_call", "httpmcp_call"]
    # Finalize phase: only done
    result = ToolRouter.filter_tool_names(actions, [], export_like=True, is_finalize_phase=True)
    assert result == ["done"]

    # Analyze phase: no MCP
    result = ToolRouter.filter_tool_names(actions, [], export_like=True, is_analyze_phase=True)
    assert "mcp_tool_call" not in result
    assert "shell" in result

    # Generic: all actions
    result = ToolRouter.filter_tool_names(actions, [], export_like=False)
    assert "mcp_tool_call" in result


def test_tool_router_build_minimal():
    """build_minimal_tools_block should include essential tools."""
    from app.services.agent_runtime.tool_router import ToolRouter

    block = ToolRouter.build_minimal_tools_block(
        save_dir="/tmp", allowed_actions=["shell", "file_read", "file_write"],
    )
    assert "SHELL:" in block
    assert "FINAL:" in block
    assert "/tmp/" in block


def test_tool_router_builds_bound_mcp_catalog():
    """Bound MCP tools should survive model imports and enter the system prompt."""
    from app.services.agent_runtime.system_prompt import SystemPromptBuilder

    db = MagicMock()
    mcp = MagicMock()
    mcp.id = "mcp-1"
    mcp.name = "ads-sync-hub-mcp"
    db.query.return_value.filter.return_value.first.return_value = mcp
    agent = MagicMock()

    tools = [
        {
            "name": "list_ads_views",
            "description": "list views",
            "inputSchema": {"type": "object", "properties": {}},
        },
        {
            "name": "describe_ads_view",
            "description": "describe view",
            "inputSchema": {
                "type": "object",
                "required": ["view_name"],
                "properties": {"view_name": {"type": "string"}},
            },
        },
    ]

    async def _run():
        with patch(
            "app.services.agent_runtime.utils._get_mcp_tools_cached",
            new=AsyncMock(return_value=tools),
        ):
            return await SystemPromptBuilder.build_tools_desc(
                db=db,
                agent=agent,
                allowed=["mcp_tool_call"],
                skill_ids=[],
                mcp_ids=["mcp-1"],
                rag_ids=[],
            )

    block = asyncio.run(_run())
    assert "ads-sync-hub-mcp" in block
    assert "MCP: list_ads_views" in block
    assert "MCP: describe_ads_view" in block
    assert "required=[view_name]" in block


def test_tool_router_phase_refresh():
    """should_refresh_tools should detect meaningful phase transitions."""
    from app.services.agent_runtime.tool_router import ToolRouter

    assert ToolRouter.should_refresh_tools("discover", "fetch")
    assert ToolRouter.should_refresh_tools("fetch", "analyze")
    assert not ToolRouter.should_refresh_tools("discover", "plan")


# ---- SystemPromptBuilder conversational vs tool path ----

def test_system_prompt_conversational():
    """build_conversational_system should suppress tool protocol markers."""
    from app.services.agent_runtime.system_prompt import SystemPromptBuilder

    prompt = SystemPromptBuilder.build_conversational_system(None)
    assert "对话模式" in prompt
    assert "禁止输出 PLAN:" in prompt
    assert "FINAL:" in prompt  # the prohibition mentions FINAL


def test_system_prompt_base():
    """build_system_base should include agent prompt."""
    from app.services.agent_runtime.system_prompt import SystemPromptBuilder
    from unittest.mock import MagicMock

    agent = MagicMock()
    agent.prompt = "You are an export assistant."
    prompt = SystemPromptBuilder.build_system_base(agent)
    assert "export assistant" in prompt


# ---- run_agent (drop-in replacement for run_react_loop) ----


def test_run_agent_resolves_context_and_delegates():
    """run_agent should resolve LLM/sandbox from DB and delegate to AgentRuntime."""
    from unittest.mock import AsyncMock, MagicMock, patch
    from app.services.agent_runtime.runtime import run_agent

    agent = MagicMock()
    agent.id = "test-agent"
    agent.name = "TestAgent"
    agent.prompt = "You are helpful."
    agent.max_iters = 10
    agent.llm_timeout = 30
    agent.sandbox_id = None
    agent.llm_id = "llm-1"
    agent.allowed_actions = '["shell", "file_read"]'
    agent.skills = "[]"
    agent.mcps = '["mcp-1"]'
    agent.rags = "[]"

    mock_llm = MagicMock()
    mock_llm.id = "llm-1"
    mock_llm.model = "test-model"

    mock_db = MagicMock()

    def mock_query(model):
        mock_result = MagicMock()
        mock_result.first.return_value = mock_llm if str(model).endswith("LLMResource") else None
        return mock_result

    mock_db.query.side_effect = mock_query

    async def _run():
        with patch(
            "app.services.agent_runtime.runtime.AgentRuntime.run",
            new=AsyncMock(return_value="FINAL: test result"),
        ):
            with patch(
                "app.services.intent_router.analyze_turn_intent",
                new=AsyncMock(return_value=MagicMock(intent="data_query", is_export=False)),
            ):
                result = await run_agent(
                    mock_db, agent, "session-1", "hello", "testuser",
                )
                assert "test result" in result

    import asyncio
    asyncio.run(_run())


def test_run_agent_routes_with_recent_export_state_context():
    """The outer router must not classify repair turns without prior task evidence."""
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, MagicMock, patch

    from app.models import ChatMessage, ChatNote, LLMResource, Sandbox
    from app.services.agent_runtime.runtime import run_agent

    agent = MagicMock()
    agent.id = "test-agent"
    agent.name = "DBA"
    agent.prompt = "You are helpful."
    agent.history_length = 30
    agent.llm_timeout = 30
    agent.sandbox_id = "sandbox-1"
    agent.llm_id = "llm-1"
    agent.allowed_actions = "[]"
    agent.skills = "[]"
    agent.mcps = "[]"
    agent.rags = "[]"

    mock_llm = MagicMock(id="llm-1", model="test-model")
    mock_sandbox = MagicMock(id="sandbox-1")
    history = [
        SimpleNamespace(role="user", content="导出用户分析", meta="{}"),
        SimpleNamespace(role="assistant", content="已导出", meta="{}"),
    ]
    mock_db = MagicMock()

    def mock_query(model):
        result = MagicMock()
        chain = result.filter.return_value
        if model is LLMResource:
            chain.first.return_value = mock_llm
        elif model is Sandbox:
            chain.first.return_value = mock_sandbox
        elif model is ChatNote:
            chain.first.return_value = None
        elif model is ChatMessage:
            chain.order_by.return_value.limit.return_value.all.return_value = list(
                reversed(history)
            )
        return result

    mock_db.query.side_effect = mock_query
    intent = MagicMock(intent="export_report", is_export=True)
    analyze = AsyncMock(return_value=intent)
    prior_state = {
        "task_title": "充值用户分析",
        "source_brief": "导出充值用户分析表",
        "analyze_columns": ["用户ID", "充值金额"],
        "export_contract": {"query_contract": {"sql": "SELECT 1"}},
    }

    async def _run():
        with (
            patch("app.services.intent_router.analyze_turn_intent", new=analyze),
            patch(
                "app.services.skill_lesson.find_repair_base_run_state",
                return_value=("run-1", prior_state),
            ),
            patch(
                "app.services.agent_runtime.runtime.AgentRuntime.run",
                new=AsyncMock(return_value="done"),
            ),
        ):
            await run_agent(
                mock_db,
                agent,
                "session-1",
                "修复后重新导出",
                "testuser",
            )

    asyncio.run(_run())
    kwargs = analyze.await_args.kwargs
    assert kwargs["require_decided_relation"] is True
    assert "充值用户分析" in kwargs["conversation_context"]
    assert '"available_artifacts": ["sql"]' in kwargs["conversation_context"]


def test_fallback_to_react_engine_on_modular_failure():
    """When _run_modular raises, AgentRuntime.run() should fall back to react_engine."""
    from unittest.mock import AsyncMock, MagicMock, patch
    from app.services.agent_runtime.runtime import AgentRuntime
    from app.services.agent_runtime.context import AgentContext

    agent = MagicMock()
    agent.id = "test-agent"
    agent.name = "Test"
    agent.prompt = "You are helpful."
    agent.max_iters = 10
    agent.llm_timeout = 30
    agent.sandbox_id = None
    agent.allowed_actions = '["shell", "file_read"]'
    agent.skills = "[]"
    agent.mcps = '["mcp-1"]'

    llm = MagicMock()
    llm.id = "llm-1"
    llm.model = "test-model"

    ctx = AgentContext.from_params(
        db=MagicMock(),
        agent=agent,
        session_id="s1",
        user_message="hello",
        username="u1",
        llm=llm,
        mcp_ids=["mcp-1"],
        allowed_actions=["shell", "file_read"],
    )

    async def _run():
        with patch(
            "app.services.agent_runtime.runtime.AgentRuntime._run_modular",
            side_effect=RuntimeError("forced modular failure"),
        ):
            with patch(
                "app.services.react_engine.run_react_loop",
                new=AsyncMock(return_value="FALLBACK: result from legacy loop"),
            ):
                runtime = AgentRuntime()
                result = await runtime.run(ctx)
                assert "FALLBACK" in result
                assert "legacy loop" in result

    import asyncio
    asyncio.run(_run())


def test_fallback_does_not_double_save_user_message():
    """User is saved upfront; fallback must pass persist_user_message=False."""
    from unittest.mock import AsyncMock, MagicMock, patch
    from app.services.agent_runtime.runtime import AgentRuntime
    from app.services.agent_runtime.context import AgentContext
    from app.models import ChatMessage

    agent = MagicMock()
    agent.id = "test-agent"
    agent.name = "Test"
    agent.prompt = "You are helpful."
    agent.max_iters = 10
    agent.llm_timeout = 30
    agent.sandbox_id = None
    agent.allowed_actions = '["shell"]'
    agent.skills = "[]"
    agent.mcps = '["mcp-1"]'

    llm = MagicMock()
    llm.id = "llm-1"
    llm.model = "test-model"

    mock_db = MagicMock()
    ctx = AgentContext.from_params(
        db=mock_db,
        agent=agent,
        session_id="s1",
        user_message="hello",
        username="u1",
        llm=llm,
        mcp_ids=["mcp-1"],
        allowed_actions=["shell"],
    )

    fallback = AsyncMock(return_value="fallback done")

    async def _run():
        with patch(
            "app.services.agent_runtime.runtime.AgentRuntime._run_modular",
            side_effect=RuntimeError("forced failure"),
        ):
            with patch(
                "app.services.react_engine.run_react_loop",
                new=fallback,
            ):
                runtime = AgentRuntime()
                return await runtime.run(ctx)

    import asyncio
    result = asyncio.run(_run())

    assert result == "fallback done"
    # User row was persisted before modular (and thus before fallback)
    assert mock_db.add.called
    user_msgs = [
        c.args[0] for c in mock_db.add.call_args_list
        if isinstance(c.args[0], ChatMessage) and getattr(c.args[0], "role", None) == "user"
    ]
    assert len(user_msgs) == 1
    assert user_msgs[0].content == "hello"
    mock_db.commit.assert_called()
    fallback.assert_awaited_once()
    assert fallback.await_args.kwargs.get("persist_user_message") is False


def test_user_message_saved_before_modular():
    """ChatMessage(role=user) must exist before _run_modular is entered."""
    from unittest.mock import AsyncMock, MagicMock, patch
    from app.services.agent_runtime.runtime import AgentRuntime
    from app.services.agent_runtime.context import AgentContext
    from app.models import ChatMessage

    agent = MagicMock()
    agent.id = "test-agent"
    agent.name = "Test"
    agent.prompt = "You are helpful."
    agent.max_iters = 10
    agent.llm_timeout = 30
    agent.sandbox_id = None
    agent.allowed_actions = '["shell"]'
    agent.skills = "[]"
    agent.mcps = '["mcp-1"]'

    llm = MagicMock()
    llm.id = "llm-1"
    llm.model = "test-model"

    mock_db = MagicMock()
    ctx = AgentContext.from_params(
        db=mock_db,
        agent=agent,
        session_id="s1",
        user_message="persist me first",
        username="u1",
        llm=llm,
        mcp_ids=["mcp-1"],
        allowed_actions=["shell"],
    )

    seen_user_before_modular = {"ok": False}

    async def _modular_side_effect(_ctx):
        adds = [
            c.args[0] for c in mock_db.add.call_args_list
            if isinstance(c.args[0], ChatMessage) and getattr(c.args[0], "role", None) == "user"
        ]
        seen_user_before_modular["ok"] = (
            len(adds) == 1 and adds[0].content == "persist me first" and mock_db.commit.called
        )
        return ("FINAL: ok", [], [], 0)

    async def _run():
        with patch(
            "app.services.agent_runtime.runtime.AgentRuntime._run_modular",
            side_effect=_modular_side_effect,
        ):
            with patch(
                "app.services.agent_runtime.runtime.AgentRuntime._save_assistant_message",
            ):
                with patch(
                    "app.services.agent_runtime.runtime.AgentRuntime._publish_modular_done",
                    new=AsyncMock(),
                ):
                    runtime = AgentRuntime()
                    return await runtime.run(ctx)

    import asyncio
    result = asyncio.run(_run())

    assert result == "FINAL: ok"
    assert seen_user_before_modular["ok"] is True
