"""sequence/top_n query-graph nodes: at most one pull per node key."""

from app.services.export_column_plan import (
    build_column_plan,
    build_query_graph,
    query_graph_done_enough,
)
from app.services.react_engine import (
    _query_node_auto_offset_modes,
    _query_node_already_pulled,
    _query_node_oneshot_modes,
)


def test_oneshot_modes_include_sequence():
    assert "sequence" in _query_node_oneshot_modes()
    assert "top_n" in _query_node_oneshot_modes()


def test_sequence_node_second_pull_is_noop():
    plan = build_column_plan(["用户ID", "总充值金额", "连续充值次数"])
    tw = {"start_ms": 1, "end_ms": 2, "cohort": "pay"}
    nodes = build_query_graph(plan, tw)
    seq = next(n for n in nodes if n.get("mode") == "sequence")
    key = str(seq.get("key") or "")
    assert key
    assert not _query_node_already_pulled(
        seq, done_keys=set(), failed_views=set(),
    )
    # After success mark
    assert _query_node_already_pulled(
        seq, done_keys={key}, failed_views=set(),
    )
    # After soft-fail mark
    assert _query_node_already_pulled(
        seq, done_keys=set(), failed_views={f"node:{key}"},
    )
    # agg on same view is not oneshot-blocked by sequence done key alone
    agg = next(
        n for n in nodes
        if n.get("mode") == "agg" and "pay" in (n.get("view") or "")
    )
    assert not _query_node_already_pulled(
        agg, done_keys={key}, failed_views=set(),
    )


def test_sequence_plan_does_not_invent_raw_bet_log():
    plan = build_column_plan(["用户ID", "连续充值次数"])
    nodes = build_query_graph(plan, {"start_ms": 1, "end_ms": 2, "cohort": "pay"})
    seq_nodes = [n for n in nodes if n.get("mode") == "sequence"]
    assert any(n.get("role") == "pay" for n in seq_nodes)
    # No planned bet view → do not invent user_bet_log sequence node
    assert not any(
        "user_bet_log" in str(n.get("view") or "") for n in nodes
    )
    assert not any(n.get("role") == "bet" for n in seq_nodes)


def test_sequence_nodes_are_oneshot_but_auto_offset_capable():
    assert "sequence" in _query_node_oneshot_modes()
    assert "sequence" in _query_node_auto_offset_modes()
    assert "top_n" not in _query_node_auto_offset_modes()


def test_done_enough_with_sequence_done_key():
    nodes = [
        {
            "key": "field:user",
            "view": "view_result_user_info",
            "role": "user",
            "segment": "identity",
            "mode": "field",
        },
        {
            "key": "agg:pay",
            "view": "view_result_pay_order_log",
            "role": "pay",
            "segment": "fact_agg",
            "mode": "agg",
        },
        {
            "key": "sequence:pay:streak",
            "view": "view_result_pay_order_log",
            "role": "pay",
            "segment": "sequence",
            "mode": "sequence",
        },
    ]
    pages = {
        "view_result_user_info": 1,
        "view_result_pay_order_log": 1,
    }
    # Aggregates / sequence are node-specific; a shared view page is not enough.
    assert not query_graph_done_enough(nodes, pages, set())
    # sequence-only fail (no view blacklist) still done when key marked
    assert not query_graph_done_enough(
        nodes,
        {"view_result_user_info": 1, "view_result_pay_order_log": 1},
        {f"node:sequence:pay:streak"},
        done_node_keys=set(),
    )
    assert query_graph_done_enough(
        nodes,
        {"view_result_user_info": 1, "view_result_pay_order_log": 1},
        set(),
        done_node_keys={"agg:pay", "sequence:pay:streak"},
    )
