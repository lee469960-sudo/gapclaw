"""Health-gated fixed-manifest deployment orchestration."""

from __future__ import annotations

from time import sleep
from typing import Callable, Protocol

from tools.gap_deploy_runner.release_manifest import ReleaseManifest
from tools.gap_deploy_runner.state import ReleaseStateStore


class ComposeAdapter(Protocol):
    def apply(self, manifest: ReleaseManifest) -> None: ...
    def services_healthy(self) -> bool: ...


class ApiHealthAdapter(Protocol):
    def ready(self) -> bool: ...


class TerminalCallbackDispatcher(Protocol):
    def publish(
        self,
        manifest: ReleaseManifest,
        *,
        status: str,
        health_result: str = "",
        rollback_result: str = "",
        failure_summary: str = "",
    ) -> dict[str, int]: ...


class DeploymentError(RuntimeError):
    pass


class HealthGatedDeployment:
    def __init__(
        self,
        store: ReleaseStateStore,
        compose: ComposeAdapter,
        api: ApiHealthAdapter,
        *,
        allowed_images: dict[str, str],
        callbacks: TerminalCallbackDispatcher | None = None,
        health_attempts: int = 1,
        health_interval_seconds: float = 0,
        sleeper: Callable[[float], None] = sleep,
    ):
        if health_attempts < 1:
            raise ValueError("health_attempts must be positive")
        self.store, self.compose, self.api, self.allowed_images, self.callbacks = (
            store, compose, api, allowed_images, callbacks,
        )
        self.health_attempts = health_attempts
        self.health_interval_seconds = health_interval_seconds
        self.sleeper = sleeper

    def deploy(self, manifest: ReleaseManifest) -> dict[str, object]:
        with self.store.locked():
            self.store.record_received(manifest)
            self.compose.apply(manifest)
            if self._healthy():
                return self._complete(manifest, self.store.record_success(manifest), health_result="ok")
            baseline = self.store.load()["last_known_healthy"]
            if not baseline:
                self.store.record_terminal(manifest, phase="reconciliation_required")
                return self._complete(
                    manifest,
                    {"phase": "reconciliation_required", "reason": "runner_no_healthy_release"},
                    health_result="failed", failure_summary="runner_no_healthy_release",
                )
            rollback = ReleaseManifest.from_dict(baseline, allowed_images=self.allowed_images)
            self.compose.apply(rollback)
            healthy = self._healthy()
            phase = "rolled_back" if healthy else "reconciliation_required"
            self.store.record_terminal(manifest, phase=phase)
            return self._complete(
                manifest, {"phase": phase, "release_id": rollback.release_id}, health_result="failed",
                rollback_result="succeeded" if healthy else "failed",
                failure_summary="release_health_failed",
            )

    def _healthy(self) -> bool:
        for attempt in range(self.health_attempts):
            try:
                if self.compose.services_healthy() and self.api.ready():
                    return True
            except TimeoutError:
                pass
            if attempt + 1 < self.health_attempts:
                self.sleeper(self.health_interval_seconds)
        return False

    def _complete(
        self,
        manifest: ReleaseManifest,
        outcome: dict[str, object],
        *,
        health_result: str,
        rollback_result: str = "",
        failure_summary: str = "",
    ) -> dict[str, object]:
        if self.callbacks is not None:
            self.callbacks.publish(
                manifest, status=str(outcome["phase"]), health_result=health_result,
                rollback_result=rollback_result, failure_summary=failure_summary,
            )
        return outcome
