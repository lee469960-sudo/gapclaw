"""Manifest publication for Git configuration only."""

from __future__ import annotations

import json

from app.config import get_settings
from app.models import (
    CodeControlAudit,
    CodeProject,
    CodeProjectManifest,
    CodeRepositorySource,
)
from app.security import new_id, now_str
from app.services.code_agent.authorization import (
    CodeAuthorizationError,
    require_project_owner,
)
from app.services.code_agent.control_plane import (
    ManifestUnavailableError,
    PolicyRejectedError,
    validate_manifest_for_admission,
)
from app.services.code_agent.failures import record_code_failure
from app.services.code_agent.local_source import (
    LocalRepositoryPolicyError,
    normalize_local_repository_locator,
)
from app.services.code_agent.secret_store import (
    DeployTokenSecretStore,
    SecretReferenceError,
)
from app.services.code_agent.source_policy import (
    RepositorySourcePolicyError,
    normalize_remote_source,
)


_REASON_FIELDS = {
    "repository_auth_failed": "credential_ref",
    "repository_ref_invalid": "requested_ref",
    "project_environment_not_allowed": "environment_tier",
}


class ManifestPublishError(RuntimeError):
    def __init__(self, reason: str, *, field: str = "source"):
        self.reason = reason
        self.field_errors = {field: reason}
        super().__init__(reason)


class ManifestPublishService:
    def __init__(
        self,
        db,
        *,
        settings,
    ):
        self.db = db
        self.settings = settings

    def _validate_policy(
        self,
        *,
        actor,
        project: CodeProject,
        manifest: CodeProjectManifest,
    ) -> CodeRepositorySource:
        require_project_owner(actor, project, db=self.db, operation="manifest:publish")
        if manifest.status != "draft":
            raise ManifestPublishError("manifest_published_immutable", field="manifest")
        if project.environment_tier != "internal_non_production":
            raise ManifestPublishError(
                "project_environment_not_allowed",
                field="environment_tier",
            )
        source = self.db.get(CodeRepositorySource, manifest.source_id or "")
        if (
            source is None
            or source.project_id != project.id
            or source.status != "draft"
            or source.source_type != manifest.source_type
            or source.locator != manifest.repository
            or source.credential_ref != manifest.credential_ref
            or source.requested_ref != manifest.requested_ref
        ):
            raise ManifestPublishError("repository_source_not_allowed")
        try:
            if source.source_type == "local":
                normalized_locator = str(normalize_local_repository_locator(
                    source.locator,
                    allowed_roots=self.settings.code_local_repository_roots,
                ))
            else:
                normalized_locator = normalize_remote_source(
                    source.locator,
                    allowlist=self.settings.code_repository_allowlist,
                ).locator
        except (LocalRepositoryPolicyError, RepositorySourcePolicyError) as exc:
            raise ManifestPublishError("repository_source_not_allowed") from exc
        if normalized_locator != source.locator:
            raise ManifestPublishError("repository_source_not_allowed")
        if source.credential_ref:
            visible = {
                item.reference_id
                for item in DeployTokenSecretStore(self.db).project_metadata(
                    actor=actor,
                    project=project,
                )
            }
            if source.credential_ref not in visible:
                raise ManifestPublishError(
                    "repository_auth_failed",
                    field="credential_ref",
                )
        return source

    def _record_failure(
        self,
        *,
        actor,
        project: CodeProject,
        reason: str,
        manifest: CodeProjectManifest,
    ) -> None:
        self.db.rollback()
        try:
            record_code_failure(
                self.db,
                actor=str(getattr(actor, "username", "") or "anonymous"),
                reason=reason,
                project_id=project.id,
                details={"manifest_id": manifest.id},
            )
        except Exception:
            self.db.rollback()

    def publish(
        self,
        *,
        actor,
        project: CodeProject,
        manifest: CodeProjectManifest,
    ) -> CodeProjectManifest:
        try:
            source = self._validate_policy(
                actor=actor,
                project=project,
                manifest=manifest,
            )
            expected = (
                manifest.source_id,
                manifest.source_type,
                manifest.repository,
                manifest.credential_ref,
                manifest.requested_ref,
            )
            self.db.refresh(source)
            self.db.refresh(manifest)
            current = (
                manifest.source_id,
                manifest.source_type,
                manifest.repository,
                manifest.credential_ref,
                manifest.requested_ref,
            )
            source_current = (
                source.id,
                source.source_type,
                source.locator,
                source.credential_ref,
                source.requested_ref,
            )
            if (
                manifest.status != "draft"
                or source.status != "draft"
                or current != expected
                or source_current != expected
            ):
                raise ManifestPublishError("manifest_publish_conflict")
            manifest.source_scan_report_id = ""
            manifest.security_schema_version = 1
            manifest.base_commit = ""
            manifest.resolved_commit = ""
            manifest.snapshot_id = ""
            manifest.snapshot_hash = ""
            manifest.trusted_image = manifest.trusted_image or ""
            project_policy = json.loads(project.policy or "{}")
            if not isinstance(project_policy, dict):
                raise ManifestUnavailableError("manifest_invalid_project_policy")
            validate_manifest_for_admission(manifest, project_policy=project_policy)
            source.status = "active"
            source.updated_at = now_str()
            manifest.status = "published"
            manifest.published_at = now_str()
            self.db.add(CodeControlAudit(
                id=new_id(),
                actor=str(getattr(actor, "username", "")),
                action="manifest_publish",
                project_id=project.id,
                manifest_id=manifest.id,
                details=json.dumps({
                    "version": manifest.version,
                    "source_id": source.id,
                    "repository": manifest.repository,
                    "requested_ref": manifest.requested_ref,
                    "publish_mode": "git_config_only",
                }, sort_keys=True),
                created_at=now_str(),
            ))
            self.db.commit()
            return manifest
        except ManifestPublishError as exc:
            self._record_failure(
                actor=actor,
                project=project,
                reason=exc.reason,
                manifest=manifest,
            )
            raise
        except CodeAuthorizationError as exc:
            self.db.rollback()
            raise ManifestPublishError(exc.reason, field="authorization") from exc
        except (
            LocalRepositoryPolicyError,
            RepositorySourcePolicyError,
            SecretReferenceError,
            ManifestUnavailableError,
            PolicyRejectedError,
            json.JSONDecodeError,
        ) as exc:
            reason = getattr(exc, "reason", "infrastructure_error")
            if isinstance(exc, SecretReferenceError):
                reason = "repository_auth_failed"
            self._record_failure(
                actor=actor,
                project=project,
                reason=reason,
                manifest=manifest,
            )
            raise ManifestPublishError(
                reason,
                field=_REASON_FIELDS.get(reason, "source"),
            ) from exc
        except Exception as exc:
            self._record_failure(
                actor=actor,
                project=project,
                reason="infrastructure_error",
                manifest=manifest,
            )
            raise ManifestPublishError("manifest_publish_failed") from exc


def build_manifest_publish_service(db) -> ManifestPublishService:
    settings = get_settings()
    return ManifestPublishService(db, settings=settings)
