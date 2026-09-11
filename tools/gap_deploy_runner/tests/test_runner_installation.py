from __future__ import annotations

from pathlib import Path

import pytest

from tools.gap_deploy_runner.installation import RunnerInstallation, RunnerInstallationError


def _installation(tmp_path: Path) -> RunnerInstallation:
    return RunnerInstallation(root=tmp_path / "gap-runner", app_env_file=tmp_path / "gap" / ".env")


def _prepare(installation: RunnerInstallation) -> None:
    installation.app_env_file.parent.mkdir(parents=True)
    installation.app_env_file.write_text("ALIYUN_REGISTRY=registry.example.com\n", encoding="utf-8")
    installation.app_env_file.chmod(0o600)
    installation.root.mkdir(parents=True, exist_ok=True)
    installation.runner_env_file.write_text("GAP_RUNNER_TARGET_ID=production\n", encoding="utf-8")
    installation.compose_file.parent.mkdir(parents=True)
    installation.compose_file.write_text("services: {}\n", encoding="utf-8")
    installation.state_dir.mkdir(parents=True)
    installation.state_dir.chmod(0o750)
    for certificate in installation.tls_files:
        certificate.parent.mkdir(parents=True, exist_ok=True)
        certificate.write_text("test\n", encoding="utf-8")
    installation.tls_files[2].chmod(0o600)


@pytest.mark.parametrize(
    ("prepare", "reason"),
    (
        (lambda installation: None, "runner_install_env_missing"),
        (lambda installation: _prepare(installation), ""),
    ),
)
def test_preflight_requires_host_env(installation: RunnerInstallation, prepare, reason: str):
    prepare(installation)
    if reason:
        with pytest.raises(RunnerInstallationError, match=reason):
            installation.validate(compose_available=lambda: True)
    else:
        installation.validate(compose_available=lambda: True)


@pytest.fixture
def installation(tmp_path: Path) -> RunnerInstallation:
    return _installation(tmp_path)


@pytest.mark.parametrize(
    ("path_name", "reason"),
    (
        ("compose_file", "runner_install_compose_missing"),
        ("state_dir", "runner_install_state_missing"),
    ),
)
def test_preflight_detects_missing_runner_files(installation: RunnerInstallation, path_name: str, reason: str):
    _prepare(installation)
    path = getattr(installation, path_name)
    if path.is_dir():
        path.rmdir()
    else:
        path.unlink()
    with pytest.raises(RunnerInstallationError, match=reason):
        installation.validate(compose_available=lambda: True)


@pytest.mark.parametrize(
    ("certificate_index", "reason"),
    ((0, "runner_install_ca_missing"), (1, "runner_install_cert_missing"), (2, "runner_install_key_missing")),
)
def test_preflight_detects_missing_tls_material(installation: RunnerInstallation, certificate_index: int, reason: str):
    _prepare(installation)
    installation.tls_files[certificate_index].unlink()
    with pytest.raises(RunnerInstallationError, match=reason):
        installation.validate(compose_available=lambda: True)


def test_preflight_detects_missing_docker_compose(installation: RunnerInstallation):
    _prepare(installation)
    with pytest.raises(RunnerInstallationError, match="runner_install_docker_compose_missing"):
        installation.validate(compose_available=lambda: False)


@pytest.mark.parametrize("path_name", ("app_env_file",))
def test_preflight_rejects_sensitive_file_readable_by_others(installation: RunnerInstallation, path_name: str):
    _prepare(installation)
    getattr(installation, path_name).chmod(0o640)
    with pytest.raises(RunnerInstallationError, match="runner_install_sensitive_file_permissions"):
        installation.validate(compose_available=lambda: True)


def test_preflight_rejects_private_key_readable_by_others(installation: RunnerInstallation):
    _prepare(installation)
    installation.tls_files[2].chmod(0o640)
    with pytest.raises(RunnerInstallationError, match="runner_install_sensitive_file_permissions"):
        installation.validate(compose_available=lambda: True)


def test_preflight_rejects_state_directory_writable_by_others(installation: RunnerInstallation):
    _prepare(installation)
    installation.state_dir.chmod(0o770)
    with pytest.raises(RunnerInstallationError, match="runner_install_state_permissions"):
        installation.validate(compose_available=lambda: True)


def test_systemd_asset_uses_fixed_paths_and_hardening():
    asset = (Path(__file__).parents[1] / "assets" / "gap-deploy-runner.service").read_text(encoding="utf-8")
    assert "User=gap-runner" in asset
    assert "ExecStart=/opt/gap-runner/bin/gap-deploy-runner-server --root /opt/gap-runner --app-env /opt/gap/.env" in asset
    assert "NoNewPrivileges=true" in asset
    assert "ReadWritePaths=/opt/gap-runner/state" in asset


def test_runner_binary_installer_packages_only_the_reviewed_runner_modules_and_fixed_wrappers():
    assets = Path(__file__).parents[1] / "assets"
    installer = (assets / "install-runner-binaries.sh").read_text(encoding="utf-8")

    assert '"$source_root"/*.py "$library_root/"' in installer
    assert "gap-deploy-runner-server" in installer
    assert "python3 -m tools.gap_deploy_runner" in (assets / "gap-deploy-runner").read_text(encoding="utf-8")
    assert "python3 -m tools.gap_deploy_runner.server" in (assets / "gap-deploy-runner-server").read_text(encoding="utf-8")
    assert 'sh "$(dirname "$0")/install-runner-binaries.sh" "$runner_root"' in (assets / "initialize-host.sh").read_text(encoding="utf-8")


def test_deployment_documentation_matches_private_runner_and_fixed_asset_paths():
    documentation = (Path(__file__).parents[3] / "docs" / "deployment.md").read_text(encoding="utf-8")

    assert "https://gap-runner.internal:9443" in documentation
    assert "https://runner.gapclaw.online/internal/release-runner/callback" in documentation
    assert "/opt/gap-runner/bin/gap-deploy-runner deploy --manifest" in documentation
    assert "tools/gap_deploy_runner/assets/initialize-host.sh" in documentation
    assert "install-production-caddy.sh" in documentation
    assert "Caddyfile.production" in documentation
    assert "/etc/caddy/production/runner-ca.crt" in documentation
    assert "import /etc/caddy/sites/*.Caddyfile" in documentation
    assert "不会启动或加载 Nginx" in documentation
    assert "gap-edge" not in documentation
    assert "gap-proxy" not in documentation
    assert "/opt/gap-edge/tls/runner-ca.crt" not in documentation
    assert "/opt/gap/release-mtls" in documentation
    assert "不读取 `scripts/deploy.sh`" in documentation
    assert "git pull" not in documentation
