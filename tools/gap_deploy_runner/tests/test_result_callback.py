from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from tools.gap_deploy_runner.result_callback import GapCallbackTls, ResultCallbackDispatcher
from tools.gap_deploy_runner.state import ReleaseStateStore
from tools.gap_deploy_runner.tests.test_release_state import _manifest


class FlakyTransport:
    def __init__(self):
        self.calls: list[dict[str, object]] = []
        self.fail = True

    def post(self, event: dict[str, object]) -> None:
        self.calls.append(event)
        if self.fail:
            raise OSError("offline")


def test_callback_url_is_fixed():
    with pytest.raises(ValueError, match="runner_callback_url_not_allowed"):
        GapCallbackTls("ca", "cert", "key", url="https://example.test/callback")


def test_staging_callback_url_is_derived_from_its_fixed_target():
    callback = GapCallbackTls("ca", "cert", "key", target_id="staging")

    assert callback.url == "https://runner-staging.gapclaw.online/internal/release-runner/callback"
    with pytest.raises(ValueError, match="runner_callback_url_not_allowed"):
        GapCallbackTls(
            "ca", "cert", "key", target_id="staging",
            url="https://runner.gapclaw.online/internal/release-runner/callback",
        )


def test_terminal_callback_is_persisted_and_retried_after_restart(tmp_path):
    now = datetime(2026, 9, 11, 8, 0, tzinfo=timezone.utc)
    clock = lambda: now
    store = ReleaseStateStore(tmp_path / "release-state.json", target_id="production")
    transport = FlakyTransport()
    dispatcher = ResultCallbackDispatcher(store, transport, clock=clock)

    report = dispatcher.publish(_manifest(1), status="succeeded", health_result="ok")

    assert report == {"sent": 0, "failed": 1, "pending": 1}
    pending = store.load()["pending_callbacks"]
    assert pending[0]["event"]["api_image"].endswith("1" * 64)
    assert pending[0]["attempts"] == 1

    transport.fail = False
    later = now + timedelta(seconds=5)
    restarted = ResultCallbackDispatcher(
        ReleaseStateStore(store.path, target_id="production"), transport, clock=lambda: later,
    )
    assert restarted.retry_pending() == {"sent": 1, "failed": 0, "pending": 0}
    assert transport.calls[-1]["status"] == "succeeded"
