"""Race-resistant local repository copy into Importer-owned staging."""

from __future__ import annotations

import json
import os
import re
import shutil
import stat
from dataclasses import dataclass
from pathlib import Path


_IMPORT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_DIRECTORY_FLAGS = (
    os.O_RDONLY
    | getattr(os, "O_DIRECTORY", 0)
    | getattr(os, "O_NOFOLLOW", 0)
    | getattr(os, "O_CLOEXEC", 0)
)
_FILE_FLAGS = (
    os.O_RDONLY
    | getattr(os, "O_NOFOLLOW", 0)
    | getattr(os, "O_CLOEXEC", 0)
)


class LocalRepositoryPolicyError(RuntimeError):
    def __init__(self, reason: str = "repository_source_not_allowed"):
        self.reason = reason
        super().__init__(reason)


@dataclass(frozen=True)
class LocalRepositoryImport:
    canonical_source_path: str
    staging_path: str
    file_count: int
    byte_count: int
    source_bound: bool = False


@dataclass(frozen=True)
class _EntryFacts:
    mode: int
    device: int
    inode: int
    size: int
    mtime_ns: int
    ctime_ns: int

    @classmethod
    def from_stat(cls, value: os.stat_result) -> _EntryFacts:
        return cls(
            mode=value.st_mode,
            device=value.st_dev,
            inode=value.st_ino,
            size=value.st_size,
            mtime_ns=value.st_mtime_ns,
            ctime_ns=value.st_ctime_ns,
        )


def normalize_local_repository_roots(raw: str | list[str]) -> tuple[Path, ...]:
    if isinstance(raw, str):
        try:
            values = json.loads(raw or "[]")
        except json.JSONDecodeError as exc:
            raise LocalRepositoryPolicyError() from exc
    else:
        values = raw
    if not isinstance(values, list):
        raise LocalRepositoryPolicyError()

    roots: set[Path] = set()
    for value in values:
        if not isinstance(value, str) or not value or value != value.strip():
            raise LocalRepositoryPolicyError()
        configured = Path(value)
        try:
            resolved = configured.resolve(strict=True)
        except OSError as exc:
            raise LocalRepositoryPolicyError() from exc
        if not configured.is_absolute() or configured.is_symlink() or not resolved.is_dir():
            raise LocalRepositoryPolicyError()
        roots.add(resolved)
    return tuple(sorted(roots, key=lambda item: (-len(item.parts), str(item))))


def _raw_local_path(locator: str) -> Path:
    raw = str(locator or "")
    if (
        not raw
        or raw != raw.strip()
        or "\x00" in raw
        or "\\" in raw
        or raw.startswith("file://")
    ):
        raise LocalRepositoryPolicyError()
    parts = raw.split("/")
    if not raw.startswith("/") or any(part in {"", ".", ".."} for part in parts[1:]):
        raise LocalRepositoryPolicyError()
    return Path(raw)


def normalize_local_repository_locator(
    locator: str,
    *,
    allowed_roots: str | list[str],
) -> Path:
    lexical = _raw_local_path(locator)
    roots = normalize_local_repository_roots(allowed_roots)
    try:
        resolved = lexical.resolve(strict=True)
    except OSError as exc:
        raise LocalRepositoryPolicyError() from exc
    if resolved != lexical or lexical.is_symlink() or not resolved.is_dir():
        raise LocalRepositoryPolicyError()
    if not any(resolved == root or resolved.is_relative_to(root) for root in roots):
        raise LocalRepositoryPolicyError()
    return resolved


def _directory_entries(directory_fd: int) -> dict[str, _EntryFacts]:
    try:
        with os.scandir(directory_fd) as entries:
            result = {
                entry.name: _EntryFacts.from_stat(entry.stat(follow_symlinks=False))
                for entry in entries
            }
    except OSError as exc:
        raise LocalRepositoryPolicyError() from exc
    if any(name in {"", ".", ".."} or "/" in name or "\x00" in name for name in result):
        raise LocalRepositoryPolicyError()
    return result


def _same_object(left: _EntryFacts, right: _EntryFacts) -> bool:
    return (
        left.device == right.device
        and left.inode == right.inode
        and stat.S_IFMT(left.mode) == stat.S_IFMT(right.mode)
    )


def _write_all(target_fd: int, data: bytes) -> None:
    offset = 0
    while offset < len(data):
        written = os.write(target_fd, data[offset:])
        if written <= 0:
            raise LocalRepositoryPolicyError()
        offset += written


class LocalRepositoryImporter:
    def __init__(
        self,
        *,
        allowed_roots: str | list[str],
        staging_root: Path,
    ):
        self.allowed_roots = normalize_local_repository_roots(allowed_roots)
        if not getattr(os, "O_NOFOLLOW", 0) or not getattr(os, "O_DIRECTORY", 0):
            raise LocalRepositoryPolicyError()
        self._root_facts = {
            root: _EntryFacts.from_stat(root.stat(follow_symlinks=False))
            for root in self.allowed_roots
        }
        try:
            self.staging_root = staging_root.resolve(strict=True)
        except OSError as exc:
            raise LocalRepositoryPolicyError() from exc
        if staging_root.is_symlink() or not self.staging_root.is_dir():
            raise LocalRepositoryPolicyError()
        if any(
            self.staging_root == root
            or self.staging_root.is_relative_to(root)
            or root.is_relative_to(self.staging_root)
            for root in self.allowed_roots
        ):
            raise LocalRepositoryPolicyError()

    def _resolve_source(self, locator: str) -> tuple[Path, Path, tuple[str, ...]]:
        resolved = normalize_local_repository_locator(
            locator,
            allowed_roots=[str(root) for root in self.allowed_roots],
        )

        for root in self.allowed_roots:
            try:
                relative = resolved.relative_to(root)
            except ValueError:
                continue
            return resolved, root, tuple(relative.parts)
        raise LocalRepositoryPolicyError()

    def _open_source(self, root: Path, relative_parts: tuple[str, ...]) -> int:
        try:
            current_fd = os.open(root, _DIRECTORY_FLAGS)
            root_facts = self._root_facts[root]
            if not _same_object(_EntryFacts.from_stat(os.fstat(current_fd)), root_facts):
                raise LocalRepositoryPolicyError()
            for part in relative_parts:
                next_fd = os.open(part, _DIRECTORY_FLAGS, dir_fd=current_fd)
                os.close(current_fd)
                current_fd = next_fd
                if os.fstat(current_fd).st_dev != root_facts.device:
                    raise LocalRepositoryPolicyError()
            return current_fd
        except LocalRepositoryPolicyError:
            if "current_fd" in locals():
                os.close(current_fd)
            raise
        except OSError as exc:
            if "current_fd" in locals():
                os.close(current_fd)
            raise LocalRepositoryPolicyError() from exc

    def _copy_file(
        self,
        name: str,
        source_directory_fd: int,
        destination_directory_fd: int,
        expected: _EntryFacts,
        root_device: int,
    ) -> int:
        source_fd = -1
        destination_fd = -1
        try:
            source_fd = os.open(name, _FILE_FLAGS, dir_fd=source_directory_fd)
            before = _EntryFacts.from_stat(os.fstat(source_fd))
            if (
                before != expected
                or before.device != root_device
                or not stat.S_ISREG(before.mode)
            ):
                raise LocalRepositoryPolicyError()
            mode = stat.S_IMODE(before.mode) & 0o777
            destination_fd = os.open(
                name,
                os.O_WRONLY
                | os.O_CREAT
                | os.O_EXCL
                | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_CLOEXEC", 0),
                mode,
                dir_fd=destination_directory_fd,
            )
            copied = 0
            while True:
                chunk = os.read(source_fd, 1024 * 1024)
                if not chunk:
                    break
                _write_all(destination_fd, chunk)
                copied += len(chunk)
            if copied != before.size or _EntryFacts.from_stat(os.fstat(source_fd)) != before:
                raise LocalRepositoryPolicyError()
            return copied
        except OSError as exc:
            raise LocalRepositoryPolicyError() from exc
        finally:
            if destination_fd >= 0:
                os.close(destination_fd)
            if source_fd >= 0:
                os.close(source_fd)

    def _copy_tree(
        self,
        source_directory_fd: int,
        destination_directory_fd: int,
        root_device: int,
    ) -> tuple[int, int]:
        before = _directory_entries(source_directory_fd)
        files = 0
        size = 0
        for name in sorted(before):
            expected = before[name]
            if expected.device != root_device or stat.S_ISLNK(expected.mode) or not (
                stat.S_ISREG(expected.mode) or stat.S_ISDIR(expected.mode)
            ):
                raise LocalRepositoryPolicyError()
            if stat.S_ISREG(expected.mode):
                size += self._copy_file(
                    name,
                    source_directory_fd,
                    destination_directory_fd,
                    expected,
                    root_device,
                )
                files += 1
                continue

            source_child_fd = -1
            destination_child_fd = -1
            try:
                source_child_fd = os.open(
                    name,
                    _DIRECTORY_FLAGS,
                    dir_fd=source_directory_fd,
                )
                if _EntryFacts.from_stat(os.fstat(source_child_fd)) != expected:
                    raise LocalRepositoryPolicyError()
                os.mkdir(name, mode=0o700, dir_fd=destination_directory_fd)
                destination_child_fd = os.open(
                    name,
                    _DIRECTORY_FLAGS,
                    dir_fd=destination_directory_fd,
                )
                child_files, child_size = self._copy_tree(
                    source_child_fd,
                    destination_child_fd,
                    root_device,
                )
                if _EntryFacts.from_stat(os.fstat(source_child_fd)) != expected:
                    raise LocalRepositoryPolicyError()
                files += child_files
                size += child_size
            except OSError as exc:
                raise LocalRepositoryPolicyError() from exc
            finally:
                if destination_child_fd >= 0:
                    os.close(destination_child_fd)
                if source_child_fd >= 0:
                    os.close(source_child_fd)

        if _directory_entries(source_directory_fd) != before:
            raise LocalRepositoryPolicyError()
        return files, size

    def import_to_staging(self, locator: str, *, import_id: str) -> LocalRepositoryImport:
        if not _IMPORT_ID.fullmatch(str(import_id or "")):
            raise LocalRepositoryPolicyError()
        source, root, relative_parts = self._resolve_source(locator)
        if self.staging_root == source or self.staging_root.is_relative_to(source):
            raise LocalRepositoryPolicyError()
        if source.is_relative_to(self.staging_root):
            raise LocalRepositoryPolicyError()

        destination = self.staging_root / import_id
        if destination.exists() or destination.is_symlink():
            raise LocalRepositoryPolicyError()

        source_fd = -1
        destination_fd = -1
        try:
            source_fd = self._open_source(root, relative_parts)
            destination.mkdir(mode=0o700)
            destination_fd = os.open(destination, _DIRECTORY_FLAGS)
            file_count, byte_count = self._copy_tree(
                source_fd,
                destination_fd,
                self._root_facts[root].device,
            )
        except Exception:
            shutil.rmtree(destination, ignore_errors=True)
            raise
        finally:
            if destination_fd >= 0:
                os.close(destination_fd)
            if source_fd >= 0:
                os.close(source_fd)

        return LocalRepositoryImport(
            canonical_source_path=str(source),
            staging_path=str(destination),
            file_count=file_count,
            byte_count=byte_count,
        )
