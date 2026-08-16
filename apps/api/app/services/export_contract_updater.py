"""Helpers for keeping export trace contract fields in sync."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.services.export_column_plan import (
    apply_schema_hints_to_column_plan,
    build_query_graph,
)
from app.services.export_schema_discovery import ViewSchemaHint
from app.services.export_trace import ExportTrace


@dataclass
class ExportContractUpdate:
    column_plan: list[dict[str, Any]] = field(default_factory=list)
    query_graph: list[dict[str, Any]] = field(default_factory=list)
    schema_discovery: dict[str, Any] = field(default_factory=dict)


def sync_export_contract_state(
    trace: ExportTrace,
    *,
    column_plan: list[dict[str, Any]] | None,
    time_window: dict | None,
    schema_hints: dict[str, ViewSchemaHint | dict[str, Any]] | None = None,
    schema_required: bool | None = None,
    dim_views: dict[str, str] | None = None,
) -> ExportContractUpdate:
    """Apply schema hints, rebuild query graph, and mirror fields to trace contract."""
    plan = [dict(c) for c in (column_plan or []) if isinstance(c, dict)]
    if schema_hints:
        plan = apply_schema_hints_to_column_plan(plan, schema_hints=schema_hints)
    schema_fields_by_view: dict[str, list[str]] = {}
    if isinstance(schema_hints, dict):
        for v, h in schema_hints.items():
            if not v:
                continue
            fields = list(getattr(h, "fields", None) or [])
            if not fields and isinstance(h, dict):
                fields = [str(x) for x in (h.get("fields") or []) if x]
            if fields:
                schema_fields_by_view[str(v)] = fields
    graph = build_query_graph(
        plan,
        time_window,
        dim_views=dim_views or {},
        schema_fields_by_view=schema_fields_by_view or None,
        bound_only=True,
    )

    trace.column_plan = list(plan)
    trace.query_graph = list(graph)
    if isinstance(trace.export_contract, dict):
        trace.export_contract["column_plan"] = list(plan)
        trace.export_contract["query_graph"] = list(graph)

    if schema_required is not None:
        schema = dict(trace.schema_discovery or {})
        schema["required"] = bool(schema_required)
        schema.setdefault("list_ads_views", {})
        schema.setdefault("described_views", {})
        trace.set_schema_discovery(schema)

    return ExportContractUpdate(
        column_plan=plan,
        query_graph=graph,
        schema_discovery=dict(trace.schema_discovery or {}),
    )


def record_schema_list(
    trace: ExportTrace,
    text: str,
) -> dict[str, Any]:
    """Record list_ads_views evidence and mirror it into export_contract."""
    trace.record_ads_views_list(text or "")
    return dict(trace.schema_discovery or {})


def record_schema_hint(
    trace: ExportTrace,
    *,
    view: str,
    schema_hint: ViewSchemaHint,
    column_plan: list[dict[str, Any]] | None,
    time_window: dict | None,
    schema_hints: dict[str, ViewSchemaHint | dict[str, Any]] | None = None,
    dim_views: dict[str, str] | None = None,
) -> ExportContractUpdate:
    """Record describe_ads_view evidence and refresh plan/graph/contract."""
    if view and schema_hint.fields:
        trace.record_ads_view_schema(view, schema_hint.to_dict())
    return sync_export_contract_state(
        trace,
        column_plan=column_plan,
        time_window=time_window,
        schema_hints=schema_hints,
        dim_views=dim_views,
    )
