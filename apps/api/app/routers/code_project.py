"""RBAC and ACL protected Code Project control-plane API."""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.deps import can_access_page, get_session_user, _json_list
from app.models import (
    CodeControlAudit,
    CodeProject,
    CodeProjectManifest,
    CodeRepositorySource,
    User,
)
from app.schemas import fail, ok
from app.security import new_id, now_str
from app.services.code_agent.control_plane import (
    can_use_code_project,
    project_availability,
)
from app.services.code_agent.authorization import (
    CodeAuthorizationError,
    require_project_creator,
    require_project_owner,
)
from app.services.code_agent.git_importer import (
    RestrictedGitImportError,
    normalize_requested_ref,
)
from app.services.code_agent.local_source import (
    LocalRepositoryPolicyError,
    normalize_local_repository_locator,
)
from app.services.code_agent.manifest_publish import (
    ManifestPublishError,
    build_manifest_publish_service,
)
from app.services.code_agent.secret_store import DeployTokenSecretStore, SecretReferenceError
from app.services.code_agent.source_policy import (
    RepositorySourcePolicyError,
    normalize_remote_source,
    normalize_remote_source_syntax,
    normalize_repository_allowlist,
)


PAGE_PERMISSION = "/pages/page_code_project.cgi"
SUPPORTED_ENVIRONMENT_TIER = "internal_non_production"
PROJECT_VISIBILITIES = {"private", "public"}

router = APIRouter(prefix=PAGE_PERMISSION, tags=["code-projects"])


class CodeProjectBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: str | None = None
    id: str | None = None
    project_id: str | None = None
    manifest_id: str | None = None
    name: str | None = None
    description: str | None = None
    enabled: bool | None = None
    environment_tier: str | None = None
    visibility: str | None = None
    allowed_users: list[str] | None = None
    repository: str | None = None
    base_commit: str | None = None
    source_type: str | None = None
    source_locator: str | None = None
    credential_ref: str | None = None
    credential_label: str | None = None
    credential_username: str | None = None
    credential_password: str | None = None
    requested_ref: str | None = None
    allowed_paths: list[str] | None = None
    validation_plan: list[dict[str, Any]] | None = None
    trusted_image: str | None = None
    image_digest: str | None = None
    allowed_tools: list[str] | None = None
    coding_runtime: str | None = None
    policy: dict[str, Any] | None = None
    budgets: dict[str, int] | None = None
    workspace_retention_hours: int | None = None


class ProjectAvailabilityResponse(BaseModel):
    ready: bool
    status: str
    reason: str
    detail: str = ""
    manifest_id: str | None = None
    manifest_version: int | None = None


class CodeProjectResponse(BaseModel):
    id: str
    name: str
    organization_id: str = "default"
    description: str
    enabled: bool
    environment_tier: str
    visibility: str
    allowed_users: list[str] = Field(default_factory=list)
    workspace_retention_hours: int | None = None
    creator: str
    created_at: str
    modified_at: str
    availability: ProjectAvailabilityResponse


class ManifestResponse(BaseModel):
    id: str
    project_id: str
    version: int
    status: str
    source_id: str = ""
    source_type: str = ""
    source_locator: str = ""
    credential_ref: str = ""
    requested_ref: str = ""
    resolved_commit: str = ""
    snapshot_id: str = ""
    snapshot_hash: str = ""
    repository: str = ""
    base_commit: str = ""
    allowed_paths: list[str] = Field(default_factory=list)
    validation_plan: list[dict[str, Any]] = Field(default_factory=list)
    trusted_image: str = ""
    image_digest: str = ""
    allowed_tools: list[str] = Field(default_factory=list)
    coding_runtime: str = "legacy"
    policy: dict[str, Any] = Field(default_factory=dict)
    budgets: dict[str, int] = Field(default_factory=dict)
    validation_errors: dict[str, str] = Field(default_factory=dict)
    security_validation: dict[str, Any] = Field(default_factory=dict)
    published_at: str = ""
    created_at: str = ""


class CredentialReferenceResponse(BaseModel):
    reference_id: str
    organization_id: str
    label: str
    credential_kind: str
    auth_username: str
    allowed_project_ids: list[str] = Field(default_factory=list)
    read_only: bool
    status: str


class ManifestOptionsResponse(BaseModel):
    source_types: list[str] = Field(default_factory=list)
    remote_origins: list[str] = Field(default_factory=list)
    local_roots: list[str] = Field(default_factory=list)
    credential_references: list[CredentialReferenceResponse] = Field(default_factory=list)


def _has_page_permission(user: User, db: Session) -> bool:
    roles = _json_list(user.roles)
    return "master" in roles or "admin" in roles or can_access_page(user, PAGE_PERMISSION, db)


def _project_dict(db: Session, project: CodeProject) -> dict[str, Any]:
    return {
        "id": project.id,
        "name": project.name,
        "organization_id": project.organization_id or "default",
        "description": project.description or "",
        "enabled": bool(project.enabled),
        "environment_tier": project.environment_tier,
        "visibility": project.visibility,
        "allowed_users": _json_list(project.allowed_users),
        "workspace_retention_hours": project.workspace_retention_hours,
        "creator": project.creator,
        "created_at": project.created_at,
        "modified_at": project.modified_at,
        "availability": project_availability(db, project),
    }


def _visible_projects(db: Session, user: User) -> list[CodeProject]:
    return [project for project in db.query(CodeProject).all() if can_use_code_project(user, project)]


def _decode_json(raw: str, fallback):
    try:
        value = json.loads(raw or "")
    except (TypeError, json.JSONDecodeError):
        return fallback
    return value if isinstance(value, type(fallback)) else fallback


def _manifest_validation_errors(manifest: CodeProjectManifest) -> dict[str, str]:
    errors: dict[str, str] = {}
    secure_source = bool(
        (manifest.security_schema_version or 0) >= 1
        or manifest.source_id
        or manifest.source_type
        or manifest.credential_ref
        or manifest.requested_ref
    )
    if secure_source:
        if not manifest.source_type.strip():
            errors["source_type"] = "manifest_missing_source_type"
        if not manifest.repository.strip():
            errors["source_locator"] = "manifest_missing_source_locator"
        if not manifest.source_id.strip() and manifest.repository.strip():
            errors["source_locator"] = "manifest_source_not_validated"
        if not manifest.requested_ref.strip():
            errors["requested_ref"] = "manifest_missing_requested_ref"
        if manifest.status == "draft":
            settings = get_settings()
            try:
                if manifest.source_type == "local":
                    normalize_local_repository_locator(
                        manifest.repository,
                        allowed_roots=settings.code_local_repository_roots,
                    )
                elif manifest.source_type:
                    normalized = normalize_remote_source(
                        manifest.repository,
                        allowlist=settings.code_repository_allowlist,
                    )
                    if normalized.source_type != manifest.source_type:
                        raise RepositorySourcePolicyError()
            except (RepositorySourcePolicyError, LocalRepositoryPolicyError):
                errors["source_locator"] = "repository_source_not_allowed"
    else:
        if not manifest.repository.strip():
            errors["repository"] = "manifest_missing_repository"
        if not manifest.base_commit.strip():
            errors["base_commit"] = "manifest_missing_base_commit"
    return errors


def _manifest_dict(manifest: CodeProjectManifest) -> dict[str, Any]:
    validation_errors = _manifest_validation_errors(manifest)
    if manifest.status == "published" and manifest.security_schema_version >= 1:
        security_status = "validated"
    elif validation_errors:
        security_status = "draft_incomplete"
    else:
        security_status = "ready_for_validation"
    policy = _decode_json(manifest.policy, {})
    coding_runtime = str(policy.get("coding_runtime") or "legacy")
    return {
        "id": manifest.id,
        "project_id": manifest.project_id,
        "version": manifest.version,
        "status": manifest.status,
        "source_id": manifest.source_id or "",
        "source_type": manifest.source_type or "",
        "source_locator": manifest.repository or "",
        "credential_ref": manifest.credential_ref or "",
        "requested_ref": manifest.requested_ref or "",
        "resolved_commit": manifest.resolved_commit or "",
        "snapshot_id": manifest.snapshot_id or "",
        "snapshot_hash": manifest.snapshot_hash or "",
        "repository": manifest.repository,
        "base_commit": manifest.base_commit,
        "allowed_paths": _decode_json(manifest.allowed_paths, []),
        "validation_plan": _decode_json(manifest.validation_plan, []),
        "trusted_image": manifest.trusted_image,
        "image_digest": manifest.image_digest or "",
        "allowed_tools": _decode_json(manifest.allowed_tools, []),
        "coding_runtime": coding_runtime,
        "policy": policy,
        "budgets": _decode_json(manifest.budgets, {}),
        "validation_errors": validation_errors,
        "security_validation": {
            "ready": not validation_errors,
            "status": security_status,
            "errors": validation_errors,
        },
        "published_at": manifest.published_at,
        "created_at": manifest.created_at,
    }


def _settings_list(raw: str) -> list[str]:
    value = _decode_json(raw, [])
    return [str(item) for item in value if isinstance(item, str)]


def _manifest_options(db: Session, user: User, project: CodeProject) -> dict[str, Any]:
    settings = get_settings()
    readiness = settings.code_agent_security_readiness()
    allowlist = _settings_list(settings.code_repository_allowlist)
    try:
        origins = sorted(normalize_repository_allowlist(allowlist)) if allowlist else []
    except RepositorySourcePolicyError:
        origins = []
    if "repository_ssh_known_hosts_file" in readiness["errors"]:
        origins = [origin for origin in origins if not origin.startswith("ssh://")]
    local_roots = _settings_list(settings.code_local_repository_roots)
    if "local_repository_roots" in readiness["errors"]:
        local_roots = []
    credentials = [
        item.to_dict()
        for item in DeployTokenSecretStore(db).project_metadata(actor=user, project=project)
    ]
    source_types = {origin.split(":", 1)[0] for origin in origins}
    if local_roots:
        source_types.add("local")
    return ManifestOptionsResponse.model_validate({
        "source_types": sorted(source_types),
        "remote_origins": origins,
        "local_roots": local_roots,
        "credential_references": credentials,
    }).model_dump()


def _secure_draft_values(
    db: Session,
    user: User,
    project: CodeProject,
    manifest: CodeProjectManifest,
    body: CodeProjectBody,
) -> tuple[dict[str, str], dict[str, str]]:
    values = {
        "source_type": (
            body.source_type.strip()
            if body.source_type is not None
            else (manifest.source_type or "").strip()
        ),
        "source_locator": (
            body.source_locator.strip()
            if body.source_locator is not None
            else (
                body.repository.strip()
                if body.repository is not None
                else (manifest.repository or "").strip()
            )
        ),
        "credential_ref": (
            body.credential_ref.strip()
            if body.credential_ref is not None
            else (manifest.credential_ref or "").strip()
        ),
        "requested_ref": (
            body.requested_ref.strip()
            if body.requested_ref is not None
            else (
                body.base_commit.strip()
                if body.base_commit is not None
                else (manifest.requested_ref or manifest.base_commit or "").strip()
            )
        ),
    }
    errors: dict[str, str] = {}
    settings = get_settings()
    source_type = values["source_type"]
    locator = values["source_locator"]
    if source_type and source_type not in {"https", "ssh", "http", "local"}:
        errors["source_type"] = "repository_source_not_allowed"
    if source_type and locator:
        try:
            if source_type == "local":
                values["source_locator"] = str(normalize_local_repository_locator(
                    locator,
                    allowed_roots=settings.code_local_repository_roots,
                ))
            else:
                normalized = normalize_remote_source_syntax(locator)
                if normalized.source_type != source_type:
                    raise RepositorySourcePolicyError()
                values["source_locator"] = normalized.locator
        except (RepositorySourcePolicyError, LocalRepositoryPolicyError):
            errors["source_locator"] = "repository_source_not_allowed"
    if values["requested_ref"]:
        try:
            values["requested_ref"] = normalize_requested_ref(values["requested_ref"])
        except RestrictedGitImportError:
            errors["requested_ref"] = "repository_ref_invalid"
    if values["credential_ref"]:
        visible_references = {
            item.reference_id
            for item in DeployTokenSecretStore(db).project_metadata(
                actor=user,
                project=project,
            )
        }
        if values["credential_ref"] not in visible_references:
            errors["credential_ref"] = "repository_credential_not_allowed"
    return values, errors


def _inline_credential_requested(body: CodeProjectBody) -> bool:
    return bool(
        (body.credential_label or "").strip()
        or (body.credential_username or "").strip()
        or (body.credential_password or "")
    )


def _create_inline_credential(
    db: Session,
    user: User,
    project: CodeProject,
    body: CodeProjectBody,
) -> tuple[str, dict[str, str]]:
    username = (body.credential_username or "").strip()
    password = body.credential_password or ""
    errors: dict[str, str] = {}
    if not username:
        errors["credential_username"] = "repository_credential_username_required"
    if not password:
        errors["credential_password"] = "repository_credential_password_required"
    if errors:
        return "", errors
    try:
        credential = DeployTokenSecretStore(db).assign(
            actor=user,
            organization_id=project.organization_id or "default",
            label=(body.credential_label or "").strip() or f"{project.name} Git read only",
            value=password,
            allowed_project_ids=[project.id],
            auth_username=username,
        )
    except CodeAuthorizationError as exc:
        return "", {"credential_ref": exc.reason}
    except SecretReferenceError as exc:
        return "", {"credential_ref": exc.reason}
    return credential.reference_id, {}


def _validate_project_input(body: CodeProjectBody, *, creating: bool) -> str | None:
    if creating and not (body.name or "").strip():
        return "project_name_required"
    if body.environment_tier is not None and body.environment_tier != SUPPORTED_ENVIRONMENT_TIER:
        return "project_environment_not_allowed"
    if body.visibility is not None and body.visibility not in PROJECT_VISIBILITIES:
        return "project_visibility_invalid"
    if body.workspace_retention_hours is not None:
        if body.workspace_retention_hours < 0:
            return "project_workspace_retention_invalid"
        if body.workspace_retention_hours > get_settings().code_workspace_retention_hours:
            return "project_workspace_retention_exceeds_platform"
    return None


def _require_visible_project(db: Session, user: User, project_id: str | None) -> CodeProject | None:
    project = db.query(CodeProject).filter(CodeProject.id == (project_id or "")).first()
    return project if can_use_code_project(user, project) else None


def _require_owned_project(db: Session, user: User, project_id: str | None) -> CodeProject | None:
    project = db.query(CodeProject).filter(CodeProject.id == (project_id or "")).first()
    try:
        return require_project_owner(user, project, db=db)
    except CodeAuthorizationError:
        return None


def _audit_write(
    db: Session,
    user: User,
    action: str,
    project_id: str,
    *,
    manifest_id: str = "",
    details: dict[str, Any] | None = None,
) -> None:
    db.add(CodeControlAudit(
        id=new_id(),
        actor=user.username,
        action=action,
        project_id=project_id,
        manifest_id=manifest_id,
        details=json.dumps(details or {}, sort_keys=True),
        created_at=now_str(),
    ))


@router.get("")
async def code_project_get(
    action: str = Query("list"),
    id: str = Query(None),
    user: User = Depends(get_session_user),
    db: Session = Depends(get_db),
):
    if not _has_page_permission(user, db):
        return fail("code_project_page_unauthorized")
    if action == "list":
        return ok([_project_dict(db, project) for project in _visible_projects(db, user)])
    if action == "get":
        project = _require_visible_project(db, user, id)
        return ok(_project_dict(db, project)) if project else fail("code_project_not_found")
    if action == "get_draft":
        project = _require_owned_project(db, user, id)
        if not project:
            return fail("code_project_not_found")
        manifest = (
            db.query(CodeProjectManifest)
            .filter(
                CodeProjectManifest.project_id == project.id,
                CodeProjectManifest.status == "draft",
            )
            .order_by(CodeProjectManifest.version.desc())
            .first()
        )
        return ok(_manifest_dict(manifest) if manifest else None)
    if action == "manifest_options":
        project = _require_owned_project(db, user, id)
        if not project:
            return fail("code_project_not_found")
        return ok(_manifest_options(db, user, project))
    if action == "history":
        project = _require_owned_project(db, user, id)
        if not project:
            return fail("code_project_not_found")
        query = db.query(CodeProjectManifest).filter(
            CodeProjectManifest.project_id == project.id,
            CodeProjectManifest.status == "published",
        )
        rows = query.order_by(CodeProjectManifest.version.desc()).all()
        return ok([_manifest_dict(row) for row in rows])
    return fail("code_project_action_unknown")


@router.post("")
async def code_project_post(
    body: CodeProjectBody,
    user: User = Depends(get_session_user),
    db: Session = Depends(get_db),
):
    if not _has_page_permission(user, db):
        return fail("code_project_page_unauthorized")
    action = body.action or "create"
    if action == "create":
        try:
            require_project_creator(user, db=db)
        except CodeAuthorizationError as exc:
            return fail(exc.reason)
        if reason := _validate_project_input(body, creating=True):
            return fail(reason)
        name = (body.name or "").strip()
        if db.query(CodeProject).filter(CodeProject.name == name).first():
            return fail("code_project_name_exists")
        timestamp = now_str()
        project = CodeProject(
            id=new_id(),
            name=name,
            organization_id=getattr(user, "organization_id", "default") or "default",
            description=(body.description or "").strip(),
            enabled=True if body.enabled is None else body.enabled,
            environment_tier=body.environment_tier or SUPPORTED_ENVIRONMENT_TIER,
            visibility=body.visibility or "private",
            allowed_users=json.dumps(body.allowed_users or []),
            workspace_retention_hours=body.workspace_retention_hours,
            creator=user.username,
            created_at=timestamp,
            modified_at=timestamp,
        )
        db.add(project)
        _audit_write(db, user, "project_create", project.id)
        db.commit()
        return ok(_project_dict(db, project), "创建成功")
    if action in {"update", "set_enabled"}:
        project = _require_owned_project(db, user, body.id or body.project_id)
        if not project:
            return fail("code_project_not_found")
        if action == "set_enabled":
            if body.enabled is None:
                return fail("project_enabled_required")
            project.enabled = body.enabled
        else:
            if reason := _validate_project_input(body, creating=False):
                return fail(reason)
            if body.name is not None:
                name = body.name.strip()
                if not name:
                    return fail("project_name_required")
                duplicate = db.query(CodeProject).filter(
                    CodeProject.name == name,
                    CodeProject.id != project.id,
                ).first()
                if duplicate:
                    return fail("code_project_name_exists")
                project.name = name
            if body.description is not None:
                project.description = body.description.strip()
            if body.environment_tier is not None:
                project.environment_tier = body.environment_tier
            if body.visibility is not None:
                project.visibility = body.visibility
            if body.allowed_users is not None:
                project.allowed_users = json.dumps(body.allowed_users)
            if body.workspace_retention_hours is not None:
                project.workspace_retention_hours = body.workspace_retention_hours
        project.modified_at = now_str()
        _audit_write(
            db,
            user,
            "project_enable" if action == "set_enabled" else "project_update",
            project.id,
            details={"enabled": project.enabled} if action == "set_enabled" else {},
        )
        db.commit()
        return ok(_project_dict(db, project), "保存成功")
    if action == "save_draft":
        project = _require_owned_project(db, user, body.project_id)
        if not project:
            return fail("code_project_not_found")
        manifest = None
        if body.manifest_id:
            manifest = db.query(CodeProjectManifest).filter(
                CodeProjectManifest.id == body.manifest_id,
                CodeProjectManifest.project_id == project.id,
            ).first()
            if not manifest:
                return fail("manifest_not_found")
            if manifest.status != "draft":
                return fail("manifest_published_immutable")
        if manifest is None:
            manifest = (
                db.query(CodeProjectManifest)
                .filter(
                    CodeProjectManifest.project_id == project.id,
                    CodeProjectManifest.status == "draft",
                )
                .order_by(CodeProjectManifest.version.desc())
                .first()
            )
        if manifest is None:
            latest = (
                db.query(CodeProjectManifest.version)
                .filter(CodeProjectManifest.project_id == project.id)
                .order_by(CodeProjectManifest.version.desc())
                .first()
            )
            manifest = CodeProjectManifest(
                id=new_id(),
                project_id=project.id,
                version=(latest[0] + 1) if latest else 1,
                status="draft",
                created_at=now_str(),
            )
        secure_fields = {
            "source_type",
            "source_locator",
            "credential_ref",
            "requested_ref",
        }
        inline_credential = _inline_credential_requested(body)
        structured_input = bool(secure_fields & body.model_fields_set) or inline_credential
        secure_values: dict[str, str] = {}
        if structured_input:
            secure_values, secure_errors = _secure_draft_values(
                db,
                user,
                project,
                manifest,
                body,
            )
            if secure_errors:
                return fail(
                    "manifest_security_selection_invalid",
                    data={"field_errors": secure_errors},
                )
            if inline_credential:
                credential_ref, credential_errors = _create_inline_credential(
                    db,
                    user,
                    project,
                    body,
                )
                if credential_errors:
                    return fail(
                        "manifest_security_selection_invalid",
                        data={"field_errors": credential_errors},
                    )
                body_with_credential = body.model_copy(update={"credential_ref": credential_ref})
                secure_values, secure_errors = _secure_draft_values(
                    db,
                    user,
                    project,
                    manifest,
                    body_with_credential,
                )
                if secure_errors:
                    return fail(
                        "manifest_security_selection_invalid",
                        data={"field_errors": secure_errors},
                    )
        if manifest not in db:
            db.add(manifest)
        for attr in ("repository", "base_commit"):
            value = getattr(body, attr)
            if value is not None:
                setattr(manifest, attr, value.strip())
        if structured_input:
            timestamp = now_str()
            source_type = secure_values["source_type"]
            source_locator = secure_values["source_locator"]
            manifest.source_type = source_type
            manifest.repository = source_locator
            manifest.credential_ref = secure_values["credential_ref"]
            manifest.requested_ref = secure_values["requested_ref"]
            manifest.base_commit = secure_values["requested_ref"]
            manifest.security_schema_version = max(
                manifest.security_schema_version or 0,
                1,
            )
            if source_type and source_locator:
                source = (
                    db.query(CodeRepositorySource)
                    .filter(
                        CodeRepositorySource.id == (manifest.source_id or ""),
                        CodeRepositorySource.project_id == project.id,
                        CodeRepositorySource.status == "draft",
                    )
                    .first()
                )
                if source is None:
                    source = CodeRepositorySource(
                        id=new_id(),
                        project_id=project.id,
                        status="draft",
                        created_by=user.username,
                        created_at=timestamp,
                    )
                    db.add(source)
                source.source_type = source_type
                source.locator = source_locator
                source.credential_ref = secure_values["credential_ref"]
                source.requested_ref = secure_values["requested_ref"]
                source.updated_at = timestamp
                manifest.source_id = source.id
            else:
                manifest.source_id = ""
        _audit_write(db, user, "manifest_draft_save", project.id, manifest_id=manifest.id)
        db.commit()
        return ok(_manifest_dict(manifest), "草稿已保存")
    if action == "publish":
        project = _require_owned_project(db, user, body.project_id)
        if not project:
            return fail("code_project_not_found")
        if project.environment_tier != SUPPORTED_ENVIRONMENT_TIER:
            return fail(
                "project_environment_not_allowed",
                data={"field_errors": {"environment_tier": "project_environment_not_allowed"}},
            )
        manifest = db.query(CodeProjectManifest).filter(
            CodeProjectManifest.id == (body.manifest_id or ""),
            CodeProjectManifest.project_id == project.id,
        ).first()
        if not manifest:
            return fail("manifest_not_found")
        if manifest.status != "draft":
            return fail("manifest_published_immutable")
        errors = _manifest_validation_errors(manifest)
        if errors:
            return fail("manifest_invalid", data={"field_errors": errors})
        try:
            published = build_manifest_publish_service(db).publish(
                actor=user,
                project=project,
                manifest=manifest,
            )
        except ManifestPublishError as exc:
            return fail(exc.reason, data={"field_errors": exc.field_errors})
        return ok(_manifest_dict(published), "发布成功")
    return fail("code_project_action_unknown")
