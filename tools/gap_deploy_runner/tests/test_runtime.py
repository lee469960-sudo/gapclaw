from types import SimpleNamespace

from tools.gap_deploy_runner.runtime import DockerComposeHost
from tools.gap_deploy_runner.tests.test_release_state import _manifest


def test_compose_apply_uses_managed_compose_directory(monkeypatch, tmp_path):
    compose_file = tmp_path / "compose" / "gap-production.compose.yml"
    compose_file.parent.mkdir()
    compose_file.write_text("services: {}\n", encoding="utf-8")
    config = SimpleNamespace(
        target_id="production",
        env_file=tmp_path / "gap.env",
        compose_environment=lambda _manifest: {},
    )
    host = DockerComposeHost(config, SimpleNamespace(compose_file=compose_file))
    captured = {}

    def run(*_args, **kwargs):
        captured.update(kwargs)
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr("tools.gap_deploy_runner.runtime.subprocess.run", run)

    host.apply(_manifest(1))

    assert captured["cwd"] == compose_file.parent
