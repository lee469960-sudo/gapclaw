"""OpenSpec task 5.3: atomic canonical Code artifact sealing."""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import (
    CodeAgentRun,
    CodeArtifact,
    CodeArtifactReview,
    CodeProject,
    CodeScanReport,
    CodeSourceSnapshot,
)
from app.services.code_agent.artifacts import CodeArtifactSealer
from app.services.code_agent.output_security import scan_changed_content
from app.services.code_agent.scanner import (
    BuiltinSecretScanner,
    ScanReport,
    persist_scan_report,
)
from app.services.code_agent.review import (
    ArtifactReviewError,
    accept_sealed_artifact,
    artifact_file,
    load_verified_bundle,
    review_payload,
)
from app.services.code_agent.snapshot_store import SourceSnapshotStore
from app.services.code_agent.workspace import (
    WorkspaceIntegrityGuard,
    WorkspaceManager,
    workspace_changed_paths,
)


def _git(repo, *args):
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    ).stdout.strip()


def _subject(tmp_path, *, base_content="value = 1\n", single_top_level=False, claude_runtime=False):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    source_root = repo / "gamestat" if single_top_level else repo
    (source_root / "src").mkdir(parents=True)
    (source_root / "src" / "app.py").write_text(base_content, encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "base")
    commit = _git(repo, "rev-parse", "HEAD")
    snapshots = tmp_path / "snapshots"
    snapshots.mkdir()
    store = SourceSnapshotStore(snapshots)
    sealed = store.seal(repo, resolved_commit=commit)
    source_scan = persist_scan_report(
        db, BuiltinSecretScanner(scope="source").scan(sealed.storage_path)
    )
    policy = {"allowed_paths": ["src/"]}
    if claude_runtime:
        policy["coding_runtime"] = "claude_code"
    policy_json = json.dumps(policy, sort_keys=True, separators=(",", ":"))
    policy_hash = hashlib.sha256(policy_json.encode()).hexdigest()
    db.add(CodeSourceSnapshot(
        id=sealed.snapshot_id, source_id="source1", resolved_commit=commit,
        content_hash=sealed.content_hash, storage_path=sealed.storage_path,
        scan_report_id=source_scan.id, importer_version="test",
        policy_hash=policy_hash, status="sealed",
    ))
    run = CodeAgentRun(
        id="run1", agent_id="agent1", project_id="project1", manifest_id="manifest1",
        manifest_version=7, repository=str(repo), base_commit=commit,
        source_id="source1", source_scan_report_id=source_scan.id,
        resolved_commit=commit, snapshot_id=sealed.snapshot_id, snapshot_hash=sealed.content_hash,
        image="internal/code@sha256:abc", image_digest="sha256:abc",
        effective_policy=policy_json, effective_policy_hash=policy_hash,
        task_contract=json.dumps({
            "coding_runtime": "claude_code" if claude_runtime else "legacy",
            "validation_plan": [{"command": "pytest -q"}],
        }),
        runner_facts=json.dumps({"image_id": "sha256:abc"}), container_id="container1",
        verifier_report=json.dumps({"passed": True, "outcome": "verification_passed"}),
        status="running", workspace_state="", workspace_downloadable=False,
    )
    db.add(CodeProject(
        id="project1",
        name="Project",
        creator="owner",
        allowed_users='["reviewer"]',
    ))
    db.add(run)
    db.commit()
    WorkspaceManager(tmp_path / "runs").prepare(run, snapshot_store=store)
    _mark_verified(run)
    db.commit()
    runner = SimpleNamespace(freeze=MagicMock())
    return db, run, runner


def _edit(run, relative, content):
    WorkspaceIntegrityGuard(run).check_before_write(relative)
    target = Path(run.workspace_path) / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    _mark_verified(run)


def _mark_verified(run, *, allow_detected=False):
    changed = workspace_changed_paths(run)
    scanned = scan_changed_content(run.workspace_path, changed)
    assert scanned.complete
    if not allow_detected:
        assert not scanned.detected
    run.verifier_report = json.dumps({
        "passed": True,
        "outcome": "verification_passed",
        "changed_paths": list(changed),
        "checks": ["secret_detection_complete"],
        "secret_scan_complete": True,
        "content_hash": scanned.content_hash,
    })


def _set_secret_policy(run, *, source: str, source_unscannable: str, patch: str = "block"):
    policy = json.loads(run.effective_policy)
    policy["secret_policy"] = {
        "source": source,
        "source_unscannable": source_unscannable,
        "patch": patch,
        "output": "redact",
    }
    run.effective_policy = json.dumps(policy, sort_keys=True, separators=(",", ":"))
    run.effective_policy_hash = hashlib.sha256(run.effective_policy.encode()).hexdigest()


def _mark_source_scan_incomplete_with_findings(db, run):
    scan = db.get(CodeScanReport, run.source_scan_report_id)
    scan.status = "incomplete"
    scan.complete = False
    scan.failure_reason = "scanner_binary_unsupported"
    scan.findings_count = 1
    scan.findings = json.dumps(
        [
            {"classification": "scanner_binary_unsupported", "path": ".DS_Store"},
            {"classification": "secret_pattern", "path": "profiles.yml"},
        ],
        sort_keys=True,
    )
    db.commit()


def test_verified_workspace_is_frozen_and_atomically_sealed_as_canonical_artifacts(tmp_path):
    db, run, runner = _subject(tmp_path)
    _edit(run, "src/app.py", "value = 2\n")
    _edit(run, "src/new.py", "created = True\n")
    artifact_root = tmp_path / "artifacts"

    result = CodeArtifactSealer(db, run, runner, root=artifact_root).seal()

    assert result.sealed is True
    assert result.outcome == "patch_ready"
    runner.freeze.assert_called_once_with("container1")
    artifact = db.query(CodeArtifact).filter(CodeArtifact.run_id == "run1").one()
    sealed = artifact_root / "run1"
    assert sorted(path.name for path in sealed.iterdir()) == [
        "manifest.json", "patch-scan-report.json", "patch.diff", "policy.json",
        "source-scan-report.json", "verifier-report.json",
    ]
    patch = (sealed / "patch.diff").read_text(encoding="utf-8")
    assert "a/src/app.py" in patch and "b/src/app.py" in patch
    assert "a/src/new.py" in patch or "b/src/new.py" in patch
    assert "a/base/" not in patch and "b/workspace/" not in patch
    assert artifact.base_commit == run.base_commit
    assert artifact.diff_hash == hashlib.sha256(patch.encode()).hexdigest()
    manifest = json.loads((sealed / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["diff_hash"] == artifact.diff_hash
    assert manifest["image_id"] == "sha256:abc"
    assert manifest["image_digest"] == "sha256:abc"
    assert manifest["snapshot_id"] == run.snapshot_id
    assert manifest["snapshot_hash"] == run.snapshot_hash
    assert manifest["resolved_commit"] == run.resolved_commit
    assert len(manifest["source_scan_report_hash"]) == 64
    assert len(manifest["patch_scan_report_hash"]) == 64
    assert run.artifact_id == artifact.id
    assert run.workspace_state == "sealed"
    assert run.workspace_downloadable is False


def test_runtime_only_claude_files_are_excluded_from_changed_paths_and_sealed_patch(tmp_path):
    db, run, runner = _subject(tmp_path)
    _edit(run, "src/app.py", "value = 2\n")
    runtime_dir = Path(run.workspace_path) / ".claude"
    runtime_dir.mkdir()
    (runtime_dir / "mcp.json").write_text('{"secret":"runtime-only"}', encoding="utf-8")
    (runtime_dir / "skills").mkdir()
    (runtime_dir / "skills" / "dbt").mkdir()
    (runtime_dir / "skills" / "dbt" / "SKILL.md").write_text("# Skill\n", encoding="utf-8")
    _mark_verified(run)
    artifact_root = tmp_path / "artifacts"

    assert workspace_changed_paths(run) == ("src/app.py",)
    result = CodeArtifactSealer(db, run, runner, root=artifact_root).seal()

    assert result.sealed is True
    patch = (artifact_root / "run1" / "patch.diff").read_text(encoding="utf-8")
    assert ".claude" not in patch
    assert "runtime-only" not in patch
    assert "src/app.py" in patch


def test_flattened_claude_workspace_does_not_leak_root_rewrite_into_sealed_patch(tmp_path):
    db, run, runner = _subject(tmp_path, single_top_level=True, claude_runtime=True)
    _edit(run, "src/app.py", "value = 2\n")
    (Path(run.workspace_path) / ".git" / "runtime-state").write_text("ignored\n", encoding="utf-8")
    runtime_dir = Path(run.workspace_path) / ".claude"
    runtime_dir.mkdir()
    (runtime_dir / "trace.json").write_text('{"token":"runtime-only"}', encoding="utf-8")
    _mark_verified(run)
    artifact_root = tmp_path / "artifacts"

    assert workspace_changed_paths(run) == ("src/app.py",)
    result = CodeArtifactSealer(db, run, runner, root=artifact_root).seal()

    assert result.sealed is True
    patch = (artifact_root / "run1" / "patch.diff").read_text(encoding="utf-8")
    assert "gamestat/" not in patch
    assert ".git" not in patch
    assert ".claude" not in patch
    assert "runtime-only" not in patch
    assert "a/src/app.py" in patch
    assert "b/src/app.py" in patch


def test_unverified_or_incomplete_sealing_never_marks_partial_artifacts_ready(tmp_path):
    db, run, runner = _subject(tmp_path)
    run.verifier_report = json.dumps({"passed": False})
    root = tmp_path / "artifacts"

    unverified = CodeArtifactSealer(db, run, runner, root=root).seal()

    assert unverified.outcome == "infrastructure_error"
    assert unverified.reason == "verifier_report_missing"
    assert db.query(CodeArtifact).count() == 0
    assert not root.exists()
    runner.freeze.assert_not_called()

    run.verifier_report = json.dumps({"passed": True})
    incomplete = CodeArtifactSealer(db, run, runner, root=root).seal()
    assert incomplete.reason == "verifier_report_incomplete"
    runner.freeze.assert_not_called()


def test_non_patch_terminal_results_never_seal_artifacts(tmp_path):
    for status in ("target_not_found", "needs_user_decision"):
        case_root = tmp_path / status
        case_root.mkdir()
        db, run, runner = _subject(case_root)
        run.status = status
        run.verifier_report = "{}"
        artifact_root = tmp_path / f"artifacts-{status}"

        result = CodeArtifactSealer(db, run, runner, root=artifact_root).seal()

        assert result.sealed is False
        assert result.outcome == status
        assert result.reason == status
        assert not artifact_root.exists()


def test_sealer_rejects_content_changed_after_complete_verifier_scan(tmp_path):
    db, run, runner = _subject(tmp_path)
    _edit(run, "src/app.py", "value = 2\n")
    path = Path(run.workspace_path) / "src" / "app.py"
    path.write_text("value = 3\n", encoding="utf-8")
    root = tmp_path / "artifacts"

    result = CodeArtifactSealer(db, run, runner, root=root).seal()

    assert result.sealed is False
    assert result.reason == "verifier_content_mismatch"
    assert not root.exists()


def test_frozen_patch_secret_hit_never_produces_patch_ready(tmp_path):
    db, run, runner = _subject(tmp_path)
    _edit(run, "src/app.py", "value = 2\n")
    secret = "sk-example0123456789abcdef"
    (Path(run.workspace_path) / "src" / "app.py").write_text(
        f"API_KEY={secret}\n", encoding="utf-8"
    )
    root = tmp_path / "artifacts"

    result = CodeArtifactSealer(db, run, runner, root=root).seal()

    assert result.sealed is False
    assert result.outcome == "policy_rejected"
    assert result.reason == "secret_detected"
    assert db.query(CodeArtifact).count() == 0
    scan = db.query(CodeScanReport).filter(CodeScanReport.scope == "patch").one()
    assert scan.complete is True
    assert scan.findings_count == 2
    assert "src/app.py" in scan.findings
    assert "@canonical.patch.diff" in scan.findings
    assert secret not in scan.findings
    assert not root.exists()


def test_patch_warn_policy_seals_secret_hit_and_bundle_is_reviewable(tmp_path):
    db, run, runner = _subject(tmp_path)
    _set_secret_policy(run, source="block", source_unscannable="block", patch="warn")
    secret = "sk-example0123456789abcdef"
    WorkspaceIntegrityGuard(run).check_before_write("src/app.py")
    (Path(run.workspace_path) / "src" / "app.py").write_text(
        f"API_KEY={secret}\n", encoding="utf-8"
    )
    _mark_verified(run, allow_detected=True)
    root = tmp_path / "artifacts"

    result = CodeArtifactSealer(db, run, runner, root=root).seal()

    assert result.sealed is True
    assert result.outcome == "patch_ready"
    assert db.query(CodeArtifact).count() == 1
    scan = db.query(CodeScanReport).filter(CodeScanReport.scope == "patch").one()
    assert scan.complete is True
    assert scan.findings_count >= 1
    assert secret not in scan.findings
    bundle = load_verified_bundle(db.query(CodeArtifact).one())
    patch_scan = json.loads(bundle.patch_scan)
    assert "API_KEY=" in bundle.patch.decode("utf-8")
    assert patch_scan["findings_count"] >= 1
    assert patch_scan["complete"] is True
    assert secret not in json.dumps(patch_scan)


def test_secret_present_only_in_canonical_deleted_diff_never_produces_patch_ready(tmp_path):
    secret = "sk-example0123456789abcdef"
    db, run, runner = _subject(tmp_path, base_content=f"API_KEY={secret}\n")
    # Simulate a source report produced before the current scanner rule learned
    # this pattern; the final patch scanner must still protect artifact bytes.
    source_scan = db.get(CodeScanReport, run.source_scan_report_id)
    source_scan.findings_count = 0
    source_scan.findings = "[]"
    source_scan.failure_reason = ""
    db.commit()
    target = Path(run.workspace_path) / "src" / "app.py"
    WorkspaceIntegrityGuard(run).check_before_write("src/app.py")
    target.unlink()
    _mark_verified(run)
    root = tmp_path / "artifacts"

    result = CodeArtifactSealer(db, run, runner, root=root).seal()

    assert result.sealed is False
    assert result.outcome == "policy_rejected"
    assert result.reason == "secret_detected"
    scan = db.query(CodeScanReport).filter(CodeScanReport.scope == "patch").one()
    assert scan.findings_count == 1
    assert "@canonical.patch.diff" in scan.findings
    assert secret not in scan.findings
    assert db.query(CodeArtifact).count() == 0
    assert not root.exists()


def test_frozen_untracked_binary_never_produces_patch_ready(tmp_path):
    db, run, runner = _subject(tmp_path)
    binary = Path(run.workspace_path) / "src" / "new.bin"
    WorkspaceIntegrityGuard(run).check_before_write("src/new.bin")
    binary.write_bytes(b"\0binary payload")
    _mark_verified(run)
    root = tmp_path / "artifacts"

    result = CodeArtifactSealer(db, run, runner, root=root).seal()

    assert result.sealed is False
    assert result.reason == "scanner_binary_unsupported"
    assert db.query(CodeArtifact).count() == 0
    scan = db.query(CodeScanReport).filter(CodeScanReport.scope == "patch").one()
    assert scan.complete is False
    assert scan.skipped_count == 1
    assert not root.exists()


def test_patch_scan_truncation_never_produces_patch_ready(tmp_path):
    db, run, runner = _subject(tmp_path)
    _edit(run, "src/app.py", "value = 2\n")
    incomplete = ScanReport(
        scope="patch", scanner="test", scanner_version="1",
        input_hash="0" * 64, status="incomplete", complete=False,
        findings=(), files_discovered=1, files_scanned=0,
        bytes_discovered=100, bytes_scanned=10,
        skipped_count=0, truncated_count=1,
        failure_reason="scanner_truncated",
    )
    scanner = MagicMock()
    scanner.scan.return_value = incomplete
    root = tmp_path / "artifacts"

    result = CodeArtifactSealer(
        db, run, runner, root=root, patch_scanner=scanner
    ).seal()

    assert result.sealed is False
    assert result.reason == "scanner_truncated"
    assert db.query(CodeArtifact).count() == 0
    assert db.query(CodeScanReport).filter(CodeScanReport.scope == "patch").count() == 1
    assert not root.exists()


def test_verified_empty_diff_has_explicit_no_change_terminal_state(tmp_path):
    db, run, runner = _subject(tmp_path)

    result = CodeArtifactSealer(db, run, runner, root=tmp_path / "artifacts").seal()

    assert result.sealed is True
    assert result.outcome == "no_change_justified"
    assert result.diff_hash == hashlib.sha256(b"").hexdigest()


def test_source_warn_policy_seals_empty_diff_despite_incomplete_source_scan(tmp_path):
    db, run, runner = _subject(tmp_path)
    _set_secret_policy(run, source="warn", source_unscannable="warn")
    _mark_source_scan_incomplete_with_findings(db, run)
    root = tmp_path / "artifacts"

    result = CodeArtifactSealer(db, run, runner, root=root).seal()

    assert result.sealed is True
    assert result.outcome == "no_change_justified"
    assert db.query(CodeArtifact).count() == 1


def test_default_block_policy_rejects_incomplete_source_scan_with_findings(tmp_path):
    db, run, runner = _subject(tmp_path)
    _mark_source_scan_incomplete_with_findings(db, run)
    root = tmp_path / "artifacts"

    result = CodeArtifactSealer(db, run, runner, root=root).seal()

    assert result.sealed is False
    assert result.outcome == "infrastructure_error"
    assert result.reason == "artifact_evidence_missing"
    assert db.query(CodeArtifact).count() == 0
    assert not root.exists()


def test_storage_failure_removes_new_partial_collection_and_returns_infrastructure_error(tmp_path):
    db, run, runner = _subject(tmp_path)
    _edit(run, "src/app.py", "value = 2\n")
    root = tmp_path / "artifacts"
    original_commit = db.commit
    db.commit = MagicMock(side_effect=RuntimeError("storage unavailable"))

    result = CodeArtifactSealer(db, run, runner, root=root).seal()

    assert result.outcome == "infrastructure_error"
    assert result.reason == "artifact_sealing_failed"
    assert not (root / "run1").exists()
    assert run.artifact_id == ""
    db.commit = original_commit


def test_diff_line_quota_exhaustion_does_not_emit_partial_artifact(tmp_path):
    db, run, runner = _subject(tmp_path)
    policy = json.loads(run.effective_policy)
    policy["budgets"] = {"max_diff_lines": 1}
    run.effective_policy = json.dumps(policy, sort_keys=True, separators=(",", ":"))
    run.effective_policy_hash = hashlib.sha256(
        run.effective_policy.encode()
    ).hexdigest()
    _edit(run, "src/app.py", "value = 2\n")
    root = tmp_path / "artifacts"

    result = CodeArtifactSealer(db, run, runner, root=root).seal()

    assert result.outcome == "budget_exhausted"
    assert result.reason == "diff_lines_budget_exhausted"
    assert json.loads(run.budget_usage)["diff_lines"] > 1
    assert db.query(CodeArtifact).count() == 0
    assert not (root / "run1").exists()


@pytest.mark.parametrize(
    "missing",
    ["source_scan_report_id", "snapshot_id", "snapshot_hash", "resolved_commit", "effective_policy_hash"],
)
def test_missing_sealed_evidence_never_produces_success(tmp_path, missing):
    db, run, runner = _subject(tmp_path)
    _edit(run, "src/app.py", "value = 2\n")
    setattr(run, missing, "")
    root = tmp_path / "artifacts"

    result = CodeArtifactSealer(db, run, runner, root=root).seal()

    assert result.sealed is False
    assert result.outcome == "infrastructure_error"
    assert result.reason == "artifact_evidence_missing"
    assert db.query(CodeArtifact).count() == 0
    assert not root.exists()


def test_partial_artifact_file_write_is_removed_and_never_sealed(tmp_path, monkeypatch):
    db, run, runner = _subject(tmp_path)
    _edit(run, "src/app.py", "value = 2\n")
    root = tmp_path / "artifacts"
    original_write = Path.write_bytes

    def fail_mid_bundle(path, value):
        if path.name == "patch-scan-report.json":
            raise OSError("simulated partial seal")
        return original_write(path, value)

    monkeypatch.setattr(Path, "write_bytes", fail_mid_bundle)
    result = CodeArtifactSealer(db, run, runner, root=root).seal()

    assert result.sealed is False
    assert result.reason == "artifact_sealing_failed"
    assert db.query(CodeArtifact).count() == 0
    assert not list(root.glob(".*.tmp"))
    assert not (root / "run1").exists()


def test_tampered_scan_report_breaks_artifact_integrity(tmp_path):
    db, run, runner = _subject(tmp_path)
    _edit(run, "src/app.py", "value = 2\n")
    CodeArtifactSealer(db, run, runner, root=tmp_path / "artifacts").seal()
    artifact = db.query(CodeArtifact).one()
    scan_path = Path(artifact.storage_path) / "patch-scan-report.json"
    scan_path.chmod(0o600)
    scan_path.write_text('{"complete":true}', encoding="utf-8")

    with pytest.raises(ArtifactReviewError, match="code_artifact_hash_mismatch"):
        load_verified_bundle(artifact)


def test_human_review_and_acceptance_reference_only_the_sealed_artifact_without_git_writes(tmp_path):
    db, run, runner = _subject(tmp_path)
    _edit(run, "src/app.py", "value = 2\n")
    CodeArtifactSealer(db, run, runner, root=tmp_path / "artifacts").seal()
    artifact = db.query(CodeArtifact).one()
    run.status = "patch_ready"
    db.commit()
    repo = Path(run.repository)
    head_before = _git(repo, "rev-parse", "HEAD")
    status_before = _git(repo, "status", "--short")

    bundle = load_verified_bundle(artifact)
    payload = review_payload(artifact, bundle)
    patch_path, patch_name = artifact_file(bundle, "patch")
    review = accept_sealed_artifact(
        db,
        user=SimpleNamespace(username="reviewer", roles='["reviewer"]'),
        artifact=artifact,
        run=run,
        project=db.get(CodeProject, "project1"),
        latest_manifest=SimpleNamespace(base_commit=run.base_commit),
    )

    assert "value = 2" in payload["patch"]
    assert payload["hashes"]["diff_hash"] == artifact.diff_hash
    assert patch_path.name == patch_name == "patch.diff"
    assert review.artifact_id == artifact.id
    assert review.action == "accepted"
    assert db.query(CodeArtifactReview).count() == 1
    assert _git(repo, "rev-parse", "HEAD") == head_before
    assert _git(repo, "status", "--short") == status_before


def test_acceptance_rejects_stale_or_tampered_artifacts(tmp_path):
    db, run, runner = _subject(tmp_path)
    _edit(run, "src/app.py", "value = 2\n")
    CodeArtifactSealer(db, run, runner, root=tmp_path / "artifacts").seal()
    artifact = db.query(CodeArtifact).one()
    run.status = "patch_ready"
    db.commit()

    try:
        accept_sealed_artifact(
            db,
            user=SimpleNamespace(username="reviewer", roles='["reviewer"]'),
            artifact=artifact,
            run=run,
            project=db.get(CodeProject, "project1"),
            latest_manifest=SimpleNamespace(base_commit="f" * 40),
        )
        assert False, "stale artifact must be rejected"
    except ArtifactReviewError as exc:
        assert exc.reason == "code_artifact_stale"
    assert run.status == "stale"

    run.status = "patch_ready"
    patch_path = Path(artifact.storage_path) / "patch.diff"
    patch_path.chmod(0o600)
    patch_path.write_text("tampered\n", encoding="utf-8")
    try:
        load_verified_bundle(artifact)
        assert False, "tampered artifact must be rejected"
    except ArtifactReviewError as exc:
        assert exc.reason == "code_artifact_hash_mismatch"
