from __future__ import annotations

import threading
import time

from tools.gap_deploy_runner.release_manifest import ReleaseManifest
from tools.gap_deploy_runner.state import ReleaseStateStore


def _manifest(index: int) -> ReleaseManifest:
    digest = f"{index:x}" * 64
    return ReleaseManifest.from_dict({
        "schema_version": 1, "release_id": f"v1.2.{index}-01234567", "git_tag": f"v1.2.{index}", "version": f"v1.2.{index}",
        "commit_sha": "0123456789abcdef0123456789abcdef01234567", "target_id": "production",
        "api_image": f"registry.example.com/gap-api@sha256:{digest}", "web_image": f"registry.example.com/gap-web@sha256:{digest}",
        "created_at": "2026-09-11T08:00:00Z", "health_check_version": "v1",
    }, allowed_images={"api": "registry.example.com/gap-api", "web": "registry.example.com/gap-web"})


def test_state_round_trips_latest_known_healthy_release(tmp_path):
    store = ReleaseStateStore(tmp_path / "release-state.json", target_id="production")
    store.record_received(_manifest(1))
    store.record_success(_manifest(1))

    loaded = ReleaseStateStore(store.path, target_id="production").load()

    assert loaded["phase"] == "succeeded"
    assert loaded["last_known_healthy"]["release_id"] == "v1.2.1-01234567"


def test_success_history_keeps_only_latest_five(tmp_path):
    store = ReleaseStateStore(tmp_path / "release-state.json", target_id="production")
    for index in range(7):
        store.record_success(_manifest(index))

    assert [item["release_id"] for item in store.load()["success_history"]] == [
        f"v1.2.{index}-01234567" for index in range(2, 7)
    ]


def test_interrupted_active_state_requires_reconciliation(tmp_path):
    store = ReleaseStateStore(tmp_path / "release-state.json", target_id="production")
    store.record_received(_manifest(1))

    assert ReleaseStateStore(store.path, target_id="production").load()["phase"] == "reconciliation_required"


def test_target_lock_serializes_concurrent_access(tmp_path):
    store = ReleaseStateStore(tmp_path / "release-state.json", target_id="production")
    active = 0
    peak = 0
    guard = threading.Lock()

    def acquire() -> None:
        nonlocal active, peak
        with store.locked():
            with guard:
                active += 1
                peak = max(peak, active)
            time.sleep(0.005)
            with guard:
                active -= 1

    threads = [threading.Thread(target=acquire) for _ in range(12)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert peak == 1
