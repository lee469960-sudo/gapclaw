"""Fixed mTLS protocol boundary between GAP and its Deploy Runner."""

from __future__ import annotations

import ssl
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import httpx

from app.models import ReleaseLifecycleAudit
from app.services.release_ledger import ReleaseLedger, ReleaseLedgerError


class ReleaseRunnerError(RuntimeError):
    pass


ALLOWED_RELEASE_RUNNER_URLS = frozenset({
    "https://gap-runner.internal:9443",
    "https://gap-runner-staging.internal:9443",
})


@dataclass(frozen=True)
class ReleaseRunnerTls:
    base_url: str
    ca_file: Path
    cert_file: Path
    key_file: Path
    timeout_seconds: float = 10.0

    def __post_init__(self) -> None:
        if self.base_url.rstrip("/") not in ALLOWED_RELEASE_RUNNER_URLS:
            raise ReleaseRunnerError("release_runner_url_not_allowed")


class ReleaseRunnerClient:
    def __init__(self, tls: ReleaseRunnerTls, *, client_factory=httpx.Client):
        self.tls, self.client_factory = tls, client_factory

    def status(self) -> dict[str, Any]:
        return self._request("GET", "/v1/status")

    def health(self) -> dict[str, Any]:
        return self._request("GET", "/v1/health")

    def rollback(self) -> dict[str, Any]:
        return self._request("POST", "/v1/rollback")

    def deploy(self, manifest: Mapping[str, object]) -> dict[str, Any]:
        return self._request("POST", "/v1/deploy", json_payload=dict(manifest))

    def _request(self, method: str, path: str, *, json_payload: dict[str, object] | None = None) -> dict[str, Any]:
        try:
            ssl_context = ssl.create_default_context(cafile=str(self.tls.ca_file))
            ssl_context.load_cert_chain(certfile=str(self.tls.cert_file), keyfile=str(self.tls.key_file))
            with self.client_factory(
                verify=ssl_context, timeout=self.tls.timeout_seconds,
            ) as client:
                request_kwargs = {"json": json_payload} if json_payload is not None else {}
                response = client.request(method, f"{self.tls.base_url.rstrip('/')}{path}", **request_kwargs)
                response.raise_for_status()
                payload = response.json()
        except (OSError, httpx.HTTPError) as exc:
            raise ReleaseRunnerError("release_runner_request_failed") from exc
        if not isinstance(payload, dict):
            raise ReleaseRunnerError("release_runner_response_invalid")
        return payload


class ReleaseCallbackService:
    def __init__(self, ledger: ReleaseLedger):
        self.ledger = ledger

    def accept(self, payload: dict[str, object], *, proxy_verified: str) -> dict[str, object]:
        if proxy_verified != "SUCCESS":
            raise ReleaseRunnerError("release_runner_client_untrusted")
        try:
            self.ledger.store_manifest(payload)
            audit, created = self.ledger.record_terminal_callback(payload)
        except ReleaseLedgerError as exc:
            raise ReleaseRunnerError(exc.reason) from exc
        return {"release_id": audit.release_id, "status": audit.status, "created": created}


_TERMINAL_PHASES = {"succeeded", "failed", "rolled_back", "reconciliation_required"}


class ReleaseReconciler:
    """Compare the fixed Runner status shape with persisted terminal audits."""

    def __init__(self, ledger: ReleaseLedger):
        self.ledger = ledger

    def reconcile(self, runner_status: Mapping[str, object]) -> dict[str, object]:
        release = runner_status.get("release")
        if not isinstance(release, Mapping):
            return {"state": "reconciliation_required", "reason": "release_runner_status_invalid"}
        release_id = str(release.get("release_id") or "")
        target_id = str(release.get("target_id") or "")
        phase = str(release.get("phase") or "")
        if not phase or (phase != "idle" and (not release_id or not target_id)):
            return {"state": "reconciliation_required", "reason": "release_runner_status_invalid"}
        if phase not in _TERMINAL_PHASES:
            return {"state": "reconciliation_required", "reason": "release_runner_not_terminal"}
        audit = self.ledger.db.query(ReleaseLifecycleAudit).filter_by(
            release_id=release_id, target_id=target_id, status=phase,
        ).first()
        if audit is None:
            return {"state": "reconciliation_required", "reason": "release_audit_missing"}
        return {"state": "synchronized", "release_id": release_id, "status": phase}
