"""Runtime MCP schema discovery helpers."""

import asyncio
from pathlib import Path

import openpyxl

from app.services.export_schema_executor import execute_schema_discovery_actions
from app.services.export_contract_updater import record_schema_hint, record_schema_list
from app.services.export_schema_discovery import (
    build_schema_discovery_actions,
    describe_views_from_schema_actions,
    described_schema_views,
    parse_describe_schema_hint,
    schema_cache_to_view_fields,
    schema_list_captured,
)
from app.services.export_column_plan import (
    apply_binding_hints_to_column_plan,
    apply_schema_hints_to_column_plan,
    build_column_plan,
    carry_forward_bound_columns,
    sql_from_agg_spec,
)
from app.services.export_trace import ExportTrace
from app.services.export_repair_plan import build_repair_plan, format_repair_plan_block
from app.services.export_verifier import format_verifier_block, verify_export_deliverable


def _write_verified_xlsx(path: Path, headers: list[str]) -> None:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "数据"
    ws.append(headers)
    ws.append([12.5 for _ in headers])
    wb.create_sheet("口径")
    wb.save(path)


def test_parse_describe_schema_hint_from_json_columns():
    hint = parse_describe_schema_hint(
        """
        {"table_comment":"充值订单表","columns":[
          {"name":"uid","type":"UInt64","comment":"用户ID"},
          {"name":"price","type":"Int64","comment":"金额，分"},
          {"name":"finish_time","type":"Int64","comment":"完成时间"}
        ]}
        """,
        view="view_result_pay_order_log",
    )
    assert hint.view == "view_result_pay_order_log"
    assert hint.fields == ["uid", "price", "finish_time"]
    assert hint.comments["price"] == "金额，分"
    assert hint.view_comment == "充值订单表"
    assert schema_cache_to_view_fields({hint.view: hint})[hint.view] == hint.fields


def test_parse_describe_schema_hint_from_markdown_table():
    hint = parse_describe_schema_hint(
        """
        | name | type | comment |
        | uid | UInt64 | 用户ID |
        | channel_id | UInt32 | 渠道 |
        """,
        view="view_result_user_info",
    )
    assert "uid" in hint.fields
    assert "channel_id" in hint.fields
    assert hint.comments["channel_id"] == "渠道"


def test_schema_hints_rewrite_known_metric_fields():
    plan = build_column_plan(["总充值金额"])
    hint = parse_describe_schema_hint(
        {
            "columns": [
                {"name": "uid", "comment": "用户ID"},
                {"name": "amount", "comment": "充值金额，单位分"},
                {"name": "status", "comment": "订单状态"},
                {"name": "create_time", "comment": "创建时间"},
            ]
        }.__repr__().replace("'", '"'),
        view="view_result_pay_order_log",
    )
    adjusted = apply_schema_hints_to_column_plan(
        plan,
        schema_hints={hint.view: hint},
    )
    spec = adjusted[0]["agg_spec"]
    assert "sum(amount)" in spec["select"]
    assert spec["time_field"] == "create_time"
    assert adjusted[0]["schema_source"] == "describe_ads_view"
    _view, sql = sql_from_agg_spec(spec, {"start_ms": 1, "end_ms": 2})
    assert "amount" in sql
    assert "finish_time" not in sql


def test_schema_hints_do_not_cross_apply_source_replacements_to_agg_view():
    plan = build_column_plan(["总下注金额(SC)", "总返奖金额(SC)", "下注次数(SC投注次数)"])
    bet_log_hint = parse_describe_schema_hint(
        """
        {"columns":[
          {"name":"uid","comment":"用户ID"},
          {"name":"bet_sc","comment":"下注sc真钱"},
          {"name":"after_result_sc","comment":"返奖sc真钱"},
          {"name":"after_bet_sc","comment":"下注后sc"},
          {"name":"create_time","comment":"创建时间"}
        ]}
        """,
        view="view_result_user_bet_log",
    )
    daily_hint = parse_describe_schema_hint(
        """
        {"columns":[
          {"name":"stat_date","comment":"统计日期"},
          {"name":"uid","comment":"用户ID"},
          {"name":"goods_type","comment":"游戏类型 1真钱 4不可提真钱"},
          {"name":"bet_nums","comment":"投注次数"},
          {"name":"bet_value","comment":"下注金额，单位分"},
          {"name":"result_value","comment":"返奖金额，单位分"}
        ]}
        """,
        view="view_result_gameuser_betstat_everyday_bygame",
    )
    adjusted = apply_schema_hints_to_column_plan(
        plan,
        schema_hints={
            bet_log_hint.view: bet_log_hint,
            daily_hint.view: daily_hint,
        },
    )
    specs = [c["agg_spec"] for c in adjusted]
    assert "sum(bet_value)" in specs[0]["select"]
    assert "sum(result_value)" in specs[1]["select"]
    assert "sum(bet_nums)" in specs[2]["select"]
    assert all("goods_type IN (1, 4)" == spec["filter_sql"] for spec in specs)
    assert all("bet_sc" not in spec["select"] for spec in specs)


def test_schema_hints_generate_unknown_metric_from_comment():
    plan = build_column_plan(["总返佣金额"])
    hint = parse_describe_schema_hint(
        """
        {"columns":[
          {"name":"uid","comment":"用户ID"},
          {"name":"commission_amount","comment":"返佣金额，单位分"},
          {"name":"create_time","comment":"创建时间"}
        ]}
        """,
        view="view_result_pay_order_log",
    )
    adjusted = apply_schema_hints_to_column_plan(
        plan,
        schema_hints={hint.view: hint},
    )
    assert adjusted[0]["kind"] == "aggregate"
    assert adjusted[0]["rule"] == "schema"
    assert adjusted[0]["agg_spec"]["view"] == "view_result_pay_order_log"
    assert "sum(commission_amount)" in adjusted[0]["agg_spec"]["select"]


def test_schema_hints_choose_best_view_by_table_comment():
    plan = build_column_plan(["总出款手续费"])
    pay_hint = parse_describe_schema_hint(
        """
        {"table_comment":"充值订单表","columns":[
          {"name":"uid","comment":"用户ID"},
          {"name":"fee_amount","comment":"手续费金额，单位分"},
          {"name":"create_time","comment":"创建时间"}
        ]}
        """,
        view="view_result_pay_order_log",
    )
    cash_hint = parse_describe_schema_hint(
        """
        {"table_comment":"提现出款订单表","columns":[
          {"name":"uid","comment":"用户ID"},
          {"name":"fee_amount","comment":"手续费金额，单位分"},
          {"name":"update_time","comment":"更新时间"}
        ]}
        """,
        view="view_result_cash_order_log",
    )
    adjusted = apply_schema_hints_to_column_plan(
        plan,
        schema_hints={
            pay_hint.view: pay_hint,
            cash_hint.view: cash_hint,
        },
    )
    assert adjusted[0]["rule"] == "schema"
    assert adjusted[0]["agg_spec"]["view"] == "view_result_cash_order_log"
    assert adjusted[0]["agg_spec"]["time_field"] == "update_time"
    assert adjusted[0]["schema_score"] > 0


def test_binding_hints_can_override_seeded_user_view_for_ban_column():
    plan = build_column_plan(["是否被封禁"])
    adjusted = apply_binding_hints_to_column_plan(
        plan,
        bindings=[
            {
                "goal": "是否被封禁",
                "resource": "view_result_user_ban_log",
            }
        ],
    )
    col = adjusted[0]
    assert any(
        str(src.get("view") or "") == "view_result_user_ban_log"
        for src in (col.get("sources") or [])
        if isinstance(src, dict)
    )
    assert col["schema_source"] == "binding"
    assert "view_result_user_ban_log" in str(col.get("数据来源") or "")


def test_carry_forward_bound_columns_preserves_prior_specialized_binding():
    prior = apply_binding_hints_to_column_plan(
        build_column_plan(["用户ID", "注册时间", "是否被封禁"]),
        bindings=[
            {
                "goal": "是否被封禁",
                "resource": "view_result_user_block",
            }
        ],
    )
    rebuilt = carry_forward_bound_columns(
        build_column_plan(["用户ID", "注册时间", "是否被封禁"]),
        prior_plan=prior,
    )
    by_header = {str(col.get("header")): col for col in rebuilt}
    assert by_header["是否被封禁"]["数据来源"] == "`view_result_user_block`"
    assert by_header["是否被封禁"]["binding_status"] == "bound"
    assert by_header["用户ID"]["数据来源"] == "`view_result_user_info`"


def test_export_trace_records_schema_discovery():
    trace = ExportTrace(run_id="r1")
    hint = parse_describe_schema_hint(
        '{"table_comment":"充值订单表","columns":[{"name":"amount","comment":"金额"}]}',
        view="view_result_pay_order_log",
    )
    trace.set_schema_discovery({"required": True})
    trace.record_ads_views_list("view_result_pay_order_log 充值订单表")
    trace.record_ads_view_schema(hint.view, hint.to_dict())
    data = trace.to_dict()
    assert data["schema_discovery"]["required"] is True
    assert data["schema_discovery"]["list_ads_views"]["captured"] is True
    assert "view_result_pay_order_log" in data["schema_discovery"]["described_views"]
    assert data["export_contract"]["schema_discovery"]["required"] is True


def test_verifier_schema_discovery_required_is_soft_gate(tmp_path, monkeypatch):
    sid = "sbx_schema_gate"
    root = tmp_path / sid / "workplace"
    root.mkdir(parents=True)

    def _wp(sandbox_id: str) -> Path:
        return root if sandbox_id == sid else Path("/nope")

    monkeypatch.setattr("app.services.export_verifier.ensure_workplace", _wp)
    headers = ["总充值金额"]
    rel = "schema_gate.xlsx"
    _write_verified_xlsx(root / rel, headers)
    plan = build_column_plan(headers)

    missing = verify_export_deliverable(
        sandbox_id=sid,
        file_rel=rel,
        column_plan=plan,
        trace={"schema_discovery": {"required": True}},
    )
    assert missing["status"] == "repairable"
    assert any("list_ads_views" in w for w in missing["warnings"])
    assert any("describe_ads_view" in w for w in missing["warnings"])

    view = plan[0]["agg_spec"]["view"]
    ok = verify_export_deliverable(
        sandbox_id=sid,
        file_rel=rel,
        column_plan=plan,
        trace={
            "schema_discovery": {
                "required": True,
                "list_ads_views": {"captured": True},
                "described_views": {view: {"fields": ["uid", "price"]}},
            }
        },
    )
    assert ok["status"] == "pass", ok


def test_repair_plan_names_schema_discovery_actions(tmp_path, monkeypatch):
    sid = "sbx_schema_repair"
    root = tmp_path / sid / "workplace"
    root.mkdir(parents=True)

    def _wp(sandbox_id: str) -> Path:
        return root if sandbox_id == sid else Path("/nope")

    monkeypatch.setattr("app.services.export_verifier.ensure_workplace", _wp)
    headers = ["总充值金额"]
    rel = "schema_repair.xlsx"
    _write_verified_xlsx(root / rel, headers)
    plan = build_column_plan(headers)
    trace = ExportTrace(run_id="schema_repair")
    trace.column_plan = plan
    trace.query_graph = [
        {
            "key": "agg:pay:pay_sum",
            "role": "pay",
            "view": plan[0]["agg_spec"]["view"],
            "headers": headers,
        }
    ]
    trace.set_schema_discovery({"required": True})

    verifier = verify_export_deliverable(
        sandbox_id=sid,
        file_rel=rel,
        column_plan=plan,
        trace=trace.to_dict(),
    )
    repair = build_repair_plan(verifier, trace.to_dict())
    actions = repair["actions"]
    assert repair["status"] == "repairable"
    assert [a["action_type"] for a in actions[:2]] == [
        "discover_ads_views",
        "describe_ads_views",
    ]
    assert actions[1]["views"] == [plan[0]["agg_spec"]["view"]]
    verifier_block = format_verifier_block(verifier)
    assert "Schema 缺口" in verifier_block
    assert plan[0]["agg_spec"]["view"] in verifier_block
    repair_block = format_repair_plan_block(repair)
    assert "### 修复计划" in repair_block
    assert "discover_ads_views" in repair_block
    assert "describe_ads_views" in repair_block
    assert plan[0]["agg_spec"]["view"] in repair_block


def test_schema_discovery_actions_synthesize_first_run_metadata_steps():
    actions = build_schema_discovery_actions(
        schema_discovery={"required": True},
        planned_views=["view_result_pay_order_log", "view_result_cash_order_log"],
    )
    assert [a["action_type"] for a in actions] == [
        "discover_ads_views",
        "describe_ads_views",
    ]
    assert actions[1]["views"] == [
        "view_result_pay_order_log",
        "view_result_cash_order_log",
    ]
    assert describe_views_from_schema_actions(actions) == actions[1]["views"]


def test_schema_discovery_actions_skip_existing_schema_evidence():
    hint = parse_describe_schema_hint(
        '{"columns":[{"name":"uid","comment":"用户ID"}]}',
        view="view_result_pay_order_log",
    )
    schema = {
        "required": True,
        "list_ads_views": {"captured": True},
        "described_views": {hint.view: hint.to_dict()},
    }
    assert schema_list_captured(schema)
    assert described_schema_views(schema) == {hint.view}
    assert build_schema_discovery_actions(
        schema_discovery=schema,
        planned_views=[hint.view],
    ) == []


def test_schema_discovery_actions_preserve_repair_plan_priority():
    repair_plan = {
        "actions": [
            {"action_type": "inspect_verifier_warnings"},
            {
                "action_type": "describe_ads_views",
                "views": ["view_result_cash_order_log"],
            },
        ]
    }
    actions = build_schema_discovery_actions(
        repair_plan=repair_plan,
        schema_discovery={
            "required": True,
            "list_ads_views": {"captured": False},
        },
        planned_views=["view_result_pay_order_log"],
    )
    assert actions == [
        {
            "action_type": "discover_ads_views",
            "reason": "当前 run 缺少 MCP 接口/视图列表发现证据",
        },
        {
            "action_type": "describe_ads_views",
            "views": ["view_result_cash_order_log"],
        }
    ]


def test_schema_executor_runs_metadata_calls_and_parses_describe():
    calls: list[str] = []
    finished: list[str] = []

    async def _execute_mcp(_action, normalized: str, _view: str) -> str:
        calls.append(normalized)
        if "list_ads_views" in normalized:
            return "view_result_pay_order_log 充值订单表"
        return '{"columns":[{"name":"uid","comment":"用户ID"},{"name":"amount","comment":"充值金额"}]}'

    async def _on_result(call) -> None:
        finished.append(f"{call.action_type}:{call.view}:{call.ok}")

    result = asyncio.run(execute_schema_discovery_actions(
        [
            {"action_type": "discover_ads_views"},
            {"action_type": "describe_ads_views", "views": ["view_result_pay_order_log"]},
        ],
        execute_mcp=_execute_mcp,
        is_failure=lambda text: "error" in text.lower(),
        schema_discovery={"required": True},
        on_call_result=_on_result,
    ))
    assert result.ran == 2
    assert calls == [
        "MCP: list_ads_views {}",
        'MCP: describe_ads_view {"view_name": "view_result_pay_order_log"}',
    ]
    assert result.ads_views_list_text.startswith("view_result")
    assert result.schema_hints["view_result_pay_order_log"].fields == ["uid", "amount"]
    assert finished == [
        "discover_ads_views::True",
        "describe_ads_views:view_result_pay_order_log:True",
    ]


def test_schema_executor_skips_existing_evidence():
    async def _execute_mcp(_action, _normalized: str, _view: str) -> str:
        raise AssertionError("should not call MCP when evidence already exists")

    result = asyncio.run(execute_schema_discovery_actions(
        [
            {"action_type": "discover_ads_views"},
            {"action_type": "describe_ads_views", "views": ["view_result_pay_order_log"]},
        ],
        execute_mcp=_execute_mcp,
        is_failure=lambda _text: False,
        schema_discovery={
            "required": True,
            "list_ads_views": {"captured": True},
            "described_views": {
                "view_result_pay_order_log": {"fields": ["uid"]},
            },
        },
    ))
    assert result.ran == 0
    assert result.calls == []


def test_contract_updater_records_schema_and_syncs_trace_contract():
    plan = build_column_plan(["总充值金额"])
    trace = ExportTrace(run_id="contract_update")
    trace.export_contract = {"version": "export_contract.v1"}
    trace.set_schema_discovery({"required": True})
    schema = record_schema_list(trace, "view_result_pay_order_log 充值订单表")
    assert schema["list_ads_views"]["captured"] is True
    assert trace.export_contract["schema_discovery"]["list_ads_views"]["captured"] is True
    hint = parse_describe_schema_hint(
        '{"columns":[{"name":"uid"},{"name":"amount","comment":"充值金额"},{"name":"create_time"}]}',
        view="view_result_pay_order_log",
    )
    update = record_schema_hint(
        trace,
        view=hint.view,
        schema_hint=hint,
        column_plan=plan,
        time_window={"start_ms": 1, "end_ms": 2},
        schema_hints={hint.view: hint},
    )
    assert "sum(amount)" in update.column_plan[0]["agg_spec"]["select"]
    assert hint.view in update.schema_discovery["described_views"]
    assert trace.column_plan == update.column_plan
    assert trace.query_graph == update.query_graph
    assert trace.export_contract["column_plan"] == update.column_plan
    assert trace.export_contract["query_graph"] == update.query_graph
    assert trace.schema_discovery["required"] is True
