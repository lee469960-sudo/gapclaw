"""Structured _run_state helpers for export execution resume."""

from __future__ import annotations

import json
import re
from typing import Any

from app.services.export_column_plan import (
    soft_align_query_graph_to_schema,
    soft_align_sql_to_schema,
)
from app.services.export_schema_discovery import schema_cache_to_view_fields


COHORT_ROW_COMPLETE_RATIO = 0.85


def schema_fields_by_view_from_run_state(
    *,
    schema_discovery: dict | None = None,
    export_contract: dict | None = None,
) -> dict[str, list[str]]:
    """Collect describe field sets for soft SQL align (prefer full evidence)."""
    out: dict[str, list[str]] = {}
    contract = export_contract if isinstance(export_contract, dict) else {}
    for blob in (
        schema_discovery if isinstance(schema_discovery, dict) else None,
        contract.get("schema_discovery")
        if isinstance(contract.get("schema_discovery"), dict)
        else None,
    ):
        if not isinstance(blob, dict):
            continue
        described = blob.get("described_views")
        if isinstance(described, dict):
            out.update(schema_cache_to_view_fields(described))
    return out


def _soft_align_time_window_sql_examples(
    time_window: dict | None,
    schema_fields_by_view: dict[str, list[str]] | None,
) -> tuple[dict | None, list[str]]:
    """Soft-align embedded example SQL in time_window when view fields exist."""
    if not isinstance(time_window, dict) or not schema_fields_by_view:
        return time_window, []
    notes: list[str] = []
    out = dict(time_window)
    sql_keys = (
        "sql_select",
        "user_sql_select",
        "pay_sql_select",
        "mcp_example",
        "user_mcp_example",
        "pay_mcp_example",
    )

    def _align_sql_text(sql: str) -> tuple[str, list[str]]:
        text = (sql or "").strip()
        if not text:
            return text, []
        m = re.search(r"\bads\.([A-Za-z_][A-Za-z0-9_]*)", text, re.I)
        if not m:
            return text, []
        view = m.group(1)
        fields = schema_fields_by_view.get(view) or []
        if not fields:
            return text, []
        aligned, align_notes, _unk = soft_align_sql_to_schema(text, fields)
        return aligned, align_notes

    for key in sql_keys:
        raw = out.get(key)
        if raw is None:
            continue
        if key.endswith("mcp_example") and isinstance(raw, str):
            text = raw.strip()
            # MCP example may be JSON args or a bare SQL fragment.
            if text.startswith("{"):
                try:
                    args = json.loads(text)
                except Exception:
                    args = None
                if isinstance(args, dict) and isinstance(args.get("sql"), str):
                    aligned, align_notes = _align_sql_text(args["sql"])
                    if align_notes:
                        args = dict(args)
                        args["sql"] = aligned
                        out[key] = json.dumps(args, ensure_ascii=False)
                        for n in align_notes:
                            tagged = f"time_window.{key}: {n}"
                            if tagged not in notes:
                                notes.append(tagged)
                continue
            aligned, align_notes = _align_sql_text(text)
            if align_notes:
                out[key] = aligned
                for n in align_notes:
                    tagged = f"time_window.{key}: {n}"
                    if tagged not in notes:
                        notes.append(tagged)
            continue
        if isinstance(raw, str):
            aligned, align_notes = _align_sql_text(raw)
            if align_notes:
                out[key] = aligned
                for n in align_notes:
                    tagged = f"time_window.{key}: {n}"
                    if tagged not in notes:
                        notes.append(tagged)
    return out, notes


def soft_revalidate_export_contract_sql(
    export_contract: dict | None,
    *,
    schema_discovery: dict | None = None,
    time_window: dict | None = None,
) -> tuple[dict | None, dict | None, list[str]]:
    """Re-soft-align query_graph / time_window SQL before _run_state persist.

    Returns (export_contract, time_window, sql_schema_notes). Diagnostic only.
    """
    fields_map = schema_fields_by_view_from_run_state(
        schema_discovery=schema_discovery,
        export_contract=export_contract,
    )
    notes: list[str] = []
    contract = dict(export_contract) if isinstance(export_contract, dict) else None
    tw = time_window
    if contract and fields_map:
        graph = contract.get("query_graph")
        if isinstance(graph, list):
            aligned_graph, graph_notes = soft_align_query_graph_to_schema(
                graph, fields_map
            )
            contract["query_graph"] = aligned_graph
            notes.extend(graph_notes)
        tw_aligned, tw_notes = _soft_align_time_window_sql_examples(tw, fields_map)
        tw = tw_aligned
        notes.extend(tw_notes)
        if notes:
            existing = [
                str(x).strip()
                for x in (contract.get("sql_schema_notes") or [])
                if str(x).strip()
            ]
            for n in notes:
                if n not in existing:
                    existing.append(n)
            contract["sql_schema_notes"] = existing[:48]
    return contract, tw, notes


def _dedupe(items: list[str] | tuple[str, ...] | set[str]) -> list[str]:
    return [x for x in dict.fromkeys(str(i).strip() for i in items if str(i).strip())]


def cohort_rows_incomplete(
    *,
    deliverable_rows: int | None,
    cohort_uid_estimate: int | None,
    ratio: float = COHORT_ROW_COMPLETE_RATIO,
) -> bool:
    if not cohort_uid_estimate or cohort_uid_estimate <= 0:
        return False
    if deliverable_rows is None:
        return False
    return int(deliverable_rows) < int(cohort_uid_estimate * ratio)


def serialize_time_window_for_state(time_window: dict | None) -> dict | None:
    if not isinstance(time_window, dict):
        return None
    start_ms = time_window.get("start_ms")
    end_ms = time_window.get("end_ms")
    if start_ms is None or end_ms is None:
        return None
    out = {
        "label": str(time_window.get("label") or "").strip(),
        "start_ms": int(start_ms),
        "end_ms": int(end_ms),
        "tz_label": str(time_window.get("tz_label") or "").strip(),
    }
    for k in (
        "sql_hint",
        "sql_select",
        "mcp_example",
        "utc_offset_hours",
        "cohort",
        "user_sql_select",
        "pay_sql_select",
        "user_mcp_example",
        "pay_mcp_example",
    ):
        if time_window.get(k) is not None:
            out[k] = time_window.get(k)
    return out


def hydrate_time_window_from_state(
    state: dict | None,
    *,
    page_limit: int = 10000,
) -> dict | None:
    """Rebuild engine time_window dict from persisted run_state.time_window."""
    if not isinstance(state, dict):
        return None
    tw = state.get("time_window")
    if not isinstance(tw, dict):
        return None
    try:
        start_ms = int(tw["start_ms"])
        end_ms = int(tw["end_ms"])
    except (KeyError, TypeError, ValueError):
        return None
    tz_label = str(tw.get("tz_label") or "").strip() or "见需求"
    label = str(tw.get("label") or "").strip() or f"{tz_label} [{start_ms},{end_ms})"
    cohort = str(tw.get("cohort") or "").strip() or "register"
    sql_hint = str(tw.get("sql_hint") or "").strip()
    sql_select = str(tw.get("sql_select") or "").strip()
    mcp_example = str(tw.get("mcp_example") or "").strip()
    user_sql = str(tw.get("user_sql_select") or "").strip()
    pay_sql = str(tw.get("pay_sql_select") or "").strip()
    out = {
        "tz_label": tz_label,
        "start_ms": start_ms,
        "end_ms": end_ms,
        "label": label,
        "sql_hint": sql_hint,
        "sql_select": sql_select,
        "mcp_example": mcp_example,
        "cohort": cohort,
        "user_sql_select": user_sql,
        "pay_sql_select": pay_sql,
        "user_mcp_example": str(tw.get("user_mcp_example") or "").strip(),
        "pay_mcp_example": str(tw.get("pay_mcp_example") or "").strip(),
    }
    if tw.get("utc_offset_hours") is not None:
        out["utc_offset_hours"] = tw.get("utc_offset_hours")
    return out


def derive_export_completeness(
    *,
    missing_roles: list[str] | None = None,
    fact_truncated_roles: list[str] | None = None,
    user_truncated: bool = False,
    user_fetch_complete: bool | None = None,
    deliverable: str = "",
    fallback: bool = False,
    deliverable_rows: int | None = None,
    cohort_uid_estimate: int | None = None,
    fetched_view_pages: dict[str, int] | None = None,
    has_task_data: bool | None = None,
    force: str = "",
) -> str:
    """Rule-derived completeness tag for _run_state."""
    if force:
        return str(force)
    pages = fetched_view_pages or {}
    total_pages = sum(int(n or 0) for n in pages.values())
    miss = [str(r).strip() for r in (missing_roles or []) if str(r).strip()]
    fact_miss = [r for r in miss if r in ("pay", "cash", "bet")]
    fact_trunc = [str(r).strip() for r in (fact_truncated_roles or []) if str(r).strip()]
    row_gap = cohort_rows_incomplete(
        deliverable_rows=deliverable_rows,
        cohort_uid_estimate=cohort_uid_estimate,
    )
    has_deliv = bool(str(deliverable or "").strip())
    if has_task_data is False and not has_deliv and total_pages <= 0:
        return "no_data"
    if has_task_data is True and not has_deliv:
        return "iters_exhausted"
    if not has_deliv and total_pages <= 0:
        return "no_data"
    if not has_deliv and total_pages > 0:
        return "iters_exhausted"
    if fallback:
        return "fallback"
    if fact_miss:
        return "fact_starved"
    if fact_trunc or user_truncated or user_fetch_complete is False or row_gap:
        return "truncated"
    if has_deliv and not miss:
        return "complete"
    if has_deliv:
        return "truncated"
    return "unknown"


def build_run_state_digest(
    *,
    completeness: str,
    missing_roles: list[str] | None = None,
    fact_truncated_roles: list[str] | None = None,
    need_continue_roles: list[str] | None = None,
    deliverable_rows: int | None = None,
    cohort_uid_estimate: int | None = None,
    has_task_data: bool = False,
    deliverable: str = "",
    mcp_query_count: int | None = None,
    mcp_budget: int | None = None,
    schema_discovery: dict | None = None,
) -> str:
    """1-3 sentence Chinese digest for humans / next-run prompts."""
    bits: list[str] = []
    if completeness == "no_data":
        bits.append("本轮无 MCP 落盘页（筛选可能无结果、权限或时间窗有误）。")
        bits.append("建议先 COUNT 核对，再改窗/改参重查。")
    elif completeness == "iters_exhausted":
        mcp_bit = ""
        if mcp_query_count is not None and mcp_budget is not None:
            mcp_bit = f"（MCP 仅 {int(mcp_query_count)}/{int(mcp_budget)}）"
        if has_task_data:
            if (deliverable or "").strip():
                bits.append(
                    f"轮次用尽{mcp_bit}：已写出当前目录 `{deliverable.strip()}`"
                    "（部分列可能未完整）。"
                )
            else:
                bits.append(
                    f"轮次用尽{mcp_bit}：task/ 已有过程数据，但未写出当前目录 xlsx。"
                )
                bits.append("请 SHELL 写表，或新会话说「按缺口补齐重新导出」。")
        else:
            bits.append(f"轮次用尽{mcp_bit}且无过程数据/交付物。")
    elif completeness == "fact_starved":
        miss = [r for r in (missing_roles or []) if r in ("pay", "cash", "bet")]
        bits.append("明细 role 未齐套：" + ("、".join(miss) if miss else "见 missing_roles") + "。")
    elif completeness == "truncated":
        trunc = list(need_continue_roles or fact_truncated_roles or [])
        if trunc:
            bits.append("明细满页截断：" + "、".join(trunc) + "；须 OFFSET 续翻至短页。")
        if cohort_rows_incomplete(
            deliverable_rows=deliverable_rows,
            cohort_uid_estimate=cohort_uid_estimate,
        ):
            bits.append(
                f"交付行数={deliverable_rows} << 目标≈{cohort_uid_estimate}（未标完整）。"
            )
    elif completeness == "fallback":
        bits.append("走了原始回退交付，不是完整中文分析表。")
    elif completeness == "complete":
        bits.append("短页齐套且已有交付：" + (deliverable or "见 deliverable") + "。")
    else:
        bits.append(f"完整度={completeness}。")
    schema = schema_discovery if isinstance(schema_discovery, dict) else {}
    if schema.get("required") and not schema.get("complete"):
        miss = _dedupe(schema.get("missing_describe_views") or [])
        if not schema.get("list_ads_views"):
            bits.append("Schema 发现未齐：缺 list_ads_views。")
        if miss:
            bits.append("Schema 发现未齐：缺 describe_ads_view " + "、".join(miss[:4]) + "。")
    return "".join(bits)[:400]


def summarize_schema_discovery(
    *,
    schema_discovery: dict | None = None,
    export_contract: dict | None = None,
) -> dict[str, Any]:
    """Compact schema evidence status for _run_state and digests."""
    contract = export_contract if isinstance(export_contract, dict) else {}
    schema = schema_discovery if isinstance(schema_discovery, dict) else {}
    if not schema and isinstance(contract.get("schema_discovery"), dict):
        schema = dict(contract["schema_discovery"])
    required = bool(schema.get("required"))
    listed = schema.get("list_ads_views")
    list_ok = bool(listed is True or (isinstance(listed, dict) and listed.get("captured")))
    described = schema.get("described_views")
    described_views = (
        _dedupe(list(described.keys())) if isinstance(described, dict) else []
    )
    explicit_complete = schema.get("complete")
    planned_views = [
        str(n.get("view") or "").strip()
        for n in (contract.get("query_graph") or [])
        if isinstance(n, dict) and str(n.get("view") or "").strip()
    ]
    missing = [v for v in _dedupe(planned_views) if v not in set(described_views)]
    if isinstance(explicit_complete, bool) and not planned_views:
        complete = explicit_complete
    else:
        complete = bool(not required)
        if required and list_ok and planned_views:
            complete = not missing
    return {
        "required": required,
        "list_ads_views": list_ok,
        "described_views": described_views,
        "missing_describe_views": missing,
        "complete": complete,
    }


def build_schema_discovery_next_actions(
    schema_summary: dict | None,
) -> list[dict[str, Any]]:
    schema = schema_summary if isinstance(schema_summary, dict) else {}
    if not schema.get("required") or schema.get("complete"):
        return []
    actions: list[dict[str, Any]] = []
    if not schema.get("list_ads_views"):
        actions.append({
            "role": "schema",
            "view": "",
            "offset": 0,
            "mcp_example": "MCP: list_ads_views {}",
            "hint": "补齐 schema discovery：先获取 MCP 接口/视图列表",
            "action_type": "discover_ads_views",
        })
    for view in _dedupe(schema.get("missing_describe_views") or [])[:4]:
        actions.append({
            "role": "schema",
            "view": view,
            "offset": 0,
            "mcp_example": f'MCP: describe_ads_view {{"view_name":"{view}"}}',
            "hint": f"补齐 schema discovery：获取 `{view}` 字段/表备注",
            "action_type": "describe_ads_views",
        })
    return actions


def build_schema_discovery_repair_plan(
    schema_summary: dict | None,
) -> dict[str, Any]:
    schema = schema_summary if isinstance(schema_summary, dict) else {}
    if not schema.get("required") or schema.get("complete"):
        return {}
    actions: list[dict[str, Any]] = []
    if not schema.get("list_ads_views"):
        actions.append({
            "action_type": "discover_ads_views",
            "priority": -2,
            "reason": "缺少 MCP 接口列表发现证据：先调用 list_ads_views",
            "role": "",
            "column": "",
            "views": [],
            "node_keys": [],
            "blocking": False,
        })
    missing = _dedupe(schema.get("missing_describe_views") or [])
    if missing:
        actions.append({
            "action_type": "describe_ads_views",
            "priority": -1,
            "reason": "缺少 MCP 表备注/字段发现证据：对计划 view 调用 describe_ads_view",
            "role": "",
            "column": "",
            "views": missing,
            "node_keys": [],
            "blocking": False,
        })
    if not actions:
        return {}
    return {
        "status": "repairable",
        "source_status": "repairable",
        "actions": actions,
        "preserve_done_node_keys": [],
        "skip_failed_node_keys": [],
        "repair_hints": ["补齐 MCP schema discovery 后再执行数据 query。"],
        "summary": {
            "action_count": len(actions),
            "blocking_count": 0,
        },
    }


def strip_completed_schema_repair_actions(
    repair_plan: dict | None,
    schema_summary: dict | None,
) -> dict[str, Any]:
    repair = repair_plan if isinstance(repair_plan, dict) else {}
    if not repair:
        return {}
    schema = schema_summary if isinstance(schema_summary, dict) else {}
    if not schema.get("complete"):
        return dict(repair)
    if not isinstance(repair.get("actions"), list):
        return dict(repair)
    schema_action_types = {"discover_ads_views", "describe_ads_views"}
    actions = [
        a
        for a in (repair.get("actions") or [])
        if isinstance(a, dict)
        and str(a.get("action_type") or "") not in schema_action_types
    ]
    if not actions:
        return {}
    out = dict(repair)
    out["actions"] = actions
    summary = dict(out.get("summary") or {})
    summary["action_count"] = len(actions)
    summary["blocking_count"] = sum(1 for a in actions if a.get("blocking"))
    out["summary"] = summary
    return out


def build_export_run_state_payload(
    *,
    phase: str,
    mcp_query_count: int,
    budget: int,
    todos: list[dict],
    deliverable: str = "",
    fallback: bool = False,
    covered_roles: list[str] | None = None,
    missing_roles: list[str] | None = None,
    fetched_view_pages: dict[str, int] | None = None,
    fetched_view_last_rows: dict[str, int] | None = None,
    mcp_failures: list[dict] | None = None,
    source_brief: str = "",
    task_title: str = "",
    time_window: dict | None = None,
    target_roles: list[str] | None = None,
    analyze_columns: list[str] | None = None,
    column_plan: list[dict] | None = None,
    completeness: str = "",
    short_page_roles: list[str] | None = None,
    need_continue_roles: list[str] | None = None,
    next_actions: list[dict] | None = None,
    digest: str = "",
    updated_at: str = "",
    session_id: str = "",
    user_intent: str = "",
    turn_digest: str = "",
    constraints: dict | None = None,
    export_contract: dict | None = None,
    repair_plan: dict | None = None,
    schema_discovery: dict | None = None,
    done_node_keys: list[str] | None = None,
    failed_views: list[str] | None = None,
    fact_page_cap: int | None = None,
    user_fetch_complete: bool | None = None,
    user_pages: int | None = None,
    user_truncated: bool | None = None,
    fact_truncated_roles: list[str] | None = None,
    deliverable_rows: int | None = None,
    cohort_uid_estimate: int | None = None,
    cte_attempts_used: int | None = None,
    cte_attempt_limit: int | None = None,
    cte_attempt_errors: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    export_contract, time_window, sql_schema_notes = soft_revalidate_export_contract_sql(
        export_contract,
        schema_discovery=schema_discovery,
        time_window=time_window,
    )
    schema_summary = summarize_schema_discovery(
        schema_discovery=schema_discovery,
        export_contract=export_contract,
    )
    if schema_summary.get("required"):
        if not digest:
            digest = build_run_state_digest(
                completeness=completeness,
                missing_roles=missing_roles,
                need_continue_roles=need_continue_roles,
                deliverable=deliverable,
                mcp_query_count=mcp_query_count,
                mcp_budget=budget,
                schema_discovery=schema_summary,
            )
        next_actions = [
            *build_schema_discovery_next_actions(schema_summary),
            *list(next_actions or []),
        ]
    payload: dict[str, Any] = {
        "phase": phase,
        "mcp_query_count": mcp_query_count,
        "budget": budget,
        "todos": todos,
        "deliverable": deliverable,
        "fallback": fallback,
        "covered_roles": list(covered_roles or []),
        "missing_roles": list(missing_roles or []),
        "fetched_view_pages": dict(fetched_view_pages or {}),
        "fetched_view_last_rows": dict(fetched_view_last_rows or {}),
        "done_node_keys": list(done_node_keys or []),
        "export_failed_views": list(failed_views or []),
        "mcp_failures": list(mcp_failures or [])[:8],
        "source_brief": (source_brief or "")[:4000],
        "task_title": (task_title or "").strip()[:160],
        "time_window": serialize_time_window_for_state(time_window),
        "target_roles": list(target_roles or []),
        "analyze_columns": list(analyze_columns or [])[:32],
        "column_plan": list(column_plan or [])[:40],
        "completeness": completeness,
        "short_page_roles": list(short_page_roles or []),
        "need_continue_roles": list(need_continue_roles or []),
        "next_actions": list(next_actions or [])[:6],
        "digest": digest,
        "updated_at": updated_at,
    }
    if schema_summary.get("required"):
        payload["schema_discovery"] = schema_summary
    cleaned_repair_plan = strip_completed_schema_repair_actions(
        repair_plan,
        schema_summary,
    )
    if cleaned_repair_plan:
        payload["repair_plan"] = cleaned_repair_plan
    elif schema_summary.get("required") and not schema_summary.get("complete"):
        schema_repair = build_schema_discovery_repair_plan(schema_summary)
        if schema_repair:
            payload["repair_plan"] = schema_repair
    if (session_id or "").strip():
        payload["session_id"] = str(session_id).strip()
    if (user_intent or "").strip():
        payload["user_intent"] = str(user_intent).strip()
    if (turn_digest or "").strip():
        payload["turn_digest"] = str(turn_digest).strip()[:400]
    if isinstance(constraints, dict) and constraints:
        payload["constraints"] = constraints
    if isinstance(export_contract, dict) and export_contract:
        payload["export_contract"] = export_contract
    if sql_schema_notes:
        payload["sql_schema_notes"] = list(sql_schema_notes)[:48]
    if fact_page_cap is not None:
        try:
            payload["fact_page_cap"] = int(fact_page_cap)
        except (TypeError, ValueError):
            pass
    if user_fetch_complete is not None:
        payload["user_fetch_complete"] = bool(user_fetch_complete)
    if user_pages is not None:
        payload["user_pages"] = int(user_pages)
    if user_truncated is not None:
        payload["user_truncated"] = bool(user_truncated)
    if fact_truncated_roles is not None:
        payload["fact_truncated_roles"] = list(fact_truncated_roles)
    if deliverable_rows is not None:
        payload["deliverable_rows"] = int(deliverable_rows)
    if cohort_uid_estimate is not None:
        try:
            payload["cohort_uid_estimate"] = int(cohort_uid_estimate)
        except (TypeError, ValueError):
            pass
    if cte_attempts_used is not None:
        try:
            payload["cte_attempts_used"] = int(cte_attempts_used)
        except (TypeError, ValueError):
            pass
    if cte_attempt_limit is not None:
        try:
            payload["cte_attempt_limit"] = int(cte_attempt_limit)
        except (TypeError, ValueError):
            pass
    if cte_attempt_errors is not None:
        payload["cte_attempt_errors"] = [
            dict(item) for item in (cte_attempt_errors or [])
            if isinstance(item, dict)
        ][:16]
    return payload
