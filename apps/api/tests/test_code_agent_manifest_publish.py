"""Manifest publish pipeline ordering, failure isolation and immutable freeze tests."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import Settings
from app.database import Base
from app.models import (
    CodeProject,
    CodeProjectManifest,
    CodeRepositorySource,
    CodeScanReport,
    CodeSourceSnapshot,
)
from app.security import now_str
from app.services.code_agent.git_importer import RestrictedGitImportError
from app.services.code_agent.manifest_publish import (
    DefaultSourceScanner,
    ImportedRepository,
    ManifestPublishError,
    ManifestPublishService,
)
from app.services.code_agent.snapshot_store import SnapshotSealError, SourceSnapshotStore


def _db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _repository(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "approved"
    repository = root / "repository"
    repository.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(repository)], check=True)
    subprocess.run(
        ["git", "-C", str(repository), "config", "user.name", "Test"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(repository), "config", "user.email", "test@example.test"],
        check=True,
    )
    (repository / "model.sql").write_text("select 1\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repository), "add", "model.sql"], check=True)
    subprocess.run(["git", "-C", str(repository), "commit", "-qm", "initial"], check=True)
    commit = subprocess.run(
        ["git", "-C", str(repository), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    return repository, Path(commit)


def _commit_secret(repository: Path) -> str:
    (repository / "profiles.yml").write_text(
        "password: abcdefghi\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "-C", str(repository), "add", "profiles.yml"], check=True)
    subprocess.run(["git", "-C", str(repository), "commit", "-qm", "secret fixture"], check=True)
    return subprocess.run(
        ["git", "-C", str(repository), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _commit_binary(repository: Path) -> str:
    (repository / "fixture.bin").write_bytes(b"\x00binary")
    subprocess.run(["git", "-C", str(repository), "add", "fixture.bin"], check=True)
    subprocess.run(["git", "-C", str(repository), "commit", "-qm", "binary fixture"], check=True)
    return subprocess.run(
        ["git", "-C", str(repository), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _settings(tmp_path: Path, approved_root: Path) -> Settings:
    data_root = tmp_path / "data"
    workspace_root = data_root / "workspaces"
    workspace_root.mkdir(parents=True)
    return Settings(
        data_dir=str(data_root),
        code_repository_allowlist="[]",
        code_repository_internal_cidrs="[]",
        code_local_repository_roots=json.dumps([str(approved_root)]),
        code_trusted_image_digests=json.dumps([
            "registry.test/code-runner@sha256:" + "c" * 64,
        ]),
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
        allowed_paths='["model.sql"]',
        validation_plan='[{"command":"pytest -q"}]',
        trusted_image="registry.test/code-runner@sha256:" + "c" * 64,
        image_digest="registry.test/code-runner@sha256:" + "c" * 64,
        security_schema_version=1,
        allowed_tools='["read","test"]',
        policy='{"network":false}',
        budgets='{"timeout_seconds":60}',
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
        base_commit="HEAD",
        allowed_paths='["model.sql"]',
        validation_plan='[{"command":"pytest -q"}]',
        trusted_image="registry.test/code-runner@sha256:" + "c" * 64,
        image_digest="registry.test/code-runner@sha256:" + "c" * 64,
        security_schema_version=1,
        allowed_tools='["read","test"]',
        policy='{"network":false}',
        budgets='{"timeout_seconds":60}',
        created_at=now_str(),
    )
    db.add_all([project, current, source, draft])
    db.commit()
    return project, current, source, draft


class RecordingAcquirer:
    def __init__(self, repository: Path, commit: str, events: list[str], failure=""):
        self.repository = repository
        self.commit = commit
        self.events = events
        self.failure = failure

    def acquire(self, **_kwargs):
        self.events.append("import")
        if self.failure:
            raise RestrictedGitImportError(self.failure)
        return ImportedRepository(
            repository_path=str(self.repository),
            resolved_commit=self.commit,
            size_bytes=(self.repository / "model.sql").stat().st_size,
            file_count=1,
            cleanup_paths=(),
        )


class RecordingScanner:
    def __init__(self, delegate, events: list[str], *, incomplete=False):
        self.delegate = delegate
        self.events = events
        self.incomplete = incomplete

    def scan(self, repository_path):
        self.events.append("scan")
        if not self.incomplete:
            return self.delegate.scan(repository_path)
        report = CodeScanReport(
            id="scan-failed",
            scope="source",
            input_hash="0" * 64,
            scanner="test",
            scanner_version="1",
            status="incomplete",
            complete=False,
            findings_count=0,
            findings="[]",
            failure_reason="source_scan_failed",
            created_at=now_str(),
        )
        self.delegate.db.add(report)
        self.delegate.db.commit()
        return report


class RecordingStore:
    def __init__(self, delegate, events: list[str], *, fail=False):
        self.delegate = delegate
        self.events = events
        self.fail = fail

    def seal(self, *args, **kwargs):
        self.events.append("seal")
        if self.fail:
            raise SnapshotSealError("snapshot_invalid")
        return self.delegate.seal(*args, **kwargs)

    def verify(self, *args, **kwargs):
        return self.delegate.verify(*args, **kwargs)

    def delete_verified(self, *args, **kwargs):
        return self.delegate.delete_verified(*args, **kwargs)


def _service(db, tmp_path, settings, repository, commit, events, *, stage=""):
    snapshot_root = tmp_path / "snapshots"
    snapshot_root.mkdir()
    store = RecordingStore(
        SourceSnapshotStore(snapshot_root),
        events,
        fail=stage == "seal",
    )
    return ManifestPublishService(
        db,
        settings=settings,
        acquirer=RecordingAcquirer(
            repository,
            commit,
            events,
            failure="repository_ref_invalid" if stage == "import" else "",
        ),
        scanner=RecordingScanner(
            DefaultSourceScanner(db),
            events,
            incomplete=stage == "scan",
        ),
        snapshot_store=store,
    )


def test_publish_orders_import_scan_seal_and_freezes_exact_snapshot(tmp_path):
    db = _db()
    repository, commit_path = _repository(tmp_path)
    commit = commit_path.name
    project, current, source, draft = _rows(db, repository)
    events: list[str] = []
    service = _service(
        db,
        tmp_path,
        _settings(tmp_path, repository.parent),
        repository,
        commit,
        events,
    )

    published = service.publish(
        actor=SimpleNamespace(username="admin", roles='["admin"]'),
        project=project,
        manifest=draft,
    )

    assert events == ["import", "scan", "seal"]
    assert published.status == "published"
    assert published.resolved_commit == commit
    assert published.base_commit == commit
    assert published.snapshot_id
    assert published.snapshot_hash
    assert published.source_scan_report_id
    assert published.trusted_image == "registry.test/code-runner"
    assert published.image_digest == "registry.test/code-runner@sha256:" + "c" * 64
    assert source.status == "active"
    assert current.status == "published"
    snapshot = db.get(CodeSourceSnapshot, published.snapshot_id)
    assert snapshot.status == "sealed"
    assert snapshot.ref_count == 1
    assert snapshot.resolved_commit == commit


def test_source_secret_findings_block_publish_by_default(tmp_path):
    db = _db()
    repository, _commit_path = _repository(tmp_path)
    commit = _commit_secret(repository)
    project, current, source, draft = _rows(db, repository)
    events: list[str] = []
    service = _service(
        db,
        tmp_path,
        _settings(tmp_path, repository.parent),
        repository,
        commit,
        events,
    )

    with pytest.raises(ManifestPublishError, match="secret_detected"):
        service.publish(
            actor=SimpleNamespace(username="admin", roles='["admin"]'),
            project=project,
            manifest=draft,
        )

    assert events == ["import", "scan"]
    assert db.get(CodeProjectManifest, current.id).status == "published"
    assert db.get(CodeProjectManifest, draft.id).status == "draft"
    assert not db.query(CodeSourceSnapshot).filter(
        CodeSourceSnapshot.ref_count > 0
    ).count()


def test_source_secret_findings_publish_as_warning_when_policy_allows(tmp_path):
    db = _db()
    repository, _commit_path = _repository(tmp_path)
    commit = _commit_secret(repository)
    project, _current, source, draft = _rows(db, repository)
    draft.policy = json.dumps({
        "network": False,
        "secret_policy": {
            "source": "warn",
            "patch": "block",
            "output": "redact",
        },
    }, sort_keys=True)
    db.commit()
    events: list[str] = []
    service = _service(
        db,
        tmp_path,
        _settings(tmp_path, repository.parent),
        repository,
        commit,
        events,
    )

    published = service.publish(
        actor=SimpleNamespace(username="admin", roles='["admin"]'),
        project=project,
        manifest=draft,
    )
    report = db.get(CodeScanReport, published.source_scan_report_id)

    assert events == ["import", "scan", "seal"]
    assert published.status == "published"
    assert source.status == "active"
    assert report.status == "complete"
    assert report.complete is True
    assert report.findings_count == 1
    assert report.failure_reason == "secret_detected"


def test_source_unscannable_files_block_publish_by_default(tmp_path):
    db = _db()
    repository, _commit_path = _repository(tmp_path)
    commit = _commit_binary(repository)
    project, current, source, draft = _rows(db, repository)
    events: list[str] = []
    service = _service(
        db,
        tmp_path,
        _settings(tmp_path, repository.parent),
        repository,
        commit,
        events,
    )

    with pytest.raises(ManifestPublishError, match="source_scan_failed"):
        service.publish(
            actor=SimpleNamespace(username="admin", roles='["admin"]'),
            project=project,
            manifest=draft,
        )

    assert events == ["import", "scan"]
    assert db.get(CodeProjectManifest, current.id).status == "published"
    assert db.get(CodeProjectManifest, draft.id).status == "draft"
    assert source.status == "draft"


def test_source_unscannable_files_publish_as_warning_when_policy_allows(tmp_path):
    db = _db()
    repository, _commit_path = _repository(tmp_path)
    commit = _commit_binary(repository)
    project, _current, source, draft = _rows(db, repository)
    draft.policy = json.dumps({
        "network": False,
        "secret_policy": {
            "source": "warn",
            "source_unscannable": "warn",
            "patch": "block",
            "output": "redact",
        },
    }, sort_keys=True)
    db.commit()
    events: list[str] = []
    service = _service(
        db,
        tmp_path,
        _settings(tmp_path, repository.parent),
        repository,
        commit,
        events,
    )

    published = service.publish(
        actor=SimpleNamespace(username="admin", roles='["admin"]'),
        project=project,
        manifest=draft,
    )
    report = db.get(CodeScanReport, published.source_scan_report_id)
    findings = json.loads(report.findings)

    assert events == ["import", "scan", "seal"]
    assert published.status == "published"
    assert source.status == "active"
    assert report.status == "incomplete"
    assert report.complete is False
    assert report.findings_count == 0
    assert report.skipped_count == 1
    assert report.failure_reason == "scanner_binary_unsupported"
    assert findings == [{
        "classification": "scanner_binary_unsupported",
        "path": "fixture.bin",
    }]


def test_draft_change_during_import_rejects_stale_result_and_orphans_snapshot(tmp_path):
    db = _db()
    repository, commit_path = _repository(tmp_path)
    project, current, source, draft = _rows(db, repository)
    events: list[str] = []
    service = _service(
        db,
        tmp_path,
        _settings(tmp_path, repository.parent),
        repository,
        commit_path.name,
        events,
    )
    original_seal = service.snapshot_store.seal

    def seal_then_edit(*args, **kwargs):
        sealed = original_seal(*args, **kwargs)
        source.requested_ref = "new-ref"
        draft.requested_ref = "new-ref"
        db.commit()
        return sealed

    service.snapshot_store.seal = seal_then_edit

    with pytest.raises(ManifestPublishError, match="manifest_publish_conflict"):
        service.publish(
            actor=SimpleNamespace(username="admin", roles='["admin"]'),
            project=project,
            manifest=draft,
        )

    db.expire_all()
    assert db.get(CodeProjectManifest, current.id).status == "published"
    assert db.get(CodeProjectManifest, draft.id).status == "draft"
    assert not db.get(CodeProjectManifest, draft.id).snapshot_id
    orphan = db.query(CodeSourceSnapshot).one()
    assert orphan.status == "failed"
    assert orphan.ref_count == 0


@pytest.mark.parametrize(
    ("stage", "reason", "expected_events"),
    [
        ("permission", "code_project_not_found", []),
        ("source", "repository_source_not_allowed", []),
        ("image", "image_digest_invalid", []),
        ("auth", "repository_auth_failed", []),
        ("import", "repository_ref_invalid", ["import"]),
        ("scan", "source_scan_failed", ["import", "scan"]),
        ("seal", "snapshot_invalid", ["import", "scan", "seal"]),
    ],
)
def test_each_pretransaction_publish_failure_preserves_old_version_and_no_runnable_snapshot(
    tmp_path,
    stage,
    reason,
    expected_events,
):
    stage_root = tmp_path / stage
    stage_root.mkdir()
    db = _db()
    repository, commit_path = _repository(stage_root)
    project, current, source, draft = _rows(db, repository)
    settings = _settings(stage_root, repository.parent)
    if stage == "source":
        source.locator = str(stage_root / "outside")
        draft.repository = source.locator
    elif stage == "image":
        draft.image_digest = "registry.test/unapproved@sha256:" + "d" * 64
    elif stage == "auth":
        source.credential_ref = "missing-reference"
        draft.credential_ref = source.credential_ref
    db.commit()
    events: list[str] = []
    service = _service(
        db,
        stage_root,
        settings,
        repository,
        commit_path.name,
        events,
        stage=stage,
    )

    with pytest.raises(ManifestPublishError) as captured:
        service.publish(
            actor=(
                SimpleNamespace(username="mallory", roles='["owner"]')
                if stage == "permission"
                else SimpleNamespace(username="admin", roles='["admin"]')
            ),
            project=project,
            manifest=draft,
        )

    assert captured.value.reason == reason
    assert events == expected_events
    db.expire_all()
    assert db.get(CodeProjectManifest, current.id).status == "published"
    failed_draft = db.get(CodeProjectManifest, draft.id)
    assert failed_draft.status == "draft"
    assert not failed_draft.snapshot_id
    assert not db.query(CodeSourceSnapshot).filter(
        CodeSourceSnapshot.ref_count > 0
    ).count()
    assert db.get(CodeRepositorySource, source.id).status == "draft"
