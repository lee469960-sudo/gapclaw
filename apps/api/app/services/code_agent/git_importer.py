"""Restricted Git ref resolution and bounded import into isolated staging."""

from __future__ import annotations

import os
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Callable

from app.services.code_agent.network_policy import (
    RepositoryNetworkGuard,
    ValidatedRepositoryConnection,
)


_SAFE_REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,127}$")
_COMMIT_SHA = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
_LFS_POINTER = b"version https://git-lfs.github.com/spec/v1\n"


def _write_all_fd(descriptor: int, value: bytes | bytearray) -> None:
    offset = 0
    while offset < len(value):
        written = os.write(descriptor, value[offset:])
        if written <= 0:
            raise RestrictedGitImportError("repository_auth_failed")
        offset += written


class RestrictedGitImportError(RuntimeError):
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


def normalize_requested_ref(requested_ref: str) -> str:
    ref = str(requested_ref or "")
    if (
        not _SAFE_REF.fullmatch(ref)
        or ".." in ref
        or "//" in ref
        or ref.endswith(".")
        or ref.endswith(".lock")
    ):
        raise RestrictedGitImportError("repository_ref_invalid")
    return ref


@dataclass(frozen=True)
class GitImportLimits:
    max_download_bytes: int
    max_unpacked_bytes: int
    max_files: int
    max_file_bytes: int
    timeout_seconds: float

    def __post_init__(self) -> None:
        for value in (
            self.max_download_bytes,
            self.max_unpacked_bytes,
            self.max_files,
            self.max_file_bytes,
            self.timeout_seconds,
        ):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
                raise ValueError("repository_import_limits_invalid")

    @classmethod
    def from_settings(cls, settings) -> GitImportLimits:
        return cls(
            max_download_bytes=settings.code_import_max_download_bytes,
            max_unpacked_bytes=settings.code_import_max_unpacked_bytes,
            max_files=settings.code_import_max_files,
            max_file_bytes=settings.code_import_max_file_bytes,
            timeout_seconds=settings.code_import_timeout_seconds,
        )


@dataclass(frozen=True)
class RestrictedGitImport:
    repository_path: str
    requested_ref: str
    resolved_commit: str
    downloaded_bytes: int
    unpacked_bytes: int
    file_count: int
    hooks_executed: bool = False
    submodules_initialized: bool = False
    lfs_objects_fetched: bool = False
    config_isolated: bool = True


@dataclass(frozen=True)
class _TreeEntry:
    mode: str
    object_type: str
    object_id: str
    size: int
    path: str


class RestrictedGitImporter:
    def __init__(
        self,
        *,
        staging_root: Path,
        limits: GitImportLimits,
        clock: Callable[[], float] = time.monotonic,
    ):
        try:
            self.staging_root = staging_root.resolve(strict=True)
        except OSError as exc:
            raise RestrictedGitImportError("repository_source_not_allowed") from exc
        if staging_root.is_symlink() or not self.staging_root.is_dir():
            raise RestrictedGitImportError("repository_source_not_allowed")
        self.limits = limits
        self.clock = clock

    @staticmethod
    def _environment(
        *,
        allowed_protocols: str = "file",
        overrides: dict[str, str] | None = None,
    ) -> dict[str, str]:
        environment = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_ASKPASS": "/usr/bin/false",
            "SSH_ASKPASS": "/usr/bin/false",
            "GIT_LFS_SKIP_SMUDGE": "1",
            "GIT_OPTIONAL_LOCKS": "0",
            "GIT_ALLOW_PROTOCOL": allowed_protocols,
        }
        environment.update(overrides or {})
        return environment

    @staticmethod
    def _git_prefix(extra_configs: tuple[str, ...] = ()) -> list[str]:
        prefix = [
            "git",
            "-c", "core.hooksPath=/dev/null",
            "-c", "core.fsmonitor=false",
            "-c", "credential.helper=",
            "-c", "submodule.recurse=false",
            "-c", "filter.lfs.smudge=",
            "-c", "filter.lfs.process=",
            "-c", "filter.lfs.required=false",
            "-c", "protocol.file.allow=always",
        ]
        for config in extra_configs:
            prefix.extend(["-c", config])
        return prefix

    def _tree_size(
        self,
        root: Path,
        *,
        deadline: float | None = None,
        stop_after: int | None = None,
    ) -> int:
        if not root.exists():
            return 0
        total = 0
        pending = [root]
        while pending:
            if deadline is not None and self.clock() >= deadline:
                raise RestrictedGitImportError("repository_limit_exceeded")
            current = pending.pop()
            try:
                with os.scandir(current) as entries:
                    for entry in entries:
                        try:
                            facts = entry.stat(follow_symlinks=False)
                        except FileNotFoundError:
                            continue
                        if stat.S_ISDIR(facts.st_mode):
                            pending.append(Path(entry.path))
                        else:
                            total += facts.st_size
                            if stop_after is not None and total > stop_after:
                                return total
            except FileNotFoundError:
                continue
            except OSError as exc:
                raise RestrictedGitImportError("repository_unreachable") from exc
        return total

    def _run_git(
        self,
        args: list[str],
        *,
        deadline: float,
        cwd: Path | None = None,
        output_limit: int = 1024 * 1024,
        monitored_path: Path | None = None,
        monitored_limit: int | None = None,
        output_file=None,
        failure_reason: str = "repository_unreachable",
        allowed_protocols: str = "file",
        extra_configs: tuple[str, ...] = (),
        environment_overrides: dict[str, str] | None = None,
        pass_fds: tuple[int, ...] = (),
    ) -> bytes:
        owned_output = output_file is None
        sink = output_file or tempfile.TemporaryFile()
        try:
            if self.clock() >= deadline:
                raise RestrictedGitImportError("repository_limit_exceeded")
            try:
                process = subprocess.Popen(
                    [*self._git_prefix(extra_configs), *args],
                    cwd=cwd,
                    env=self._environment(
                        allowed_protocols=allowed_protocols,
                        overrides=environment_overrides,
                    ),
                    stdin=subprocess.DEVNULL,
                    stdout=sink,
                    stderr=subprocess.DEVNULL,
                    close_fds=True,
                    pass_fds=pass_fds,
                )
            except OSError as exc:
                raise RestrictedGitImportError(failure_reason) from exc
            while process.poll() is None:
                if self.clock() >= deadline:
                    process.kill()
                    process.wait()
                    raise RestrictedGitImportError("repository_limit_exceeded")
                if sink.tell() > output_limit:
                    process.kill()
                    process.wait()
                    raise RestrictedGitImportError("repository_limit_exceeded")
                if (
                    monitored_path is not None
                    and monitored_limit is not None
                    and self._tree_size(
                        monitored_path,
                        deadline=deadline,
                        stop_after=monitored_limit,
                    ) > monitored_limit
                ):
                    process.kill()
                    process.wait()
                    raise RestrictedGitImportError("repository_limit_exceeded")
                time.sleep(0.01)
            if process.returncode != 0:
                raise RestrictedGitImportError(failure_reason)
            if self.clock() >= deadline:
                raise RestrictedGitImportError("repository_limit_exceeded")
            if sink.tell() > output_limit:
                raise RestrictedGitImportError("repository_limit_exceeded")
            if (
                monitored_path is not None
                and monitored_limit is not None
                and self._tree_size(
                    monitored_path,
                    deadline=deadline,
                    stop_after=monitored_limit,
                ) > monitored_limit
            ):
                raise RestrictedGitImportError("repository_limit_exceeded")
            sink.seek(0)
            return sink.read() if owned_output else b""
        finally:
            if owned_output:
                sink.close()

    @staticmethod
    def _validate_ref(requested_ref: str) -> str:
        return normalize_requested_ref(requested_ref)

    def _resolve_commit(self, repository: Path, ref: str, deadline: float) -> str:
        output = self._run_git(
            ["-C", str(repository), "rev-parse", "--verify", "--end-of-options", f"{ref}^{{commit}}"],
            deadline=deadline,
            output_limit=256,
            failure_reason="repository_ref_invalid",
        )
        commit = output.decode("ascii", errors="strict").strip().lower()
        if not _COMMIT_SHA.fullmatch(commit):
            raise RestrictedGitImportError("repository_ref_invalid")
        return commit

    def _read_tree(self, repository: Path, commit: str, deadline: float) -> list[_TreeEntry]:
        output_limit = max(4096, self.limits.max_files * 512)
        output = self._run_git(
            ["-C", str(repository), "ls-tree", "-r", "-z", "-l", commit],
            deadline=deadline,
            output_limit=output_limit,
            failure_reason="repository_ref_invalid",
        )
        entries: list[_TreeEntry] = []
        total = 0
        for record in output.split(b"\x00"):
            if not record:
                continue
            try:
                metadata, raw_path = record.split(b"\t", 1)
                mode, object_type, object_id, raw_size = metadata.split(b" ", 3)
                path = raw_path.decode("utf-8", errors="strict")
                size = int(raw_size) if raw_size != b"-" else 0
            except (UnicodeDecodeError, ValueError) as exc:
                raise RestrictedGitImportError("repository_feature_unsupported") from exc
            entry = _TreeEntry(
                mode=mode.decode("ascii"),
                object_type=object_type.decode("ascii"),
                object_id=object_id.decode("ascii"),
                size=size,
                path=path,
            )
            if entry.mode == "160000" or entry.object_type == "commit":
                raise RestrictedGitImportError("repository_feature_unsupported")
            if entry.mode not in {"100644", "100755"} or entry.object_type != "blob":
                raise RestrictedGitImportError("repository_feature_unsupported")
            if entry.size < 0 or entry.size > self.limits.max_file_bytes:
                raise RestrictedGitImportError("repository_limit_exceeded")
            entries.append(entry)
            total += entry.size
            if len(entries) > self.limits.max_files or total > self.limits.max_unpacked_bytes:
                raise RestrictedGitImportError("repository_limit_exceeded")
        return entries

    @staticmethod
    def _safe_member_path(name: str) -> PurePosixPath:
        path = PurePosixPath(name)
        if (
            not name
            or name.startswith("/")
            or "\\" in name
            or path.is_absolute()
            or any(part in {"", ".", ".."} for part in path.parts)
            or path.parts[0] == ".git"
        ):
            raise RestrictedGitImportError("repository_feature_unsupported")
        return path

    def _extract_archive(
        self,
        archive,
        destination: Path,
        expected_entries: list[_TreeEntry],
        deadline: float,
    ) -> tuple[int, int]:
        expected = {entry.path: entry for entry in expected_entries}
        extracted: set[str] = set()
        files = 0
        unpacked = 0
        archive.seek(0)
        try:
            bundle = tarfile.open(fileobj=archive, mode="r:")
        except tarfile.TarError as exc:
            raise RestrictedGitImportError("repository_feature_unsupported") from exc
        with bundle:
            for member in bundle:
                if self.clock() >= deadline:
                    raise RestrictedGitImportError("repository_limit_exceeded")
                relative = self._safe_member_path(member.name.rstrip("/"))
                target = destination.joinpath(*relative.parts)
                if member.isdir():
                    target.mkdir(mode=0o700, parents=True, exist_ok=True)
                    continue
                if not member.isfile() or member.name in extracted:
                    raise RestrictedGitImportError("repository_feature_unsupported")
                tree_entry = expected.get(member.name)
                if tree_entry is None or member.size != tree_entry.size:
                    raise RestrictedGitImportError("repository_feature_unsupported")
                files += 1
                unpacked += member.size
                if (
                    files > self.limits.max_files
                    or member.size > self.limits.max_file_bytes
                    or unpacked > self.limits.max_unpacked_bytes
                ):
                    raise RestrictedGitImportError("repository_limit_exceeded")
                target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                source = bundle.extractfile(member)
                if source is None:
                    raise RestrictedGitImportError("repository_feature_unsupported")
                prefix = b""
                written = 0
                with target.open("xb") as handle:
                    while True:
                        if self.clock() >= deadline:
                            raise RestrictedGitImportError("repository_limit_exceeded")
                        chunk = source.read(1024 * 1024)
                        if not chunk:
                            break
                        if len(prefix) < len(_LFS_POINTER):
                            prefix += chunk[:len(_LFS_POINTER) - len(prefix)]
                        handle.write(chunk)
                        written += len(chunk)
                if written != member.size:
                    raise RestrictedGitImportError("repository_feature_unsupported")
                if prefix == _LFS_POINTER:
                    raise RestrictedGitImportError("repository_feature_unsupported")
                target.chmod(0o755 if tree_entry.mode == "100755" else 0o644)
                extracted.add(member.name)
        if extracted != set(expected):
            raise RestrictedGitImportError("repository_feature_unsupported")
        return files, unpacked

    def import_from_staging(
        self,
        source_repository: Path,
        *,
        requested_ref: str,
        import_id: str,
    ) -> RestrictedGitImport:
        try:
            source = source_repository.resolve(strict=True)
        except OSError as exc:
            raise RestrictedGitImportError("repository_source_not_allowed") from exc
        if source_repository.is_symlink() or not source.is_dir():
            raise RestrictedGitImportError("repository_source_not_allowed")
        if (
            self.staging_root == source
            or self.staging_root.is_relative_to(source)
            or source.is_relative_to(self.staging_root)
        ):
            raise RestrictedGitImportError("repository_source_not_allowed")
        return self._import_clone(
            clone_source=str(source),
            requested_ref=requested_ref,
            import_id=import_id,
        )

    @staticmethod
    def _ssh_options(
        connection: ValidatedRepositoryConnection,
        known_hosts_file: Path,
    ) -> dict[str, str]:
        try:
            known_hosts = known_hosts_file.resolve(strict=True)
        except OSError as exc:
            raise RestrictedGitImportError("repository_network_policy_denied") from exc
        if (
            not known_hosts_file.is_absolute()
            or known_hosts_file.is_symlink()
            or not known_hosts.is_file()
        ):
            raise RestrictedGitImportError("repository_network_policy_denied")
        command = shlex.join([
            "/usr/bin/ssh",
            "-F", os.devnull,
            "-o", "BatchMode=yes",
            "-o", "IdentitiesOnly=yes",
            "-o", "StrictHostKeyChecking=yes",
            "-o", f"UserKnownHostsFile={known_hosts}",
            "-o", f"Hostname={connection.addresses[0]}",
            "-o", f"HostKeyAlias={connection.source.host}",
            "-p", str(connection.source.port),
        ])
        return {"GIT_SSH_COMMAND": command}

    @staticmethod
    def _http_configs(
        connection: ValidatedRepositoryConnection,
    ) -> tuple[str, ...]:
        configs = [
            "protocol.file.allow=never",
            "http.followRedirects=false",
        ]
        if connection.uses_transport_proxy:
            return tuple(configs)
        address = connection.addresses[0]
        if ":" in address:
            address = f"[{address}]"
        configs.append(
            (
                "http.curloptResolve="
                f"{connection.source.host}:{connection.source.port}:{address}"
            )
        )
        return tuple(configs)

    def import_remote(
        self,
        network_guard: RepositoryNetworkGuard,
        source: str,
        *,
        requested_ref: str,
        import_id: str,
        ssh_known_hosts_file: Path | None = None,
        credential_lease=None,
        auth_username: str = "",
        proxy_environment: dict[str, str] | None = None,
    ) -> RestrictedGitImport:
        connection = network_guard.validate_connection(source)
        scheme = connection.source.scheme
        if credential_lease is not None and scheme not in {"https", "http"}:
            raise RestrictedGitImportError("repository_auth_failed")
        if scheme in {"https", "http"}:
            extra_configs = self._http_configs(connection)
            environment_overrides = (
                dict(proxy_environment or {})
                if connection.uses_transport_proxy
                else None
            )
        elif scheme == "ssh" and ssh_known_hosts_file is not None:
            extra_configs = ("protocol.file.allow=never",)
            environment_overrides = self._ssh_options(connection, ssh_known_hosts_file)
        else:
            raise RestrictedGitImportError("repository_network_policy_denied")
        return self._import_clone(
            clone_source=connection.source.locator,
            requested_ref=requested_ref,
            import_id=import_id,
            allowed_protocols=scheme,
            extra_configs=extra_configs,
            environment_overrides=environment_overrides,
            credential_lease=credential_lease,
            auth_username=auth_username,
        )

    def _import_clone(
        self,
        *,
        clone_source: str,
        requested_ref: str,
        import_id: str,
        allowed_protocols: str = "file",
        extra_configs: tuple[str, ...] = (),
        environment_overrides: dict[str, str] | None = None,
        credential_lease=None,
        auth_username: str = "",
    ) -> RestrictedGitImport:
        ref = self._validate_ref(requested_ref)
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", import_id or ""):
            raise RestrictedGitImportError("repository_source_not_allowed")
        destination = self.staging_root / import_id
        if destination.exists() or destination.is_symlink():
            raise RestrictedGitImportError("repository_source_not_allowed")

        deadline = self.clock() + self.limits.timeout_seconds
        template_path = self.staging_root / f".{import_id}.empty-template"
        askpass_path = self.staging_root / f".{import_id}.askpass.py"
        credential_fd = -1
        credential_write_fd = -1
        archive = tempfile.TemporaryFile()
        try:
            template_path.mkdir(mode=0o700)
            clone_environment = dict(environment_overrides or {})
            clone_pass_fds: tuple[int, ...] = ()
            if credential_lease is not None:
                username = str(auth_username or "oauth2")
                secret = bytearray(credential_lease.read())
                invalid_secret = (
                    not username
                    or len(username) > 256
                    or any(character in username for character in ("\x00", "\r", "\n"))
                    or not secret
                    or len(secret) > 16 * 1024
                    or any(character in secret for character in (0, 10, 13))
                )
                if invalid_secret:
                    for index in range(len(secret)):
                        secret[index] = 0
                    raise RestrictedGitImportError("repository_auth_failed")
                helper = (
                    f"#!{sys.executable}\n"
                    "import os, sys\n"
                    "prompt = sys.argv[1].lower() if len(sys.argv) > 1 else ''\n"
                    "if 'username' in prompt:\n"
                    "    os.write(1, (os.environ['CODE_AGENT_AUTH_USERNAME'] + '\\n').encode())\n"
                    "else:\n"
                    "    fd = int(os.environ['CODE_AGENT_ASKPASS_FD'])\n"
                    "    value = bytearray()\n"
                    "    while True:\n"
                    "        item = os.read(fd, 1)\n"
                    "        if not item or item == b'\\n':\n"
                    "            break\n"
                    "        value.extend(item)\n"
                    "    os.write(1, value + b'\\n')\n"
                    "    for index in range(len(value)):\n"
                    "        value[index] = 0\n"
                )
                askpass_path.write_text(helper, encoding="utf-8")
                askpass_path.chmod(0o500)
                credential_fd, credential_write_fd = os.pipe()
                try:
                    _write_all_fd(credential_write_fd, secret + b"\n")
                finally:
                    for index in range(len(secret)):
                        secret[index] = 0
                    os.close(credential_write_fd)
                    credential_write_fd = -1
                clone_environment.update({
                    "GIT_ASKPASS": str(askpass_path),
                    "CODE_AGENT_ASKPASS_FD": str(credential_fd),
                    "CODE_AGENT_AUTH_USERNAME": username,
                })
                clone_pass_fds = (credential_fd,)
            self._run_git(
                [
                    "clone",
                    "--quiet",
                    "--no-checkout",
                    "--no-local",
                    "--no-recurse-submodules",
                    f"--template={template_path}",
                    "--",
                    clone_source,
                    str(destination),
                ],
                deadline=deadline,
                output_limit=4096,
                monitored_path=destination,
                monitored_limit=self.limits.max_download_bytes,
                allowed_protocols=allowed_protocols,
                extra_configs=extra_configs,
                environment_overrides=clone_environment,
                pass_fds=clone_pass_fds,
                failure_reason=(
                    "repository_auth_failed"
                    if credential_lease is not None
                    else "repository_unreachable"
                ),
            )
            if credential_fd >= 0:
                os.close(credential_fd)
                credential_fd = -1
            askpass_path.unlink(missing_ok=True)
            downloaded = self._tree_size(
                destination / ".git" / "objects",
                deadline=deadline,
                stop_after=self.limits.max_download_bytes,
            )
            if downloaded > self.limits.max_download_bytes:
                raise RestrictedGitImportError("repository_limit_exceeded")
            commit = self._resolve_commit(destination, ref, deadline)
            entries = self._read_tree(destination, commit, deadline)
            archive_limit = self.limits.max_unpacked_bytes + max(
                1024 * 1024,
                self.limits.max_files * 2048,
            )
            self._run_git(
                ["-C", str(destination), "archive", "--format=tar", commit],
                deadline=deadline,
                output_limit=archive_limit,
                output_file=archive,
                failure_reason="repository_ref_invalid",
            )
            file_count, unpacked = self._extract_archive(
                archive,
                destination,
                entries,
                deadline,
            )
        except RestrictedGitImportError:
            shutil.rmtree(destination, ignore_errors=True)
            raise
        except (OSError, UnicodeError, tarfile.TarError) as exc:
            shutil.rmtree(destination, ignore_errors=True)
            raise RestrictedGitImportError("repository_feature_unsupported") from exc
        finally:
            if credential_fd >= 0:
                os.close(credential_fd)
            if credential_write_fd >= 0:
                os.close(credential_write_fd)
            askpass_path.unlink(missing_ok=True)
            archive.close()
            shutil.rmtree(template_path, ignore_errors=True)

        return RestrictedGitImport(
            repository_path=str(destination),
            requested_ref=ref,
            resolved_commit=commit,
            downloaded_bytes=downloaded,
            unpacked_bytes=unpacked,
            file_count=file_count,
        )
