from types import SimpleNamespace
import threading

import pytest

from tools.gap_deploy_runner.runtime import DockerComposeHost, DeployRunnerRuntime
from tools.gap_deploy_runner.deployment import HealthGatedDeployment
from tools.gap_deploy_runner.runner import RunnerCommandError
from tools.gap_deploy_runner.state import ReleaseStateStore
from tools.gap_deploy_runner.tests.test_release_state import _manifest


def _runtime(tmp_path, *, compose=None):
    runtime = DeployRunnerRuntime.__new__(DeployRunnerRuntime)
    runtime.config = SimpleNamespace(
        target_id="production",
        allowed_images={"api": "registry.example.com/gap-api", "web": "registry.example.com/gap-web"},
    )
    runtime.store = ReleaseStateStore(tmp_path / "state.json", target_id="production")
    runtime._operation_lock = threading.Lock()
    runtime.deployment = HealthGatedDeployment(
        runtime.store, compose or SimpleNamespace(apply=lambda _: None, services_healthy=lambda: True),
        SimpleNamespace(ready=lambda: True), allowed_images=runtime.config.allowed_images,
    )
    return runtime


def test_prepared_deploy_is_durable_and_nonterminal_before_execution(tmp_path):
    runtime = _runtime(tmp_path)
    result, execute = runtime.prepare_deploy(_manifest(1).to_dict())
    assert result["status"] == "accepted"
    assert runtime.store.load()["phase"] == "received"
    assert runtime.store.load()["last_known_healthy"] is None
    execute()
    assert runtime.store.load()["phase"] == "succeeded"


def test_prepared_deploy_rejects_invalid_manifest_without_state_change(tmp_path):
    runtime = _runtime(tmp_path)
    with pytest.raises(RunnerCommandError, match="manifest_fields_invalid"):
        runtime.prepare_deploy({"command": "docker compose down"})
    assert runtime.store.load()["phase"] == "idle"
    assert not runtime._operation_lock.locked()


def test_duplicate_current_release_does_not_execute_again(tmp_path):
    calls = []
    runtime = _runtime(tmp_path, compose=SimpleNamespace(apply=calls.append, services_healthy=lambda: True))
    _, execute = runtime.prepare_deploy(_manifest(1).to_dict())
    execute()
    duplicate, execute = runtime.prepare_deploy(_manifest(1).to_dict())
    assert duplicate["status"] == "duplicate"
    assert execute is None
    assert len(calls) == 1
    changed = _manifest(1).to_dict()
    changed["api_image"] = _manifest(2).api_image
    with pytest.raises(RunnerCommandError, match="runner_release_conflict"):
        runtime.prepare_deploy(changed)
    assert not runtime._operation_lock.locked()


def test_restart_does_not_replay_accepted_deployment(tmp_path):
    original = _runtime(tmp_path)
    _, execute = original.prepare_deploy(_manifest(1).to_dict())
    restarted = _runtime(tmp_path)
    assert restarted.store.load()["phase"] == "reconciliation_required"
    duplicate, replay = restarted.prepare_deploy(_manifest(1).to_dict())
    assert duplicate["status"] == "duplicate"
    assert duplicate["release"]["phase"] == "reconciliation_required"
    assert replay is None
    # Simulate process exit without executing the abandoned closure.
    original._operation_lock.release()


def test_deploy_preparation_serializes_with_an_inflight_deployment(tmp_path):
    runtime = _runtime(tmp_path)
    _, first = runtime.prepare_deploy(_manifest(1).to_dict())
    waiting, accepted = threading.Event(), threading.Event()
    second = []

    def prepare_second():
        waiting.set()
        second.append(runtime.prepare_deploy(_manifest(2).to_dict()))
        accepted.set()

    thread = threading.Thread(target=prepare_second)
    thread.start()
    try:
        assert waiting.wait(1)
        assert not accepted.wait(0.05)
        assert runtime.store.load()["current"]["release_id"] == _manifest(1).release_id
        first()
        assert accepted.wait(1)
        assert second[0][0]["release"]["release_id"] == _manifest(2).release_id
        second[0][1]()
    finally:
        thread.join(timeout=1)


def test_background_failure_is_explicitly_non_successful(tmp_path):
    def fail(_):
        raise OSError("private transport detail")

    runtime = _runtime(tmp_path, compose=SimpleNamespace(apply=fail, services_healthy=lambda: True))
    _, execute = runtime.prepare_deploy(_manifest(1).to_dict())
    execute()
    assert runtime.store.load()["phase"] == "reconciliation_required"
    assert runtime.store.load()["last_known_healthy"] is None
    assert not runtime._operation_lock.locked()


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


def test_compose_health_accepts_compose_line_delimited_json(monkeypatch, tmp_path):
    compose_file = tmp_path / "compose" / "gap-production.compose.yml"
    compose_file.parent.mkdir()
    compose_file.write_text("services: {}\n", encoding="utf-8")
    config = SimpleNamespace(target_id="production", env_file=tmp_path / "gap.env")
    host = DockerComposeHost(config, SimpleNamespace(compose_file=compose_file))
    monkeypatch.setattr(
        "tools.gap_deploy_runner.runtime.subprocess.run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=0,
            stdout='{"Health":"healthy"}\n{"Health":"healthy"}\n',
        ),
    )

    assert host.services_healthy() is True
