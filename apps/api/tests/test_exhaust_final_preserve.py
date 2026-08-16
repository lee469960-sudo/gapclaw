"""Exhaust fallback must not wipe useful finals; data_query skips PLAN gate floor."""

from __future__ import annotations

from types import SimpleNamespace

from app.services.react_engine import (
    _TOOL_INTENT_ITER_FLOOR,
    _build_exhaust_fallback,
    _effective_max_iterations,
)


def test_exhaust_preserves_existing_final_non_export():
    text, paths = _build_exhaust_fallback(
        sandbox=None,
        save_dir="",
        saved_paths=[],
        progress_lines=["policy=generic reason=x"],
        last_reply="ignored prose",
        final="请问要查哪一天的注册人数？",
        mcp_results=[],
        export_like=False,
        max_iters=2,
    )
    assert "请问要查哪一天" in text
    assert "任务轮次已用尽" not in text
    assert paths == []


def test_exhaust_uses_last_reply_when_final_empty():
    text, _ = _build_exhaust_fallback(
        sandbox=None,
        save_dir="",
        saved_paths=[],
        progress_lines=["policy=generic"],
        last_reply="意图已识别：注册人数。请补充日期。",
        final="",
        mcp_results=[],
        export_like=False,
        max_iters=16,
    )
    assert "注册人数" in text
    assert "任务轮次已用尽" not in text


def test_exhaust_empty_message_actionable_not_blunt():
    text, _ = _build_exhaust_fallback(
        sandbox=None,
        save_dir="",
        saved_paths=[],
        progress_lines=["policy=generic"],
        last_reply="",
        final="",
        mcp_results=[],
        export_like=False,
        max_iters=2,
    )
    assert "任务轮次已用尽" not in text
    assert "最大轮次" in text or "提高最大轮次" in text
    assert "MCP" in text or "重试" in text


def test_tool_intent_iteration_floor():
    agent = SimpleNamespace(max_iterations=2)
    assert _effective_max_iterations(agent, needs_tools=True) == _TOOL_INTENT_ITER_FLOOR
    assert _effective_max_iterations(agent, needs_tools=False) == 2
    agent150 = SimpleNamespace(max_iterations=150)
    assert _effective_max_iterations(agent150, needs_tools=True) == 150


def test_data_query_skips_generic_plan_gate_logic():
    """Mirror react_engine gate: data_query → generic_phase empty."""
    export_like = False
    turn_intent = SimpleNamespace(intent="data_query", needs_tools=True)
    require_plan_gate = True
    skip_generic_plan = (turn_intent.intent or "").strip() == "data_query"
    generic_phase = (
        "plan"
        if (
            not export_like
            and not skip_generic_plan
            and require_plan_gate
            and turn_intent.needs_tools
        )
        else ""
    )
    assert skip_generic_plan is True
    assert generic_phase == ""

    other = SimpleNamespace(intent="other_tools", needs_tools=True)
    skip_other = (other.intent or "").strip() == "data_query"
    phase_other = (
        "plan"
        if (not export_like and not skip_other and require_plan_gate and other.needs_tools)
        else ""
    )
    assert phase_other == "plan"
