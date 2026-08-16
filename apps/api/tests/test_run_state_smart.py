"""Smart _run_state: completeness / next_actions / digest / empty FINAL."""

import json

from app.services.export_run_state import (
    build_export_run_state_payload,
    derive_export_completeness,
    hydrate_time_window_from_state,
    strip_completed_schema_repair_actions,
)
from app.services.react_engine import (
    _EXPORT_FULL_PAGE_ROWS,
    _EXPORT_QUERY_BUDGET_TYPE_B,
    _EXPORT_QUERY_BUDGET_TYPE_B_MAX,
    _build_run_state_digest,
    _build_run_state_next_actions,
    _compute_adaptive_export_budget,
    _derive_export_completeness,
    _format_smart_empty_export_final,
    _write_export_run_state,
)
from app.services.skill_lesson import (
    build_export_skill_lesson,
    infer_repair_gaps,
    lesson_is_high_confidence,
    load_recent_fetch_gap_hints,
)
from app.services.workplace import ensure_workplace


def test_derive_completeness_no_data_vs_iters_exhausted():
    assert (
        _derive_export_completeness(
            fetched_view_pages={},
            has_task_data=False,
            deliverable="",
        )
        == "no_data"
    )
    assert (
        _derive_export_completeness(
            fetched_view_pages={"view_result_pay_order_log": 2},
            has_task_data=True,
            deliverable="",
        )
        == "iters_exhausted"
    )
    assert (
        _derive_export_completeness(
            fact_truncated_roles=["pay"],
            deliverable="a.xlsx",
            fetched_view_pages={"view_result_pay_order_log": 2},
        )
        == "truncated"
    )


def test_export_run_state_module_builds_payload_contract():
    tw = {
        "label": "contract window",
        "start_ms": 100,
        "end_ms": 200,
        "cohort": "pay",
    }
    assert derive_export_completeness(
        deliverable="out.xlsx",
        deliverable_rows=10,
        cohort_uid_estimate=100,
    ) == "truncated"
    hydrated = hydrate_time_window_from_state({"time_window": tw})
    assert hydrated and hydrated["cohort"] == "pay"
    assert hydrated["mcp_example"] == ""

    payload = build_export_run_state_payload(
        phase="fetch",
        mcp_query_count=2,
        budget=18,
        todos=[],
        deliverable="out.xlsx",
        task_title="充值用户分析",
        time_window=tw,
        completeness="truncated",
        need_continue_roles=["pay"],
        next_actions=[{"role": "pay"}],
        digest="明细满页截断：pay",
        updated_at="2026-08-06 12:00:00",
        export_contract={"version": "export_contract.v1"},
        repair_plan={"status": "repairable"},
        done_node_keys=["agg:pay:pay_sum", "flag:pay:refund"],
        failed_views=["node:top_n:bet:game"],
    )
    assert payload["time_window"]["cohort"] == "pay"
    assert payload["task_title"] == "充值用户分析"
    assert payload["export_contract"]["version"] == "export_contract.v1"
    assert payload["repair_plan"]["status"] == "repairable"
    assert payload["done_node_keys"] == ["agg:pay:pay_sum", "flag:pay:refund"]
    assert payload["export_failed_views"] == ["node:top_n:bet:game"]


def test_export_run_state_payload_keeps_cte_attempt_diagnostics():
    payload = build_export_run_state_payload(
        phase="finalize",
        mcp_query_count=3,
        budget=8,
        todos=[],
        completeness="iters_exhausted",
        cte_attempts_used=3,
        cte_attempt_limit=5,
        cte_attempt_errors=[
            {"attempt": 1, "stage": "sql_compose", "error": "timeout"},
            {"attempt": 2, "stage": "query_count", "error": "mcp failed"},
        ],
    )
    assert payload["cte_attempts_used"] == 3
    assert payload["cte_attempt_limit"] == 5
    assert [row["stage"] for row in payload["cte_attempt_errors"]] == [
        "sql_compose",
        "query_count",
    ]


def test_run_state_payload_summarizes_schema_discovery_and_next_actions():
    payload = build_export_run_state_payload(
        phase="fetch",
        mcp_query_count=0,
        budget=18,
        todos=[],
        completeness="iters_exhausted",
        export_contract={
            "version": "export_contract.v1",
            "schema_discovery": {"required": True},
            "query_graph": [
                {"view": "view_result_pay_order_log"},
                {"view": "view_result_cash_order_log"},
            ],
        },
        updated_at="2026-08-06 12:00:00",
    )
    assert payload["schema_discovery"]["required"] is True
    assert payload["schema_discovery"]["complete"] is False
    assert payload["schema_discovery"]["missing_describe_views"] == [
        "view_result_pay_order_log",
        "view_result_cash_order_log",
    ]
    assert "Schema 发现未齐" in payload["digest"]
    examples = [a.get("mcp_example") for a in payload["next_actions"]]
    assert "MCP: list_ads_views {}" in examples
    assert 'MCP: describe_ads_view {"view_name":"view_result_pay_order_log"}' in examples
    assert payload["repair_plan"]["status"] == "repairable"
    assert [a["action_type"] for a in payload["repair_plan"]["actions"]] == [
        "discover_ads_views",
        "describe_ads_views",
    ]


def test_run_state_payload_does_not_mark_schema_complete_when_only_list_exists():
    payload = build_export_run_state_payload(
        phase="discover",
        mcp_query_count=0,
        budget=18,
        todos=[],
        completeness="iters_exhausted",
        export_contract={
            "version": "export_contract.v1",
            "schema_discovery": {"required": True},
            "query_graph": [],
        },
        schema_discovery={
            "required": True,
            "list_ads_views": {"captured": True},
        },
        updated_at="2026-08-06 12:00:00",
    )
    assert payload["schema_discovery"]["required"] is True
    assert payload["schema_discovery"]["list_ads_views"] is True
    assert payload["schema_discovery"]["complete"] is False
    assert payload["schema_discovery"]["missing_describe_views"] == []


def test_truncated_next_actions_has_offset(tmp_path, monkeypatch):
    from app.services import react_engine as re
    from app.services import workplace as wp

    sid = "sbx-smart-trunc"
    monkeypatch.setattr(wp, "_wp_root", lambda _s: tmp_path / _s / "workplace")
    root = ensure_workplace(sid)

    def _fake_write(sandbox, rel, content):
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return f"已写入 {rel}"

    monkeypatch.setattr(re, "_write_workplace", _fake_write)

    class _FakeSandbox:
        id = sid

    tw = {
        "label": "美国东部时间 2026-07-21 至 2026-07-31",
        "start_ms": 100,
        "end_ms": 200,
        "sql_select": (
            "SELECT * FROM ads.view_result_pay_order_log "
            "WHERE create_time >= 100 AND create_time < 200"
        ),
    }
    _write_export_run_state(
        _FakeSandbox(),  # type: ignore[arg-type]
        "1790000000001",
        phase="fetch",
        mcp_query_count=10,
        budget=18,
        todos=[],
        deliverable="",
        fetched_view_pages={"view_result_pay_order_log": 3},
        fetched_view_last_rows={
            "view_result_pay_order_log": _EXPORT_FULL_PAGE_ROWS,
        },
        fact_truncated_roles=["pay"],
        need_continue_roles=["pay"],
        time_window=tw,
        target_roles=["user", "pay", "cash", "bet"],
        has_task_data=True,
        completeness="truncated",
    )
    obj = json.loads(
        (root / "task" / "1790000000001" / "_run_state.json").read_text(
            encoding="utf-8"
        )
    )
    assert obj["completeness"] == "truncated"
    assert obj["fetched_view_last_rows"]["view_result_pay_order_log"] == (
        _EXPORT_FULL_PAGE_ROWS
    )
    assert obj["need_continue_roles"] == ["pay"]
    assert obj["digest"]
    assert obj["next_actions"]
    pay_act = next(a for a in obj["next_actions"] if a.get("role") == "pay")
    assert pay_act.get("offset", 0) >= 3000
    assert "OFFSET" in str(pay_act.get("mcp_example") or "") or "offset" in str(
        pay_act.get("mcp_example") or ""
    ).lower() or "MCP:" in str(pay_act.get("mcp_example") or "")


def test_smart_empty_final_no_data_vs_iters_exhausted():
    no_data = _format_smart_empty_export_final(
        completeness="no_data",
        digest=_build_run_state_digest(completeness="no_data"),
        next_actions=_build_run_state_next_actions(
            completeness="no_data",
            time_window={"label": "t", "start_ms": 1, "end_ms": 2},
        ),
    )
    assert "未落盘" in no_data or "筛选" in no_data
    assert "MCP 未返回可落盘数据" not in no_data

    iters = _format_smart_empty_export_final(
        completeness="iters_exhausted",
        digest=_build_run_state_digest(
            completeness="iters_exhausted", has_task_data=True,
        ),
        next_actions=_build_run_state_next_actions(
            completeness="iters_exhausted",
            has_task_data=True,
            need_continue_roles=["pay"],
            fetched_view_pages={"view_result_pay_order_log": 2},
        ),
    )
    assert "task/" in iters
    assert "SHELL" in iters or "写" in iters
    assert "MCP 未返回可落盘数据" not in iters
    assert "建议下一步" in iters


def test_prior_iters_exhausted_raises_budget_and_hint():
    prior = {
        "budget": 18,
        "completeness": "iters_exhausted",
        "digest": "轮次用尽：task/ 已有过程数据，但未写出当前目录 xlsx。",
        "need_continue_roles": ["pay"],
        "fact_truncated_roles": ["pay"],
        "next_actions": [
            {
                "role": "pay",
                "mcp_example": 'MCP: query_ads_view {"view":"view_result_pay_order_log"}',
                "hint": "续翻",
            }
        ],
        "time_window": {"label": "美国东部时间 2026-07-21 至 2026-07-31"},
        "user_pages": 2,
        "user_fetch_complete": True,
    }
    budget, hint = _compute_adaptive_export_budget(
        type_b=True,
        prior_state=prior,
        current_tw_label="美国东部时间 2026-07-21 至 2026-07-31",
    )
    assert budget > _EXPORT_QUERY_BUDGET_TYPE_B
    assert budget <= _EXPORT_QUERY_BUDGET_TYPE_B_MAX
    assert "iters_exhausted" in hint or "上轮" in hint
    assert "摘要" in hint or "轮次用尽" in hint
    assert "MCP:" in hint or "建议" in hint


def test_load_gap_hints_prefers_digest(tmp_path, monkeypatch):
    from app.services import skill_lesson as sl
    from app.services import workplace as wp

    sid = "sbx-smart-hints"
    monkeypatch.setattr(wp, "_wp_root", lambda _s: tmp_path / _s / "workplace")
    monkeypatch.setattr(sl, "ensure_workplace", lambda s: ensure_workplace(s))
    root = ensure_workplace(sid)
    run = root / "task" / "1790000000002"
    run.mkdir(parents=True)
    (run / "_run_state.json").write_text(
        json.dumps(
            {
                "completeness": "truncated",
                "digest": "明细满页截断：pay；须 OFFSET 续翻至短页。",
                "need_continue_roles": ["pay"],
                "next_actions": [
                    {
                        "role": "schema",
                        "mcp_example": "MCP: list_ads_views {}",
                        "hint": "补齐 schema discovery",
                    },
                    {
                        "role": "schema",
                        "mcp_example": 'MCP: describe_ads_view {"view_name":"view_result_pay_order_log"}',
                        "hint": "补齐 view_result_pay_order_log 表备注",
                    },
                    {
                        "role": "pay",
                        "mcp_example": "MCP: query_ads_view OFFSET 3000",
                        "hint": "续翻 pay",
                    }
                ],
                "user_pages": 1,
                "user_fetch_complete": True,
                "budget": 22,
                "repair_plan": {
                    "status": "repairable",
                    "actions": [
                        {
                            "action_type": "fetch_noncore_or_keep_incomplete",
                            "role": "bet",
                            "column": "总下注金额(SC)",
                        }
                    ],
                    "preserve_done_node_keys": ["agg:pay:pay_sum"],
                    "skip_failed_node_keys": ["top_n:bet:game"],
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    hint = load_recent_fetch_gap_hints(sid)
    assert "近期拉取缺口" in hint
    assert "truncated" in hint
    assert "摘要" in hint or "满页截断" in hint
    assert "OFFSET" in hint or "建议" in hint
    assert "list_ads_views" in hint
    assert "describe_ads_view" in hint
    assert "RepairPlan" in hint
    assert "fetch_noncore_or_keep_incomplete" in hint
    assert "agg:pay:pay_sum" in hint
    assert "top_n:bet:game" in hint


def test_adaptive_budget_hint_keeps_schema_list_and_describe_actions():
    budget, hint = _compute_adaptive_export_budget(
        type_b=True,
        prior_state={
            "completeness": "iters_exhausted",
            "time_window": {"label": "same", "start_ms": 1, "end_ms": 2},
            "next_actions": [
                {"mcp_example": "MCP: list_ads_views {}"},
                {
                    "mcp_example": 'MCP: describe_ads_view {"view_name":"view_result_pay_order_log"}'
                },
            ],
            "budget": _EXPORT_QUERY_BUDGET_TYPE_B,
        },
        current_tw={"label": "same", "start_ms": 1, "end_ms": 2},
        current_tw_label="same",
    )
    assert budget > _EXPORT_QUERY_BUDGET_TYPE_B
    assert "list_ads_views" in hint
    assert "describe_ads_view" in hint


def test_infer_repair_gaps_reads_need_continue():
    gaps = infer_repair_gaps({
        "missing_roles": [],
        "covered_roles": ["user", "pay", "cash", "bet"],
        "target_roles": ["user", "pay", "cash", "bet"],
        "need_continue_roles": ["pay", "cash"],
        "fetched_view_pages": {
            "view_result_pay_order_log": 3,
            "view_result_cash_order_log": 2,
            "view_result_user_bet_log": 1,
            "view_result_user_info": 1,
        },
    })
    assert "pay" in gaps
    assert "cash" in gaps


def test_completed_schema_removes_stale_schema_repair_plan():
    repair = {
        "status": "repairable",
        "actions": [
            {"action_type": "describe_ads_views", "views": ["view_result_pay_order_log"]},
        ],
        "summary": {"action_count": 1, "blocking_count": 0},
    }
    schema = {
        "required": True,
        "list_ads_views": True,
        "described_views": ["view_result_pay_order_log"],
        "missing_describe_views": [],
        "complete": True,
    }
    assert strip_completed_schema_repair_actions(repair, schema) == {}
    payload = build_export_run_state_payload(
        phase="fetch",
        mcp_query_count=1,
        budget=10,
        todos=[],
        repair_plan=repair,
        schema_discovery=schema,
    )
    assert "repair_plan" not in payload
    assert payload["schema_discovery"]["complete"] is True


def test_lesson_aligns_completeness_high_conf():
    md = build_export_skill_lesson(
        run_id="1790000000003",
        user_message="导出充值用户",
        mode="fallback",
        deliverable="",
        completeness="iters_exhausted",
        digest="轮次用尽：task/ 已有过程数据，但未写出当前目录 xlsx。",
        covered_roles=["user", "pay"],
        missing_roles=[],
        fetched_view_pages={"view_result_pay_order_log": 2},
    )
    assert "iters_exhausted" in md
    assert "完整度" in md or "摘要" in md
    assert lesson_is_high_confidence(md)
