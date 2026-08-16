"""_run_state / query_graph SQL soft-align after describe (no hard gates)."""

from __future__ import annotations

from app.services.export_column_plan import (
    build_column_plan,
    build_query_graph,
    soft_align_filter_sql_to_schema,
    soft_align_query_graph_to_schema,
    soft_align_sql_to_schema,
)
from app.services.export_run_state import (
    build_export_run_state_payload,
    soft_revalidate_export_contract_sql,
)

_BET_DAY_FIELDS = {
    "stat_date",
    "game_id",
    "uid",
    "bet_nums",
    "bet_value",
    "result_value",
}

_PAY_FIELDS = {
    "uid",
    "price",
    "status",
    "finish_time",
    "create_time",
    "update_time",
}

_HARD_GATE_SNIPPETS = (
    "禁止写表",
    "禁止 FINAL",
    "禁止写表/FINAL",
    "禁止写表/禁止 FINAL",
)


def test_soft_align_filter_strips_unknown_goods_type():
    filt, notes = soft_align_filter_sql_to_schema(
        "goods_type IN (1, 4)",
        _BET_DAY_FIELDS,
    )
    assert filt == ""
    assert any("goods_type" in n for n in notes)


def test_soft_align_filter_strips_unknown_type_keeps_status():
    filt, notes = soft_align_filter_sql_to_schema(
        "type = 1 AND status = 2",
        _PAY_FIELDS,
    )
    assert filt == "status = 2"
    assert any("type" in n for n in notes)


def test_soft_align_filter_keeps_known_status():
    filt, notes = soft_align_filter_sql_to_schema("status = 2", _PAY_FIELDS)
    assert filt == "status = 2"
    assert not notes


def test_build_query_graph_strips_goods_type_when_not_in_describe():
    plan = build_column_plan(["下注次数"])
    tw = {"start_ms": 1_700_000_000_000, "end_ms": 1_700_086_400_000}
    view = "view_result_gameuser_betstat_everyday_bygame"
    nodes = build_query_graph(
        plan,
        tw,
        schema_fields_by_view={view: sorted(_BET_DAY_FIELDS)},
    )
    bet_nodes = [n for n in nodes if n.get("view") == view and n.get("sql")]
    assert bet_nodes
    for n in bet_nodes:
        sql = n.get("sql") or ""
        assert "goods_type" not in sql
        notes = n.get("schema_soft_notes") or []
        assert notes


def test_build_query_graph_keeps_status_when_described():
    plan = build_column_plan(["充值次数"])
    tw = {"start_ms": 1000, "end_ms": 2000}
    view = "view_result_pay_order_log"
    nodes = build_query_graph(
        plan,
        tw,
        schema_fields_by_view={view: sorted(_PAY_FIELDS)},
    )
    pay_nodes = [n for n in nodes if n.get("view") == view and "count" in (n.get("sql") or "").lower()]
    assert pay_nodes
    sql = pay_nodes[0].get("sql") or ""
    assert "status = 2" in sql


def test_soft_align_sql_strips_type_predicate_without_type_field():
    sql = (
        "SELECT uid, sum(bet_sc) AS amt FROM ads.view_result_user_bet_log "
        "WHERE create_time >= 1 AND create_time < 2 AND (type = 1) GROUP BY uid"
    )
    fields = {"uid", "bet_sc", "create_time"}
    aligned, notes, _unk = soft_align_sql_to_schema(sql, fields)
    assert "type = 1" not in aligned
    assert "type" not in aligned or "create_time" in aligned
    assert any("type" in n for n in notes)
    assert "create_time >= 1" in aligned


def test_run_state_persist_revalidates_query_graph():
    view = "view_result_gameuser_betstat_everyday_bygame"
    bad_sql = (
        f"SELECT uid, sum(bet_nums) AS bet_cnt FROM ads.{view} "
        "WHERE stat_date >= toDate(fromUnixTimestamp64Milli(1)) "
        "AND (goods_type IN (1, 4)) GROUP BY uid"
    )
    contract = {
        "version": 1,
        "query_graph": [
            {
                "key": "agg:bet",
                "view": view,
                "role": "bet",
                "mode": "agg",
                "sql": bad_sql,
            }
        ],
        "schema_discovery": {
            "required": True,
            "described_views": {
                view: {"view": view, "fields": sorted(_BET_DAY_FIELDS)},
            },
        },
    }
    payload = build_export_run_state_payload(
        phase="fetch",
        mcp_query_count=0,
        budget=40,
        todos=[],
        export_contract=contract,
        schema_discovery={"required": True, "described_views": [view]},
    )
    ec = payload.get("export_contract") or {}
    graph = ec.get("query_graph") or []
    assert graph
    assert "goods_type" not in (graph[0].get("sql") or "")
    notes = list(ec.get("sql_schema_notes") or []) + list(
        payload.get("sql_schema_notes") or []
    )
    assert notes
    assert any("goods_type" in n for n in notes)
    blob = json_dumps_safe(payload)
    for snip in _HARD_GATE_SNIPPETS:
        assert snip not in blob


def test_soft_revalidate_no_hard_gate_copy():
    view = "view_result_pay_order_log"
    contract, _tw, notes = soft_revalidate_export_contract_sql(
        {
            "query_graph": [
                {
                    "view": view,
                    "sql": (
                        f"SELECT uid, count() AS c FROM ads.{view} "
                        "WHERE finish_time >= 1 AND status = 2 GROUP BY uid"
                    ),
                }
            ],
            "schema_discovery": {
                "described_views": {
                    view: {"fields": sorted(_PAY_FIELDS)},
                }
            },
        }
    )
    sql = (contract or {}).get("query_graph", [{}])[0].get("sql") or ""
    assert "status = 2" in sql
    text = " ".join(notes) + " " + str(contract)
    for snip in ("禁止写表", "禁止 FINAL"):
        assert snip not in text


def test_soft_align_query_graph_helper_and_no_invent():
    view = "view_result_gameuser_betstat_everyday_bygame"
    graph, notes = soft_align_query_graph_to_schema(
        [
            {
                "view": view,
                "sql": (
                    f"SELECT uid, sum(bet_nums) AS bet_cnt FROM ads.{view} "
                    "WHERE (goods_type IN (1, 4)) AND (type = 1) GROUP BY uid"
                ),
            }
        ],
        {view: sorted(_BET_DAY_FIELDS)},
    )
    sql = graph[0].get("sql") or ""
    assert "goods_type" not in sql
    assert "type = 1" not in sql
    # no invent: do not rewrite to another column name
    assert "product_type" not in sql
    assert notes


def json_dumps_safe(obj) -> str:
    import json

    return json.dumps(obj, ensure_ascii=False)
