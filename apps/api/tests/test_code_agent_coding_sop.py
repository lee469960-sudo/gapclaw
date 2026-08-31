"""Coding SOP: baked skills, grill gate, and credentials."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from fastapi import BackgroundTasks
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import (
    Agent,
    ChatMessage,
    CodeAgentRun,
    CodeControlAudit,
    CodeDeployCredential,
    CodeProject,
    CodeProjectManifest,
    CodeSourceSnapshot,
    LLMResource,
)
from app.routers.agent_chat import ChatBody, chat_post
from app.security import encrypt_secret
from app.services.code_agent.claude_code_runtime import (
    ClaudeCodeRuntimeAdapter,
    materialize_coding_sop,
    resolve_claude_code_exec_env,
)
import app.services.code_agent.control_plane as control_plane
from tests.test_code_agent_claude_code_runtime import FakeClaudeRunner, _runtime_input


def test_materialize_coding_sop_writes_skills_and_claude_md(tmp_path):
    written = materialize_coding_sop(str(tmp_path))
    assert ".claude/CLAUDE.md" in written
    claude = (tmp_path / ".claude" / "CLAUDE.md").read_text()
    assert "Do not run `/opsx:archive`" in claude
    assert "grill" not in claude.lower() or "Do not grill" in claude
    for slug in (
        "openspec-propose",
        "openspec-apply-change",
        "openspec-verify-change",
        "openspec-archive-change",
        "planning-with-files",
    ):
        skill = tmp_path / ".claude" / "skills" / slug / "SKILL.md"
        assert skill.is_file(), slug
    assert not (tmp_path / ".claude" / "skills" / "grill-me").exists()


def test_claude_command_keeps_secret_out_of_argv():
    secret = "sk-" + "d" * 20
    runner = FakeClaudeRunner()
    adapter = ClaudeCodeRuntimeAdapter(runner=runner)
    adapter.run(_runtime_input(exec_env={"ANTHROPIC_API_KEY": secret}))
    command = runner.calls[0][1]
    assert secret not in command
    assert "ANTHROPIC_API_KEY" not in command


def test_resolve_claude_code_exec_env_fail_closed_without_key():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    db.add(Agent(id="agent1", name="Code", llm_id=""))
    db.commit()
    env, reason = resolve_claude_code_exec_env(db, SimpleNamespace(agent_id="agent1"))
    assert env == {}
    assert reason == "llm_not_configured"


def test_resolve_claude_code_exec_env_rejects_llm_group_with_specific_reason():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    db.add(LLMResource(
        id="group1",
        type="group",
        name="Mixed Group",
        members='["llm1"]',
    ))
    db.add(Agent(id="agent1", name="Code", llm_id="group1"))
    db.commit()

    env, reason = resolve_claude_code_exec_env(db, SimpleNamespace(agent_id="agent1"))

    assert env == {}
    assert reason == "llm_group_not_supported"


def test_resolve_claude_code_exec_env_rejects_missing_model_with_specific_reason():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    db.add(LLMResource(
        id="llm1",
        type="llm",
        name="Claude",
        provider="anthropic",
        api_key_enc=encrypt_secret("sk-live-secret-value"),
        model="",
    ))
    db.add(Agent(id="agent1", name="Code", llm_id="llm1"))
    db.commit()

    env, reason = resolve_claude_code_exec_env(db, SimpleNamespace(agent_id="agent1"))

    assert env == {}
    assert reason == "llm_model_missing"


def test_resolve_claude_code_exec_env_uses_bound_llm():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    secret = "sk-live-secret-value"
    db.add(LLMResource(
        id="llm1",
        type="llm",
        name="Claude",
        provider="anthropic",
        base_url="https://api.example.test",
        api_key_enc=encrypt_secret(secret),
        model="claude-sonnet-4",
    ))
    db.add(Agent(id="agent1", name="Code", llm_id="llm1"))
    db.commit()
    env, reason = resolve_claude_code_exec_env(db, SimpleNamespace(agent_id="agent1"))
    assert reason == ""
    assert env["ANTHROPIC_API_KEY"] == secret
    assert env["ANTHROPIC_BASE_URL"] == "https://api.example.test"
    assert env["PIP_DEFAULT_TIMEOUT"] == "15"
    assert env["PIP_RETRIES"] == "0"
    assert env["GIT_TERMINAL_PROMPT"] == "0"


def _db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _actor():
    return SimpleNamespace(username="admin", roles='["admin"]', organization_id="default")


def _claude_project(db, monkeypatch, *, enabled=True):
    db.add(CodeProject(id="project1", name="Project", creator="admin"))
    db.add(Agent(
        id="agent1",
        name="Code Agent",
        profile="code",
        code_project_id="project1",
    ))
    db.add(CodeProjectManifest(
        id="manifest1",
        project_id="project1",
        version=1,
        status="published",
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
        allowed_paths='["src/"]',
        validation_plan='[{"command": "pytest -q"}]',
        trusted_image="internal/python:3.12",
        image_digest="sha256:" + "c" * 64,
        security_schema_version=1,
        allowed_tools='["read", "search", "edit", "test"]',
        policy='{"network": false, "coding_runtime": "claude_code"}',
        budgets='{"max_iterations": 40, "timeout_seconds": 1800}',
    ))
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
        code_claude_code_runtime_enabled = enabled

        def code_agent_security_readiness(self):
            return {"ready": True, "status": "ready", "reason": "ready", "errors": {}}

    monkeypatch.setattr(control_plane, "get_settings", lambda: ReadySettings())


def test_claude_code_first_message_creates_run_without_prefix(monkeypatch):
    db = _db()
    _claude_project(db, monkeypatch, enabled=True)
    tasks = BackgroundTasks()
    response = asyncio.run(chat_post(
        ChatBody(action="submit_chat", agent_id="agent1", session_id="s1", message="把 IP 改了"),
        tasks,
        action=None,
        user=SimpleNamespace(username="admin", roles='["operator"]'),
        db=db,
    ))
    assert response["code"] == 0
    assert response["data"]["status"] == "pending"
    assert db.query(CodeAgentRun).count() == 1
    assert tasks.tasks[0].func.__name__ == "_enqueue_code_chat"
    run = db.get(CodeAgentRun, response["data"]["code_run_id"])
    assert json.loads(run.task_contract)["objective"] == "把 IP 改了"


def test_claude_code_direct_execute_creates_run_without_confirmation(monkeypatch):
    db = _db()
    _claude_project(db, monkeypatch, enabled=True)
    tasks = BackgroundTasks()
    trigger = "开始执行:请修改 local 配置"
    response = asyncio.run(chat_post(
        ChatBody(action="submit_chat", agent_id="agent1", session_id="s1", message=trigger),
        tasks,
        action=None,
        user=SimpleNamespace(username="admin", roles='["operator"]'),
        db=db,
    ))

    assert response["code"] == 0
    assert response["data"]["status"] == "pending"
    assert tasks.tasks[0].func.__name__ == "_enqueue_code_chat"
    assert tasks.tasks[0].args[-1] == response["data"]["code_run_id"]
    run = db.get(CodeAgentRun, response["data"]["code_run_id"])
    contract = json.loads(run.task_contract)
    assert contract["objective"] == "请修改 local 配置"
    assert contract["coding_runtime"] == "claude_code"
    assert json.loads(run.runner_facts)["task_entry"]["trigger_text"] == trigger
    audit = db.query(CodeControlAudit).filter(CodeControlAudit.action == "run_create").one()
    assert json.loads(audit.details)["trigger_text"] == trigger


def test_claude_code_direct_execute_rejects_empty_objective(monkeypatch):
    db = _db()
    _claude_project(db, monkeypatch, enabled=True)
    tasks = BackgroundTasks()
    response = asyncio.run(chat_post(
        ChatBody(action="submit_chat", agent_id="agent1", session_id="s1", message="开始执行:   "),
        tasks,
        action=None,
        user=SimpleNamespace(username="admin", roles='["operator"]'),
        db=db,
    ))

    assert response["code"] == 1
    assert response["msg"] == "缺少任务目标"
    assert db.query(CodeAgentRun).count() == 0
    assert tasks.tasks == []


def test_claude_code_confirmation_text_is_treated_as_normal_objective(monkeypatch):
    db = _db()
    _claude_project(db, monkeypatch, enabled=True)
    response = asyncio.run(chat_post(
        ChatBody(action="submit_chat", agent_id="agent1", session_id="s1", message="开始实现"),
        BackgroundTasks(),
        action=None,
        user=SimpleNamespace(username="admin", roles='["operator"]'),
        db=db,
    ))
    assert response["code"] == 0
    assert response["data"]["status"] == "pending"
    run = db.get(CodeAgentRun, response["data"]["code_run_id"])
    contract = json.loads(run.task_contract)
    assert contract["objective"] == "开始实现"
    assert contract["coding_runtime"] == "claude_code"


def test_legacy_still_creates_run_on_first_message(monkeypatch):
    db = _db()
    _claude_project(db, monkeypatch, enabled=False)
    response = asyncio.run(chat_post(
        ChatBody(action="submit_chat", agent_id="agent1", session_id="s1", message="Fix"),
        BackgroundTasks(),
        action=None,
        user=SimpleNamespace(username="admin", roles='["operator"]'),
        db=db,
    ))
    assert response["code"] == 0
    assert response["data"]["status"] == "pending"
    assert db.query(CodeAgentRun).count() == 1


def test_legacy_code_agent_keeps_direct_execute_as_normal_objective(monkeypatch):
    db = _db()
    _claude_project(db, monkeypatch, enabled=False)
    response = asyncio.run(chat_post(
        ChatBody(action="submit_chat", agent_id="agent1", session_id="s1", message="开始执行:Fix"),
        BackgroundTasks(),
        action=None,
        user=SimpleNamespace(username="admin", roles='["operator"]'),
        db=db,
    ))

    assert response["code"] == 0
    run = db.query(CodeAgentRun).one()
    assert json.loads(run.task_contract)["objective"] == "开始执行:Fix"


def test_standard_agent_keeps_direct_execute_on_standard_chat_path():
    db = _db()
    db.add(Agent(id="standard1", name="Standard", profile="standard"))
    db.commit()
    tasks = BackgroundTasks()
    response = asyncio.run(chat_post(
        ChatBody(action="submit_chat", agent_id="standard1", session_id="s1", message="开始执行:Fix"),
        tasks,
        action=None,
        user=SimpleNamespace(username="admin", roles="[]"),
        db=db,
    ))

    assert response["code"] == 0
    assert response["data"]["status"] == "started"
    assert db.query(CodeAgentRun).count() == 0
    assert tasks.tasks[0].func.__name__ == "_run_chat_bg"
