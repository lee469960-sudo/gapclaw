"""OpenSpec task 6.4: three-project, ten-task pilot observation artifact."""

from __future__ import annotations

import hashlib
import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import (
    CodeAgentRun,
    CodeArtifact,
    CodeArtifactReview,
    CodePilotEvaluation,
    CodeProject,
)
from app.services.code_agent.evaluation import (
    PilotEvaluationError,
    create_pilot_plan,
    export_pilot_report,
    record_pilot_observation,
)


def _db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _projects(db):
    for index in range(3):
        db.add(CodeProject(
            id=f"project{index + 1}", name=f"Project {index + 1}", enabled=True,
            environment_tier="internal_non_production", creator="owner",
        ))
    db.commit()


def _tasks():
    return [{
        "task_key": f"low-risk-{index + 1:02d}",
        "project_id": f"project{index % 3 + 1}",
        "risk_level": "low",
    } for index in range(10)]


def test_pilot_artifact_records_all_required_signals_for_three_projects_and_ten_tasks(tmp_path):
    db = _db()
    _projects(db)
    rows = create_pilot_plan(
        db,
        pilot_id="pilot-2026-q3",
        project_ids=["project1", "project2", "project3"],
        tasks=_tasks(),
    )
    assert len(rows) == 10
    for index, evaluation in enumerate(rows):
        run_id = f"run{index + 1:02d}"
        artifact_id = f"art{index + 1:02d}"
        run = CodeAgentRun(
            id=run_id, agent_id="agent1", project_id=evaluation.project_id,
            manifest_id="manifest1", manifest_version=1, status="patch_ready",
            artifact_id=artifact_id, workspace_state="retained_read_only",
            verifier_report=json.dumps({
                "passed": True,
                "tests": [{"test_index": 0, "exit_code": 0, "output": "passed"}],
            }),
            verification_baseline=json.dumps({"status": "passed", "tests": []}),
            budget_usage=json.dumps({"elapsed_seconds": index + 0.5}),
        )
        db.add(run)
        db.add(CodeArtifact(
            id=artifact_id, run_id=run_id, project_id=evaluation.project_id,
            manifest_version=1, base_commit="a" * 40,
            diff_hash="b" * 64, policy_hash="c" * 64,
            verifier_report_hash="d" * 64, storage_path=f"/sealed/{run_id}", status="sealed",
        ))
        if index < 6:
            db.add(CodeArtifactReview(
                id=f"review{index + 1:02d}", artifact_id=artifact_id, run_id=run_id,
                project_id=evaluation.project_id, action="accepted", reviewer="reviewer",
                manifest_hash="e" * 64,
            ))
        db.commit()
        record_pilot_observation(
            db,
            evaluation=evaluation,
            run=run,
            cost_microunits=(index + 1) * 100,
        )

    report = export_pilot_report(
        db, pilot_id="pilot-2026-q3", root=tmp_path / "evaluations"
    )
    content = (tmp_path / "evaluations" / "pilot-2026-q3.json").read_bytes()
    payload = json.loads(content)

    assert payload["project_count"] == 3
    assert payload["task_count"] == 10
    assert len(payload["records"]) == 10
    assert payload["summary"] == {
        "average_elapsed_milliseconds": 5000,
        "cleanup_success_rate": 1.0,
        "human_acceptance_rate": 0.6,
        "total_cost_microunits": 5500,
        "verification_reproducibility_rate": 1.0,
    }
    assert all(record["risk_level"] == "low" for record in payload["records"])
    assert report.artifact_hash == hashlib.sha256(content).hexdigest()


def test_pilot_plan_rejects_wrong_project_count_or_non_low_risk_task():
    db = _db()
    _projects(db)
    with pytest.raises(PilotEvaluationError, match="pilot_requires_three_projects"):
        create_pilot_plan(
            db, pilot_id="bad", project_ids=["project1", "project2"], tasks=_tasks()
        )
    tasks = _tasks()
    tasks[0]["risk_level"] = "high"
    with pytest.raises(PilotEvaluationError, match="pilot_task_not_low_risk_allowlisted"):
        create_pilot_plan(
            db,
            pilot_id="bad-risk",
            project_ids=["project1", "project2", "project3"],
            tasks=tasks,
        )
