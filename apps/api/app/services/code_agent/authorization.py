"""Service-boundary authorization for privileged CodeAgent operations."""

from __future__ import annotations

import json
from collections.abc import Iterable

from app.services.code_agent.failures import FailureReason, record_code_failure


ADMIN_ROLES = frozenset({"master", "admin"})
PROJECT_ROLES = frozenset({"owner", "operator", "reviewer"})
ADMIN_RESOURCE_KINDS = frozenset({
    "source_allowlist",
    "internal_networks",
    "local_roots",
    "secret_metadata",
    "secret_assignment",
    "trusted_image",
})


class CodeAuthorizationError(PermissionError):
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


def _json_roles(value) -> set[str]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, json.JSONDecodeError):
            return set()
    if not isinstance(value, Iterable) or isinstance(value, (str, bytes, dict)):
        return set()
    return {str(item) for item in value}


def _organization_id(subject) -> str:
    return str(getattr(subject, "organization_id", "default") or "default")


def _allowed_users(project) -> set[str]:
    value = getattr(project, "allowed_users", "[]")
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, json.JSONDecodeError):
            return set()
    return {str(item) for item in value} if isinstance(value, list) else set()


def is_platform_admin(subject) -> bool:
    return bool(ADMIN_ROLES.intersection(_json_roles(getattr(subject, "roles", []))))


def _same_organization(subject, project) -> bool:
    return _organization_id(subject) == _organization_id(project)


def _project_member(subject, project) -> bool:
    if not subject or not project or not _same_organization(subject, project):
        return False
    username = str(getattr(subject, "username", ""))
    return username == getattr(project, "creator", "") or username in _allowed_users(project)


def _has_project_role(subject, project, role: str) -> bool:
    if not project:
        return False
    if is_platform_admin(subject):
        return True
    if not _project_member(subject, project):
        return False
    username = str(getattr(subject, "username", ""))
    if role == "owner" and username == getattr(project, "creator", ""):
        return True
    return role in _json_roles(getattr(subject, "roles", []))


def can_view_code_project(subject, project) -> bool:
    if is_platform_admin(subject):
        return bool(project)
    return _project_member(subject, project) and bool(
        PROJECT_ROLES.intersection(_json_roles(getattr(subject, "roles", [])))
        or getattr(project, "creator", "") == getattr(subject, "username", "")
    )


def _record_denial(db, subject, operation: str, project_id: str = "") -> None:
    if db is None:
        return
    try:
        record_code_failure(
            db,
            actor=str(getattr(subject, "username", "") or "anonymous"),
            reason=FailureReason.AUTHORIZATION_DENIED,
            project_id=project_id,
            details={"operation": operation},
        )
    except Exception:
        db.rollback()


def require_platform_admin(subject, *, db=None, resource_kind: str) -> None:
    if resource_kind not in ADMIN_RESOURCE_KINDS:
        raise ValueError("code_admin_resource_unknown")
    if is_platform_admin(subject):
        return
    _record_denial(db, subject, f"admin:{resource_kind}")
    raise CodeAuthorizationError("code_admin_unauthorized")


def require_project_creator(subject, *, db=None) -> None:
    if is_platform_admin(subject) or "owner" in _json_roles(getattr(subject, "roles", [])):
        return
    _record_denial(db, subject, "project:create")
    raise CodeAuthorizationError("code_project_not_found")


def require_project_owner(subject, project, *, db=None, operation: str = "project:manage"):
    if _has_project_role(subject, project, "owner"):
        return project
    _record_denial(db, subject, operation, str(getattr(project, "id", "") or ""))
    raise CodeAuthorizationError("code_project_not_found")


def require_project_operator(
    subject,
    project,
    *,
    db=None,
    resource=None,
    operation: str = "run:operate",
):
    resource_project_id = (
        getattr(resource, "project_id", None)
        if resource is not None
        else getattr(project, "id", None)
    )
    if (
        _has_project_role(subject, project, "operator")
        and resource_project_id == getattr(project, "id", None)
    ):
        return project
    _record_denial(db, subject, operation, str(getattr(project, "id", "") or ""))
    raise CodeAuthorizationError("code_run_unauthorized")


def require_project_reviewer(
    subject,
    project,
    *,
    db=None,
    resource=None,
    operation: str = "artifact:review",
):
    resource_project_id = (
        getattr(resource, "project_id", None)
        if resource is not None
        else getattr(project, "id", None)
    )
    if (
        _has_project_role(subject, project, "reviewer")
        and resource_project_id == getattr(project, "id", None)
    ):
        return project
    _record_denial(db, subject, operation, str(getattr(project, "id", "") or ""))
    raise CodeAuthorizationError("code_artifact_unauthorized")
