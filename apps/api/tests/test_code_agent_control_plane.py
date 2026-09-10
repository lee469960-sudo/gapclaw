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
    Sandbox,
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
    db.add(Sandbox(
        id="sandbox1",
        name="Code Sandbox",
        image="internal/python:3.12",
        container_id="container1",
        status="running",
        creator="admin",
    ))
    db.add(Agent(id="agent1", name="Code Agent", sandbox_id="sandbox1"))
    db.commit()
    return row


def _manifest(db, *, status="published", requested_ref="main"):
    row = CodeProjectManifest(
        id="manifest1",
        project_id="project1",
        version=1,
        status=status,
        source_id="source1",
        source_type="ssh",
        credential_ref="deploy-token-ref",
        requested_ref=requested_ref,
        repository="ssh://git.internal/example/repo.git",
        base_commit="",
        resolved_commit="",
        snapshot_id="",
        snapshot_hash="",
        source_scan_report_id="",
        allowed_paths="[]",
        validation_plan='[{"command": "pytest -q"}]',
        trusted_image="internal/python:3.12",
        image_digest="",
        security_schema_version=1,
        allowed_tools='["read", "search", "edit", "test"]',
        policy='{"network": false}',
        budgets='{"max_iterations": 40, "timeout_seconds": 1800}',
    )
    db.add(row)
    db.commit()
    return row


def _secure_fixture(db, monkeypatch):
    """Seed active deploy credential and mock ready settings.

    ``create_code_run`` requires a published Git config, a visible credential,
    and a running bound Sandbox. The actual commit is resolved later by runtime
    Git sync inside that Sandbox.
    """
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
    monkeypatch.setattr(
        "app.services.docker_service.sync_container_status",
        lambda _container_id: "running",
    )


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
    _manifest(db, requested_ref="")
    with pytest.raises(ManifestUnavailableError, match="security_republish_required"):
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
    assert frozen_contract["repository"] == "ssh://git.internal/example/repo.git"
    assert frozen_contract["base_commit"] == "main"
    assert frozen_contract["source_id"] == "source1"
    assert frozen_contract["source_type"] == "ssh"
    assert frozen_contract["credential_ref"] == "deploy-token-ref"
    assert frozen_contract["requested_ref"] == "main"
    assert frozen_contract["resolved_commit"] == ""
    assert frozen_contract["snapshot_id"] == ""
    assert frozen_contract["snapshot_hash"] == ""
    assert frozen_contract["source_scan_report_id"] == ""
    assert frozen_contract["security_schema_version"] == 1
    assert run.source_id == frozen_contract["source_id"]
    assert run.resolved_commit == frozen_contract["resolved_commit"]
    assert run.snapshot_id == frozen_contract["snapshot_id"]
    assert run.snapshot_hash == frozen_contract["snapshot_hash"]
    assert run.image == "internal/python:3.12"
    assert run.image_digest == ""
    assert run.security_schema_version == 1
    assert frozen_contract["allowed_paths"] == ["**"]
    assert frozen_contract["coding_runtime"] == "legacy"
    assert frozen_contract["allowed_skills"] == []
    assert frozen_contract["authorized_mcp_servers"] == []
    assert frozen_contract["runtime_budgets"] == {"max_verifier_retries": 2}
    assert frozen_contract["model_config"] == {"model_ref": "", "provider": "agent_llm"}
    effective_policy = json.loads(run.effective_policy)
    assert effective_policy["allowed_paths"] == ["**"]
    assert effective_policy["allowed_source_types"] == ["ssh"]
    assert effective_policy["allowed_sources"] == ["source1"]
    assert effective_policy["allowed_tools"] == [
        "edit",
        "git_read",
        "read",
        "search",
        "shell",
        "test",
    ]
    assert effective_policy["coding_runtime"] == "legacy"
    assert "model_config" not in effective_policy
    assert effective_policy["network"] is True
    assert effective_policy["policy_sources"]["network"] == "sandbox_binding"
    assert effective_policy["policy_sources"]["coding_runtime"] == "agent_profile"
    assert run.effective_policy_hash == hashlib.sha256(
        run.effective_policy.encode("utf-8")
    ).hexdigest()

    manifest.allowed_paths = '["different/"]'
    manifest.snapshot_id = "snapshot2"
    manifest.resolved_commit = "d" * 40
    db.commit()
    assert json.loads(run.task_contract)["allowed_paths"] == ["**"]
    assert run.snapshot_id == ""
    assert run.resolved_commit == ""


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
    assert frozen["credential_ref"] == manifest.credential_ref
    assert frozen["requested_ref"] == manifest.requested_ref
    assert frozen["snapshot_id"] == ""
    assert frozen["resolved_commit"] == ""
    assert run.image == "internal/python:3.12"
    assert run.image_digest == ""

    manifest.snapshot_id = "changed-after-publish"
    manifest.resolved_commit = "e" * 40
    db.commit()
    assert run.snapshot_id == ""
    assert run.resolved_commit == ""


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
