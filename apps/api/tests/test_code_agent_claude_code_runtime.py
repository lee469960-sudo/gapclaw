"""Claude Code runtime preflight contract tests."""

from __future__ import annotations

import json
from dataclasses import FrozenInstanceError
from types import SimpleNamespace

import pytest

from app.services.code_agent.claude_code_runtime import (
    CLAUDE_CODE_FAILURE_TYPES,
    CLAUDE_CODE_NON_PATCH_TERMINAL_RESULTS,
    ClaudeCodeRuntimeAdapter,
    ClaudeCodeRuntimeInput,
    ClaudeCodePreflightInput,
    claude_code_session_name,
    claude_code_llm_binding_reason,
    classify_claude_code_failure,
    materialize_claude_code_mcp_config,
    materialize_claude_code_skills,
    normalize_claude_code_runtime_event,
    record_claude_code_mcp_injection,
    record_claude_code_skill_injection,
    record_claude_code_runtime_result,
    run_claude_code_preflight,
    write_claude_code_transcripts,
    _runtime_summary,
)
from app.services.code_agent.claude_code_runtime import _stream_tool_event
from app.services.code_agent.failures import external_failure
from app.services.code_agent.results import TERMINAL_PRESENTATION


class FakeRunner:
    def __init__(self, *, fail_contains: str = "", fail_output: str = "failed"):
        self.fail_contains = fail_contains
        self.fail_output = fail_output
        self.commands: list[str] = []

    def exec(self, container_id: str, command: str, *, timeout_seconds: int, environment=None):
        assert container_id == "container1"
        assert timeout_seconds == 30
        self.commands.append(command)
        if self.fail_contains and self.fail_contains in command:
            return 1, self.fail_output
        if command == "claude --version":
            return 0, "Claude Code 2.1.246\n"
        return 0, ""


def _input(**overrides):
    base = {
        "run_id": "run1",
        "container_id": "container1",
        "repository_cwd": "/workspace",
        "runtime_config_dir": "/workspace/.claude",
        "mcp_config_json": json.dumps({"mcpServers": {}}),
        "model_config": {"provider": "cloud_claude", "model_ref": "claude-sonnet"},
        "budget_watchdog_active": True,
        "remaining_budget_seconds": 60,
        "timeout_seconds": 30,
    }
    base.update(overrides)
    return ClaudeCodePreflightInput(**base)


def test_claude_code_preflight_passes_and_records_version():
    runner = FakeRunner()
    result = run_claude_code_preflight(_input(), runner=runner)

    assert result.passed is True
    assert result.claude_code_version == "Claude Code 2.1.246"
    assert result.failure_type == ""
    assert result.reason == ""
    assert [step.name for step in result.steps] == [
        "cli",
        "config_dir",
        "repository_cwd",
        "mcp_config",
        "model",
        "budget_watchdog",
    ]
    assert runner.commands[0] == "claude --version"


@pytest.mark.parametrize(
    ("preflight_input", "runner", "model_probe", "failure_type", "reason"),
    [
        (
            _input(),
            FakeRunner(fail_contains="claude --version", fail_output="missing"),
            None,
            "runtime_unavailable",
            "claude_code_cli_unavailable",
        ),
        (
            _input(),
            FakeRunner(fail_contains="mkdir -p", fail_output="permission denied"),
            None,
            "runtime_unavailable",
            "claude_code_config_dir_unwritable",
        ),
        (
            _input(),
            FakeRunner(fail_contains="touch /workspace/.code-agent-preflight-write", fail_output="readonly"),
            None,
            "runtime_unavailable",
            "claude_code_repository_cwd_unwritable",
        ),
        (
            _input(mcp_config_json="{"),
            FakeRunner(),
            None,
            "mcp_config_failed",
            "claude_code_mcp_config_invalid",
        ),
        (
            _input(),
            FakeRunner(),
            lambda _model: False,
            "model_unavailable",
            "claude_code_model_unavailable",
        ),
        (
            _input(budget_watchdog_active=False),
            FakeRunner(),
            None,
            "infrastructure_error",
            "claude_code_budget_watchdog_unavailable",
        ),
        (
            _input(remaining_budget_seconds=0),
            FakeRunner(),
            None,
            "budget_exhausted",
            "claude_code_budget_exhausted",
        ),
    ],
)
def test_claude_code_preflight_failures_have_stable_reasons(
    preflight_input,
    runner,
    model_probe,
    failure_type,
    reason,
):
    result = run_claude_code_preflight(
        preflight_input,
        runner=runner,
        model_probe=model_probe,
    )

    assert result.passed is False
    assert result.failure_type == failure_type
    assert result.reason == reason
    assert result.steps[-1].status == "failed"
    assert result.to_dict()["reason"] == reason


def test_claude_code_preflight_default_model_probe_requires_cloud_claude_model():
    result = run_claude_code_preflight(
        _input(model_config={"provider": "local", "model_ref": "dev-model"}),
        runner=FakeRunner(),
    )

    assert result.passed is False
    assert result.failure_type == "model_unavailable"
    assert result.reason == "claude_code_model_unavailable"


def test_claude_code_binding_rejects_openai_compatible_minimax_resource():
    class FakeDb:
        def get(self, _model, _id):
            return SimpleNamespace(
                type="llm", provider="openai", model="MiniMax-M3", api_key_enc="",
            )

    assert claude_code_llm_binding_reason(FakeDb(), "minimax") == "llm_provider_not_supported"


@pytest.mark.parametrize("message", [
    "Failed to authenticate. API Error: 401 API key is invalid.",
    '[claude-code:unrecognized_model] {"model":"MiniMax-M3"}',
])
def test_claude_code_auth_and_model_errors_are_classified_before_coding_failure(message):
    assert classify_claude_code_failure(message) == "model_unavailable"


def _runtime_input(**overrides):
    base = {
        "run_id": "run1",
        "container_id": "container1",
        "objective": "Fix the failing test",
        "task_contract": {
            "coding_runtime": "claude_code",
            "objective": "Fix the failing test",
        },
        "effective_policy": {
            "coding_runtime": "claude_code",
            "allowed_tools": ["read", "edit", "test"],
        },
        "model_config": {"provider": "cloud_claude", "model_ref": "claude-sonnet"},
        "allowed_tools": ("read", "edit", "test"),
        "validation_plan": ({"command": "pytest -q"},),
        "timeout_seconds": 120,
        "max_turns": 4,
    }
    base.update(overrides)
    return ClaudeCodeRuntimeInput(**base)


class FakeClaudeRunner:
    def __init__(self, *, exit_code: int = 0, output: str = '{"summary":"coding done"}'):
        self.exit_code = exit_code
        self.output = output
        self.calls = []

    def exec(self, container_id: str, command: str, *, timeout_seconds: int, environment=None):
        self.calls.append((container_id, command, timeout_seconds))
        return self.exit_code, self.output


def test_claude_code_runtime_input_is_frozen():
    runtime_input = _runtime_input()

    with pytest.raises(FrozenInstanceError):
        runtime_input.run_id = "other"
    with pytest.raises(TypeError):
        runtime_input.model_config["model_ref"] = "other"
    with pytest.raises(TypeError):
        runtime_input.task_contract["objective"] = "other"


def test_claude_code_runtime_adapter_returns_coding_facts_not_patch_ready():
    runner = FakeClaudeRunner()
    adapter = ClaudeCodeRuntimeAdapter(runner=runner)

    result = adapter.run(_runtime_input())
    payload = result.to_dict()

    assert result.status == "coding_completed"
    assert result.exit_code == 0
    assert result.summary == "coding done"
    assert "patch_ready" not in payload
    assert "patch_ready" not in payload.values()
    assert len(runner.calls) == 1
    container_id, command, timeout_seconds = runner.calls[0]
    assert container_id == "container1"
    assert timeout_seconds == 120
    assert command.startswith("cd /workspace && claude -p 'Follow the coding SOP")
    assert "Write all user-facing task summaries, clarifications, final results, and failure explanations in Simplified Chinese." in command
    assert "Keep commands, file paths, error identifiers, status values, code, and tool output unchanged." in command
    assert "platform-controlled local publish stage" not in command
    assert "Do not execute repository deploy, publish, release" not in command
    assert "install the minimum required system or application dependencies" in command
    assert "--output-format json --model claude-sonnet" in command
    assert "--name code-agent-run-run1 --allowedTools Bash Edit Grep Read" in command


def test_local_publish_enters_claude_code_without_platform_publish_guard():
    runner = FakeClaudeRunner()
    ClaudeCodeRuntimeAdapter(runner=runner).run(
        _runtime_input(objective="使用 CI 中的发布命令执行 local 环境发布")
    )
    command = runner.calls[0][1]
    assert "platform-controlled local publish stage" not in command
    assert "Do not execute repository deploy, publish, release" not in command
    assert "install the minimum required system or application dependencies" in command
    assert "If installation is blocked by sandbox permissions or network policy" in command


def test_claude_code_runtime_adapter_streams_tool_events_before_result():
    class StreamingRunner:
        def __init__(self):
            self.calls = []

        def exec(self, container_id, command, *, timeout_seconds, environment=None, on_output=None):
            self.calls.append(command)
            lines = [
                json.dumps({
                    "type": "assistant",
                    "message": {"content": [{
                        "type": "tool_use", "id": "tool-1", "name": "Bash",
                        "input": {"command": "pytest -q"},
                    }]},
                }),
                json.dumps({
                    "type": "user",
                    "message": {"content": [{"type": "tool_result", "tool_use_id": "tool-1"}]},
                }),
                json.dumps({"type": "result", "status": "success", "result": "stream done"}),
            ]
            output = "\n".join(lines) + "\n"
            if on_output:
                on_output(output)
            return 0, output

    events = []
    runner = StreamingRunner()
    result = ClaudeCodeRuntimeAdapter(runner=runner, on_event=events.append).run(_runtime_input())

    assert result.status == "coding_completed"
    assert result.summary == "stream done"
    assert [event["type"] for event in events] == ["test_run", "test_run"]
    assert [event["status"] for event in events] == ["started", "completed"]
    assert "--output-format stream-json" in runner.calls[0]
    assert "--verbose" in runner.calls[0]


@pytest.mark.parametrize(
    ("status", "summary"),
    [
        ("target_not_found", "未找到 192.168.10.36:5432 in profiles scope"),
        ("needs_user_decision", "多个 local/profile/dev 候选需要确认"),
    ],
)
def test_claude_code_runtime_adapter_accepts_public_non_patch_terminal_results(status, summary):
    output = json.dumps({
        "status": status,
        "summary": summary,
        "changed_files": [],
        "runtime_events": [
            {
                "type": "tool_call",
                "status": "completed",
                "summary": summary,
                "target": "192.168.10.36:5432",
                "search_scope": "allowed_paths",
                "candidates": ["profiles.yml", "config/local.yml"],
            }
        ],
    })
    result = ClaudeCodeRuntimeAdapter(runner=FakeClaudeRunner(output=output)).run(_runtime_input())

    assert status in CLAUDE_CODE_NON_PATCH_TERMINAL_RESULTS
    assert result.status == status
    assert result.exit_code == 0
    assert result.changed_files == ()
    assert result.summary == summary


def test_claude_code_runtime_adapter_failure_stays_in_coding_stage():
    runner = FakeClaudeRunner(exit_code=1, output="model error")
    adapter = ClaudeCodeRuntimeAdapter(runner=runner)

    result = adapter.run(_runtime_input())

    assert result.status == "coding_failed"
    assert result.error_type == "coding_failed"
    assert result.error_summary == "model error"
    assert "patch_ready" not in result.to_dict()


def test_claude_code_runtime_adapter_classifies_timeout_as_stable_failure():
    runner = FakeClaudeRunner(exit_code=124, output="timed out")
    adapter = ClaudeCodeRuntimeAdapter(runner=runner)

    result = adapter.run(_runtime_input())

    assert result.status == "coding_timeout"
    assert result.error_type == "coding_timeout"


def test_claude_code_runtime_reuses_same_run_session_for_retry_only():
    first_runner = FakeClaudeRunner()
    retry_runner = FakeClaudeRunner()
    other_runner = FakeClaudeRunner()

    ClaudeCodeRuntimeAdapter(runner=first_runner).run(_runtime_input(run_id="run1"))
    ClaudeCodeRuntimeAdapter(runner=retry_runner).run(_runtime_input(
        run_id="run1",
        retry_attempt=1,
        verifier_feedback="pytest failed",
    ))
    ClaudeCodeRuntimeAdapter(runner=other_runner).run(_runtime_input(run_id="run2"))

    assert claude_code_session_name("run1") == "code-agent-run-run1"
    assert "--name code-agent-run-run1" in first_runner.calls[0][1]
    assert "--resume code-agent-run-run1" in retry_runner.calls[0][1]
    assert "--name code-agent-run-run2" in other_runner.calls[0][1]
    assert "code-agent-run-run1" not in other_runner.calls[0][1]


def test_claude_code_runtime_falls_back_to_fresh_retry_when_resume_session_is_missing():
    class ResumeMissingRunner:
        def __init__(self):
            self.calls = []

        def exec(self, container_id, command, *, timeout_seconds, environment=None):
            self.calls.append(command)
            if "--resume" in command:
                return 1, "Error: --resume requires a valid session ID or session title when used with --print."
            return 0, '{"summary":"retry fixed"}'

    runner = ResumeMissingRunner()
    result = ClaudeCodeRuntimeAdapter(runner=runner).run(_runtime_input(
        retry_attempt=1, verifier_feedback="pytest failed",
    ))

    assert result.status == "coding_completed"
    assert len(runner.calls) == 2
    assert "--resume" in runner.calls[0]
    assert "--resume" not in runner.calls[1]
    assert "--name code-agent-run-run1" in runner.calls[1]


@pytest.mark.parametrize(
    ("reason", "failure_type"),
    [
        ("claude_code_cli_unavailable", "runtime_unavailable"),
        ("claude_code_model_unavailable", "model_unavailable"),
        ("llm_group_not_supported", "model_unavailable"),
        ("claude_code_mcp_config_invalid", "mcp_config_failed"),
        ("claude_code_skill_load_failed", "skill_load_failed"),
        ("claude_code_budget_exhausted", "budget_exhausted"),
        ("claude_code_coding_failed", "coding_failed"),
        ("claude_code_coding_timeout", "coding_timeout"),
        ("verification_failed", "verification_failed"),
        ("claude_code_budget_watchdog_unavailable", "infrastructure_error"),
    ],
)
def test_claude_code_failure_classifications_are_stable_and_presentable(reason, failure_type):
    assert failure_type in CLAUDE_CODE_FAILURE_TYPES
    assert classify_claude_code_failure(reason) == failure_type
    assert failure_type in TERMINAL_PRESENTATION
    external = external_failure(reason)
    expected_external_reason = "verifier_failed" if reason == "verification_failed" else failure_type
    assert external["reason"] == expected_external_reason
    assert external["stage"]
    assert external["detail"]


@pytest.mark.parametrize(
    "event_type",
    [
        "runtime_started",
        "skill_loaded",
        "mcp_loaded",
        "tool_call",
        "file_changed",
        "test_run",
        "verifier_failed_retrying",
        "verifier_passed",
        "artifact_sealed",
    ],
)
def test_claude_code_runtime_events_normalize_to_stable_profile_payloads(event_type):
    secret = "sk-" + "d" * 20
    payload = normalize_claude_code_runtime_event(
        {
            "type": event_type,
            "status": "completed",
            "summary": f"api_key={secret}",
            "tool": "Bash",
            "path": "src/app.py",
        },
        run_id="run1",
    )

    assert payload["version"] == 1
    assert payload["profile"] == "code"
    assert payload["phase"] == event_type
    assert payload["status"] == "completed"
    assert payload["run_id"] == "run1"
    assert secret not in json.dumps(payload, sort_keys=True)


def test_claude_code_edit_event_captures_utf8_redacted_code_context():
    secret = "sk-" + "q" * 20
    event = _stream_tool_event({
        "type": "assistant",
        "message": {
            "content": [{
                "type": "tool_use",
                "id": "tool1",
                "name": "Edit",
                "input": {
                    "file_path": "gamestat/dbt_project.yml",
                    "old_string": "pg_host: 192.168.1.25",
                    "new_string": f"pg_host: 192.168.0.105  # 中文\npassword={secret}",
                },
            }],
        },
    })

    assert event is not None
    assert event["type"] == "file_changed"
    assert "修改前" in event["snippet"]
    assert "中文" in event["snippet"]
    assert secret not in event["snippet"]


def test_stream_json_system_init_is_not_used_as_user_facing_summary():
    output = "\n".join([
        json.dumps({"type": "system", "subtype": "init", "cwd": "/workspace", "tools": ["Read"]}),
        json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "已完成代码检查"}]}}),
    ])

    assert _runtime_summary(0, output) == "已完成代码检查"


def test_claude_code_transcript_redaction_keeps_user_visible_copy_safe(tmp_path):
    secret = "sk-" + "e" * 20
    raw_path, redacted_path = write_claude_code_transcripts(
        str(tmp_path),
        run_id="run1",
        transcript=f'{{"message":"api_key={secret}"}}\n',
    )

    raw = (tmp_path / raw_path).read_text(encoding="utf-8")
    redacted = (tmp_path / redacted_path).read_text(encoding="utf-8")
    assert secret in raw
    assert secret not in redacted
    assert "[REDACTED:SECRET]" in redacted
    payload = {
        "transcript_path": raw_path,
        "redacted_transcript_path": redacted_path,
    }
    assert secret not in json.dumps(payload, sort_keys=True)


def test_claude_code_runtime_result_captures_and_persists_redacted_facts():
    secret = "sk-" + "a" * 20
    output = json.dumps({
        "summary": f"changed files with api_key={secret}",
        "changed_files": ["src/app.py", "tests/test_app.py"],
        "budget_usage": {"turns": 3, "note": f"token={secret}"},
        "tool_audit": [
            {"tool": "Bash", "status": "completed", "output": f"password={secret}"}
        ],
        "transcript_path": "/workspace/.code-agent-runtime/transcript.jsonl",
        "redacted_transcript_path": "/workspace/.code-agent-runtime/transcript.redacted.jsonl",
        "runtime_events": [{"type": "tool_call", "summary": f"secret={secret}"}],
    })
    adapter = ClaudeCodeRuntimeAdapter(runner=FakeClaudeRunner(output=output))
    result = adapter.run(_runtime_input())
    run = SimpleNamespace(runner_facts="{}", budget_usage="{}", tool_audit="[]")

    record_claude_code_runtime_result(run, result)

    assert result.changed_files == ("src/app.py", "tests/test_app.py")
    assert result.transcript_path == "/workspace/.code-agent-runtime/transcript.jsonl"
    assert result.redacted_transcript_path == "/workspace/.code-agent-runtime/transcript.redacted.jsonl"
    assert secret not in json.dumps(result.to_dict(), sort_keys=True)
    assert secret not in run.runner_facts
    assert secret not in run.budget_usage
    assert secret not in run.tool_audit
    runner_facts = json.loads(run.runner_facts)
    assert runner_facts["claude_code_runtime"]["summary"] == "changed files with api_key=[REDACTED:SECRET]"
    assert json.loads(run.budget_usage)["claude_code_runtime"]["changed_files"] == 2
    assert json.loads(run.tool_audit)[0]["output"] == "password=[REDACTED:SECRET]"


def test_materializes_only_frozen_authorized_skills_and_records_visible_facts(
    tmp_path,
    monkeypatch,
):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.database import Base
    from app.models import CodeAgentRun, Skill
    from app.services import skill_runtime
    from app.services import skill_runtime

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    db.add_all([
        Skill(id="skill-a", name="dbt-clickhouse-gamestat", zip_name="dbt.zip", modified_at="v1"),
        Skill(id="skill-b", name="unbound-skill", zip_name="other.zip", modified_at="v1"),
    ])
    run = CodeAgentRun(
        id="run1",
        agent_id="agent1",
        project_id="project1",
        manifest_id="manifest1",
        manifest_version=1,
        task_contract=json.dumps({"allowed_skills": ["skill-a"]}),
        runner_facts="{}",
    )
    db.add(run)
    db.commit()
    monkeypatch.setattr(
        skill_runtime,
        "read_md",
        lambda skill: f"# {skill.name}\n\nUse this skill.",
    )

    result = materialize_claude_code_skills(db, run, str(tmp_path))
    record_claude_code_skill_injection(run, result)

    skill_file = tmp_path / ".claude" / "skills" / "dbt-clickhouse-gamestat" / "SKILL.md"
    unbound_file = tmp_path / ".claude" / "skills" / "unbound-skill" / "SKILL.md"
    assert skill_file.read_text() == "# dbt-clickhouse-gamestat\n\nUse this skill."
    assert not unbound_file.exists()
    assert [fact.name for fact in result.skills] == ["dbt-clickhouse-gamestat"]
    assert result.events[0]["type"] == "skill_loaded"
    assert result.events[0]["skill_name"] == "dbt-clickhouse-gamestat"
    facts = json.loads(run.runner_facts)["claude_code_skills"]
    assert facts["skills"][0]["source"] == "zip:dbt.zip"
    assert facts["skills"][0]["version"] == "v1"
    assert len(facts["skills"][0]["content_hash"]) == 64
    assert "Use this skill" not in run.runner_facts


def test_materialize_claude_code_skills_rewrites_legacy_workplace_path(tmp_path, monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.database import Base
    from app.models import CodeAgentRun, Skill
    from app.services import skill_runtime

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    skill = Skill(id="skill-a", name="dbt-clickhouse-gamestat")
    db.add(skill)
    run = CodeAgentRun(id="run1", agent_id="agent1", project_id="project1", manifest_id="manifest1", manifest_version=1, task_contract=json.dumps({"allowed_skills": ["skill-a"]}), runner_facts="{}")
    db.add(run)
    db.commit()
    monkeypatch.setattr(skill_runtime, "read_md", lambda _skill: "Search /workplace for profiles.yml")
    materialize_claude_code_skills(db, run, str(tmp_path))
    text = (tmp_path / ".claude/skills/dbt-clickhouse-gamestat/SKILL.md").read_text()
    assert "/workspace" in text
    assert "/workplace" not in text


def test_materializes_authorized_skill_resources_but_not_unbound_or_symlinks(
    tmp_path,
    monkeypatch,
):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.database import Base
    from app.models import CodeAgentRun, Skill
    from app.services import skill_runtime

    source_root = tmp_path / "source-skill"
    source_root.mkdir()
    (source_root / "SKILL.md").write_text("# Authorized\n", encoding="utf-8")
    (source_root / "scripts").mkdir()
    (source_root / "scripts" / "check.sh").write_text("echo ok\n", encoding="utf-8")
    (source_root / "references").mkdir()
    (source_root / "references" / "guide.md").write_text("guide\n", encoding="utf-8")
    secret_target = tmp_path / "outside-secret.txt"
    secret_target.write_text("outside", encoding="utf-8")
    (source_root / "outside-link").symlink_to(secret_target)

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    db.add_all([
        Skill(id="skill-a", name="authorized", zip_name="auth.zip"),
        Skill(id="skill-b", name="unbound", zip_name="unbound.zip"),
    ])
    run = CodeAgentRun(
        id="run1",
        agent_id="agent1",
        project_id="project1",
        manifest_id="manifest1",
        manifest_version=1,
        task_contract=json.dumps({"allowed_skills": ["skill-a"]}),
        runner_facts="{}",
    )
    db.add(run)
    db.commit()
    monkeypatch.setattr(
        skill_runtime,
        "_skill_dir",
        lambda skill: source_root if skill.id == "skill-a" else None,
    )
    monkeypatch.setattr(skill_runtime, "read_md", lambda _skill: "# Authorized\n")

    result = materialize_claude_code_skills(db, run, str(workspace))

    skill_dir = workspace / ".claude" / "skills" / "authorized"
    assert (skill_dir / "SKILL.md").read_text(encoding="utf-8") == "# Authorized\n"
    assert (skill_dir / "scripts" / "check.sh").read_text(encoding="utf-8") == "echo ok\n"
    assert (skill_dir / "references" / "guide.md").read_text(encoding="utf-8") == "guide\n"
    assert not (skill_dir / "outside-link").exists()
    assert not (workspace / ".claude" / "skills" / "unbound").exists()
    assert result.events[0]["resource_count"] == 2


def test_materializes_only_authorized_mcp_config_and_visible_audit(tmp_path):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.database import Base
    from app.models import CodeAgentRun, MCP

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    db.add_all([
        MCP(
            id="mcp-a",
            name="dbt-tools",
            command="python",
            command_args='["server.py"]',
            command_env='{"TOKEN":"secret"}',
        ),
        MCP(id="mcp-b", name="unbound", command="node", command_args='["server.js"]'),
    ])
    run = CodeAgentRun(
        id="run1",
        agent_id="agent1",
        project_id="project1",
        manifest_id="manifest1",
        manifest_version=1,
        task_contract=json.dumps({"authorized_mcp_servers": ["mcp-a"]}),
        runner_facts="{}",
    )
    db.add(run)
    db.commit()

    result = materialize_claude_code_mcp_config(db, run, str(tmp_path))
    record_claude_code_mcp_injection(run, result)

    config = json.loads((tmp_path / ".claude" / "mcp.json").read_text(encoding="utf-8"))
    assert config == {
        "mcpServers": {
            "dbt-tools": {"args": ["server.py"], "command": "python"}
        }
    }
    assert "unbound" not in result.config_json
    assert "secret" not in result.config_json
    assert result.events[0]["type"] == "mcp_loaded"
    assert result.events[0]["authorization_state"] == "authorized"
    assert result.events[0]["enabled_tool_count"] == 0
    facts = json.loads(run.runner_facts)["claude_code_mcp"]
    assert facts["servers"][0]["name"] == "dbt-tools"
    assert facts["servers"][0]["authorization_state"] == "authorized"
    assert facts["servers"][0]["config_path"] == ".claude/mcp.json"


def test_mcp_config_uses_env_references_and_never_plaintext_secrets(tmp_path):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.database import Base
    from app.models import CodeAgentRun, MCP

    secret = "sk-" + "b" * 20
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    db.add(MCP(
        id="mcp-a",
        name="secure-tools",
        command="python",
        command_args='["server.py"]',
        command_env=json.dumps({
            "SAFE_TOKEN": "${MCP_SAFE_TOKEN}",
            "PLAIN_TOKEN": secret,
        }),
        headers=json.dumps({"Authorization": f"Bearer {secret}"}),
    ))
    run = CodeAgentRun(
        id="run1",
        agent_id="agent1",
        project_id="project1",
        manifest_id="manifest1",
        manifest_version=1,
        task_contract=json.dumps({"authorized_mcp_servers": ["mcp-a"]}),
        runner_facts="{}",
    )
    db.add(run)
    db.commit()

    result = materialize_claude_code_mcp_config(db, run, str(tmp_path))
    config = json.loads(result.config_json)

    assert config["mcpServers"]["secure-tools"]["env"] == {
        "SAFE_TOKEN": "${MCP_SAFE_TOKEN}"
    }
    assert secret not in result.config_json
    assert "PLAIN_TOKEN" not in result.config_json
    assert "Authorization" not in result.config_json


def test_claude_command_does_not_include_model_credentials():
    secret = "sk-" + "c" * 20
    runner = FakeClaudeRunner()
    adapter = ClaudeCodeRuntimeAdapter(runner=runner)

    adapter.run(_runtime_input(
        model_config={
            "provider": "cloud_claude",
            "model_ref": "claude-sonnet",
            "api_key": secret,
        },
    ))

    assert secret not in runner.calls[0][1]
    assert "api_key" not in runner.calls[0][1]
