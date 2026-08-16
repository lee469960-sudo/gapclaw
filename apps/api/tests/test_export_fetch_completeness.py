"""Fetch completeness + cross-run adaptive budget (row-count / OpenClaw loop)."""

import json
from pathlib import Path

from app.services.react_engine import (
    _EXPORT_QUERY_BUDGET_TYPE_B,
    _EXPORT_QUERY_BUDGET_TYPE_B_MAX,
    _compute_adaptive_export_budget,
    _export_roles_ready_for_analyze,
    _fact_roles_needing_continue,
    _parse_export_todos,
    _type_b_prefilled_plan_ready,
    _write_export_run_state,
)
from app.services.skill_lesson import load_recent_fetch_gap_hints
from app.services.workplace import ensure_workplace

_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "type_b_us_jul2026_brief.txt"


def test_roles_ready_requires_user_short_page():
    # Roles covered but user not short → not ready
    assert (
        _export_roles_ready_for_analyze(
            missing_roles=[],
            has_data=True,
            user_fetch_complete=False,
            user_pages=2,
        )
        is False
    )
    # Short page → ready
    assert (
        _export_roles_ready_for_analyze(
            missing_roles=[],
            has_data=True,
            user_fetch_complete=True,
            user_pages=2,
        )
        is True
    )
    # At cap without short page → ready
    assert (
        _export_roles_ready_for_analyze(
            missing_roles=[],
            has_data=True,
            user_fetch_complete=False,
            user_pages=6,
        )
        is True
    )
    # Missing roles → not ready
    assert (
        _export_roles_ready_for_analyze(
            missing_roles=["cash"],
            has_data=True,
            user_fetch_complete=True,
            user_pages=1,
        )
        is False
    )


def test_fact_need_continue_when_full_page():
    from app.services.react_engine import _EXPORT_FULL_PAGE_ROWS

    need = _fact_roles_needing_continue(
        ["user", "pay", "cash", "bet"],
        {"view_result_pay_order_log": 1},
        {"view_result_pay_order_log": _EXPORT_FULL_PAGE_ROWS + 50},
        budget_left=5,
    )
    assert "pay" in need
    need2 = _fact_roles_needing_continue(
        ["user", "pay"],
        {"view_result_pay_order_log": 3},
        {"view_result_pay_order_log": _EXPORT_FULL_PAGE_ROWS + 50},
        budget_left=5,
    )
    assert need2 == []  # at per-view cap


def test_type_b_default_budget_18():
    brief = _FIXTURE.read_text(encoding="utf-8")
    todos = _parse_export_todos(brief)
    roles = ["user", "pay", "cash", "bet", "channel", "game"]
    assert not _type_b_prefilled_plan_ready(todos, roles, "multi_fact")
    budget, hint = _compute_adaptive_export_budget(
        type_b=True, prior_state=None, current_tw_label="",
    )
    assert budget == _EXPORT_QUERY_BUDGET_TYPE_B
    assert hint == ""


def test_adaptive_budget_bumps_after_truncation():
    prior = {
        "budget": 18,
        "user_fetch_complete": False,
        "user_pages": 3,
        "user_truncated": True,
        "time_window": {"label": "美国东部时间 2026-07-21 至 2026-07-31"},
        "fact_truncated_roles": ["pay"],
    }
    budget, hint = _compute_adaptive_export_budget(
        type_b=True,
        prior_state=prior,
        current_tw_label="美国东部时间 2026-07-21 至 2026-07-31",
    )
    assert budget == min(_EXPORT_QUERY_BUDGET_TYPE_B_MAX, 22)
    assert "上轮拉取缺口" in hint
    assert "pay" in hint


def test_write_run_state_completeness_fields(tmp_path, monkeypatch):
    from app.services import workplace as wp
    from app.services import react_engine as re

    sid = "sbx-completeness"
    monkeypatch.setattr(wp, "_wp_root", lambda _s: tmp_path / _s / "workplace")
    # _write_workplace uses agent_tools path — patch write via workplace ensure + direct file
    root = ensure_workplace(sid)
    run_id = "1785000000001"

    class _FakeSandbox:
        id = sid

    # Bypass _write_workplace: call payload builder path by writing after
    called: dict = {}

    def _fake_write(sandbox, rel, content):
        called["rel"] = rel
        called["content"] = content
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return f"已写入 {rel}"

    monkeypatch.setattr(re, "_write_workplace", _fake_write)
    _write_export_run_state(
        _FakeSandbox(),  # type: ignore[arg-type]
        run_id,
        phase="fetch",
        mcp_query_count=5,
        budget=18,
        todos=[],
        user_fetch_complete=False,
        user_pages=3,
        user_truncated=True,
        fact_truncated_roles=["cash"],
        deliverable_rows=1000,
        export_contract={
            "version": "export_contract.v1",
            "task_spec": {"task_type": "multi_fact"},
        },
        repair_plan={
            "status": "repairable",
            "actions": [{"action_type": "fetch_noncore_or_keep_incomplete"}],
        },
    )
    assert called.get("rel") == f"task/{run_id}/_run_state.json"
    obj = json.loads(called["content"])
    assert obj["user_fetch_complete"] is False
    assert obj["user_pages"] == 3
    assert obj["user_truncated"] is True
    assert obj["fact_truncated_roles"] == ["cash"]
    assert obj["deliverable_rows"] == 1000
    assert obj["export_contract"]["version"] == "export_contract.v1"
    assert obj["repair_plan"]["status"] == "repairable"
    assert obj["repair_plan"]["actions"][0]["action_type"] == "fetch_noncore_or_keep_incomplete"


def test_load_recent_fetch_gap_hints(tmp_path, monkeypatch):
    from app.services import workplace as wp
    from app.services import skill_lesson as sl

    sid = "sbx-gaps"
    monkeypatch.setattr(wp, "_wp_root", lambda _s: tmp_path / _s / "workplace")
    monkeypatch.setattr(sl, "ensure_workplace", lambda s: ensure_workplace(s))
    root = ensure_workplace(sid)
    run = root / "task" / "1785000000002"
    run.mkdir(parents=True)
    (run / "_run_state.json").write_text(
        json.dumps(
            {
                "user_pages": 2,
                "user_fetch_complete": False,
                "user_truncated": True,
                "fact_truncated_roles": ["bet"],
                "missing_roles": [],
                "budget": 18,
                "deliverable_rows": 800,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    hint = load_recent_fetch_gap_hints(sid)
    assert "近期拉取缺口" in hint
    assert "user未短页" in hint or "截断" in hint
    assert "bet" in hint
