from __future__ import annotations

from pathlib import Path

import pytest

from tools.gap_deploy_runner.config import RunnerConfig, RunnerConfigError
from tools.gap_deploy_runner.release_manifest import ReleaseManifest


ASSET = Path(__file__).resolve().parents[1] / "assets" / "gap-prod.compose.yml"


def _env(tmp_path: Path, extra: str = "") -> Path:
    path = tmp_path / "gap.env"
    path.write_text(
        "\n".join((
            "ALIYUN_REGISTRY=registry.example.com",
            "ALIYUN_REGISTRY_USERNAME=host-pull-user",
            "ALIYUN_REGISTRY_PASSWORD=host-pull-password",
            "ALIYUN_IMAGE=gap-api",
            "IMAGE_DB=postgres:16",
            extra,
        )),
        encoding="utf-8",
    )
    return path


def _manifest() -> ReleaseManifest:
    return ReleaseManifest.from_dict({
        "schema_version": 1, "release_id": "v1.2.3-01234567", "git_tag": "v1.2.3", "version": "v1.2.3",
        "commit_sha": "0123456789abcdef0123456789abcdef01234567", "target_id": "production",
        "api_image": f"registry.example.com/gap-api@sha256:{'a' * 64}",
        "web_image": f"registry.example.com/gap-web@sha256:{'b' * 64}",
        "created_at": "2026-09-11T08:00:00Z", "health_check_version": "v1",
    }, allowed_images={"api": "registry.example.com/gap-api", "web": "registry.example.com/gap-web"})


def test_host_env_derives_allowed_repositories_and_keeps_credentials_local(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "must-not-be-read")
    config = RunnerConfig.from_env_file(_env(tmp_path), target_id="production")

    assert config.allowed_images == {"api": "registry.example.com/gap-api", "web": "registry.example.com/gap-web"}
    environment = config.compose_environment(_manifest())
    assert environment["GAP_RELEASE_API_IMAGE"].endswith("a" * 64)
    assert "GITHUB_TOKEN" not in environment


def test_host_env_rejects_github_credential_keys(tmp_path: Path):
    with pytest.raises(RunnerConfigError, match="runner_github_credential_forbidden"):
        RunnerConfig.from_env_file(_env(tmp_path, "GITHUB_TOKEN=forbidden"), target_id="production")


def test_compose_asset_accepts_only_immutable_release_image_variables():
    compose = ASSET.read_text(encoding="utf-8")

    assert "GAP_RELEASE_API_IMAGE" in compose
    assert "GAP_RELEASE_WEB_IMAGE" in compose
    assert ":latest" not in compose
    assert "deploy/docker-compose.prod.yml" not in compose
