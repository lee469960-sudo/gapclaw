from types import SimpleNamespace

from tools.gap_deploy_runner.runtime import DockerComposeHost
from tools.gap_deploy_runner.tests.test_release_state import _manifest


def test_compose_apply_uses_managed_compose_directory(monkeypatch, tmp_path):
    compose_file = tmp_path / "compose" / "gap-production.compose.yml"
    compose_file.parent.mkdir()
    compose_file.write_text("services: {}\n", encoding="utf-8")
    manifest = _manifest(1)
    config = SimpleNamespace(
        target_id="production",
        env_file=tmp_path / "gap.env",
        compose_environment=lambda _manifest: {"GAP_RELEASE_API_IMAGE": manifest.api_image},
    )
    host = DockerComposeHost(config, SimpleNamespace(compose_file=compose_file))
    captured = {}

    def run(*args, **kwargs):
        operation = "up" if "up" in args[0] else "ps"
        captured[operation] = kwargs
        return SimpleNamespace(returncode=0, stderr="", stdout="[]")

    monkeypatch.setattr("tools.gap_deploy_runner.runtime.subprocess.run", run)

    host.apply(manifest)
    assert host.services_healthy() is False

    assert captured["up"]["cwd"] == compose_file.parent
    assert captured["ps"]["cwd"] == compose_file.parent
    assert captured["ps"]["env"]["GAP_RELEASE_API_IMAGE"] == manifest.api_image
