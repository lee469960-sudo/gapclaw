from __future__ import annotations

import pytest

from tools.gap_deploy_runner.release_manifest import ReleaseManifest, ReleaseManifestValidationError


API_REPOSITORY = "registry.example.com/gap-api"
WEB_REPOSITORY = "registry.example.com/gap-web"
ALLOWED_IMAGES = {"api": API_REPOSITORY, "web": WEB_REPOSITORY}


def _payload() -> dict[str, object]:
    return {
        "schema_version": 1,
        "release_id": "v1.2.3-01234567",
        "git_tag": "v1.2.3",
        "version": "v1.2.3",
        "commit_sha": "0123456789abcdef0123456789abcdef01234567",
        "target_id": "production",
        "api_image": f"{API_REPOSITORY}@sha256:{'a' * 64}",
        "web_image": f"{WEB_REPOSITORY}@sha256:{'b' * 64}",
        "created_at": "2026-09-11T08:00:00Z",
        "health_check_version": "v1",
    }


def test_valid_release_manifest_round_trips_without_mutation():
    payload = _payload()

    manifest = ReleaseManifest.from_dict(payload, allowed_images=ALLOWED_IMAGES)

    assert manifest.to_dict() == payload


def test_tag_and_version_must_match_exactly():
    payload = _payload()
    payload["version"] = "v1.2.4"

    with pytest.raises(ReleaseManifestValidationError, match="manifest_tag_version_mismatch"):
        ReleaseManifest.from_dict(payload, allowed_images=ALLOWED_IMAGES)


@pytest.mark.parametrize("field", ["api_image", "web_image"])
def test_mutable_image_tags_are_rejected(field: str):
    payload = _payload()
    repository = API_REPOSITORY if field == "api_image" else WEB_REPOSITORY
    payload[field] = f"{repository}:latest"

    with pytest.raises(ReleaseManifestValidationError, match=f"manifest_{field}_not_digest"):
        ReleaseManifest.from_dict(payload, allowed_images=ALLOWED_IMAGES)


def test_invalid_digest_is_rejected():
    payload = _payload()
    payload["api_image"] = f"{API_REPOSITORY}@sha256:not-a-digest"

    with pytest.raises(ReleaseManifestValidationError, match="manifest_api_image_not_digest"):
        ReleaseManifest.from_dict(payload, allowed_images=ALLOWED_IMAGES)


def test_unapproved_repository_is_rejected():
    payload = _payload()
    payload["web_image"] = f"registry.example.com/other@sha256:{'c' * 64}"

    with pytest.raises(ReleaseManifestValidationError, match="manifest_web_image_repository_not_allowed"):
        ReleaseManifest.from_dict(payload, allowed_images=ALLOWED_IMAGES)


@pytest.mark.parametrize("target_id", ("production", "staging"))
def test_only_supported_deployment_targets_are_accepted(target_id: str):
    payload = _payload()
    payload["target_id"] = target_id

    assert ReleaseManifest.from_dict(payload, allowed_images=ALLOWED_IMAGES).target_id == target_id


def test_unknown_deployment_target_is_rejected_before_runner_selection():
    payload = _payload()
    payload["target_id"] = "preview"

    with pytest.raises(ReleaseManifestValidationError, match="manifest_target_id_invalid"):
        ReleaseManifest.from_dict(payload, allowed_images=ALLOWED_IMAGES)
