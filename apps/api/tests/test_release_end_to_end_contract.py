from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import ReleaseManifestRecord
from app.services.release_ledger import ReleaseLedger
from app.services.release_runner import ReleaseCallbackService, ReleaseReconciler
from tools.gap_deploy_runner.deployment import HealthGatedDeployment
from tools.gap_deploy_runner.release_manifest import ReleaseManifest
from tools.gap_deploy_runner.result_callback import ResultCallbackDispatcher
from tools.gap_deploy_runner.state import ReleaseStateStore


ALLOWED_IMAGES = {"api": "registry.example.com/gap-api", "web": "registry.example.com/gap-web"}


def _manifest(number: int) -> ReleaseManifest:
    return ReleaseManifest.from_dict({
        "schema_version": 1, "release_id": f"v1.2.{number}-01234567", "git_tag": f"v1.2.{number}",
        "version": f"v1.2.{number}", "commit_sha": "0123456789abcdef0123456789abcdef01234567",
        "target_id": "production", "api_image": f"registry.example.com/gap-api@sha256:{str(number) * 64}",
        "web_image": f"registry.example.com/gap-web@sha256:{str(number + 1) * 64}",
        "created_at": "2026-09-11T08:00:00Z", "health_check_version": "v1",
    }, allowed_images=ALLOWED_IMAGES)


class Compose:
    def __init__(self, checks: list[bool]): self.checks, self.applied = iter(checks), []
    def apply(self, manifest: ReleaseManifest): self.applied.append(manifest.release_id)
    def services_healthy(self) -> bool: return next(self.checks)


class ApiHealth:
    def __init__(self, checks: list[bool]): self.checks = iter(checks)
    def ready(self) -> bool: return next(self.checks)


class GapTransport:
    def __init__(self, service: ReleaseCallbackService, *, available: bool):
        self.service, self.available, self.events = service, available, []

    def post(self, event: dict[str, object]) -> None:
        self.events.append(event)
        if not self.available:
            raise OSError("gap_temporarily_unavailable")
        self.service.accept(event, proxy_verified="SUCCESS")


def test_digest_deploy_health_recovery_callback_retry_and_restart_reconciliation(tmp_path):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    ledger = ReleaseLedger(sessionmaker(bind=engine)())
    gap = ReleaseCallbackService(ledger)
    now = datetime(2026, 9, 11, 8, 0, tzinfo=timezone.utc)
    store = ReleaseStateStore(tmp_path / "release-state.json", target_id="production")
    online = GapTransport(gap, available=True)
    dispatcher = ResultCallbackDispatcher(store, online, clock=lambda: now)

    first = _manifest(1)
    deployment = HealthGatedDeployment(store, Compose([True]), ApiHealth([True]), allowed_images=ALLOWED_IMAGES, callbacks=dispatcher)
    assert deployment.deploy(first)["phase"] == "succeeded"
    assert ledger.db.get(ReleaseManifestRecord, first.release_id).api_image == first.api_image

    failed = _manifest(2)
    deployment = HealthGatedDeployment(store, Compose([False, True]), ApiHealth([True]), allowed_images=ALLOWED_IMAGES, callbacks=dispatcher)
    assert deployment.deploy(failed)["phase"] == "rolled_back"
    assert store.load()["phase"] == "rolled_back"
    assert online.events[-1]["status"] == "rolled_back"

    pending = _manifest(3)
    offline = GapTransport(gap, available=False)
    offline_dispatcher = ResultCallbackDispatcher(store, offline, clock=lambda: now)
    deployment = HealthGatedDeployment(store, Compose([True]), ApiHealth([True]), allowed_images=ALLOWED_IMAGES, callbacks=offline_dispatcher)
    assert deployment.deploy(pending)["phase"] == "succeeded"
    assert len(store.load()["pending_callbacks"]) == 1

    restarted = ResultCallbackDispatcher(
        ReleaseStateStore(store.path, target_id="production"), online, clock=lambda: now + timedelta(seconds=5),
    )
    assert restarted.retry_pending() == {"sent": 1, "failed": 0, "pending": 0}
    status = {"release": {"release_id": pending.release_id, "target_id": "production", "phase": "succeeded"}}
    assert ReleaseReconciler(ledger).reconcile(status)["state"] == "synchronized"
