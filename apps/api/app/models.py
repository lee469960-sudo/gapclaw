import json
from datetime import datetime
from sqlalchemy import (
    String, Text, Boolean, Integer, DateTime, ForeignKey, JSON,
    CheckConstraint, UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base


def _json_list(v):
    if v is None:
        return []
    if isinstance(v, str):
        try:
            return json.loads(v)
        except Exception:
            return []
    return v


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    organization_id: Mapped[str] = mapped_column(String(64), default="default", index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    roles: Mapped[str] = mapped_column(Text, default='["user"]')
    assignable_roles: Mapped[str] = mapped_column(Text, default="[]")
    permissions: Mapped[str] = mapped_column(Text, default="[]")
    disabled: Mapped[bool] = mapped_column(Boolean, default=False)
    tokens: Mapped[str] = mapped_column(Text, default="[]")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    def to_dict(self, include_tokens: bool = False) -> dict:
        tokens = _json_list(self.tokens)
        d = {
            "username": self.username,
            "organization_id": self.organization_id or "default",
            "role": "",
            "roles": _json_list(self.roles),
            "assignable_roles": _json_list(self.assignable_roles),
            "disabled": self.disabled,
            "permission": _json_list(self.permissions),
            "token": tokens if include_tokens else [],
            "token_count": len(tokens),
        }
        return d


class Session(Base):
    __tablename__ = "sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    username: Mapped[str] = mapped_column(String(64), index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class LLMResource(Base):
    __tablename__ = "llm_resources"

    id: Mapped[str] = mapped_column(String(16), primary_key=True)
    type: Mapped[str] = mapped_column(String(16), default="llm")  # llm | group
    name: Mapped[str] = mapped_column(String(255))
    provider: Mapped[str] = mapped_column(String(64), default="openai")
    base_url: Mapped[str] = mapped_column(String(512), default="")
    api_key_enc: Mapped[str] = mapped_column(Text, default="")
    model: Mapped[str] = mapped_column(String(128), default="")
    members: Mapped[str] = mapped_column(Text, default="[]")
    routing_capabilities: Mapped[str] = mapped_column(Text, default="{}")
    description: Mapped[str] = mapped_column(Text, default="")
    max_context_tokens: Mapped[int] = mapped_column(Integer, default=128000)
    max_output_tokens: Mapped[int] = mapped_column(Integer, default=4096)
    visibility: Mapped[str] = mapped_column(String(16), default="private")
    allowed_users: Mapped[str] = mapped_column(Text, default="[]")
    creator: Mapped[str] = mapped_column(String(64), default="")
    modified_at: Mapped[str] = mapped_column(String(32), default="")

    def to_dict(self, mask_key: bool = True) -> dict:
        from app.security import decrypt_secret, mask_secret
        key = decrypt_secret(self.api_key_enc) if self.api_key_enc else ""
        d = {
            "id": self.id,
            "type": self.type,
            "name": self.name,
            "provider": self.provider,
            "base_url": self.base_url,
            "api_key": mask_secret(key) if mask_key else key,
            "model": self.model,
            "members": _json_list(self.members),
            "routing_capabilities": _json_dict(self.routing_capabilities),
            "description": self.description,
            "max_context_tokens": self.max_context_tokens,
            "max_output_tokens": self.max_output_tokens,
            "visibility": self.visibility,
            "allowed_users": _json_list(self.allowed_users),
            "creator": self.creator,
            "modified_at": self.modified_at,
        }
        return d


def _json_dict(v):
    if isinstance(v, dict):
        return v
    if isinstance(v, str):
        try:
            parsed = json.loads(v)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}
    return {}


class ModelRoleGroup(Base):
    __tablename__ = "model_role_groups"

    id: Mapped[str] = mapped_column(String(16), primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(32))
    preferred_llm_id: Mapped[str] = mapped_column(String(16), default="")
    fallback_llm_ids: Mapped[str] = mapped_column(Text, default="[]")
    budget: Mapped[int] = mapped_column(Integer, default=0)
    timeout: Mapped[int] = mapped_column(Integer, default=0)
    visibility: Mapped[str] = mapped_column(String(16), default="private")
    allowed_users: Mapped[str] = mapped_column(Text, default="[]")
    creator: Mapped[str] = mapped_column(String(64), default="")
    version: Mapped[int] = mapped_column(Integer, default=1)
    modified_at: Mapped[str] = mapped_column(String(32), default="")

    def to_dict(self) -> dict:
        return {
            "id": self.id, "name": self.name, "role": self.role,
            "preferred_llm_id": self.preferred_llm_id,
            "fallback_llm_ids": _json_list(self.fallback_llm_ids),
            "budget": self.budget, "timeout": self.timeout,
            "visibility": self.visibility,
            "allowed_users": _json_list(self.allowed_users),
            "creator": self.creator, "version": self.version,
            "modified_at": self.modified_at,
        }


class ModelRoutingPolicy(Base):
    __tablename__ = "model_routing_policies"

    id: Mapped[str] = mapped_column(String(16), primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    router_llm_id: Mapped[str] = mapped_column(String(16), default="")
    role_group_ids: Mapped[str] = mapped_column(Text, default="[]")
    default_role: Mapped[str] = mapped_column(String(32), default="general")
    visibility: Mapped[str] = mapped_column(String(16), default="private")
    allowed_users: Mapped[str] = mapped_column(Text, default="[]")
    creator: Mapped[str] = mapped_column(String(64), default="")
    version: Mapped[int] = mapped_column(Integer, default=1)
    modified_at: Mapped[str] = mapped_column(String(32), default="")

    def to_dict(self) -> dict:
        return {
            "id": self.id, "name": self.name,
            "router_llm_id": self.router_llm_id,
            "role_group_ids": _json_list(self.role_group_ids),
            "default_role": self.default_role,
            "visibility": self.visibility,
            "allowed_users": _json_list(self.allowed_users),
            "creator": self.creator, "version": self.version,
            "modified_at": self.modified_at,
        }


class ModelRouteDecision(Base):
    __tablename__ = "model_route_decisions"

    id: Mapped[str] = mapped_column(String(16), primary_key=True)
    agent_id: Mapped[str] = mapped_column(String(16), index=True)
    session_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    policy_id: Mapped[str] = mapped_column(String(16), default="")
    policy_version: Mapped[int] = mapped_column(Integer, default=1)
    role: Mapped[str] = mapped_column(String(32), default="")
    llm_id: Mapped[str] = mapped_column(String(16), default="")
    detail: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[str] = mapped_column(String(32), default="")


class Skill(Base):
    __tablename__ = "skills"

    id: Mapped[str] = mapped_column(String(16), primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    tags: Mapped[str] = mapped_column(String(255), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    zip_path: Mapped[str] = mapped_column(String(512), default="")
    zip_name: Mapped[str] = mapped_column(String(255), default="")
    visibility: Mapped[str] = mapped_column(String(16), default="private")
    allowed_users: Mapped[str] = mapped_column(Text, default="[]")
    creator: Mapped[str] = mapped_column(String(64), default="")
    modified_at: Mapped[str] = mapped_column(String(32), default="")

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "tags": self.tags,
            "description": self.description,
            "has_zip": bool(self.zip_path),
            "zip_name": self.zip_name,
            "visibility": self.visibility,
            "allowed_users": _json_list(self.allowed_users),
            "creator": self.creator,
            "modified_at": self.modified_at,
        }


class MCP(Base):
    __tablename__ = "mcps"

    id: Mapped[str] = mapped_column(String(16), primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    tags: Mapped[str] = mapped_column(String(255), default="")
    url: Mapped[str] = mapped_column(String(512), default="")
    headers: Mapped[str] = mapped_column(Text, default="{}")
    protocol: Mapped[str] = mapped_column(String(32), default="sse")
    command: Mapped[str] = mapped_column(String(255), default="")
    command_args: Mapped[str] = mapped_column(Text, default="[]")
    command_env: Mapped[str] = mapped_column(Text, default="{}")
    description: Mapped[str] = mapped_column(Text, default="")
    visibility: Mapped[str] = mapped_column(String(16), default="private")
    allowed_users: Mapped[str] = mapped_column(Text, default="[]")
    creator: Mapped[str] = mapped_column(String(64), default="")
    modified_at: Mapped[str] = mapped_column(String(32), default="")

    def to_dict(self) -> dict:
        has_capability_metadata = bool((self.description or "").strip() or (self.tags or "").strip())
        env = {}
        try:
            env = json.loads(self.command_env or "{}")
            if not isinstance(env, dict):
                env = {}
        except Exception:
            env = {}
        return {
            "id": self.id,
            "name": self.name,
            "tags": self.tags,
            "url": self.url,
            "headers": self.headers,
            "protocol": self.protocol,
            "command": self.command or "",
            "command_args": _json_list(self.command_args),
            "command_env": env,
            "description": self.description,
            "visibility": self.visibility,
            "allowed_users": _json_list(self.allowed_users),
            "creator": self.creator,
            "modified_at": self.modified_at,
            "routing_eligible": True,
            "routing_status": "eligible" if has_capability_metadata else "missing_capability_metadata",
        }


class Sandbox(Base):
    __tablename__ = "sandboxes"

    id: Mapped[str] = mapped_column(String(16), primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    image: Mapped[str] = mapped_column(String(255), default="python:3.12-slim")
    cpu_count: Mapped[int] = mapped_column(Integer, default=2)
    memory_mb: Mapped[int] = mapped_column(Integer, default=512)
    network_mode: Mapped[str] = mapped_column(String(32), default="bridge")
    port_mappings: Mapped[str] = mapped_column(String(512), default="")
    visibility: Mapped[str] = mapped_column(String(16), default="private")
    allowed_users: Mapped[str] = mapped_column(Text, default="[]")
    container_id: Mapped[str] = mapped_column(String(128), default="")
    container_name: Mapped[str] = mapped_column(String(128), default="")
    status: Mapped[str] = mapped_column(String(32), default="stopped")
    creator: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[str] = mapped_column(String(32), default="")
    updated_at: Mapped[str] = mapped_column(String(32), default="")

    def to_dict(self) -> dict:
        from app.config import get_settings
        from pathlib import Path
        settings = get_settings()
        wp = Path(settings.workplace_dir).resolve() / self.id / "workplace"
        wp.mkdir(parents=True, exist_ok=True)
        skills_path = str(Path(settings.data_dir).resolve() / "skills")
        engine_path = str(Path(settings.data_dir).resolve() / "engine")
        mcp_path = str(Path(settings.data_dir).resolve() / "mcp")
        llm_path = str(Path(settings.data_dir).resolve() / "llm")
        mounts = {
            skills_path: {"bind": "/skills", "mode": "ro"},
            engine_path: {"bind": "/engine", "mode": "ro"},
            mcp_path: {"bind": "/mcp", "mode": "ro"},
            llm_path: {"bind": "/llm", "mode": "ro"},
            str(wp): {"bind": "/workplace", "mode": "rw"},
        }
        return {
            "id": self.id,
            "name": self.name,
            "image": self.image,
            "cpu_count": self.cpu_count,
            "memory_mb": self.memory_mb,
            "network_mode": self.network_mode,
            "port_mappings": self.port_mappings,
            "visibility": self.visibility,
            "allowed_users": _json_list(self.allowed_users),
            "skills_path": skills_path,
            "engine_path": engine_path,
            "mcp_path": mcp_path,
            "llm_path": llm_path,
            "workplace_path": str(wp),
            "workplace_mount": "/workplace",
            "mounts": mounts,
            "container_id": self.container_id,
            "container_name": self.container_name,
            "status": self.status,
            "creator": self.creator,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class Agent(Base):
    __tablename__ = "agents"

    id: Mapped[str] = mapped_column(String(16), primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    description: Mapped[str] = mapped_column(Text, default="")
    prompt: Mapped[str] = mapped_column(Text, default="")
    profile: Mapped[str] = mapped_column(String(16), default="standard")
    code_project_id: Mapped[str] = mapped_column(String(16), default="")
    llm_id: Mapped[str] = mapped_column(String(16), default="")
    routing_policy_id: Mapped[str] = mapped_column(String(16), default="")
    sandbox_id: Mapped[str] = mapped_column(String(16), default="")
    skills: Mapped[str] = mapped_column(Text, default="[]")
    mcps: Mapped[str] = mapped_column(Text, default="[]")
    rags: Mapped[str] = mapped_column(Text, default="[]")
    httpmcps: Mapped[str] = mapped_column(Text, default="[]")
    max_iterations: Mapped[int] = mapped_column(Integer, default=150)
    history_length: Mapped[int] = mapped_column(Integer, default=30)
    summary_max_words: Mapped[int] = mapped_column(Integer, default=5000)
    proactivity: Mapped[int] = mapped_column(Integer, default=2)
    response_style: Mapped[str] = mapped_column(String(16), default="adaptive")
    llm_timeout: Mapped[int] = mapped_column(Integer, default=1800)
    skill_timeout: Mapped[int] = mapped_column(Integer, default=1800)
    shell_timeout: Mapped[int] = mapped_column(Integer, default=1800)
    mcp_soft_circuit: Mapped[int] = mapped_column(Integer, default=5)
    tool_result_clip: Mapped[int] = mapped_column(Integer, default=6000)
    visibility: Mapped[str] = mapped_column(String(16), default="private")
    allowed_users: Mapped[str] = mapped_column(Text, default="[]")
    allowed_actions: Mapped[str] = mapped_column(Text, default='["skill_read_md","skill_run_script","mcp_tool_call","shell","file_read","file_write","file_search_replace","file_search"]')
    memory: Mapped[str] = mapped_column(Text, default="")
    session_list: Mapped[str] = mapped_column(Text, default="[]")
    creator: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[str] = mapped_column(String(32), default="")
    modified_at: Mapped[str] = mapped_column(String(32), default="")

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "prompt": self.prompt,
            "profile": self.profile or "standard",
            "code_project_id": self.code_project_id or "",
            "llm": self.llm_id,
            "routing_policy_id": self.routing_policy_id or "",
            "sandbox": self.sandbox_id,
            "skills": _json_list(self.skills),
            "mcps": _json_list(self.mcps),
            "rags": _json_list(self.rags),
            "httpmcps": _json_list(self.httpmcps),
            "max_iterations": self.max_iterations,
            "history_length": self.history_length,
            "summary_max_words": self.summary_max_words or 5000,
            "proactivity": self.proactivity,
            "response_style": self.response_style or "adaptive",
            "llm_timeout": self.llm_timeout,
            "skill_timeout": self.skill_timeout,
            "shell_timeout": self.shell_timeout,
            "mcp_soft_circuit": self.mcp_soft_circuit,
            "tool_result_clip": self.tool_result_clip,
            "visibility": self.visibility,
            "allowed_users": _json_list(self.allowed_users),
            "allowed_actions": _json_list(self.allowed_actions),
            "creator": self.creator,
            "created_at": self.created_at,
            "modified_at": self.modified_at,
            "session_list": _json_list(self.session_list),
        }


class CodeProject(Base):
    __tablename__ = "code_projects"

    id: Mapped[str] = mapped_column(String(16), primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    organization_id: Mapped[str] = mapped_column(String(64), default="default", index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    environment_tier: Mapped[str] = mapped_column(String(32), default="internal_non_production")
    visibility: Mapped[str] = mapped_column(String(16), default="private")
    allowed_users: Mapped[str] = mapped_column(Text, default="[]")
    policy: Mapped[str] = mapped_column(Text, default="{}")
    workspace_retention_hours: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )
    creator: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[str] = mapped_column(String(32), default="")
    modified_at: Mapped[str] = mapped_column(String(32), default="")


class CodeScanReport(Base):
    __tablename__ = "code_scan_reports"
    __table_args__ = (
        CheckConstraint("scope IN ('source', 'patch')", name="ck_code_scan_report_scope"),
        CheckConstraint(
            "status IN ('pending', 'complete', 'incomplete', 'failed')",
            name="ck_code_scan_report_status",
        ),
        CheckConstraint("findings_count >= 0", name="ck_code_scan_report_findings_count"),
    )

    id: Mapped[str] = mapped_column(String(16), primary_key=True)
    scope: Mapped[str] = mapped_column(String(16), index=True)
    input_hash: Mapped[str] = mapped_column(String(64), index=True)
    scanner: Mapped[str] = mapped_column(String(64))
    scanner_version: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    complete: Mapped[bool] = mapped_column(Boolean, default=False)
    findings_count: Mapped[int] = mapped_column(Integer, default=0)
    files_discovered: Mapped[int] = mapped_column(Integer, default=0)
    files_scanned: Mapped[int] = mapped_column(Integer, default=0)
    bytes_discovered: Mapped[int] = mapped_column(Integer, default=0)
    bytes_scanned: Mapped[int] = mapped_column(Integer, default=0)
    skipped_count: Mapped[int] = mapped_column(Integer, default=0)
    truncated_count: Mapped[int] = mapped_column(Integer, default=0)
    findings: Mapped[str] = mapped_column(Text, default="[]")
    failure_reason: Mapped[str] = mapped_column(String(64), default="")
    publish_state: Mapped[str] = mapped_column(String(32), default="not_requested")
    publish_preflight: Mapped[str] = mapped_column(Text, default="{}")
    publish_confirmation: Mapped[str] = mapped_column(String(64), default="")
    publish_result: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[str] = mapped_column(String(32), default="")


class CodeRepositorySource(Base):
    __tablename__ = "code_repository_sources"
    __table_args__ = (
        CheckConstraint(
            "source_type IN ('https', 'ssh', 'http', 'local')",
            name="ck_code_repository_source_type",
        ),
        CheckConstraint(
            "status IN ('draft', 'active', 'disabled', 'failed')",
            name="ck_code_repository_source_status",
        ),
    )

    id: Mapped[str] = mapped_column(String(16), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        String(16), ForeignKey("code_projects.id", ondelete="CASCADE"), index=True
    )
    source_type: Mapped[str] = mapped_column(String(16), index=True)
    locator: Mapped[str] = mapped_column(String(1024))
    credential_ref: Mapped[str] = mapped_column(String(128), default="", index=True)
    requested_ref: Mapped[str] = mapped_column(String(128), default="HEAD")
    policy_ref: Mapped[str] = mapped_column(String(64), default="")
    status: Mapped[str] = mapped_column(String(16), default="draft", index=True)
    created_by: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[str] = mapped_column(String(32), default="")
    updated_at: Mapped[str] = mapped_column(String(32), default="")


class CodeDeployCredential(Base):
    __tablename__ = "code_deploy_credentials"
    __table_args__ = (
        CheckConstraint(
            "credential_kind = 'deploy_token'",
            name="ck_code_deploy_credential_kind",
        ),
        CheckConstraint(
            "status IN ('active', 'disabled')",
            name="ck_code_deploy_credential_status",
        ),
        CheckConstraint(
            "read_only = TRUE",
            name="ck_code_deploy_credential_read_only",
        ),
    )

    id: Mapped[str] = mapped_column(String(16), primary_key=True)
    organization_id: Mapped[str] = mapped_column(String(64), index=True)
    label: Mapped[str] = mapped_column(String(128))
    credential_kind: Mapped[str] = mapped_column(String(32), default="deploy_token")
    auth_username: Mapped[str] = mapped_column(String(128), default="")
    secret_enc: Mapped[str] = mapped_column(Text)
    allowed_project_ids: Mapped[str] = mapped_column(Text, default="[]")
    read_only: Mapped[bool] = mapped_column(Boolean, default=True)
    status: Mapped[str] = mapped_column(String(16), default="active", index=True)
    created_by: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[str] = mapped_column(String(32), default="")
    updated_at: Mapped[str] = mapped_column(String(32), default="")


class CodeSourceSnapshot(Base):
    __tablename__ = "code_source_snapshots"
    __table_args__ = (
        CheckConstraint(
            "status IN ('importing', 'scanning', 'sealed', 'failed', 'deleted')",
            name="ck_code_source_snapshot_status",
        ),
        CheckConstraint("ref_count >= 0", name="ck_code_source_snapshot_ref_count"),
    )

    id: Mapped[str] = mapped_column(String(16), primary_key=True)
    source_id: Mapped[str] = mapped_column(
        String(16), ForeignKey("code_repository_sources.id", ondelete="CASCADE"), index=True
    )
    resolved_commit: Mapped[str] = mapped_column(String(64), index=True)
    content_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    storage_path: Mapped[str] = mapped_column(String(1024))
    scan_report_id: Mapped[str] = mapped_column(
        String(16), ForeignKey("code_scan_reports.id"), index=True
    )
    importer_version: Mapped[str] = mapped_column(String(64))
    policy_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), default="importing", index=True)
    ref_count: Mapped[int] = mapped_column(Integer, default=0)
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    file_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[str] = mapped_column(String(32), default="")
    sealed_at: Mapped[str] = mapped_column(String(32), default="")
    cleanup_after: Mapped[str] = mapped_column(String(32), default="")
    cleanup_attempts: Mapped[int] = mapped_column(Integer, default=0)
    cleanup_error: Mapped[str] = mapped_column(String(64), default="")
    cleanup_next_attempt: Mapped[str] = mapped_column(String(32), default="")


class CodeProjectManifest(Base):
    __tablename__ = "code_project_manifests"
    __table_args__ = (UniqueConstraint("project_id", "version", name="uq_code_project_manifest_version"),)

    id: Mapped[str] = mapped_column(String(16), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(16), index=True)
    version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(32), default="draft")
    source_id: Mapped[str] = mapped_column(String(16), default="", index=True)
    source_type: Mapped[str] = mapped_column(String(16), default="")
    credential_ref: Mapped[str] = mapped_column(String(128), default="")
    requested_ref: Mapped[str] = mapped_column(String(128), default="")
    resolved_commit: Mapped[str] = mapped_column(String(64), default="")
    snapshot_id: Mapped[str] = mapped_column(String(16), default="", index=True)
    snapshot_hash: Mapped[str] = mapped_column(String(64), default="")
    source_scan_report_id: Mapped[str] = mapped_column(String(16), default="", index=True)
    repository: Mapped[str] = mapped_column(String(512), default="")
    base_commit: Mapped[str] = mapped_column(String(128), default="")
    allowed_paths: Mapped[str] = mapped_column(Text, default="[]")
    validation_plan: Mapped[str] = mapped_column(Text, default="[]")
    trusted_image: Mapped[str] = mapped_column(String(255), default="")
    image_digest: Mapped[str] = mapped_column(String(128), default="")
    security_schema_version: Mapped[int] = mapped_column(Integer, default=0)
    allowed_tools: Mapped[str] = mapped_column(Text, default="[]")
    policy: Mapped[str] = mapped_column(Text, default="{}")
    budgets: Mapped[str] = mapped_column(Text, default="{}")
    local_publish_command_id: Mapped[str] = mapped_column(String(128), default="")
    local_publish_target: Mapped[str] = mapped_column(String(16), default="")
    local_publish_dependencies: Mapped[str] = mapped_column(Text, default="[]")
    local_publish_network_targets: Mapped[str] = mapped_column(Text, default="[]")
    local_publish_secret_ref: Mapped[str] = mapped_column(String(128), default="")
    local_publish_verification_plan: Mapped[str] = mapped_column(Text, default="[]")
    local_publish_lock_key: Mapped[str] = mapped_column(String(255), default="")
    published_at: Mapped[str] = mapped_column(String(32), default="")
    created_at: Mapped[str] = mapped_column(String(32), default="")


class CodeControlAudit(Base):
    __tablename__ = "code_control_audits"

    id: Mapped[str] = mapped_column(String(16), primary_key=True)
    actor: Mapped[str] = mapped_column(String(64), index=True)
    action: Mapped[str] = mapped_column(String(32), index=True)
    project_id: Mapped[str] = mapped_column(String(16), index=True)
    manifest_id: Mapped[str] = mapped_column(String(16), default="", index=True)
    details: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[str] = mapped_column(String(32), default="")


class CodeAgentRun(Base):
    __tablename__ = "code_agent_runs"

    id: Mapped[str] = mapped_column(String(16), primary_key=True)
    agent_id: Mapped[str] = mapped_column(String(16), index=True)
    session_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    project_id: Mapped[str] = mapped_column(String(16), index=True)
    manifest_id: Mapped[str] = mapped_column(String(16), index=True)
    manifest_version: Mapped[int] = mapped_column(Integer)
    source_id: Mapped[str] = mapped_column(String(16), default="", index=True)
    source_type: Mapped[str] = mapped_column(String(16), default="")
    requested_ref: Mapped[str] = mapped_column(String(128), default="")
    resolved_commit: Mapped[str] = mapped_column(String(64), default="")
    snapshot_id: Mapped[str] = mapped_column(String(16), default="", index=True)
    snapshot_hash: Mapped[str] = mapped_column(String(64), default="")
    source_scan_report_id: Mapped[str] = mapped_column(String(16), default="", index=True)
    repository: Mapped[str] = mapped_column(String(512), default="")
    base_commit: Mapped[str] = mapped_column(String(128), default="")
    image: Mapped[str] = mapped_column(String(255), default="")
    image_digest: Mapped[str] = mapped_column(String(128), default="")
    security_schema_version: Mapped[int] = mapped_column(Integer, default=0)
    task_contract: Mapped[str] = mapped_column(Text, default="{}")
    effective_policy: Mapped[str] = mapped_column(Text, default="{}")
    effective_policy_hash: Mapped[str] = mapped_column(String(64), default="")
    workspace_path: Mapped[str] = mapped_column(String(1024), default="")
    source_facts: Mapped[str] = mapped_column(Text, default="{}")
    runner_facts: Mapped[str] = mapped_column(Text, default="{}")
    container_id: Mapped[str] = mapped_column(String(128), default="")
    runner_network_id: Mapped[str] = mapped_column(String(128), default="")
    runner_state: Mapped[str] = mapped_column(String(32), default="not_started")
    execution_eligible: Mapped[bool] = mapped_column(Boolean, default=False)
    cleanup_state: Mapped[str] = mapped_column(String(32), default="not_required")
    cleanup_attempts: Mapped[int] = mapped_column(Integer, default=0)
    cleanup_error: Mapped[str] = mapped_column(String(64), default="")
    cleanup_next_attempt: Mapped[str] = mapped_column(String(32), default="")
    workspace_state: Mapped[str] = mapped_column(String(32), default="prepared")
    workspace_retention_hours: Mapped[int] = mapped_column(Integer, default=168)
    retained_until: Mapped[str] = mapped_column(String(32), default="")
    workspace_downloadable: Mapped[bool] = mapped_column(Boolean, default=False)
    tool_audit: Mapped[str] = mapped_column(Text, default="[]")
    tool_calls_used: Mapped[int] = mapped_column(Integer, default=0)
    budget_usage: Mapped[str] = mapped_column(Text, default="{}")
    verification_baseline: Mapped[str] = mapped_column(Text, default="{}")
    verifier_report: Mapped[str] = mapped_column(Text, default="{}")
    artifact_id: Mapped[str] = mapped_column(String(16), default="")
    publish_state: Mapped[str] = mapped_column(String(32), default="not_requested")
    publish_preflight: Mapped[str] = mapped_column(Text, default="{}")
    publish_confirmation: Mapped[str] = mapped_column(String(64), default="")
    publish_result: Mapped[str] = mapped_column(Text, default="{}")
    status: Mapped[str] = mapped_column(String(32), default="pending")
    failure_reason: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[str] = mapped_column(String(32), default="")


class CodePublishLock(Base):
    __tablename__ = "code_publish_locks"
    __table_args__ = (
        UniqueConstraint("project_id", "environment", "lock_key", name="uq_code_publish_lock_scope"),
    )

    id: Mapped[str] = mapped_column(String(16), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(16), index=True)
    environment: Mapped[str] = mapped_column(String(16), default="local")
    lock_key: Mapped[str] = mapped_column(String(255))
    run_id: Mapped[str] = mapped_column(String(16), index=True)
    acquired_at: Mapped[str] = mapped_column(String(32), default="")
    released_at: Mapped[str] = mapped_column(String(32), default="")


class CodeArtifact(Base):
    __tablename__ = "code_artifacts"

    id: Mapped[str] = mapped_column(String(16), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(16), unique=True, index=True)
    project_id: Mapped[str] = mapped_column(String(16), index=True)
    manifest_version: Mapped[int] = mapped_column(Integer)
    base_commit: Mapped[str] = mapped_column(String(128))
    diff_hash: Mapped[str] = mapped_column(String(64))
    policy_hash: Mapped[str] = mapped_column(String(64))
    verifier_report_hash: Mapped[str] = mapped_column(String(64))
    image: Mapped[str] = mapped_column(String(255), default="")
    image_id: Mapped[str] = mapped_column(String(255), default="")
    storage_path: Mapped[str] = mapped_column(String(1024))
    status: Mapped[str] = mapped_column(String(32), default="sealed")
    created_at: Mapped[str] = mapped_column(String(32), default="")


class CodeArtifactReview(Base):
    __tablename__ = "code_artifact_reviews"

    id: Mapped[str] = mapped_column(String(16), primary_key=True)
    artifact_id: Mapped[str] = mapped_column(String(16), unique=True, index=True)
    run_id: Mapped[str] = mapped_column(String(16), index=True)
    project_id: Mapped[str] = mapped_column(String(16), index=True)
    action: Mapped[str] = mapped_column(String(16), default="accepted")
    reviewer: Mapped[str] = mapped_column(String(64))
    manifest_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[str] = mapped_column(String(32), default="")


class CodeKillSwitch(Base):
    __tablename__ = "code_kill_switches"
    __table_args__ = (UniqueConstraint("scope", "target", name="uq_code_kill_switch_scope_target"),)

    id: Mapped[str] = mapped_column(String(16), primary_key=True)
    scope: Mapped[str] = mapped_column(String(16), index=True)
    target: Mapped[str] = mapped_column(String(512), default="*")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    reason: Mapped[str] = mapped_column(String(128), default="")
    updated_by: Mapped[str] = mapped_column(String(64), default="")
    updated_at: Mapped[str] = mapped_column(String(32), default="")


class CodePilotEvaluation(Base):
    __tablename__ = "code_pilot_evaluations"
    __table_args__ = (UniqueConstraint("pilot_id", "task_key", name="uq_code_pilot_task"),)

    id: Mapped[str] = mapped_column(String(16), primary_key=True)
    pilot_id: Mapped[str] = mapped_column(String(64), index=True)
    task_key: Mapped[str] = mapped_column(String(64))
    project_id: Mapped[str] = mapped_column(String(16), index=True)
    run_id: Mapped[str] = mapped_column(String(16), default="", index=True)
    risk_level: Mapped[str] = mapped_column(String(16), default="low")
    status: Mapped[str] = mapped_column(String(16), default="planned")
    result_status: Mapped[str] = mapped_column(String(32), default="")
    verifier_reproducible: Mapped[bool] = mapped_column(Boolean, default=False)
    human_accepted: Mapped[bool] = mapped_column(Boolean, default=False)
    cost_microunits: Mapped[int] = mapped_column(Integer, default=0)
    elapsed_milliseconds: Mapped[int] = mapped_column(Integer, default=0)
    cleanup_result: Mapped[str] = mapped_column(String(32), default="")
    created_at: Mapped[str] = mapped_column(String(32), default="")
    observed_at: Mapped[str] = mapped_column(String(32), default="")


class CodePilotReport(Base):
    __tablename__ = "code_pilot_reports"

    id: Mapped[str] = mapped_column(String(16), primary_key=True)
    pilot_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    artifact_path: Mapped[str] = mapped_column(String(1024))
    artifact_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), default="complete")
    created_at: Mapped[str] = mapped_column(String(32), default="")


class AgentTick(Base):
    __tablename__ = "agent_ticks"

    id: Mapped[int] = mapped_column(primary_key=True)
    tick_id: Mapped[str] = mapped_column(String(16), index=True)
    agent_id: Mapped[str] = mapped_column(String(16), index=True)
    session_id: Mapped[str] = mapped_column(String(16), index=True)
    cron: Mapped[str] = mapped_column(String(64), default="")
    message: Mapped[str] = mapped_column(Text, default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    creator: Mapped[str] = mapped_column(String(64), default="")


class ScheduledTask(Base):
    """Durable, session-scoped configuration for an automatic Agent task."""

    __tablename__ = "scheduled_tasks"
    __table_args__ = (
        UniqueConstraint("legacy_agent_tick_id", name="uq_scheduled_task_legacy_tick"),
    )

    id: Mapped[str] = mapped_column(String(16), primary_key=True)
    agent_id: Mapped[str] = mapped_column(String(16), index=True)
    session_id: Mapped[str] = mapped_column(String(64), index=True)
    owner_username: Mapped[str] = mapped_column(String(64), index=True)
    message: Mapped[str] = mapped_column(Text, default="")
    schedule_type: Mapped[str] = mapped_column(String(16), default="cron")
    cron: Mapped[str] = mapped_column(String(128), default="")
    interval_seconds: Mapped[int] = mapped_column(Integer, default=0)
    run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    timezone: Mapped[str] = mapped_column(String(64), default="Asia/Shanghai")
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    snapshot_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    config_snapshot: Mapped[str] = mapped_column(Text, default="{}")
    notification_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    notification_channel_id: Mapped[str] = mapped_column(String(16), default="", index=True)
    notification_chat_id: Mapped[str] = mapped_column(String(128), default="")
    legacy_agent_tick_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    migration_reason: Mapped[str] = mapped_column(Text, default="")
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow,
    )


class ScheduledTaskRun(Base):
    """One idempotent scheduled or manual execution attempt for a task."""

    __tablename__ = "scheduled_task_runs"
    __table_args__ = (
        UniqueConstraint("task_id", "occurrence_key", name="uq_scheduled_task_run_occurrence"),
    )

    id: Mapped[str] = mapped_column(String(16), primary_key=True)
    task_id: Mapped[str] = mapped_column(String(16), index=True)
    occurrence_key: Mapped[str] = mapped_column(String(96))
    source: Mapped[str] = mapped_column(String(16), default="scheduled")
    state: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    scheduled_for: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    queued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancel_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    attempt: Mapped[int] = mapped_column(Integer, default=0)
    lease_owner: Mapped[str] = mapped_column(String(64), default="")
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    agent_run_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    chat_message_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    config_snapshot: Mapped[str] = mapped_column(Text, default="{}")
    error_summary: Mapped[str] = mapped_column(Text, default="")


class ScheduledTaskProgress(Base):
    """Cross-process view of the steps produced by a scheduled Worker."""

    __tablename__ = "scheduled_task_progress"
    run_id: Mapped[str] = mapped_column(String(16), primary_key=True)
    steps: Mapped[str] = mapped_column(Text, default="[]")


class ScheduledTaskSessionSlot(Base):
    """A lease that serializes automatic work for one Agent session."""

    __tablename__ = "scheduled_task_session_slots"

    session_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    active_run_id: Mapped[str] = mapped_column(String(16), default="", index=True)
    lease_owner: Mapped[str] = mapped_column(String(64), default="")
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True,
    )


class ScheduledTaskNotificationDelivery(Base):
    """Outbox record for a scheduled-task result notification."""

    __tablename__ = "scheduled_task_notification_deliveries"
    __table_args__ = (
        UniqueConstraint("run_id", "channel_id", name="uq_scheduled_task_delivery_run_channel"),
    )

    id: Mapped[str] = mapped_column(String(16), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(16), index=True)
    channel_id: Mapped[str] = mapped_column(String(16), index=True)
    destination: Mapped[str] = mapped_column(String(128), default="")
    state: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    payload: Mapped[str] = mapped_column(Text, default="{}")
    error_summary: Mapped[str] = mapped_column(Text, default="")
    attempted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    agent_id: Mapped[str] = mapped_column(String(16), index=True)
    session_id: Mapped[str] = mapped_column(String(16), index=True)
    role: Mapped[str] = mapped_column(String(32))
    content: Mapped[str] = mapped_column(Text, default="")
    meta: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[str] = mapped_column(String(32), default="")


class ChatNote(Base):
    __tablename__ = "chat_notes"

    id: Mapped[int] = mapped_column(primary_key=True)
    agent_id: Mapped[str] = mapped_column(String(16), index=True)
    session_id: Mapped[str] = mapped_column(String(16), index=True)
    content: Mapped[str] = mapped_column(Text, default="")


class ChatSummary(Base):
    __tablename__ = "chat_summaries"

    id: Mapped[int] = mapped_column(primary_key=True)
    agent_id: Mapped[str] = mapped_column(String(16), index=True)
    session_id: Mapped[str] = mapped_column(String(16), index=True)
    chat_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    content: Mapped[str] = mapped_column(Text, default="")


class AgentRunState(Base):
    __tablename__ = "agent_run_states"
    __table_args__ = (UniqueConstraint("agent_id", "session_id", name="uq_agent_run_state"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    agent_id: Mapped[str] = mapped_column(String(16), index=True)
    session_id: Mapped[str] = mapped_column(String(16), index=True)
    state: Mapped[str] = mapped_column(Text, default="{}")
    updated_at: Mapped[str] = mapped_column(String(32), default="")


class AgentGroup(Base):
    __tablename__ = "agent_groups"

    id: Mapped[str] = mapped_column(String(16), primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    description: Mapped[str] = mapped_column(Text, default="")
    announcement: Mapped[str] = mapped_column(Text, default="")
    visibility: Mapped[str] = mapped_column(String(16), default="private")
    allowed_users: Mapped[str] = mapped_column(Text, default="[]")
    members: Mapped[str] = mapped_column(Text, default="[]")
    session_list: Mapped[str] = mapped_column(Text, default="[]")
    history_length: Mapped[int] = mapped_column(Integer, default=10)
    creator: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[str] = mapped_column(String(32), default="")
    modified_at: Mapped[str] = mapped_column(String(32), default="")

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "announcement": self.announcement,
            "visibility": self.visibility,
            "allowed_users": _json_list(self.allowed_users),
            "members": _json_list(self.members),
            "member_count": len(_json_list(self.members)),
            "session_list": _json_list(self.session_list),
            "history_length": self.history_length,
            "creator": self.creator,
            "created_at": self.created_at,
            "modified_at": self.modified_at,
        }


class GroupWorkflow(Base):
    __tablename__ = "group_workflows"

    id: Mapped[int] = mapped_column(primary_key=True)
    group_id: Mapped[str] = mapped_column(String(16), index=True)
    session_id: Mapped[str] = mapped_column(String(16), index=True)
    task: Mapped[str] = mapped_column(Text, default="")
    mode: Mapped[str] = mapped_column(String(32), default="auto")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    status: Mapped[str] = mapped_column(String(32), default="idle")
    creator: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[str] = mapped_column(String(32), default="")


class SqlServer(Base):
    __tablename__ = "sql_servers"

    id: Mapped[str] = mapped_column(String(16), primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    host: Mapped[str] = mapped_column(String(255))
    port: Mapped[int] = mapped_column(Integer, default=3306)
    db_type: Mapped[str] = mapped_column(String(32), default="mysql")
    username: Mapped[str] = mapped_column(String(128), default="")
    password_enc: Mapped[str] = mapped_column(Text, default="")
    database: Mapped[str] = mapped_column(String(128), default="")
    query_limit: Mapped[int] = mapped_column(Integer, default=100)
    connect_timeout: Mapped[int] = mapped_column(Integer, default=10)
    read_timeout: Mapped[int] = mapped_column(Integer, default=60)
    write_timeout: Mapped[int] = mapped_column(Integer, default=60)
    remark: Mapped[str] = mapped_column(Text, default="")
    creator: Mapped[str] = mapped_column(String(64), default="")

    def to_dict(self, mask_pw: bool = True) -> dict:
        from app.security import decrypt_secret, mask_secret
        pw = decrypt_secret(self.password_enc) if self.password_enc else ""
        return {
            "id": self.id,
            "name": self.name,
            "host": self.host,
            "port": self.port,
            "db_type": self.db_type,
            "username": self.username,
            "password": mask_secret(pw) if mask_pw else pw,
            "database": self.database,
            "query_limit": self.query_limit,
            "connect_timeout": self.connect_timeout,
            "read_timeout": self.read_timeout,
            "write_timeout": self.write_timeout,
            "remark": self.remark,
            "creator": self.creator,
        }


class SqlHistory(Base):
    __tablename__ = "sql_history"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64))
    server_id: Mapped[str] = mapped_column(String(16), default="")
    sql_text: Mapped[str] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(String(32), default="")


class SshServer(Base):
    __tablename__ = "ssh_servers"

    id: Mapped[str] = mapped_column(String(16), primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    host: Mapped[str] = mapped_column(String(255))
    port: Mapped[int] = mapped_column(Integer, default=22)
    username: Mapped[str] = mapped_column(String(128), default="root")
    auth_type: Mapped[str] = mapped_column(String(16), default="password")
    password_enc: Mapped[str] = mapped_column(Text, default="")
    private_key_enc: Mapped[str] = mapped_column(Text, default="")
    passphrase_enc: Mapped[str] = mapped_column(Text, default="")
    description: Mapped[str] = mapped_column(Text, default="")
    visibility: Mapped[str] = mapped_column(String(16), default="private")
    allowed_users: Mapped[str] = mapped_column(Text, default="[]")
    creator: Mapped[str] = mapped_column(String(64), default="")

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "host": self.host,
            "port": self.port,
            "username": self.username,
            "auth_type": self.auth_type,
            "description": self.description,
            "visibility": self.visibility,
            "allowed_users": _json_list(self.allowed_users),
            "creator": self.creator,
        }


class FileShare(Base):
    __tablename__ = "file_shares"

    id: Mapped[int] = mapped_column(primary_key=True)
    path: Mapped[str] = mapped_column(String(1024))
    name: Mapped[str] = mapped_column(String(255))
    share_token: Mapped[str] = mapped_column(String(64), unique=True)
    visibility: Mapped[str] = mapped_column(String(16), default="private")
    allowed_users: Mapped[str] = mapped_column(Text, default="[]")
    creator: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[str] = mapped_column(String(32), default="")


class RagCorpus(Base):
    __tablename__ = "rag_corpus"

    id: Mapped[str] = mapped_column(String(16), primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    description: Mapped[str] = mapped_column(Text, default="")
    file_path: Mapped[str] = mapped_column(String(512), default="")
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    index_status: Mapped[str] = mapped_column(String(16), default="pending")
    index_error: Mapped[str] = mapped_column(Text, default="")
    visibility: Mapped[str] = mapped_column(String(16), default="private")
    allowed_users: Mapped[str] = mapped_column(Text, default="[]")
    creator: Mapped[str] = mapped_column(String(64), default="")
    modified_at: Mapped[str] = mapped_column(String(32), default="")

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "file_path": self.file_path,
            "chunk_count": self.chunk_count,
            "index_status": self.index_status or "pending",
            "index_error": self.index_error or "",
            "visibility": self.visibility,
            "allowed_users": _json_list(self.allowed_users),
            "creator": self.creator,
            "modified_at": self.modified_at,
        }


class HttpMcp(Base):
    __tablename__ = "http_mcps"

    id: Mapped[str] = mapped_column(String(16), primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    version: Mapped[str] = mapped_column(String(32), default="1.0.0")
    url: Mapped[str] = mapped_column(String(512), default="")
    method: Mapped[str] = mapped_column(String(16), default="POST")
    headers: Mapped[str] = mapped_column(Text, default="{}")
    body_template: Mapped[str] = mapped_column(Text, default="")
    description: Mapped[str] = mapped_column(Text, default="")
    auth_json: Mapped[str] = mapped_column(Text, default="[]")
    tools_json: Mapped[str] = mapped_column(Text, default="[]")
    visibility: Mapped[str] = mapped_column(String(16), default="private")
    allowed_users: Mapped[str] = mapped_column(Text, default="[]")
    creator: Mapped[str] = mapped_column(String(64), default="")
    modified_at: Mapped[str] = mapped_column(String(32), default="")

    def _tools(self) -> list:
        tools = _json_list(self.tools_json)
        if tools:
            return tools
        # Legacy single-endpoint → one synthetic tool
        if self.url:
            try:
                hdr = json.loads(self.headers or "{}")
            except Exception:
                hdr = {}
            headers = [{"key": k, "value": str(v)} for k, v in (hdr or {}).items()]
            return [
                {
                    "id": "default",
                    "name": self.name or "default",
                    "method": self.method or "POST",
                    "url": self.url,
                    "description": self.description or "",
                    "timeout": 300,
                    "headers": headers,
                    "fixed_args": [],
                    "args": [],
                    "body_template": self.body_template or "",
                }
            ]
        return []

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "version": self.version or "1.0.0",
            "url": self.url,
            "method": self.method,
            "headers": self.headers,
            "body_template": self.body_template,
            "description": self.description,
            "auth": _json_list(self.auth_json),
            "tools": self._tools(),
            "visibility": self.visibility,
            "allowed_users": _json_list(self.allowed_users),
            "creator": self.creator,
            "modified_at": self.modified_at,
        }


class SiteConfig(Base):
    __tablename__ = "site_config"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    key: Mapped[str] = mapped_column(String(64), unique=True)
    value: Mapped[str] = mapped_column(Text, default="")


class RoleGrant(Base):
    __tablename__ = "role_grants"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), index=True)
    roles: Mapped[str] = mapped_column(Text, default="[]")


class RoleDefinition(Base):
    __tablename__ = "role_definitions"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    label: Mapped[str] = mapped_column(String(128), default="")
    permissions: Mapped[str] = mapped_column(Text, default="[]")
    builtin: Mapped[bool] = mapped_column(Boolean, default=False)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "label": self.label,
            "permissions": _json_list(self.permissions),
            "builtin": self.builtin,
        }


class RagChunk(Base):
    __tablename__ = "rag_chunks"

    id: Mapped[int] = mapped_column(primary_key=True)
    corpus_id: Mapped[str] = mapped_column(String(16), index=True)
    text: Mapped[str] = mapped_column(Text, default="")
    embedding: Mapped[str] = mapped_column(Text, default="[]")
    embedding_vec: Mapped[str] = mapped_column(Text, default="")
    chunk_index: Mapped[int] = mapped_column(Integer, default=0)


class GroupChatMessage(Base):
    __tablename__ = "group_chat_messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    group_id: Mapped[str] = mapped_column(String(16), index=True)
    session_id: Mapped[str] = mapped_column(String(16), index=True)
    role: Mapped[str] = mapped_column(String(32))
    content: Mapped[str] = mapped_column(Text, default="")
    agent_id: Mapped[str] = mapped_column(String(16), default="")
    created_at: Mapped[str] = mapped_column(String(32), default="")


class ImChannel(Base):
    """External IM bot channel (Feishu / DingTalk / Telegram / QQ / WeCom / mock)."""

    __tablename__ = "im_channels"

    id: Mapped[str] = mapped_column(String(16), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), default="")
    provider: Mapped[str] = mapped_column(String(32), index=True)  # mock|feishu|dingtalk|telegram|qq|wecom
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    agent_id: Mapped[str] = mapped_column(String(16), default="", index=True)
    # Non-secret + secret fields as JSON; secrets stored encrypted via encrypt_secret on whole blob
    config_enc: Mapped[str] = mapped_column(Text, default="")
    webhook_secret: Mapped[str] = mapped_column(String(64), default="")  # path token for URL
    last_error: Mapped[str] = mapped_column(Text, default="")
    last_event_at: Mapped[str] = mapped_column(String(32), default="")
    creator: Mapped[str] = mapped_column(String(64), default="")
    modified_at: Mapped[str] = mapped_column(String(32), default="")
    visibility: Mapped[str] = mapped_column(String(16), default="private")
    allowed_users: Mapped[str] = mapped_column(Text, default="[]")

    def get_config(self) -> dict:
        from app.security import decrypt_secret
        raw = decrypt_secret(self.config_enc) if self.config_enc else "{}"
        try:
            data = json.loads(raw)
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def set_config(self, cfg: dict) -> None:
        from app.security import encrypt_secret
        self.config_enc = encrypt_secret(json.dumps(cfg or {}, ensure_ascii=False))

    def to_dict(self, mask_secrets: bool = True) -> dict:
        from app.security import mask_secret
        cfg = self.get_config()
        if mask_secrets:
            secret_keys = {
                "app_secret", "secret", "bot_token", "access_token", "encrypt_key",
                "encoding_aes_key", "client_secret", "token", "verification_token",
            }
            cfg = {
                k: (mask_secret(str(v)) if k in secret_keys and v else v)
                for k, v in cfg.items()
            }
        return {
            "id": self.id,
            "name": self.name,
            "provider": self.provider,
            "enabled": self.enabled,
            "agent_id": self.agent_id,
            "config": cfg,
            "webhook_secret": self.webhook_secret,
            "webhook_path": f"/hooks/channels/{self.provider}/{self.id}/{self.webhook_secret}",
            "last_error": self.last_error,
            "last_event_at": self.last_event_at,
            "creator": self.creator,
            "modified_at": self.modified_at,
            "visibility": self.visibility or "private",
            "allowed_users": _json_list(self.allowed_users),
        }


class ImSession(Base):
    __tablename__ = "im_sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    channel_id: Mapped[str] = mapped_column(String(16), index=True)
    external_chat_id: Mapped[str] = mapped_column(String(128), index=True)
    external_user_id: Mapped[str] = mapped_column(String(128), default="")
    agent_id: Mapped[str] = mapped_column(String(16), index=True)
    agent_session_id: Mapped[str] = mapped_column(String(32), index=True)
    updated_at: Mapped[str] = mapped_column(String(32), default="")


class ImDedup(Base):
    __tablename__ = "im_dedup"

    id: Mapped[int] = mapped_column(primary_key=True)
    channel_id: Mapped[str] = mapped_column(String(16), index=True)
    msg_id: Mapped[str] = mapped_column(String(128), index=True)
    created_at: Mapped[str] = mapped_column(String(32), default="")


class ImEventLog(Base):
    __tablename__ = "im_event_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    channel_id: Mapped[str] = mapped_column(String(16), index=True)
    level: Mapped[str] = mapped_column(String(16), default="info")
    message: Mapped[str] = mapped_column(Text, default="")
    detail: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[str] = mapped_column(String(32), default="")


class ReleaseManifestRecord(Base):
    __tablename__ = "release_manifest_records"

    release_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    target_id: Mapped[str] = mapped_column(String(64), index=True)
    version: Mapped[str] = mapped_column(String(64), default="")
    git_tag: Mapped[str] = mapped_column(String(128), default="")
    commit_sha: Mapped[str] = mapped_column(String(64), default="")
    api_image: Mapped[str] = mapped_column(String(512), default="")
    web_image: Mapped[str] = mapped_column(String(512), default="")
    created_at: Mapped[str] = mapped_column(String(32), default="")


class ReleaseLifecycleAudit(Base):
    __tablename__ = "release_lifecycle_audits"
    __table_args__ = (UniqueConstraint("release_id", "status", name="uq_release_terminal_status"),)

    id: Mapped[str] = mapped_column(String(16), primary_key=True)
    release_id: Mapped[str] = mapped_column(String(64), index=True)
    target_id: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(32), index=True)
    health_result: Mapped[str] = mapped_column(String(32), default="")
    rollback_result: Mapped[str] = mapped_column(String(32), default="")
    failure_summary: Mapped[str] = mapped_column(Text, default="")
    trigger_source: Mapped[str] = mapped_column(String(32), default="runner")
    occurred_at: Mapped[str] = mapped_column(String(32), index=True)


class ReleaseRollbackRequest(Base):
    __tablename__ = "release_rollback_requests"

    id: Mapped[str] = mapped_column(String(16), primary_key=True)
    release_id: Mapped[str] = mapped_column(String(64), index=True)
    target_id: Mapped[str] = mapped_column(String(64), index=True)
    requested_by: Mapped[str] = mapped_column(String(64), default="")
    status: Mapped[str] = mapped_column(String(32), default="pending")
    requested_at: Mapped[str] = mapped_column(String(32), default="")
    completed_at: Mapped[str] = mapped_column(String(32), default="")


class ReleaseCallbackDelivery(Base):
    __tablename__ = "release_callback_deliveries"
    __table_args__ = (UniqueConstraint("release_id", "status", name="uq_release_callback_status"),)

    id: Mapped[str] = mapped_column(String(16), primary_key=True)
    release_id: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(32), index=True)
    delivery_state: Mapped[str] = mapped_column(String(32), default="received")
    attempts: Mapped[int] = mapped_column(Integer, default=1)
    received_at: Mapped[str] = mapped_column(String(32), default="")


class ReleaseHookDelivery(Base):
    __tablename__ = "release_hook_deliveries"

    delivery_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    release_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    target_id: Mapped[str] = mapped_column(String(64), index=True)
    state: Mapped[str] = mapped_column(String(32), default="accepted")
    accepted_at: Mapped[str] = mapped_column(String(32), default="")


class ReleaseVersionSync(Base):
    """Auditable, idempotent public-site version synchronization attempt."""

    __tablename__ = "release_version_syncs"
    __table_args__ = (UniqueConstraint("release_id", "transition", name="uq_release_version_sync_transition"),)

    id: Mapped[str] = mapped_column(String(16), primary_key=True)
    release_id: Mapped[str] = mapped_column(String(64), index=True)
    target_id: Mapped[str] = mapped_column(String(64), index=True)
    transition: Mapped[str] = mapped_column(String(32), index=True)
    version: Mapped[str] = mapped_column(String(64), default="")
    commit_sha: Mapped[str] = mapped_column(String(64), default="")
    state: Mapped[str] = mapped_column(String(32), default="applied")
    detail: Mapped[str] = mapped_column(Text, default="")
    occurred_at: Mapped[str] = mapped_column(String(32), default="")


class ReleaseNotificationDelivery(Base):
    """Deduplicated, sanitized lifecycle notification delivery audit."""

    __tablename__ = "release_notification_deliveries"
    __table_args__ = (UniqueConstraint("release_id", "transition", "channel_id", name="uq_release_notification_delivery"),)

    id: Mapped[str] = mapped_column(String(16), primary_key=True)
    release_id: Mapped[str] = mapped_column(String(64), index=True)
    transition: Mapped[str] = mapped_column(String(32), index=True)
    channel_id: Mapped[str] = mapped_column(String(16), index=True)
    state: Mapped[str] = mapped_column(String(32), default="pending")
    payload: Mapped[str] = mapped_column(Text, default="{}")
    error: Mapped[str] = mapped_column(Text, default="")
    attempted_at: Mapped[str] = mapped_column(String(32), default="")
    delivered_at: Mapped[str] = mapped_column(String(32), default="")
