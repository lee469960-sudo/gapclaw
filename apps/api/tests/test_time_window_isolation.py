"""Time-window isolation + dialogue-aware _run_state fields."""

import json
from pathlib import Path

from app.services.react_engine import (
    _EXPORT_QUERY_BUDGET_TYPE_B,
    _build_export_constraints,
    _build_export_repair_system_hint,
    _build_turn_digest,
    _export_turn_state_from_intent,
    _compute_adaptive_export_budget,
    _normalize_export_fetch_budget,
    _parse_export_time_window,
    _resolve_export_time_window,
    _write_export_run_state,
)
from app.services.intent_router import TurnIntent
from app.services.skill_lesson import find_latest_run_state, find_repair_base_run_state
from app.services.workplace import ensure_workplace


_BRIEF_JUN = (
    "导出美国东部时间 2026-06-01 至 2026-08-01 充值用户\n"
    "1.用户ID\n2.充值\n3.提现\n4.下注"
)
_PRIOR_JUN = _parse_export_time_window(_BRIEF_JUN)
assert _PRIOR_JUN is not None

_PRIOR_STATE_JUN = {
    "session_id": "sess-a",
    "user_intent": "new_export",
    "source_brief": _BRIEF_JUN,
    "time_window": _PRIOR_JUN,
    "budget": 18,
    "completeness": "truncated",
    "user_fetch_complete": False,
    "user_pages": 3,
    "user_truncated": True,
    "fact_truncated_roles": ["pay"],
    "need_continue_roles": ["pay"],
    "next_actions": [
        {
            "role": "pay",
            "mcp_example": 'MCP: query_ads_view {"sql":"SELECT * FROM v OFFSET 3000"}',
            "hint": "续翻 pay",
        }
    ],
    "fetched_view_pages": {"view_result_pay_order_log": 2},
    "target_roles": ["user", "pay", "cash", "bet"],
    "missing_roles": ["bet"],
}


def test_resolve_user_july_overrides_prior_june():
    msg = (
        "按缺口补齐重新导出，时间窗改为美国东部时间 2026-07-01 至 2026-08-01"
    )
    tw, from_prior = _resolve_export_time_window(
        msg,
        source_brief=_BRIEF_JUN,
        prior_state=_PRIOR_STATE_JUN,
    )
    assert tw is not None
    assert from_prior is False
    assert tw["start_ms"] != _PRIOR_JUN["start_ms"]
    jul = _parse_export_time_window(
        "美国东部时间 2026-07-01 至 2026-08-01"
    )
    assert jul is not None
    assert tw["start_ms"] == jul["start_ms"]
    intent = _export_turn_state_from_intent(
        TurnIntent(task_relation="revise"),
        resolved_tw=tw,
        prior_state=_PRIOR_STATE_JUN,
    )
    assert intent == "window_change"
    digest = _build_turn_digest(msg, intent=intent, time_window=tw)
    assert "时间窗" in digest or "变更" in digest


def test_resolve_pure_repair_keeps_prior_june():
    msg = "按缺口补齐重新导出"
    tw, from_prior = _resolve_export_time_window(
        msg,
        source_brief=_BRIEF_JUN,
        prior_state=_PRIOR_STATE_JUN,
    )
    assert tw is not None
    assert tw["start_ms"] == _PRIOR_JUN["start_ms"]
    assert tw["end_ms"] == _PRIOR_JUN["end_ms"]
    intent = _export_turn_state_from_intent(
        TurnIntent(task_relation="continue"),
        resolved_tw=tw,
        prior_state=_PRIOR_STATE_JUN,
    )
    assert intent == "repair"


def test_continue_export_xlsx_keeps_prior_context():
    msg = "继续导出 xlsx"
    tw, _from_prior = _resolve_export_time_window(
        msg,
        source_brief=_BRIEF_JUN,
        prior_state=_PRIOR_STATE_JUN,
    )
    assert tw is not None
    assert tw["start_ms"] == _PRIOR_JUN["start_ms"]
    intent = _export_turn_state_from_intent(
        TurnIntent(task_relation="continue"),
        resolved_tw=tw,
        prior_state=_PRIOR_STATE_JUN,
    )
    assert intent == "repair"


def test_adaptive_budget_different_window_no_old_offset():
    cur = _parse_export_time_window(
        "美国东部时间 2026-07-01 至 2026-08-01"
    )
    assert cur is not None
    budget, hint = _compute_adaptive_export_budget(
        type_b=True,
        prior_state=_PRIOR_STATE_JUN,
        current_tw_label=cur["label"],
        current_tw=cur,
        allow_prior_mcp_examples=False,
    )
    assert "OFFSET" not in hint
    assert budget == _EXPORT_QUERY_BUDGET_TYPE_B


def test_adaptive_budget_same_window_ms_allows_bump():
    budget, hint = _compute_adaptive_export_budget(
        type_b=True,
        prior_state=_PRIOR_STATE_JUN,
        current_tw_label=_PRIOR_JUN["label"],
        current_tw=_PRIOR_JUN,
        allow_prior_mcp_examples=True,
    )
    assert budget > 18 or "上轮" in hint
    assert "OFFSET" in hint or "建议" in hint


def test_adaptive_empty_current_not_same_window():
    budget, hint = _compute_adaptive_export_budget(
        type_b=True,
        prior_state=_PRIOR_STATE_JUN,
        current_tw_label="",
        current_tw=None,
        allow_prior_mcp_examples=True,
    )
    assert budget == _EXPORT_QUERY_BUDGET_TYPE_B
    assert "OFFSET" not in hint


def test_fetch_budget_normalizes_low_plan_for_pay_cohort_type_b():
    budget, cap = _normalize_export_fetch_budget(
        requested_budget=14,
        current_cap=14,
        type_b=True,
        pay_cohort=True,
        full_fetch=True,
        fact_page_cap=10,
        user_page_cap=3,
    )
    assert budget > 14
    assert budget <= cap
    assert cap >= budget


def test_repair_hint_window_change_skips_prior_mcp():
    cur = _parse_export_time_window(
        "美国东部时间 2026-07-01 至 2026-08-01"
    )
    assert cur is not None
    hint = _build_export_repair_system_hint(
        user_message="时间窗改为 2026-07-01 至 2026-08-01 按缺口补齐",
        prior_state=_PRIOR_STATE_JUN,
        prior_run_id="111",
        resolved_tw=cur,
        window_changed=True,
    )
    assert "时间窗已更新" in hint
    assert "勿沿用" in hint
    assert "OFFSET 3000" not in hint


def test_write_run_state_session_and_intent(tmp_path, monkeypatch):
    from app.services import workplace as wp
    from app.services import react_engine as re
    from types import SimpleNamespace

    sid = "sbx-tw-iso"
    monkeypatch.setattr(wp, "_wp_root", lambda _s: tmp_path / _s / "workplace")
    root = ensure_workplace(sid)
    run_id = "1799990000001"
    tw = _parse_export_time_window(
        "美国东部时间 2026-07-01 至 2026-08-01"
    )
    assert tw is not None

    def _fake_write(sandbox, rel, content):
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return f"已写入 {rel}"

    monkeypatch.setattr(re, "_write_workplace", _fake_write)
    sandbox = SimpleNamespace(id=sid)
    _write_export_run_state(
        sandbox,
        run_id,
        phase="fetch",
        mcp_query_count=2,
        budget=18,
        todos=[],
        time_window=tw,
        target_roles=["user", "pay"],
        session_id="sess-a",
        user_intent="window_change",
        turn_digest="本轮变更时间窗：" + tw["label"],
        constraints=_build_export_constraints(
            time_window=tw, target_roles=["user", "pay"], source_brief="",
        ),
    )
    path = root / "task" / run_id / "_run_state.json"
    assert path.is_file()
    obj = json.loads(path.read_text(encoding="utf-8"))
    assert obj["session_id"] == "sess-a"
    assert obj["user_intent"] == "window_change"
    assert "turn_digest" in obj and obj["turn_digest"]
    assert obj["constraints"]["start_ms"] == tw["start_ms"]


def test_find_latest_prefers_same_session(tmp_path, monkeypatch):
    sid = "sbx_sess"
    root = tmp_path / sid
    other = root / "task" / "100"
    same = root / "task" / "200"
    other.mkdir(parents=True)
    same.mkdir(parents=True)
    (other / "_run_state.json").write_text(
        json.dumps({
            "session_id": "sess-other",
            "missing_roles": ["pay"],
            "time_window": _PRIOR_JUN,
            "source_brief": "other",
            "fetched_view_pages": {"view_result_pay_order_log": 1},
        }),
        encoding="utf-8",
    )
    (same / "_run_state.json").write_text(
        json.dumps({
            "session_id": "sess-a",
            "missing_roles": ["cash"],
            "time_window": _PRIOR_JUN,
            "source_brief": "same",
            "fetched_view_pages": {"view_result_cash_order_log": 1},
        }),
        encoding="utf-8",
    )
    import os
    import time
    now = time.time()
    # other is newer globally
    os.utime(same / "_run_state.json", (now - 50, now - 50))
    os.utime(other / "_run_state.json", (now, now))

    monkeypatch.setattr(
        "app.services.skill_lesson.ensure_workplace",
        lambda s: root if s == sid else Path("/nope"),
    )
    rid, st = find_latest_run_state(sid, session_id="sess-a")
    assert rid == "200"
    assert st["session_id"] == "sess-a"

    base = find_repair_base_run_state(sid, session_id="sess-a")
    assert base is not None
    assert base[0] == "200"

    # Without session filter, newest wins
    rid2, st2 = find_latest_run_state(sid)
    assert rid2 == "100"
    assert st2["session_id"] == "sess-other"


def test_run_state_lookup_never_falls_back_to_another_session(tmp_path, monkeypatch):
    sid = "sbx_strict_session"
    root = tmp_path / sid
    other = root / "task" / "100"
    other.mkdir(parents=True)
    (other / "_run_state.json").write_text(
        json.dumps({
            "session_id": "sess-other",
            "source_brief": "unrelated export",
            "fetched_view_pages": {"some_view": 1},
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "app.services.skill_lesson.ensure_workplace",
        lambda s: root if s == sid else Path("/nope"),
    )

    assert find_latest_run_state(sid, session_id="sess-new") is None
    assert find_repair_base_run_state(sid, session_id="sess-new") is None
