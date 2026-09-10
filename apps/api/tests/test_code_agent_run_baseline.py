"""OpenSpec task 4.4: freeze run task contract and audit facts.

A published Manifest carries immutable source evidence (``resolved_commit``,
``snapshot_id``, ``snapshot_hash``, ``image_digest``). Run admission must freeze
that evidence into the run row and a ``run_create`` audit fact, and must never
re-resolve a symbolic ref or re-import from Git. Therefore later branch/tag
drift or a Git-service outage cannot change an existing Manifest's commit/snapshot,
and repeated runs share one identical frozen baseline.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import (
    Agent,
    CodeAgentRun,
    CodeControlAudit,
    CodeProject,
    CodeProjectManifest,
    CodeSourceSnapshot,
)
import app.services.code_agent.control_plane as control_plane
import app.services.code_agent.git_importer as git_importer
from app.services.code_agent.control_plane import create_code_run

COMMIT = "a" * 40
DRIFTED_COMMIT = "d" * 40
SNAPSHOT_HASH = "b" * 64
IMAGE_DIGEST = "sha256:" + "c" * 64
ORIGIN = "ssh://git.internal:22"


def _db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _project(db):
    project = CodeProject(
        id="project1",
        name="Project",
        enabled=True,
        environment_tier="internal_non_production",
        organization_id="default",
        creator="admin",
    )
    db.add(project)
    db.commit()
    return project


def _manifest(db, **overrides):
    values = dict(
        id="manifest1",
        project_id="project1",
        version=1,
        status="published",
        source_id="source1",
        source_type="ssh",
        requested_ref="main",
        resolved_commit=COMMIT,
        snapshot_id="snapshot1",
        snapshot_hash=SNAPSHOT_HASH,
        source_scan_report_id="scan1",
        repository="ssh://git.internal/example/repo.git",
        base_commit=COMMIT,
        allowed_paths='["src/"]',
        validation_plan='[{"command": "pytest -q"}]',
        trusted_image="internal/python:3.12",
        image_digest=IMAGE_DIGEST,
        security_schema_version=1,
        allowed_tools='["read", "test"]',
        policy='{"network": false}',
        budgets='{"timeout_seconds": 60}',
    )
    values.update(overrides)
    manifest = CodeProjectManifest(**values)
    db.add(manifest)
    db.commit()
    return manifest


def _seed_ready(db, monkeypatch):
    """Seed a sealed snapshot + agent and mock ready settings for the synthetic manifest."""
    db.add(CodeSourceSnapshot(
        id="snapshot1",
        source_id="source1",
        resolved_commit=COMMIT,
        content_hash=SNAPSHOT_HASH,
        storage_path="/tmp/snapshot1",
        scan_report_id="scan1",
        importer_version="1",
        policy_hash="p" * 64,
        status="sealed",
    ))
    db.add(Agent(id="agent1", name="Code Agent", profile="code", code_project_id="project1"))
    db.commit()

    class ReadySettings:
        code_repository_allowlist = json.dumps([ORIGIN])
        code_repository_internal_cidrs = "[]"
        code_local_repository_roots = "[]"
        code_trusted_image_digests = json.dumps([IMAGE_DIGEST])

        def code_agent_security_readiness(self):
            return {"ready": True, "status": "ready", "reason": "ready", "errors": {}}

    monkeypatch.setattr(control_plane, "get_settings", lambda: ReadySettings())
    monkeypatch.setattr(
        control_plane,
        "_running_bound_sandbox",
        lambda _db, _agent: SimpleNamespace(
            id="sandbox1", image="internal/python:3.12", network_mode="none"
        ),
    )


def _actor():
    return SimpleNamespace(
        username="admin", roles='["admin"]', organization_id="default", permissions="[]"
    )


def _freeze_offline(monkeypatch):
    """Simulate a Git-service outage so any ref resolve/import would raise."""

    def _offline(*args, **kwargs):
        raise RuntimeError("git service offline")

    monkeypatch.setattr(git_importer.RestrictedGitImporter, "_run_git", _offline)
    monkeypatch.setattr(git_importer.RestrictedGitImporter, "_resolve_commit", _offline)
    monkeypatch.setattr(git_importer.RestrictedGitImporter, "import_remote", _offline)
    monkeypatch.setattr(git_importer.RestrictedGitImporter, "import_from_staging", _offline)


def _baseline(run):
    return (
        run.resolved_commit,
        run.snapshot_id,
        run.snapshot_hash,
        run.image_digest,
        run.effective_policy_hash,
        run.task_contract,
    )


def test_run_creation_freezes_manifest_commit_and_snapshot_without_resolving_ref(monkeypatch):
    db = _db()
    project = _project(db)
    manifest = _manifest(db)
    _seed_ready(db, monkeypatch)

    agent = db.get(Agent, "agent1")
    run = create_code_run(db, agent=agent, actor=_actor(), project_id=project.id, objective="Fix")
    db.commit()

    assert run.resolved_commit == COMMIT
    assert run.snapshot_id == "snapshot1"
    assert run.snapshot_hash == SNAPSHOT_HASH
    assert run.requested_ref == "main"
    # Run admission records the frozen SHA, never a re-resolved symbolic ref.
    assert run.resolved_commit == manifest.resolved_commit
    # Creating a run must not mutate the published Manifest's frozen evidence.
    assert manifest.resolved_commit == COMMIT
    assert manifest.snapshot_id == "snapshot1"
    assert manifest.snapshot_hash == SNAPSHOT_HASH


def test_repeated_runs_share_identical_frozen_baseline_even_with_git_offline(monkeypatch):
    db = _db()
    project = _project(db)
    _manifest(db)
    _seed_ready(db, monkeypatch)
    _freeze_offline(monkeypatch)

    agent = db.get(Agent, "agent1")
    first = create_code_run(db, agent=agent, actor=_actor(), project_id=project.id, objective="Fix")
    second = create_code_run(db, agent=agent, actor=_actor(), project_id=project.id, objective="Fix")
    db.commit()

    assert first.id != second.id
    assert _baseline(first) == _baseline(second)
    assert first.resolved_commit == COMMIT
    assert first.snapshot_id == "snapshot1"
    assert first.snapshot_hash == SNAPSHOT_HASH


def test_run_create_audit_fact_records_frozen_commit_snapshot_and_policy_hash(monkeypatch):
    db = _db()
    project = _project(db)
    _manifest(db)
    _seed_ready(db, monkeypatch)

    agent = db.get(Agent, "agent1")
    run = create_code_run(db, agent=agent, actor=_actor(), project_id=project.id, objective="Fix")
    db.commit()

    audit = db.query(CodeControlAudit).filter(
        CodeControlAudit.action == "run_create"
    ).one()
    details = json.loads(audit.details)
    assert audit.actor == "admin"
    assert audit.project_id == project.id
    assert audit.manifest_id == "manifest1"
    assert details["run_id"] == run.id
    assert details["manifest_version"] == 1
    assert details["source_id"] == "source1"
    assert details["source_type"] == "ssh"
    assert details["requested_ref"] == "main"
    assert details["resolved_commit"] == COMMIT
    assert details["snapshot_id"] == "snapshot1"
    assert details["snapshot_hash"] == SNAPSHOT_HASH
    assert details["image_digest"] == IMAGE_DIGEST
    assert details["effective_policy_hash"] == run.effective_policy_hash


def test_existing_run_baseline_survives_manifest_drift(monkeypatch):
    db = _db()
    project = _project(db)
    manifest = _manifest(db)
    _seed_ready(db, monkeypatch)

    agent = db.get(Agent, "agent1")
    run = create_code_run(db, agent=agent, actor=_actor(), project_id=project.id, objective="Fix")
    db.commit()
    original_baseline = _baseline(run)

    # Simulate branch/tag drift: the source ref now points at a different commit
    # and a new sealed snapshot backs it. The existing run must keep its baseline.
    manifest.resolved_commit = DRIFTED_COMMIT
    manifest.base_commit = DRIFTED_COMMIT
    db.query(CodeSourceSnapshot).filter(CodeSourceSnapshot.id == "snapshot1").update(
        {"resolved_commit": DRIFTED_COMMIT}
    )
    db.commit()

    db.refresh(run)
    assert _baseline(run) == original_baseline
    assert run.resolved_commit == COMMIT
    assert run.snapshot_hash == SNAPSHOT_HASH

    audit = db.query(CodeControlAudit).filter(
        CodeControlAudit.action == "run_create"
    ).one()
    assert json.loads(audit.details)["resolved_commit"] == COMMIT
