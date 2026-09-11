"""Controlled, fail-closed Release Management configuration."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from app.config import Settings
from app.services.release_runner import ReleaseRunnerTls


class ReleaseConfigError(ValueError):
    pass


@dataclass(frozen=True)
class ReleaseEnvironmentBundle:
    environment: str
    target_id: str
    runner_base_url: str
    callback_identity: str


_ENVIRONMENT_BUNDLES = {
    "production": ReleaseEnvironmentBundle(
        environment="production",
        target_id="production",
        runner_base_url="https://gap-runner.internal:9443",
        callback_identity="runner.gapclaw.online",
    ),
    "staging": ReleaseEnvironmentBundle(
        environment="staging",
        target_id="staging",
        runner_base_url="https://gap-runner-staging.internal:9443",
        callback_identity="runner-staging.gapclaw.online",
    ),
}


def _environment_bundle(environment: str) -> ReleaseEnvironmentBundle:
    try:
        return _ENVIRONMENT_BUNDLES[environment.strip().lower()]
    except (AttributeError, KeyError) as exc:
        raise ReleaseConfigError("release_environment_invalid") from exc


@dataclass(frozen=True)
class ReleaseManagementConfig:
    environment: str
    target_id: str
    runner_base_url: str
    callback_identity: str
    ca_file: Path
    client_cert_file: Path
    client_key_file: Path
    timeout_seconds: float

    @classmethod
    def from_settings(cls, settings: Settings) -> "ReleaseManagementConfig":
        config = cls.preview_from_settings(settings)
        config.validate()
        return config

    @classmethod
    def preview_from_settings(cls, settings: Settings) -> "ReleaseManagementConfig":
        """Load fields for a redacted readiness view without accepting their use."""
        bundle = _environment_bundle(settings.release_environment)
        return cls(
            environment=bundle.environment,
            target_id=bundle.target_id,
            runner_base_url=bundle.runner_base_url,
            callback_identity=bundle.callback_identity,
            ca_file=Path(settings.release_runner_ca_file or ""),
            client_cert_file=Path(settings.release_runner_client_cert_file or ""),
            client_key_file=Path(settings.release_runner_client_key_file or ""),
            timeout_seconds=settings.release_runner_timeout_seconds,
        )

    def validate(self) -> None:
        if not re.fullmatch(r"[a-z][a-z0-9-]{0,63}", self.target_id):
            raise ReleaseConfigError("release_target_invalid")
        if not all(str(path) and path.is_absolute() for path in (self.ca_file, self.client_cert_file, self.client_key_file)):
            raise ReleaseConfigError("release_mtls_reference_invalid")
        if not 0 < self.timeout_seconds <= 60:
            raise ReleaseConfigError("release_runner_timeout_invalid")
        try:
            ReleaseRunnerTls(
                self.runner_base_url, self.ca_file, self.client_cert_file, self.client_key_file, self.timeout_seconds,
            )
        except Exception as exc:
            raise ReleaseConfigError("release_runner_url_invalid") from exc

    def runner_tls(self) -> ReleaseRunnerTls:
        self.validate()
        return ReleaseRunnerTls(
            self.runner_base_url, self.ca_file, self.client_cert_file, self.client_key_file, self.timeout_seconds,
        )

    def public_view(self) -> dict[str, object]:
        ready = True
        reason = "ready"
        try:
            self.validate()
        except ReleaseConfigError as exc:
            ready, reason = False, exc.args[0]
        return {
            "environment": self.environment,
            "target_id": self.target_id,
            "callback_identity": self.callback_identity,
            "timeout_seconds": self.timeout_seconds,
            "ready": ready,
            "reason": reason,
            "ca_configured": self.ca_file.is_absolute(),
            "client_certificate_configured": self.client_cert_file.is_absolute(),
            "client_identity_configured": self.client_key_file.is_absolute(),
        }
