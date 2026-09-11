from __future__ import annotations

from pathlib import Path

import pytest

from tools.gap_deploy_runner.installation import RunnerInstallation, RunnerInstallationError


ASSETS = Path(__file__).parents[1] / "assets"


def test_staging_installation_selects_only_the_staging_compose_asset(tmp_path: Path):
    installation = RunnerInstallation(
        root=tmp_path / "gap-staging-runner", app_env_file=tmp_path / "gap-staging" / ".env", target_id="staging",
    )

    assert installation.compose_file == tmp_path / "gap-staging-runner" / "compose" / "gap-staging.compose.yml"
    with pytest.raises(RunnerInstallationError, match="runner_target_not_allowed"):
        RunnerInstallation(root=tmp_path, app_env_file=tmp_path / ".env", target_id="preview").compose_file


def test_staging_assets_use_only_staging_roots_networks_identity_and_state():
    sources = {
        path.name: path.read_text(encoding="utf-8")
        for path in (
            ASSETS / "gap-staging.compose.yml",
            ASSETS / "gap-deploy-runner-staging.service",
            ASSETS / "runner-staging.env.example",
            ASSETS / "gap-staging.env.example",
            ASSETS / "initialize-staging-host.sh",
        )
    }
    combined = "\n".join(sources.values())

    assert "/opt/gap-staging-runner" in combined
    assert "/opt/gap-staging" in combined
    assert "gap-staging" in combined
    assert "GAP_RUNNER_TARGET_ID=staging" in combined
    assert "AcrPull only" in combined
    assert "gap-staging-pull" in combined
    for forbidden in (
        "/opt/gap-runner", "/opt/gap/release-mtls", "gap-prod", "gap-edge", "gap-proxy",
        "GAP_RUNNER_TARGET_ID=production", "runner.gapclaw.online",
    ):
        assert forbidden not in combined


def test_staging_compose_keeps_staging_mtls_state_and_private_runner_mapping_separate():
    compose = (ASSETS / "gap-staging.compose.yml").read_text(encoding="utf-8")

    assert "/opt/gap-staging/release-mtls:/run/gap-release-mtls:ro" in compose
    assert '"gap-runner-staging.internal:host-gateway"' in compose
    assert "name: gap-staging" in compose
    assert '"127.0.0.1:${API_PORT:-18000}:8000"' in compose
    assert '"127.0.0.1:${WEB_PORT:-18080}:8080"' in compose
