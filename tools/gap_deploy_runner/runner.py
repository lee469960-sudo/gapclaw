"""Fixed-operation command boundary for the GAP Deploy Runner."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any, Mapping

from tools.gap_deploy_runner.release_manifest import (
    ReleaseManifest,
    ReleaseManifestValidationError,
)


class RunnerOperation(str, Enum):
    DEPLOY = "deploy"
    STATUS = "status"
    HEALTH = "health"
    ROLLBACK = "rollback"


class RunnerCommandError(RuntimeError):
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


@dataclass(frozen=True)
class RunnerState:
    target_id: str
    phase: str
    release_id: str = ""
    api_image: str = ""
    web_image: str = ""

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


class DeployRunner:
    """In-memory command model; persistence and host execution are added separately."""

    def __init__(self, *, allowed_images: Mapping[str, str], target_id: str):
        self._allowed_images = dict(allowed_images)
        self._target_id = target_id
        self._state = RunnerState(target_id=target_id, phase="idle")

    def dispatch(
        self,
        operation: str,
        *,
        manifest_payload: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        try:
            action = RunnerOperation(operation)
        except ValueError as exc:
            raise RunnerCommandError("runner_operation_not_allowed") from exc

        if action is RunnerOperation.DEPLOY:
            return self._deploy(manifest_payload)
        if action is RunnerOperation.STATUS:
            return {"status": "ok", "release": self._state.to_dict()}
        if action is RunnerOperation.HEALTH:
            return {
                "status": "ok" if self._state.phase == "succeeded" else "unavailable",
                "release": self._state.to_dict(),
            }
        return self._rollback()

    def _deploy(self, manifest_payload: Mapping[str, Any] | None) -> dict[str, Any]:
        if manifest_payload is None:
            raise RunnerCommandError("runner_manifest_required")
        try:
            manifest = ReleaseManifest.from_dict(
                manifest_payload,
                allowed_images=self._allowed_images,
            )
        except ReleaseManifestValidationError as exc:
            raise RunnerCommandError(exc.reason) from exc
        if manifest.target_id != self._target_id:
            raise RunnerCommandError("runner_target_not_allowed")
        self._state = RunnerState(
            target_id=manifest.target_id,
            phase="received",
            release_id=manifest.release_id,
            api_image=manifest.api_image,
            web_image=manifest.web_image,
        )
        return {"status": "accepted", "release": self._state.to_dict()}

    def _rollback(self) -> dict[str, Any]:
        raise RunnerCommandError("runner_no_healthy_release")
