"""Query graph execution planning helpers for export runs."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.services.export_column_plan import build_query_graph, query_graph_done_enough
from app.services.export_repair_plan import (
    limit_query_nodes_for_repair,
    prioritize_query_nodes_for_repair,
    repair_plan_should_reverify_after_progress,
)
from app.services.export_trace import classify_mcp_error


@dataclass
class QueryExecutionPlan:
    nodes: list[dict[str, Any]] = field(default_factory=list)
    scoped_by_repair: bool = False
    repair_action_types: list[str] = field(default_factory=list)

    @property
    def progress_suffix(self) -> str:
        if not self.repair_action_types:
            return ""
        suffix = "RepairPlan 已优先排序查询图 actions=" + ",".join(
            self.repair_action_types[:5]
        )
        if self.scoped_by_repair:
            suffix += f" scoped_nodes={len(self.nodes)}"
        return suffix


@dataclass
class QueryNodeFailureDecision:
    soft_fail: bool = False
    node_only_failure: bool = False
    mark_node_done: bool = False
    mark_node_failed: bool = False
    status: str = "error"
    trace_record: dict[str, Any] = field(default_factory=dict)


@dataclass
class QueryNodeRowsDecision:
    mark_node_done: bool = False
    user_fetch_complete: bool = False
    full_page: bool = False


def build_query_execution_plan(
    *,
    column_plan: list[dict] | None,
    time_window: dict | None,
    dim_views: dict[str, str] | None = None,
    repair_plan: dict[str, Any] | None = None,
    repair_intent: bool = False,
    page_limit: int = 10000,
    schema_fields_by_view: dict[str, list[str]] | None = None,
    bound_only: bool = False,
) -> QueryExecutionPlan:
    nodes = build_query_graph(
        column_plan or [],
        time_window,
        dim_views=dim_views or {},
        page_limit=page_limit,
        schema_fields_by_view=schema_fields_by_view,
        bound_only=bound_only,
    )
    repair = repair_plan if isinstance(repair_plan, dict) else {}
    actions = [
        str(a.get("action_type") or "")
        for a in (repair.get("actions") or [])
        if isinstance(a, dict) and str(a.get("action_type") or "")
    ]
    scoped = False
    if repair_intent and actions:
        nodes, scoped = limit_query_nodes_for_repair(nodes, repair)
        nodes = prioritize_query_nodes_for_repair(nodes, repair)
    return QueryExecutionPlan(
        nodes=nodes,
        scoped_by_repair=scoped,
        repair_action_types=actions,
    )


def query_node_should_skip(
    node: dict[str, Any],
    *,
    done_node_keys: set[str],
    failed_views: set[str],
    fetched_view_pages: dict[str, int],
    already_pulled: bool = False,
    oneshot_modes: set[str] | frozenset[str] | None = None,
) -> bool:
    view = str(node.get("view") or "")
    mode = str(node.get("mode") or "")
    node_key = str(node.get("key") or view)
    if already_pulled:
        return True
    if node_key and f"node:{node_key}" in failed_views:
        return True
    if node_key and node_key in done_node_keys:
        return True
    if view and view in failed_views:
        return True
    if view and int(fetched_view_pages.get(view, 0) or 0) >= 1:
        if mode in ("lookup", "field"):
            if mode in (oneshot_modes or frozenset()) and node_key:
                done_node_keys.add(node_key)
            return True
    return False


def build_query_node_failure_decision(
    node: dict[str, Any],
    *,
    error: str,
    sql: str = "",
    fetched_view_pages: dict[str, int] | None = None,
    oneshot_modes: set[str] | frozenset[str] | None = None,
) -> QueryNodeFailureDecision:
    view = str(node.get("view") or "")
    mode = str(node.get("mode") or "agg").strip().lower()
    node_key = str(node.get("key") or view)
    role = str(node.get("role") or "").strip().lower()
    cost = str(node.get("cost") or "")
    segment = str(node.get("segment") or "")
    soft = (
        cost == "heavy"
        or bool(node.get("incomplete_ok"))
        or segment in ("sequence", "top_n")
        or mode in (oneshot_modes or frozenset())
    )
    pages = fetched_view_pages or {}
    node_only = bool(
        soft
        and (
            int(pages.get(view, 0) or 0) >= 1
            or segment in ("sequence", "top_n")
            or mode in (oneshot_modes or frozenset())
        )
    )
    return QueryNodeFailureDecision(
        soft_fail=soft,
        node_only_failure=node_only,
        mark_node_done=node_only,
        mark_node_failed=node_only,
        status="soft_fail" if soft else "error",
        trace_record={
            "key": str(node_key or ""),
            "role": str(role or ""),
            "view": str(view or ""),
            "sql": str(sql or "")[:2000],
            "error": error,
            "error_class": classify_mcp_error(error),
            "status": "soft_fail" if soft else "error",
        },
    )


def build_query_node_rows_decision(
    node: dict[str, Any],
    *,
    row_count: int,
    full_page_rows: int,
    oneshot_modes: set[str] | frozenset[str] | None = None,
) -> QueryNodeRowsDecision:
    mode = str(node.get("mode") or "agg").strip().lower()
    role = str(node.get("role") or "").strip().lower()
    full_page = int(row_count or 0) >= int(full_page_rows)
    mark_done = mode not in ("field", "raw") or mode in (oneshot_modes or frozenset())
    user_complete = bool(
        role == "user"
        and (
            not full_page
            or mode in ("field", "agg", "flag", "top_n")
        )
    )
    return QueryNodeRowsDecision(
        mark_node_done=mark_done,
        user_fetch_complete=user_complete,
        full_page=full_page,
    )


def query_execution_ready(
    nodes: list[dict[str, Any]] | None,
    *,
    fetched_view_pages: dict[str, int],
    failed_views: set[str],
    done_node_keys: set[str],
) -> bool:
    return bool(nodes) and query_graph_done_enough(
        nodes or [],
        fetched_view_pages,
        failed_views,
        done_node_keys=done_node_keys,
    )


def should_reverify_after_query_progress(
    repair_plan: dict[str, Any] | None,
    *,
    landed_count: int,
    scoped_by_repair: bool,
) -> bool:
    return repair_plan_should_reverify_after_progress(
        repair_plan,
        landed_count=landed_count,
        scoped=scoped_by_repair,
    )
