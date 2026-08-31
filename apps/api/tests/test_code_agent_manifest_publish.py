"""Manifest publish tests for Git-config-only CodeAgent manifests."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import Settings
from app.database import Base
from app.models import (
    CodeControlAudit,
    CodeProject,
    CodeProjectManifest,
    CodeRepositorySource,
    CodeScanReport,
    CodeSourceSnapshot,
)
from app.security import now_str
from app.services.code_agent.manifest_publish import (
    ManifestPublishError,
    ManifestPublishService,
)


def _db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _settings(tmp_path: Path, approved_root: Path) -> Settings:
    data_root = tmp_path / "data"
    workspace_root = data_root / "workspaces"
    workspace_root.mkdir(parents=True)
    return Settings(
        data_dir=str(data_root),
        code_repository_allowlist="[]",
        code_repository_internal_cidrs="[]",
        code_local_repository_roots=json.dumps([str(approved_root)]),
        code_trusted_image_digests="[]",
        code_workspace_api_root=str(workspace_root),
        code_workspace_host_root="/srv/code-workspaces",
    )


def _rows(db, repository: Path):
    project = CodeProject(
        id="project1",
        name="Project",
        organization_id="default",
        creator="admin",
    )
    current = CodeProjectManifest(
        id="current1",
        project_id=project.id,
        version=1,
        status="published",
        source_id="old-source",
        source_type="local",
        requested_ref="HEAD",
        resolved_commit="a" * 40,
        snapshot_id="old-snapshot",
        snapshot_hash="b" * 64,
        source_scan_report_id="old-scan",
        repository=str(repository),
        base_commit="a" * 40,
        security_schema_version=1,
        published_at=now_str(),
        created_at=now_str(),
    )
    source = CodeRepositorySource(
        id="source2",
        project_id=project.id,
        source_type="local",
        locator=str(repository),
        credential_ref="",
        requested_ref="HEAD",
        status="draft",
        created_by="admin",
        created_at=now_str(),
        updated_at=now_str(),
    )
    draft = CodeProjectManifest(
        id="draft2",
        project_id=project.id,
        version=2,
        status="draft",
        source_id=source.id,
        source_type=source.source_type,
        credential_ref="",
        requested_ref="HEAD",
        repository=source.locator,
        base_commit="stale-base",
        resolved_commit="c" * 40,
        snapshot_id="stale-snapshot",
        snapshot_hash="d" * 64,
        source_scan_report_id="stale-scan",
        trusted_image="legacy/image:latest",
        image_digest="legacy/image@sha256:" + "e" * 64,
        allowed_paths='["legacy/**"]',
        validation_plan='[{"command":"pytest -q"}]',
        allowed_tools='["read","test"]',
        policy='{"network":false}',
        budgets='{"timeout_seconds":60}',
        security_schema_version=1,
        created_at=now_str(),
    )
    db.add_all([project, current, source, draft])
    db.commit()
    return project, current, source, draft


def _actor():
    return SimpleNamespace(username="admin", roles='["admin"]')


def test_publish_only_activates_git_config_and_clears_source_evidence(tmp_path):
    db = _db()
    repository = tmp_path / "approved" / "repository"
    repository.mkdir(parents=True)
    project, current, source, draft = _rows(db, repository)
    service = ManifestPublishService(
        db,
        settings=_settings(tmp_path, repository.parent),
    )

    published = service.publish(actor=_actor(), project=project, manifest=draft)

    assert published.status == "published"
    assert published.source_id == source.id
    assert published.source_type == "local"
    assert published.repository == str(repository)
    assert published.requested_ref == "HEAD"
    assert published.base_commit == ""
    assert published.resolved_commit == ""
    assert published.snapshot_id == ""
    assert published.snapshot_hash == ""
    assert published.source_scan_report_id == ""
    assert source.status == "active"
    assert current.status == "published"
    assert db.query(CodeScanReport).count() == 0
    assert db.query(CodeSourceSnapshot).count() == 0
    audit = db.query(CodeControlAudit).filter(CodeControlAudit.action == "manifest_publish").one()
    assert json.loads(audit.details)["publish_mode"] == "git_config_only"


def test_publish_does_not_inspect_repository_contents(tmp_path):
    db = _db()
    repository = tmp_path / "approved" / "repository"
    repository.mkdir(parents=True)
    (repository / "profiles.yml").write_text("password: abcdefghi\n", encoding="utf-8")
    project, _current, source, draft = _rows(db, repository)
    service = ManifestPublishService(
        db,
        settings=_settings(tmp_path, repository.parent),
    )

    published = service.publish(actor=_actor(), project=project, manifest=draft)

    assert published.status == "published"
    assert source.status == "active"
    assert published.source_scan_report_id == ""
    assert db.query(CodeScanReport).count() == 0


def test_publish_rejects_invalid_source_without_snapshot_side_effects(tmp_path):
    db = _db()
    approved = tmp_path / "approved"
    approved.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    project, current, source, draft = _rows(db, approved / "repository")
    source.locator = str(outside)
    draft.repository = source.locator
    db.commit()
    service = ManifestPublishService(db, settings=_settings(tmp_path, approved))

    with pytest.raises(ManifestPublishError) as captured:
        service.publish(actor=_actor(), project=project, manifest=draft)

    assert captured.value.reason == "repository_source_not_allowed"
    db.expire_all()
    assert db.get(CodeProjectManifest, current.id).status == "published"
    assert db.get(CodeProjectManifest, draft.id).status == "draft"
    assert db.get(CodeRepositorySource, source.id).status == "draft"
    assert db.query(CodeScanReport).count() == 0
    assert db.query(CodeSourceSnapshot).count() == 0


def test_publish_rejects_missing_credential_reference(tmp_path):
    db = _db()
    repository = tmp_path / "approved" / "repository"
    repository.mkdir(parents=True)
    project, _current, source, draft = _rows(db, repository)
    source.credential_ref = "missing-reference"
    draft.credential_ref = source.credential_ref
    db.commit()
    service = ManifestPublishService(
        db,
        settings=_settings(tmp_path, repository.parent),
    )

    with pytest.raises(ManifestPublishError) as captured:
        service.publish(actor=_actor(), project=project, manifest=draft)

    assert captured.value.reason == "repository_auth_failed"
    assert captured.value.field_errors == {"credential_ref": "repository_auth_failed"}
    assert source.status == "draft"
