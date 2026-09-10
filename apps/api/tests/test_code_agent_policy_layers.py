"""OpenSpec task 2.3: six-layer least-privilege policy merge."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import (
    Agent,
    CodeAgentRun,
    CodeProject,
    CodeProjectManifest,
    CodeSourceSnapshot,
    LLMResource,
    Sandbox,
)
from app.security import encrypt_secret
import app.services.code_agent.control_plane as control_plane
from app.services.code_agent.control_plane import (
    PLATFORM_POLICY,
    PolicyRejectedError,
    create_code_run,
    merge_policy_layers,
)


def _ready(db, monkeypatch):
    """Seed a sealed snapshot and mock ready settings for a successful run."""
    db.add(CodeSourceSnapshot(
        id="snapshot-a",
        source_id="source-a",
        resolved_commit="a" * 40,
        content_hash="b" * 64,
        storage_path="/tmp/snapshot-a",
        scan_report_id="scan-a",
        importer_version="1",
        policy_hash="p" * 64,
        status="sealed",
    ))
    db.commit()

    class ReadySettings:
        code_repository_allowlist = '["ssh://git.example.test:22"]'
        code_repository_internal_cidrs = "[]"
        code_local_repository_roots = "[]"
        code_trusted_image_digests = '["sha256:' + "c" * 64 + '"]'

        def code_agent_security_readiness(self):
            return {"ready": True, "status": "ready", "reason": "ready", "errors": {}}

    settings = ReadySettings()
    monkeypatch.setattr(control_plane, "get_settings", lambda: settings)
    return settings


def _bind_running_sandbox(db, monkeypatch, agent: Agent) -> None:
    sandbox = Sandbox(
        id=f"sandbox-{agent.id}",
        name="Code Sandbox",
        image="internal/python:3.12",
        container_id=f"container-{agent.id}",
        status="running",
        creator="operator",
    )
    agent.sandbox_id = sandbox.id
    db.add(sandbox)
    monkeypatch.setattr(
        "app.services.docker_service.sync_container_status",
        lambda _container_id: "running",
    )


def test_all_six_named_layers_intersect_permissions_sources_images_and_paths(monkeypatch):
    monkeypatch.setitem(PLATFORM_POLICY, "network", True)
    effective = merge_policy_layers(
        platform={
            "allowed_paths": ["src/", "docs/"],
            "allowed_tools": ["read", "edit", "test"],
            "allowed_sources": ["source-a", "source-b"],
            "allowed_source_types": ["https", "ssh"],
            "allowed_image_digests": ["sha256:a", "sha256:b"],
            "network": True,
            "protected_paths": [".git/"],
        },
        organization={
            "allowed_paths": ["src/"],
            "allowed_tools": ["read", "edit"],
            "allowed_sources": ["source-a"],
            "allowed_source_types": ["ssh"],
            "allowed_image_digests": ["sha256:a"],
            "protected_paths": ["ci/"],
        },
        project={
            "allowed_paths": ["src/app/"],
            "allowed_tools": ["read", "edit", "shell"],
            "allowed_sources": ["source-a", "source-c"],
            "allowed_image_digests": ["sha256:a", "sha256:c"],
            "test_integrity_paths": ["tests/"],
        },
        manifest={
            "allowed_paths": ["src/app/api/"],
            "allowed_tools": ["read", "edit"],
            "network": False,
            "protected_paths": ["src/app/api/generated/"],
        },
        profile={
            "allowed_paths": ["src/app/api/", "other/"],
            "allowed_tools": ["read"],
            "network": True,
        },
        task={
            "allowed_paths": ["**"],
            "allowed_tools": ["read", "push"],
            "allowed_sources": ["*"],
            "allowed_image_digests": ["*"],
            "network": True,
        },
    )

    assert effective["allowed_paths"] == ["src/app/api/"]
    assert effective["allowed_tools"] == ["read"]
    assert effective["allowed_sources"] == ["source-a"]
    assert effective["allowed_source_types"] == ["ssh"]
    assert effective["allowed_image_digests"] == ["sha256:a"]
    assert effective["network"] is False
    assert effective["protected_paths"] == [
        ".git/", "ci/", "src/app/api/generated/",
    ]
    assert effective["test_integrity_paths"] == ["tests/"]
    assert effective["policy_sources"]["allowed_paths"] == "manifest"
    assert effective["policy_sources"]["allowed_tools"] == "profile"
    assert effective["policy_sources"]["network"] == "manifest"


@pytest.mark.parametrize("budget_key", sorted(PLATFORM_POLICY["budgets"]))
def test_every_numeric_resource_budget_takes_the_strictest_layer_and_records_source(
    budget_key,
):
    platform_limit = PLATFORM_POLICY["budgets"][budget_key]
    organization_limit = max(1, platform_limit - 1)
    project_limit = max(1, organization_limit - 1)
    manifest_limit = max(1, project_limit - 1)
    profile_limit = max(1, manifest_limit - 1)
    expected_limit = platform_limit
    expected_source = "platform"
    for source, value in (
        ("organization", organization_limit),
        ("project", project_limit),
        ("manifest", manifest_limit),
        ("profile", profile_limit),
    ):
        if value < expected_limit:
            expected_limit = value
            expected_source = source
    effective = merge_policy_layers(
        organization={"budgets": {budget_key: organization_limit}},
        project={"budgets": {budget_key: project_limit}},
        manifest={"budgets": {budget_key: manifest_limit}},
        profile={"budgets": {budget_key: profile_limit}},
        task={"budgets": {budget_key: platform_limit + 100}},
    )

    assert effective["budgets"][budget_key] == expected_limit
    assert effective["policy_sources"]["budgets"][budget_key] == expected_source


@pytest.mark.parametrize(
    "layer",
    [
        {"unknown": True},
        {"network": "yes"},
        {"allowed_sources": "source-a"},
        {"secret_policy": "warn"},
        {"secret_policy": {"source": "allow"}},
        {"secret_policy": {"source_unscannable": "allow"}},
        {"secret_policy": {"patch": "allow"}},
        {"secret_policy": {"output": "raw"}},
        {"coding_runtime": "other"},
        {"allowed_skills": "skill-a"},
        {"authorized_mcp_servers": "mcp-a"},
        {"runtime_budgets": {"max_verifier_retries": True}},
        {"runtime_budgets": {"unknown": 1}},
        {"budgets": {"timeout_seconds": True}},
        {"budgets": {"unknown_budget": 1}},
    ],
)
def test_invalid_policy_shapes_fail_closed(layer):
    with pytest.raises(PolicyRejectedError):
        merge_policy_layers(task=layer)


def test_source_secret_policy_warn_is_explicit_but_patch_and_output_stay_safe():
    effective = merge_policy_layers(
        manifest={
            "secret_policy": {
                "source": "warn",
                "source_unscannable": "warn",
                "patch": "block",
                "output": "redact",
            },
        },
    )

    assert effective["secret_policy"] == {
        "source": "warn",
        "source_unscannable": "warn",
        "patch": "block",
        "output": "redact",
    }
    assert effective["policy_sources"]["secret_policy"] == "manifest"


def test_patch_secret_policy_warn_is_accepted_and_other_defaults_stay():
    effective = merge_policy_layers(
        manifest={"secret_policy": {"patch": "warn"}},
    )

    assert effective["secret_policy"] == {
        "source": "block",
        "source_unscannable": "block",
        "patch": "warn",
        "output": "redact",
    }
    assert effective["policy_sources"]["secret_policy"] == "manifest"


def test_coding_runtime_defaults_to_legacy_and_accepts_claude_code_opt_in():
    default_policy = merge_policy_layers()
    assert default_policy["coding_runtime"] == "legacy"
    assert default_policy["runtime_budgets"] == {"max_verifier_retries": 2}

    effective = merge_policy_layers(
        manifest={
            "coding_runtime": "claude_code",
            "runtime_budgets": {"max_verifier_retries": 1},
        },
    )

    assert effective["coding_runtime"] == "claude_code"
    assert effective["runtime_budgets"] == {"max_verifier_retries": 1}
    assert effective["policy_sources"]["coding_runtime"] == "manifest"
    assert effective["policy_sources"]["runtime_budgets"]["max_verifier_retries"] == "manifest"


def test_shell_command_intersection_keeps_only_the_narrower_approved_form():
    effective = merge_policy_layers(
        organization={"shell_commands": ["pytest", "ruff"]},
        project={"shell_commands": ["pytest -q", "bash -c unsafe"]},
        task={"shell_commands": ["pytest", "bash"]},
    )

    assert effective["shell_commands"] == ["pytest -q"]
    assert effective["policy_sources"]["shell_commands"] == "project"


def test_run_freezes_project_manifest_profile_and_task_intersection(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    project = CodeProject(
        id="project-a",
        name="Project",
        creator="operator",
        policy=json.dumps({
            "allowed_paths": ["src/"],
            "allowed_tools": ["read", "edit", "test"],
            "protected_paths": ["src/generated/"],
            "budgets": {"timeout_seconds": 300, "memory_mb": 384},
        }),
    )
    agent = Agent(
        id="agent-a",
        name="Code",
        profile="code",
        code_project_id=project.id,
        skills='["skill-bound"]',
        mcps='["mcp-bound"]',
        llm_id="llm-a",
    )
    manifest = CodeProjectManifest(
        id="manifest-a",
        project_id=project.id,
        version=1,
        status="published",
        source_id="source-a",
        source_type="ssh",
        requested_ref="main",
        resolved_commit="a" * 40,
        snapshot_id="snapshot-a",
        snapshot_hash="b" * 64,
        source_scan_report_id="scan-a",
        repository="ssh://git.example.test/repo.git",
        base_commit="a" * 40,
        allowed_paths='["src/app/"]',
        validation_plan='[{"command":"pytest -q"}]',
        trusted_image="runner@example",
        image_digest="sha256:" + "c" * 64,
        security_schema_version=1,
        allowed_tools='["read","edit"]',
        policy='{"network":false,"protected_paths":["src/app/generated/"]}',
        budgets='{"timeout_seconds":240,"memory_mb":512}',
    )
    db.add(LLMResource(
        id="llm-cloud",
        type="llm",
        name="Claude",
        provider="anthropic",
        api_key_enc=encrypt_secret("sk-live-secret-value"),
        model="llm-cloud",
    ))
    _bind_running_sandbox(db, monkeypatch, agent)
    db.add_all([project, agent, manifest])
    db.commit()
    _ready(db, monkeypatch)

    run = create_code_run(
        db,
        agent=agent,
        actor=SimpleNamespace(username="operator", roles='["operator"]'),
        project_id=project.id,
        objective="Narrow policy",
        organization_policy={
            "allowed_paths": ["src/"],
            "budgets": {"timeout_seconds": 250},
        },
        profile_policy={
            "allowed_tools": ["read"],
            "budgets": {"timeout_seconds": 200},
        },
        task_policy={
            "allowed_paths": ["**"],
            "allowed_tools": ["read", "shell"],
            "allowed_sources": ["source-a", "source-b"],
            "allowed_image_digests": ["*"],
            "budgets": {"timeout_seconds": 400, "memory_mb": 256},
        },
    )
    policy = json.loads(run.effective_policy)
    frozen_contract = json.loads(run.task_contract)

    assert policy["allowed_paths"] == ["src/app/"]
    assert policy["allowed_tools"] == ["read"]
    assert policy["coding_runtime"] == "legacy"
    assert policy["allowed_skills"] == ["skill-bound"]
    assert policy["authorized_mcp_servers"] == ["mcp-bound"]
    assert policy["runtime_budgets"] == {"max_verifier_retries": 2}
    assert "model_config" not in policy
    assert policy["allowed_sources"] == ["source-a"]
    assert policy["allowed_source_types"] == ["ssh"]
    assert policy["allowed_image_digests"] == ["*"]
    assert policy["budgets"]["timeout_seconds"] == 200
    assert policy["budgets"]["memory_mb"] == 256
    assert policy["protected_paths"] == ["src/generated/"]
    assert policy["policy_sources"]["budgets"]["timeout_seconds"] == "profile"
    assert policy["policy_sources"]["budgets"]["memory_mb"] == "task"
    assert frozen_contract["coding_runtime"] == "legacy"
    assert frozen_contract["allowed_skills"] == ["skill-bound"]
    assert frozen_contract["authorized_mcp_servers"] == ["mcp-bound"]
    assert frozen_contract["runtime_budgets"] == {"max_verifier_retries": 2}
    assert frozen_contract["model_config"] == {"model_ref": "llm-a", "provider": "agent_llm"}


def test_run_freezes_claude_code_runtime_when_feature_flag_enabled(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    project = CodeProject(id="project-a", name="Project", creator="operator")
    agent = Agent(
        id="agent-a",
        name="Code",
        profile="code",
        code_project_id=project.id,
        skills='["skill-a","skill-b"]',
        mcps='["mcp-a","mcp-b"]',
        llm_id="llm-cloud",
    )
    manifest = CodeProjectManifest(
        id="manifest-a",
        project_id=project.id,
        version=1,
        status="published",
        source_id="source-a",
        source_type="ssh",
        requested_ref="main",
        resolved_commit="a" * 40,
        snapshot_id="snapshot-a",
        snapshot_hash="b" * 64,
        source_scan_report_id="scan-a",
        repository="ssh://git.example.test/repo.git",
        base_commit="a" * 40,
        allowed_paths='["src/"]',
        validation_plan='[{"command":"pytest -q"}]',
        trusted_image="runner@example",
        image_digest="sha256:" + "c" * 64,
        security_schema_version=1,
        allowed_tools='["read"]',
        policy=json.dumps({
            "network": False,
            "coding_runtime": "claude_code",
            "allowed_skills": ["skill-a"],
            "authorized_mcp_servers": ["mcp-a"],
            "runtime_budgets": {"max_verifier_retries": 1},
        }),
        budgets='{"timeout_seconds":240}',
    )
    db.add(LLMResource(
        id="llm-cloud",
        type="llm",
        name="Claude",
        provider="anthropic",
        api_key_enc=encrypt_secret("sk-live-secret-value"),
        model="llm-cloud",
    ))
    _bind_running_sandbox(db, monkeypatch, agent)
    db.add_all([project, agent, manifest])
    db.commit()
    settings = _ready(db, monkeypatch)
    settings.code_claude_code_runtime_enabled = True

    run = create_code_run(
        db,
        agent=agent,
        actor=SimpleNamespace(username="operator", roles='["operator"]'),
        project_id=project.id,
        objective="Use Claude Code",
    )
    policy = json.loads(run.effective_policy)
    contract = json.loads(run.task_contract)

    assert policy["coding_runtime"] == "claude_code"
    assert policy["allowed_skills"] == ["skill-a", "skill-b"]
    assert policy["authorized_mcp_servers"] == ["mcp-a", "mcp-b"]
    assert policy["runtime_budgets"] == {"max_verifier_retries": 2}
    assert "model_config" not in policy
    assert contract["coding_runtime"] == "claude_code"
    assert contract["allowed_skills"] == ["skill-a", "skill-b"]
    assert contract["authorized_mcp_servers"] == ["mcp-a", "mcp-b"]
    assert contract["runtime_budgets"] == {"max_verifier_retries": 2}
    assert contract["model_config"] == {"model_ref": "llm-cloud", "provider": "cloud_claude"}


def test_claude_code_runtime_feature_flag_disabled_falls_back_to_legacy(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    project = CodeProject(id="project-a", name="Project", creator="operator")
    agent = Agent(id="agent-a", name="Code", profile="code", code_project_id=project.id)
    manifest = CodeProjectManifest(
        id="manifest-a",
        project_id=project.id,
        version=1,
        status="published",
        source_id="source-a",
        source_type="ssh",
        requested_ref="main",
        resolved_commit="a" * 40,
        snapshot_id="snapshot-a",
        snapshot_hash="b" * 64,
        source_scan_report_id="scan-a",
        repository="ssh://git.example.test/repo.git",
        base_commit="a" * 40,
        allowed_paths='["src/"]',
        validation_plan='[{"command":"pytest -q"}]',
        trusted_image="runner@example",
        image_digest="sha256:" + "c" * 64,
        security_schema_version=1,
        allowed_tools='["read"]',
        policy='{"network":false,"coding_runtime":"claude_code"}',
        budgets='{"timeout_seconds":240}',
    )
    _bind_running_sandbox(db, monkeypatch, agent)
    db.add_all([project, agent, manifest])
    db.commit()
    settings = _ready(db, monkeypatch)
    settings.code_claude_code_runtime_enabled = True

    historical_run = create_code_run(
        db,
        agent=agent,
        actor=SimpleNamespace(username="operator", roles='["operator"]'),
        project_id=project.id,
        objective="Historical Claude Code run",
    )
    historical_contract = json.loads(historical_run.task_contract)
    assert historical_contract["coding_runtime"] == "claude_code"

    settings.code_claude_code_runtime_enabled = False

    run = create_code_run(
        db,
        agent=agent,
        actor=SimpleNamespace(username="operator", roles='["operator"]'),
        project_id=project.id,
        objective="Fallback",
    )
    policy = json.loads(run.effective_policy)
    contract = json.loads(run.task_contract)

    assert policy["coding_runtime"] == "legacy"
    assert policy["policy_sources"]["coding_runtime"] == "agent_profile"
    assert contract["coding_runtime"] == "legacy"
    assert db.query(CodeAgentRun).count() == 2
    assert json.loads(db.get(CodeAgentRun, historical_run.id).task_contract)["coding_runtime"] == "claude_code"


@pytest.mark.parametrize(
    ("task_policy", "reason"),
        [
            ({"allowed_sources": ["other-source"]}, "policy_source_denied"),
            ({"allowed_source_types": ["https"]}, "policy_source_type_denied"),
            ({"allowed_paths": ["other/"]}, "policy_paths_denied"),
        ],
    )
def test_run_is_rejected_when_the_frozen_contract_falls_outside_the_intersection(
    task_policy,
    reason,
    monkeypatch,
):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    project = CodeProject(id="project-a", name="Project", creator="operator")
    agent = Agent(id="agent-a", name="Code", profile="code", code_project_id=project.id)
    manifest = CodeProjectManifest(
        id="manifest-a",
        project_id=project.id,
        version=1,
        status="published",
        source_id="source-a",
        source_type="ssh",
        requested_ref="main",
        resolved_commit="a" * 40,
        snapshot_id="snapshot-a",
        snapshot_hash="b" * 64,
        source_scan_report_id="scan-a",
        repository="ssh://git.example.test/repo.git",
        base_commit="a" * 40,
        allowed_paths='["src/"]',
        validation_plan='[{"command":"pytest -q"}]',
        trusted_image="runner@example",
        image_digest="sha256:" + "c" * 64,
        security_schema_version=1,
        allowed_tools='["read"]',
        policy='{"network":false}',
        budgets='{"timeout_seconds":240}',
    )
    _bind_running_sandbox(db, monkeypatch, agent)
    db.add_all([project, agent, manifest])
    db.commit()

    with pytest.raises(PolicyRejectedError, match=reason):
        create_code_run(
            db,
            agent=agent,
            actor=SimpleNamespace(username="operator", roles='["operator"]'),
            project_id=project.id,
            objective="Attempt expansion",
            task_policy=task_policy,
        )
