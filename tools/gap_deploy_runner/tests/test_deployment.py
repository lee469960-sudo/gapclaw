from __future__ import annotations

from tools.gap_deploy_runner.deployment import HealthGatedDeployment
from tools.gap_deploy_runner.tests.test_release_state import _manifest
from tools.gap_deploy_runner.state import ReleaseStateStore


class Compose:
    def __init__(self, checks): self.checks, self.applied = iter(checks), []
    def apply(self, manifest): self.applied.append(manifest.release_id)
    def services_healthy(self): return next(self.checks)

class Api:
    def __init__(self, checks): self.checks = iter(checks)
    def ready(self): return next(self.checks)


def _runner(tmp_path, compose_checks, api_checks=None, callbacks=None):
    return HealthGatedDeployment(ReleaseStateStore(tmp_path / "state.json", target_id="production"), Compose(compose_checks), Api(api_checks or compose_checks), allowed_images={"api": "registry.example.com/gap-api", "web": "registry.example.com/gap-web"}, callbacks=callbacks)


def test_success_records_known_healthy(tmp_path):
    runner = _runner(tmp_path, [True], [True])
    assert runner.deploy(_manifest(1))["phase"] == "succeeded"


def test_health_polling_waits_for_a_starting_service(tmp_path):
    waits = []
    runner = HealthGatedDeployment(
        ReleaseStateStore(tmp_path / "state.json", target_id="production"),
        Compose([False, True]),
        Api([True]),
        allowed_images={"api": "registry.example.com/gap-api", "web": "registry.example.com/gap-web"},
        health_attempts=2,
        health_interval_seconds=5,
        sleeper=waits.append,
    )
    assert runner.deploy(_manifest(1))["phase"] == "succeeded"
    assert waits == [5]


def test_failed_health_automatically_rolls_back(tmp_path):
    runner = _runner(tmp_path, [True], [True])
    runner.deploy(_manifest(1))
    runner.compose.checks = iter([False, True]); runner.api.checks = iter([True])
    assert runner.deploy(_manifest(2))["phase"] == "rolled_back"
    assert runner.compose.applied[-2:] == ["v1.2.2-01234567", "v1.2.1-01234567"]


def test_no_baseline_requires_reconciliation(tmp_path):
    assert _runner(tmp_path, [False], [True]).deploy(_manifest(1))["reason"] == "runner_no_healthy_release"


def test_api_health_failure_also_triggers_rollback(tmp_path):
    runner = _runner(tmp_path, [True], [True])
    runner.deploy(_manifest(1))
    runner.compose.checks = iter([True, True]); runner.api.checks = iter([False, True])
    assert runner.deploy(_manifest(2))["phase"] == "rolled_back"


def test_api_health_timeout_triggers_rollback(tmp_path):
    runner = _runner(tmp_path, [True], [True])
    runner.deploy(_manifest(1))
    runner.compose.checks = iter([True, True])
    outcomes = iter([TimeoutError(), True])
    def ready():
        outcome = next(outcomes)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome
    runner.api.ready = ready
    assert runner.deploy(_manifest(2))["phase"] == "rolled_back"


def test_terminal_deployment_result_is_sent_to_fixed_callback_dispatcher(tmp_path):
    calls = []

    class Callbacks:
        def publish(self, manifest, **kwargs):
            calls.append((manifest.release_id, kwargs))
            return {"sent": 1, "failed": 0, "pending": 0}

    assert _runner(tmp_path, [True], [True], callbacks=Callbacks()).deploy(_manifest(1))["phase"] == "succeeded"
    assert calls == [("v1.2.1-01234567", {"status": "succeeded", "health_result": "ok", "rollback_result": "", "failure_summary": ""})]
