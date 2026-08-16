"""Soft export coaching / task-anchor helpers (no hard gates)."""

from pathlib import Path

from app.services.react_engine import (
    _export_analyze_column_texts,
    _export_next_action_coach,
    _format_export_task_anchor,
    _parse_export_time_window,
    _parse_export_todos,
    _prefer_richer_export_brief,
)

_FIXTURE = (
    Path(__file__).resolve().parent / "fixtures" / "type_b_us_jul2026_brief.txt"
)


def _brief() -> str:
    return _FIXTURE.read_text(encoding="utf-8")


def test_fixture_parses_14_columns_and_time_window():
    brief = _brief()
    todos = _parse_export_todos(brief)
    cols = _export_analyze_column_texts(todos)
    assert len(cols) == 14
    assert cols[0].startswith("注册时间")
    assert "流水倍数" in cols[9]
    assert "是否有退款" in cols[13]
    tw = _parse_export_time_window(brief)
    assert tw is not None
    assert "2026-07-21" in tw["label"]
    assert "2026-07-31" in tw["label"]


def test_task_anchor_lists_all_columns():
    brief = _brief()
    cols = _export_analyze_column_texts(_parse_export_todos(brief))
    tw = _parse_export_time_window(brief)
    anchor = _format_export_task_anchor(
        source_brief=brief,
        time_window=tw,
        column_headers=cols,
        target_roles=["user", "pay", "cash", "bet", "channel", "game"],
    )
    assert "【任务锚点】" in anchor
    assert "14.是否有退款" in anchor or "14." in anchor
    assert "2026-07-21" in anchor
    assert "user" in anchor and "bet" in anchor


def test_coach_suggests_bet_and_channel_roles():
    text = _export_next_action_coach(
        phase="fetch",
        fetched_view_pages={
            "view_result_user_info": 1,
            "view_result_pay_order_log": 1,
            "view_result_cash_order_log": 1,
        },
        target_roles=["user", "pay", "cash", "bet", "channel", "game"],
        covered_roles=["user", "pay", "cash"],
        missing_roles=["bet", "channel", "game"],
        time_window={"start_ms": 1, "end_ms": 2, "label": "test"},
        column_headers=["注册时间", "用户ID", "注册渠道"],
        budget_left=8,
        user_fetch_complete=True,
        dim_views={
            "channel": "view_result_config_channel",
            "game": "view_result_config_game",
        },
    )
    assert "【下一步建议】" in text
    assert "bet" in text and "channel" in text
    assert "export-report" in text or "一次拉全" in text
    # Soft pointers for missing roles (no forced OFFSET until fact_need_continue)
    assert "拉维表" in text or "channel" in text


def test_coach_offset_when_fact_need_continue():
    text = _export_next_action_coach(
        phase="fetch",
        fetched_view_pages={"view_result_pay_order_log": 2},
        target_roles=["user", "pay"],
        covered_roles=["user", "pay"],
        missing_roles=[],
        time_window={
            "start_ms": 1,
            "end_ms": 2,
            "label": "test",
            "pay_sql_select": (
                "SELECT * FROM ads.view_result_pay_order_log "
                "WHERE create_time >= 1 AND create_time < 2"
            ),
        },
        budget_left=5,
        user_fetch_complete=True,
        fact_need_continue=["pay"],
        cohort_uid_estimate=2000,
    )
    assert "OFFSET" in text
    assert "query_ads_view" in text
    assert "一次拉全" in text or "续翻" in text


def test_coach_suggests_user_first_when_empty():
    text = _export_next_action_coach(
        phase="fetch",
        fetched_view_pages={},
        target_roles=["user", "pay", "cash", "bet"],
        missing_roles=["user", "pay", "cash", "bet"],
        time_window={
            "mcp_example": 'MCP: query_ads_view {"view":"view_result_user_info"}',
            "start_ms": 1,
            "end_ms": 2,
        },
        budget_left=12,
    )
    assert "user_info" in text or "user" in text
    assert "pay" in text


def test_coach_suggests_shell_when_roles_ready():
    text = _export_next_action_coach(
        phase="analyze",
        fetched_view_pages={
            "view_result_user_info": 1,
            "view_result_pay_order_log": 1,
            "view_result_cash_order_log": 1,
            "view_result_user_bet_log": 1,
            "view_result_config_channel": 1,
            "view_result_config_game": 1,
        },
        target_roles=["user", "pay", "cash", "bet", "channel", "game"],
        covered_roles=["user", "pay", "cash", "bet", "channel", "game"],
        missing_roles=[],
        column_headers=["注册时间", "用户ID", "总充值金额"],
        budget_left=0,
    )
    assert "SHELL" in text
    assert "注册时间" in text


def test_prefer_richer_brief():
    short = "按缺口补齐重新导出"
    full = _brief()
    assert _prefer_richer_export_brief(short, full) == full.strip() or "注册时间" in (
        _prefer_richer_export_brief(short, full)
    )
