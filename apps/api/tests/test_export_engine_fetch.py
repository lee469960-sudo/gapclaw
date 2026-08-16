"""Engine-driven fetch, READ/metric soft-block, digest MCP visibility, steps no-cap."""

import json

from app.routers.agent_chat import _steps_tail_for_message
from app.services.react_engine import (
    _EXPORT_PAGE_LIMIT,
    _build_engine_fetch_mcp,
    _build_run_state_digest,
    _format_llm_mcp_progress,
    _should_soft_block_export_read,
    _should_soft_block_metric_tool,
    _view_for_export_role,
)


def test_build_engine_fetch_mcp_pay_with_window():
    tw = {
        "start_ms": 1000,
        "end_ms": 2000,
        "pay_sql_select": (
            "SELECT * FROM ads.view_result_pay_order_log "
            "WHERE create_time >= 1000 AND create_time < 2000"
        ),
    }
    # Fact pull requires contract view (whitelist / explicit view)
    line = _build_engine_fetch_mcp(
        "pay",
        tw,
        view="view_result_pay_order_log",
        whitelist=["view_result_pay_order_log"],
    )
    assert line.startswith("MCP: query_ads_view ")
    args = json.loads(line.split(" ", 2)[2])
    assert args["view"] == "view_result_pay_order_log"
    assert "sql" not in args
    assert args["limit"] == _EXPORT_PAGE_LIMIT


def test_build_engine_fetch_mcp_does_not_invent_offset_sql():
    tw = {
        "start_ms": 1,
        "end_ms": 2,
        "pay_sql_select": (
            "SELECT * FROM ads.view_result_pay_order_log "
            "WHERE create_time >= 1 AND create_time < 2"
        ),
    }
    line = _build_engine_fetch_mcp(
        "pay",
        tw,
        view="view_result_pay_order_log",
        whitelist=["view_result_pay_order_log"],
        pages_done=2,
    )
    args = json.loads(line.split(" ", 2)[2])
    assert "sql" not in args


def test_view_for_role_requires_explicit_dimension_binding():
    assert _view_for_export_role("channel") == ""
    assert (
        _view_for_export_role(
            "channel", dim_views={"channel": "view_result_user_channel_cfg"},
        )
        == "view_result_user_channel_cfg"
    )


def test_soft_block_read_in_fetch_when_roles_missing():
    assert _should_soft_block_export_read(
        phase="fetch", missing_roles=["cash", "bet"], has_root_xlsx=False,
    )
    assert _should_soft_block_export_read(
        phase="analyze", missing_roles=["pay"], has_root_xlsx=False,
    )
    assert not _should_soft_block_export_read(
        phase="discover", missing_roles=["pay"], has_root_xlsx=False,
    )
    assert not _should_soft_block_export_read(
        phase="analyze", missing_roles=[], has_root_xlsx=True,
    )
    # fetch with no missing still blocks READ until xlsx (avoid idle re-read)
    assert _should_soft_block_export_read(
        phase="fetch", missing_roles=[], has_root_xlsx=False,
    )


def test_soft_block_metric_only_in_fetch():
    assert _should_soft_block_metric_tool("query_ads_metric", phase="fetch")
    assert not _should_soft_block_metric_tool("query_ads_view", phase="fetch")
    assert not _should_soft_block_metric_tool("query_ads_metric", phase="discover")
    assert not _should_soft_block_metric_tool("query_ads_metric", phase="analyze")


def test_digest_iters_exhausted_includes_mcp_ratio():
    dig = _build_run_state_digest(
        completeness="iters_exhausted",
        has_task_data=True,
        mcp_query_count=4,
        mcp_budget=34,
    )
    assert "MCP 仅 4/34" in dig
    assert "轮次用尽" in dig


def test_format_llm_mcp_progress():
    s = _format_llm_mcp_progress(
        llm_iter=54, max_iters=100, mcp_query_count=4, mcp_budget=34,
    )
    assert "LLM 54/100" in s
    assert "MCP 4/34" in s


def test_steps_api_returns_full_history_no_older():
    steps = [
        {"type": "llm", "title": f"LLM {i}", "status": "done", "iteration": i}
        for i in range(1, 61)
    ]
    meta = json.dumps({"steps": steps}, ensure_ascii=False)
    out = _steps_tail_for_message(meta, 0)
    assert out["step_count"] == 60
    assert out["older"] == 0
    assert len(out["steps"]) == 60
    # explicit large limit also uncapped (no hard 100)
    out2 = _steps_tail_for_message(meta, 200)
    assert out2["older"] == 0
    assert len(out2["steps"]) == 60
