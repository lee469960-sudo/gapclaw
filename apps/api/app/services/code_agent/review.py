"""Read-only review and acceptance of immutable Code artifacts."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

from app.models import CodeArtifactReview
from app.security import new_id, now_str
from app.services.code_agent.authorization import require_project_reviewer
from app.services.code_agent.scanner import (
    _patch_secret_policy_action,
    _source_scan_complete_or_warned,
    _source_secret_policy_action,
    _source_unscannable_policy_action,
)


class ArtifactReviewError(ValueError):
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


_FILES = {
    "patch": "patch.diff",
    "policy": "policy.json",
    "verifier": "verifier-report.json",
    "source_scan": "source-scan-report.json",
    "patch_scan": "patch-scan-report.json",
    "manifest": "manifest.json",
}


@dataclass(frozen=True)
class ArtifactBundle:
    root: Path
    patch: bytes
    policy: bytes
    verifier: bytes
    source_scan: bytes
    patch_scan: bytes
    manifest: bytes


def _hash(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def load_verified_bundle(artifact) -> ArtifactBundle:
    if not artifact or artifact.status != "sealed":
        raise ArtifactReviewError("code_artifact_not_sealed")
    root = Path(artifact.storage_path).resolve()
    if not root.is_dir():
        raise ArtifactReviewError("code_artifact_missing")
    content: dict[str, bytes] = {}
    for kind, filename in _FILES.items():
        path = root / filename
        if path.is_symlink() or not path.is_file() or path.parent != root:
            raise ArtifactReviewError("code_artifact_incomplete")
        content[kind] = path.read_bytes()
    try:
        manifest = json.loads(content["manifest"])
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ArtifactReviewError("code_artifact_manifest_invalid") from exc
    expected = {
        "artifact_id": artifact.id,
        "run_id": artifact.run_id,
        "project_id": artifact.project_id,
        "base_commit": artifact.base_commit,
        "diff_hash": artifact.diff_hash,
        "policy_hash": artifact.policy_hash,
        "verifier_report_hash": artifact.verifier_report_hash,
    }
    if any(manifest.get(key) != value for key, value in expected.items()):
        raise ArtifactReviewError("code_artifact_manifest_mismatch")
    required = (
        "resolved_commit", "snapshot_id", "snapshot_hash", "image_digest",
        "source_scan_report_id", "source_scan_report_hash",
        "patch_scan_report_id", "patch_scan_report_hash",
    )
    if manifest.get("version") != 2 or any(not manifest.get(key) for key in required):
        raise ArtifactReviewError("code_artifact_manifest_incomplete")
    if (
        _hash(content["patch"]) != artifact.diff_hash
        or _hash(content["policy"]) != artifact.policy_hash
        or _hash(content["verifier"]) != artifact.verifier_report_hash
        or _hash(content["source_scan"]) != manifest["source_scan_report_hash"]
        or _hash(content["patch_scan"]) != manifest["patch_scan_report_hash"]
    ):
        raise ArtifactReviewError("code_artifact_hash_mismatch")
    try:
        verifier = json.loads(content["verifier"])
        source_scan = json.loads(content["source_scan"])
        patch_scan = json.loads(content["patch_scan"])
        policy_text = content["policy"].decode("utf-8")
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ArtifactReviewError("code_artifact_evidence_invalid") from exc
    source_secret_action = _source_secret_policy_action(policy_text)
    source_unscannable_action = _source_unscannable_policy_action(policy_text)
    patch_secret_action = _patch_secret_policy_action(policy_text)
    source_findings = int(source_scan.get("findings_count") or 0)
    patch_findings = int(patch_scan.get("findings_count") or 0)
    source_complete_or_warned = _source_scan_complete_or_warned(
        SimpleNamespace(
            status=str(source_scan.get("status") or ""),
            complete=bool(source_scan.get("complete")),
            failure_reason=str(source_scan.get("failure_reason") or ""),
        ),
        source_unscannable_action=source_unscannable_action,
    )
    if (
        verifier.get("patch_scan_report_id") != manifest["patch_scan_report_id"]
        or source_scan.get("id") != manifest["source_scan_report_id"]
        or patch_scan.get("id") != manifest["patch_scan_report_id"]
        or source_scan.get("scope") != "source"
        or patch_scan.get("scope") != "patch"
        or not source_complete_or_warned
        or patch_scan.get("complete") is not True
        or source_findings < 0
        or (source_findings and source_secret_action != "warn")
        or patch_findings < 0
        or (patch_findings and patch_secret_action != "warn")
    ):
        raise ArtifactReviewError("code_artifact_evidence_mismatch")
    return ArtifactBundle(
        root=root,
        patch=content["patch"],
        policy=content["policy"],
        verifier=content["verifier"],
        source_scan=content["source_scan"],
        patch_scan=content["patch_scan"],
        manifest=content["manifest"],
    )


def load_reviewable_bundle(db, *, user, artifact, run, project) -> ArtifactBundle:
    """Authorize the complete project/resource tuple before reading artifact bytes."""
    require_project_reviewer(
        user,
        project,
        db=db,
        resource=artifact,
        operation="artifact:read",
    )
    if not run or getattr(run, "project_id", None) != getattr(project, "id", None):
        require_project_reviewer(
            user,
            None,
            db=db,
            resource=artifact,
            operation="artifact:read",
        )
    if run.status != "patch_ready" or run.artifact_id != artifact.id:
        raise ArtifactReviewError("code_artifact_not_reviewable")
    return load_verified_bundle(artifact)


def review_payload(artifact, bundle: ArtifactBundle) -> dict:
    report = json.loads(bundle.verifier)
    patch = bundle.patch.decode("utf-8", errors="replace")
    return {
        "artifact_id": artifact.id,
        "patch": patch[:1_000_000],
        "patch_truncated": len(patch) > 1_000_000,
        "verifier_report": report,
        "hashes": {
            "base_commit": artifact.base_commit,
            "diff_hash": artifact.diff_hash,
            "policy_hash": artifact.policy_hash,
            "verifier_report_hash": artifact.verifier_report_hash,
            "source_scan_report_hash": json.loads(bundle.manifest)["source_scan_report_hash"],
            "patch_scan_report_hash": json.loads(bundle.manifest)["patch_scan_report_hash"],
            "manifest_hash": _hash(bundle.manifest),
        },
        "image": artifact.image,
        "image_id": artifact.image_id,
    }


def artifact_file(bundle: ArtifactBundle, kind: str) -> tuple[Path, str]:
    if kind not in _FILES:
        raise ArtifactReviewError("code_artifact_file_not_allowed")
    return bundle.root / _FILES[kind], _FILES[kind]


def accept_sealed_artifact(
    db, *, user, artifact, run, project, latest_manifest
) -> CodeArtifactReview:
    require_project_reviewer(
        user,
        project,
        db=db,
        resource=artifact,
        operation="artifact:accept",
    )
    if not run or getattr(run, "project_id", None) != getattr(project, "id", None):
        require_project_reviewer(
            user,
            None,
            db=db,
            resource=artifact,
            operation="artifact:accept",
        )
    bundle = load_verified_bundle(artifact)
    if run.status != "patch_ready" or run.artifact_id != artifact.id:
        raise ArtifactReviewError("code_artifact_not_adoptable")
    if not latest_manifest or latest_manifest.base_commit != artifact.base_commit:
        run.status = "stale"
        run.failure_reason = "base_commit_stale"
        db.commit()
        raise ArtifactReviewError("code_artifact_stale")
    existing = db.query(CodeArtifactReview).filter(
        CodeArtifactReview.artifact_id == artifact.id
    ).first()
    if existing:
        return existing
    review = CodeArtifactReview(
        id=new_id(),
        artifact_id=artifact.id,
        run_id=run.id,
        project_id=run.project_id,
        action="accepted",
        reviewer=user.username,
        manifest_hash=_hash(bundle.manifest),
        created_at=now_str(),
    )
    db.add(review)
    db.commit()
    return review
