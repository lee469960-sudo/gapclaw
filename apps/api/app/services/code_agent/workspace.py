"""Managed, fixed-baseline source workspaces for CodeAgent runs."""

from __future__ import annotations

import io
import fnmatch
import hashlib
import json
import os
import re
import shlex
import stat
import subprocess
import tarfile
import shutil
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from app.config import get_settings
from app.services.code_agent.snapshot_store import SnapshotSealError, SourceSnapshotStore
from app.services.code_agent.workspace_mount import write_workspace_sentinel


_COMMIT_SHA = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")


class WorkspacePreparationError(RuntimeError):
    pass


class WorkspaceGitSyncError(RuntimeError):
    def __init__(self, reason: str, *, detail: str = ""):
        self.reason = reason
        self.detail = detail
        super().__init__(reason)


@dataclass(frozen=True)
class WorkspaceFacts:
    path: str
    repository: str
    requested_commit: str
    resolved_commit: str
    snapshot_id: str = ""
    snapshot_hash: str = ""
    baseline_digest: str = ""
    preparation_mode: str = "git_archive"
    repo_root_mode: str = "preserved"
    repo_root_ready: bool = True
    repo_root_reason: str = "ready"
    hooks_executed: bool = False
    submodules_initialized: bool = False

    def to_dict(self) -> dict:
        return {
            "path": self.path,
            "repository": self.repository,
            "requested_commit": self.requested_commit,
            "resolved_commit": self.resolved_commit,
            "snapshot_id": self.snapshot_id,
            "snapshot_hash": self.snapshot_hash,
            "baseline_digest": self.baseline_digest,
            "preparation_mode": self.preparation_mode,
            "repo_root_mode": self.repo_root_mode,
            "repo_root_ready": self.repo_root_ready,
            "repo_root_reason": self.repo_root_reason,
            "hooks_executed": self.hooks_executed,
            "submodules_initialized": self.submodules_initialized,
        }


class WorkspaceManager:
    def __init__(self, root: Path | None = None, retention_hours: int | None = None):
        settings = get_settings()
        if root is not None:
            self.root = Path(root).resolve()
        else:
            configured_api_root = str(
                getattr(settings, "code_workspace_api_root", "") or ""
            ).strip()
            if configured_api_root:
                self.root = Path(configured_api_root).resolve()
            else:
                self.root = (
                    Path(settings.data_dir) / "code-agent" / "runs"
                ).resolve()
        configured = (
            settings.code_workspace_retention_hours
            if retention_hours is None
            else retention_hours
        )
        self.retention_hours = max(0, int(configured))
        self._allocated_runs: set[str] = set()

    @staticmethod
    def _git_env() -> dict[str, str]:
        env = dict(os.environ)
        env.update({
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_TERMINAL_PROMPT": "0",
        })
        return env

    @classmethod
    def _git(cls, args: list[str], *, binary: bool = False) -> bytes | str:
        try:
            result = subprocess.run(
                ["git", "-c", "core.hooksPath=/dev/null", *args],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=cls._git_env(),
                text=not binary,
                timeout=300,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise WorkspacePreparationError("workspace_source_unavailable") from exc
        return result.stdout

    def prepare(
        self,
        run,
        *,
        snapshot_store: SourceSnapshotStore | None = None,
        sandbox_id: str = "",
    ) -> WorkspaceFacts:
        """Materialize or reuse the project's persistent writable Workspace.

        For CodeAgent runs bound to an existing Sandbox, the Workspace lives under
        that Sandbox's `/workplace/code/<project_id>/workspace` directory so the
        human terminal, Claude Code and the left preview panel see the same files.
        In persistent mode this only prepares the shared directory; repository
        clone/fetch happens inside the bound Sandbox after runner attachment.
        """
        persistent = bool(str(sandbox_id or "").strip())
        if persistent:
            from app.services.workplace import ensure_workplace

            project_key = str(getattr(run, "project_id", "") or "").strip() or run.id
            run_root = (ensure_workplace(sandbox_id) / "code" / project_key).resolve()
            workspace = run_root / "workspace"
            needs_materialize = not workspace.exists() or not any(workspace.iterdir())
            if workspace.exists() and not workspace.is_dir():
                raise WorkspacePreparationError("workspace_mount_invalid")
        else:
            run_root = (self.root / run.id).resolve()
            if run_root.parent != self.root or run_root.exists():
                raise WorkspacePreparationError("workspace_already_exists")
            workspace = run_root / "workspace"
            needs_materialize = True

        run_root.mkdir(parents=True, exist_ok=persistent)
        self._allocated_runs.add(run.id)
        created_persistent_workspace = False
        try:
            repo_root_mode = "persistent_empty" if persistent else "snapshot_materialized"
            if persistent:
                workspace.mkdir(parents=True, exist_ok=True)
                repo_root_mode = "persistent_reused" if (workspace / ".git").is_dir() else "persistent_empty"
            else:
                store = snapshot_store or _default_snapshot_store()
                snapshot_path, resolved_commit = self._verify_snapshot(run, store)
                if persistent:
                    if workspace.exists():
                        shutil.rmtree(workspace)
                    _remove_path_for_retention(run_root / "source.git")
                    created_persistent_workspace = True
                repo_root_mode = self._materialize(snapshot_path, run_root, run=run)
            if not persistent and needs_materialize is False and not (workspace / ".git").is_dir():
                raise WorkspacePreparationError("workspace_git_metadata_missing")
            if _run_uses_claude_code(run):
                if not persistent:
                    _verify_claude_workspace_git_root(workspace)
            control = run_root / "control"
            control.mkdir(exist_ok=True)
            baseline = _workspace_snapshot(workspace)
            baseline_json = json.dumps(baseline, sort_keys=True, separators=(",", ":"))
            (control / "baseline.json").write_text(baseline_json, encoding="utf-8")
            (control / "authorized_writes.json").write_text("[]", encoding="utf-8")
            write_workspace_sentinel(
                run_root, run.id, str(getattr(run, "snapshot_hash", "") or "")
            )
        except Exception:
            self._allocated_runs.discard(run.id)
            if not persistent:
                shutil.rmtree(run_root, ignore_errors=True)
            elif created_persistent_workspace:
                shutil.rmtree(workspace, ignore_errors=True)
                _remove_path_for_retention(run_root / "source.git")
            raise
        facts = WorkspaceFacts(
            path=str(workspace),
            repository=run.repository,
            requested_commit=run.base_commit,
            resolved_commit=str(getattr(run, "resolved_commit", "") or run.base_commit or ""),
            snapshot_id=str(getattr(run, "snapshot_id", "") or ""),
            snapshot_hash=str(getattr(run, "snapshot_hash", "") or ""),
            baseline_digest=hashlib.sha256(baseline_json.encode()).hexdigest(),
            preparation_mode="persistent_git_sync" if persistent else "snapshot_materialize",
            repo_root_mode=repo_root_mode,
            repo_root_ready=True,
            repo_root_reason="ready",
        )
        run.workspace_path = facts.path
        run.source_facts = json.dumps(facts.to_dict(), sort_keys=True)
        if persistent:
            source_facts = json.loads(run.source_facts)
            source_facts["workspace_mode"] = "persistent_sandbox"
            source_facts["sandbox_id"] = sandbox_id
            run.source_facts = json.dumps(source_facts, sort_keys=True)
        run.workspace_state = "prepared"
        run.workspace_downloadable = False
        return facts

    @staticmethod
    def _verify_snapshot(run, store: SourceSnapshotStore) -> tuple[Path, str]:
        """Validate run identity + sealed snapshot hash/commit/metadata (no I/O beyond the store)."""
        snapshot_hash = str(getattr(run, "snapshot_hash", "") or "").strip()
        snapshot_id = str(getattr(run, "snapshot_id", "") or "").strip()
        resolved_commit = str(getattr(run, "resolved_commit", "") or "").strip().lower()
        if not snapshot_hash or not _COMMIT_SHA.fullmatch(resolved_commit):
            raise WorkspacePreparationError("workspace_snapshot_identity_invalid")
        if snapshot_id and snapshot_id != snapshot_hash[:16]:
            raise WorkspacePreparationError("workspace_snapshot_identity_invalid")
        snapshot_path = store.root / snapshot_hash
        try:
            store.verify(
                snapshot_path,
                expected_hash=snapshot_hash,
                expected_commit=resolved_commit,
            )
        except SnapshotSealError as exc:
            raise WorkspacePreparationError("workspace_snapshot_invalid") from exc
        _verify_sanitized_git_metadata(snapshot_path)
        return snapshot_path, resolved_commit

    @staticmethod
    def _materialize(snapshot_path: Path, run_root: Path, *, run=None) -> str:
        """Copy the sealed snapshot as a normal worktree with sanitized ``.git``."""
        workspace = run_root / "workspace"
        workspace.mkdir()
        source_root = snapshot_path
        repo_root_mode = "preserved"
        if run is not None and _run_uses_claude_code(run):
            business_entries = _snapshot_business_entries(snapshot_path)
            if (
                len(business_entries) == 1
                and business_entries[0].is_dir()
                and not business_entries[0].is_symlink()
            ):
                source_root = business_entries[0]
                repo_root_mode = "single_top_level_flattened"
        if repo_root_mode == "single_top_level_flattened":
            shutil.copytree(snapshot_path / ".git", workspace / ".git", symlinks=True)
        for entry in sorted(source_root.iterdir()):
            if repo_root_mode == "single_top_level_flattened" and entry.name == ".git":
                continue
            if entry.is_dir() and not entry.is_symlink():
                shutil.copytree(entry, workspace / entry.name, symlinks=True)
            else:
                shutil.copy2(entry, workspace / entry.name, follow_symlinks=False)
        os.symlink("workspace/.git", run_root / "source.git")
        _make_writable(workspace)
        return repo_root_mode

    def cleanup_allocated_workspace(self, run) -> None:
        """Compensate from the first allocated directory, including partial prepare."""
        if run.id not in self._allocated_runs:
            return
        if _source_facts(run).get("workspace_mode") == "persistent_sandbox":
            self._allocated_runs.discard(run.id)
            return
        resolved_root = (self.root / run.id).resolve()
        if resolved_root.parent != self.root:
            raise WorkspacePreparationError("workspace_cleanup_target_invalid")
        try:
            if not resolved_root.exists():
                return
            # A sealed artifact still needs a read-only source Workspace for
            # the run-bound preview UI. Retain it under the existing TTL;
            # purge_expired remains responsible for eventual deletion.
            if getattr(run, "workspace_state", "") in {"prepared", "sealed"}:
                self.retain_after_run(run)
                return
            if getattr(run, "workspace_state", "") == "retained_read_only":
                return
            shutil.rmtree(resolved_root)
        finally:
            self._allocated_runs.discard(run.id)

    def retain_after_run(self, run) -> None:
        if _source_facts(run).get("workspace_mode") == "persistent_sandbox":
            run.workspace_state = "prepared"
            run.retained_until = ""
            run.workspace_downloadable = False
            return
        workspace = Path(run.workspace_path).resolve()
        run_root = workspace.parent
        if run_root.parent != self.root or not workspace.is_dir():
            raise WorkspacePreparationError("workspace_cleanup_target_invalid")
        retention_hours = getattr(run, "workspace_retention_hours", None)
        if retention_hours is None:
            retention_hours = self.retention_hours
        retention_hours = min(self.retention_hours, max(0, int(retention_hours)))
        if retention_hours == 0:
            self._delete_run_root(run, run_root)
            return
        _remove_compat_git_metadata(run_root)
        for path in sorted(run_root.rglob("*"), reverse=True):
            try:
                if path.is_symlink():
                    continue
                mode = path.stat(follow_symlinks=False).st_mode
                path.chmod(mode & ~0o222, follow_symlinks=False)
            except (OSError, NotImplementedError):
                if not path.is_symlink():
                    raise
        run_root.chmod(run_root.stat().st_mode & ~0o222)
        run.workspace_state = "retained_read_only"
        run.retained_until = (
            datetime.now() + timedelta(hours=retention_hours)
        ).strftime("%Y-%m-%d %H:%M:%S")
        run.workspace_downloadable = False

    def reset_to_frozen_baseline(self, run) -> None:
        """Restore the prepared tree after pre-execution baseline validation."""
        if _source_facts(run).get("workspace_mode") == "persistent_sandbox":
            workspace = Path(run.workspace_path).resolve()
            baseline = _workspace_snapshot(workspace)
            baseline_json = json.dumps(baseline, sort_keys=True, separators=(",", ":"))
            (workspace.parent / "control" / "baseline.json").write_text(
                baseline_json, encoding="utf-8"
            )
            facts = _source_facts(run)
            facts["baseline_digest"] = hashlib.sha256(baseline_json.encode()).hexdigest()
            facts["path"] = run.workspace_path
            run.source_facts = json.dumps(facts, sort_keys=True)
            return
        workspace = Path(run.workspace_path).resolve()
        run_root = workspace.parent
        mirror = run_root / "source.git"
        if run_root.parent != self.root or not workspace.is_dir() or not mirror.is_dir():
            raise WorkspacePreparationError("workspace_reset_target_invalid")
        for child in workspace.iterdir():
            if child.name == ".git":
                continue
            if child.is_dir() and not child.is_symlink():
                shutil.rmtree(child)
            else:
                child.unlink()
        archive = self._git([
            "--git-dir", str(mirror), "archive", "--format=tar", run.base_commit,
        ], binary=True)
        with tarfile.open(fileobj=io.BytesIO(archive), mode="r:") as bundle:
            bundle.extractall(workspace, filter="data")
        flatten_archived_base_if_needed(run, workspace)
        (run_root / "control" / "authorized_writes.json").write_text("[]", encoding="utf-8")
        WorkspaceIntegrityGuard(run).check_before_seal()

    def downloadable_workspace(self, run) -> Path | None:
        if not run.workspace_downloadable or run.workspace_state != "sealed":
            return None
        path = Path(run.workspace_path).resolve()
        return path if path.is_dir() else None

    def purge_expired(self, run, *, now: datetime | None = None) -> bool:
        if run.workspace_state != "retained_read_only" or not run.retained_until:
            return False
        expires = datetime.strptime(run.retained_until, "%Y-%m-%d %H:%M:%S")
        if expires > (now or datetime.now()):
            return False
        workspace = Path(run.workspace_path).resolve()
        run_root = workspace.parent
        if run_root.parent != self.root:
            raise WorkspacePreparationError("workspace_cleanup_target_invalid")
        self._delete_run_root(run, run_root)
        return True

    def _delete_run_root(self, run, run_root: Path) -> None:
        for path in [run_root, *run_root.rglob("*")]:
            if path.is_symlink():
                continue
            mode = path.stat().st_mode
            path.chmod(mode | 0o700 if path.is_dir() else mode | 0o600)
        shutil.rmtree(run_root)
        run.workspace_state = "deleted"
        run.workspace_path = ""
        run.retained_until = ""
        run.workspace_downloadable = False


def effective_workspace_retention_hours(project, settings=None) -> int:
    """Freeze the platform limit and an optional shorter Project override."""
    platform_hours = max(
        0,
        int(getattr(
            settings or get_settings(),
            "code_workspace_retention_hours",
            168,
        )),
    )
    project_hours = getattr(project, "workspace_retention_hours", None)
    if project_hours is None:
        return platform_hours
    return min(platform_hours, max(0, int(project_hours)))


def _default_snapshot_store() -> SourceSnapshotStore:
    """Build the deployment snapshot store at the same root the publish flow uses."""
    settings = get_settings()
    snapshot_root = Path(settings.data_dir) / "code-agent-source" / "snapshots"
    snapshot_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    resolved = snapshot_root.resolve(strict=True)
    if snapshot_root.is_symlink() or not resolved.is_dir():
        raise WorkspacePreparationError("workspace_snapshot_store_invalid")
    resolved.chmod(0o700)
    return SourceSnapshotStore(resolved)


_GIT_SYNC_AUTH_RE = re.compile(
    r"authentication failed|could not read username|could not read password|"
    r"terminal prompts disabled|401|403|access denied|permission denied",
    re.IGNORECASE,
)
_GIT_SYNC_REF_RE = re.compile(
    r"couldn't find remote ref|could not find remote ref|not our ref|"
    r"invalid refspec|unknown revision|ambiguous argument",
    re.IGNORECASE,
)
_GIT_SYNC_NETWORK_RE = re.compile(
    r"could not resolve host|failed to connect|connection refused|"
    r"network is unreachable|operation timed out|connection timed out|"
    r"repository not found|could not read from remote repository",
    re.IGNORECASE,
)
_COMMIT_OUTPUT_RE = re.compile(r"^[0-9a-f]{40,64}$")


def _classify_git_sync_failure(output: str) -> str:
    text = str(output or "")
    if _GIT_SYNC_AUTH_RE.search(text):
        return "repository_auth_failed"
    if _GIT_SYNC_REF_RE.search(text):
        return "repository_ref_invalid"
    if _GIT_SYNC_NETWORK_RE.search(text):
        return "repository_unreachable"
    return "repository_sync_failed"


def _workspace_mount_from_facts(runner_facts) -> str:
    value = getattr(runner_facts, "workspace_mount", "")
    if value:
        return str(value)
    if isinstance(runner_facts, dict):
        return str(runner_facts.get("workspace_mount") or "")
    return ""


def _credential_for_git_sync(db, run) -> tuple[str, str]:
    from app.models import CodeDeployCredential, CodeProject, CodeProjectManifest
    from app.services.code_agent.secret_store import (
        DeployTokenSecretStore,
        repository_importer_identity,
    )

    manifest = db.get(CodeProjectManifest, str(getattr(run, "manifest_id", "") or ""))
    credential_ref = str(getattr(manifest, "credential_ref", "") or "")
    if not credential_ref:
        return "", ""
    project = db.get(CodeProject, str(getattr(run, "project_id", "") or ""))
    if project is None:
        raise WorkspaceGitSyncError("repository_auth_failed")
    row = db.get(CodeDeployCredential, credential_ref)
    username = str(getattr(row, "auth_username", "") or "") if row else ""
    try:
        with DeployTokenSecretStore(db).resolve_for_importer(
            identity=repository_importer_identity(),
            reference_id=credential_ref,
            organization_id=str(project.organization_id or ""),
            project_id=str(project.id or ""),
        ) as lease:
            password = lease.read().decode("utf-8", errors="strict")
    except Exception as exc:
        raise WorkspaceGitSyncError("repository_auth_failed") from exc
    return username, password


def _refresh_persistent_workspace_baseline(run, *, resolved_commit: str) -> None:
    workspace = Path(run.workspace_path).resolve()
    run_root = workspace.parent
    control = run_root / "control"
    control.mkdir(exist_ok=True)
    baseline = _workspace_snapshot(workspace)
    baseline_json = json.dumps(baseline, sort_keys=True, separators=(",", ":"))
    (control / "baseline.json").write_text(baseline_json, encoding="utf-8")
    (control / "authorized_writes.json").write_text("[]", encoding="utf-8")
    try:
        facts = json.loads(run.source_facts or "{}")
    except (TypeError, json.JSONDecodeError):
        facts = {}
    if not isinstance(facts, dict):
        facts = {}
    facts.update({
        "path": str(workspace),
        "repository": str(getattr(run, "repository", "") or ""),
        "requested_commit": str(getattr(run, "requested_ref", "") or ""),
        "resolved_commit": resolved_commit,
        "snapshot_id": str(getattr(run, "snapshot_id", "") or ""),
        "snapshot_hash": str(getattr(run, "snapshot_hash", "") or ""),
        "baseline_digest": hashlib.sha256(baseline_json.encode()).hexdigest(),
        "preparation_mode": "persistent_git_sync",
        "repo_root_mode": "sandbox_git_synced",
        "repo_root_ready": True,
        "repo_root_reason": "ready",
        "workspace_mode": "persistent_sandbox",
    })
    run.source_facts = json.dumps(facts, sort_keys=True)


def sync_repository_in_sandbox(db, run, runner, runner_facts) -> dict[str, str]:
    """Clone/fetch the Manifest repository inside the bound persistent Sandbox."""
    repository = str(getattr(run, "repository", "") or "").strip()
    requested_ref = str(getattr(run, "requested_ref", "") or "").strip() or "HEAD"
    container_id = str(getattr(run, "container_id", "") or "")
    workspace_mount = _workspace_mount_from_facts(runner_facts)
    if not repository or not requested_ref:
        raise WorkspaceGitSyncError("repository_ref_invalid")
    if not container_id or not workspace_mount.startswith("/workplace/"):
        raise WorkspaceGitSyncError("workspace_mount_invalid")

    git_code, git_output = runner.exec(
        container_id,
        "git --version",
        timeout_seconds=30,
    )
    if git_code != 0:
        raise WorkspaceGitSyncError("repository_feature_unsupported", detail=git_output)

    username, password = _credential_for_git_sync(db, run)
    base_environment = {"GIT_TERMINAL_PROMPT": "0"}
    credential_environment = {
        **base_environment,
        "GIT_ASKPASS": f"/tmp/code-agent-askpass-{run.id}",
        "GIT_USERNAME": username,
        "GIT_PASSWORD": password,
    }
    askpass = shlex.quote(credential_environment["GIT_ASKPASS"])
    if username or password:
        setup_askpass = (
            f"umask 077 && printf '%s\\n' "
            f"'#!/bin/sh' "
            f"'case \"$1\" in' "
            f"'*Username*) printf \"%s\\\\n\" \"$GIT_USERNAME\" ;;' "
            f"'*Password*) printf \"%s\\\\n\" \"$GIT_PASSWORD\" ;;' "
            f"'*) printf \"\\\\n\" ;;' "
            f"'esac' > {askpass} && chmod 700 {askpass}"
        )
        code, output = runner.exec(
            container_id,
            setup_askpass,
            timeout_seconds=30,
            environment=credential_environment,
        )
        if code != 0:
            raise WorkspaceGitSyncError("repository_auth_failed", detail=output)

    workspace_arg = shlex.quote(workspace_mount)
    repo_arg = shlex.quote(repository)
    ref_arg = shlex.quote(requested_ref)
    sync_command = (
        "set -eu\n"
        f"cd {workspace_arg}\n"
        "if [ -d .git ]; then\n"
        f"  git remote set-url origin {repo_arg} 2>/dev/null || git remote add origin {repo_arg}\n"
        "else\n"
        "  if [ -n \"$(find . -mindepth 1 -maxdepth 1 -not -name .claude -print -quit)\" ]; then\n"
        "    echo WORKSPACE_NOT_EMPTY_WITHOUT_GIT >&2\n"
        "    exit 43\n"
        "  fi\n"
        f"  git clone --no-checkout {repo_arg} .\n"
        "fi\n"
        f"git fetch --tags --prune origin {ref_arg}\n"
        "git checkout -f --detach FETCH_HEAD\n"
        "git rev-parse HEAD\n"
    )
    environment = credential_environment if (username or password) else base_environment
    try:
        code, output = runner.exec(
            container_id,
            sync_command,
            timeout_seconds=300,
            environment=environment,
        )
    finally:
        if username or password:
            try:
                runner.exec(container_id, f"rm -f {askpass}", timeout_seconds=10)
            except Exception:
                pass
    if code != 0:
        reason = (
            "workspace_git_metadata_missing"
            if "WORKSPACE_NOT_EMPTY_WITHOUT_GIT" in str(output or "")
            else _classify_git_sync_failure(output)
        )
        raise WorkspaceGitSyncError(reason, detail=output)
    commit = ""
    for line in reversed(str(output or "").splitlines()):
        candidate = line.strip().lower()
        if _COMMIT_OUTPUT_RE.fullmatch(candidate):
            commit = candidate
            break
    if not commit:
        code, rev_output = runner.exec(
            container_id,
            f"cd {workspace_arg} && git rev-parse HEAD",
            timeout_seconds=30,
            environment=base_environment,
        )
        if code == 0:
            for line in reversed(str(rev_output or "").splitlines()):
                candidate = line.strip().lower()
                if _COMMIT_OUTPUT_RE.fullmatch(candidate):
                    commit = candidate
                    break
    if not commit:
        raise WorkspaceGitSyncError("repository_sync_failed", detail=output)
    run.resolved_commit = commit
    run.base_commit = commit
    run.snapshot_id = ""
    run.snapshot_hash = ""
    run.source_scan_report_id = ""
    _refresh_persistent_workspace_baseline(run, resolved_commit=commit)
    try:
        contract = json.loads(run.task_contract or "{}")
    except (TypeError, json.JSONDecodeError):
        contract = {}
    if isinstance(contract, dict):
        contract["resolved_commit"] = commit
        contract["base_commit"] = commit
        contract["snapshot_id"] = ""
        contract["snapshot_hash"] = ""
        contract["source_scan_report_id"] = ""
        run.task_contract = json.dumps(contract, sort_keys=True)
    return {
        "repository": repository,
        "requested_ref": requested_ref,
        "resolved_commit": commit,
        "workspace_mount": workspace_mount,
    }


def _verify_sanitized_git_metadata(snapshot_path: Path) -> None:
    """Fail closed unless the snapshot's ``.git`` carries only sanitized local metadata.

    The exact-commit and hash binding is enforced by ``SourceSnapshotStore.verify``;
    this check additionally proves the snapshot has no object alternates, no hooks
    and no configured remotes (so no later read can reach another object store or
    a network remote).
    """
    git_directory = snapshot_path / ".git"
    if not git_directory.is_dir() or git_directory.is_symlink():
        raise WorkspacePreparationError("workspace_snapshot_invalid")
    if (git_directory / "objects" / "info" / "alternates").exists():
        raise WorkspacePreparationError("workspace_snapshot_invalid")
    hooks = git_directory / "hooks"
    if hooks.is_dir() and any(hooks.iterdir()):
        raise WorkspacePreparationError("workspace_snapshot_invalid")
    try:
        config = (git_directory / "config").read_text(encoding="utf-8")
    except OSError as exc:
        raise WorkspacePreparationError("workspace_snapshot_invalid") from exc
    for line in config.splitlines():
        if line.strip().startswith("[remote"):
            raise WorkspacePreparationError("workspace_snapshot_invalid")


def _read_git_ref(git_dir: Path, refname: str) -> str | None:
    """Resolve a ref from a bare-ish ``.git`` directory, tolerating packed refs.

    ``git gc`` may pack ``refs/heads/snapshot`` into ``packed-refs``, so a loose
    file lookup is insufficient. Returns the lowercase SHA-1/256 or ``None``.
    """
    loose = git_dir / refname
    try:
        if loose.is_file() and not loose.is_symlink():
            return loose.read_text(encoding="utf-8").strip().lower()
    except OSError:
        return None
    packed = git_dir / "packed-refs"
    try:
        if not packed.is_file():
            return None
        for line in packed.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if " " in line:
                sha, ref = line.split(" ", 1)
                if ref == refname:
                    return sha.lower()
    except OSError:
        return None
    return None


def _make_writable(root: Path) -> None:
    for path in [root, *root.rglob("*")]:
        if path.is_symlink():
            continue
        mode = path.stat(follow_symlinks=False).st_mode
        path.chmod(mode | (0o200 if path.is_file() else 0o300))


def _remove_path_for_retention(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink(missing_ok=True)
    elif path.is_dir():
        shutil.rmtree(path, ignore_errors=True)


def _remove_compat_git_metadata(run_root: Path) -> None:
    _remove_path_for_retention(run_root / "source.git")
    _remove_path_for_retention(run_root / "workspace" / ".git")


def _run_uses_claude_code(run) -> bool:
    for attr in ("task_contract", "effective_policy"):
        try:
            payload = json.loads(getattr(run, attr, "") or "{}")
        except (TypeError, json.JSONDecodeError):
            payload = {}
        if isinstance(payload, dict) and payload.get("coding_runtime") == "claude_code":
            return True
    return False


def _snapshot_business_entries(snapshot_path: Path) -> list[Path]:
    return [
        entry
        for entry in sorted(snapshot_path.iterdir())
        if entry.name not in {".git", ".claude"}
    ]


def _verify_claude_workspace_git_root(workspace: Path) -> None:
    git_dir = workspace / ".git"
    if not git_dir.is_dir() or git_dir.is_symlink():
        raise WorkspacePreparationError("workspace_git_metadata_missing")
    try:
        top_level = subprocess.run(
            ["git", "-C", str(workspace), "rev-parse", "--show-toplevel"],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=WorkspaceManager._git_env(),
            timeout=30,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError) as exc:
        raise WorkspacePreparationError("workspace_repo_root_mismatch") from exc
    try:
        resolved_top_level = Path(top_level).resolve(strict=True)
    except OSError as exc:
        raise WorkspacePreparationError("workspace_repo_root_mismatch") from exc
    if resolved_top_level != workspace.resolve():
        raise WorkspacePreparationError("workspace_repo_root_mismatch")
    try:
        subprocess.run(
            ["git", "-C", str(workspace), "status", "--short"],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=WorkspaceManager._git_env(),
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise WorkspacePreparationError("workspace_git_status_unavailable") from exc


def _source_facts(run) -> dict:
    try:
        facts = json.loads(getattr(run, "source_facts", "") or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}
    return facts if isinstance(facts, dict) else {}


def flatten_archived_base_if_needed(run, root: Path) -> None:
    if _source_facts(run).get("repo_root_mode") != "single_top_level_flattened":
        return
    entries = [
        entry
        for entry in sorted(root.iterdir())
        if entry.name not in {".git", ".claude"}
    ]
    if len(entries) != 1 or not entries[0].is_dir() or entries[0].is_symlink():
        raise WorkspacePreparationError("workspace_repo_root_unproven")
    source = entries[0]
    temporary = root.parent / f".{root.name}-flatten"
    if temporary.exists():
        shutil.rmtree(temporary)
    temporary.mkdir()
    for entry in sorted(source.iterdir()):
        shutil.move(str(entry), str(temporary / entry.name))
    shutil.rmtree(source)
    for entry in sorted(temporary.iterdir()):
        shutil.move(str(entry), str(root / entry.name))
    temporary.rmdir()


class WorkspaceIntegrityError(RuntimeError):
    def __init__(self, reason: str = "workspace_integrity_error"):
        self.reason = reason
        super().__init__(reason)


def is_runtime_only_path(path: str) -> bool:
    normalized = str(path or "").replace("\\", "/").strip("/")
    return (
        normalized == ".claude"
        or normalized.startswith(".claude/")
        or normalized == ".git"
        or normalized.startswith(".git/")
    )


def _workspace_snapshot(root: Path) -> dict[str, str]:
    snapshot: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root).as_posix()
        if is_runtime_only_path(rel):
            continue
        if path.is_symlink():
            snapshot[rel] = "symlink:" + os.readlink(path)
        elif path.is_file():
            digest = hashlib.sha256()
            with path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
            snapshot[rel] = "file:" + digest.hexdigest()
        elif not path.is_dir():
            snapshot[rel] = f"unsupported:{stat.S_IFMT(path.lstat().st_mode):o}"
    return snapshot


def workspace_changed_paths(run) -> tuple[str, ...]:
    workspace = Path(run.workspace_path).resolve()
    baseline = json.loads(
        (workspace.parent / "control" / "baseline.json").read_text(encoding="utf-8")
    )
    current = _workspace_snapshot(workspace)
    return tuple(sorted(
        path for path in set(baseline) | set(current)
        if baseline.get(path) != current.get(path)
    ))


class WorkspaceIntegrityGuard:
    def __init__(self, run, db=None):
        self.run = run
        self.db = db
        self.workspace = Path(run.workspace_path).resolve()
        self.control = self.workspace.parent / "control"

    def _fail(self, reason: str = "workspace_integrity_error") -> None:
        self.run.status = "workspace_integrity_error"
        self.run.failure_reason = reason
        if self.db is not None:
            self.db.commit()
        raise WorkspaceIntegrityError(reason)

    def _authorized(self) -> set[str]:
        try:
            value = json.loads((self.control / "authorized_writes.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            self._fail("workspace_integrity_metadata_error")
        return {str(item) for item in value if isinstance(item, str)}

    def _validate(self, *, check_external: bool = True) -> None:
        try:
            facts = json.loads(self.run.source_facts or "{}")
            baseline_text = (self.control / "baseline.json").read_text(encoding="utf-8")
            baseline = json.loads(baseline_text)
        except (OSError, json.JSONDecodeError):
            self._fail("workspace_integrity_metadata_error")
        digest = hashlib.sha256(baseline_text.encode()).hexdigest()
        if (
            facts.get("baseline_digest") != digest
            or facts.get("resolved_commit") != self.run.base_commit
            or facts.get("repository") != self.run.repository
        ):
            self._fail("workspace_baseline_drift")
        if (
            facts.get("snapshot_hash") != str(getattr(self.run, "snapshot_hash", "") or "")
            or facts.get("snapshot_id") != str(getattr(self.run, "snapshot_id", "") or "")
        ):
            self._fail("workspace_snapshot_identity_invalid")
        if (
            str(facts.get("path") or "") != self.run.workspace_path
            or (
                facts.get("workspace_mode") != "persistent_sandbox"
                and self.workspace.parent.name != self.run.id
            )
        ):
            self._fail("workspace_cross_run_mount")
        self._check_uncontrolled_checkout()
        current = _workspace_snapshot(self.workspace)
        changed = {
            path for path in set(baseline) | set(current)
            if baseline.get(path) != current.get(path)
        }
        if check_external and not changed.issubset(self._authorized()):
            self._fail("workspace_external_write")

    def _check_uncontrolled_checkout(self) -> None:
        """Fail closed unless run-local Git metadata still pins the frozen commit.

        Claude Code needs a normal ``/workspace/.git``. ``source.git`` is kept as
        a compatibility pointer for platform paths. If HEAD or
        ``refs/heads/snapshot`` has been moved, the run must stop rather than
        report against a drifted base.
        """
        git_dir = self.workspace / ".git"
        mirror = self.workspace.parent / "source.git"
        if git_dir.exists():
            if not git_dir.is_dir() or git_dir.is_symlink():
                self._fail("workspace_integrity_metadata_error")
            if (mirror.exists() or mirror.is_symlink()) and mirror.resolve() != git_dir.resolve():
                self._fail("workspace_integrity_metadata_error")
            metadata = git_dir
        elif mirror.is_dir() and not mirror.is_symlink():
            # Compatibility for runs prepared before /workspace/.git became the
            # canonical Git metadata path.
            metadata = mirror
        else:
            self._fail("workspace_integrity_metadata_error")
        try:
            head = (metadata / "HEAD").read_text(encoding="utf-8").strip()
        except OSError:
            self._fail("workspace_integrity_metadata_error")
        expected = str(getattr(self.run, "resolved_commit", "") or self.run.base_commit or "").strip().lower()
        if str(getattr(self.run, "snapshot_hash", "") or "").strip():
            if head != "ref: refs/heads/snapshot":
                self._fail("workspace_uncontrolled_checkout")
            if _read_git_ref(metadata, "refs/heads/snapshot") != expected:
                self._fail("workspace_uncontrolled_checkout")
            return
        actual = ""
        if _COMMIT_SHA.fullmatch(head.lower()):
            actual = head.lower()
        elif head.startswith("ref: "):
            actual = _read_git_ref(metadata, head.removeprefix("ref: ").strip())
        if not expected or actual != expected:
            self._fail("workspace_uncontrolled_checkout")

    def check_before_write(self, relative_path: str) -> None:
        self._validate()
        target = (self.workspace / relative_path).resolve()
        try:
            rel = target.relative_to(self.workspace).as_posix()
        except ValueError:
            self._fail("workspace_path_escape")
        authorized = self._authorized()
        authorized.add(rel)
        (self.control / "authorized_writes.json").write_text(
            json.dumps(sorted(authorized)), encoding="utf-8"
        )

    def check_before_seal(self) -> None:
        self._validate()

    def authorize_runtime_changes(self) -> tuple[str, ...]:
        """Authorize changes made by the managed Claude Code runtime.

        Claude edits files directly inside the isolated runner Workspace rather
        than through ``CodeToolExecutor.check_before_write``. Treat only the
        resulting paths inside the frozen ``allowed_paths`` scope as runtime
        writes; all identity, baseline, checkout and containment checks still
        apply, and protected/test-integrity rules remain Verifier gates.
        """
        self._validate(check_external=False)
        current = _workspace_snapshot(self.workspace)
        baseline = json.loads(
            (self.control / "baseline.json").read_text(encoding="utf-8")
        )
        changed = tuple(sorted(
            path for path in set(baseline) | set(current)
            if baseline.get(path) != current.get(path)
        ))
        try:
            policy = json.loads(self.run.effective_policy or "{}")
        except (TypeError, json.JSONDecodeError):
            policy = {}
        rules = policy.get("allowed_paths") if isinstance(policy, dict) else []
        rules = [str(item).replace("\\", "/").strip().strip("/") for item in (rules or [])]
        unauthorized = [
            path for path in changed
            if not rules
            or ("." not in rules and "**" not in rules and not any(
                path == rule or path.startswith(rule + "/") or fnmatch.fnmatch(path, rule)
                for rule in rules if rule
            ))
        ]
        if unauthorized:
            self._fail("workspace_external_write")
        authorized = self._authorized()
        authorized.update(changed)
        self.control.joinpath("authorized_writes.json").write_text(
            json.dumps(sorted(authorized)), encoding="utf-8"
        )
        return changed
