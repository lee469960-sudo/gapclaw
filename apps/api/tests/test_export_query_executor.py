"""Query execution planning contract for export query graphs."""

from app.services.export_column_plan import build_column_plan
from app.services.export_query_executor import (
    build_query_node_failure_decision,
    build_query_node_rows_decision,
    build_query_execution_plan,
    query_execution_ready,
    query_node_should_skip,
    should_reverify_after_query_progress,
)


def test_query_execution_plan_scopes_and_orders_repair_nodes():
    plan = build_column_plan(["用户ID", "总充值金额", "总提现金额"])
    base = build_query_execution_plan(
        column_plan=plan,
        time_window={"start_ms": 1, "end_ms": 2, "cohort": "pay"},
    )
    cash_node = next(n for n in base.nodes if n.get("role") == "cash")
    repair = {
        "status": "blocked",
        "actions": [
            {
                "action_type": "fetch_core_role",
                "priority": 0,
                "role": "cash",
                "node_keys": [cash_node["key"]],
            }
        ],
    }
    scoped = build_query_execution_plan(
        column_plan=plan,
        time_window={"start_ms": 1, "end_ms": 2, "cohort": "pay"},
        repair_plan=repair,
        repair_intent=True,
    )
    assert scoped.scoped_by_repair
    assert [n["key"] for n in scoped.nodes] == [cash_node["key"]]
    assert "fetch_core_role" in scoped.progress_suffix
    assert "scoped_nodes=1" in scoped.progress_suffix


def test_query_node_should_skip_marks_oneshot_done():
    done: set[str] = set()
    failed: set[str] = set()
    node = {
        "key": "lookup:channel",
        "view": "view_result_config_channel",
        "mode": "lookup",
    }
    assert query_node_should_skip(
        node,
        done_node_keys=done,
        failed_views=failed,
        fetched_view_pages={"view_result_config_channel": 1},
        oneshot_modes={"lookup"},
    )
    assert "lookup:channel" in done

    seq = {
        "key": "sequence:pay:streak",
        "view": "view_result_pay_order_log",
        "mode": "sequence",
    }
    assert not query_node_should_skip(
        seq,
        done_node_keys=set(),
        failed_views=set(),
        fetched_view_pages={"view_result_pay_order_log": 1},
        oneshot_modes={"agg"},
    )


def test_agg_nodes_do_not_skip_after_same_view_page():
    assert not query_node_should_skip(
        {
            "key": "agg:pay:card_cnt",
            "view": "view_result_pay_order_log",
            "mode": "agg",
        },
        done_node_keys=set(),
        failed_views=set(),
        fetched_view_pages={"view_result_pay_order_log": 1},
        oneshot_modes={"sequence", "top_n"},
    )


def test_query_execution_ready_and_reverify_decision():
    plan = build_column_plan(["用户ID", "总充值金额"])
    query_plan = build_query_execution_plan(
        column_plan=plan,
        time_window={"start_ms": 1, "end_ms": 2, "cohort": "pay"},
    )
    pages = {
        "view_result_user_info": 1,
        "view_result_pay_order_log": 1,
    }
    assert query_execution_ready(
        query_plan.nodes,
        fetched_view_pages=pages,
        failed_views=set(),
        done_node_keys={str(n.get("key")) for n in query_plan.nodes},
    )
    assert should_reverify_after_query_progress(
        {"actions": [{"action_type": "fetch_noncore_or_keep_incomplete"}]},
        landed_count=1,
        scoped_by_repair=True,
    )
    assert not should_reverify_after_query_progress(
        {"actions": [{"action_type": "inspect_verifier_warnings"}]},
        landed_count=1,
        scoped_by_repair=False,
    )


def test_query_node_failure_decision_marks_soft_node_only():
    node = {
        "key": "top_n:bet:game",
        "view": "view_result_user_bet_log",
        "role": "bet",
        "mode": "top_n",
        "segment": "top_n",
    }
    decision = build_query_node_failure_decision(
        node,
        error="Unknown expression amount",
        sql="SELECT bad FROM ads.view_result_user_bet_log",
        fetched_view_pages={},
        oneshot_modes={"top_n"},
    )
    assert decision.soft_fail
    assert decision.node_only_failure
    assert decision.mark_node_done
    assert decision.mark_node_failed
    assert decision.trace_record["status"] == "soft_fail"
    assert decision.trace_record["error_class"] == "unknown_column"


def test_top_n_does_not_skip_after_sequence_page_on_same_view():
    done_keys: set[str] = set()
    node = {
        "key": "top_n:view_result_user_bet_log:game",
        "view": "view_result_user_bet_log",
        "role": "bet",
        "mode": "top_n",
        "segment": "top_n",
    }
    assert not query_node_should_skip(
        node,
        done_node_keys=done_keys,
        failed_views=set(),
        fetched_view_pages={"view_result_user_bet_log": 1},
        oneshot_modes={"sequence", "top_n"},
    )
    assert not done_keys


def test_query_node_rows_decision_marks_user_complete_and_full_page():
    user_node = {
        "key": "field:user:identity",
        "view": "view_result_user_info",
        "role": "user",
        "mode": "field",
    }
    short = build_query_node_rows_decision(
        user_node,
        row_count=10,
        full_page_rows=9000,
        oneshot_modes={"field"},
    )
    assert short.mark_node_done
    assert short.user_fetch_complete
    assert not short.full_page

    raw_full = build_query_node_rows_decision(
        {"role": "pay", "mode": "raw"},
        row_count=9000,
        full_page_rows=9000,
        oneshot_modes={"field"},
    )
    assert not raw_full.mark_node_done
    assert raw_full.full_page
