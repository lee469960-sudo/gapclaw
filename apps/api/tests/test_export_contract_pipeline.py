"""Golden contract pipeline: TaskSpec -> VerifierResult -> RepairPlan -> reverify."""

from pathlib import Path

from app.services.export_build_report import write_export_deliverable
from app.services.export_column_plan import (
    apply_binding_hints_to_column_plan,
    build_column_plan,
)
from app.services.export_contract import build_export_contract
from app.services.export_query_contract import QueryContract, validate_query_contract
from app.services.export_repair_plan import build_repair_plan
from app.services.export_trace import ExportTrace
from app.services.export_verifier import verify_export_deliverable
from app.services.workplace import write_task_json_page


def test_export_contract_pipeline_repair_to_pass(tmp_path, monkeypatch):
    sid = "sbx_contract_pipeline"
    root = tmp_path / sid / "workplace"
    root.mkdir(parents=True)

    def _wp(sandbox_id: str) -> Path:
        return root if sandbox_id == sid else Path("/nope")

    monkeypatch.setattr("app.services.workplace.ensure_workplace", _wp)
    monkeypatch.setattr("app.services.export_build_report.ensure_workplace", _wp)
    monkeypatch.setattr("app.services.export_verifier.ensure_workplace", _wp)

    run_id = "run_contract"
    headers = ["用户ID", "总充值金额", "总提现金额"]
    tw = {"start_ms": 1000, "end_ms": 2000, "label": "contract", "cohort": "pay"}

    column_plan = apply_binding_hints_to_column_plan(
        build_column_plan(headers),
        bindings=[
            {"goal": "用户ID", "resource": "view_result_user_info"},
            {"goal": "总充值金额", "resource": "view_result_pay_order_log"},
            {"goal": "总提现金额", "resource": "view_result_cash_order_log"},
        ],
    )
    contract = build_export_contract(
        task_type="multi_fact",
        headers=headers,
        time_window=tw,
        resources=[
            "view_result_user_info",
            "view_result_pay_order_log",
            "view_result_cash_order_log",
        ],
        source_brief="导出充值用户充值与提现",
        column_plan=column_plan,
    )
    spec = contract.task_spec
    validation = contract.validation
    assert validation.status == "pass"
    assert contract.to_trace_fields()["export_contract"]["version"] == "export_contract.v1"

    # ColumnPlan is now output metadata; one CTE QueryContract owns execution.
    assert contract.query_graph == []
    query_contract = QueryContract(
        sql=(
            'WITH users AS (SELECT uid FROM ads.view_result_user_info), '
            'pay AS (SELECT uid, sum(price) AS pay_sum '
            'FROM ads.view_result_pay_order_log GROUP BY uid), '
            'cash AS (SELECT uid, sum(amount) AS cash_sum '
            'FROM ads.view_result_cash_order_log GROUP BY uid) '
            'SELECT users.uid AS "用户ID", pay.pay_sum AS "总充值金额", '
            'cash.cash_sum AS "总提现金额" FROM users '
            'LEFT JOIN pay ON users.uid = pay.uid '
            'LEFT JOIN cash ON users.uid = cash.uid'
        ),
        output_columns=headers,
        resources=[
            "view_result_user_info",
            "view_result_pay_order_log",
            "view_result_cash_order_log",
        ],
    )
    query_validation = validate_query_contract(
        query_contract,
        requested_columns=headers,
        catalog_resources=query_contract.resources,
        schema_fields={
            "view_result_user_info": ["uid"],
            "view_result_pay_order_log": ["uid", "price"],
            "view_result_cash_order_log": ["uid", "amount"],
        },
    )
    assert query_validation.ok, query_validation.errors

    write_task_json_page(
        sid,
        [{"uid": 1, "register_time": 1500, "sc": 10000}],
        run_id=run_id,
        page=1,
        view="view_result_user_info",
    )
    write_task_json_page(
        sid,
        [{"uid": 1, "pay_sum": 12.5}],
        run_id=run_id,
        page=2,
        view="view_result_pay_order_log",
    )
    rel = write_export_deliverable(
        sid,
        run_id,
        column_plan=column_plan,
        time_window=tw,
        title="contract",
    )
    assert rel

    trace = ExportTrace(run_id=run_id, source_brief=spec.source_brief)
    trace.task_spec = spec.to_dict()
    trace.task_spec_validation = validation.to_dict()
    trace.column_plan = column_plan
    trace.query_contract = query_contract.to_dict()
    trace.query_graph = []
    trace.failed_views = ["view_result_cash_order_log"]
    trace.done_node_keys = ["field:user:identity", "agg:pay:pay_sum"]

    initial = verify_export_deliverable(
        sandbox_id=sid,
        file_rel=rel,
        task_spec=spec.to_dict(),
        column_plan=column_plan,
        trace=trace.to_dict(),
        failed_views=trace.failed_views,
    )
    assert initial["status"] == "repairable"
    assert "总提现金额" in initial["incomplete_non_core"]

    repair = build_repair_plan(initial, trace.to_dict())
    assert repair["status"] == "repairable"
    # File verification still yields a repairable missing-column diagnosis;
    # CTE repair is performed by regenerating the same QueryContract with error evidence.
    assert any(a.get("column") == "总提现金额" for a in repair.get("actions") or [])

    write_task_json_page(
        sid,
        [{"uid": 1, "cash_sum": 7.5}],
        run_id=run_id,
        page=3,
        view="view_result_cash_order_log",
    )
    rel2 = write_export_deliverable(
        sid,
        run_id,
        column_plan=column_plan,
        time_window=tw,
        title="contract",
    )
    assert rel2
    trace.failed_views = []
    trace.done_node_keys = [*trace.done_node_keys, "cte:final"]
    final = verify_export_deliverable(
        sandbox_id=sid,
        file_rel=rel2,
        task_spec=spec.to_dict(),
        column_plan=column_plan,
        trace=trace.to_dict(),
    )
    assert final["status"] == "pass", final
    assert final["ok"]
    assert build_repair_plan(final, trace.to_dict())["status"] == "none"
