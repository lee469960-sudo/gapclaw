"""query_ads_view SQL preflight: describe + soft align; no hard gates."""

from __future__ import annotations

from app.services.export_column_plan import soft_align_sql_to_schema
from app.services.react_engine import (
    _enrich_mcp_failure,
    _mcp_local_validate,
)


def test_local_validate_uses_soft_prompt_not_intercept():
    msg = _mcp_local_validate('MCP: query_ads_view {}')
    assert msg is not None
    assert "本地拦截" not in msg
    assert "参数软提示" in msg
    assert "禁止 FINAL" not in msg
    assert "硬门禁" not in msg


def test_local_validate_describe_soft_prompt():
    msg = _mcp_local_validate("MCP: describe_ads_view {}")
    assert msg is not None
    assert "本地拦截" not in msg
    assert "参数软提示" in msg


def test_unknown_cols_soft_message_no_hard_gate():
    fields = {"uid", "win_sc", "bet_sc", "create_time", "type"}
    sql = (
        "SELECT uid, SUM(win_sc + win_lsc) AS total_return "
        "FROM ads.view_result_user_bet_log GROUP BY uid"
    )
    aligned, notes, unknown = soft_align_sql_to_schema(sql, fields)
    assert "win_lsc" not in aligned
    assert not unknown
    # residual-unknown path message shape
    soft = _enrich_mcp_failure(
        "query_ads_view",
        (
            "MCP 参数软提示: SQL 仍含 describe 未返回的列: ghost_col。"
            "请按真实列名改 SQL 后重试。此为防错预检，不阻止后续写表/FINAL。"
        ),
        view="view_result_user_bet_log",
    )
    assert "本地拦截" not in soft
    assert "禁止 FINAL" not in soft
    assert "写表/FINAL" in soft or "不阻止" in soft


def test_align_then_no_remote_needed_when_clean():
    fields = {
        "uid",
        "bet_sc",
        "bet_lsc",
        "win_sc",
        "create_time",
        "type",
    }
    # bet_lsc present in schema → keep
    sql = (
        "SELECT uid, SUM(bet_sc + bet_lsc) AS total_bet "
        "FROM ads.view_result_user_bet_log GROUP BY uid"
    )
    aligned, notes, unknown = soft_align_sql_to_schema(sql, fields)
    assert "bet_lsc" in aligned
    assert not unknown
    assert not notes
