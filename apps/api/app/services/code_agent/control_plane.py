"""Versioned CodeAgent Manifest validation and immutable run contracts."""

from __future__ import annotations

import json
import hashlib
import re
import shlex
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import (
    Agent,
    CodeAgentRun,
    CodeControlAudit,
    CodeDeployCredential,
    CodeProject,
    CodeProjectManifest,
    CodeSourceSnapshot,
    LLMResource,
    Sandbox,
)
from app.security import new_id, now_str
from app.services.code_agent.output_security import redact_code_output
from app.services.code_agent.authorization import (
    can_view_code_project,
    require_project_operator,
)
from app.services.code_agent.local_source import (
    LocalRepositoryPolicyError,
    normalize_local_repository_locator,
)
from app.services.code_agent.source_policy import (
    RepositorySourcePolicyError,
    normalize_remote_source,
)
from app.services.code_agent.workspace import effective_workspace_retention_hours


class ManifestUnavailableError(ValueError):
    """Raised before a Code run exists when no safe writable contract is available."""

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


class PolicyRejectedError(ValueError):
    """Raised when a lower policy layer attempts to expand a capability."""

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


PLATFORM_POLICY: dict[str, Any] = {
    "allowed_paths": ["**"],
    "network": False,
    "allowed_tools": ["read", "search", "edit", "test", "shell", "git_read"],
    "coding_runtime": "legacy",
    "allowed_skills": ["*"],
    "authorized_mcp_servers": ["*"],
    "runtime_budgets": {
        "max_verifier_retries": 2,
    },
    "allowed_sources": ["*"],
    "allowed_source_types": ["https", "ssh", "http", "local"],
    "allowed_image_digests": ["*"],
    "shell_commands": ["pytest", "ruff", "mypy", "npm", "pnpm", "yarn", "make"],
    "protected_paths": [],
    "test_integrity_paths": [],
    "secret_policy": {
        "source": "block",
        "source_unscannable": "block",
        "patch": "block",
        "output": "redact",
    },
    "budgets": {
        "max_iterations": 40,
        "timeout_seconds": 1800,
        "cpu_count": 2,
        "memory_mb": 512,
        "max_tool_calls": 200,
        "max_concurrent_runs": 5,
        "max_changed_files": 20,
        "max_diff_lines": 500,
        "pids_limit": 256,
        "tmpfs_mb": 64,
        "disk_mb": 1024,
        "output_limit_bytes": 100000,
    },
}


def _is_path_subset(candidate: str, parent: str) -> bool:
    candidate = candidate.strip().strip("/")
    parent = parent.strip().strip("/")
    return parent == "**" or candidate == parent or candidate.startswith(f"{parent}/")


def _as_string_list(value: Any, reason: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) and item.strip() for item in value):
        raise PolicyRejectedError(reason)
    return value


def _path_intersection(current: list[str], requested: list[str]) -> list[str]:
    intersection: set[str] = set()
    for parent in current:
        for candidate in requested:
            if _is_path_subset(candidate, parent):
                intersection.add(candidate)
            elif _is_path_subset(parent, candidate):
                intersection.add(parent)
    return sorted(intersection, key=lambda value: value.strip().strip("/"))


def _string_intersection(current: list[str], requested: list[str]) -> list[str]:
    if "*" in current:
        return sorted(set(requested))
    if "*" in requested:
        return sorted(set(current))
    return sorted(set(current).intersection(requested))


def _command_intersection(current: list[str], requested: list[str]) -> list[str]:
    intersection: set[str] = set()
    for parent in current:
        for candidate in requested:
            try:
                parent_executable = shlex.split(parent)[0]
                candidate_executable = shlex.split(candidate)[0]
            except (ValueError, IndexError) as exc:
                raise PolicyRejectedError("policy_invalid_shell_commands") from exc
            if candidate == parent:
                intersection.add(candidate)
            elif " " not in parent and candidate_executable == parent:
                intersection.add(candidate)
            elif " " not in candidate and parent_executable == candidate:
                intersection.add(parent)
    return sorted(intersection)


def merge_policy_layers(
    *,
    platform: dict[str, Any] | None = None,
    organization: dict[str, Any] | None = None,
    project: dict[str, Any] | None = None,
    manifest: dict[str, Any] | None = None,
    profile: dict[str, Any] | None = None,
    task: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Merge the six named policy layers by deterministic restriction only."""
    effective = {
        "allowed_paths": list(PLATFORM_POLICY["allowed_paths"]),
        "network": PLATFORM_POLICY["network"],
        "allowed_tools": list(PLATFORM_POLICY["allowed_tools"]),
        "coding_runtime": PLATFORM_POLICY["coding_runtime"],
        "allowed_skills": list(PLATFORM_POLICY["allowed_skills"]),
        "authorized_mcp_servers": list(PLATFORM_POLICY["authorized_mcp_servers"]),
        "runtime_budgets": dict(PLATFORM_POLICY["runtime_budgets"]),
        "allowed_sources": list(PLATFORM_POLICY["allowed_sources"]),
        "allowed_source_types": list(PLATFORM_POLICY["allowed_source_types"]),
        "allowed_image_digests": list(PLATFORM_POLICY["allowed_image_digests"]),
        "shell_commands": list(PLATFORM_POLICY["shell_commands"]),
        "protected_paths": list(PLATFORM_POLICY["protected_paths"]),
        "test_integrity_paths": list(PLATFORM_POLICY["test_integrity_paths"]),
        "secret_policy": dict(PLATFORM_POLICY["secret_policy"]),
        "budgets": dict(PLATFORM_POLICY["budgets"]),
    }
    sources: dict[str, Any] = {
        key: "platform"
        for key in (
            "allowed_paths", "network", "allowed_tools", "coding_runtime",
            "allowed_skills", "authorized_mcp_servers", "runtime_budgets",
            "allowed_sources",
            "allowed_source_types", "allowed_image_digests", "shell_commands",
            "protected_paths", "test_integrity_paths", "secret_policy",
        )
    }
    sources["budgets"] = {
        key: "platform" for key in effective["budgets"]
    }
    sources["runtime_budgets"] = {
        key: "platform" for key in effective["runtime_budgets"]
    }
    layers = (
        ("platform", platform),
        ("organization", organization),
        ("project", project),
        ("manifest", manifest),
        ("profile", profile),
        ("task", task),
    )
    known_fields = set(sources) | {"budgets"}
    for layer_name, layer in layers:
        if not layer:
            continue
        if not isinstance(layer, dict) or set(layer).difference(known_fields):
            raise PolicyRejectedError("policy_unknown_field")
        if "allowed_paths" in layer:
            paths = _as_string_list(layer["allowed_paths"], "policy_invalid_paths")
            narrowed = _path_intersection(effective["allowed_paths"], paths)
            if narrowed != effective["allowed_paths"]:
                sources["allowed_paths"] = layer_name
            effective["allowed_paths"] = narrowed
        if "network" in layer:
            requested_network = layer["network"]
            if not isinstance(requested_network, bool):
                raise PolicyRejectedError("policy_invalid_network")
            narrowed_network = effective["network"] and requested_network
            if narrowed_network != effective["network"]:
                sources["network"] = layer_name
            effective["network"] = narrowed_network
        if "allowed_tools" in layer:
            tools = _as_string_list(layer["allowed_tools"], "policy_invalid_tools")
            narrowed = _string_intersection(effective["allowed_tools"], tools)
            if narrowed != effective["allowed_tools"]:
                sources["allowed_tools"] = layer_name
            effective["allowed_tools"] = narrowed
        if "coding_runtime" in layer:
            runtime = layer["coding_runtime"]
            if runtime not in {"legacy", "claude_code"}:
                raise PolicyRejectedError("policy_invalid_coding_runtime")
            if runtime != effective["coding_runtime"]:
                sources["coding_runtime"] = layer_name
            effective["coding_runtime"] = runtime
        for key, reason in (
            ("allowed_skills", "policy_invalid_allowed_skills"),
            ("authorized_mcp_servers", "policy_invalid_authorized_mcp_servers"),
        ):
            if key in layer:
                values = _as_string_list(layer[key], reason)
                narrowed = _string_intersection(effective[key], values)
                if narrowed != effective[key]:
                    sources[key] = layer_name
                effective[key] = narrowed
        if "runtime_budgets" in layer:
            runtime_budgets = layer["runtime_budgets"]
            if not isinstance(runtime_budgets, dict):
                raise PolicyRejectedError("policy_invalid_runtime_budgets")
            for key, value in runtime_budgets.items():
                if (
                    key not in effective["runtime_budgets"]
                    or isinstance(value, bool)
                    or not isinstance(value, int)
                    or value < 0
                ):
                    raise PolicyRejectedError("policy_invalid_runtime_budgets")
                if value < effective["runtime_budgets"][key]:
                    effective["runtime_budgets"][key] = value
                    sources["runtime_budgets"][key] = layer_name
        for key in ("allowed_sources", "allowed_source_types", "allowed_image_digests"):
            if key in layer:
                values = _as_string_list(layer[key], f"policy_invalid_{key}")
                narrowed = _string_intersection(effective[key], values)
                if narrowed != effective[key]:
                    sources[key] = layer_name
                effective[key] = narrowed
        if "shell_commands" in layer:
            commands = _as_string_list(layer["shell_commands"], "policy_invalid_shell_commands")
            narrowed = _command_intersection(effective["shell_commands"], commands)
            if narrowed != effective["shell_commands"]:
                sources["shell_commands"] = layer_name
            effective["shell_commands"] = narrowed
        for protected_key in ("protected_paths", "test_integrity_paths"):
            if protected_key in layer:
                paths = _as_string_list(layer[protected_key], f"policy_invalid_{protected_key}")
                protected = sorted(set(effective[protected_key]) | set(paths))
                if protected != effective[protected_key]:
                    sources[protected_key] = layer_name
                effective[protected_key] = protected
        if "secret_policy" in layer:
            secret_policy = layer["secret_policy"]
            if not isinstance(secret_policy, dict):
                raise PolicyRejectedError("policy_invalid_secret_policy")
            allowed_values = {
                "source": {"block", "warn"},
                "source_unscannable": {"block", "warn"},
                "patch": {"block", "warn"},
                "output": {"redact"},
            }
            for key, value in secret_policy.items():
                if key not in allowed_values or value not in allowed_values[key]:
                    raise PolicyRejectedError("policy_invalid_secret_policy")
                if value != effective["secret_policy"][key]:
                    effective["secret_policy"][key] = value
                    sources["secret_policy"] = layer_name
        if "budgets" in layer:
            budgets = layer["budgets"]
            if not isinstance(budgets, dict):
                raise PolicyRejectedError("policy_invalid_budgets")
            for key, value in budgets.items():
                if (
                    key not in effective["budgets"]
                    or isinstance(value, bool)
                    or not isinstance(value, int)
                    or value <= 0
                ):
                    raise PolicyRejectedError("policy_invalid_budgets")
                if value < effective["budgets"][key]:
                    effective["budgets"][key] = value
                    sources["budgets"][key] = layer_name
    effective["policy_sources"] = sources
    return effective


def _require_contract_in_effective_policy(
    policy: dict[str, Any],
    contract: "FrozenCodeTaskContract",
) -> None:
    checks = (
        (contract.source_id, policy["allowed_sources"], "policy_source_denied"),
        (
            contract.source_type,
            policy["allowed_source_types"],
            "policy_source_type_denied",
        ),
    )
    for selected, allowed, reason in checks:
        if selected not in allowed and "*" not in allowed:
            raise PolicyRejectedError(reason)
    if not policy["allowed_paths"]:
        raise PolicyRejectedError("policy_paths_denied")
    if not policy["allowed_tools"]:
        raise PolicyRejectedError("policy_tools_denied")


@dataclass(frozen=True)
class FrozenCodeTaskContract:
    repository: str
    base_commit: str
    source_id: str
    source_type: str
    credential_ref: str
    requested_ref: str
    resolved_commit: str
    snapshot_id: str
    snapshot_hash: str
    source_scan_report_id: str
    security_schema_version: int
    objective: str
    allowed_paths: tuple[str, ...]
    validation_plan: tuple[dict[str, Any], ...]
    budgets: dict[str, int]
    coding_runtime: str = "legacy"
    model_config: dict[str, Any] = field(default_factory=dict)
    runtime_budgets: dict[str, int] = field(default_factory=dict)
    allowed_skills: tuple[str, ...] = ()
    authorized_mcp_servers: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "repository": self.repository,
            "base_commit": self.base_commit,
            "source_id": self.source_id,
            "source_type": self.source_type,
            "credential_ref": self.credential_ref,
            "requested_ref": self.requested_ref,
            "resolved_commit": self.resolved_commit,
            "snapshot_id": self.snapshot_id,
            "snapshot_hash": self.snapshot_hash,
            "source_scan_report_id": self.source_scan_report_id,
            "security_schema_version": self.security_schema_version,
            "objective": self.objective,
            "allowed_paths": list(self.allowed_paths),
            "validation_plan": list(self.validation_plan),
            "budgets": dict(self.budgets),
            "coding_runtime": self.coding_runtime,
            "model_config": dict(self.model_config),
            "runtime_budgets": dict(self.runtime_budgets),
            "allowed_skills": list(self.allowed_skills),
            "authorized_mcp_servers": list(self.authorized_mcp_servers),
        }


def _json_value(raw: str, expected: type, field: str):
    try:
        value = json.loads(raw or ("[]" if expected is list else "{}"))
    except json.JSONDecodeError as exc:
        raise ManifestUnavailableError(f"manifest_invalid_{field}") from exc
    if not isinstance(value, expected):
        raise ManifestUnavailableError(f"manifest_invalid_{field}")
    return value


def _published_manifest(db: Session, project_id: str) -> CodeProjectManifest:
    project = db.query(CodeProject).filter(CodeProject.id == project_id).first()
    if not project or not project.enabled:
        raise ManifestUnavailableError("project_unavailable")
    if project.environment_tier != "internal_non_production":
        raise ManifestUnavailableError("project_environment_not_allowed")
    manifest = (
        db.query(CodeProjectManifest)
        .filter(
            CodeProjectManifest.project_id == project_id,
            CodeProjectManifest.status == "published",
        )
        .order_by(CodeProjectManifest.version.desc())
        .first()
    )
    if not manifest:
        republish_required = (
            db.query(CodeProjectManifest.id)
            .filter(
                CodeProjectManifest.project_id == project_id,
                CodeProjectManifest.status == "security_republish_required",
            )
            .first()
        )
        if republish_required:
            raise ManifestUnavailableError("security_republish_required")
        raise ManifestUnavailableError("manifest_missing")
    return manifest


def can_use_code_project(user, project: CodeProject | None) -> bool:
    return can_view_code_project(user, project)


def freeze_task_contract(
    manifest: CodeProjectManifest,
    objective: str,
    *,
    coding_runtime: str = "legacy",
    model_config: dict[str, Any] | None = None,
    runtime_budgets: dict[str, int] | None = None,
    allowed_skills: list[str] | None = None,
    authorized_mcp_servers: list[str] | None = None,
) -> FrozenCodeTaskContract:
    allowed_paths = _json_value(manifest.allowed_paths, list, "allowed_paths") or ["**"]
    validation_plan = _json_value(manifest.validation_plan, list, "validation_plan") or []
    budgets = _json_value(manifest.budgets, dict, "budgets") or dict(PLATFORM_POLICY["budgets"])
    if not manifest.repository.strip():
        raise ManifestUnavailableError("manifest_missing_repository")
    requested_ref = (manifest.requested_ref or "HEAD").strip()
    if not objective.strip():
        raise ManifestUnavailableError("task_missing_objective")
    if not all(isinstance(path, str) and path.strip() for path in allowed_paths):
        raise ManifestUnavailableError("manifest_missing_allowed_paths")
    if not budgets or not all(isinstance(value, int) and value > 0 for value in budgets.values()):
        raise ManifestUnavailableError("manifest_invalid_budgets")
    if coding_runtime not in {"legacy", "claude_code"}:
        raise ManifestUnavailableError("manifest_invalid_coding_runtime")
    return FrozenCodeTaskContract(
        repository=manifest.repository,
        base_commit=(manifest.resolved_commit or manifest.base_commit or requested_ref).strip(),
        source_id=manifest.source_id or "",
        source_type=manifest.source_type or "",
        credential_ref=manifest.credential_ref or "",
        requested_ref=requested_ref,
        resolved_commit=(manifest.resolved_commit or "").strip(),
        snapshot_id=manifest.snapshot_id or "",
        snapshot_hash=manifest.snapshot_hash or "",
        source_scan_report_id=manifest.source_scan_report_id or "",
        security_schema_version=int(manifest.security_schema_version or 0),
        objective=objective.strip(),
        allowed_paths=tuple(allowed_paths),
        validation_plan=tuple(validation_plan),
        budgets=budgets,
        coding_runtime=coding_runtime,
        model_config=dict(model_config or {}),
        runtime_budgets=dict(runtime_budgets or {}),
        allowed_skills=tuple(allowed_skills or []),
        authorized_mcp_servers=tuple(authorized_mcp_servers or []),
    )


def _json_string_list(raw: str) -> list[str]:
    try:
        values = json.loads(raw or "[]")
    except (TypeError, json.JSONDecodeError):
        return []
    if not isinstance(values, list):
        return []
    return [str(value).strip() for value in values if isinstance(value, str) and value.strip()]


def _bound_capabilities(bound: list[str], allowed: list[str]) -> list[str]:
    if "*" in allowed:
        return sorted(set(bound))
    return sorted(set(bound).intersection(allowed))


def _apply_runtime_feature_flag(policy: dict[str, Any], settings) -> None:
    if (
        policy.get("coding_runtime") == "claude_code"
        and not bool(getattr(settings, "code_claude_code_runtime_enabled", False))
    ):
        policy["coding_runtime"] = "legacy"
        policy.setdefault("policy_sources", {})["coding_runtime"] = "feature_flag"


def preview_coding_runtime(db: Session, agent: Agent) -> str:
    """Return the runtime a new chat would freeze, without creating a run."""
    project_id = str(getattr(agent, "code_project_id", "") or "").strip()
    if not project_id:
        return "legacy"
    settings = get_settings()
    return "claude_code" if bool(
        getattr(settings, "code_claude_code_runtime_enabled", False)
    ) else "legacy"


def agent_would_use_claude_code(db: Session, agent: Agent) -> bool:
    return preview_coding_runtime(db, agent) == "claude_code"


def _require_secure_manifest_evidence(manifest: CodeProjectManifest) -> None:
    required = (
        manifest.source_id,
        manifest.source_type,
        manifest.repository,
        manifest.requested_ref,
    )
    if int(manifest.security_schema_version or 0) < 1 or any(
        not (value or "").strip() for value in required
    ):
        raise ManifestUnavailableError("security_republish_required")


_COMMIT_SHA = re.compile(r"^[0-9a-f]{40}$|^[0-9a-f]{64}$")


def _settings_list(raw: str) -> list[str]:
    try:
        value = json.loads(raw or "[]")
    except (TypeError, json.JSONDecodeError):
        return []
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if isinstance(item, str) and item.strip()]


def _credential_available(db: Session, reference_id: str, project: CodeProject) -> bool:
    row = (
        db.query(CodeDeployCredential)
        .filter(
            CodeDeployCredential.id == reference_id,
            CodeDeployCredential.organization_id == project.organization_id,
            CodeDeployCredential.status == "active",
        )
        .first()
    )
    if row is None or not row.read_only:
        return False
    try:
        allowed = json.loads(row.allowed_project_ids or "[]")
    except (TypeError, json.JSONDecodeError):
        return False
    return project.id in allowed


def secure_readiness_reason(
    db: Session,
    manifest: CodeProjectManifest,
    *,
    project: CodeProject,
    settings,
) -> str:
    """Return '' when the published Git configuration is ready for a run."""
    source_type = (manifest.source_type or "").strip()
    locator = (manifest.repository or "").strip()
    if not source_type or not locator:
        return "repository_source_not_allowed"
    try:
        if source_type == "local":
            normalize_local_repository_locator(
                locator,
                allowed_roots=settings.code_local_repository_roots,
            )
        else:
            normalized = normalize_remote_source(
                locator,
                allowlist=settings.code_repository_allowlist,
            )
            if normalized.source_type != source_type:
                raise RepositorySourcePolicyError()
    except (RepositorySourcePolicyError, LocalRepositoryPolicyError):
        return "repository_source_not_allowed"

    credential_ref = (manifest.credential_ref or "").strip()
    if credential_ref and not _credential_available(db, credential_ref, project):
        return "repository_auth_failed"

    if not (manifest.requested_ref or "").strip():
        return "repository_ref_invalid"

    return ""


def validate_manifest_for_admission(
    manifest: CodeProjectManifest,
    *,
    project_policy: dict[str, Any] | None = None,
) -> None:
    """Validate the same immutable fields required before a Code run can start."""
    _require_secure_manifest_evidence(manifest)
    contract = freeze_task_contract(manifest, "availability-check")
    manifest_policy = _json_value(manifest.policy, dict, "policy")
    policy = merge_policy_layers(
        project=project_policy,
        manifest={
            **manifest_policy,
            "allowed_paths": list(contract.allowed_paths),
            "allowed_sources": [contract.source_id],
            "allowed_source_types": [contract.source_type],
            "budgets": dict(contract.budgets),
        },
    )
    _require_contract_in_effective_policy(policy, contract)


def _running_bound_sandbox(db: Session, agent: Agent) -> Sandbox:
    sandbox_id = str(getattr(agent, "sandbox_id", "") or "").strip()
    if not sandbox_id:
        raise ManifestUnavailableError("sandbox_not_configured")
    sandbox = db.get(Sandbox, sandbox_id)
    if sandbox is None:
        raise ManifestUnavailableError("sandbox_not_configured")
    from app.services import docker_service

    status = docker_service.sync_container_status(sandbox.container_id) if sandbox.container_id else None
    if status and status != sandbox.status:
        sandbox.status = status
        db.flush()
    if not sandbox.container_id or (status or sandbox.status) != "running":
        raise ManifestUnavailableError("sandbox_not_running")
    return sandbox


def project_availability(db: Session, project: CodeProject) -> dict[str, Any]:
    """Return the normalized, side-effect-free control-plane readiness state."""
    if not project.enabled:
        return {"ready": False, "status": "unavailable", "reason": "project_disabled", "detail": ""}
    if project.environment_tier != "internal_non_production":
        return {
            "ready": False,
            "status": "unavailable",
            "reason": "project_environment_not_allowed",
            "detail": project.environment_tier or "",
        }
    manifest = (
        db.query(CodeProjectManifest)
        .filter(
            CodeProjectManifest.project_id == project.id,
            CodeProjectManifest.status == "published",
        )
        .order_by(CodeProjectManifest.version.desc())
        .first()
    )
    if not manifest:
        republish_required = (
            db.query(CodeProjectManifest.id)
            .filter(
                CodeProjectManifest.project_id == project.id,
                CodeProjectManifest.status == "security_republish_required",
            )
            .first()
        )
        if republish_required:
            return {
                "ready": False,
                "status": "unavailable",
                "reason": "security_republish_required",
                "detail": "",
            }
        return {"ready": False, "status": "unavailable", "reason": "manifest_missing", "detail": ""}
    settings = get_settings()
    try:
        _require_secure_manifest_evidence(manifest)
    except ManifestUnavailableError as exc:
        return {
            "ready": False,
            "status": "unavailable",
            "reason": exc.reason,
            "detail": "",
            "manifest_id": manifest.id,
            "manifest_version": manifest.version,
        }
    try:
        validate_manifest_for_admission(
            manifest,
            project_policy=_json_value(project.policy, dict, "project_policy"),
        )
    except (ManifestUnavailableError, PolicyRejectedError) as exc:
        return {
            "ready": False,
            "status": "unavailable",
            "reason": "manifest_invalid",
            "detail": exc.reason,
            "manifest_id": manifest.id,
            "manifest_version": manifest.version,
        }
    reason = secure_readiness_reason(db, manifest, project=project, settings=settings)
    if reason:
        return {
            "ready": False,
            "status": "unavailable",
            "reason": reason,
            "detail": "",
            "manifest_id": manifest.id,
            "manifest_version": manifest.version,
        }
    return {
        "ready": True,
        "status": "ready",
        "reason": "ready",
        "detail": "",
        "manifest_id": manifest.id,
        "manifest_version": manifest.version,
    }


def create_code_run(
    db: Session,
    *,
    agent: Agent,
    actor,
    project_id: str,
    objective: str,
    session_id: str = "",
    organization_policy: dict[str, Any] | None = None,
    profile_policy: dict[str, Any] | None = None,
    task_policy: dict[str, Any] | None = None,
    trigger_text: str = "",
) -> CodeAgentRun:
    """Create a pending Code run only after freezing a complete published Manifest."""
    project = db.query(CodeProject).filter(CodeProject.id == project_id).first()
    require_project_operator(actor, project, db=db, operation="run:create")
    manifest = _published_manifest(db, project_id)
    _require_secure_manifest_evidence(manifest)
    settings = get_settings()
    contract = freeze_task_contract(manifest, objective)
    sandbox = _running_bound_sandbox(db, agent)
    allowed_tools = list(PLATFORM_POLICY["allowed_tools"])
    from app.services.code_agent.kill_switch import enforce_code_kill_switches

    enforce_code_kill_switches(
        db,
        project_id=project_id,
        repository=manifest.repository,
        tools=allowed_tools,
        image=sandbox.image,
        model=agent.llm_id,
    )
    from app.services.code_agent.storage_capacity import enforce_storage_admission

    enforce_storage_admission(db, settings=settings)
    manifest_policy = {
        "allowed_sources": [contract.source_id],
        "allowed_source_types": [contract.source_type],
        "allowed_paths": list(contract.allowed_paths),
        "allowed_tools": allowed_tools,
        "budgets": dict(contract.budgets),
    }
    frozen_project_policy = _json_value(project.policy, dict, "project_policy")
    policy = merge_policy_layers(
        organization=organization_policy,
        project=frozen_project_policy,
        manifest=manifest_policy,
        profile=profile_policy,
        task=task_policy,
    )
    settings = get_settings()
    policy["coding_runtime"] = preview_coding_runtime(db, agent)
    policy["network"] = str(getattr(sandbox, "network_mode", "") or "bridge") != "none"
    policy.setdefault("policy_sources", {})["coding_runtime"] = "agent_profile"
    policy.setdefault("policy_sources", {})["network"] = "sandbox_binding"
    bound_skills = _json_string_list(agent.skills)
    bound_mcps = _json_string_list(agent.mcps)
    frozen_skills = _bound_capabilities(bound_skills, policy["allowed_skills"])
    frozen_mcps = _bound_capabilities(bound_mcps, policy["authorized_mcp_servers"])
    policy["allowed_skills"] = frozen_skills
    policy["authorized_mcp_servers"] = frozen_mcps
    llm = db.query(LLMResource).filter(LLMResource.id == agent.llm_id).first() if agent.llm_id else None
    model_ref = str(getattr(llm, "model", "") or "").strip() or str(agent.llm_id or "").strip()
    base_url = str(getattr(llm, "base_url", "") or "").strip()
    policy["model_config"] = {
        "provider": "cloud_claude" if policy["coding_runtime"] == "claude_code" else "agent_llm",
        "model_ref": model_ref,
    }
    if policy["coding_runtime"] == "claude_code":
        from app.services.code_agent.claude_code_runtime import (
            claude_code_llm_binding_reason,
        )

        model_reason = claude_code_llm_binding_reason(db, str(agent.llm_id or ""))
        if model_reason:
            policy["model_config"]["model_binding_reason"] = model_reason
    if base_url:
        policy["model_config"]["base_url"] = base_url
    policy.setdefault("policy_sources", {})["allowed_skills"] = "agent_binding"
    policy.setdefault("policy_sources", {})["authorized_mcp_servers"] = "agent_binding"
    policy.setdefault("policy_sources", {})["model_config"] = "agent"
    contract = freeze_task_contract(
        manifest,
        objective,
        coding_runtime=policy["coding_runtime"],
        model_config=policy["model_config"],
        runtime_budgets=policy["runtime_budgets"],
        allowed_skills=frozen_skills,
        authorized_mcp_servers=frozen_mcps,
    )
    _require_contract_in_effective_policy(policy, contract)
    enforce_code_kill_switches(
        db,
        project_id=project_id,
        repository=manifest.repository,
        tools=allowed_tools,
        image=sandbox.image,
        model=agent.llm_id,
        runtime=policy["coding_runtime"],
    )
    readiness_reason = secure_readiness_reason(
        db,
        manifest,
        project=project,
        settings=settings,
    )
    if readiness_reason:
        raise ManifestUnavailableError(readiness_reason)
    policy_json = json.dumps(policy, sort_keys=True, separators=(",", ":"))
    policy_hash = hashlib.sha256(policy_json.encode("utf-8")).hexdigest()

    active_runs = db.query(CodeAgentRun).filter(
        CodeAgentRun.project_id == project_id,
        CodeAgentRun.status.in_(["pending", "running"]),
    ).count()
    concurrency_limit = int(policy["budgets"]["max_concurrent_runs"])
    quota_exhausted = active_runs >= concurrency_limit
    safe_trigger_text = redact_code_output(trigger_text).text.strip()
    runner_facts = {}
    if safe_trigger_text:
        runner_facts["task_entry"] = {"trigger_text": safe_trigger_text}

    run = CodeAgentRun(
        id=new_id(),
        agent_id=agent.id,
        session_id=session_id,
        project_id=project_id,
        manifest_id=manifest.id,
        manifest_version=manifest.version,
        source_id=contract.source_id,
        source_type=contract.source_type,
        requested_ref=contract.requested_ref,
        resolved_commit=contract.resolved_commit,
        snapshot_id=contract.snapshot_id,
        snapshot_hash=contract.snapshot_hash,
        source_scan_report_id=contract.source_scan_report_id,
        repository=contract.repository,
        base_commit=contract.base_commit,
        image=sandbox.image,
        image_digest="",
        security_schema_version=contract.security_schema_version,
        task_contract=json.dumps(contract.to_dict(), sort_keys=True),
        effective_policy=policy_json,
        effective_policy_hash=policy_hash,
        execution_eligible=False,
        runner_state="not_started",
        runner_network_id="",
        runner_facts=json.dumps(runner_facts, ensure_ascii=False, sort_keys=True),
        cleanup_state="not_required",
        cleanup_attempts=0,
        cleanup_error="",
        cleanup_next_attempt="",
        workspace_retention_hours=effective_workspace_retention_hours(
            project, settings
        ),
        budget_usage=json.dumps({
            "project_active_runs_at_admission": active_runs,
            "max_concurrent_runs": concurrency_limit,
        }, sort_keys=True),
        status="budget_exhausted" if quota_exhausted else "pending",
        failure_reason="project_concurrency_limit" if quota_exhausted else "",
        created_at=now_str(),
    )
    db.add(run)
    audit_details = {
        "run_id": run.id,
        "manifest_version": manifest.version,
        "source_id": contract.source_id,
        "source_type": contract.source_type,
        "requested_ref": contract.requested_ref,
        "resolved_commit": contract.resolved_commit,
        "snapshot_id": contract.snapshot_id,
        "snapshot_hash": contract.snapshot_hash,
        "sandbox_id": sandbox.id,
        "sandbox_image": sandbox.image,
        "security_schema_version": contract.security_schema_version,
        "effective_policy_hash": policy_hash,
    }
    if safe_trigger_text:
        audit_details["trigger_text"] = safe_trigger_text
    db.add(CodeControlAudit(
        id=new_id(),
        actor=str(getattr(actor, "username", "")),
        action="run_create",
        project_id=project_id,
        manifest_id=manifest.id,
        details=json.dumps(audit_details, ensure_ascii=False, sort_keys=True),
        created_at=now_str(),
    ))
    db.flush()
    return run
