"""OpenSpec task 7.2: source scans gate every writable Code runtime."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import Agent, CodeAgentRun, CodeScanReport, CodeSourceSnapshot
from app.services.agent_runtime.runtime import run_agent
from app.services.code_agent.scanner import (
    BuiltinSecretScanner,
    SourceScanValidationError,
    validate_source_scan_report,
)


def _db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _subject(
    tmp_path,
    *,
    findings=None,
    complete=True,
    status="complete",
    binary=False,
    effective_policy="{}",
):
    db = _db()
    source = tmp_path / "sealed-source"
    source.mkdir()
    finding_rows = list(findings or [])
    if binary:
        (source / "app.py").write_bytes(b"\x00binary")
    else:
        content = "password: abcdefghi\n" if finding_rows else "value = 1\n"
        (source / "app.py").write_text(content, encoding="utf-8")
    scanned = BuiltinSecretScanner(scope="source").scan(source)
    content_hash = "a" * 64
    commit = "b" * 40
    db.add(Agent(
        id="code", name="Code", profile="code", allowed_actions="[]",
        skills="[]", mcps="[]", rags="[]", httpmcps="[]",
    ))
    db.add(CodeScanReport(
        id="scan1", scope="source", input_hash=scanned.input_hash,
        scanner=scanned.scanner, scanner_version=scanned.scanner_version,
        status=status, complete=complete, findings_count=len(finding_rows),
        files_discovered=1, files_scanned=1 if complete else 0,
        bytes_discovered=10, bytes_scanned=10 if complete else 0,
        skipped_count=0, truncated_count=0 if complete else 1,
        findings=json.dumps(finding_rows),
        failure_reason="" if complete else "scanner_crashed",
    ))
    db.add(CodeSourceSnapshot(
        id=content_hash[:16], source_id="source1", resolved_commit=commit,
        content_hash=content_hash, storage_path=str(source), scan_report_id="scan1",
        importer_version="test", policy_hash="policy", status="sealed",
    ))
    db.add(CodeAgentRun(
        id="run1", agent_id="code", project_id="project1", manifest_id="manifest1",
        manifest_version=1, source_id="source1", resolved_commit=commit,
        snapshot_id=content_hash[:16], snapshot_hash=content_hash,
        source_scan_report_id="scan1", status="pending",
        task_contract="{}", effective_policy=effective_policy,
    ))
    db.commit()
    return db, db.get(Agent, "code"), db.get(CodeAgentRun, "run1")


def test_valid_sealed_source_report_matches_current_snapshot_input(tmp_path):
    db, _agent, run = _subject(tmp_path)

    report = validate_source_scan_report(db, run)

    assert report.id == "scan1"
    assert run.status == "pending"


def test_source_secret_findings_are_allowed_when_effective_policy_warns(tmp_path):
    db, _agent, run = _subject(
        tmp_path,
        findings=[{"path": "app.py", "classification": "secret_pattern"}],
        effective_policy=json.dumps({
            "secret_policy": {
                "source": "warn",
                "patch": "block",
                "output": "redact",
            },
        }, sort_keys=True),
    )

    report = validate_source_scan_report(db, run)

    assert report.id == "scan1"
    assert report.findings_count == 1
    assert run.status == "pending"


def test_source_unscannable_findings_are_allowed_when_effective_policy_warns(tmp_path):
    db, _agent, run = _subject(
        tmp_path,
        findings=[{"path": "app.py", "classification": "scanner_binary_unsupported"}],
        complete=False,
        status="incomplete",
        binary=True,
        effective_policy=json.dumps({
            "secret_policy": {
                "source": "warn",
                "source_unscannable": "warn",
                "patch": "block",
                "output": "redact",
            },
        }, sort_keys=True),
    )
    report_row = db.get(CodeScanReport, "scan1")
    report_row.findings_count = 0
    report_row.skipped_count = 1
    report_row.truncated_count = 0
    report_row.failure_reason = "scanner_binary_unsupported"
    db.commit()

    report = validate_source_scan_report(db, run)

    assert report.id == "scan1"
    assert report.findings_count == 0
    assert report.skipped_count == 1
    assert run.status == "pending"


@pytest.mark.parametrize(
    ("findings", "complete", "status", "reason"),
    [
        ([{"path": "app.py", "classification": "secret_pattern"}], True, "complete", "secret_detected"),
        ([], False, "failed", "source_scan_failed"),
    ],
)
def test_secret_hit_or_scanner_failure_never_starts_workspace_or_runner(
    tmp_path, findings, complete, status, reason
):
    db, agent, run = _subject(
        tmp_path, findings=findings, complete=complete, status=status
    )
    workspace = MagicMock()
    runner = MagicMock()
    protected = "sk-example0123456789abcdef"

    with patch(
        "app.services.agent_runtime.runtime.AgentRuntime.run",
        new=AsyncMock(return_value=protected),
    ) as execute:
        with pytest.raises(SourceScanValidationError, match=reason) as failure:
            asyncio.run(run_agent(
                db, agent, "s1", "fix it", code_run_id="run1",
                workspace_manager=workspace, code_runner=runner,
            ))

    workspace.prepare.assert_not_called()
    runner.start.assert_not_called()
    execute.assert_not_awaited()
    assert run.status == "policy_rejected"
    assert run.failure_reason == reason
    assert protected not in str(failure.value)
