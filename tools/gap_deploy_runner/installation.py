"""Fixed host-layout validation for the GAP Deploy Runner."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import subprocess
from typing import Callable


class RunnerInstallationError(RuntimeError):
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


def docker_compose_available() -> bool:
    try:
        completed = subprocess.run(
            ["docker", "compose", "version"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError:
        return False
    return completed.returncode == 0


@dataclass(frozen=True)
class RunnerInstallation:
    root: Path
    app_env_file: Path
    target_id: str = "production"

    @property
    def compose_file(self) -> Path:
        if self.target_id not in {"production", "staging"}:
            raise RunnerInstallationError("runner_target_not_allowed")
        return self.root / "compose" / f"gap-{self.target_id}.compose.yml"

    @property
    def runner_env_file(self) -> Path:
        return self.root / "runner.env"

    @property
    def state_dir(self) -> Path:
        return self.root / "state"

    @property
    def tls_dir(self) -> Path:
        return self.root / "tls"

    @property
    def tls_files(self) -> tuple[Path, Path, Path]:
        return (
            self.tls_dir / "ca.crt",
            self.tls_dir / "runner.crt",
            self.tls_dir / "runner.key",
        )

    def validate(self, *, compose_available: Callable[[], bool] = docker_compose_available) -> None:
        required = (
            (self.app_env_file, "runner_install_env_missing"),
            (self.runner_env_file, "runner_install_runner_env_missing"),
            (self.compose_file, "runner_install_compose_missing"),
            (self.tls_files[0], "runner_install_ca_missing"),
            (self.tls_files[1], "runner_install_cert_missing"),
            (self.tls_files[2], "runner_install_key_missing"),
            (self.state_dir, "runner_install_state_missing"),
        )
        for path, reason in required:
            if not path.exists():
                raise RunnerInstallationError(reason)
        if not compose_available():
            raise RunnerInstallationError("runner_install_docker_compose_missing")
        if self._readable_by_others(self.app_env_file) or self._readable_by_others(self.tls_files[2]):
            raise RunnerInstallationError("runner_install_sensitive_file_permissions")
        if self._writable_by_others(self.state_dir):
            raise RunnerInstallationError("runner_install_state_permissions")

    @staticmethod
    def _readable_by_others(path: Path) -> bool:
        return path.stat().st_mode & 0o077 != 0

    @staticmethod
    def _writable_by_others(path: Path) -> bool:
        return path.stat().st_mode & 0o022 != 0
