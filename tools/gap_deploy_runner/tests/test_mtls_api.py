from __future__ import annotations

from tools.gap_deploy_runner.mtls import RunnerHttpApi
from tools.gap_deploy_runner.runner import DeployRunner
from tools.gap_deploy_runner.state import ReleaseStateStore
from tools.gap_deploy_runner.tests.test_release_state import _manifest


def _api() -> RunnerHttpApi:
    return RunnerHttpApi(DeployRunner(allowed_images={"api": "registry.example.com/gap-api", "web": "registry.example.com/gap-web"}, target_id="production"))


def test_network_api_exposes_only_fixed_operations_and_manifest_deploy():
    api = _api()
    assert api.handle("GET", "/v1/status")[0] == 200
    assert api.handle("GET", "/v1/health")[0] == 200
    assert api.handle("POST", "/v1/rollback") == (409, {"reason": "runner_no_healthy_release"})
    assert api.handle("POST", "/v1/deploy") == (409, {"reason": "runner_manifest_required"})
    assert api.handle("POST", "/v1/deploy", manifest_payload=_manifest(1).to_dict())[1]["status"] == "accepted"
    assert api.handle("POST", "/v1/rollback?tag=v0.0.1")[0] == 404


def test_network_api_rejects_a_different_mtls_client_identity_before_callback_or_deploy():
    calls = []

    class Callbacks:
        def retry_pending(self):
            calls.append("retry")
            return {"sent": 0, "failed": 0, "pending": 0}

    api = RunnerHttpApi(
        DeployRunner(allowed_images={"api": "registry.example.com/gap-api", "web": "registry.example.com/gap-web"}, target_id="production"),
        callbacks=Callbacks(),
    )

    assert api.handle("POST", "/v1/deploy", manifest_payload=_manifest(1).to_dict(), client_common_name="other-client") == (
        403, {"reason": "runner_client_identity_not_allowed"},
    )
    assert calls == []


def test_fixed_runner_access_retries_pending_callbacks_without_new_route():
    calls = []

    class Callbacks:
        def retry_pending(self):
            calls.append("retry")
            return {"sent": 0, "failed": 0, "pending": 0}

    api = RunnerHttpApi(
        DeployRunner(allowed_images={"api": "registry.example.com/gap-api", "web": "registry.example.com/gap-web"}, target_id="production"),
        callbacks=Callbacks(),
    )
    assert api.handle("GET", "/v1/status")[0] == 200
    assert calls == ["retry"]


def test_status_returns_only_persisted_known_healthy_rollback_target(tmp_path):
    store = ReleaseStateStore(tmp_path / "state.json", target_id="production")
    store.record_success(_manifest(1))
    api = RunnerHttpApi(
        DeployRunner(allowed_images={"api": "registry.example.com/gap-api", "web": "registry.example.com/gap-web"}, target_id="production"),
        state_store=store,
    )

    status, payload = api.handle("GET", "/v1/status")

    assert status == 200
    assert payload["last_known_healthy"] == {"release_id": "v1.2.1-01234567", "target_id": "production"}
