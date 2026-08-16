"""Verifier + repair-plan + outcome finalization for export deliverables."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.services.export_engine_outcome import EngineOutcome, build_engine_outcome
from app.services.export_repair_plan import build_repair_plan
from app.services.export_trace import ExportTrace, write_export_trace
from app.services.export_verifier import verify_export_deliverable


@dataclass
class ExportFinalizerResult:
    verifier_result: dict[str, Any] = field(default_factory=dict)
    repair_plan: dict[str, Any] = field(default_factory=dict)
    outcome: EngineOutcome = field(default_factory=EngineOutcome)


def outcome_from_verification(
    verifier_result: dict[str, Any] | None,
    repair_plan: dict[str, Any] | None = None,
    *,
    mode: str = "analyzed",
    prefer_fallback: bool = False,
) -> EngineOutcome:
    return build_engine_outcome(
        verifier_result,
        repair_plan,
        mode=mode,
        prefer_fallback=prefer_fallback,
    )


def verify_export_finalizer(
    *,
    sandbox_id: str,
    run_id: str,
    file_rel: str,
    task_spec: dict | None,
    column_plan: list[dict] | None,
    trace: ExportTrace,
    failed_views: set[str] | list[str] | None = None,
    abandoned_roles: set[str] | list[str] | None = None,
    mode: str = "analyzed",
    prefer_fallback: bool = False,
) -> ExportFinalizerResult:
    verifier = verify_export_deliverable(
        sandbox_id=sandbox_id,
        file_rel=file_rel,
        task_spec=task_spec,
        column_plan=column_plan,
        trace=trace.to_dict(),
        failed_views=failed_views,
        abandoned_roles=abandoned_roles,
    )
    trace.set_verification(verifier)
    trace.set_repair_plan(build_repair_plan(verifier, trace.to_dict()))
    write_export_trace(sandbox_id, run_id, trace)
    outcome = outcome_from_verification(
        verifier,
        trace.repair_plan,
        mode=mode,
        prefer_fallback=prefer_fallback,
    )
    return ExportFinalizerResult(
        verifier_result=verifier,
        repair_plan=dict(trace.repair_plan or {}),
        outcome=outcome,
    )
