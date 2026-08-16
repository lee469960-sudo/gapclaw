"""DecisionEngine unit tests — decide() pipeline, stalling, and AgentAction output."""

from app.services.agent_runtime.decision_engine import DecisionEngine
from app.services.agent_runtime.loop_state import AgentLoopState
from app.services.agent_runtime.result_types import Decision, ObservationOutcome
from app.services.agent_runtime.types import ActionType, AgentAction, VerificationResult


# ---- reply classification ----

def test_classify_final():
    assert DecisionEngine.classify_reply("FINAL: report done") == "final"

def test_classify_plan():
    assert DecisionEngine.classify_reply("PLAN:\n- step 1: fetch data") == "plan"

def test_classify_tool():
    assert DecisionEngine.classify_reply("MCP: query_ads_view") == "tool"

def test_classify_text():
    assert DecisionEngine.classify_reply("Let me think about this") == "text"

def test_classify_empty():
    assert DecisionEngine.classify_reply("") == "empty"
    assert DecisionEngine.classify_reply("   ") == "empty"


# ---- is_final_reply ----

def test_is_final_reply():
    assert DecisionEngine.is_final_reply("FINAL: done")
    assert DecisionEngine.is_final_reply("FINAL：完成")
    assert not DecisionEngine.is_final_reply("MCP: query")

def test_looks_like_tool_call():
    assert DecisionEngine.looks_like_tool_call("MCP: query_ads_view")
    assert DecisionEngine.looks_like_tool_call("SHELL: ls")
    assert DecisionEngine.looks_like_tool_call("WRITE: file.txt")
    assert not DecisionEngine.looks_like_tool_call("FINAL: done")
    assert not DecisionEngine.looks_like_tool_call("hello")


# ---- is_stalling ----

def test_is_stalling_empty_streak():
    assert DecisionEngine.is_stalling(empty_llm_streak=4, max_empty_streak=3)

def test_is_stalling_no_progress():
    assert DecisionEngine.is_stalling(no_progress=7, max_no_progress=6)

def test_is_stalling_no_tool_no_progress():
    assert DecisionEngine.is_stalling(no_progress=4, ran_any_tool=False, tools_effective=False)

def test_is_stalling_not_stalling():
    assert not DecisionEngine.is_stalling(empty_llm_streak=1, no_progress=0)

def test_is_stalling_explicit_final_bypass():
    assert not DecisionEngine.is_stalling(
        empty_llm_streak=5, had_explicit_final=True
    )

def test_is_stalling_from_state():
    state = AgentLoopState(empty_llm_streak=5, no_progress=3)
    assert DecisionEngine.is_stalling(state)

def test_is_stalling_state_not_stalling():
    state = AgentLoopState(empty_llm_streak=0, no_progress=1, ran_any_tool=True)
    assert not DecisionEngine.is_stalling(state)


# ---- is_task_complete ----

def test_is_task_complete_explicit():
    assert DecisionEngine.is_task_complete("", had_explicit_final=True)

def test_is_task_complete_reply():
    assert DecisionEngine.is_task_complete("FINAL: i'm done")

def test_is_task_complete_soft():
    assert DecisionEngine.is_task_complete("some text", soft_finish_requested=True)

def test_is_task_complete_export_finalize():
    assert DecisionEngine.is_task_complete(
        "analyzing...", export_like=True, export_phase="finalize",
        finalize_hint_injected=True,
    )

def test_is_task_complete_not_complete():
    assert not DecisionEngine.is_task_complete("hello world")


# ---- decide() pipeline ----

def test_decide_final():
    d = DecisionEngine.decide("FINAL: task complete")
    assert d == Decision.FINISH

def test_decide_empty_ok():
    d = DecisionEngine.decide("")
    assert d == Decision.CONTINUE

def test_decide_empty_stalling():
    state = AgentLoopState(empty_llm_streak=5, no_progress=0)
    d = DecisionEngine.decide("", state=state)
    assert d == Decision.REPLAN

def test_decide_tool_success():
    d = DecisionEngine.decide(
        "MCP: query_ads_view {\"view\":\"x\"}",
        action="mcp_tool_call",
        normalized='MCP: query_ads_view {"view":"x"}',
        tool_result='{"data":[{"col":1}]}',
    )
    assert d == Decision.CONTINUE

def test_decide_tool_failure():
    d = DecisionEngine.decide(
        "MCP: query_ads_view",
        action="mcp_tool_call",
        tool_result="MCP 调用失败: timeout",
    )
    assert d == Decision.SWITCH_APPROACH

def test_decide_tool_param_error():
    # Realistic enriched MCP failure: contains error indicator + missing-view keyword
    d = DecisionEngine.decide(
        "MCP: query_ads_view",
        action="mcp_tool_call",
        tool_result="error: 缺少 view_name — MCP 调用失败",
    )
    assert d == Decision.REPAIR_PARAMS

def test_decide_text_stalling():
    state = AgentLoopState(no_progress=8, empty_llm_streak=0)
    d = DecisionEngine.decide("random text", state=state)
    assert d == Decision.REPLAN

def test_decide_verification_triggers_replan():
    verif = VerificationResult(
        success=False, confidence=0.9, reason="goal not met",
        next_action="replan",
    )
    d = DecisionEngine.decide(
        "MCP: query_ads_view", action="mcp_tool_call",
        tool_result="some data", verification=verif,
    )
    assert d == Decision.REPLAN

def test_decide_had_explicit_final_from_state():
    state = AgentLoopState(had_explicit_final=True)
    d = DecisionEngine.decide("some text", state=state)
    assert d == Decision.FINISH

def test_decide_export_finalize_complete():
    state = AgentLoopState(finalize_hint_injected=True)
    d = DecisionEngine.decide(
        "", state=state, export_like=True, export_phase="finalize",
    )
    assert d == Decision.FINISH


# ---- decide_next_action() ----

def test_decide_next_action_final():
    aa = DecisionEngine.decide_next_action("FINAL: done")
    assert aa.type == ActionType.FINISH
    assert aa.raw_reply == "FINAL: done"

def test_decide_next_action_replan():
    state = AgentLoopState(empty_llm_streak=5)
    aa = DecisionEngine.decide_next_action("", state=state)
    assert aa.type == ActionType.REPLAN

def test_decide_next_action_tool():
    aa = DecisionEngine.decide_next_action(
        "MCP: query_ads_view {\"view\":\"x\"}",
        action="mcp_tool_call",
        normalized='MCP: query_ads_view {"view":"x"}',
        tool_result="some result",
    )
    assert aa.type in (ActionType.MCP, ActionType.TOOL)

def test_decide_next_action_plan():
    aa = DecisionEngine.decide_next_action("PLAN: prepare")
    assert aa.type == ActionType.LLM

def test_decide_next_action_text():
    aa = DecisionEngine.decide_next_action("I need to think more")
    assert aa.type == ActionType.LLM
    assert aa.raw_reply == "I need to think more"


# ---- classify_tool_outcome ----

def test_classify_tool_outcome_none():
    outcome = DecisionEngine.classify_tool_outcome(None)
    assert outcome == ObservationOutcome.TOOL_FAILURE

def test_classify_tool_outcome_empty():
    outcome = DecisionEngine.classify_tool_outcome("")
    assert outcome == ObservationOutcome.TOOL_FAILURE

def test_classify_tool_outcome_success():
    outcome = DecisionEngine.classify_tool_outcome(
        '{"data":[{"a":1}]}', action="mcp_tool_call",
    )
    assert outcome == ObservationOutcome.SUCCESS

def test_classify_tool_outcome_missing_view():
    outcome = DecisionEngine.classify_tool_outcome(
        "错误: 缺少 view", action="mcp_tool_call",
    )
    assert outcome == ObservationOutcome.PARAM_ERROR

def test_classify_tool_outcome_forbidden():
    outcome = DecisionEngine.classify_tool_outcome(
        "error: 操作被禁止 限流拦截 — MCP 调用失败", action="mcp_tool_call",
    )
    assert outcome == ObservationOutcome.PLAN_ERROR


# ---- outcome_to_decision ----

def test_outcome_to_decision():
    assert DecisionEngine.outcome_to_decision(ObservationOutcome.SUCCESS) == Decision.CONTINUE
    assert DecisionEngine.outcome_to_decision(ObservationOutcome.PARAM_ERROR) == Decision.REPAIR_PARAMS
    assert DecisionEngine.outcome_to_decision(ObservationOutcome.TOOL_FAILURE) == Decision.SWITCH_APPROACH
    assert DecisionEngine.outcome_to_decision(ObservationOutcome.INFO_GAP) == Decision.SEARCH_MORE
    assert DecisionEngine.outcome_to_decision(ObservationOutcome.PLAN_ERROR) == Decision.REPLAN
    assert DecisionEngine.outcome_to_decision(ObservationOutcome.COMPLETE) == Decision.FINISH


# ---- clean_display_text ----

def test_clean_display_text_strips_protocol():
    cleaned = DecisionEngine.clean_display_text("Hello\nSHELL: ls\nWorld")
    assert "SHELL:" not in cleaned
    assert "Hello" in cleaned

def test_clean_final_answer():
    cleaned = DecisionEngine.clean_final_answer("FINAL: the answer is 42")
    assert "FINAL:" not in cleaned
    assert "the answer is 42" in cleaned


def test_clean_final_answer_keeps_only_last_final_payload():
    raw = (
        "完成。\n\n"
        "Actually I think the previous turn already had FINAL marker.\n"
        "Let me structure the response.\n\n"
        "FINAL:\n查询结果：注册人数为 364。"
    )
    cleaned = DecisionEngine.clean_final_answer(raw)

    assert cleaned == "查询结果：注册人数为 364。"
    assert "Actually" not in cleaned
    assert "Let me structure" not in cleaned
