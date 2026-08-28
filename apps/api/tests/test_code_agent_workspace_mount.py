"""OpenSpec task 5.3: API-root → daemon-host-root Workspace bind-path mapping.

The mapping must fail closed (``workspace_mount_invalid``) on a missing mapping,
a path outside the API root, a cross-run source, a guessed path (missing/wrong
sentinel), a symlink escape or a host root containing traversal.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services.code_agent.workspace_mount import (
    WorkspaceMountError,
    resolve_workspace_host_path,
    workspace_sentinel,
    write_workspace_sentinel,
)

HOST_ROOT = "/srv/gap/code-agent/runs"


def _settings(api_root: Path, host_root: str = HOST_ROOT):
    return SimpleNamespace(
        code_workspace_api_root=str(api_root),
        code_workspace_host_root=host_root,
    )


def _prepare(tmp_path, run_id="run1", snapshot_hash="a" * 64, *, write=True):
    api_root = tmp_path / "api-runs"
    run_root = api_root / run_id
    workspace = run_root / "workspace"
    workspace.mkdir(parents=True)
    if write:
        write_workspace_sentinel(run_root, run_id, snapshot_hash)
    return api_root, run_root, workspace, snapshot_hash


def test_valid_mapping_returns_contained_host_path(tmp_path):
    api_root, _run_root, workspace, snapshot_hash = _prepare(tmp_path)
    result = resolve_workspace_host_path(
        workspace, run_id="run1", snapshot_hash=snapshot_hash,
        settings=_settings(api_root),
    )
    assert result == "/srv/gap/code-agent/runs/run1/workspace"


def test_missing_roots_fail_closed(tmp_path):
    api_root, _run_root, workspace, snapshot_hash = _prepare(tmp_path)
    with pytest.raises(WorkspaceMountError, match="workspace_mount_invalid"):
        resolve_workspace_host_path(
            workspace, run_id="run1", snapshot_hash=snapshot_hash,
            settings=SimpleNamespace(code_workspace_api_root="", code_workspace_host_root=""),
        )


def test_workspace_outside_api_root_is_rejected(tmp_path):
    api_root, _run_root, _workspace, snapshot_hash = _prepare(tmp_path)
    outside = tmp_path / "outside" / "workspace"
    outside.mkdir(parents=True)
    with pytest.raises(WorkspaceMountError, match="workspace_mount_invalid"):
        resolve_workspace_host_path(
            outside, run_id="run1", snapshot_hash=snapshot_hash,
            settings=_settings(api_root),
        )


def test_cross_run_source_is_rejected(tmp_path):
    api_root, _run_root, workspace, snapshot_hash = _prepare(tmp_path, run_id="run2")
    with pytest.raises(WorkspaceMountError, match="workspace_mount_invalid"):
        resolve_workspace_host_path(
            workspace, run_id="run1", snapshot_hash=snapshot_hash,
            settings=_settings(api_root),
        )


def test_path_escape_via_parent_is_rejected(tmp_path):
    api_root, _run_root, _workspace, snapshot_hash = _prepare(tmp_path)
    outside = tmp_path / "evil" / "workspace"
    outside.mkdir(parents=True)
    escape = api_root / "run1" / ".." / ".." / "evil" / "workspace"
    with pytest.raises(WorkspaceMountError, match="workspace_mount_invalid"):
        resolve_workspace_host_path(
            escape, run_id="run1", snapshot_hash=snapshot_hash,
            settings=_settings(api_root),
        )


def test_symlink_escape_is_rejected(tmp_path):
    api_root = tmp_path / "api-runs"
    run_root = api_root / "run1"
    run_root.mkdir(parents=True)
    snapshot_hash = "a" * 64
    write_workspace_sentinel(run_root, "run1", snapshot_hash)
    outside = tmp_path / "outside"
    outside.mkdir()
    link = run_root / "workspace"
    link.symlink_to(outside, target_is_directory=True)
    with pytest.raises(WorkspaceMountError, match="workspace_mount_invalid"):
        resolve_workspace_host_path(
            link, run_id="run1", snapshot_hash=snapshot_hash,
            settings=_settings(api_root),
        )


def test_missing_sentinel_is_rejected(tmp_path):
    api_root, _run_root, workspace, snapshot_hash = _prepare(tmp_path, write=False)
    with pytest.raises(WorkspaceMountError, match="workspace_mount_invalid"):
        resolve_workspace_host_path(
            workspace, run_id="run1", snapshot_hash=snapshot_hash,
            settings=_settings(api_root),
        )


def test_wrong_sentinel_value_is_rejected(tmp_path):
    api_root, run_root, workspace, snapshot_hash = _prepare(tmp_path)
    # Overwrite with a sentinel derived from a different snapshot (guessed identity).
    write_workspace_sentinel(run_root, "run1", "b" * 64)
    with pytest.raises(WorkspaceMountError, match="workspace_mount_invalid"):
        resolve_workspace_host_path(
            workspace, run_id="run1", snapshot_hash=snapshot_hash,
            settings=_settings(api_root),
        )


def test_host_root_with_traversal_is_rejected(tmp_path):
    api_root, _run_root, workspace, snapshot_hash = _prepare(tmp_path)
    with pytest.raises(WorkspaceMountError, match="workspace_mount_invalid"):
        resolve_workspace_host_path(
            workspace, run_id="run1", snapshot_hash=snapshot_hash,
            settings=_settings(api_root, host_root="/srv/../outside"),
        )


def test_sentinel_is_deterministic_and_binds_identity():
    value = workspace_sentinel("run1", "a" * 64)
    assert value == workspace_sentinel("run1", "a" * 64)
    assert value != workspace_sentinel("run1", "b" * 64)
    assert value != workspace_sentinel("run2", "a" * 64)
    assert len(value) == 64
