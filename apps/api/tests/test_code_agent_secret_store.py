"""OpenSpec task 2.2: reference-only Deploy Token Secret adapter."""

from __future__ import annotations

import json
import logging
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import (
    CodeAgentRun,
    CodeControlAudit,
    CodeDeployCredential,
    CodeProject,
    CodeProjectManifest,
    CodeRepositorySource,
)
from app.services.code_agent.authorization import CodeAuthorizationError
from app.services.code_agent.secret_store import (
    DeployTokenLease,
    DeployTokenSecretStore,
    SecretReferenceError,
    repository_importer_identity,
)


SECRET_VALUE = "deploy-token-very-sensitive-1234567890"


def _db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _user(username: str, role: str, organization_id: str = "org-a"):
    return SimpleNamespace(
        username=username,
        roles=json.dumps([role]),
        organization_id=organization_id,
    )


def _project(db, *, project_id="project-a", organization_id="org-a"):
    project = CodeProject(
        id=project_id,
        name=project_id,
        organization_id=organization_id,
        creator="owner",
        allowed_users='["operator"]',
    )
    db.add(project)
    db.commit()
    return project


def _assign(db, *, project_id="project-a", organization_id="org-a"):
    return DeployTokenSecretStore(db).assign(
        actor=_user("admin", "admin", "platform"),
        organization_id=organization_id,
        label="read-only test repository",
        value=SECRET_VALUE,
        allowed_project_ids=[project_id],
        auth_username="deploy-user",
        reference_id="credential-a",
    )


def test_secret_store_persists_ciphertext_and_returns_metadata_reference_only():
    db = _db()
    project = _project(db)
    metadata = _assign(db)
    row = db.get(CodeDeployCredential, metadata.reference_id)

    assert row.secret_enc != SECRET_VALUE
    assert SECRET_VALUE not in row.secret_enc
    assert metadata.to_dict() == {
        "reference_id": "credential-a",
        "organization_id": "org-a",
        "label": "read-only test repository",
        "credential_kind": "deploy_token",
        "auth_username": "deploy-user",
        "allowed_project_ids": ["project-a"],
        "read_only": True,
        "status": "active",
    }
    assert SECRET_VALUE not in json.dumps(metadata.to_dict(), sort_keys=True)

    visible = DeployTokenSecretStore(db).project_metadata(
        actor=_user("owner", "owner"),
        project=project,
    )
    assert [item.reference_id for item in visible] == ["credential-a"]
    with pytest.raises(CodeAuthorizationError):
        DeployTokenSecretStore(db).project_metadata(
            actor=_user("operator", "operator"),
            project=project,
        )


def test_secret_reference_assignment_is_organization_and_project_scoped():
    db = _db()
    project_a = _project(db)
    project_b = _project(db, project_id="project-b")
    project_other_org = _project(
        db,
        project_id="project-other-org",
        organization_id="org-b",
    )
    _assign(db)
    store = DeployTokenSecretStore(db)

    assert len(store.project_metadata(
        actor=_user("owner", "owner"), project=project_a
    )) == 1
    assert store.project_metadata(
        actor=_user("owner", "owner"), project=project_b
    ) == []
    with pytest.raises(CodeAuthorizationError, match="code_project_not_found"):
        store.project_metadata(
            actor=_user("owner", "owner", "org-a"),
            project=project_other_org,
        )
    with pytest.raises(SecretReferenceError, match="repository_auth_failed"):
        DeployTokenSecretStore(db).assign(
            actor=_user("admin", "admin", "platform"),
            organization_id="org-a",
            label="invalid cross-organization assignment",
            value=SECRET_VALUE,
            allowed_project_ids=[project_other_org.id],
        )


def test_only_importer_identity_can_resolve_and_lease_is_revoked_after_use():
    db = _db()
    _project(db)
    _assign(db)
    store = DeployTokenSecretStore(db)

    with pytest.raises(SecretReferenceError, match="repository_auth_failed"):
        with store.resolve_for_importer(
            identity=SimpleNamespace(name="repository_importer"),
            reference_id="credential-a",
            organization_id="org-a",
            project_id="project-a",
        ):
            pass

    with store.resolve_for_importer(
        identity=repository_importer_identity(),
        reference_id="credential-a",
        organization_id="org-a",
        project_id="project-a",
    ) as lease:
        assert lease.read().decode() == SECRET_VALUE
        assert SECRET_VALUE not in repr(lease)

    assert lease.closed is True
    assert lease.cleared is True
    with pytest.raises(SecretReferenceError, match="repository_auth_failed"):
        lease.read()


def test_lease_is_cleared_even_when_resolve_audit_commit_fails(monkeypatch):
    db = _db()
    _project(db)
    _assign(db)
    store = DeployTokenSecretStore(db)
    original_commit = db.commit
    closed: list[DeployTokenLease] = []
    original_close = DeployTokenLease.close

    def track_close(lease):
        original_close(lease)
        closed.append(lease)

    def fail_commit():
        raise RuntimeError("audit storage unavailable")

    monkeypatch.setattr(DeployTokenLease, "close", track_close)
    monkeypatch.setattr(db, "commit", fail_commit)
    with pytest.raises(RuntimeError, match="audit storage unavailable"):
        with store.resolve_for_importer(
            identity=repository_importer_identity(),
            reference_id="credential-a",
            organization_id="org-a",
            project_id="project-a",
        ):
            pass
    monkeypatch.setattr(db, "commit", original_commit)

    assert len(closed) == 1
    assert closed[0].closed is True
    assert closed[0].cleared is True


@pytest.mark.parametrize("failure", ["missing", "disabled", "wrong_scope", "corrupt"])
def test_secret_resolution_failures_are_stable_and_never_log_secret(caplog, failure):
    db = _db()
    _project(db)
    _assign(db)
    store = DeployTokenSecretStore(db)
    reference_id = "credential-a"
    organization_id = "org-a"
    project_id = "project-a"
    if failure == "missing":
        reference_id = "missing-reference"
    elif failure == "disabled":
        store.disable(
            actor=_user("admin", "admin", "platform"),
            reference_id=reference_id,
        )
    elif failure == "wrong_scope":
        project_id = "project-b"
    else:
        row = db.get(CodeDeployCredential, reference_id)
        row.secret_enc = "not-valid-fernet-ciphertext"
        db.commit()

    caplog.set_level(logging.WARNING)
    with pytest.raises(SecretReferenceError) as rejected:
        with store.resolve_for_importer(
            identity=repository_importer_identity(),
            reference_id=reference_id,
            organization_id=organization_id,
            project_id=project_id,
        ):
            pass

    assert rejected.value.reason == "repository_auth_failed"
    assert str(rejected.value) == "repository_auth_failed"
    assert SECRET_VALUE not in caplog.text
    assert SECRET_VALUE not in repr(rejected.value)


def test_secret_never_reaches_source_manifest_run_audit_runner_facts_or_workspace(tmp_path):
    db = _db()
    project = _project(db)
    metadata = _assign(db)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "README.md").write_text("safe source\n", encoding="utf-8")
    db.add_all([
        CodeRepositorySource(
            id="source-a",
            project_id=project.id,
            source_type="https",
            locator="https://git.example.test/repo.git",
            credential_ref=metadata.reference_id,
            status="active",
        ),
        CodeProjectManifest(
            id="manifest-a",
            project_id=project.id,
            version=1,
            credential_ref=metadata.reference_id,
            status="draft",
        ),
        CodeAgentRun(
            id="run-a",
            agent_id="agent-a",
            project_id=project.id,
            manifest_id="manifest-a",
            manifest_version=1,
            workspace_path=str(workspace),
            runner_facts=json.dumps({
                "network_mode": "none",
                "mounts": [{"destination": "/workspace"}],
                "environment": [],
            }),
        ),
    ])
    db.commit()

    with DeployTokenSecretStore(db).resolve_for_importer(
        identity=repository_importer_identity(),
        reference_id=metadata.reference_id,
        organization_id=project.organization_id,
        project_id=project.id,
    ) as lease:
        assert lease.read().decode() == SECRET_VALUE

    source = db.get(CodeRepositorySource, "source-a")
    manifest = db.get(CodeProjectManifest, "manifest-a")
    run = db.get(CodeAgentRun, "run-a")
    audits = db.query(CodeControlAudit).all()
    persisted_surfaces = [
        source.locator,
        source.credential_ref,
        manifest.credential_ref,
        manifest.repository,
        manifest.policy,
        run.task_contract,
        run.runner_facts,
        run.source_facts,
        *(audit.details for audit in audits),
    ]
    workspace_content = "".join(
        path.read_text(encoding="utf-8") for path in workspace.rglob("*") if path.is_file()
    )

    assert all(SECRET_VALUE not in value for value in persisted_surfaces)
    assert SECRET_VALUE not in workspace_content
    assert SECRET_VALUE not in json.dumps(json.loads(run.runner_facts), sort_keys=True)


def test_deploy_credential_model_rejects_write_capable_records():
    db = _db()
    db.add(CodeDeployCredential(
        id="write-token",
        organization_id="org-a",
        label="must fail",
        credential_kind="deploy_token",
        secret_enc="ciphertext",
        allowed_project_ids='["project-a"]',
        read_only=False,
        status="active",
    ))
    with pytest.raises(IntegrityError):
        db.commit()
