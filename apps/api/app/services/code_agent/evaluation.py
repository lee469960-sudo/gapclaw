"""Pilot plan, observation records and immutable evaluation report artifacts."""

from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path

from app.config import get_settings
from app.models import (
    CodeArtifact,
    CodeArtifactReview,
    CodePilotEvaluation,
    CodePilotReport,
    CodeProject,
)
from app.security import new_id, now_str


class PilotEvaluationError(ValueError):
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


def create_pilot_plan(db, *, pilot_id: str, project_ids: list[str], tasks: list[dict]):
    unique_projects = list(dict.fromkeys(project_ids))
    if len(unique_projects) != 3:
        raise PilotEvaluationError("pilot_requires_three_projects")
    projects = db.query(CodeProject).filter(CodeProject.id.in_(unique_projects)).all()
    if (
        len(projects) != 3
        or any(not project.enabled for project in projects)
        or any(project.environment_tier != "internal_non_production" for project in projects)
    ):
        raise PilotEvaluationError("pilot_project_not_allowlisted_internal")
    if len(tasks) != 10:
        raise PilotEvaluationError("pilot_requires_ten_tasks")
    keys = [str(item.get("task_key") or "").strip() for item in tasks]
    if len(set(keys)) != 10 or any(not key for key in keys):
        raise PilotEvaluationError("pilot_task_key_invalid")
    if any(
        item.get("project_id") not in unique_projects or item.get("risk_level") != "low"
        for item in tasks
    ):
        raise PilotEvaluationError("pilot_task_not_low_risk_allowlisted")
    if {item["project_id"] for item in tasks} != set(unique_projects):
        raise PilotEvaluationError("pilot_projects_not_covered")
    rows = []
    for item, key in zip(tasks, keys):
        row = CodePilotEvaluation(
            id=new_id(),
            pilot_id=pilot_id,
            task_key=key,
            project_id=item["project_id"],
            risk_level="low",
            status="planned",
            created_at=now_str(),
        )
        db.add(row)
        rows.append(row)
    db.commit()
    return rows


def record_pilot_observation(db, *, evaluation, run, cost_microunits: int):
    if evaluation.status != "planned" or evaluation.project_id != run.project_id:
        raise PilotEvaluationError("pilot_observation_mismatch")
    if not isinstance(cost_microunits, int) or cost_microunits < 0:
        raise PilotEvaluationError("pilot_cost_invalid")
    try:
        report = json.loads(run.verifier_report or "{}")
        baseline = json.loads(run.verification_baseline or "{}")
        usage = json.loads(run.budget_usage or "{}")
    except json.JSONDecodeError as exc:
        raise PilotEvaluationError("pilot_run_facts_invalid") from exc
    artifact = (
        db.query(CodeArtifact).filter(CodeArtifact.id == run.artifact_id).first()
        if run.artifact_id else None
    )
    review = (
        db.query(CodeArtifactReview).filter(CodeArtifactReview.artifact_id == run.artifact_id).first()
        if run.artifact_id else None
    )
    tests = report.get("tests") if isinstance(report, dict) else None
    reproducible = bool(
        report.get("passed") is True
        and baseline.get("status") in {"passed", "broken"}
        and isinstance(tests, list)
        and tests
        and all(isinstance(item, dict) and item.get("exit_code") == 0 for item in tests)
        and artifact
        and artifact.status == "sealed"
    )
    evaluation.run_id = run.id
    evaluation.status = "observed"
    evaluation.result_status = run.status
    evaluation.verifier_reproducible = reproducible
    evaluation.human_accepted = bool(review and review.action == "accepted")
    evaluation.cost_microunits = cost_microunits
    evaluation.elapsed_milliseconds = max(0, round(float(usage.get("elapsed_seconds") or 0) * 1000))
    evaluation.cleanup_result = run.workspace_state
    evaluation.observed_at = now_str()
    db.commit()
    return evaluation


def export_pilot_report(db, *, pilot_id: str, root: str | Path | None = None) -> CodePilotReport:
    rows = db.query(CodePilotEvaluation).filter(
        CodePilotEvaluation.pilot_id == pilot_id
    ).order_by(CodePilotEvaluation.task_key).all()
    if len(rows) != 10 or any(row.status != "observed" for row in rows):
        raise PilotEvaluationError("pilot_observations_incomplete")
    if len({row.project_id for row in rows}) != 3:
        raise PilotEvaluationError("pilot_projects_not_covered")
    cleanup_success = {"sealed", "retained_read_only", "purged"}
    records = [{
        "task_key": row.task_key,
        "project_id": row.project_id,
        "run_id": row.run_id,
        "risk_level": row.risk_level,
        "result_status": row.result_status,
        "verifier_reproducible": row.verifier_reproducible,
        "human_accepted": row.human_accepted,
        "cost_microunits": row.cost_microunits,
        "elapsed_milliseconds": row.elapsed_milliseconds,
        "cleanup_result": row.cleanup_result,
    } for row in rows]
    total = len(records)
    payload = {
        "version": 1,
        "pilot_id": pilot_id,
        "project_count": 3,
        "task_count": 10,
        "records": records,
        "summary": {
            "verification_reproducibility_rate": sum(r["verifier_reproducible"] for r in records) / total,
            "human_acceptance_rate": sum(r["human_accepted"] for r in records) / total,
            "total_cost_microunits": sum(r["cost_microunits"] for r in records),
            "average_elapsed_milliseconds": round(
                sum(r["elapsed_milliseconds"] for r in records) / total
            ),
            "cleanup_success_rate": sum(
                r["cleanup_result"] in cleanup_success for r in records
            ) / total,
        },
    }
    content = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    report_root = Path(root or (Path(get_settings().data_dir) / "code-evaluations")).resolve()
    report_root.mkdir(parents=True, exist_ok=True)
    final = report_root / f"{pilot_id}.json"
    if final.exists() or db.query(CodePilotReport).filter(CodePilotReport.pilot_id == pilot_id).first():
        raise PilotEvaluationError("pilot_report_already_exists")
    temp = report_root / f".{pilot_id}.{uuid.uuid4().hex}.tmp"
    try:
        temp.write_bytes(content)
        temp.rename(final)
        final.chmod(0o400)
        report = CodePilotReport(
            id=new_id(),
            pilot_id=pilot_id,
            artifact_path=str(final),
            artifact_hash=hashlib.sha256(content).hexdigest(),
            status="complete",
            created_at=now_str(),
        )
        db.add(report)
        db.commit()
        return report
    except Exception:
        db.rollback()
        if temp.exists():
            temp.unlink()
        if final.exists():
            final.chmod(0o600)
            final.unlink()
        raise
