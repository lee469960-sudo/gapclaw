from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import ReleaseManifestRecord
from app.services.release_hook import ReleaseHookConfig, ReleaseHookService
from app.services.release_ledger import ReleaseLedger
from app.services.release_runner import ReleaseCallbackService
from tools.gap_deploy_runner.deployment import HealthGatedDeployment
from tools.gap_deploy_runner.release_manifest import ReleaseManifest
from tools.gap_deploy_runner.result_callback import ResultCallbackDispatcher
from tools.gap_deploy_runner.state import ReleaseStateStore


NOW = datetime(2026, 9, 12, 9, 0, tzinfo=timezone.utc)
SECRET = "s" * 32
ALLOWED_IMAGES = {"api": "registry.example.com/gap-api", "web": "registry.example.com/gap-web"}


def _manifest(number: int) -> dict[str, object]:
    return {
        "schema_version": 1,
        "release_id": f"v1.2.{number}-01234567",
        "git_tag": f"v1.2.{number}",
        "version": f"v1.2.{number}",
        "commit_sha": "0123456789abcdef0123456789abcdef01234567",
        "target_id": "production",
        "api_image": f"registry.example.com/gap-api@sha256:{str(number) * 64}",
        "web_image": f"registry.example.com/gap-web@sha256:{str(number + 1) * 64}",
        "created_at": "2026-09-12T08:59:00Z",
        "health_check_version": "v1",
    }


def _signed_hook(number: int) -> tuple[bytes, str]:
    envelope = {
        "delivery_id": f"release-{number}",
        "issued_at": NOW.isoformat().replace("+00:00", "Z"),
        "manifest": _manifest(number),
    }
    raw = json.dumps(envelope, sort_keys=True, separators=(",", ":")).encode()
    signature = "sha256=" + hmac.new(SECRET.encode(), raw, hashlib.sha256).hexdigest()
    return raw, signature


class Compose:
    def __init__(self, checks: list[bool]):
        self.checks, self.applied = iter(checks), []

    def apply(self, manifest: ReleaseManifest) -> None:
        self.applied.append(manifest.release_id)

    def services_healthy(self) -> bool:
        return next(self.checks)


class ApiHealth:
    def __init__(self, checks: list[bool]): self.checks = iter(checks)
    def ready(self) -> bool: return next(self.checks)


class CallbackTransport:
    def __init__(self, callback: ReleaseCallbackService, *, available: bool):
        self.callback, self.available = callback, available

    def post(self, event: dict[str, object]) -> None:
        if not self.available:
            raise OSError("gap_unavailable")
        self.callback.accept(event, proxy_verified="SUCCESS")


class ContractRunner:
    """A Runner adapter whose only input is the manifest received from the Hook."""

    def __init__(self, deployment: HealthGatedDeployment):
        self.deployment, self.received = deployment, []

    def deploy(self, payload: dict[str, object]) -> dict[str, object]:
        manifest = ReleaseManifest.from_dict(payload, allowed_images=ALLOWED_IMAGES)
        self.received.append(manifest)
        return self.deployment.deploy(manifest)


def _hook_service(ledger: ReleaseLedger, runner: ContractRunner) -> ReleaseHookService:
    return ReleaseHookService(
        ledger, runner, ReleaseHookConfig(SECRET, 300), target_id="production", clock=lambda: NOW,
    )


def test_signed_hook_to_fixed_runner_contract_covers_digest_health_rollback_and_callback_retry(tmp_path):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    ledger = ReleaseLedger(sessionmaker(bind=engine)())
    callback = ReleaseCallbackService(ledger)
    store = ReleaseStateStore(tmp_path / "release-state.json", target_id="production")

    online = CallbackTransport(callback, available=True)
    first_runner = ContractRunner(HealthGatedDeployment(
        store, Compose([True]), ApiHealth([True]), allowed_images=ALLOWED_IMAGES,
        callbacks=ResultCallbackDispatcher(store, online, clock=lambda: NOW),
    ))
    raw, signature = _signed_hook(1)
    assert _hook_service(ledger, first_runner).accept(raw, signature=signature)["runner"]["phase"] == "succeeded"
    assert ledger.db.get(ReleaseManifestRecord, "v1.2.1-01234567").api_image.endswith("1" * 64)

    failed_compose = Compose([False, True])
    failed_runner = ContractRunner(HealthGatedDeployment(
        store, failed_compose, ApiHealth([True]), allowed_images=ALLOWED_IMAGES,
        callbacks=ResultCallbackDispatcher(store, online, clock=lambda: NOW),
    ))
    raw, signature = _signed_hook(2)
    assert _hook_service(ledger, failed_runner).accept(raw, signature=signature)["runner"]["phase"] == "rolled_back"
    assert failed_compose.applied == ["v1.2.2-01234567", "v1.2.1-01234567"]

    offline = CallbackTransport(callback, available=False)
    retry_runner = ContractRunner(HealthGatedDeployment(
        store, Compose([True]), ApiHealth([True]), allowed_images=ALLOWED_IMAGES,
        callbacks=ResultCallbackDispatcher(store, offline, clock=lambda: NOW),
    ))
    raw, signature = _signed_hook(3)
    assert _hook_service(ledger, retry_runner).accept(raw, signature=signature)["runner"]["phase"] == "succeeded"
    assert len(store.load()["pending_callbacks"]) == 1
    assert ResultCallbackDispatcher(store, online, clock=lambda: NOW + timedelta(seconds=5)).retry_pending() == {
        "sent": 1, "failed": 0, "pending": 0,
    }
