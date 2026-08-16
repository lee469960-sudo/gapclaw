"""Engine pull follows column_plan / whitelist — no bet→user_bet_log default."""

from __future__ import annotations

import json

from app.services.export_column_plan import apply_binding_hints_to_column_plan, build_column_plan
from app.services.react_engine import (
    _build_engine_fetch_mcp,
    _view_for_export_pull,
    _view_for_export_role,
)


def test_view_for_export_pull_total_bet_uses_everyday():
    plan = build_column_plan(["总下注"])
    plan = apply_binding_hints_to_column_plan(
        plan,
        bindings=[{"goal": "总下注", "resource": "view_result_gameuser_betstat_everyday_bygame"}],
    )
    view = _view_for_export_pull("bet", column_plan=plan)
    assert "everyday_bygame" in view
    assert "user_bet_log" not in view
    assert _view_for_export_role("bet") == ""


def test_view_for_export_pull_bet_empty_without_plan_or_whitelist():
    assert _view_for_export_pull("bet") == ""
    assert _view_for_export_pull("bet", column_plan=[], whitelist=[]) == ""
    assert _view_for_export_pull("pay") == ""
    assert _view_for_export_pull("cash") == ""
    line = _build_engine_fetch_mcp(
        "bet",
        {"start_ms": 1, "end_ms": 2},
        column_plan=[],
        whitelist=[],
    )
    assert line == ""
    assert _build_engine_fetch_mcp("pay", {"start_ms": 1, "end_ms": 2}) == ""


def test_build_engine_fetch_mcp_bet_from_plan_no_raw_sql():
    plan = build_column_plan(["总下注"])
    plan = apply_binding_hints_to_column_plan(
        plan,
        bindings=[{"goal": "总下注", "resource": "view_result_gameuser_betstat_everyday_bygame"}],
    )
    view = _view_for_export_pull("bet", column_plan=plan)
    line = _build_engine_fetch_mcp(
        "bet",
        {"start_ms": 1, "end_ms": 2},
        view=view,
        column_plan=plan,
    )
    assert line.startswith("MCP: query_ads_view ")
    args = json.loads(line.split(" ", 2)[2])
    assert "everyday_bygame" in args["view"]
    assert "user_bet_log" not in args.get("view", "")
    # Must not inherit SELECT * FROM user_bet_log
    assert "user_bet_log" not in (args.get("sql") or "")


def test_whitelist_betstat_preferred_over_raw_log():
    view = _view_for_export_pull(
        "bet",
        whitelist=[
            "view_result_user_bet_log",
            "view_result_gameuser_betstat_everyday_bygame",
        ],
    )
    assert view == "view_result_gameuser_betstat_everyday_bygame"


def test_plan_everyday_not_overridden_by_raw_in_whitelist():
    plan = build_column_plan(["总下注"])
    plan = apply_binding_hints_to_column_plan(
        plan,
        bindings=[{"goal": "总下注", "resource": "view_result_gameuser_betstat_everyday_bygame"}],
    )
    view = _view_for_export_pull(
        "bet",
        column_plan=plan,
        whitelist=["view_result_user_bet_log"],
    )
    assert "everyday_bygame" in view
