"""Deterministic GitHub CI Hook intake for the fixed Release Runner protocol."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import hmac
import json
import re
from dataclasses import dataclass
from typing import Any, Callable, Mapping

from app.config import Settings
from app.services.release_ledger import ReleaseLedger, ReleaseLedgerError
from app.services.release_runner import ReleaseRunnerClient, ReleaseRunnerError


_DELIVERY_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_TAG_RE = re.compile(r"^v(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)$")
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_IMAGE_RE = re.compile(r"^[a-z0-9][a-z0-9./_-]*@sha256:[0-9a-f]{64}$")
_IDENTIFIER_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")
_MANIFEST_FIELDS = frozenset({
    "schema_version", "release_id", "git_tag", "version", "commit_sha", "target_id",
    "api_image", "web_image", "created_at", "health_check_version",
})


class ReleaseHookError(ValueError):
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


@dataclass(frozen=True)
class ReleaseHookConfig:
    secret: str
    max_age_seconds: int

    @classmethod
    def from_settings(cls, settings: Settings) -> "ReleaseHookConfig":
        config = cls(secret=settings.release_hook_secret, max_age_seconds=settings.release_hook_max_age_seconds)
        if len(config.secret) < 32:
            raise ReleaseHookError("release_hook_not_configured")
        if not 1 <= config.max_age_seconds <= 3600:
            raise ReleaseHookError("release_hook_time_window_invalid")
        return config


def _parse_timestamp(value: object, *, reason: str) -> datetime:
    if not isinstance(value, str):
        raise ReleaseHookError(reason)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ReleaseHookError(reason) from exc
    if parsed.tzinfo is None:
        raise ReleaseHookError(reason)
    return parsed.astimezone(timezone.utc)


def _validated_manifest(payload: object, *, target_id: str) -> dict[str, object]:
    if not isinstance(payload, dict) or set(payload) != _MANIFEST_FIELDS:
        raise ReleaseHookError("release_hook_manifest_invalid")
    if type(payload.get("schema_version")) is not int or payload["schema_version"] != 1:
        raise ReleaseHookError("release_hook_manifest_invalid")
    values = {key: payload[key] for key in _MANIFEST_FIELDS}
    if not all(isinstance(value, str) and value for key, value in values.items() if key != "schema_version"):
        raise ReleaseHookError("release_hook_manifest_invalid")
    if values["target_id"] != target_id:
        raise ReleaseHookError("release_hook_target_not_allowed")
    if not _IDENTIFIER_RE.fullmatch(str(values["release_id"])):
        raise ReleaseHookError("release_hook_manifest_invalid")
    if not _TAG_RE.fullmatch(str(values["git_tag"])) or values["version"] != values["git_tag"]:
        raise ReleaseHookError("release_hook_manifest_invalid")
    if not _SHA_RE.fullmatch(str(values["commit_sha"])):
        raise ReleaseHookError("release_hook_manifest_invalid")
    if not _IMAGE_RE.fullmatch(str(values["api_image"])) or not _IMAGE_RE.fullmatch(str(values["web_image"])):
        raise ReleaseHookError("release_hook_manifest_invalid")
    if not _IDENTIFIER_RE.fullmatch(str(values["health_check_version"])):
        raise ReleaseHookError("release_hook_manifest_invalid")
    _parse_timestamp(values["created_at"], reason="release_hook_manifest_invalid")
    return values


class ReleaseHookService:
    """Verify, canonically parse and durably deduplicate one CI delivery."""

    def __init__(
        self,
        ledger: ReleaseLedger,
        runner: ReleaseRunnerClient,
        config: ReleaseHookConfig,
        *,
        target_id: str,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ):
        self.ledger, self.runner, self.config, self.target_id, self.clock = ledger, runner, config, target_id, clock

    def accept(self, raw_body: bytes, *, signature: str | None) -> dict[str, object]:
        expected = "sha256=" + hmac.new(self.config.secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
        if not isinstance(signature, str) or not hmac.compare_digest(signature, expected):
            raise ReleaseHookError("release_hook_signature_invalid")
        try:
            envelope = json.loads(raw_body)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ReleaseHookError("release_hook_payload_invalid") from exc
        if not isinstance(envelope, dict) or set(envelope) != {"delivery_id", "issued_at", "manifest"}:
            raise ReleaseHookError("release_hook_payload_invalid")
        canonical = json.dumps(envelope, sort_keys=True, separators=(",", ":")).encode("utf-8")
        if not hmac.compare_digest(raw_body, canonical):
            raise ReleaseHookError("release_hook_payload_not_canonical")
        delivery_id = envelope["delivery_id"]
        if not isinstance(delivery_id, str) or not _DELIVERY_ID_RE.fullmatch(delivery_id):
            raise ReleaseHookError("release_hook_delivery_invalid")
        issued_at = _parse_timestamp(envelope["issued_at"], reason="release_hook_timestamp_invalid")
        now = self.clock().astimezone(timezone.utc)
        if abs((now - issued_at).total_seconds()) > self.config.max_age_seconds:
            raise ReleaseHookError("release_hook_timestamp_expired")
        manifest = _validated_manifest(envelope["manifest"], target_id=self.target_id)
        try:
            delivery, created = self.ledger.accept_hook_delivery(manifest, delivery_id=delivery_id)
        except ReleaseLedgerError as exc:
            raise ReleaseHookError(exc.reason) from exc
        if not created:
            return {"status": "duplicate", "delivery_id": delivery.delivery_id, "release_id": delivery.release_id}
        try:
            runner_result = self.runner.deploy(manifest)
        except ReleaseRunnerError as exc:
            self.ledger.mark_hook_dispatch_failed(delivery, reason=str(exc))
            raise ReleaseHookError("release_hook_runner_unavailable") from exc
        return {"status": "accepted", "delivery_id": delivery.delivery_id, "release_id": delivery.release_id, "runner": runner_result}
