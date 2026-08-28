"""OpenSpec task 5.4: stable CodeAgent result API semantics."""

from __future__ import annotations

import asyncio
import hashlib
import json
from types import SimpleNamespace

from fastapi.responses import FileResponse
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import Agent, CodeAgentRun, CodeArtifact, CodeProject, User
from app.routers.agent_chat import chat_get
from app.services.code_agent.results import TERMINAL_PRESENTATION, serialize_code_result


def _run(status, *, verified=False):
    return SimpleNamespace(
        id="run1", project_id="project1", status=status, failure_reason="reason",
        manifest_id="manifest1", manifest_version=3,
        verifier_report=json.dumps({"passed": verified}),
    )


def _artifact():
    return SimpleNamespace(
        id="artifact1", status="sealed", base_commit="a" * 40, diff_hash="b" * 64,
        policy_hash="c" * 64, verifier_report_hash="d" * 64,
        image="internal/code@sha256:abc", image_id="sha256:abc",
    )


def test_required_terminal_states_have_stable_presentation_and_only_verified_seal_is_adoptable():
    required = {
        "patch_ready", "stale", "no_change_justified", "verification_inconclusive",
        "budget_exhausted", "no_progress", "policy_rejected", "infrastructure_error",
        "target_not_found", "needs_user_decision",
    }
    assert required.issubset(TERMINAL_PRESENTATION)

    ready = serialize_code_result(_run("patch_ready", verified=True), _artifact())
    assert ready["directly_adoptable"] is True
    assert ready["warning"] == ""
    assert ready["manifest_version"] == 3
    assert ready["verifier_report"] == {"passed": True}

    for status in required - {"patch_ready"}:
        result = serialize_code_result(_run(status, verified=status == "no_change_justified"), _artifact())
        assert result["status"] == status
        assert result["directly_adoptable"] is False
        assert "不可直接采用" in result["warning"]

    inconsistent = serialize_code_result(_run("patch_ready", verified=False), None)
    assert inconsistent["status"] == "infrastructure_error"
    assert inconsistent["directly_adoptable"] is False


def test_non_patch_terminal_results_are_distinct_and_not_adoptable():
    for status in ("target_not_found", "needs_user_decision"):
        run = _run(status, verified=False)
        run.failure_reason = status
        run.task_contract = json.dumps({"coding_runtime": "claude_code"})
        run.effective_policy = json.dumps({"coding_runtime": "claude_code"})
        run.runner_facts = json.dumps({
            "claude_code_runtime": {
                "status": status,
                "summary": "未找到 192.168.10.36:5432",
                "runtime_events": [
                    {
                        "type": "tool_call",
                        "target": "192.168.10.36:5432",
                        "search_scope": "allowed_paths",
                        "candidates": ["profiles.yml"],
                    }
                ],
            }
        })
        result = serialize_code_result(run)

        assert result["status"] == status
        assert result["label"] == TERMINAL_PRESENTATION[status][0]
        assert result["failure_reason"] == status
        assert result["failure"]["stage"] == "coding"
        assert result["directly_adoptable"] is False
        assert result["artifact"] is None
        assert "不可直接采用" in result["warning"]
        runtime_result = result["runtime"]["runtime_result"]
        assert runtime_result["summary"] == "未找到 192.168.10.36:5432"
        assert runtime_result["runtime_events"][0]["target"] == "192.168.10.36:5432"
        assert runtime_result["runtime_events"][0]["search_scope"] == "allowed_paths"


def test_code_result_includes_claude_code_runtime_visibility_facts():
    secret = "sk-" + "c" * 20
    run = SimpleNamespace(
        id="run1",
        project_id="project1",
        status="verification_failed",
        failure_reason="verification_failed",
        manifest_id="manifest1",
        manifest_version=3,
        verifier_report=json.dumps({"passed": False}),
        budget_usage="{}",
        task_contract=json.dumps({"coding_runtime": "claude_code"}),
        effective_policy=json.dumps({"coding_runtime": "claude_code"}),
        runner_facts=json.dumps({
            "claude_code_preflight": {"passed": True},
            "claude_code_skills": {
                "skills": [{"id": "skill1", "name": "dbt-clickhouse-gamestat"}],
            },
            "claude_code_mcp": {
                "servers": [{"id": "mcp1", "name": "dbt-mcp", "enabled_tool_count": 3}],
            },
            "claude_code_runtime": {
                "status": "coding_completed",
                "summary": f"password={secret}",
            },
        }),
    )

    result = serialize_code_result(run)
    serialized = json.dumps(result, sort_keys=True)

    assert result["runtime"]["coding_runtime"] == "claude_code"
    assert result["runtime"]["preflight"]["passed"] is True
    assert result["runtime"]["skills"][0]["name"] == "dbt-clickhouse-gamestat"
    assert result["runtime"]["mcp_servers"][0]["enabled_tool_count"] == 3
    assert result["runtime"]["runtime_result"]["status"] == "coding_completed"
    assert secret not in serialized
    assert "[REDACTED:SECRET]" in serialized


def test_code_result_surfaces_specific_claude_code_model_binding_reason():
    run = SimpleNamespace(
        id="run1",
        project_id="project1",
        status="model_unavailable",
        failure_reason="llm_group_not_supported",
        manifest_id="manifest1",
        manifest_version=3,
        verifier_report="{}",
        budget_usage="{}",
        task_contract=json.dumps({"coding_runtime": "claude_code"}),
        effective_policy=json.dumps({"coding_runtime": "claude_code"}),
        runner_facts=json.dumps({
            "claude_code_preflight": {
                "passed": False,
                "failure_type": "model_unavailable",
                "reason": "llm_group_not_supported",
            },
        }),
    )

    result = serialize_code_result(run)

    assert result["status"] == "model_unavailable"
    assert result["failure_reason"] == "model_unavailable"
    assert result["failure"]["internal_reason"] == "llm_group_not_supported"
    assert "LLM Group" in result["failure"]["detail"]


def test_code_result_api_is_session_scoped_project_authorized_and_marks_unverified_result():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    db.add(Agent(id="agent1", name="Code", profile="code", code_project_id="project1"))
    db.add(CodeProject(
        id="project1", name="Project", visibility="private",
        allowed_users='["reviewer"]', creator="owner", enabled=True,
    ))
    db.add(CodeAgentRun(
        id="run1", agent_id="agent1", session_id="session1", project_id="project1",
        manifest_id="manifest1", manifest_version=1, status="verification_inconclusive",
        verifier_report=json.dumps({"passed": False}), failure_reason="baseline_unavailable",
    ))
    db.commit()
    reviewer = User(username="reviewer", password_hash="x", roles='["reviewer"]')

    response = asyncio.run(chat_get(
        request=SimpleNamespace(headers={}), action="get_code_result",
        agent_id="agent1", session_id="session1", path=None, code_run_id=None,
        message_id=None, limit=None, user=reviewer, db=db,
    ))

    assert response["data"]["status"] == "verification_inconclusive"
    assert response["data"]["directly_adoptable"] is False
    assert response["data"]["manifest_version"] == 1
    assert response["data"]["verifier_report"] == {"passed": False}
    assert "不可直接采用" in response["data"]["warning"]


def test_project_authorized_review_and_fixed_file_download_interfaces(tmp_path):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    root = tmp_path / "artifact"
    root.mkdir()
    patch = b"diff --git a/src/app.py b/src/app.py\n"
    policy = b"{}"
    source_scan = json.dumps({
        "id": "source-scan1", "scope": "source", "complete": True,
        "findings_count": 0, "status": "complete", "failure_reason": "",
    }, sort_keys=True).encode()
    patch_scan = json.dumps({
        "id": "patch-scan1", "scope": "patch", "complete": True,
        "findings_count": 0,
    }, sort_keys=True).encode()
    verifier = json.dumps({
        "passed": True, "patch_scan_report_id": "patch-scan1",
    }, sort_keys=True).encode()
    hashes = {
        "diff_hash": hashlib.sha256(patch).hexdigest(),
        "policy_hash": hashlib.sha256(policy).hexdigest(),
        "verifier_report_hash": hashlib.sha256(verifier).hexdigest(),
    }
    manifest = json.dumps({
        "version": 2,
        "artifact_id": "artifact1", "run_id": "run1", "project_id": "project1",
        "base_commit": "a" * 40, "resolved_commit": "a" * 40,
        "snapshot_id": "snapshot1", "snapshot_hash": "e" * 64,
        "image_digest": "sha256:abc",
        "source_scan_report_id": "source-scan1",
        "source_scan_report_hash": hashlib.sha256(source_scan).hexdigest(),
        "patch_scan_report_id": "patch-scan1",
        "patch_scan_report_hash": hashlib.sha256(patch_scan).hexdigest(),
        **hashes,
    }, sort_keys=True).encode()
    for name, value in {
        "patch.diff": patch, "policy.json": policy,
        "verifier-report.json": verifier,
        "source-scan-report.json": source_scan,
        "patch-scan-report.json": patch_scan,
        "manifest.json": manifest,
    }.items():
        (root / name).write_bytes(value)
    db.add(CodeProject(
        id="project1", name="Project", visibility="private",
        allowed_users='["reviewer"]', creator="owner", enabled=True,
    ))
    db.add(CodeAgentRun(
        id="run1", agent_id="agent1", session_id="session1", project_id="project1",
        manifest_id="manifest1", manifest_version=1, status="patch_ready",
        verifier_report=verifier.decode(), artifact_id="artifact1",
        resolved_commit="a" * 40, snapshot_id="snapshot1", snapshot_hash="e" * 64,
        image_digest="sha256:abc",
    ))
    db.add(CodeArtifact(
        id="artifact1", run_id="run1", project_id="project1", manifest_version=1,
        base_commit="a" * 40, storage_path=str(root), status="sealed", **hashes,
    ))
    db.commit()
    reviewer = User(username="reviewer", password_hash="x", roles='["reviewer"]')

    common = dict(
        request=SimpleNamespace(headers={}), agent_id="agent1", session_id="session1",
        path=None, code_run_id=None, artifact_id="artifact1", message_id=None,
        limit=None, user=reviewer, db=db,
    )
    reviewed = asyncio.run(chat_get(
        action="review_code_artifact", artifact_kind=None, **common,
    ))
    downloaded = asyncio.run(chat_get(
        action="download_code_artifact", artifact_kind="patch", **common,
    ))

    assert reviewed["data"]["patch"] == patch.decode()
    assert reviewed["data"]["hashes"]["diff_hash"] == hashes["diff_hash"]
    assert isinstance(downloaded, FileResponse)
    assert downloaded.path == root / "patch.diff"

    run = db.get(CodeAgentRun, "run1")
    run.status = "verification_inconclusive"
    db.commit()
    blocked = asyncio.run(chat_get(
        action="review_code_artifact", artifact_kind=None, **common,
    ))
    assert blocked["msg"] == "code_artifact_not_reviewable"
