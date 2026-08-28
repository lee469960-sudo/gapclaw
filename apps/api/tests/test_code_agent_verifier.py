"""OpenSpec task 5.1: authoritative post-loop Code verifier."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app.services.code_agent.runner import RunnerCommandTimeout
from app.services.code_agent.output_security import SecretScanResult
from app.services.code_agent.snapshot_store import SourceSnapshotStore
from app.services.code_agent.verifier import CodeVerifier
from app.services.code_agent.workspace import WorkspaceIntegrityGuard, WorkspaceManager


def _git(repo, *args):
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    ).stdout.strip()


def _subject(tmp_path, *, protected_paths=None, test_integrity_paths=None, secret_policy=None):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    (repo / "src").mkdir()
    (repo / "tests").mkdir()
    (repo / "src" / "app.py").write_text("value = 1\n", encoding="utf-8")
    (repo / "tests" / "test_app.py").write_text("def test_app(): pass\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "base")
    commit = _git(repo, "rev-parse", "HEAD")
    snapshots = tmp_path / "snapshots"
    snapshots.mkdir()
    store = SourceSnapshotStore(snapshots)
    sealed = store.seal(repo, resolved_commit=commit)
    run = SimpleNamespace(
        id="run1", repository=str(repo), base_commit=commit,
        resolved_commit=commit, snapshot_id=sealed.snapshot_id, snapshot_hash=sealed.content_hash,
        workspace_path="", source_facts="{}", status="running", failure_reason="",
        workspace_state="", container_id="container1", verifier_report="{}",
        verification_baseline=json.dumps({
            "status": "passed",
            "reason": "",
            "tests": [
                {"test_index": 0, "command": "pytest -q", "exit_code": 0, "output": "passed"},
                {"test_index": 1, "command": "ruff check .", "exit_code": 0, "output": "passed"},
            ],
        }),
        effective_policy=json.dumps({
            "allowed_paths": ["src/", "tests/"],
            "protected_paths": protected_paths or [],
            "test_integrity_paths": test_integrity_paths or [],
            "budgets": {"timeout_seconds": 60},
            **({"secret_policy": secret_policy} if secret_policy else {}),
        }),
        task_contract=json.dumps({
            "validation_plan": [
                {"command": "pytest -q"},
                {"command": "ruff check ."},
            ],
        }),
    )
    WorkspaceManager(tmp_path / "runs").prepare(run, snapshot_store=store)
    db = SimpleNamespace(commit=MagicMock())
    runner = SimpleNamespace(exec=MagicMock(return_value=(0, "passed")))
    return CodeVerifier(db, run, runner), run, runner


def _edit(run, relative, content):
    WorkspaceIntegrityGuard(run).check_before_write(relative)
    (Path(run.workspace_path) / relative).write_text(content, encoding="utf-8")


def test_verifier_always_executes_the_entire_frozen_validation_plan(tmp_path):
    verifier, run, runner = _subject(tmp_path)
    _edit(run, "src/app.py", "value = 2\n")

    report = verifier.verify()

    assert report.passed is True
    assert report.outcome == "verification_passed"
    assert report.checks == (
        "workspace_integrity", "protected_paths", "test_integrity",
        "validation_plan", "secret_detection_complete",
    )
    assert report.secret_scan_complete is True
    assert len(report.content_hash) == 64
    assert [call.args[1] for call in runner.exec.call_args_list] == [
        "pytest -q", "ruff check .",
    ]
    assert json.loads(run.verifier_report)["passed"] is True


def test_verifier_blocks_protected_path_even_when_tests_would_pass(tmp_path):
    verifier, run, runner = _subject(tmp_path, protected_paths=["src/app.py"])
    _edit(run, "src/app.py", "value = 2\n")

    report = verifier.verify()

    assert report.passed is False
    assert report.outcome == "policy_rejected"
    assert report.reason == "protected_path_modified"
    runner.exec.assert_not_called()


def test_verifier_blocks_test_integrity_changes_before_running_tests(tmp_path):
    verifier, run, runner = _subject(tmp_path, test_integrity_paths=["tests/**"])
    _edit(run, "tests/test_app.py", "def test_app(): assert True\n")

    report = verifier.verify()

    assert report.reason == "test_integrity_violation"
    assert report.outcome == "policy_rejected"
    runner.exec.assert_not_called()


def test_verifier_blocks_secret_like_content_and_never_persists_it(tmp_path):
    verifier, run, runner = _subject(tmp_path)
    secret = "sk-example0123456789abcdef"
    _edit(run, "src/app.py", f"API_KEY={secret}\n")

    report = verifier.verify()

    assert report.reason == "secret_detected"
    assert report.outcome == "policy_rejected"
    assert secret not in run.verifier_report
    runner.exec.assert_not_called()


def test_verifier_honors_patch_warn_and_still_runs_validation(tmp_path):
    verifier, run, runner = _subject(
        tmp_path,
        secret_policy={
            "source": "block",
            "source_unscannable": "block",
            "patch": "warn",
            "output": "redact",
        },
    )
    secret = "sk-example0123456789abcdef"
    _edit(run, "src/app.py", f"API_KEY={secret}\n")

    report = verifier.verify()

    assert report.passed is True
    assert report.outcome == "verification_passed"
    assert report.secret_scan_complete is True
    assert len(report.content_hash) == 64
    assert secret not in run.verifier_report
    assert [call.args[1] for call in runner.exec.call_args_list] == [
        "pytest -q", "ruff check .",
    ]


def test_verifier_scans_secret_in_large_file_without_size_bypass(tmp_path):
    verifier, run, runner = _subject(tmp_path)
    secret = "sk-example0123456789abcdef"
    _edit(run, "src/large.py", "x" * (2 * 1024 * 1024 - 4) + "\n" + secret)

    report = verifier.verify()

    assert report.outcome == "policy_rejected"
    assert report.reason == "secret_detected"
    assert secret not in run.verifier_report
    runner.exec.assert_not_called()


def test_verifier_scans_secret_in_binary_changed_content(tmp_path):
    verifier, run, runner = _subject(tmp_path)
    WorkspaceIntegrityGuard(run).check_before_write("src/data.bin")
    secret = b"ghp_0123456789abcdef0123456789abcdef"
    (Path(run.workspace_path) / "src" / "data.bin").write_bytes(b"\x00\xff" + secret + b"\x00")

    report = verifier.verify()

    assert report.outcome == "policy_rejected"
    assert report.reason == "secret_detected"
    assert secret.decode() not in run.verifier_report
    runner.exec.assert_not_called()


@pytest.mark.parametrize("reason", ["secret_scan_truncated", "secret_scan_failed"])
def test_incomplete_secret_scan_fails_closed(tmp_path, reason):
    verifier, run, runner = _subject(tmp_path)
    _edit(run, "src/app.py", "value = 2\n")
    incomplete = SecretScanResult(False, False, "", 128, reason)

    with patch(
        "app.services.code_agent.verifier.scan_changed_content",
        return_value=incomplete,
    ):
        report = verifier.verify()

    assert report.passed is False
    assert report.outcome == "verification_inconclusive"
    assert report.reason == reason
    assert report.secret_scan_complete is False
    runner.exec.assert_not_called()


def test_unsupported_changed_file_type_fails_secret_scan_closed(tmp_path):
    verifier, run, runner = _subject(tmp_path)
    relative = "src/unsupported.pipe"
    WorkspaceIntegrityGuard(run).check_before_write(relative)
    os.mkfifo(Path(run.workspace_path) / relative)

    report = verifier.verify()

    assert report.passed is False
    assert report.outcome == "verification_inconclusive"
    assert report.reason == "secret_scan_unsupported"
    runner.exec.assert_not_called()


def test_validation_baseline_is_recorded_and_workspace_is_restored(tmp_path):
    verifier, run, runner = _subject(tmp_path)
    workspace = Path(run.workspace_path)

    def _baseline_exec(_container, command, **_kwargs):
        (workspace / "generated.tmp").write_text(command, encoding="utf-8")
        return (1 if command == "pytest -q" else 0), "baseline output"

    runner.exec.side_effect = _baseline_exec
    manager = WorkspaceManager(workspace.parent.parent)
    baseline = verifier.capture_baseline(manager)

    assert baseline["status"] == "broken"
    assert baseline["reason"] == "pre_existing_failure"
    assert not (workspace / "generated.tmp").exists()
    assert json.loads(run.verification_baseline) == baseline
    WorkspaceIntegrityGuard(run).check_before_seal()


def test_baseline_container_timeout_is_not_downgraded_to_unavailable(tmp_path):
    verifier, run, runner = _subject(tmp_path)
    runner.exec.side_effect = RunnerCommandTimeout()

    with pytest.raises(RunnerCommandTimeout, match="runner_command_timed_out"):
        verifier.capture_baseline(WorkspaceManager(Path(run.workspace_path).parent.parent))

    assert run.verification_baseline != json.dumps({
        "status": "unavailable", "reason": "baseline_unavailable", "tests": [],
    }, sort_keys=True)


def test_final_verifier_container_timeout_is_not_downgraded_to_inconclusive(tmp_path):
    verifier, run, runner = _subject(tmp_path)
    _edit(run, "src/app.py", "value = 2\n")
    runner.exec.side_effect = RunnerCommandTimeout()

    with pytest.raises(RunnerCommandTimeout, match="runner_command_timed_out"):
        verifier.verify()

    assert run.verifier_report == "{}"


def test_verifier_classifies_new_and_pre_existing_failures(tmp_path):
    verifier, run, runner = _subject(tmp_path)
    _edit(run, "src/app.py", "value = 2\n")
    runner.exec.side_effect = [(1, "new failure"), (0, "passed")]

    new_failure = verifier.verify()

    assert new_failure.outcome == "verification_failed"
    assert new_failure.reason == "new_validation_failure"
    assert new_failure.tests[0]["failure_classification"] == "new_failure"

    run.verification_baseline = json.dumps({
        "status": "broken",
        "reason": "pre_existing_failure",
        "tests": [
            {"test_index": 0, "command": "pytest -q", "exit_code": 1, "output": "old failure"},
            {"test_index": 1, "command": "ruff check .", "exit_code": 0, "output": "passed"},
        ],
    })
    runner.exec.side_effect = [(1, "same failure"), (0, "passed")]

    existing_failure = verifier.verify()

    assert existing_failure.outcome == "baseline_broken"
    assert existing_failure.reason == "pre_existing_failure"
    assert existing_failure.tests[0]["failure_classification"] == "pre_existing_failure"


def test_unavailable_baseline_keeps_final_verification_inconclusive(tmp_path):
    verifier, run, runner = _subject(tmp_path)
    runner.exec.side_effect = RuntimeError("dependency unavailable: internal detail")
    manager = WorkspaceManager(Path(run.workspace_path).parent.parent)

    baseline = verifier.capture_baseline(manager)
    report = verifier.verify()

    assert baseline["status"] == "unavailable"
    assert baseline["reason"] == "baseline_unavailable"
    assert "internal detail" not in run.verification_baseline
    assert report.outcome == "verification_inconclusive"
    assert report.reason == "verification_baseline_unavailable"


def test_changed_file_quota_exhaustion_is_observable_and_does_not_lower_verification(tmp_path):
    verifier, run, runner = _subject(tmp_path)
    policy = json.loads(run.effective_policy)
    policy["budgets"]["max_changed_files"] = 1
    run.effective_policy = json.dumps(policy)
    verifier = CodeVerifier(verifier.db, run, runner)
    _edit(run, "src/app.py", "value = 2\n")
    _edit(run, "tests/test_app.py", "def test_app(): assert True\n")

    report = verifier.verify()

    assert report.outcome == "budget_exhausted"
    assert report.reason == "changed_files_budget_exhausted"
    assert json.loads(run.budget_usage)["changed_files"] == 2
    runner.exec.assert_not_called()
