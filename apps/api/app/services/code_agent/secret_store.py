"""Reference-only, read-only Deploy Token storage for Repository Importer."""

from __future__ import annotations

import json
import logging
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from typing import Iterator

from app.models import CodeControlAudit, CodeDeployCredential, CodeProject
from app.security import (
    decrypt_secret_strict,
    encrypt_secret,
    new_id,
    now_str,
)
from app.services.code_agent.authorization import (
    require_platform_admin,
    require_project_owner,
)
from app.services.code_agent.failures import FailureReason, record_code_failure


logger = logging.getLogger(__name__)


class SecretReferenceError(ValueError):
    def __init__(self, reason: str = "repository_auth_failed"):
        self.reason = reason
        super().__init__(reason)


@dataclass(frozen=True)
class DeployCredentialMetadata:
    reference_id: str
    organization_id: str
    label: str
    credential_kind: str
    auth_username: str
    allowed_project_ids: tuple[str, ...]
    read_only: bool
    status: str

    def to_dict(self) -> dict:
        value = asdict(self)
        value["allowed_project_ids"] = list(self.allowed_project_ids)
        return value


class _RepositoryImporterIdentity:
    __slots__ = ()

    def __repr__(self) -> str:
        return "<RepositoryImporterIdentity>"


_REPOSITORY_IMPORTER_IDENTITY = _RepositoryImporterIdentity()


def repository_importer_identity():
    """Return the single in-process identity accepted by the Secret adapter."""
    return _REPOSITORY_IMPORTER_IDENTITY


class DeployTokenLease:
    """Short-lived mutable secret bytes that are overwritten when the lease closes."""

    __slots__ = ("_material", "_closed")

    def __init__(self, value: str):
        self._material = bytearray(value.encode("utf-8"))
        self._closed = False

    @property
    def closed(self) -> bool:
        return self._closed

    @property
    def cleared(self) -> bool:
        return self._closed and all(value == 0 for value in self._material)

    def read(self) -> bytes:
        if self._closed:
            raise SecretReferenceError()
        return bytes(self._material)

    def close(self) -> None:
        if self._closed:
            return
        for index in range(len(self._material)):
            self._material[index] = 0
        self._closed = True

    def __enter__(self):
        return self

    def __exit__(self, _exc_type, _exc, _traceback):
        self.close()

    def __repr__(self) -> str:
        return f"<DeployTokenLease closed={self._closed}>"


def _project_ids(raw: str) -> tuple[str, ...]:
    try:
        value = json.loads(raw or "[]")
    except (TypeError, json.JSONDecodeError):
        return ()
    if not isinstance(value, list):
        return ()
    return tuple(str(item) for item in value)


def _metadata(row: CodeDeployCredential) -> DeployCredentialMetadata:
    return DeployCredentialMetadata(
        reference_id=row.id,
        organization_id=row.organization_id,
        label=row.label,
        credential_kind=row.credential_kind,
        auth_username=row.auth_username,
        allowed_project_ids=_project_ids(row.allowed_project_ids),
        read_only=bool(row.read_only),
        status=row.status,
    )


def _audit(db, *, actor: str, action: str, project_id: str = "", details=None) -> None:
    db.add(CodeControlAudit(
        id=new_id(),
        actor=actor,
        action=action,
        project_id=project_id,
        details=json.dumps(details or {}, sort_keys=True),
        created_at=now_str(),
    ))


class DeployTokenSecretStore:
    def __init__(self, db):
        self.db = db

    def _resolution_failure(self, *, reference_id: str, project_id: str) -> None:
        try:
            record_code_failure(
                self.db,
                actor="service:repository_importer",
                reason=FailureReason.REPOSITORY_AUTH_FAILED,
                project_id=project_id,
                details={"reference_id": reference_id},
            )
        except Exception:
            self.db.rollback()

    def assign(
        self,
        *,
        actor,
        organization_id: str,
        label: str,
        value: str,
        allowed_project_ids: list[str],
        auth_username: str = "",
        reference_id: str = "",
    ) -> DeployCredentialMetadata:
        require_platform_admin(actor, db=self.db, resource_kind="secret_assignment")
        organization = organization_id.strip()
        normalized_label = label.strip()
        projects = sorted({item.strip() for item in allowed_project_ids if item.strip()})
        if not organization or not normalized_label or not value or not projects:
            raise SecretReferenceError()
        approved_projects = self.db.query(CodeProject.id).filter(
            CodeProject.id.in_(projects),
            CodeProject.organization_id == organization,
        ).all()
        if {row[0] for row in approved_projects} != set(projects):
            raise SecretReferenceError()
        row = (
            self.db.query(CodeDeployCredential)
            .filter(CodeDeployCredential.id == reference_id)
            .first()
            if reference_id
            else None
        )
        timestamp = now_str()
        if row is None:
            row = CodeDeployCredential(
                id=reference_id or new_id(),
                organization_id=organization,
                label=normalized_label,
                credential_kind="deploy_token",
                auth_username=auth_username.strip(),
                secret_enc=encrypt_secret(value),
                allowed_project_ids=json.dumps(projects),
                read_only=True,
                status="active",
                created_by=str(getattr(actor, "username", "")),
                created_at=timestamp,
                updated_at=timestamp,
            )
            self.db.add(row)
        else:
            row.organization_id = organization
            row.label = normalized_label
            row.auth_username = auth_username.strip()
            row.secret_enc = encrypt_secret(value)
            row.allowed_project_ids = json.dumps(projects)
            row.read_only = True
            row.status = "active"
            row.updated_at = timestamp
        _audit(
            self.db,
            actor=str(getattr(actor, "username", "")),
            action="credential_assign",
            details={
                "reference_id": row.id,
                "organization_id": organization,
                "project_count": len(projects),
            },
        )
        self.db.commit()
        return _metadata(row)

    def disable(self, *, actor, reference_id: str) -> DeployCredentialMetadata:
        require_platform_admin(actor, db=self.db, resource_kind="secret_assignment")
        row = self.db.query(CodeDeployCredential).filter(
            CodeDeployCredential.id == reference_id
        ).first()
        if not row:
            raise SecretReferenceError()
        row.status = "disabled"
        row.updated_at = now_str()
        _audit(
            self.db,
            actor=str(getattr(actor, "username", "")),
            action="credential_disable",
            details={"reference_id": row.id},
        )
        self.db.commit()
        return _metadata(row)

    def admin_metadata(self, *, actor) -> list[DeployCredentialMetadata]:
        require_platform_admin(actor, db=self.db, resource_kind="secret_metadata")
        return [_metadata(row) for row in self.db.query(CodeDeployCredential).all()]

    def project_metadata(self, *, actor, project) -> list[DeployCredentialMetadata]:
        require_project_owner(
            actor,
            project,
            db=self.db,
            operation="credential:list",
        )
        rows = self.db.query(CodeDeployCredential).filter(
            CodeDeployCredential.organization_id == project.organization_id,
            CodeDeployCredential.status == "active",
        ).all()
        return [
            _metadata(row)
            for row in rows
            if project.id in _project_ids(row.allowed_project_ids)
        ]

    @contextmanager
    def resolve_for_importer(
        self,
        *,
        identity,
        reference_id: str,
        organization_id: str,
        project_id: str,
    ) -> Iterator[DeployTokenLease]:
        if identity is not _REPOSITORY_IMPORTER_IDENTITY:
            logger.warning("Deploy credential resolution denied: invalid service identity")
            self._resolution_failure(
                reference_id=reference_id,
                project_id=project_id,
            )
            raise SecretReferenceError()
        row = self.db.query(CodeDeployCredential).filter(
            CodeDeployCredential.id == reference_id,
            CodeDeployCredential.organization_id == organization_id,
            CodeDeployCredential.status == "active",
        ).first()
        project_exists = self.db.query(CodeProject.id).filter(
            CodeProject.id == project_id,
            CodeProject.organization_id == organization_id,
        ).first()
        if (
            not row
            or not project_exists
            or not row.read_only
            or project_id not in _project_ids(row.allowed_project_ids)
        ):
            logger.warning("Deploy credential resolution denied: reference unavailable")
            self._resolution_failure(
                reference_id=reference_id,
                project_id=project_id,
            )
            raise SecretReferenceError()
        try:
            value = decrypt_secret_strict(row.secret_enc)
        except Exception as exc:
            logger.warning("Deploy credential resolution denied: decrypt failed")
            self._resolution_failure(
                reference_id=reference_id,
                project_id=project_id,
            )
            raise SecretReferenceError() from exc
        lease = DeployTokenLease(value)
        value = ""
        try:
            _audit(
                self.db,
                actor="service:repository_importer",
                action="credential_resolve",
                project_id=project_id,
                details={"reference_id": row.id},
            )
            self.db.commit()
            yield lease
        finally:
            lease.close()
