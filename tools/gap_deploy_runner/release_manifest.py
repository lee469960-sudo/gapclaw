"""Strict, dependency-free release-manifest validation for GAP deployment."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import re
from typing import Any, Mapping


_TAG_RE = re.compile(r"^v(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)$")
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_IDENTIFIER_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")
_TARGET_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
_IMAGE_RE = re.compile(r"^(?P<repository>[a-z0-9][a-z0-9./_-]*)@sha256:(?P<digest>[0-9a-f]{64})$")

MANIFEST_SCHEMA_VERSION = 1
DEPLOYMENT_TARGETS = frozenset({"production", "staging"})
_FIELDS = frozenset({
    "schema_version",
    "release_id",
    "git_tag",
    "version",
    "commit_sha",
    "target_id",
    "api_image",
    "web_image",
    "created_at",
    "health_check_version",
})


class ReleaseManifestValidationError(ValueError):
    """A stable reason for rejecting an untrusted release manifest."""

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


def _require_string(payload: Mapping[str, Any], field: str) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or not value:
        raise ReleaseManifestValidationError(f"manifest_{field}_invalid")
    return value


def _validate_created_at(value: str) -> None:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ReleaseManifestValidationError("manifest_created_at_invalid") from exc
    if parsed.tzinfo is None:
        raise ReleaseManifestValidationError("manifest_created_at_invalid")


def _validate_image(value: str, expected_repository: str, field: str) -> None:
    matched = _IMAGE_RE.fullmatch(value)
    if matched is None:
        raise ReleaseManifestValidationError(f"manifest_{field}_not_digest")
    if matched.group("repository") != expected_repository:
        raise ReleaseManifestValidationError(f"manifest_{field}_repository_not_allowed")


@dataclass(frozen=True)
class ReleaseManifest:
    schema_version: int
    release_id: str
    git_tag: str
    version: str
    commit_sha: str
    target_id: str
    api_image: str
    web_image: str
    created_at: str
    health_check_version: str

    @classmethod
    def from_dict(
        cls,
        payload: Mapping[str, Any],
        *,
        allowed_images: Mapping[str, str],
    ) -> "ReleaseManifest":
        if not isinstance(payload, Mapping):
            raise ReleaseManifestValidationError("manifest_invalid")
        if set(payload) != _FIELDS:
            raise ReleaseManifestValidationError("manifest_fields_invalid")
        if type(payload["schema_version"]) is not int or payload["schema_version"] != MANIFEST_SCHEMA_VERSION:
            raise ReleaseManifestValidationError("manifest_schema_version_invalid")

        try:
            api_repository = allowed_images["api"]
            web_repository = allowed_images["web"]
        except (KeyError, TypeError) as exc:
            raise ReleaseManifestValidationError("manifest_allowed_images_invalid") from exc
        if not all(isinstance(value, str) and value for value in (api_repository, web_repository)):
            raise ReleaseManifestValidationError("manifest_allowed_images_invalid")

        release_id = _require_string(payload, "release_id")
        git_tag = _require_string(payload, "git_tag")
        version = _require_string(payload, "version")
        commit_sha = _require_string(payload, "commit_sha")
        target_id = _require_string(payload, "target_id")
        api_image = _require_string(payload, "api_image")
        web_image = _require_string(payload, "web_image")
        created_at = _require_string(payload, "created_at")
        health_check_version = _require_string(payload, "health_check_version")

        if not _IDENTIFIER_RE.fullmatch(release_id):
            raise ReleaseManifestValidationError("manifest_release_id_invalid")
        if not _TAG_RE.fullmatch(git_tag) or version != git_tag:
            raise ReleaseManifestValidationError("manifest_tag_version_mismatch")
        if not _SHA_RE.fullmatch(commit_sha):
            raise ReleaseManifestValidationError("manifest_commit_sha_invalid")
        if not _TARGET_RE.fullmatch(target_id) or target_id not in DEPLOYMENT_TARGETS:
            raise ReleaseManifestValidationError("manifest_target_id_invalid")
        if not _IDENTIFIER_RE.fullmatch(health_check_version):
            raise ReleaseManifestValidationError("manifest_health_check_version_invalid")
        _validate_created_at(created_at)
        _validate_image(api_image, api_repository, "api_image")
        _validate_image(web_image, web_repository, "web_image")

        return cls(
            schema_version=payload["schema_version"],
            release_id=release_id,
            git_tag=git_tag,
            version=version,
            commit_sha=commit_sha,
            target_id=target_id,
            api_image=api_image,
            web_image=web_image,
            created_at=created_at,
            health_check_version=health_check_version,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "release_id": self.release_id,
            "git_tag": self.git_tag,
            "version": self.version,
            "commit_sha": self.commit_sha,
            "target_id": self.target_id,
            "api_image": self.api_image,
            "web_image": self.web_image,
            "created_at": self.created_at,
            "health_check_version": self.health_check_version,
        }
