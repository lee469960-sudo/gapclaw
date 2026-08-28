"""API-root → daemon-host-root Workspace bind-path mapping with fail-closed validation.

When the API runs inside a container and drives an external Docker daemon, the
Workspace path visible to the API process differs from the path visible to the
daemon host. This module maps the former to the latter only after proving the run
directory is genuine, so a missing mapping, a guessed path or a cross-run source
can never become a Docker bind source.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

from app.config import get_settings


SENTINEL_FILENAME = ".code-agent-run"


class WorkspaceMountError(RuntimeError):
    def __init__(self, reason: str = "workspace_mount_invalid"):
        self.reason = reason
        super().__init__(reason)


def workspace_sentinel(run_id: str, snapshot_hash: str) -> str:
    """Deterministic per-run identity marker binding the run to its frozen snapshot."""
    return hashlib.sha256(
        f"{run_id}\0{snapshot_hash}".encode("utf-8")
    ).hexdigest()


def write_workspace_sentinel(run_root: Path, run_id: str, snapshot_hash: str) -> None:
    """Persist the run sentinel at the run root, proven by the mount mapping later."""
    (run_root / SENTINEL_FILENAME).write_text(
        workspace_sentinel(run_id, snapshot_hash), encoding="utf-8"
    )


def resolve_workspace_host_path(
    workspace_path: str | Path,
    *,
    run_id: str,
    snapshot_hash: str,
    settings=None,
) -> str:
    """Map an API-visible Workspace path to the daemon-host bind source, or fail closed.

    Validates, in order: the roots are absolute; the workspace is a real directory
    strictly inside the configured API root laid out as ``<api_root>/<run_id>/workspace``;
    the run sentinel at ``<api_root>/<run_id>`` matches the frozen run identity; and the
    resulting host path is normalized and contained inside the configured host root.
    Any violation raises ``WorkspaceMountError("workspace_mount_invalid")``.
    """
    settings = settings or get_settings()
    api_root = Path(settings.code_workspace_api_root or "")
    host_root = Path(settings.code_workspace_host_root or "")
    if not api_root.is_absolute() or not host_root.is_absolute():
        raise WorkspaceMountError()

    # API side: resolve and contain. Symlinks/escapes and missing directories fail.
    try:
        resolved_api_root = api_root.resolve(strict=True)
        resolved_workspace = Path(workspace_path).resolve(strict=True)
        relative = resolved_workspace.relative_to(resolved_api_root)
    except (OSError, ValueError):
        raise WorkspaceMountError()
    # The workspace must sit exactly at <run_id>/workspace; anything else is a
    # guessed path or a cross-run source.
    if relative.parts != (run_id, "workspace"):
        raise WorkspaceMountError()

    # Sentinel proves this is the genuine run directory for this exact frozen snapshot.
    sentinel = resolved_api_root / run_id / SENTINEL_FILENAME
    try:
        value = sentinel.read_text(encoding="utf-8").strip()
    except OSError:
        raise WorkspaceMountError()
    if value != workspace_sentinel(run_id, snapshot_hash):
        raise WorkspaceMountError()

    # Host side: normalize and require lexical containment within the host root.
    if ".." in host_root.parts:
        raise WorkspaceMountError()
    host_root_norm = os.path.normpath(str(host_root))
    host_path_norm = os.path.normpath(str(host_root / relative))
    if host_path_norm != host_root_norm and not host_path_norm.startswith(
        host_root_norm + os.sep
    ):
        raise WorkspaceMountError()
    return host_path_norm
