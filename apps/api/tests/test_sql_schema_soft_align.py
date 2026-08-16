"""soft_align_sql_to_schema: strip unknown cols using describe fields only."""

from __future__ import annotations

from app.services.export_column_plan import (
    soft_align_sql_to_schema,
    sql_unknown_identifiers,
)

_BET_LOG_FIELDS = {
    "uid",
    "bet_sc",
    "win_sc",
    "create_time",
    "type",
    "game_id",
    "after_result_sc",
}


def test_soft_align_strips_win_lsc_from_sum():
    sql = (
        "SELECT uid, SUM(bet_sc + bet_lsc) AS total_bet, "
        "SUM(win_sc + win_lsc) AS total_return, COUNTIf(type = 1) AS sc_bet_cnt "
        "FROM ads.view_result_user_bet_log "
        "WHERE create_time >= 1 AND create_time < 2 GROUP BY uid"
    )
    aligned, notes, unknown = soft_align_sql_to_schema(sql, _BET_LOG_FIELDS)
    assert "win_lsc" not in aligned
    assert "bet_lsc" not in aligned
    assert "SUM(win_sc)" in aligned.replace(" ", "")
    assert "SUM(bet_sc)" in aligned.replace(" ", "")
    assert any("win_lsc" in n for n in notes)
    assert "win_lsc" not in unknown
    assert "bet_lsc" not in unknown


def test_soft_align_keeps_known_columns():
    sql = (
        "SELECT uid, SUM(win_sc) AS total_return "
        "FROM ads.view_result_user_bet_log WHERE type = 1 GROUP BY uid"
    )
    aligned, notes, unknown = soft_align_sql_to_schema(sql, _BET_LOG_FIELDS)
    assert aligned == sql or "SUM(win_sc)" in aligned
    assert not notes
    assert not unknown


def test_sql_unknown_ignores_aliases_and_relations():
    sql = (
        "SELECT uid, SUM(win_sc) AS total_return "
        "FROM ads.view_result_user_bet_log GROUP BY uid"
    )
    unk = sql_unknown_identifiers(sql, _BET_LOG_FIELDS)
    assert "total_return" not in unk
    assert "view_result_user_bet_log" not in unk
    assert "ads" not in unk


def test_soft_align_drops_bare_unknown_select_col():
    sql = "SELECT uid, win_lsc, bet_sc FROM ads.view_result_user_bet_log"
    aligned, notes, unknown = soft_align_sql_to_schema(sql, _BET_LOG_FIELDS)
    assert "win_lsc" not in aligned
    assert "bet_sc" in aligned
    assert "uid" in aligned
    assert any("win_lsc" in n for n in notes)
