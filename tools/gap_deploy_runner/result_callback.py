"""Persisted, fixed mTLS result callbacks from Runner to GAP."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import ssl
from typing import Protocol
from urllib.request import Request, urlopen

from tools.gap_deploy_runner.release_manifest import ReleaseManifest
from tools.gap_deploy_runner.state import ReleaseStateStore


_CALLBACK_URLS = {
    "production": "https://runner.gapclaw.online/internal/release-runner/callback",
    "staging": "https://runner-staging.gapclaw.online/internal/release-runner/callback",
}


@dataclass(frozen=True)
class GapCallbackTls:
    ca_file: Path
    cert_file: Path
    key_file: Path
    timeout_seconds: float = 10.0
    target_id: str = "production"
    url: str = ""

    def __post_init__(self) -> None:
        expected_url = _CALLBACK_URLS.get(self.target_id)
        if expected_url is None or self.url not in {"", expected_url}:
            raise ValueError("runner_callback_url_not_allowed")
        object.__setattr__(self, "url", expected_url)

    def client_context(self) -> ssl.SSLContext:
        context = ssl.create_default_context(cafile=str(self.ca_file))
        context.load_cert_chain(certfile=str(self.cert_file), keyfile=str(self.key_file))
        return context


class CallbackTransport(Protocol):
    def post(self, event: dict[str, object]) -> None: ...


class HttpsCallbackTransport:
    def __init__(self, tls: GapCallbackTls):
        self.tls = tls

    def post(self, event: dict[str, object]) -> None:
        request = Request(
            self.tls.url,
            data=json.dumps(event, sort_keys=True).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request, context=self.tls.client_context(), timeout=self.tls.timeout_seconds) as response:
            if response.status < 200 or response.status >= 300:
                raise OSError("runner_callback_rejected")


class ResultCallbackDispatcher:
    """Queues every terminal event before sending; delivery failures are retried later."""

    def __init__(self, store: ReleaseStateStore, transport: CallbackTransport, *, clock=None):
        self.store, self.transport = store, transport
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def publish(
        self,
        manifest: ReleaseManifest,
        *,
        status: str,
        health_result: str = "",
        rollback_result: str = "",
        failure_summary: str = "",
    ) -> dict[str, int]:
        event = {
            **manifest.to_dict(), "status": status, "occurred_at": self._now(),
            "health_result": health_result, "rollback_result": rollback_result,
            "failure_summary": failure_summary[:1000],
        }
        self.store.queue_callback(event)
        return self.retry_pending()

    def retry_pending(self) -> dict[str, int]:
        now = self._now()
        sent = failed = 0
        for event in self.store.due_callbacks(now=now):
            try:
                self.transport.post(event)
            except Exception:
                attempts = self.store.callback_attempts(
                    release_id=str(event["release_id"]), status=str(event["status"]),
                )
                self.store.record_callback_failure(
                    release_id=str(event["release_id"]), status=str(event["status"]),
                    next_attempt_at=self._retry_at(now, attempts=attempts), reason="runner_callback_unavailable",
                )
                failed += 1
            else:
                self.store.record_callback_delivered(release_id=str(event["release_id"]), status=str(event["status"]))
                sent += 1
        pending = len(self.store.load()["pending_callbacks"])
        return {"sent": sent, "failed": failed, "pending": pending}

    def _now(self) -> str:
        return self.clock().astimezone(timezone.utc).isoformat(timespec="seconds")

    @staticmethod
    def _retry_at(now: str, *, attempts: int | None) -> str:
        delay = min(300, 5 * (2 ** max(0, attempts or 0)))
        return (datetime.fromisoformat(now) + timedelta(seconds=delay)).isoformat(timespec="seconds")
