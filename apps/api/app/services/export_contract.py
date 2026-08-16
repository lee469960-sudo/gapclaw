"""Compile structured export contracts from resolved export context."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.services.export_task_spec import TaskSpec, build_task_spec
from app.services.export_task_validator import (
    TaskSpecValidationResult,
    validate_task_spec,
)


@dataclass
class ExportContract:
    task_spec: TaskSpec
    validation: TaskSpecValidationResult
    column_plan: list[dict[str, Any]] = field(default_factory=list)
    query_contract: dict[str, Any] = field(default_factory=dict)
    query_graph: list[dict[str, Any]] = field(default_factory=list)
    schema_discovery: dict[str, Any] = field(default_factory=dict)
    version: str = "export_contract.v1"

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "task_spec": self.task_spec.to_dict(),
            "task_spec_validation": self.validation.to_dict(),
            "column_plan": list(self.column_plan),
            "query_contract": dict(self.query_contract),
            "query_graph": list(self.query_graph),
            "schema_discovery": dict(self.schema_discovery or {}),
            "source_brief": self.task_spec.source_brief,
        }

    def to_trace_fields(self) -> dict[str, Any]:
        payload = self.to_dict()
        return {
            "export_contract": payload,
            "task_spec": payload["task_spec"],
            "task_spec_validation": payload["task_spec_validation"],
            "column_plan": payload["column_plan"],
            "query_contract": payload["query_contract"],
            "query_graph": payload["query_graph"],
            "schema_discovery": payload["schema_discovery"],
            "source_brief": payload["source_brief"],
        }


def build_export_contract(
    *,
    task_type: str,
    headers: list[str] | None,
    time_window: dict | None,
    resources: list[str] | None,
    pinned_views: list[str] | None = None,
    source_brief: str = "",
    column_plan: list[dict] | None = None,
    schema_discovery: dict[str, Any] | None = None,
) -> ExportContract:
    """Build TaskSpec + validation + output-column contract."""
    spec = build_task_spec(
        task_type=task_type or "multi_fact",
        headers=headers or [],
        time_window=time_window,
        resources=resources or [],
        pinned_views=pinned_views or [],
        source_brief=(source_brief or "")[:2000],
    )
    validation = validate_task_spec(spec)
    plan = [c for c in (column_plan or []) if isinstance(c, dict)]
    return ExportContract(
        task_spec=spec,
        validation=validation,
        column_plan=plan,
        query_graph=[],
        schema_discovery=dict(schema_discovery or {}),
    )
