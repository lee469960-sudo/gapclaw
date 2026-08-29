"""OpenSpec task 2.1: Profile compilation stays inside the unified runtime."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import Agent, CodeAgentRun, CodeControlAudit, LLMResource, MCP, Skill
from app.security import encrypt_secret
from app.services.agent_runtime.runtime import run_agent
from app.services.agent_runtime.hub import hub, stop_chat
from app.services.code_agent.lifecycle import CodeCleanupError, register_code_cleanup
from app.services.code_agent.workspace import (
    WorkspaceFacts,
    WorkspaceManager,
    WorkspacePreparationError,
)
from app.services.code_agent.runner import (
    CodeContainerRunner,
    RunnerCommandTimeout,
    RunnerFacts,
    RunnerUnavailableError,
)
from app.services.code_agent.artifacts import CodeArtifactSealer, SealingResult
from app.services.code_agent.claude_code_runtime import ClaudeCodePreflightResult, ClaudeCodeRuntimeResult
from app.services.code_agent.verifier import CodeVerifier, VerificationReport


_REAL_WORKSPACE_PREPARE = WorkspaceManager.prepare


def _db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


@pytest.fixture(autouse=True)
def _fake_workspace(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "app.services.code_agent.scanner.validate_source_scan_report",
        lambda _db, _run: None,
    )

    def _prepare(_manager, run):
        facts = WorkspaceFacts(
            path=str(tmp_path / run.id),
            repository=run.repository,
            requested_commit=run.base_commit,
            resolved_commit=run.base_commit,
        )
        run.workspace_path = facts.path
        run.source_facts = json.dumps(facts.to_dict(), sort_keys=True)
        return facts

    monkeypatch.setattr(WorkspaceManager, "prepare", _prepare)
    monkeypatch.setattr(WorkspaceManager, "retain_after_run", lambda self, run: None)
    monkeypatch.setattr(CodeContainerRunner, "__init__", lambda self: None)
    monkeypatch.setattr(CodeContainerRunner, "start", lambda self, run, workspace: RunnerFacts(
        container_id="container1", image=run.image or "test:image", image_id="sha256:test",
        network_mode="none", cpu_count=2, memory_mb=512,
    ))
    monkeypatch.setattr(CodeVerifier, "verify", lambda self: VerificationReport(
        passed=True,
        outcome="execution_completed",
        reason="",
        changed_paths=(),
        checks=("test_fixture",),
        tests=(),
    ))
    monkeypatch.setattr(CodeVerifier, "capture_baseline", lambda self, manager: {
        "status": "passed", "reason": "", "tests": [],
    })
    monkeypatch.setattr(CodeArtifactSealer, "seal", lambda self: SealingResult(
        sealed=True, outcome="execution_completed", reason="",
    ))


def _code_run(db, *, with_skill: bool = False):
    skills = '["skill1"]' if with_skill else "[]"
    if with_skill:
        db.add(Skill(id="skill1", name="dbt-clickhouse-gamestat"))
    db.add(LLMResource(
        id="llm1",
        name="Claude",
        provider="anthropic",
        api_key_enc=encrypt_secret("sk-test-runtime"),
        model="claude-sonnet",
    ))
    db.add(Agent(
        id="code",
        name="Code",
        profile="code",
        skills=skills,
        allowed_actions='["shell"]',
        llm_id="llm1",
    ))
    db.add(Agent(id="standard", name="Standard", allowed_actions='["shell"]'))
    db.add(CodeAgentRun(
        id="run1", agent_id="code", project_id="p1", manifest_id="m1", manifest_version=1,
        repository="ssh://git.internal/repo.git", base_commit="a" * 40,
        task_contract=json.dumps({"objective": "Fix test"}),
        effective_policy=json.dumps({
            "network": False,
            "allowed_tools": ["read", "search", "edit", "test"],
            "budgets": {"timeout_seconds": 1800, "max_tool_calls": 200},
        }), status="pending",
    ))
    db.commit()


def test_code_profile_compiles_immutable_context_and_drops_standard_tool_grants():
    db = _db()
    _code_run(db)

    async def _run():
        with patch("app.services.agent_runtime.runtime.AgentRuntime.run", new=AsyncMock(return_value="ok")) as execute, patch(
            "app.services.code_agent.claude_code_runtime.materialize_coding_sop"
        ) as sop:
            result = await run_agent(db, db.get(Agent, "code"), "s1", "Fix test", code_run_id="run1")
        sop.assert_not_called()
        ctx = execute.await_args.args[0]
        assert ctx.profile == "code"
        assert ctx.code_execution is not None
        assert ctx.code_execution.run_id == "run1"
        assert set(ctx.allowed_actions) == {"code_read", "code_search", "code_edit", "code_test"}
        assert ctx.tool_executor is not None
        with pytest.raises(Exception):
            ctx.code_execution.run_id = "other"
        return result

    assert asyncio.run(_run()) == "ok"


def test_code_profile_retains_bound_skills_as_context_capabilities():
    db = _db()
    _code_run(db, with_skill=True)

    async def _run():
        with patch("app.services.agent_runtime.runtime.AgentRuntime.run", new=AsyncMock(return_value="ok")) as execute:
            result = await run_agent(db, db.get(Agent, "code"), "s1", "Fix test", code_run_id="run1")
        ctx = execute.await_args.args[0]
        assert ctx.profile == "code"
        assert ctx.skill_ids == ["skill1"]
        assert ctx.skill_names == ["dbt-clickhouse-gamestat"]
        assert {name for name, _md in ctx.skill_mds} == {"dbt-clickhouse-gamestat"}
        assert "skill_read_md" in ctx.allowed_actions
        assert "skill_run_script" in ctx.allowed_actions
        assert "mcp_tool_call" not in ctx.allowed_actions
        return result

    assert asyncio.run(_run()) == "ok"


def test_claude_code_preflight_uses_existing_runner_container_and_workspace(monkeypatch):
    db = _db()
    _code_run(db, with_skill=True)
    db.add(MCP(id="mcp1", name="dbt-mcp", command="python", command_args='["mcp.py"]'))
    run = db.get(CodeAgentRun, "run1")
    run.task_contract = json.dumps({
        "objective": "Fix test",
        "coding_runtime": "claude_code",
        "model_config": {"provider": "cloud_claude", "model_ref": "claude-sonnet"},
        "allowed_skills": ["skill1"],
        "authorized_mcp_servers": ["mcp1"],
    })
    policy = json.loads(run.effective_policy)
    policy["coding_runtime"] = "claude_code"
    policy["model_config"] = {"provider": "cloud_claude", "model_ref": "claude-sonnet"}
    run.effective_policy = json.dumps(policy)
    db.commit()
    start_calls = []
    exec_calls = []
    startup_order = []

    def _start(_self, code_run, workspace):
        startup_order.append("runner_start")
        start_calls.append((code_run.id, workspace.path))
        return RunnerFacts(
            container_id="container1",
            image=code_run.image or "test:image",
            image_id="sha256:test",
            network_mode="none",
            cpu_count=2,
            memory_mb=512,
            workspace_mount="/workspace",
        )

    def _exec(_self, container_id, command, *, timeout_seconds, environment=None):
        exec_calls.append((container_id, command, timeout_seconds))
        if command == "claude --version":
            return 0, "Claude Code 2.1.246\n"
        return 0, ""

    monkeypatch.setattr(CodeContainerRunner, "start", _start)
    monkeypatch.setattr(CodeContainerRunner, "exec", _exec)
    events = []

    async def _collect(event):
        events.append(event)

    async def _run():
        hub.subscribe("code:s1", _collect)
        try:
            with patch("subprocess.run", side_effect=AssertionError("host subprocess not allowed")), patch(
                "app.services.code_agent.claude_code_runtime.materialize_coding_sop",
                side_effect=lambda _path: startup_order.append("sop_materialized"),
            ), patch(
                "app.services.agent_runtime.runtime.AgentRuntime.run", new=AsyncMock(return_value="ok")
            ) as legacy_run, patch(
                "app.services.code_agent.claude_code_runtime.ClaudeCodeRuntimeAdapter.run",
                return_value=ClaudeCodeRuntimeResult(
                    status="coding_completed",
                    exit_code=0,
                    summary="ok",
                ),
            ):
                result = await run_agent(db, db.get(Agent, "code"), "s1", "Fix test", code_run_id="run1")
                legacy_run.assert_not_awaited()
                return result
        finally:
            hub.unsubscribe("code:s1", _collect)

    assert asyncio.run(_run()) == "ok"
    assert len(start_calls) == 1
    assert startup_order.index("sop_materialized") < startup_order.index("runner_start")
    assert all(container_id == "container1" for container_id, _command, _timeout in exec_calls)
    assert exec_calls[0][1] == "claude --version"
    assert any(
        command == "mkdir -p /workspace/.claude && test -d /workspace/.claude && test -w /workspace/.claude"
        for _container_id, command, _timeout in exec_calls
    )
    assert any(
        command == "test -d /workspace && test -r /workspace && test -w /workspace && touch /workspace/.code-agent-preflight-write && rm -f /workspace/.code-agent-preflight-write"
        for _container_id, command, _timeout in exec_calls
    )
    facts = json.loads(db.get(CodeAgentRun, "run1").runner_facts)
    assert facts["claude_code_preflight"]["passed"] is True
    assert facts["claude_code_preflight"]["claude_code_version"] == "Claude Code 2.1.246"
    assert facts["claude_code_skills"]["events"][0]["type"] == "skill_loaded"
    assert facts["claude_code_skills"]["skills"][0]["name"] == "dbt-clickhouse-gamestat"
    phases = [
        event["profile"]["phase"]
        for event in events
        if event.get("type") == "profile" and event.get("profile", {}).get("run_id") == "run1"
    ]
    assert "runtime_started" in phases
    assert "skill_loaded" in phases
    assert "mcp_loaded" in phases


def test_standard_profile_does_not_receive_code_context_or_change_run_signature():
    db = _db()
    _code_run(db)

    async def _run():
        with patch("app.services.agent_runtime.runtime.AgentRuntime.run", new=AsyncMock(return_value="ok")) as execute:
            result = await run_agent(db, db.get(Agent, "standard"), "s1", "hello")
        ctx = execute.await_args.args[0]
        assert ctx.profile == "standard"
        assert ctx.code_execution is None
        assert ctx.allowed_actions == ["shell"]
        return result

    assert asyncio.run(_run()) == "ok"


def test_code_profile_requires_a_pending_matching_run():
    db = _db()
    _code_run(db)
    with pytest.raises(ValueError, match="code_run_required"):
        asyncio.run(run_agent(db, db.get(Agent, "code"), "s1", "Fix test"))
    with pytest.raises(ValueError, match="code_run_not_allowed_for_standard_profile"):
        asyncio.run(run_agent(db, db.get(Agent, "standard"), "s1", "hello", code_run_id="run1"))


def test_code_profile_events_keep_common_envelope_with_versioned_payload():
    db = _db()
    _code_run(db)
    events = []

    async def _collect(event):
        events.append(event)

    async def _run():
        hub.subscribe("code:s1", _collect)
        with patch("app.services.agent_runtime.runtime.AgentRuntime._save_user_message"), patch(
            "app.services.agent_runtime.runtime.AgentRuntime._publish_inbound_events", new=AsyncMock()
        ), patch("app.services.agent_runtime.runtime.AgentRuntime._is_conversational", return_value=False), patch(
            "app.services.agent_runtime.runtime.AgentRuntime._run_modular", new=AsyncMock(return_value=("ok", [], [], 0, 100))
        ), patch(
            "app.services.agent_runtime.runtime.AgentRuntime._save_assistant_message"
        ), patch("app.services.agent_runtime.runtime.AgentRuntime._publish_modular_done", new=AsyncMock()):
            await run_agent(db, db.get(Agent, "code"), "s1", "Fix test", code_run_id="run1")
        hub.unsubscribe("code:s1", _collect)

    asyncio.run(_run())
    payloads = [event["profile"] for event in events if event.get("type") == "profile"]
    assert [payload["phase"] for payload in payloads] == [
        "verify_baseline", "verify_baseline", "prepare", "terminate",
        "verify", "verify", "seal", "seal", "cleanup",
    ]
    assert all(payload["version"] == 1 and payload["run_id"] == "run1" for payload in payloads)
    persisted = json.loads(db.get(CodeAgentRun, "run1").runner_facts)["code_profile_events"]
    assert [event["phase"] for event in persisted] == [payload["phase"] for payload in payloads]
    assert [event["sequence"] for event in persisted] == list(range(len(persisted)))


def test_code_runtime_always_runs_registered_cleanup_and_records_completion():
    db = _db()
    _code_run(db)
    cleaned = []
    register_code_cleanup("run1", lambda: cleaned.append("done"))

    async def _run():
        with patch("app.services.agent_runtime.runtime.AgentRuntime.run", new=AsyncMock(return_value="ok")):
            return await run_agent(db, db.get(Agent, "code"), "s1", "Fix test", code_run_id="run1")

    assert asyncio.run(_run()) == "ok"
    assert cleaned == ["done"]
    assert db.get(CodeAgentRun, "run1").status == "execution_completed"


def test_model_final_cannot_bypass_failed_authoritative_verifier():
    db = _db()
    _code_run(db)
    verifier = MagicMock()
    verifier.capture_baseline.return_value = {"status": "passed", "reason": "", "tests": []}
    verifier.verify = lambda: VerificationReport(
        passed=False,
        outcome="verification_failed",
        reason="validation_failed",
        changed_paths=("src/app.py",),
        checks=("validation_plan",),
        tests=({"test_index": 0, "exit_code": 1},),
    )

    with patch(
        "app.services.agent_runtime.runtime.AgentRuntime.run",
        new=AsyncMock(return_value="FINAL: tests passed"),
    ):
        result = asyncio.run(run_agent(
            db,
            db.get(Agent, "code"),
            "s1",
            "Fix test",
            code_run_id="run1",
            code_verifier=verifier,
        ))

    assert result == "FINAL: tests passed"
    run = db.get(CodeAgentRun, "run1")
    assert run.status == "verification_failed"
    assert run.failure_reason == "verification_failed"


def test_claude_code_completed_cannot_bypass_failed_authoritative_verifier(monkeypatch):
    db = _db()
    _code_run(db)
    run = db.get(CodeAgentRun, "run1")
    run.task_contract = json.dumps({
        "objective": "Fix test",
        "coding_runtime": "claude_code",
        "model_config": {"provider": "cloud_claude", "model_ref": "claude-sonnet"},
        "runtime_budgets": {"max_verifier_retries": 0},
    })
    policy = json.loads(run.effective_policy)
    policy["coding_runtime"] = "claude_code"
    policy["model_config"] = {"provider": "cloud_claude", "model_ref": "claude-sonnet"}
    policy["runtime_budgets"] = {"max_verifier_retries": 0}
    run.effective_policy = json.dumps(policy)
    db.commit()

    def _exec(_self, _container_id, command, *, timeout_seconds, environment=None):
        if command == "claude --version":
            return 0, "Claude Code 2.1.246\n"
        return 0, ""

    monkeypatch.setattr(CodeContainerRunner, "exec", _exec)
    verifier = MagicMock()
    verifier.capture_baseline.return_value = {"status": "passed", "reason": "", "tests": []}
    verifier.verify.return_value = VerificationReport(
        passed=False,
        outcome="verification_failed",
        reason="new_validation_failure",
        changed_paths=("models/a.sql",),
        checks=("validation_plan",),
        tests=({"command": "pytest", "exit_code": 1},),
    )
    sealer = MagicMock()

    with patch(
        "app.services.agent_runtime.runtime.AgentRuntime.run",
        new=AsyncMock(return_value="legacy runtime must not run"),
    ) as legacy_run, patch(
        "app.services.code_agent.claude_code_runtime.ClaudeCodeRuntimeAdapter.run",
        return_value=ClaudeCodeRuntimeResult(
            status="coding_completed",
            exit_code=0,
            summary="done",
            changed_files=("models/a.sql",),
        ),
    ) as claude_run, patch(
        "app.services.code_agent.claude_code_runtime.archive_openspec_after_seal",
        return_value={"archived": ["must-not-run"], "reason": ""},
    ) as archive:
        result = asyncio.run(run_agent(
            db,
            db.get(Agent, "code"),
            "s1",
            "Fix test",
            code_run_id="run1",
            code_verifier=verifier,
            code_artifact_sealer=sealer,
        ))

    assert result == "done"
    legacy_run.assert_not_awaited()
    claude_run.assert_called_once()
    verifier.verify.assert_called_once()
    sealer.seal.assert_not_called()
    archive.assert_not_called()
    run = db.get(CodeAgentRun, "run1")
    assert run.status == "verification_failed"
    assert run.failure_reason == "verification_failed"
    assert run.status != "patch_ready"


def test_claude_code_verifier_failure_retries_same_session_with_redacted_feedback(monkeypatch):
    db = _db()
    _code_run(db)
    run = db.get(CodeAgentRun, "run1")
    run.task_contract = json.dumps({
        "objective": "Fix dbt model",
        "coding_runtime": "claude_code",
        "model_config": {"provider": "cloud_claude", "model_ref": "claude-sonnet"},
        "runtime_budgets": {"max_verifier_retries": 1},
    })
    policy = json.loads(run.effective_policy)
    policy["coding_runtime"] = "claude_code"
    policy["model_config"] = {"provider": "cloud_claude", "model_ref": "claude-sonnet"}
    policy["runtime_budgets"] = {"max_verifier_retries": 1}
    run.effective_policy = json.dumps(policy)
    db.commit()

    def _exec(_self, _container_id, command, *, timeout_seconds, environment=None):
        if command == "claude --version":
            return 0, "Claude Code 2.1.246\n"
        return 0, ""

    monkeypatch.setattr(CodeContainerRunner, "exec", _exec)
    secret = "sk-" + "b" * 20
    verifier = MagicMock()
    verifier.capture_baseline.return_value = {"status": "passed", "reason": "", "tests": []}
    verifier.verify.side_effect = [
        VerificationReport(
            passed=False,
            outcome="verification_failed",
            reason="new_validation_failure",
            changed_paths=("models/a.sql",),
            checks=("validation_plan",),
            tests=({
                "test_index": 0,
                "command": "dbt test",
                "exit_code": 1,
                "output": f"password={secret}",
                "failure_classification": "new_failure",
            },),
        ),
        VerificationReport(
            passed=True,
            outcome="verification_passed",
            reason="",
            changed_paths=("models/a.sql",),
            checks=("validation_plan",),
            tests=({"test_index": 0, "command": "dbt test", "exit_code": 0},),
            secret_scan_complete=True,
        ),
    ]
    sealer = MagicMock()
    sealer.seal.return_value = SealingResult(
        sealed=True,
        outcome="patch_ready",
        reason="",
        artifact_id="artifact1",
        diff_hash="d" * 64,
    )
    runtime_inputs = []

    def _claude_run(_adapter, runtime_input):
        runtime_inputs.append(runtime_input)
        if len(runtime_inputs) == 1:
            return ClaudeCodeRuntimeResult(
                status="coding_completed",
                exit_code=0,
                summary="initial done",
                changed_files=("models/a.sql",),
            )
        return ClaudeCodeRuntimeResult(
            status="coding_completed",
            exit_code=0,
            summary="fixed",
            changed_files=("models/a.sql",),
        )

    events = []

    async def _collect(event):
        events.append(event)

    async def _run():
        hub.subscribe("code:s1", _collect)
        try:
            with patch(
                "app.services.code_agent.claude_code_runtime.ClaudeCodeRuntimeAdapter.run",
                new=_claude_run,
            ):
                return await run_agent(
                    db,
                    db.get(Agent, "code"),
                    "s1",
                    "Fix test",
                    code_run_id="run1",
                    code_verifier=verifier,
                    code_artifact_sealer=sealer,
                )
        finally:
            hub.unsubscribe("code:s1", _collect)

    assert asyncio.run(_run()) == "fixed"
    assert len(runtime_inputs) == 2
    assert runtime_inputs[0].retry_attempt == 0
    assert runtime_inputs[1].retry_attempt == 1
    assert runtime_inputs[0].session_name == runtime_inputs[1].session_name
    assert runtime_inputs[0].container_id == runtime_inputs[1].container_id == "container1"
    assert runtime_inputs[0].repository_cwd == runtime_inputs[1].repository_cwd == "/workspace"
    assert dict(runtime_inputs[0].effective_policy) == dict(runtime_inputs[1].effective_policy)
    assert secret not in runtime_inputs[1].verifier_feedback
    assert "[REDACTED:SECRET]" in runtime_inputs[1].verifier_feedback
    assert verifier.verify.call_count == 2
    sealer.seal.assert_called_once()
    run = db.get(CodeAgentRun, "run1")
    assert run.status == "patch_ready"
    retry_events = [
        event["profile"]
        for event in events
        if event.get("type") == "profile"
        and event.get("profile", {}).get("phase") == "verifier_failed_retrying"
    ]
    assert retry_events[0]["retry_attempt"] == 1
    assert retry_events[0]["max_retries"] == 1
    assert secret not in json.dumps(retry_events[0], sort_keys=True)
    runtime_events = [
        event["profile"]
        for event in events
        if event.get("type") == "profile"
        and event.get("profile", {}).get("phase") == "runtime_result"
    ]
    assert runtime_events[0]["status"] == "started"
    assert runtime_events[1]["status"] == "completed"


def test_claude_code_verifier_retry_exhaustion_stops_after_initial_plus_two(monkeypatch):
    db = _db()
    _code_run(db)
    run = db.get(CodeAgentRun, "run1")
    run.task_contract = json.dumps({
        "objective": "Fix dbt model",
        "coding_runtime": "claude_code",
        "model_config": {"provider": "cloud_claude", "model_ref": "claude-sonnet"},
        "runtime_budgets": {"max_verifier_retries": 99},
    })
    policy = json.loads(run.effective_policy)
    policy["coding_runtime"] = "claude_code"
    policy["model_config"] = {"provider": "cloud_claude", "model_ref": "claude-sonnet"}
    policy["runtime_budgets"] = {"max_verifier_retries": 99}
    run.effective_policy = json.dumps(policy)
    db.commit()

    def _exec(_self, _container_id, command, *, timeout_seconds, environment=None):
        if command == "claude --version":
            return 0, "Claude Code 2.1.246\n"
        return 0, ""

    monkeypatch.setattr(CodeContainerRunner, "exec", _exec)
    verifier = MagicMock()
    verifier.capture_baseline.return_value = {"status": "passed", "reason": "", "tests": []}
    verifier.verify.return_value = VerificationReport(
        passed=False,
        outcome="verification_failed",
        reason="new_validation_failure",
        changed_paths=("models/a.sql",),
        checks=("validation_plan",),
        tests=({"test_index": 0, "command": "dbt test", "exit_code": 1},),
    )
    sealer = MagicMock()
    runtime_inputs = []

    def _claude_run(_adapter, runtime_input):
        runtime_inputs.append(runtime_input)
        return ClaudeCodeRuntimeResult(
            status="coding_completed",
            exit_code=0,
            summary=f"attempt {runtime_input.retry_attempt}",
            changed_files=("models/a.sql",),
        )

    with patch(
        "app.services.code_agent.claude_code_runtime.ClaudeCodeRuntimeAdapter.run",
        new=_claude_run,
    ):
        assert asyncio.run(run_agent(
            db,
            db.get(Agent, "code"),
            "s1",
            "Fix test",
            code_run_id="run1",
            code_verifier=verifier,
            code_artifact_sealer=sealer,
        )) == "attempt 2"

    assert [item.retry_attempt for item in runtime_inputs] == [0, 1, 2]
    assert verifier.verify.call_count == 3
    sealer.seal.assert_not_called()
    run = db.get(CodeAgentRun, "run1")
    assert run.status == "verification_failed"
    assert run.failure_reason == "verification_failed"


def test_claude_code_budget_exhausted_verifier_result_does_not_retry(monkeypatch):
    db = _db()
    _code_run(db)
    run = db.get(CodeAgentRun, "run1")
    run.task_contract = json.dumps({
        "objective": "Fix dbt model",
        "coding_runtime": "claude_code",
        "model_config": {"provider": "cloud_claude", "model_ref": "claude-sonnet"},
        "runtime_budgets": {"max_verifier_retries": 2},
    })
    policy = json.loads(run.effective_policy)
    policy["coding_runtime"] = "claude_code"
    policy["model_config"] = {"provider": "cloud_claude", "model_ref": "claude-sonnet"}
    policy["runtime_budgets"] = {"max_verifier_retries": 2}
    run.effective_policy = json.dumps(policy)
    db.commit()

    def _exec(_self, _container_id, command, *, timeout_seconds, environment=None):
        if command == "claude --version":
            return 0, "Claude Code 2.1.246\n"
        return 0, ""

    monkeypatch.setattr(CodeContainerRunner, "exec", _exec)
    verifier = MagicMock()
    verifier.capture_baseline.return_value = {"status": "passed", "reason": "", "tests": []}
    verifier.verify.return_value = VerificationReport(
        passed=False,
        outcome="budget_exhausted",
        reason="changed_files_budget_exhausted",
        changed_paths=("models/a.sql",),
        checks=("workspace_integrity",),
        tests=(),
    )
    runtime_inputs = []

    def _claude_run(_adapter, runtime_input):
        runtime_inputs.append(runtime_input)
        return ClaudeCodeRuntimeResult(
            status="coding_completed",
            exit_code=0,
            summary="done",
            changed_files=("models/a.sql",),
        )

    with patch(
        "app.services.code_agent.claude_code_runtime.ClaudeCodeRuntimeAdapter.run",
        new=_claude_run,
    ):
        assert asyncio.run(run_agent(
            db,
            db.get(Agent, "code"),
            "s1",
            "Fix test",
            code_run_id="run1",
            code_verifier=verifier,
        )) == "done"

    assert [item.retry_attempt for item in runtime_inputs] == [0]
    assert verifier.verify.call_count == 1
    run = db.get(CodeAgentRun, "run1")
    assert run.status == "budget_exhausted"
    assert run.failure_reason == "budget_exhausted"


def test_claude_code_workspace_integrity_failure_does_not_retry(monkeypatch):
    db = _db()
    _code_run(db)
    run = db.get(CodeAgentRun, "run1")
    run.task_contract = json.dumps({
        "objective": "Fix dbt model",
        "coding_runtime": "claude_code",
        "model_config": {"provider": "cloud_claude", "model_ref": "claude-sonnet"},
        "runtime_budgets": {"max_verifier_retries": 2},
    })
    policy = json.loads(run.effective_policy)
    policy.update({
        "coding_runtime": "claude_code",
        "model_config": {"provider": "cloud_claude", "model_ref": "claude-sonnet"},
        "runtime_budgets": {"max_verifier_retries": 2},
    })
    run.effective_policy = json.dumps(policy)
    db.commit()

    monkeypatch.setattr(
        "app.services.code_agent.claude_code_runtime.run_claude_code_preflight",
        lambda *_args, **_kwargs: ClaudeCodePreflightResult(passed=True),
    )

    verifier = MagicMock()
    verifier.capture_baseline.return_value = {"status": "passed", "reason": "", "tests": []}
    verifier.verify.return_value = VerificationReport(
        passed=False,
        outcome="workspace_integrity_error",
        reason="workspace_external_write",
        changed_paths=("models/a.sql",),
        checks=("workspace_integrity",),
        tests=(),
    )
    runtime_inputs = []

    def _claude_run(_adapter, runtime_input):
        runtime_inputs.append(runtime_input)
        return ClaudeCodeRuntimeResult(
            status="coding_completed", exit_code=0, summary="done", changed_files=("models/a.sql",)
        )

    with patch(
        "app.services.code_agent.claude_code_runtime.ClaudeCodeRuntimeAdapter.run",
        new=_claude_run,
    ):
        assert asyncio.run(run_agent(
            db, db.get(Agent, "code"), "s1", "Fix test", code_run_id="run1",
            code_verifier=verifier,
        )) == "done"

    assert [item.retry_attempt for item in runtime_inputs] == [0]
    assert verifier.verify.call_count == 1
    run = db.get(CodeAgentRun, "run1")
    assert run.status == "workspace_integrity_error"
    assert run.failure_reason == "workspace_integrity_error"


def test_claude_code_patch_ready_requires_verifier_pass_and_sealer_success(monkeypatch):
    db = _db()
    _code_run(db)
    run = db.get(CodeAgentRun, "run1")
    run.task_contract = json.dumps({
        "objective": "Fix dbt model",
        "coding_runtime": "claude_code",
        "model_config": {"provider": "cloud_claude", "model_ref": "claude-sonnet"},
        "runtime_budgets": {"max_verifier_retries": 0},
    })
    policy = json.loads(run.effective_policy)
    policy["coding_runtime"] = "claude_code"
    policy["model_config"] = {"provider": "cloud_claude", "model_ref": "claude-sonnet"}
    policy["runtime_budgets"] = {"max_verifier_retries": 0}
    run.effective_policy = json.dumps(policy)
    db.commit()

    def _exec(_self, _container_id, command, *, timeout_seconds, environment=None):
        if command == "claude --version":
            return 0, "Claude Code 2.1.246\n"
        return 0, ""

    monkeypatch.setattr(CodeContainerRunner, "exec", _exec)
    verifier = MagicMock()
    verifier.capture_baseline.return_value = {"status": "passed", "reason": "", "tests": []}
    verifier.verify.return_value = VerificationReport(
        passed=True,
        outcome="verification_passed",
        reason="",
        changed_paths=("models/a.sql",),
        checks=("validation_plan",),
        tests=({"test_index": 0, "command": "dbt test", "exit_code": 0},),
        secret_scan_complete=True,
    )
    sealer = MagicMock()
    sealer.seal.return_value = SealingResult(
        sealed=True,
        outcome="patch_ready",
        reason="",
        artifact_id="artifact1",
        diff_hash="d" * 64,
    )
    events = []

    async def _collect(event):
        events.append(event)

    async def _run():
        hub.subscribe("code:s1", _collect)
        try:
            with patch(
                "app.services.code_agent.claude_code_runtime.ClaudeCodeRuntimeAdapter.run",
                return_value=ClaudeCodeRuntimeResult(
                    status="coding_completed",
                    exit_code=0,
                    summary="done",
                    changed_files=("models/a.sql",),
                ),
            ), patch(
                "app.services.code_agent.claude_code_runtime.archive_openspec_after_seal",
                return_value={"archived": ["fix-profiles"], "reason": ""},
            ):
                return await run_agent(
                    db,
                    db.get(Agent, "code"),
                    "s1",
                    "Fix test",
                    code_run_id="run1",
                    code_verifier=verifier,
                    code_artifact_sealer=sealer,
                )
        finally:
            hub.unsubscribe("code:s1", _collect)

    assert asyncio.run(_run()) == "done"
    verifier.verify.assert_called_once()
    sealer.seal.assert_called_once()
    run = db.get(CodeAgentRun, "run1")
    assert run.status == "patch_ready"
    facts = json.loads(run.runner_facts)
    assert facts["claude_code_openspec_archive"] == {
        "archived": ["fix-profiles"],
        "reason": "",
    }
    phases = [
        event["profile"]["phase"]
        for event in events
        if event.get("type") == "profile" and event.get("profile", {}).get("run_id") == "run1"
    ]
    assert "verifier_passed" in phases
    assert "artifact_sealed" in phases


def test_claude_code_sealer_failure_after_verifier_pass_does_not_patch_ready(monkeypatch):
    db = _db()
    _code_run(db)
    run = db.get(CodeAgentRun, "run1")
    run.task_contract = json.dumps({
        "objective": "Fix dbt model",
        "coding_runtime": "claude_code",
        "model_config": {"provider": "cloud_claude", "model_ref": "claude-sonnet"},
        "runtime_budgets": {"max_verifier_retries": 0},
    })
    policy = json.loads(run.effective_policy)
    policy["coding_runtime"] = "claude_code"
    policy["model_config"] = {"provider": "cloud_claude", "model_ref": "claude-sonnet"}
    policy["runtime_budgets"] = {"max_verifier_retries": 0}
    run.effective_policy = json.dumps(policy)
    db.commit()

    def _exec(_self, _container_id, command, *, timeout_seconds, environment=None):
        if command == "claude --version":
            return 0, "Claude Code 2.1.246\n"
        return 0, ""

    monkeypatch.setattr(CodeContainerRunner, "exec", _exec)
    verifier = MagicMock()
    verifier.capture_baseline.return_value = {"status": "passed", "reason": "", "tests": []}
    verifier.verify.return_value = VerificationReport(
        passed=True,
        outcome="verification_passed",
        reason="",
        changed_paths=("models/a.sql",),
        checks=("validation_plan",),
        tests=({"test_index": 0, "command": "dbt test", "exit_code": 0},),
        secret_scan_complete=True,
    )
    sealer = MagicMock()
    sealer.seal.return_value = SealingResult(
        sealed=False,
        outcome="infrastructure_error",
        reason="artifact_store_unavailable",
    )

    with patch(
        "app.services.code_agent.claude_code_runtime.ClaudeCodeRuntimeAdapter.run",
        return_value=ClaudeCodeRuntimeResult(
            status="coding_completed",
            exit_code=0,
            summary="done",
            changed_files=("models/a.sql",),
        ),
    ), patch(
        "app.services.code_agent.claude_code_runtime.archive_openspec_after_seal",
        return_value={"archived": ["must-not-run"], "reason": ""},
    ) as archive:
        assert asyncio.run(run_agent(
            db,
            db.get(Agent, "code"),
            "s1",
            "Fix test",
            code_run_id="run1",
            code_verifier=verifier,
            code_artifact_sealer=sealer,
        )) == "done"

    verifier.verify.assert_called_once()
    sealer.seal.assert_called_once()
    archive.assert_not_called()
    run = db.get(CodeAgentRun, "run1")
    assert run.status == "infrastructure_error"
    assert run.failure_reason == "infrastructure_error"
    assert run.status != "patch_ready"


@pytest.mark.parametrize(
    ("scenario", "changed_files", "with_skill", "repair_retry"),
    [
        ("single_file_change", ("models/a.sql",), False, False),
        ("multi_file_change", ("models/a.sql", "macros/b.sql"), False, False),
        ("test_fail_then_fix", ("models/a.sql",), False, True),
        ("bound_dbt_skill_repository_modification", ("models/gamestat.sql",), True, False),
    ],
)
def test_claude_code_mvp_acceptance_scenarios_reach_patch_ready_through_verifier_and_sealer(
    monkeypatch,
    scenario,
    changed_files,
    with_skill,
    repair_retry,
):
    db = _db()
    _code_run(db, with_skill=with_skill)
    run = db.get(CodeAgentRun, "run1")
    contract = {
        "objective": f"MVP scenario: {scenario}",
        "coding_runtime": "claude_code",
        "model_config": {"provider": "cloud_claude", "model_ref": "claude-sonnet"},
        "runtime_budgets": {"max_verifier_retries": 1 if repair_retry else 0},
    }
    if with_skill:
        contract["allowed_skills"] = ["skill1"]
    run.task_contract = json.dumps(contract)
    policy = json.loads(run.effective_policy)
    policy["coding_runtime"] = "claude_code"
    policy["model_config"] = {"provider": "cloud_claude", "model_ref": "claude-sonnet"}
    policy["runtime_budgets"] = {"max_verifier_retries": 1 if repair_retry else 0}
    run.effective_policy = json.dumps(policy)
    db.commit()

    def _exec(_self, _container_id, command, *, timeout_seconds, environment=None):
        if command == "claude --version":
            return 0, "Claude Code 2.1.246\n"
        return 0, ""

    monkeypatch.setattr(CodeContainerRunner, "exec", _exec)
    verifier = MagicMock()
    verifier.capture_baseline.return_value = {"status": "passed", "reason": "", "tests": []}
    passed_report = VerificationReport(
        passed=True,
        outcome="verification_passed",
        reason="",
        changed_paths=changed_files,
        checks=("validation_plan",),
        tests=({"test_index": 0, "command": "dbt test", "exit_code": 0},),
        secret_scan_complete=True,
    )
    if repair_retry:
        verifier.verify.side_effect = [
            VerificationReport(
                passed=False,
                outcome="verification_failed",
                reason="new_validation_failure",
                changed_paths=changed_files,
                checks=("validation_plan",),
                tests=({"test_index": 0, "command": "dbt test", "exit_code": 1},),
            ),
            passed_report,
        ]
    else:
        verifier.verify.return_value = passed_report
    sealer = MagicMock()
    sealer.seal.return_value = SealingResult(
        sealed=True,
        outcome="patch_ready",
        reason="",
        artifact_id=f"artifact-{scenario}",
        diff_hash="d" * 64,
    )
    runtime_inputs = []

    def _claude_run(_adapter, runtime_input):
        runtime_inputs.append(runtime_input)
        return ClaudeCodeRuntimeResult(
            status="coding_completed",
            exit_code=0,
            summary=f"{scenario} done",
            changed_files=changed_files,
        )

    with patch(
        "app.services.code_agent.claude_code_runtime.ClaudeCodeRuntimeAdapter.run",
        new=_claude_run,
    ):
        assert asyncio.run(run_agent(
            db,
            db.get(Agent, "code"),
            "s1",
            "Fix test",
            code_run_id="run1",
            code_verifier=verifier,
            code_artifact_sealer=sealer,
        )) == f"{scenario} done"

    run = db.get(CodeAgentRun, "run1")
    assert run.status == "patch_ready"
    assert verifier.verify.call_count == (2 if repair_retry else 1)
    sealer.seal.assert_called_once()
    assert [item.retry_attempt for item in runtime_inputs] == ([0, 1] if repair_retry else [0])
    if with_skill:
        facts = json.loads(run.runner_facts)
        assert facts["claude_code_skills"]["skills"][0]["name"] == "dbt-clickhouse-gamestat"


def test_code_runtime_cleanup_failure_is_observable_in_run_status():
    db = _db()
    _code_run(db)

    def _fail_cleanup():
        raise RuntimeError("runner unavailable")

    register_code_cleanup("run1", _fail_cleanup)
    with patch("app.services.agent_runtime.runtime.AgentRuntime.run", new=AsyncMock(side_effect=RuntimeError("boom"))):
        with pytest.raises(RuntimeError, match="cleanup_failed"):
            asyncio.run(run_agent(db, db.get(Agent, "code"), "s1", "Fix test", code_run_id="run1"))
    run = db.get(CodeAgentRun, "run1")
    assert run.status == "infrastructure_error"
    assert run.failure_reason == "cleanup_failed"


def test_code_runtime_cancellation_stops_and_cleans_resources():
    db = _db()
    _code_run(db)
    cleaned = []
    register_code_cleanup("run1", lambda: cleaned.append("done"))

    async def _cancel(_runtime, ctx):
        stop_chat(ctx.agent.id, ctx.session_id)
        return "[已停止]"

    with patch("app.services.agent_runtime.runtime.AgentRuntime.run", new=_cancel):
        assert asyncio.run(run_agent(
            db, db.get(Agent, "code"), "s1", "Fix test", code_run_id="run1"
        )) == "[已停止]"
    assert cleaned == ["done"]
    assert db.get(CodeAgentRun, "run1").status == "cancelled"


def test_code_runtime_timeout_stops_and_cleans_resources():
    db = _db()
    _code_run(db)
    run = db.get(CodeAgentRun, "run1")
    run.effective_policy = json.dumps({"budgets": {"timeout_seconds": 1}})
    db.commit()
    cleaned = []
    register_code_cleanup("run1", lambda: cleaned.append("done"))

    async def _slow(_runtime, _ctx):
        await asyncio.sleep(2)
        return "late"

    with patch("app.services.agent_runtime.runtime.AgentRuntime.run", new=_slow):
        with pytest.raises(asyncio.TimeoutError):
            asyncio.run(run_agent(
                db, db.get(Agent, "code"), "s1", "Fix test", code_run_id="run1"
            ))
    assert cleaned == ["done"]
    assert db.get(CodeAgentRun, "run1").status == "timed_out"


def test_baseline_command_timeout_uses_common_timeout_cleanup_path():
    db = _db()
    _code_run(db)
    cleaned = []
    register_code_cleanup("run1", lambda: cleaned.append("done"))
    verifier = MagicMock()
    verifier.capture_baseline.side_effect = RunnerCommandTimeout()

    with patch(
        "app.services.agent_runtime.runtime.AgentRuntime.run",
        new=AsyncMock(return_value="must not run"),
    ) as execute:
        with pytest.raises(RunnerCommandTimeout, match="runner_command_timed_out"):
            asyncio.run(run_agent(
                db, db.get(Agent, "code"), "s1", "Fix test",
                code_run_id="run1", code_verifier=verifier,
            ))

    execute.assert_not_awaited()
    assert cleaned == ["done"]
    assert db.get(CodeAgentRun, "run1").status == "timed_out"


def test_final_verifier_command_timeout_uses_common_timeout_cleanup_path():
    db = _db()
    _code_run(db)
    cleaned = []
    register_code_cleanup("run1", lambda: cleaned.append("done"))
    verifier = MagicMock()
    verifier.capture_baseline.return_value = {"status": "passed", "reason": "", "tests": []}
    verifier.verify.side_effect = RunnerCommandTimeout()

    with patch(
        "app.services.agent_runtime.runtime.AgentRuntime.run",
        new=AsyncMock(return_value="model final"),
    ):
        with pytest.raises(RunnerCommandTimeout, match="runner_command_timed_out"):
            asyncio.run(run_agent(
                db, db.get(Agent, "code"), "s1", "Fix test",
                code_run_id="run1", code_verifier=verifier,
            ))

    assert cleaned == ["done"]
    assert db.get(CodeAgentRun, "run1").status == "timed_out"


def test_workspace_prepare_failure_cleans_partial_allocation_and_is_terminal(tmp_path, monkeypatch):
    db = _db()
    _code_run(db)
    manager = WorkspaceManager(tmp_path / "startup-runs")
    manager.prepare = _REAL_WORKSPACE_PREPARE.__get__(manager, WorkspaceManager)

    def _verify_ok(run, _store):
        # Reach allocation with a valid identity; snapshot verification itself is
        # covered by test_code_agent_workspace_snapshot.py.
        return Path(tmp_path / "snap"), run.base_commit

    def _fail_materialize(_snapshot_path, _run_root):
        raise WorkspacePreparationError("source unavailable")

    monkeypatch.setattr(WorkspaceManager, "_verify_snapshot", staticmethod(_verify_ok))
    monkeypatch.setattr(WorkspaceManager, "_materialize", staticmethod(_fail_materialize))
    runner = MagicMock()

    with pytest.raises(WorkspacePreparationError, match="source unavailable"):
        asyncio.run(run_agent(
            db, db.get(Agent, "code"), "s1", "Fix test",
            code_run_id="run1", workspace_manager=manager, code_runner=runner,
        ))

    run = db.get(CodeAgentRun, "run1")
    assert run.status == "infrastructure_error"
    assert run.failure_reason == "startup_failed"
    assert not (tmp_path / "startup-runs" / "run1").exists()
    assert "run1" not in manager._allocated_runs
    runner.start.assert_not_called()


class _StartupWorkspaceManager:
    def __init__(self, root: Path, *, cleanup_fails: bool = False):
        self.root = root
        self.cleanup_fails = cleanup_fails
        self.cleaned = False

    def prepare(self, run):
        workspace = self.root / run.id / "workspace"
        workspace.mkdir(parents=True)
        (workspace / "partial.py").write_text("value = 1\n", encoding="utf-8")
        run.workspace_path = str(workspace)
        run.workspace_state = "prepared"
        return WorkspaceFacts(
            path=str(workspace), repository=run.repository,
            requested_commit=run.base_commit, resolved_commit=run.base_commit,
        )

    def cleanup_allocated_workspace(self, run):
        if self.cleanup_fails:
            raise RuntimeError("retention unavailable")
        workspace = Path(run.workspace_path)
        for path in workspace.rglob("*"):
            path.chmod(path.stat().st_mode & ~0o222)
        workspace.chmod(workspace.stat().st_mode & ~0o222)
        run.workspace_state = "retained_read_only"
        self.cleaned = True


def test_runner_start_failure_freezes_workspace_and_cannot_resume_pending_run(tmp_path):
    db = _db()
    _code_run(db)
    manager = _StartupWorkspaceManager(tmp_path / "startup-runs")
    runner = MagicMock()
    runner.start.side_effect = RunnerUnavailableError("runner_start_failed")
    events = []

    async def _collect(event):
        events.append(event)

    with pytest.raises(RunnerUnavailableError, match="runner_start_failed"):
        async def _run():
            hub.subscribe("code:s1", _collect)
            try:
                await run_agent(
                    db, db.get(Agent, "code"), "s1", "Fix test",
                    code_run_id="run1", workspace_manager=manager, code_runner=runner,
                )
            finally:
                hub.unsubscribe("code:s1", _collect)

        asyncio.run(_run())

    run = db.get(CodeAgentRun, "run1")
    workspace = Path(run.workspace_path)
    assert run.status == "infrastructure_error"
    assert run.failure_reason == "startup_failed"
    assert run.workspace_state == "retained_read_only"
    assert manager.cleaned is True
    assert workspace.stat().st_mode & 0o222 == 0
    assert (workspace / "partial.py").stat().st_mode & 0o222 == 0
    step_actions = [
        event["step"]["action"]
        for event in events
        if event.get("type") == "step" and event.get("step")
    ]
    assert step_actions == [
        "code_workspace_prepare",
        "code_workspace_prepare",
        "code_runner_start",
        "code_runner_start",
    ]
    audit = db.query(CodeControlAudit).filter(CodeControlAudit.action == "security_failure").one()
    payload = json.loads(audit.details)
    assert payload["reason"] == "runner_unavailable"
    assert payload["run_id"] == "run1"
    assert payload["facts"] == {
        "error_summary": "runner_start_failed",
        "error_type": "RunnerUnavailableError",
        "operation": "runner:start",
    }
    with pytest.raises(ValueError, match="code_run_not_pending"):
        asyncio.run(run_agent(
            db, db.get(Agent, "code"), "s2", "retry",
            code_run_id="run1", workspace_manager=manager, code_runner=runner,
        ))


def test_context_build_failure_after_runner_start_uses_outer_compensation_guard(tmp_path):
    db = _db()
    _code_run(db)
    manager = _StartupWorkspaceManager(tmp_path / "startup-runs")
    runner = MagicMock()
    runner.start.return_value = RunnerFacts(
        container_id="container1", image="test:image", image_id="sha256:test",
        network_mode="none", cpu_count=2, memory_mb=512,
    )

    with patch(
        "app.services.agent_runtime.context.AgentContext.from_params",
        side_effect=RuntimeError("context build failed"),
    ):
        with pytest.raises(RuntimeError, match="context build failed"):
            asyncio.run(run_agent(
                db, db.get(Agent, "code"), "s1", "Fix test",
                code_run_id="run1", workspace_manager=manager, code_runner=runner,
            ))

    run = db.get(CodeAgentRun, "run1")
    workspace = Path(run.workspace_path)
    assert runner.start.call_count == 1
    assert run.status == "infrastructure_error"
    assert run.failure_reason == "startup_failed"
    assert run.workspace_state == "retained_read_only"
    assert manager.cleaned is True
    assert workspace.stat().st_mode & 0o222 == 0


def test_startup_cleanup_failure_is_persisted_and_not_silent(tmp_path):
    db = _db()
    _code_run(db)
    manager = _StartupWorkspaceManager(tmp_path / "startup-runs", cleanup_fails=True)
    runner = MagicMock()
    runner.start.side_effect = RunnerUnavailableError("runner_start_failed")

    with pytest.raises(CodeCleanupError, match="cleanup_failed"):
        asyncio.run(run_agent(
            db, db.get(Agent, "code"), "s1", "Fix test",
            code_run_id="run1", workspace_manager=manager, code_runner=runner,
        ))

    run = db.get(CodeAgentRun, "run1")
    assert run.status == "infrastructure_error"
    assert run.failure_reason == "cleanup_failed"


def test_code_profile_steps_keep_phase_labels_and_snippets():
    from types import SimpleNamespace

    from app.services.agent_runtime.runtime import _code_profile_steps_for_message

    run = SimpleNamespace(runner_facts=json.dumps({
        "code_profile_events": [
            {"profile": "code", "phase": "prepare", "status": "started"},
            {"profile": "code", "phase": "tool_call", "status": "completed", "command": "ls models", "snippet": "models/a.sql"},
            {"profile": "code", "phase": "tool_call", "status": "completed", "command": "cat models/a.sql", "snippet": "select 1"},
        ]
    }))
    steps = _code_profile_steps_for_message(run)
    titles = [item["title"] for item in steps]
    assert titles[0].startswith("准备运行环境")
    assert all(not title.startswith("CodeAgent 运行阶段") for title in titles)
    assert [item["title"] for item in steps if item["action"] == "code_tool_call"] == [
        "Claude Code 工具调用 · completed",
        "Claude Code 工具调用 · completed",
    ]
    assert steps[-1]["content"] == "cat models/a.sql"
    assert steps[-1]["snippet"] == "select 1"
