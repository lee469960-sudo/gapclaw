"""Installable fixed-operation runtime for the Deploy Runner host binaries."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
import subprocess
import threading
from typing import Any, Callable, Mapping
from urllib.request import urlopen

from tools.gap_deploy_runner.config import RunnerConfig, RunnerConfigError, _read_env
from tools.gap_deploy_runner.deployment import ApiHealthAdapter, ComposeAdapter, HealthGatedDeployment
from tools.gap_deploy_runner.installation import RunnerInstallation, RunnerInstallationError
from tools.gap_deploy_runner.release_manifest import ReleaseManifest, ReleaseManifestValidationError
from tools.gap_deploy_runner.result_callback import GapCallbackTls, HttpsCallbackTransport, ResultCallbackDispatcher
from tools.gap_deploy_runner.runner import RunnerCommandError, RunnerOperation
from tools.gap_deploy_runner.state import ReleaseStateStore

logger = logging.getLogger(__name__)


class RunnerRuntimeError(RuntimeError):
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


class DockerComposeHost(ComposeAdapter):
    """The reviewed Compose invocation; no caller supplies a command or file path."""

    def __init__(self, config: RunnerConfig, installation: RunnerInstallation):
        self.config, self.installation = config, installation
        self._active_environment: dict[str, str] = {}

    def _command(self, *args: str) -> list[str]:
        return [
            "docker", "compose", "--project-name", f"gap-{self.config.target_id}",
            "--env-file", str(self.config.env_file), "-f", str(self.installation.compose_file),
            "--profile", "scheduled-tasks", *args,
        ]

    def apply(self, manifest: ReleaseManifest) -> None:
        environment = self.config.compose_environment(manifest)
        self._active_environment = environment
        completed = subprocess.run(
            self._command("up", "--detach", "--remove-orphans"),
            cwd=self.installation.compose_file.parent,
            env={**os.environ, **environment},
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        if completed.returncode != 0:
            detail = (completed.stderr or "").strip().splitlines()
            message = detail[-1] if detail else ""
            for value in environment.values():
                if len(value) >= 4:
                    message = message.replace(value, "[redacted]")
            logger.warning("GAP Compose apply failed (exit %s): %s", completed.returncode, message[:512])
            raise RunnerRuntimeError("runner_compose_apply_failed")

    def services_healthy(self) -> bool:
        completed = subprocess.run(
            self._command("ps", "--format", "json"),
            cwd=self.installation.compose_file.parent,
            env={**os.environ, **self._active_environment},
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            check=False,
        )
        if completed.returncode != 0:
            return False
        try:
            services = json.loads(completed.stdout or "[]")
        except json.JSONDecodeError:
            try:
                services = [json.loads(line) for line in (completed.stdout or "").splitlines() if line.strip()]
            except json.JSONDecodeError:
                return False
        if not isinstance(services, list) or not services:
            return False
        for item in services:
            if not isinstance(item, dict):
                return False
            health = str(item.get("Health") or "").strip()
            if health:
                if health != "healthy":
                    return False
                continue
            if str(item.get("State") or "").strip() != "running":
                return False
        return True


class LocalApiHealth(ApiHealthAdapter):
    def __init__(self, port: int):
        self.url = f"http://127.0.0.1:{port}/health"

    def ready(self) -> bool:
        try:
            with urlopen(self.url, timeout=5) as response:
                return 200 <= response.status < 300
        except OSError:
            return False


class DeployRunnerRuntime:
    """A host-local adapter exposing only the fixed Runner operation set."""

    def __init__(self, *, root: Path, app_env_file: Path):
        try:
            runner_env = _read_env(root / "runner.env")
            target_id = runner_env.get("GAP_RUNNER_TARGET_ID", "")
            if target_id not in {"production", "staging"}:
                raise RunnerRuntimeError("runner_target_not_allowed")
            self.installation = RunnerInstallation(root=root, app_env_file=app_env_file, target_id=target_id)
            self.installation.validate()
            self.config = RunnerConfig.from_env_file(app_env_file, target_id=target_id)
            self.listen = runner_env.get("GAP_RUNNER_LISTEN", "")
            self._api_port = int(self.config.values.get("API_PORT", "8000"))
        except (RunnerInstallationError, RunnerConfigError, ValueError) as exc:
            raise RunnerRuntimeError(getattr(exc, "reason", "runner_runtime_config_invalid")) from exc
        self.store = ReleaseStateStore(root / "state" / "release-state.json", target_id=self.config.target_id)
        self._operation_lock = threading.Lock()
        self.store.load()  # Reconcile an interrupted operation once, at startup.
        callbacks = ResultCallbackDispatcher(
            self.store,
            HttpsCallbackTransport(GapCallbackTls(*self.installation.tls_files, target_id=self.config.target_id)),
        )
        self.deployment = HealthGatedDeployment(
            self.store,
            DockerComposeHost(self.config, self.installation),
            LocalApiHealth(self._api_port),
            allowed_images=dict(self.config.allowed_images),
            callbacks=callbacks,
            health_attempts=12,
            health_interval_seconds=5,
        )

    def dispatch(self, operation: str, *, manifest_payload: Mapping[str, Any] | None = None) -> dict[str, Any]:
        try:
            action = RunnerOperation(operation)
        except ValueError as exc:
            raise RunnerCommandError("runner_operation_not_allowed") from exc
        if action is RunnerOperation.DEPLOY:
            with self._operation_lock:
                return self._deploy(manifest_payload)
        if action is RunnerOperation.STATUS:
            return {"status": "ok", "release": self._release_state()}
        if action is RunnerOperation.HEALTH:
            state = self._release_state()
            return {"status": "ok" if state["phase"] == "succeeded" else "unavailable", "release": state}
        with self._operation_lock:
            return self._rollback()

    def _validated_manifest(self, payload: Mapping[str, Any] | None) -> ReleaseManifest:
        if payload is None:
            raise RunnerCommandError("runner_manifest_required")
        try:
            manifest = ReleaseManifest.from_dict(payload, allowed_images=self.config.allowed_images)
        except ReleaseManifestValidationError as exc:
            raise RunnerCommandError(exc.reason) from exc
        if manifest.target_id != self.config.target_id:
            raise RunnerCommandError("runner_target_not_allowed")
        return manifest

    def prepare_deploy(
        self, payload: Mapping[str, Any] | None,
    ) -> tuple[dict[str, Any], Callable[[], None] | None]:
        manifest = self._validated_manifest(payload)
        self._operation_lock.acquire()
        try:
            with self.store.locked():
                current = self.store.load().get("current")
                if isinstance(current, dict) and current.get("release_id") == manifest.release_id:
                    if current != manifest.to_dict():
                        raise RunnerCommandError("runner_release_conflict")
                    duplicate = {"status": "duplicate", "release": self._release_state()}
                    self._operation_lock.release()
                    return duplicate, None
                self.store.record_received(manifest)
            accepted = {"status": "accepted", "release": self._release_state()}
        except Exception:
            self._operation_lock.release()
            raise

        def execute() -> None:
            try:
                self.deployment.deploy(manifest)
            except Exception:
                logger.warning("GAP asynchronous deployment requires reconciliation")
                with self.store.locked():
                    state = self.store.load()
                    if state["phase"] == "received":
                        self.store.record_terminal(manifest, phase="reconciliation_required")
                        self.deployment._complete(
                            manifest, {"phase": "reconciliation_required"},
                            health_result="failed", failure_summary="runner_deployment_interrupted",
                        )
            finally:
                self._operation_lock.release()

        return accepted, execute

    def _deploy(self, payload: Mapping[str, Any] | None) -> dict[str, Any]:
        manifest = self._validated_manifest(payload)
        try:
            return self.deployment.deploy(manifest)
        except RunnerRuntimeError as exc:
            raise RunnerCommandError(exc.reason) from exc

    def _rollback(self) -> dict[str, Any]:
        with self.store.locked():
            baseline = self.store.load()["last_known_healthy"]
            if not isinstance(baseline, dict):
                raise RunnerCommandError("runner_no_healthy_release")
            try:
                manifest = ReleaseManifest.from_dict(baseline, allowed_images=self.config.allowed_images)
                self.deployment.compose.apply(manifest)
            except (ReleaseManifestValidationError, RunnerRuntimeError) as exc:
                raise RunnerCommandError(getattr(exc, "reason", "runner_rollback_failed")) from exc
            if not self.deployment._healthy():
                raise RunnerCommandError("runner_rollback_failed")
            self.store.record_success(manifest)
            return {"status": "rolled_back", "release": self._release_state()}

    def _release_state(self) -> dict[str, str]:
        state = self.store.load()
        current = state.get("current")
        return {
            "target_id": self.config.target_id,
            "phase": str(state["phase"]),
            "release_id": str(current.get("release_id", "")) if isinstance(current, dict) else "",
            "api_image": str(current.get("api_image", "")) if isinstance(current, dict) else "",
            "web_image": str(current.get("web_image", "")) if isinstance(current, dict) else "",
        }
