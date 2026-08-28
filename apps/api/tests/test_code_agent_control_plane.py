"""OpenSpec task 1.2: versioned Manifest and frozen Code run contract coverage."""

from __future__ import annotations

import asyncio
import hashlib
import json
from types import SimpleNamespace

import pytest
from fastapi import BackgroundTasks
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
import app.routers.code_project
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
    PolicyRejectedError,
    create_code_run,
    merge_policy_layers,
)
from app.routers.agent import AgentBody, agent_post
from app.routers.agent_chat import ChatBody, chat_post
from app.routers.code_project import CodeProjectBody, code_project_post
from app.services.code_agent.kill_switch import (
    CodeKillSwitchError,
    set_code_kill_switch,
)


def _db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _actor():
    return SimpleNamespace(username="admin", roles='["admin"]', organization_id="default")


def _project(db):
    row = CodeProject(id="project1", name="Project", creator="admin")
    db.add(row)
    db.add(Agent(id="agent1", name="Code Agent"))
    db.commit()
    return row


def _manifest(db, *, status="published", allowed_paths='["src/"]'):
    row = CodeProjectManifest(
        id="manifest1",
        project_id="project1",
        version=1,
        status=status,
        source_id="source1",
        source_type="ssh",
        credential_ref="deploy-token-ref",
        requested_ref="main",
        resolved_commit="a" * 40,
        snapshot_id="snapshot1",
        snapshot_hash="b" * 64,
        source_scan_report_id="scan1",
        repository="ssh://git.internal/example/repo.git",
        base_commit="a" * 40,
        allowed_paths=allowed_paths,
        validation_plan='[{"command": "pytest -q"}]',
        trusted_image="internal/python:3.12",
        image_digest="sha256:" + "c" * 64,
        security_schema_version=1,
        allowed_tools='["read", "search", "edit", "test"]',
        policy='{"network": false}',
        budgets='{"max_iterations": 40, "timeout_seconds": 1800}',
    )
    db.add(row)
    db.commit()
    return row


def _secure_fixture(db, monkeypatch):
    """Seed sealed snapshot + active deploy credential and mock ready settings.

    ``create_code_run`` now gates admission on ``secure_readiness_reason``, so any
    test that expects a run to be created must back the Manifest's frozen
    source/snapshot/image evidence with real rows and a matching settings view.
    """
    db.add(CodeSourceSnapshot(
        id="snapshot1",
        source_id="source1",
        resolved_commit="a" * 40,
        content_hash="b" * 64,
        storage_path="/tmp/snapshot1",
        scan_report_id="scan1",
        importer_version="1",
        policy_hash="p" * 64,
        status="sealed",
    ))
    db.add(CodeDeployCredential(
        id="deploy-token-ref",
        organization_id="default",
        label="deploy",
        credential_kind="deploy_token",
        secret_enc="encrypted",
        allowed_project_ids='["project1"]',
        read_only=True,
        status="active",
    ))
    db.commit()

    class ReadySettings:
        code_repository_allowlist = '["ssh://git.internal:22"]'
        code_repository_internal_cidrs = "[]"
        code_local_repository_roots = "[]"
        code_trusted_image_digests = '["sha256:' + "c" * 64 + '"]'

        def code_agent_security_readiness(self):
            return {"ready": True, "status": "ready", "reason": "ready", "errors": {}}

    monkeypatch.setattr(control_plane, "get_settings", lambda: ReadySettings())
    monkeypatch.setattr(app.routers.code_project, "get_settings", lambda: ReadySettings())


def test_missing_or_unpublished_manifest_does_not_create_code_run():
    db = _db()
    project = _project(db)
    agent = db.get(Agent, "agent1")
    with pytest.raises(ManifestUnavailableError, match="manifest_missing"):
        create_code_run(
            db, agent=agent, actor=_actor(), project_id=project.id, objective="Fix a test"
        )
    assert db.query(CodeAgentRun).count() == 0

    _manifest(db, status="draft")
    with pytest.raises(ManifestUnavailableError, match="manifest_missing"):
        create_code_run(
            db, agent=agent, actor=_actor(), project_id=project.id, objective="Fix a test"
        )
    assert db.query(CodeAgentRun).count() == 0


@pytest.mark.parametrize("environment_tier", ["production", "staging", ""])
def test_non_internal_non_production_project_never_creates_code_run(environment_tier):
    db = _db()
    project = _project(db)
    project.environment_tier = environment_tier
    _manifest(db)
    db.commit()

    with pytest.raises(ManifestUnavailableError, match="project_environment_not_allowed"):
        create_code_run(
            db,
            agent=db.get(Agent, "agent1"),
            actor=_actor(),
            project_id=project.id,
            objective="Fix the bug",
        )

    assert db.query(CodeAgentRun).count() == 0


def test_invalid_manifest_does_not_create_writable_code_run():
    db = _db()
    _project(db)
    _manifest(db, allowed_paths="[]")
    with pytest.raises(ManifestUnavailableError, match="manifest_missing_allowed_paths"):
        create_code_run(
            db,
            agent=db.get(Agent, "agent1"),
            actor=_actor(),
            project_id="project1",
            objective="Fix a test",
        )
    assert db.query(CodeAgentRun).count() == 0


def test_legacy_published_manifest_requires_security_republish_before_admission():
    db = _db()
    _project(db)
    legacy = CodeProjectManifest(
        id="legacy-manifest",
        project_id="project1",
        version=1,
        status="published",
        repository="ssh://git.internal/example/repo.git",
        base_commit="a" * 40,
        allowed_paths='["src/"]',
        validation_plan='[{"command": "pytest -q"}]',
        trusted_image="internal/python:3.12",
        allowed_tools='["read", "test"]',
        budgets='{"timeout_seconds": 60}',
    )
    db.add(legacy)
    db.commit()

    with pytest.raises(ManifestUnavailableError, match="security_republish_required"):
        create_code_run(
            db,
            agent=db.get(Agent, "agent1"),
            actor=_actor(),
            project_id="project1",
            objective="Fix a test",
        )
    assert db.query(CodeAgentRun).count() == 0


def test_published_manifest_freezes_contract_and_policy_snapshot(monkeypatch):
    db = _db()
    project = _project(db)
    project.workspace_retention_hours = 24
    db.commit()
    manifest = _manifest(db)
    _secure_fixture(db, monkeypatch)
    run = create_code_run(
        db,
        agent=db.get(Agent, "agent1"),
        actor=_actor(),
        project_id="project1",
        objective="Fix a test",
    )
    frozen_contract = json.loads(run.task_contract)
    assert run.status == "pending"
    assert run.workspace_retention_hours == 24
    assert run.manifest_version == 1
    assert frozen_contract["base_commit"] == "a" * 40
    assert frozen_contract["source_id"] == "source1"
    assert frozen_contract["source_type"] == "ssh"
    assert frozen_contract["requested_ref"] == "main"
    assert frozen_contract["resolved_commit"] == "a" * 40
    assert frozen_contract["snapshot_id"] == "snapshot1"
    assert frozen_contract["snapshot_hash"] == "b" * 64
    assert frozen_contract["source_scan_report_id"] == "scan1"
    assert frozen_contract["image_digest"] == "sha256:" + "c" * 64
    assert frozen_contract["security_schema_version"] == 1
    assert run.source_id == frozen_contract["source_id"]
    assert run.resolved_commit == frozen_contract["resolved_commit"]
    assert run.snapshot_id == frozen_contract["snapshot_id"]
    assert run.snapshot_hash == frozen_contract["snapshot_hash"]
    assert run.image_digest == frozen_contract["image_digest"]
    assert run.security_schema_version == 1
    assert frozen_contract["allowed_paths"] == ["src/"]
    assert frozen_contract["coding_runtime"] == "legacy"
    assert frozen_contract["allowed_skills"] == []
    assert frozen_contract["authorized_mcp_servers"] == []
    assert frozen_contract["runtime_budgets"] == {"max_verifier_retries": 2}
    assert frozen_contract["model_config"] == {"model_ref": "", "provider": "agent_llm"}
    assert json.loads(run.effective_policy) == {
        "allowed_image_digests": ["sha256:" + "c" * 64],
        "allowed_paths": ["src/"],
        "allowed_skills": [],
        "allowed_source_types": ["ssh"],
        "allowed_sources": ["source1"],
        "allowed_tools": ["edit", "read", "search", "test"],
        "authorized_mcp_servers": [],
        "budgets": {
            "cpu_count": 2,
            "max_iterations": 40,
            "max_tool_calls": 200,
            "max_concurrent_runs": 5,
            "max_changed_files": 20,
            "max_diff_lines": 500,
            "memory_mb": 512,
            "timeout_seconds": 1800,
            "pids_limit": 256,
            "tmpfs_mb": 64,
            "disk_mb": 1024,
            "output_limit_bytes": 100000,
        },
        "coding_runtime": "legacy",
        "model_config": {"model_ref": "", "provider": "agent_llm"},
        "network": False,
        "policy_sources": {
            "allowed_image_digests": "manifest",
            "allowed_paths": "manifest",
            "allowed_skills": "agent_binding",
            "allowed_source_types": "manifest",
            "allowed_sources": "manifest",
            "allowed_tools": "manifest",
            "authorized_mcp_servers": "agent_binding",
            "budgets": {
                "cpu_count": "platform",
                "max_changed_files": "platform",
                "max_concurrent_runs": "platform",
                "max_diff_lines": "platform",
                "max_iterations": "platform",
                "max_tool_calls": "platform",
                "memory_mb": "platform",
                "timeout_seconds": "platform",
                "pids_limit": "platform",
                "tmpfs_mb": "platform",
                "disk_mb": "platform",
                "output_limit_bytes": "platform",
            },
            "coding_runtime": "platform",
            "model_config": "agent",
            "network": "platform",
            "protected_paths": "platform",
            "runtime_budgets": {
                "max_verifier_retries": "platform",
            },
            "secret_policy": "platform",
            "shell_commands": "platform",
            "test_integrity_paths": "platform",
        },
        "protected_paths": [],
        "secret_policy": {
            "source": "block",
            "source_unscannable": "block",
            "patch": "block",
            "output": "redact",
        },
        "runtime_budgets": {"max_verifier_retries": 2},
        "shell_commands": ["pytest", "ruff", "mypy", "npm", "pnpm", "yarn", "make"],
        "test_integrity_paths": [],
    }
    assert run.effective_policy_hash == hashlib.sha256(
        run.effective_policy.encode("utf-8")
    ).hexdigest()

    manifest.allowed_paths = '["different/"]'
    manifest.snapshot_id = "snapshot2"
    manifest.resolved_commit = "d" * 40
    db.commit()
    assert json.loads(run.task_contract)["allowed_paths"] == ["src/"]
    assert run.snapshot_id == "snapshot1"
    assert run.resolved_commit == "a" * 40


def test_secure_draft_publish_and_run_copy_keep_one_immutable_contract(monkeypatch):
    db = _db()
    _project(db)
    manifest = _manifest(db, status="draft")
    _secure_fixture(db, monkeypatch)

    class PreparedPublisher:
        def publish(self, *, actor, project, manifest):
            manifest.status = "published"
            db.commit()
            return manifest

    monkeypatch.setattr(
        app.routers.code_project,
        "build_manifest_publish_service",
        lambda current_db: PreparedPublisher(),
    )

    response = asyncio.run(code_project_post(
        CodeProjectBody(
            action="publish",
            project_id="project1",
            manifest_id=manifest.id,
        ),
        user=SimpleNamespace(username="admin", roles='["master"]'),
        db=db,
    ))
    assert response["code"] == 0
    assert db.get(CodeProjectManifest, manifest.id).status == "published"

    run = create_code_run(
        db,
        agent=db.get(Agent, "agent1"),
        actor=_actor(),
        project_id="project1",
        objective="Fix a test",
    )
    frozen = json.loads(run.task_contract)
    assert frozen["source_id"] == manifest.source_id
    assert frozen["snapshot_id"] == manifest.snapshot_id
    assert frozen["resolved_commit"] == manifest.resolved_commit
    assert run.image_digest == manifest.image_digest

    manifest.snapshot_id = "changed-after-publish"
    manifest.resolved_commit = "e" * 40
    db.commit()
    assert run.snapshot_id == "snapshot1"
    assert run.resolved_commit == "a" * 40


def test_policy_layers_exclude_lower_level_expansion_attempts():
    effective = merge_policy_layers(
        project={
            "allowed_paths": ["src/"],
            "allowed_tools": ["read"],
            "budgets": {"timeout_seconds": 60},
        },
        task={
            "allowed_paths": ["src/", "other/"],
            "network": True,
            "allowed_tools": ["read", "push"],
            "budgets": {"timeout_seconds": 1801},
        },
    )

    assert effective["allowed_paths"] == ["src/"]
    assert effective["network"] is False
    assert effective["allowed_tools"] == ["read"]
    assert effective["budgets"]["timeout_seconds"] == 60


def test_policy_layers_only_reduce_effective_capabilities():
    effective = merge_policy_layers(
        project={
            "allowed_paths": ["src/"],
            "allowed_tools": ["read", "test"],
            "budgets": {"timeout_seconds": 600},
        },
        task={
            "allowed_paths": ["src/component/"],
            "allowed_tools": ["read"],
            "budgets": {"timeout_seconds": 120},
        },
    )
    assert effective["allowed_paths"] == ["src/component/"]
    assert effective["allowed_tools"] == ["read"]
    assert effective["budgets"]["timeout_seconds"] == 120


def test_code_profile_configuration_requires_an_authorized_project():
    db = _db()
    _project(db)
    response = asyncio.run(agent_post(
        AgentBody(action="create", name="code", profile="code", code_project_id="project1"),
        SimpleNamespace(username="outsider", roles="[]"),
        db,
    ))
    assert response["code"] == 1
    assert response["msg"] == "code_project_unauthorized"
    assert db.query(Agent).filter(Agent.name == "code").first() is None


def test_authorized_code_profile_can_be_saved_without_changing_standard_agents(monkeypatch):
    db = _db()
    _project(db)
    _manifest(db)
    _secure_fixture(db, monkeypatch)
    code_response = asyncio.run(agent_post(
        AgentBody(action="create", name="code", profile="code", code_project_id="project1"),
        SimpleNamespace(username="admin", roles="[]"),
        db,
    ))
    assert code_response["code"] == 0
    assert code_response["data"]["profile"] == "code"
    standard_response = asyncio.run(agent_post(
        AgentBody(action="create", name="standard"),
        SimpleNamespace(username="admin", roles="[]"),
        db,
    ))
    assert standard_response["code"] == 0
    assert standard_response["data"]["profile"] == "standard"


def test_code_task_entry_rejects_missing_manifest_before_run_creation():
    db = _db()
    _project(db)
    agent = db.get(Agent, "agent1")
    agent.profile = "code"
    agent.code_project_id = "project1"
    db.commit()
    response = asyncio.run(chat_post(
        ChatBody(action="submit_chat", agent_id="agent1", session_id="s1", message="Fix a test"),
        BackgroundTasks(),
        action=None,
        user=SimpleNamespace(username="admin", roles='["operator"]'),
        db=db,
    ))
    assert response["code"] == 1
    assert response["msg"] == "manifest_missing"
    assert db.query(CodeAgentRun).count() == 0


@pytest.mark.parametrize(
    ("scope", "target"),
    [
        ("global", "*"),
        ("project", "project1"),
        ("repository", "ssh://git.internal/example/repo.git"),
        ("tool", "edit"),
        ("image", "internal/python:3.12"),
        ("model", "llm1"),
        ("runtime", "legacy"),
    ],
)
def test_layered_kill_switches_block_only_new_code_runs(scope, target, monkeypatch):
    db = _db()
    _project(db)
    _manifest(db)
    _secure_fixture(db, monkeypatch)
    agent = db.get(Agent, "agent1")
    agent.llm_id = "llm1"
    set_code_kill_switch(db, scope=scope, target=target, reason="incident")
    db.commit()

    with pytest.raises(CodeKillSwitchError, match=f"code_kill_switch_{scope}"):
        create_code_run(
            db,
            agent=agent,
            actor=_actor(),
            project_id="project1",
            objective="Fix a test",
        )
    assert db.query(CodeAgentRun).count() == 0


def test_kill_switch_does_not_cancel_existing_code_run_or_disable_standard_agent(monkeypatch):
    db = _db()
    _project(db)
    _manifest(db)
    _secure_fixture(db, monkeypatch)
    agent = db.get(Agent, "agent1")
    existing = create_code_run(
        db,
        agent=agent,
        actor=_actor(),
        project_id="project1",
        objective="Existing task",
    )
    db.commit()
    set_code_kill_switch(db, scope="global", reason="incident")
    db.commit()

    with pytest.raises(CodeKillSwitchError, match="code_kill_switch_global"):
        create_code_run(
            db,
            agent=agent,
            actor=_actor(),
            project_id="project1",
            objective="New task",
        )
    assert db.get(CodeAgentRun, existing.id).status == "pending"

    response = asyncio.run(chat_post(
        ChatBody(action="submit_chat", agent_id="agent1", session_id="s1", message="hello"),
        BackgroundTasks(),
        action=None,
        user=SimpleNamespace(username="admin", roles="[]"),
        db=db,
    ))
    assert response["code"] == 0
    assert response["data"]["status"] == "started"


def test_project_concurrency_quota_creates_observable_budget_exhausted_result(monkeypatch):
    db = _db()
    _project(db)
    _manifest(db)
    _secure_fixture(db, monkeypatch)
    agent = db.get(Agent, "agent1")
    first = create_code_run(
        db,
        agent=agent,
        actor=_actor(),
        project_id="project1",
        objective="First",
        task_policy={"budgets": {"max_concurrent_runs": 1}},
    )
    db.commit()
    second = create_code_run(
        db,
        agent=agent,
        actor=_actor(),
        project_id="project1",
        objective="Second",
        task_policy={"budgets": {"max_concurrent_runs": 1}},
    )
    db.commit()

    assert first.status == "pending"
    assert second.status == "budget_exhausted"
    assert second.failure_reason == "project_concurrency_limit"
    assert json.loads(second.budget_usage) == {
        "max_concurrent_runs": 1,
        "project_active_runs_at_admission": 1,
    }


def test_code_submission_uses_dedicated_queue_while_standard_keeps_existing_path(monkeypatch):
    db = _db()
    _project(db)
    _manifest(db)
    _secure_fixture(db, monkeypatch)
    code = db.get(Agent, "agent1")
    code.profile = "code"
    code.code_project_id = "project1"
    db.add(Agent(id="standard1", name="Standard", profile="standard"))
    db.commit()
    code_tasks = BackgroundTasks()
    standard_tasks = BackgroundTasks()

    code_response = asyncio.run(chat_post(
        ChatBody(action="submit_chat", agent_id="agent1", session_id="s1", message="Fix"),
        code_tasks,
        action=None,
        user=SimpleNamespace(username="admin", roles='["operator"]'),
        db=db,
    ))
    standard_response = asyncio.run(chat_post(
        ChatBody(action="submit_chat", agent_id="standard1", session_id="s1", message="Work"),
        standard_tasks, action=None, user=SimpleNamespace(username="admin", roles="[]"), db=db,
    ))

    assert code_response["code"] == 0
    assert code_tasks.tasks[0].func.__name__ == "_enqueue_code_chat"
    assert standard_response["code"] == 0
    assert standard_tasks.tasks[0].func.__name__ == "_run_chat_bg"
