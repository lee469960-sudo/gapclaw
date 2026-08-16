"""Idle soft-converge: think-strip intent, explore≠progress, empty-LLM early stop gate."""

from __future__ import annotations

from app.services.intent_router import _extract_json_object
from app.services.react_engine import (
    _export_idle_early_finish_allowed,
    _reply_is_empty_idle,
    _shell_counts_as_progress,
    _shell_looks_like_explore,
)


def test_extract_json_object_strips_think_blocks():
    raw = (
        "<think>先推理一下导出意图……</think>\n"
        '{"intent":"export_report","reason":"导出报表","wants_deliverable":true,'
        '"query_goal":"导出用户表","metrics":["注册"],"time_window":null}'
    )
    data = _extract_json_object(raw)
    assert data is not None
    assert data["intent"] == "export_report"
    assert data["wants_deliverable"] is True


def test_extract_json_object_strips_orphan_think_closer():
    raw = (
        "reasoning noise</think>\n"
        '{"intent":"export_report","reason":"x","wants_deliverable":true,'
        '"query_goal":"g","metrics":[],"time_window":null}'
    )
    data = _extract_json_object(raw)
    assert data is not None
    assert data["intent"] == "export_report"


def test_explore_shell_not_effective_progress():
    assert _shell_looks_like_explore("SHELL: ls task/") is True
    assert _shell_counts_as_progress("SHELL: ls task/") is False
    assert _shell_looks_like_explore("SHELL: head -n 5 task/page_1.json") is True
    assert _shell_counts_as_progress("SHELL: head -n 5 task/page_1.json") is False
    assert _shell_looks_like_explore(
        "SHELL: python3 -c \"import pandas as pd; print(pd.read_json('task/x.json').info())\""
    ) is True
    assert _shell_counts_as_progress(
        "SHELL: python3 -c \"import pandas as pd; print(pd.read_json('task/x.json').info())\""
    ) is False


def test_xlsx_write_shell_is_effective_progress():
    cmd = (
        "SHELL: python3 -c \"import pandas as pd; "
        "pd.DataFrame({'用户ID':[1]}).to_excel('新注册.xlsx', index=False)\""
    )
    assert _shell_counts_as_progress(cmd) is True
    assert _shell_looks_like_explore(cmd) is False


def test_empty_llm_idle_detection():
    assert _reply_is_empty_idle("") is True
    assert _reply_is_empty_idle("   ") is True
    assert _reply_is_empty_idle("<think>hmm</think>") is True
    assert _reply_is_empty_idle("FINAL: 完成") is False
    assert _reply_is_empty_idle("SHELL: ls") is False
    assert _reply_is_empty_idle("我再检查一下列是否齐全再写表") is False


def test_empty_llm_early_finish_requires_deliverable_no_hard_gates():
    # With claimed root deliverable → may early FINAL (no hexad / bet hard gate)
    assert _export_idle_early_finish_allowed(
        export_like=True,
        export_phase="analyze",
        empty_llm_streak=2,
        has_root_deliverable=True,
    ) is True
    assert _export_idle_early_finish_allowed(
        export_like=True,
        export_phase="finalize",
        empty_llm_streak=5,
        has_root_deliverable=True,
    ) is True

    # No deliverable → never early FINAL (coach / platform write only)
    assert _export_idle_early_finish_allowed(
        export_like=True,
        export_phase="analyze",
        empty_llm_streak=5,
        has_root_deliverable=False,
    ) is False

    # Fetch window: no early FINAL even with deliverable
    assert _export_idle_early_finish_allowed(
        export_like=True,
        export_phase="fetch",
        empty_llm_streak=5,
        has_root_deliverable=True,
    ) is False

    # Streak below threshold
    assert _export_idle_early_finish_allowed(
        export_like=True,
        export_phase="analyze",
        empty_llm_streak=1,
        has_root_deliverable=True,
    ) is False
