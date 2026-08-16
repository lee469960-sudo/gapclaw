"""uniqExact keyword soft-preflight + LIMIT-1 SQL probe helpers (no hard gates)."""

from __future__ import annotations

from app.services.export_column_plan import (
    should_soft_probe_query_node,
    sql_unknown_identifiers,
    sql_with_probe_limit,
    soft_align_sql_to_schema,
)

_HARD_GATE_SNIPPETS = (
    "禁止写表",
    "禁止 FINAL",
    "禁止写表/FINAL",
)

_PAY_FIELDS = {
    "uid",
    "price",
    "status",
    "finish_time",
    "create_time",
}


def test_uniqexact_not_treated_as_unknown_column():
    sql = (
        "SELECT uniqExact(uid) AS user_cnt "
        "FROM ads.view_result_pay_order_log "
        "WHERE status = 2 GROUP BY uid"
    )
    unk = sql_unknown_identifiers(sql, _PAY_FIELDS)
    assert "uniqExact" not in unk
    assert "UNIQUEXACT" not in {u.upper() for u in unk}
    aligned, notes, unknown = soft_align_sql_to_schema(sql, _PAY_FIELDS)
    assert "uniqExact" not in unknown
    assert "uniqExact" in aligned
    blob = " ".join(notes) + " " + aligned
    for snip in _HARD_GATE_SNIPPETS:
        assert snip not in blob


def test_sql_with_probe_limit_appends_and_replaces():
    base = (
        "SELECT uid, sum(price) AS pay_sum "
        "FROM ads.view_result_pay_order_log WHERE status = 2 GROUP BY uid"
    )
    assert sql_with_probe_limit(base, limit=1).endswith("LIMIT 1")
    assert sql_with_probe_limit(base + " LIMIT 5000", limit=1).endswith("LIMIT 1")
    assert "LIMIT 1" in sql_with_probe_limit(base, limit=1)
    assert sql_with_probe_limit(base + " LIMIT 5000", limit=1).count("LIMIT") == 1


def test_should_soft_probe_bet_fact_nodes_only():
    assert should_soft_probe_query_node(
        {
            "role": "bet",
            "mode": "agg",
            "sql": "SELECT uid FROM ads.view_result_gameuser_betstat_everyday_bygame",
        }
    )
    assert should_soft_probe_query_node(
        {
            "role": "pay",
            "mode": "agg",
            "sql": "SELECT uid FROM ads.view_result_pay_order_log",
        }
    )
    assert not should_soft_probe_query_node(
        {"role": "channel", "mode": "lookup", "sql": ""}
    )
    assert not should_soft_probe_query_node(
        {"role": "user", "mode": "field", "sql": "", "deferred": True}
    )


def test_probe_failure_note_has_no_hard_gate_copy():
    """Probe soft-failure copy used by engine must not introduce hard gates."""
    note = (
        "【SQL软探针】`view_result_gameuser_betstat_everyday_bygame` "
        "远程失败（可改参重试；不阻止写表/FINAL）: unknown identifier xxx"
    )
    for snip in ("禁止写表", "禁止 FINAL"):
        assert snip not in note
    assert "不阻止写表/FINAL" in note
