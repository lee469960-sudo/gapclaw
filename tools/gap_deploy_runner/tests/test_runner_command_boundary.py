from __future__ import annotations

import pytest

from tools.gap_deploy_runner.cli import build_parser
from tools.gap_deploy_runner.runner import DeployRunner, RunnerCommandError


API_REPOSITORY = "registry.example.com/gap-api"
WEB_REPOSITORY = "registry.example.com/gap-web"


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


def _runner() -> DeployRunner:
    return DeployRunner(
        allowed_images={"api": API_REPOSITORY, "web": WEB_REPOSITORY},
        target_id="production",
    )


def test_deploy_accepts_only_a_valid_allowed_manifest():
    result = _runner().dispatch("deploy", manifest_payload=_payload())

    assert result["status"] == "accepted"
    assert result["release"]["phase"] == "received"
    assert result["release"]["release_id"] == "v1.2.3-01234567"


def test_unknown_operation_has_no_command_fallback():
    with pytest.raises(RunnerCommandError, match="runner_operation_not_allowed"):
        _runner().dispatch("shell", manifest_payload=_payload())


def test_unapproved_images_are_rejected_before_runner_accepts_release():
    payload = _payload()
    payload["api_image"] = f"registry.example.com/other@sha256:{'c' * 64}"

    with pytest.raises(RunnerCommandError, match="manifest_api_image_repository_not_allowed"):
        _runner().dispatch("deploy", manifest_payload=payload)


def test_target_must_match_fixed_runner_target():
    payload = _payload()
    payload["target_id"] = "staging"

    with pytest.raises(RunnerCommandError, match="runner_target_not_allowed"):
        _runner().dispatch("deploy", manifest_payload=payload)


def test_staging_runner_accepts_only_staging_manifest_before_compose_work():
    staging = DeployRunner(
        allowed_images={"api": API_REPOSITORY, "web": WEB_REPOSITORY}, target_id="staging",
    )
    staging_manifest = _payload()
    staging_manifest["target_id"] = "staging"

    assert staging.dispatch("deploy", manifest_payload=staging_manifest)["status"] == "accepted"
    with pytest.raises(RunnerCommandError, match="runner_target_not_allowed"):
        staging.dispatch("deploy", manifest_payload=_payload())


def test_status_and_health_are_limited_to_fixed_release_state():
    runner = _runner()

    assert runner.dispatch("status") == {
        "status": "ok",
        "release": {"target_id": "production", "phase": "idle", "release_id": "", "api_image": "", "web_image": ""},
    }
    assert runner.dispatch("health")["status"] == "unavailable"


def test_rollback_cannot_accept_a_user_supplied_tag_or_digest():
    with pytest.raises(TypeError):
        _runner().dispatch("rollback", tag="v0.0.1")  # type: ignore[call-arg]
    with pytest.raises(RunnerCommandError, match="runner_no_healthy_release"):
        _runner().dispatch("rollback")


def test_cli_exposes_only_fixed_operations_and_manifest_flag():
    parser = build_parser()

    assert parser.parse_args(["deploy", "--manifest", "/tmp/release.json"]).operation == "deploy"
    assert parser.parse_args(["status"]).operation == "status"
    with pytest.raises(SystemExit):
        parser.parse_args(["deploy", "--command", "docker ps"])
