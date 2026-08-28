"""进度回显（task 7.1/7.2）：重复命中 / 重发 PLAN 时回显已落盘清单。

Soft only: reuses ``add_progress`` / ``add_coach_hint``, no counter / threshold.
"""

from __future__ import annotations

from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.services.agent_runtime.loop_state import AgentLoopState
from app.services.agent_runtime.runtime import (
    _apply_plan,
    _echo_materialized_manifest,
    _is_cached_reference,
)


def _cm():
    hints: list[str] = []

    class _CM:
        def add_coach_hint(self, hint):
            hints.append(hint)

        def set_task_context(self, **kwargs):
            pass

    return _CM(), hints


def test_echo_manifest_emits_progress_line_and_coach_hint():
    state = AgentLoopState()
    state.mcp_results = [
        {"path": "task/ts/mcp_result_0.json", "tool": "query_ads_view"},
        {"path": "task/ts/mcp_result_1.json", "tool": "list_views"},
    ]
    cm, hints = _cm()

    _echo_materialized_manifest(state, cm, reason="查询命中缓存")

    assert "已缓存 MCP 结果 task/ts/mcp_result_0.json" in state.progress_lines
    assert "已缓存 MCP 结果 task/ts/mcp_result_1.json" in state.progress_lines
    assert any(
        "已落盘清单" in h and "query_ads_view" in h and "list_views" in h for h in hints
    )


def test_echo_manifest_noop_without_results():
    state = AgentLoopState()
    cm, hints = _cm()

    _echo_materialized_manifest(state, cm, reason="重发 PLAN")

    assert state.progress_lines == []
    assert hints == []


def test_is_cached_reference():
    assert _is_cached_reference("该结果已缓存/已落盘 task/ts/mcp_result_0.json（123 字符）")
    assert not _is_cached_reference("ok:tools/call")
    assert not _is_cached_reference("")


def test_apply_plan_echoes_only_on_reissue():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    ctx = SimpleNamespace(
        user_message="导出数据",
        agent=SimpleNamespace(id="a1"),
        session_id="s1",
        db=db,
    )
    state = AgentLoopState()
    state.mcp_results = [{"path": "task/ts/mcp_result_0.json", "tool": "query_ads_view"}]
    cm, hints = _cm()

    # First PLAN: not a re-issue → no manifest echo.
    _apply_plan(ctx, state, cm, "- [ ] 步骤1")
    assert not any("已落盘清单" in h for h in hints)

    # Second PLAN (re-issue) → manifest echoed.
    _apply_plan(ctx, state, cm, "- [x] 步骤1")
    assert any("已落盘清单" in h and "query_ads_view" in h for h in hints)
