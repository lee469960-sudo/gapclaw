"""Read-only, run-bound CodeAgent workspace previews."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath
import subprocess

from app.services.code_agent.output_security import redact_code_output


MAX_FILE_BYTES = 256 * 1024
MAX_ENTRIES = 500


class WorkspacePreviewError(ValueError):
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


def _safe_target(root: Path, relative: str) -> tuple[str, Path]:
    rel = PurePosixPath(str(relative or "").replace("\\", "/"))
    if rel.is_absolute() or any(part in ("", ".", "..") for part in rel.parts):
        raise WorkspacePreviewError("workspace_path_not_allowed")
    if any(part in {".git", ".claude"} for part in rel.parts):
        raise WorkspacePreviewError("workspace_path_not_allowed")
    target = (root / rel.as_posix()).resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise WorkspacePreviewError("workspace_path_not_allowed") from exc
    return rel.as_posix(), target


def list_workspace(root: Path, *, relative: str = "", depth: int = 2, limit: int = MAX_ENTRIES) -> dict:
    root = root.resolve(strict=True)
    rel, target = _safe_target(root, relative) if relative else ("", root)
    if not target.is_dir():
        raise WorkspacePreviewError("workspace_file_not_found")
    depth = max(0, min(int(depth), 5))
    limit = max(1, min(int(limit), MAX_ENTRIES))
    entries: list[dict] = []
    for path in sorted(target.iterdir(), key=lambda item: (not item.is_dir(), item.name.lower())):
        if path.name in {".git", ".claude"} or path.name.startswith("."):
            continue
        child_rel = path.relative_to(root).as_posix()
        item = {"path": child_rel, "name": path.name, "type": "directory" if path.is_dir() else "file"}
        if path.is_file():
            item["size"] = path.stat().st_size
        elif depth > 0:
            item["children"] = list_workspace(root, relative=child_rel, depth=depth - 1, limit=limit)["entries"]
        entries.append(item)
        if len(entries) >= limit:
            break
    return {"path": rel, "entries": entries, "truncated": len(entries) >= limit}


def read_workspace_file(root: Path, relative: str) -> dict:
    root = root.resolve(strict=True)
    rel, target = _safe_target(root, relative)
    if not target.is_file():
        raise WorkspacePreviewError("workspace_file_not_found")
    if target.stat().st_size > MAX_FILE_BYTES:
        raise WorkspacePreviewError("workspace_file_too_large")
    try:
        content = target.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError) as exc:
        raise WorkspacePreviewError("workspace_binary_or_unreadable") from exc
    return {"path": rel, "content": redact_code_output(content).text, "size": target.stat().st_size}


def git_workspace_status(root: Path) -> dict:
    root = root.resolve(strict=True)
    if not (root / ".git").is_dir():
        raise WorkspacePreviewError("workspace_mount_invalid")
    try:
        top_level = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--show-toplevel"],
            check=True, capture_output=True, text=True, timeout=10,
            env={"GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_TERMINAL_PROMPT": "0"},
        ).stdout.strip()
        if Path(top_level).resolve() != root:
            raise WorkspacePreviewError("workspace_mount_invalid")
        status = subprocess.run(
            ["git", "-C", str(root), "status", "--short"],
            check=True, capture_output=True, text=True, timeout=10,
            env={"GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_TERMINAL_PROMPT": "0"},
        ).stdout
        branch = subprocess.run(
            ["git", "-C", str(root), "branch", "--show-current"],
            check=True, capture_output=True, text=True, timeout=10,
            env={"GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_TERMINAL_PROMPT": "0"},
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError) as exc:
        raise WorkspacePreviewError("workspace_git_status_unavailable") from exc
    baseline_path = root.parent / "control" / "baseline.json"
    try:
        baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        baseline = None
    if isinstance(baseline, dict):
        current = _workspace_file_snapshot(root)
        changed = []
        for path in sorted(set(baseline) | set(current)):
            if baseline.get(path) == current.get(path):
                continue
            changed.append({
                "status": "??" if path not in baseline else ("D " if path not in current else " M"),
                "path": path,
            })
        return {"branch": branch, "changed_files": changed, "clean": not changed}
    changed = []
    for line in status.splitlines():
        if len(line) > 3:
            path = line[3:].strip()
            if path and not path.startswith((".git/", ".claude/")):
                changed.append({"status": line[:2], "path": path})
    return {"branch": branch, "changed_files": changed, "clean": not changed}


def _workspace_file_snapshot(root: Path) -> dict[str, str]:
    snapshot: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if relative == ".git" or relative.startswith((".git/", ".claude/")):
            continue
        if path.is_symlink():
            snapshot[relative] = "symlink:" + path.readlink().as_posix()
        elif path.is_file():
            digest = hashlib.sha256()
            with path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
            snapshot[relative] = "file:" + digest.hexdigest()
    return snapshot
