"""Pay-cohort row completeness, FORMAT sql gate, soft gates, lesson honesty."""

from app.services.react_engine import (
    _MCP_CLASS_HARD_LIMIT,
    _MCP_NON_TOOL_FUSE_CLASSES,
    _build_export_analysis_appendix,
    _classify_mcp_error,
    _facts_ready_for_analyzed_delivery,
    _max_pages_for_view,
    _mcp_local_validate,
    _parse_cohort_uid_estimate,
    _strip_clickhouse_format_clause,
)
from app.services.skill_lesson import (
    build_export_skill_lesson,
    load_recent_fetch_gap_hints,
    load_recent_mcp_lesson_hints,
)


def test_strip_format_clause():
    cleaned, had = _strip_clickhouse_format_clause(
        "SELECT * FROM ads.view_result_user_info WHERE uid IN (1,2)\nFORMAT JSONEachRow"
    )
    assert had
    assert "FORMAT" not in cleaned.upper()
    assert "uid IN" in cleaned
    cleaned2, had2 = _strip_clickhouse_format_clause(
        "SELECT * FROM ads.view_result_pay_order_log WHERE 1=1;"
    )
    assert cleaned2.endswith("1=1") or "1=1" in cleaned2
    assert not had2 or ";" not in cleaned2


def test_mcp_local_validate_strips_format_before_gate():
    """FORMAT in raw sql is normalized away; validate must not hard-block."""
    from app.services.react_engine import _rewrite_mcp_with_normalized_args

    raw = (
        'MCP: query_ads_view {"view":"view_result_user_info",'
        '"sql":"SELECT * FROM ads.view_result_user_info WHERE uid=1 FORMAT JSONEachRow"}'
    )
    rewritten = _rewrite_mcp_with_normalized_args(raw)
    assert "FORMAT" not in rewritten.upper()
    assert _mcp_local_validate(rewritten) is None
    assert "format_clause" in _MCP_NON_TOOL_FUSE_CLASSES


def test_classify_remote_format_syntax_error():
    err = (
        'MCP 错误: {"error":"Syntax error: failed at position 550 (\'FORMAT\') '
        "(line 2, col 1): FORMAT JSONEachRow. Expected one of: SETTINGS, end of query. \"}"
    )
    assert _classify_mcp_error(err) == "format_clause"


def test_class_hard_limit_disabled():
    """OpenClaw-style: class×N must not hard-fuse whole MCP tools."""
    assert _MCP_CLASS_HARD_LIMIT >= 999


def test_pay_cohort_truncation_is_soft_not_block():
    """Truncation / row-gap no longer hard-ban analyzed FINAL."""
    roles = ["user", "pay", "cash", "bet", "channel", "game"]
    assert _facts_ready_for_analyzed_delivery(
        export_target_roles=roles,
        missing_roles=[],
        fact_truncated_roles=["pay", "cash"],
        budget_left=5,
        pay_cohort=True,
    )
    assert _facts_ready_for_analyzed_delivery(
        export_target_roles=roles,
        missing_roles=[],
        fact_truncated_roles=["pay"],
        budget_left=0,
        pay_cohort=True,
    )


def test_pay_cohort_row_gap_is_soft_not_block():
    roles = ["user", "pay", "cash", "bet"]
    assert _facts_ready_for_analyzed_delivery(
        export_target_roles=roles,
        missing_roles=[],
        fact_truncated_roles=[],
        budget_left=3,
        pay_cohort=True,
        deliverable_rows=1499,
        cohort_uid_estimate=3748,
    )
    # Still hard-block when targeted fact roles missing
    assert not _facts_ready_for_analyzed_delivery(
        export_target_roles=roles,
        missing_roles=["cash"],
        budget_left=3,
        pay_cohort=True,
        deliverable_rows=3600,
        cohort_uid_estimate=3748,
    )


def test_appendix_honesty_for_row_gap():
    md = _build_export_analysis_appendix(
        path=None,
        headers=[],
        todos=[],
        time_window={"label": "美国时间 6-1 至 8"},
        row_hint=1499,
        detail_truncated=True,
        truncated_roles=["pay"],
        missing_roles=[],
        user_fetch_complete=True,
        cohort_uid_estimate=3748,
    )
    assert "1,499" in md or "1499" in md
    assert "3,748" in md or "3748" in md
    assert "未标完整" in md or "行数缺口" in md
    assert "截断" in md or "pay" in md
    assert "### 导出概况" in md
    assert "### 字段说明" in md


def test_pay_cohort_fact_page_cap_is_higher():
    assert _max_pages_for_view("view_result_pay_order_log", pay_cohort=False) == 3
    assert _max_pages_for_view("view_result_pay_order_log", pay_cohort=True) == 8


def test_parse_cohort_uid_estimate():
    n = _parse_cohort_uid_estimate("使记录数达到 3747 量级", "COUNT ≈ 3748")
    assert n in (3747, 3748)


def test_lesson_not_success_when_fact_truncated():
    md = build_export_skill_lesson(
        run_id="t1",
        user_message="充值用户导出",
        mode="analyzed",
        deliverable="x.xlsx",
        covered_roles=["user", "pay", "cash", "bet"],
        missing_roles=[],
        fetched_view_pages={"view_result_pay_order_log": 3},
        fact_truncated_roles=["pay", "cash", "bet"],
        deliverable_rows=1499,
        cohort_uid_estimate=3748,
    )
    assert "成功要点" not in md
    assert "row_incomplete" in md or "满页截断" in md
    assert "续翻" in md or "OFFSET" in md


def test_mcp_hints_include_format(tmp_path, monkeypatch):
    # empty sandbox → still get FORMAT soft hint
    from app.services import skill_lesson as sl

    monkeypatch.setattr(sl, "ensure_workplace", lambda sid: tmp_path)
    text = load_recent_mcp_lesson_hints("sandbox-x")
    assert "format_clause" in text or "FORMAT" in text


def test_fetch_gap_hints_include_row_gap(tmp_path, monkeypatch):
    import json
    from app.services import skill_lesson as sl

    monkeypatch.setattr(sl, "ensure_workplace", lambda sid: tmp_path)
    task = tmp_path / "task" / "run_gap"
    task.mkdir(parents=True)
    (task / "_run_state.json").write_text(
        json.dumps({
            "fact_truncated_roles": ["pay", "cash", "bet"],
            "deliverable_rows": 1499,
            "cohort_uid_estimate": 3748,
            "user_fetch_complete": True,
            "user_pages": 1,
            "budget": 22,
            "missing_roles": [],
        }),
        encoding="utf-8",
    )
    text = load_recent_fetch_gap_hints("sandbox-x")
    assert "1499" in text
    assert "3748" in text or "目标" in text
    assert "截断" in text or "续页" in text
