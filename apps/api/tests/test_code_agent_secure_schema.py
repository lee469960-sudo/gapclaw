"""Schema contract for secure CodeAgent repository sources and snapshots."""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app.models import (
    CodeDeployCredential,
    CodeProject,
    CodeRepositorySource,
    CodeScanReport,
    CodeSourceSnapshot,
)
from app.services.code_agent.schema_migration import (
    downgrade_secure_workspace_schema,
    upgrade_secure_workspace_schema,
)


SECURE_TABLES = {
    "code_deploy_credentials",
    "code_repository_sources",
    "code_scan_reports",
    "code_source_snapshots",
}


def _engine_with_project_table():
    engine = create_engine("sqlite:///:memory:")
    CodeProject.__table__.create(bind=engine)
    return engine


def test_secure_workspace_schema_upgrade_and_downgrade_are_reversible():
    engine = _engine_with_project_table()

    upgrade_secure_workspace_schema(engine)
    assert SECURE_TABLES <= set(inspect(engine).get_table_names())

    downgrade_secure_workspace_schema(engine)
    assert SECURE_TABLES.isdisjoint(inspect(engine).get_table_names())

    upgrade_secure_workspace_schema(engine)
    assert SECURE_TABLES <= set(inspect(engine).get_table_names())


def test_secure_workspace_schema_persists_references_not_secret_values():
    engine = _engine_with_project_table()
    upgrade_secure_workspace_schema(engine)
    columns = {
        table: {column["name"] for column in inspect(engine).get_columns(table)}
        for table in SECURE_TABLES
    }

    assert "credential_ref" in columns["code_repository_sources"]
    assert "secret_enc" in columns["code_deploy_credentials"]
    forbidden = {
        "credential_value",
        "deploy_token",
        "password",
        "private_key",
        "secret",
        "secret_value",
        "token",
    }
    assert all(forbidden.isdisjoint(names) for names in columns.values())
    assert "scan_report_id" in columns["code_source_snapshots"]
    assert {
        "cleanup_attempts", "cleanup_error", "cleanup_next_attempt",
    } <= columns["code_source_snapshots"]


def test_secure_workspace_model_lifecycle_constraints_accept_valid_records():
    engine = _engine_with_project_table()
    upgrade_secure_workspace_schema(engine)
    db = sessionmaker(bind=engine)()
    try:
        db.add(CodeProject(id="project1", name="Project"))
        db.add(CodeScanReport(
            id="scan1",
            scope="source",
            input_hash="a" * 64,
            scanner="test-scanner",
            scanner_version="1",
            status="complete",
            complete=True,
        ))
        db.add(CodeRepositorySource(
            id="source1",
            project_id="project1",
            source_type="https",
            locator="https://git.example.test/repo.git",
            credential_ref="deploy-token-ref",
            requested_ref="main",
            status="active",
        ))
        db.flush()
        db.add(CodeSourceSnapshot(
            id="snapshot1",
            source_id="source1",
            resolved_commit="b" * 40,
            content_hash="c" * 64,
            storage_path="/snapshots/cc/" + "c" * 64,
            scan_report_id="scan1",
            importer_version="1",
            policy_hash="d" * 64,
            status="sealed",
            ref_count=1,
        ))
        db.commit()

        snapshot = db.get(CodeSourceSnapshot, "snapshot1")
        assert snapshot is not None
        assert snapshot.status == "sealed"
        assert snapshot.scan_report_id == "scan1"
    finally:
        db.close()


@pytest.mark.parametrize(
    ("model", "values"),
    [
        (CodeRepositorySource, {
            "id": "source2",
            "project_id": "project1",
            "source_type": "file",
            "locator": "/tmp/repo",
            "status": "active",
        }),
        (CodeSourceSnapshot, {
            "id": "snapshot2",
            "source_id": "source1",
            "resolved_commit": "b" * 40,
            "content_hash": "e" * 64,
            "storage_path": "/snapshots/ee/" + "e" * 64,
            "scan_report_id": "scan1",
            "importer_version": "1",
            "policy_hash": "d" * 64,
            "status": "writable",
        }),
    ],
)
def test_secure_workspace_model_lifecycle_constraints_reject_invalid_values(model, values):
    engine = _engine_with_project_table()
    upgrade_secure_workspace_schema(engine)
    db = sessionmaker(bind=engine)()
    try:
        db.add(CodeProject(id="project1", name="Project"))
        db.add(CodeScanReport(
            id="scan1", scope="source", input_hash="a" * 64,
            scanner="scanner", scanner_version="1",
        ))
        db.add(CodeRepositorySource(
            id="source1", project_id="project1", source_type="https",
            locator="https://git.example.test/repo.git",
        ))
        db.commit()
        db.add(model(**values))
        with pytest.raises(IntegrityError):
            db.commit()
    finally:
        db.rollback()
        db.close()
