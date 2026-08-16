"""MCP local autofill: normalize explicit input without selecting a resource."""

from app.services.react_engine import (
    _EXPORT_PAGE_LIMIT,
    _autofill_ads_query_args,
    _mcp_local_validate,
    _parse_mcp_args,
    _query_covers_time_window,
    _rewrite_mcp_with_autofill,
)


def test_autofill_view_from_sql_from_clause():
    args, notes = _autofill_ads_query_args(
        {"sql": "SELECT * FROM ads.view_result_pay_order_log WHERE create_time >= 1"},
    )
    assert args.get("view") == "view_result_pay_order_log"
    assert any("FROM" in n or "sql" in n for n in notes)
    rewritten, _ = _rewrite_mcp_with_autofill(
        'MCP: query_ads_view {"sql":"SELECT * FROM ads.view_result_pay_order_log WHERE 1=1"}'
    )
    assert _mcp_local_validate(rewritten) is None


def test_autofill_empty_args_never_selects_resource_from_time_window():
    tw = {
        "user_sql_select": (
            "SELECT * FROM ads.view_result_user_info "
            "WHERE register_time >= 1700000000000 AND register_time < 1700086400000"
        ),
        "mcp_example": (
            'MCP: query_ads_view {"view":"view_result_user_info","sql":'
            '"SELECT * FROM ads.view_result_user_info WHERE register_time >= 1","limit":1000}'
        ),
    }
    rewritten, notes = _rewrite_mcp_with_autofill(
        "MCP: query_ads_view {}",
        time_window=tw,
        target_roles=["user", "pay"],
    )
    assert rewritten == "MCP: query_ads_view {}"
    assert notes == []
    assert _mcp_local_validate(rewritten, time_window=tw) is not None


def test_true_empty_args_still_soft_blocked_with_executable_example():
    rewritten, notes = _rewrite_mcp_with_autofill("MCP: query_ads_view {}")
    assert notes == []
    msg = _mcp_local_validate(rewritten)
    assert msg is not None
    assert "缺少 view" in msg
    assert "可执行示例" in msg
    assert "query_ads_view" in msg
    assert "list_ads_views" in msg


def test_view_name_rewritten_to_view():
    rewritten, notes = _rewrite_mcp_with_autofill(
        'MCP: query_ads_view {"view_name":"view_result_user_info","limit":100}'
    )
    assert '"view": "view_result_user_info"' in rewritten or '"view":"view_result_user_info"' in rewritten
    assert "view_name" not in rewritten or '"view_name"' not in rewritten
    assert any("view_name" in n for n in notes)
    assert _mcp_local_validate(rewritten) is None


def test_where_sql_string_moved_to_sql():
    args, notes = _autofill_ads_query_args(
        {
            "view": "view_result_user_info",
            "where": "register_time >= 1 AND register_time < 2",
        },
    )
    assert "where" not in args or not isinstance(args.get("where"), str)
    assert "register_time" in str(args.get("sql") or "")
    assert any("where" in n.lower() for n in notes)
    rewritten, _ = _rewrite_mcp_with_autofill(
        'MCP: query_ads_view {"view":"view_result_user_info",'
        '"where":"register_time >= 1 AND register_time < 2"}'
    )
    assert _mcp_local_validate(rewritten) is None


def test_role_default_does_not_invent_archive_view():
    rewritten, notes = _rewrite_mcp_with_autofill(
        "MCP: query_ads_view {}",
        target_roles=["pay", "user"],
        missing_roles=["pay"],
    )
    assert "view_result_pay_order_log" not in rewritten
    assert "user_bet_log" not in rewritten
    assert notes == []
    msg = _mcp_local_validate(rewritten)
    assert msg is not None
    assert "缺少 view" in msg


def test_autofill_does_not_inject_sql_when_view_only():
    tw = {
        "start_ms": 1782878400000,
        "end_ms": 1801454400000,
        "sql_hint": "create_time >= 1782878400000 AND create_time < 1801454400000",
        "pay_sql_select": (
            "SELECT * FROM ads.view_result_pay_order_log "
            "WHERE create_time >= 1782878400000 AND create_time < 1801454400000"
        ),
    }
    rewritten, notes = _rewrite_mcp_with_autofill(
        'MCP: query_ads_view {"view":"view_result_pay_order_log"}',
        time_window=tw,
    )
    assert "create_time >= 1782878400000" not in rewritten
    assert notes == []
    assert _mcp_local_validate(rewritten, time_window=tw) is None
    assert _query_covers_time_window(
        {"view": "view_result_pay_order_log", "sql": tw["pay_sql_select"]},
        tw,
    )


def test_time_window_is_resource_agnostic():
    assert _EXPORT_PAGE_LIMIT == 1000
    from app.services.react_engine import _parse_export_time_window

    parsed = _parse_export_time_window(
        "导出美国东部时间2026-07-01至2026-08-01注册用户"
    )
    assert parsed is not None
    assert parsed["start_ms"] < parsed["end_ms"]
    assert "mcp_example" not in parsed



def test_query_covers_time_window_via_where_dict():
    tw = {"start_ms": 100, "end_ms": 200, "sql_hint": "create_time >= 100 AND create_time < 200"}
    assert not _query_covers_time_window(
        {"view": "view_result_pay_order_log", "limit": 1000},
        tw,
    )
    assert _query_covers_time_window(
        {
            "view": "view_result_pay_order_log",
            "where": {"create_time": {"$gte": 100, "$lt": 200}},
        },
        tw,
    )
    assert _query_covers_time_window(
        {
            "sql": "SELECT * FROM ads.view_result_user_info WHERE register_time >= 100 AND register_time < 200",
        },
        tw,
    )


def test_pay_without_window_sql_not_covered():
    tw = {"start_ms": 1782878400000, "end_ms": 1801454400000}
    args = {
        "view": "view_result_pay_order_log",
        "sql": "SELECT * FROM ads.view_result_pay_order_log WHERE 1=1",
        "limit": 1000,
    }
    assert not _query_covers_time_window(args, tw)
    # Autofill does not overwrite existing sql — gate must block at runtime
    filled, notes = _autofill_ads_query_args(args, time_window=tw)
    assert "WHERE 1=1" in str(filled.get("sql") or "")
    assert not any("时间窗" in n for n in notes)


def test_explicit_sql_with_window_still_passes():
    tw = {
        "start_ms": 1,
        "end_ms": 2,
    }
    rewritten, notes = _rewrite_mcp_with_autofill(
        'MCP: query_ads_view {"view":"bound_user_resource",'
        '"sql":"SELECT * FROM ads.bound_user_resource WHERE register_time >= 1 AND register_time < 2"}',
        time_window=tw,
    )
    assert "bound_user_resource" in rewritten
    assert _query_covers_time_window(_parse_mcp_args(rewritten), tw)
    assert _mcp_local_validate(rewritten, time_window=tw) is None
