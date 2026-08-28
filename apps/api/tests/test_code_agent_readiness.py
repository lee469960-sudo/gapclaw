"""OpenSpec task 4.3: fine-grained project readiness and run-admission reasons.

Every non-ready state must surface a distinct, stable reason string instead of
collapsing into ``manifest_invalid``, and an unready project must never create a
Code run. These reasons are:

    repository_source_not_allowed  -> source type/locator empty or outside the allowlist
    repository_auth_failed         -> credential reference missing/inactive/mis-scoped
    repository_ref_invalid         -> resolved commit is not a 40/64-hex SHA
    snapshot_invalid               -> snapshot missing/unsealed/hash-or-commit mismatch
    image_digest_invalid           -> image digest outside the trusted set
    workspace_mount_invalid        -> Workspace API/host roots misconfigured
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import (
    Agent,
    CodeAgentRun,
    CodeDeployCredential,
    CodeProject,
    CodeProjectManifest,
    CodeSourceSnapshot,
)
import app.services.code_agent.control_plane as control_plane
from app.services.code_agent.control_plane import (
    ManifestUnavailableError,
    create_code_run,
    project_availability,
    secure_readiness_reason,
)

COMMIT = "a" * 40
SNAPSHOT_HASH = "b" * 64
IMAGE_DIGEST = "sha256:" + "c" * 64
ORIGIN = "ssh://git.internal:22"


class _Settings:
    def __init__(self, *, allowlist, trusted_digests, errors=None):
        self.code_repository_allowlist = json.dumps(allowlist)
        self.code_local_repository_roots = "[]"
        self.code_trusted_image_digests = json.dumps(trusted_digests)
        self._errors = errors or {}

    def code_agent_security_readiness(self):
        ready = not self._errors
        return {
            "ready": ready,
            "status": "ready" if ready else "unavailable",
            "reason": "ready" if ready else "code_agent_security_config_invalid",
            "errors": self._errors,
        }


def _db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _project(db):
    project = CodeProject(
        id="project1", name="P", organization_id="default", creator="admin"
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
        credential_ref="cred1",
        requested_ref="main",
        resolved_commit=COMMIT,
        snapshot_id="snap1",
        snapshot_hash=SNAPSHOT_HASH,
        source_scan_report_id="scan1",
        repository="ssh://git.internal/example/repo.git",
        base_commit=COMMIT,
        allowed_paths='["src/"]',
        validation_plan='[{"command": "pytest -q"}]',
        trusted_image="runner@example",
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


def _seed_ready(db):
    db.add(CodeSourceSnapshot(
        id="snap1",
        source_id="source1",
        resolved_commit=COMMIT,
        content_hash=SNAPSHOT_HASH,
        storage_path="/tmp/snap1",
        scan_report_id="scan1",
        importer_version="1",
        policy_hash="p" * 64,
        status="sealed",
    ))
    db.add(CodeDeployCredential(
        id="cred1",
        organization_id="default",
        label="deploy",
        credential_kind="deploy_token",
        secret_enc="encrypted",
        allowed_project_ids='["project1"]',
        read_only=True,
        status="active",
    ))
    db.add(Agent(id="agent1", name="Code Agent"))
    db.commit()


def _ready_settings(**overrides):
    values = dict(allowlist=[ORIGIN], trusted_digests=[IMAGE_DIGEST], errors=None)
    values.update(overrides)
    return _Settings(**values)


def _perturb(db, manifest, mode):
    """Mutate the baseline so a single readiness dimension fails."""
    if mode == "source_not_allowed":
        return db, manifest, _ready_settings(allowlist=["ssh://other.example:22"])
    if mode == "auth_failed":
        db.query(CodeDeployCredential).delete()
        db.commit()
        return db, manifest, _ready_settings()
    if mode == "ref_invalid":
        manifest.resolved_commit = "not-a-40-hex-sha"
        db.commit()
        return db, manifest, _ready_settings()
    if mode == "snapshot_invalid":
        db.query(CodeSourceSnapshot).delete()
        db.commit()
        return db, manifest, _ready_settings()
    if mode == "image_invalid":
        return db, manifest, _ready_settings(trusted_digests=["sha256:" + "d" * 64])
    if mode == "mount_invalid":
        return db, manifest, _ready_settings(
            errors={"workspace_api_root": "code_config_workspace_api_root_missing"}
        )
    raise AssertionError(mode)


@pytest.mark.parametrize(
    ("mode", "expected"),
    [
        ("source_not_allowed", "repository_source_not_allowed"),
        ("auth_failed", "repository_auth_failed"),
        ("ref_invalid", "repository_ref_invalid"),
        ("snapshot_invalid", "snapshot_invalid"),
        ("image_invalid", "image_digest_invalid"),
        ("mount_invalid", "workspace_mount_invalid"),
    ],
)
def test_secure_readiness_reason_distinguishes_each_stable_reason(mode, expected):
    db = _db()
    _project(db)
    manifest = _manifest(db)
    _seed_ready(db)
    db, manifest, settings = _perturb(db, manifest, mode)
    project = db.query(CodeProject).one()

    assert secure_readiness_reason(db, manifest, project=project, settings=settings) == expected


def test_ready_manifest_returns_empty_reason():
    db = _db()
    _project(db)
    manifest = _manifest(db)
    _seed_ready(db)
    project = db.query(CodeProject).one()

    assert secure_readiness_reason(
        db, manifest, project=project, settings=_ready_settings()
    ) == ""


def test_missing_source_type_or_locator_is_source_not_allowed():
    db = _db()
    _project(db)
    manifest = _manifest(db, source_type="", repository="")
    _seed_ready(db)
    project = db.query(CodeProject).one()

    assert secure_readiness_reason(
        db, manifest, project=project, settings=_ready_settings()
    ) == "repository_source_not_allowed"


@pytest.mark.parametrize(
    ("mode", "expected"),
    [
        ("source_not_allowed", "repository_source_not_allowed"),
        ("auth_failed", "repository_auth_failed"),
        ("ref_invalid", "repository_ref_invalid"),
        ("snapshot_invalid", "snapshot_invalid"),
        ("image_invalid", "image_digest_invalid"),
        ("mount_invalid", "workspace_mount_invalid"),
    ],
)
def test_unready_project_never_creates_a_code_run(monkeypatch, mode, expected):
    db = _db()
    _project(db)
    manifest = _manifest(db)
    _seed_ready(db)
    db, manifest, settings = _perturb(db, manifest, mode)
    monkeypatch.setattr(control_plane, "get_settings", lambda: settings)

    project = db.query(CodeProject).one()
    assert project_availability(db, project)["reason"] == expected
    assert project_availability(db, project)["ready"] is False

    agent = db.get(Agent, "agent1")
    actor = SimpleNamespace(username="admin", roles='["admin"]', organization_id="default")
    with pytest.raises(ManifestUnavailableError, match=expected):
        create_code_run(db, agent=agent, actor=actor, project_id=project.id, objective="Fix")
    assert db.query(CodeAgentRun).count() == 0
