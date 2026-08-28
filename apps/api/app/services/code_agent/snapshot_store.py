"""Sanitized, content-addressed and immutable repository snapshots."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


_COMMIT_SHA = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
_SEAL_FILE = ".git/snapshot-seal.json"
_SNAPSHOT_SCHEMA = 1


class SnapshotSealError(RuntimeError):
    def __init__(self, reason: str = "snapshot_hash_mismatch"):
        self.reason = reason
        super().__init__(reason)


@dataclass(frozen=True)
class SealedSourceSnapshot:
    snapshot_id: str
    content_hash: str
    resolved_commit: str
    storage_path: str
    deduplicated: bool
    security_schema_version: int = _SNAPSHOT_SCHEMA


def _git_environment() -> dict[str, str]:
    return {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_ASKPASS": "/usr/bin/false",
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_ALLOW_PROTOCOL": "file",
    }


def _git(repository: Path, *args: str, timeout: int = 60) -> bytes:
    try:
        result = subprocess.run(
            [
                "git",
                "-c", "core.hooksPath=/dev/null",
                "-c", "credential.helper=",
                "-c", "core.fsmonitor=false",
                "-C", str(repository),
                *args,
            ],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=_git_environment(),
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise SnapshotSealError("snapshot_invalid") from exc
    return result.stdout


def _assert_regular_tree(root: Path) -> None:
    for path in [root, *root.rglob("*")]:
        try:
            facts = path.stat(follow_symlinks=False)
        except OSError as exc:
            raise SnapshotSealError("snapshot_invalid") from exc
        if path == root or stat.S_ISDIR(facts.st_mode) or stat.S_ISREG(facts.st_mode):
            continue
        raise SnapshotSealError("snapshot_invalid")


def _remove_path(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink(missing_ok=True)


def _write_sanitized_config(repository: Path) -> None:
    config = repository / ".git" / "config"
    config.write_text(
        "[core]\n"
        "\trepositoryformatversion = 0\n"
        "\tfilemode = true\n"
        "\tbare = false\n"
        "\tlogallrefupdates = false\n"
        "\thooksPath = /dev/null\n"
        "[gc]\n"
        "\tauto = 0\n"
        "[protocol]\n"
        "\tversion = 2\n",
        encoding="utf-8",
    )


def _sanitize_git_metadata(repository: Path, commit: str) -> None:
    git_directory = repository / ".git"
    if not git_directory.is_dir() or git_directory.is_symlink():
        raise SnapshotSealError("snapshot_invalid")

    for relative in (
        "objects/info/alternates",
        "hooks",
        "modules",
        "logs",
        "config.worktree",
        "FETCH_HEAD",
        "ORIG_HEAD",
        "MERGE_HEAD",
        "CHERRY_PICK_HEAD",
        "REBASE_HEAD",
    ):
        _remove_path(git_directory / relative)
    (git_directory / "hooks").mkdir(mode=0o700, exist_ok=True)

    resolved = _git(repository, "rev-parse", "--verify", f"{commit}^{{commit}}").decode(
        "ascii",
        errors="strict",
    ).strip().lower()
    if resolved != commit:
        raise SnapshotSealError("snapshot_invalid")

    refs = _git(repository, "for-each-ref", "--format=%(refname)").decode(
        "utf-8",
        errors="strict",
    ).splitlines()
    for ref in refs:
        if not ref.startswith("refs/") or any(character.isspace() for character in ref):
            raise SnapshotSealError("snapshot_invalid")
        _git(repository, "update-ref", "-d", ref)

    _write_sanitized_config(repository)
    _git(repository, "update-ref", "refs/heads/snapshot", commit)
    _git(repository, "symbolic-ref", "HEAD", "refs/heads/snapshot")
    _git(repository, "read-tree", "--reset", commit)
    _git(repository, "reflog", "expire", "--expire=now", "--all")
    _git(repository, "gc", "--prune=now")
    _remove_path(git_directory / "logs")
    _remove_path(git_directory / "objects" / "info" / "alternates")

    if _git(repository, "remote").strip():
        raise SnapshotSealError("snapshot_invalid")
    remaining_refs = _git(repository, "for-each-ref", "--format=%(refname)").decode(
        "ascii",
        errors="strict",
    ).splitlines()
    if remaining_refs != ["refs/heads/snapshot"]:
        raise SnapshotSealError("snapshot_invalid")
    if _git(repository, "status", "--porcelain=v1", "--untracked-files=all").strip():
        raise SnapshotSealError("snapshot_invalid")


def _content_hash(root: Path) -> str:
    digest = hashlib.sha256()
    digest.update(f"code-source-snapshot-v{_SNAPSHOT_SCHEMA}\0".encode())
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        relative = path.relative_to(root).as_posix()
        if relative == _SEAL_FILE:
            continue
        facts = path.stat(follow_symlinks=False)
        if stat.S_ISDIR(facts.st_mode):
            kind = b"directory"
        elif stat.S_ISREG(facts.st_mode):
            kind = b"file"
        else:
            raise SnapshotSealError("snapshot_invalid")
        digest.update(kind + b"\0")
        digest.update(relative.encode("utf-8") + b"\0")
        mode = stat.S_IMODE(facts.st_mode) & 0o777 if kind == b"file" else 0
        digest.update(f"{mode:o}\0".encode())
        if kind == b"file":
            with path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
            digest.update(b"\0")
    return digest.hexdigest()


def _make_contents_read_only(root: Path) -> None:
    for path in sorted(root.rglob("*"), reverse=True):
        facts = path.stat(follow_symlinks=False)
        if stat.S_ISDIR(facts.st_mode):
            path.chmod(0o500)
        else:
            path.chmod(0o400 | (stat.S_IMODE(facts.st_mode) & 0o111))


def _assert_immutable_tree(root: Path) -> None:
    for path in [root, *root.rglob("*")]:
        facts = path.stat(follow_symlinks=False)
        if stat.S_IMODE(facts.st_mode) & 0o222:
            raise SnapshotSealError()


def _make_writable_for_cleanup(root: Path) -> None:
    if not root.exists() or root.is_symlink():
        return
    root.chmod(0o700)
    for path in root.rglob("*"):
        if path.is_symlink():
            continue
        facts = path.stat(follow_symlinks=False)
        path.chmod(0o700 if stat.S_ISDIR(facts.st_mode) else 0o600)


class SourceSnapshotStore:
    def __init__(self, root: Path):
        try:
            self.root = root.resolve(strict=True)
        except OSError as exc:
            raise SnapshotSealError("snapshot_invalid") from exc
        if root.is_symlink() or not self.root.is_dir():
            raise SnapshotSealError("snapshot_invalid")

    def verify(
        self,
        snapshot_path: Path,
        *,
        expected_hash: str,
        expected_commit: str,
    ) -> None:
        try:
            resolved = snapshot_path.resolve(strict=True)
            resolved.relative_to(self.root)
            metadata = json.loads((resolved / _SEAL_FILE).read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            raise SnapshotSealError() from exc
        if (
            resolved.parent != self.root
            or metadata != {
                "content_hash": expected_hash,
                "resolved_commit": expected_commit,
                "security_schema_version": _SNAPSHOT_SCHEMA,
            }
            or _content_hash(resolved) != expected_hash
        ):
            raise SnapshotSealError()
        _assert_immutable_tree(resolved)

    def delete_verified(
        self,
        snapshot_path: Path,
        *,
        expected_hash: str,
        expected_commit: str,
    ) -> bool:
        if not snapshot_path.exists():
            return False
        self.verify(
            snapshot_path,
            expected_hash=expected_hash,
            expected_commit=expected_commit,
        )
        resolved = snapshot_path.resolve(strict=True)
        _make_writable_for_cleanup(resolved)
        shutil.rmtree(resolved)
        return True

    def cleanup_abandoned_staging(self, *, older_than: datetime) -> tuple[str, ...]:
        removed: list[str] = []
        for candidate in sorted(self.root.iterdir()):
            if not candidate.name.startswith(".seal-"):
                continue
            try:
                modified = datetime.fromtimestamp(
                    candidate.stat(follow_symlinks=False).st_mtime
                )
            except OSError as exc:
                raise SnapshotSealError("snapshot_invalid") from exc
            if modified > older_than:
                continue
            if candidate.is_symlink():
                candidate.unlink()
            elif candidate.is_dir():
                _make_writable_for_cleanup(candidate)
                shutil.rmtree(candidate)
            else:
                candidate.unlink()
            removed.append(candidate.name)
        return tuple(removed)

    def seal(self, repository_path: Path, *, resolved_commit: str) -> SealedSourceSnapshot:
        commit = str(resolved_commit or "").lower()
        if not _COMMIT_SHA.fullmatch(commit):
            raise SnapshotSealError("snapshot_invalid")
        try:
            source = repository_path.resolve(strict=True)
        except OSError as exc:
            raise SnapshotSealError("snapshot_invalid") from exc
        if (
            repository_path.is_symlink()
            or not source.is_dir()
            or source.is_relative_to(self.root)
            or self.root.is_relative_to(source)
        ):
            raise SnapshotSealError("snapshot_invalid")
        _assert_regular_tree(source)

        temporary = self.root / f".seal-{uuid.uuid4().hex}"
        try:
            shutil.copytree(source, temporary, symlinks=True)
            _assert_regular_tree(temporary)
            _sanitize_git_metadata(temporary, commit)
            _make_contents_read_only(temporary)
            content_hash = _content_hash(temporary)
            destination = self.root / content_hash
            seal_metadata = {
                "content_hash": content_hash,
                "resolved_commit": commit,
                "security_schema_version": _SNAPSHOT_SCHEMA,
            }
            seal_path = temporary / _SEAL_FILE
            seal_path.parent.chmod(0o700)
            seal_path.write_text(
                json.dumps(seal_metadata, sort_keys=True, separators=(",", ":")),
                encoding="utf-8",
            )
            seal_path.chmod(0o400)
            seal_path.parent.chmod(0o500)

            if destination.exists():
                self.verify(
                    destination,
                    expected_hash=content_hash,
                    expected_commit=commit,
                )
                _make_writable_for_cleanup(temporary)
                shutil.rmtree(temporary)
                deduplicated = True
            else:
                try:
                    temporary.rename(destination)
                    destination.chmod(0o500)
                    deduplicated = False
                except OSError:
                    if not destination.exists():
                        raise
                    self.verify(
                        destination,
                        expected_hash=content_hash,
                        expected_commit=commit,
                    )
                    _make_writable_for_cleanup(temporary)
                    shutil.rmtree(temporary)
                    deduplicated = True
        except Exception:
            _make_writable_for_cleanup(temporary)
            shutil.rmtree(temporary, ignore_errors=True)
            raise

        return SealedSourceSnapshot(
            snapshot_id=content_hash[:16],
            content_hash=content_hash,
            resolved_commit=commit,
            storage_path=str(destination),
            deduplicated=deduplicated,
        )
