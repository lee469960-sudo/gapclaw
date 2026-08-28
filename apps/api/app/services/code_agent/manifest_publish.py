"""Fail-closed Manifest publication over imported, scanned and sealed source."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol
from urllib.parse import urlsplit

from app.config import get_settings
from app.models import (
    CodeControlAudit,
    CodeProject,
    CodeProjectManifest,
    CodeRepositorySource,
    CodeScanReport,
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
from app.services.code_agent.git_importer import (
    GitImportLimits,
    RestrictedGitImportError,
    RestrictedGitImporter,
)
from app.services.code_agent.local_source import (
    LocalRepositoryImporter,
    LocalRepositoryPolicyError,
    normalize_local_repository_locator,
)
from app.services.code_agent.network_policy import (
    RepositoryNetworkGuard,
    RepositoryNetworkPolicyError,
)
from app.services.code_agent.scanner import BuiltinSecretScanner, persist_scan_report
from app.services.code_agent.secret_store import (
    DeployTokenSecretStore,
    SecretReferenceError,
    repository_importer_identity,
)
from app.services.code_agent.snapshot_lifecycle import (
    SnapshotLifecycleError,
    SnapshotLifecycleService,
)
from app.services.code_agent.snapshot_store import (
    SealedSourceSnapshot,
    SnapshotSealError,
    SourceSnapshotStore,
)
from app.services.code_agent.source_policy import (
    RepositorySourcePolicyError,
    normalize_remote_source,
)


IMPORTER_VERSION = "restricted-git-v1"
_REASON_FIELDS = {
    "repository_auth_failed": "credential_ref",
    "repository_ref_invalid": "requested_ref",
    "image_digest_invalid": "image_digest",
    "project_environment_not_allowed": "environment_tier",
}
_SOURCE_UNSCANNABLE_WARN_REASONS = {
    "scanner_binary_unsupported",
    "scanner_unsupported_format",
}


def _source_secret_policy_action(raw_policy: str) -> str:
    try:
        policy = json.loads(raw_policy or "{}")
    except (TypeError, json.JSONDecodeError):
        return "block"
    if not isinstance(policy, dict):
        return "block"
    secret_policy = policy.get("secret_policy")
    if not isinstance(secret_policy, dict):
        return "block"
    return "warn" if secret_policy.get("source") == "warn" else "block"


def _source_unscannable_policy_action(raw_policy: str) -> str:
    try:
        policy = json.loads(raw_policy or "{}")
    except (TypeError, json.JSONDecodeError):
        return "block"
    if not isinstance(policy, dict):
        return "block"
    secret_policy = policy.get("secret_policy")
    if not isinstance(secret_policy, dict):
        return "block"
    return "warn" if secret_policy.get("source_unscannable") == "warn" else "block"


def _source_scan_complete_or_warned(report, *, source_unscannable_action: str) -> bool:
    if (
        report.status == "complete"
        and report.complete
        and str(report.failure_reason or "") in {"", "secret_detected"}
    ):
        return True
    return bool(
        source_unscannable_action == "warn"
        and report.status == "incomplete"
        and not report.complete
        and str(report.failure_reason or "") in _SOURCE_UNSCANNABLE_WARN_REASONS
    )


def _trusted_image_from_digest_selection(image_digest: str, current: str = "") -> str:
    selected = (image_digest or "").strip()
    if "@" in selected:
        image, _digest = selected.split("@", 1)
        if image:
            return image
    return (current or "").strip() or selected


class ManifestPublishError(RuntimeError):
    def __init__(self, reason: str, *, field: str = "source"):
        self.reason = reason
        self.field_errors = {field: reason}
        super().__init__(reason)


_PROXY_CONTROL = re.compile(r"[\x00-\x20\x7f]")


def _normalized_repository_proxy_url(value: str) -> str:
    raw = str(value or "")
    if not raw:
        return ""
    if raw != raw.strip() or _PROXY_CONTROL.search(raw):
        return ""
    try:
        parsed = urlsplit(raw)
    except ValueError:
        return ""
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        return ""
    if parsed.query or parsed.fragment:
        return ""
    return raw


def repository_proxy_environment(settings) -> dict[str, str]:
    """Return the minimal proxy environment allowed for repository imports."""
    http_proxy = _normalized_repository_proxy_url(
        getattr(settings, "code_repository_http_proxy", "")
    )
    https_proxy = _normalized_repository_proxy_url(
        getattr(settings, "code_repository_https_proxy", "")
    )
    environment: dict[str, str] = {}
    if http_proxy:
        environment["http_proxy"] = http_proxy
        environment["HTTP_PROXY"] = http_proxy
    if https_proxy:
        environment["https_proxy"] = https_proxy
        environment["HTTPS_PROXY"] = https_proxy
    return environment


@dataclass(frozen=True)
class ImportedRepository:
    repository_path: str
    resolved_commit: str
    size_bytes: int
    file_count: int
    cleanup_paths: tuple[str, ...]


class RepositoryAcquirer(Protocol):
    def acquire(
        self,
        *,
        actor,
        project: CodeProject,
        source: CodeRepositorySource,
    ) -> ImportedRepository: ...


class SourceScanner(Protocol):
    def scan(self, repository_path: Path) -> CodeScanReport: ...


def _string_list(raw: str) -> list[str]:
    try:
        value = json.loads(raw or "[]")
    except (TypeError, json.JSONDecodeError) as exc:
        raise ManifestPublishError("code_agent_security_config_invalid") from exc
    if not isinstance(value, list) or not all(
        isinstance(item, str) and item.strip() for item in value
    ):
        raise ManifestPublishError("code_agent_security_config_invalid")
    return [item.strip() for item in value]


def _secure_directory(path: Path) -> Path:
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    resolved = path.resolve(strict=True)
    if path.is_symlink() or not resolved.is_dir():
        raise ManifestPublishError("snapshot_invalid")
    resolved.chmod(0o700)
    return resolved


class DefaultRepositoryAcquirer:
    def __init__(self, db, settings, *, git_root: Path, local_root: Path):
        self.db = db
        self.settings = settings
        self.git_importer = RestrictedGitImporter(
            staging_root=git_root,
            limits=GitImportLimits.from_settings(settings),
        )
        self.local_root = local_root

    def acquire(
        self,
        *,
        actor,
        project: CodeProject,
        source: CodeRepositorySource,
    ) -> ImportedRepository:
        import_id = new_id()
        cleanup_paths: list[str] = []
        try:
            if source.source_type == "local":
                copied = LocalRepositoryImporter(
                    allowed_roots=self.settings.code_local_repository_roots,
                    staging_root=self.local_root,
                ).import_to_staging(source.locator, import_id=f"{import_id}l")
                cleanup_paths.append(copied.staging_path)
                imported = self.git_importer.import_from_staging(
                    Path(copied.staging_path),
                    requested_ref=source.requested_ref,
                    import_id=f"{import_id}g",
                )
            else:
                proxy_environment = repository_proxy_environment(self.settings)
                use_transport_proxy = bool(
                    getattr(self.settings, "code_repository_use_transport_proxy", False)
                    and proxy_environment
                )
                guard = RepositoryNetworkGuard(
                    allowlist=self.settings.code_repository_allowlist,
                    approved_internal_cidrs=self.settings.code_repository_internal_cidrs,
                    use_transport_proxy=use_transport_proxy,
                    allow_public_http=bool(
                        getattr(self.settings, "code_repository_allow_public_http", False)
                    ),
                )
                known_hosts_value = str(
                    getattr(self.settings, "code_repository_ssh_known_hosts_file", "") or ""
                )
                known_hosts = Path(known_hosts_value) if known_hosts_value else None
                if source.credential_ref:
                    metadata = next(
                        (
                            item
                            for item in DeployTokenSecretStore(self.db).project_metadata(
                                actor=actor,
                                project=project,
                            )
                            if item.reference_id == source.credential_ref
                        ),
                        None,
                    )
                    if metadata is None:
                        raise SecretReferenceError()
                    with DeployTokenSecretStore(self.db).resolve_for_importer(
                        identity=repository_importer_identity(),
                        reference_id=source.credential_ref,
                        organization_id=project.organization_id,
                        project_id=project.id,
                    ) as lease:
                        imported = self.git_importer.import_remote(
                            guard,
                            source.locator,
                            requested_ref=source.requested_ref,
                            import_id=f"{import_id}g",
                            ssh_known_hosts_file=known_hosts,
                            credential_lease=lease,
                            auth_username=metadata.auth_username,
                            proxy_environment=proxy_environment,
                        )
                else:
                    imported = self.git_importer.import_remote(
                        guard,
                        source.locator,
                        requested_ref=source.requested_ref,
                        import_id=f"{import_id}g",
                        ssh_known_hosts_file=known_hosts,
                        proxy_environment=proxy_environment,
                    )
            cleanup_paths.append(imported.repository_path)
            return ImportedRepository(
                repository_path=imported.repository_path,
                resolved_commit=imported.resolved_commit,
                size_bytes=imported.unpacked_bytes,
                file_count=imported.file_count,
                cleanup_paths=tuple(cleanup_paths),
            )
        except Exception:
            for cleanup_path in reversed(cleanup_paths):
                shutil.rmtree(cleanup_path, ignore_errors=True)
            raise


class DefaultSourceScanner:
    """Persist the immutable scanner report without expanding its findings."""

    def __init__(self, db, adapter=None):
        self.db = db
        self.adapter = adapter or BuiltinSecretScanner(scope="source")

    def scan(self, repository_path: Path) -> CodeScanReport:
        result = self.adapter.scan(repository_path)
        return persist_scan_report(self.db, result)


class ManifestPublishService:
    def __init__(
        self,
        db,
        *,
        settings,
        acquirer: RepositoryAcquirer,
        scanner: SourceScanner,
        snapshot_store: SourceSnapshotStore,
    ):
        self.db = db
        self.settings = settings
        self.acquirer = acquirer
        self.scanner = scanner
        self.snapshot_store = snapshot_store
        self.lifecycle = SnapshotLifecycleService(db, snapshot_store)

    def _policy_hash(self, source: CodeRepositorySource) -> str:
        known_hosts_hash = ""
        if source.source_type == "ssh":
            known_hosts_value = str(
                getattr(self.settings, "code_repository_ssh_known_hosts_file", "") or ""
            )
            try:
                known_hosts_path = Path(known_hosts_value)
                resolved_known_hosts = known_hosts_path.resolve(strict=True)
            except OSError as exc:
                raise ManifestPublishError("repository_network_policy_denied") from exc
            if (
                not known_hosts_path.is_absolute()
                or known_hosts_path.is_symlink()
                or not resolved_known_hosts.is_file()
            ):
                raise ManifestPublishError("repository_network_policy_denied")
            try:
                known_hosts_bytes = resolved_known_hosts.read_bytes()
            except OSError as exc:
                raise ManifestPublishError("repository_network_policy_denied") from exc
            if len(known_hosts_bytes) > 1024 * 1024:
                raise ManifestPublishError("repository_network_policy_denied")
            known_hosts_hash = hashlib.sha256(known_hosts_bytes).hexdigest()
        value = json.dumps({
            "source_type": source.source_type,
            "ssh_known_hosts_hash": known_hosts_hash,
            "repository_allowlist": _string_list(
                self.settings.code_repository_allowlist
            ),
            "repository_internal_cidrs": _string_list(
                self.settings.code_repository_internal_cidrs
            ),
            "local_repository_roots": _string_list(
                self.settings.code_local_repository_roots
            ),
            "trusted_image_digests": _string_list(
                self.settings.code_trusted_image_digests
            ),
            "limits": {
                "download": self.settings.code_import_max_download_bytes,
                "unpacked": self.settings.code_import_max_unpacked_bytes,
                "files": self.settings.code_import_max_files,
                "file": self.settings.code_import_max_file_bytes,
                "timeout": self.settings.code_import_timeout_seconds,
            },
        }, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(value.encode()).hexdigest()

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
        try:
            allowed_paths = json.loads(manifest.allowed_paths or "[]")
            validation_plan = json.loads(manifest.validation_plan or "[]")
            allowed_tools = json.loads(manifest.allowed_tools or "[]")
            budgets = json.loads(manifest.budgets or "{}")
        except json.JSONDecodeError as exc:
            raise ManifestPublishError("manifest_invalid", field="manifest") from exc
        if (
            not isinstance(allowed_paths, list)
            or not allowed_paths
            or not isinstance(validation_plan, list)
            or not validation_plan
            or not isinstance(allowed_tools, list)
            or not allowed_tools
            or not isinstance(budgets, dict)
            or not budgets
            or not all(
                isinstance(value, int) and not isinstance(value, bool) and value > 0
                for value in budgets.values()
            )
        ):
            raise ManifestPublishError("manifest_invalid", field="manifest")
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
        if manifest.image_digest not in set(
            _string_list(self.settings.code_trusted_image_digests)
        ):
            raise ManifestPublishError("image_digest_invalid", field="image_digest")
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

    @staticmethod
    def _cleanup_import(imported: ImportedRepository | None) -> None:
        if imported is None:
            return
        for path in reversed(imported.cleanup_paths):
            shutil.rmtree(path, ignore_errors=True)

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

    def _preserve_orphan(
        self,
        sealed: SealedSourceSnapshot,
        *,
        source_id: str,
        scan_report_id: str,
        policy_hash: str,
        imported: ImportedRepository,
    ) -> None:
        try:
            snapshot = self.lifecycle.register_sealed(
                sealed,
                source_id=source_id,
                scan_report_id=scan_report_id,
                importer_version=IMPORTER_VERSION,
                policy_hash=policy_hash,
                size_bytes=imported.size_bytes,
                file_count=imported.file_count,
            )
            self.lifecycle.mark_orphan(snapshot, cleanup_after=datetime.now())
            self.db.commit()
        except Exception:
            self.db.rollback()

    def publish(
        self,
        *,
        actor,
        project: CodeProject,
        manifest: CodeProjectManifest,
    ) -> CodeProjectManifest:
        imported: ImportedRepository | None = None
        sealed: SealedSourceSnapshot | None = None
        report: CodeScanReport | None = None
        policy_hash = ""
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
                manifest.image_digest,
            )
            policy_hash = self._policy_hash(source)
            imported = self.acquirer.acquire(actor=actor, project=project, source=source)
            report = self.scanner.scan(Path(imported.repository_path))
            source_secret_action = _source_secret_policy_action(manifest.policy)
            source_unscannable_action = _source_unscannable_policy_action(manifest.policy)
            if (
                report.scope != "source"
                or not _source_scan_complete_or_warned(
                    report,
                    source_unscannable_action=source_unscannable_action,
                )
                or not report.input_hash
            ):
                raise ManifestPublishError("source_scan_failed")
            if report.findings_count != 0 and source_secret_action != "warn":
                raise ManifestPublishError("secret_detected")
            sealed = self.snapshot_store.seal(
                Path(imported.repository_path),
                resolved_commit=imported.resolved_commit,
            )

            self.db.refresh(source)
            self.db.refresh(manifest)
            current = (
                manifest.source_id,
                manifest.source_type,
                manifest.repository,
                manifest.credential_ref,
                manifest.requested_ref,
                manifest.image_digest,
            )
            source_current = (
                source.id,
                source.source_type,
                source.locator,
                source.credential_ref,
                source.requested_ref,
                manifest.image_digest,
            )
            if (
                manifest.status != "draft"
                or source.status != "draft"
                or current != expected
                or source_current != expected
            ):
                raise ManifestPublishError("manifest_publish_conflict")
            snapshot = self.lifecycle.register_sealed(
                sealed,
                source_id=source.id,
                scan_report_id=report.id,
                importer_version=IMPORTER_VERSION,
                policy_hash=policy_hash,
                size_bytes=imported.size_bytes,
                file_count=imported.file_count,
            )
            manifest.source_scan_report_id = snapshot.scan_report_id
            manifest.security_schema_version = sealed.security_schema_version
            manifest.base_commit = sealed.resolved_commit
            manifest.trusted_image = _trusted_image_from_digest_selection(
                manifest.image_digest,
                manifest.trusted_image,
            )
            self.lifecycle.attach_manifest(manifest, snapshot)
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
                    "snapshot_id": snapshot.id,
                    "source_secret_policy": source_secret_action,
                    "source_secret_warning_count": (
                        report.findings_count if source_secret_action == "warn" else 0
                    ),
                    "source_unscannable_policy": source_unscannable_action,
                    "source_unscannable_warning_count": (
                        report.skipped_count
                        if source_unscannable_action == "warn"
                        else 0
                    ),
                }, sort_keys=True),
                created_at=now_str(),
            ))
            self.db.commit()
            return manifest
        except ManifestPublishError as exc:
            if sealed is not None and imported is not None and report is not None:
                self.db.rollback()
                self._preserve_orphan(
                    sealed,
                    source_id=manifest.source_id,
                    scan_report_id=report.id,
                    policy_hash=policy_hash,
                    imported=imported,
                )
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
            RepositoryNetworkPolicyError,
            RestrictedGitImportError,
            SecretReferenceError,
            SnapshotSealError,
            SnapshotLifecycleError,
            ManifestUnavailableError,
            PolicyRejectedError,
            json.JSONDecodeError,
        ) as exc:
            reason = getattr(exc, "reason", "infrastructure_error")
            if isinstance(exc, SecretReferenceError):
                reason = "repository_auth_failed"
            if sealed is not None and imported is not None and report is not None:
                self.db.rollback()
                self._preserve_orphan(
                    sealed,
                    source_id=manifest.source_id,
                    scan_report_id=report.id,
                    policy_hash=policy_hash,
                    imported=imported,
                )
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
            if sealed is not None and imported is not None and report is not None:
                self.db.rollback()
                self._preserve_orphan(
                    sealed,
                    source_id=manifest.source_id,
                    scan_report_id=report.id,
                    policy_hash=policy_hash,
                    imported=imported,
                )
            self._record_failure(
                actor=actor,
                project=project,
                reason="infrastructure_error",
                manifest=manifest,
            )
            raise ManifestPublishError("manifest_publish_failed") from exc
        finally:
            self._cleanup_import(imported)


def build_manifest_publish_service(db) -> ManifestPublishService:
    settings = get_settings()
    root = _secure_directory(Path(settings.data_dir) / "code-agent-source")
    git_root = _secure_directory(root / "imports")
    local_root = _secure_directory(root / "local-copies")
    snapshot_root = _secure_directory(root / "snapshots")
    store = SourceSnapshotStore(snapshot_root)
    return ManifestPublishService(
        db,
        settings=settings,
        acquirer=DefaultRepositoryAcquirer(
            db,
            settings,
            git_root=git_root,
            local_root=local_root,
        ),
        scanner=DefaultSourceScanner(db),
        snapshot_store=store,
    )
