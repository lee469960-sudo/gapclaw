"""OpenSpec task 3.4: isolated Git resolve/import and hard limits."""

from __future__ import annotations

import os
import socket
import subprocess
from pathlib import Path

import pytest

from app.config import Settings
from app.services.code_agent import git_importer
from app.services.code_agent.git_importer import (
    GitImportLimits,
    RestrictedGitImporter,
    RestrictedGitImportError,
)
from app.services.code_agent.network_policy import (
    RepositoryNetworkGuard,
    RepositoryNetworkPolicyError,
)
from app.services.code_agent.secret_store import DeployTokenLease


def _git(repository: Path, *args: str) -> str:
    environment = dict(os.environ)
    environment.update({
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_AUTHOR_NAME": "Test",
        "GIT_AUTHOR_EMAIL": "test@example.test",
        "GIT_COMMITTER_NAME": "Test",
        "GIT_COMMITTER_EMAIL": "test@example.test",
    })
    result = subprocess.run(
        ["git", "-C", str(repository), *args],
        check=True,
        capture_output=True,
        text=True,
        env=environment,
    )
    return result.stdout.strip()


def _repository(tmp_path: Path, files: dict[str, bytes] | None = None) -> tuple[Path, str]:
    repository = tmp_path / "source"
    repository.mkdir()
    _git(repository, "init", "-q", "-b", "main")
    for name, content in (files or {"README.md": b"hello"}).items():
        path = repository / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    _git(repository, "add", ".")
    _git(repository, "commit", "-q", "-m", "initial")
    return repository, _git(repository, "rev-parse", "HEAD")


def _limits(**overrides) -> GitImportLimits:
    values = {
        "max_download_bytes": 10 * 1024 * 1024,
        "max_unpacked_bytes": 10 * 1024 * 1024,
        "max_files": 100,
        "max_file_bytes": 1024 * 1024,
        "timeout_seconds": 10,
    }
    values.update(overrides)
    return GitImportLimits(**values)


def _importer(tmp_path: Path, **limit_overrides) -> RestrictedGitImporter:
    staging = tmp_path / "imports"
    staging.mkdir()
    return RestrictedGitImporter(
        staging_root=staging,
        limits=_limits(**limit_overrides),
    )


def test_import_limits_are_loaded_from_platform_settings():
    settings = Settings(
        _env_file=None,
        code_import_max_download_bytes=11,
        code_import_max_unpacked_bytes=12,
        code_import_max_files=13,
        code_import_max_file_bytes=14,
        code_import_timeout_seconds=15,
    )

    assert GitImportLimits.from_settings(settings) == GitImportLimits(
        max_download_bytes=11,
        max_unpacked_bytes=12,
        max_files=13,
        max_file_bytes=14,
        timeout_seconds=15,
    )


@pytest.mark.parametrize("requested_ref", ["main", "HEAD"])
def test_exact_ref_is_resolved_and_imported_without_checkout_side_effects(
    tmp_path,
    requested_ref,
):
    repository, commit = _repository(tmp_path)
    importer = _importer(tmp_path)

    result = importer.import_from_staging(
        repository,
        requested_ref=requested_ref,
        import_id=f"import-{requested_ref.lower()}",
    )
    imported = Path(result.repository_path)

    assert result.resolved_commit == commit
    assert (imported / "README.md").read_text(encoding="utf-8") == "hello"
    assert (imported / ".git").is_dir()
    assert result.hooks_executed is False
    assert result.submodules_initialized is False
    assert result.lfs_objects_fetched is False
    assert result.config_isolated is True


def test_source_hooks_and_credential_helpers_are_not_executed_or_copied(tmp_path):
    repository, _ = _repository(tmp_path)
    sentinel = tmp_path / "hook-ran"
    hook = repository / ".git" / "hooks" / "post-checkout"
    hook.write_text(f"#!/bin/sh\ntouch '{sentinel}'\n", encoding="utf-8")
    hook.chmod(0o755)
    _git(repository, "config", "credential.helper", "malicious-helper")
    importer = _importer(tmp_path)

    result = importer.import_from_staging(
        repository,
        requested_ref="HEAD",
        import_id="import-hooks",
    )
    imported = Path(result.repository_path)

    assert not sentinel.exists()
    assert "malicious-helper" not in (imported / ".git" / "config").read_text(encoding="utf-8")
    hooks = imported / ".git" / "hooks"
    assert not hooks.exists() or list(hooks.iterdir()) == []


def test_every_git_process_uses_allowlisted_environment_and_isolated_config(
    tmp_path,
    monkeypatch,
):
    repository, _ = _repository(tmp_path)
    importer = _importer(tmp_path)
    secret = "deploy-token-must-not-reach-git-process"
    malicious_global = tmp_path / "malicious-global.gitconfig"
    malicious_global.write_text("[credential]\n\thelper = malicious\n", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(malicious_global))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "0")
    monkeypatch.setenv("DEPLOY_TOKEN", secret)
    real_popen = git_importer.subprocess.Popen
    invocations = []

    def record_process(*args, **kwargs):
        invocations.append((args[0], kwargs["env"]))
        return real_popen(*args, **kwargs)

    monkeypatch.setattr(git_importer.subprocess, "Popen", record_process)

    importer.import_from_staging(
        repository,
        requested_ref="HEAD",
        import_id="import-isolated-config",
    )

    assert invocations
    for command, environment in invocations:
        assert environment["GIT_CONFIG_NOSYSTEM"] == "1"
        assert environment["GIT_CONFIG_GLOBAL"] == os.devnull
        assert environment["GIT_TERMINAL_PROMPT"] == "0"
        assert environment["GIT_LFS_SKIP_SMUDGE"] == "1"
        assert secret not in repr((command, environment))
        assert "core.hooksPath=/dev/null" in command
        assert "credential.helper=" in command


def test_submodule_gitlink_is_rejected_without_initializing_nested_repository(tmp_path):
    repository, commit = _repository(tmp_path)
    _git(repository, "update-index", "--add", "--cacheinfo", f"160000,{commit},deps/sub")
    _git(repository, "commit", "-q", "-m", "gitlink")
    importer = _importer(tmp_path)

    with pytest.raises(RestrictedGitImportError, match="repository_feature_unsupported"):
        importer.import_from_staging(
            repository,
            requested_ref="HEAD",
            import_id="import-submodule",
        )

    assert not (tmp_path / "imports" / "import-submodule").exists()


def test_git_lfs_pointer_is_rejected_without_running_smudge_filter(tmp_path):
    pointer = (
        b"version https://git-lfs.github.com/spec/v1\n"
        b"oid sha256:" + b"a" * 64 + b"\nsize 123\n"
    )
    repository, _ = _repository(tmp_path, {
        ".gitattributes": b"*.bin filter=lfs diff=lfs merge=lfs -text\n",
        "model.bin": pointer,
    })
    importer = _importer(tmp_path)

    with pytest.raises(RestrictedGitImportError, match="repository_feature_unsupported"):
        importer.import_from_staging(
            repository,
            requested_ref="HEAD",
            import_id="import-lfs",
        )

    assert not (tmp_path / "imports" / "import-lfs").exists()


@pytest.mark.parametrize(
    ("overrides", "files"),
    [
        ({"max_download_bytes": 1}, {"a.txt": b"a"}),
        ({"max_unpacked_bytes": 4}, {"a.txt": b"12345"}),
        ({"max_files": 1}, {"a.txt": b"a", "b.txt": b"b"}),
        ({"max_file_bytes": 4}, {"large.txt": b"12345"}),
    ],
)
def test_download_unpack_file_count_and_single_file_limits_clean_staging(
    tmp_path,
    overrides,
    files,
):
    repository, _ = _repository(tmp_path, files)
    importer = _importer(tmp_path, **overrides)

    with pytest.raises(RestrictedGitImportError, match="repository_limit_exceeded"):
        importer.import_from_staging(
            repository,
            requested_ref="HEAD",
            import_id="import-limit",
        )

    assert not (tmp_path / "imports" / "import-limit").exists()


@pytest.mark.parametrize("requested_ref", ["missing", "--upload-pack=evil", "main..other", "@{0}"])
def test_invalid_or_unavailable_ref_is_rejected_and_staging_is_cleaned(
    tmp_path,
    requested_ref,
):
    repository, _ = _repository(tmp_path)
    importer = _importer(tmp_path)

    with pytest.raises(RestrictedGitImportError, match="repository_ref_invalid"):
        importer.import_from_staging(
            repository,
            requested_ref=requested_ref,
            import_id="import-ref",
        )

    assert not (tmp_path / "imports" / "import-ref").exists()


def test_expired_deadline_prevents_git_import_and_leaves_no_staging(tmp_path):
    repository, _ = _repository(tmp_path)
    ticks = iter([0.0, 2.0, 2.0, 2.0])
    staging = tmp_path / "imports"
    staging.mkdir()
    importer = RestrictedGitImporter(
        staging_root=staging,
        limits=_limits(timeout_seconds=1),
        clock=lambda: next(ticks),
    )

    with pytest.raises(RestrictedGitImportError, match="repository_limit_exceeded"):
        importer.import_from_staging(
            repository,
            requested_ref="HEAD",
            import_id="import-timeout",
        )

    assert not (staging / "import-timeout").exists()


def test_deadline_kills_an_active_git_process_before_cleanup(tmp_path, monkeypatch):
    repository, _ = _repository(tmp_path)
    staging = tmp_path / "imports"
    staging.mkdir()
    ticks = iter([0.0, 0.0, 2.0])

    class HangingProcess:
        returncode = None
        killed = False

        def poll(self):
            return None

        def kill(self):
            self.killed = True
            self.returncode = -9

        def wait(self):
            return self.returncode

    process = HangingProcess()
    monkeypatch.setattr(git_importer.subprocess, "Popen", lambda *_args, **_kwargs: process)
    importer = RestrictedGitImporter(
        staging_root=staging,
        limits=_limits(timeout_seconds=1),
        clock=lambda: next(ticks),
    )

    with pytest.raises(RestrictedGitImportError, match="repository_limit_exceeded"):
        importer.import_from_staging(
            repository,
            requested_ref="HEAD",
            import_id="import-killed-timeout",
        )

    assert process.killed is True
    assert not (staging / "import-killed-timeout").exists()


def test_remote_http_is_revalidated_and_git_is_pinned_before_clone(tmp_path, monkeypatch):
    staging = tmp_path / "imports"
    staging.mkdir()
    dns_calls = []

    def resolver(host, port, **_kwargs):
        dns_calls.append((host, port))
        return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("10.20.1.8", port))]

    guard = RepositoryNetworkGuard(
        allowlist=["http://g.testskydata.com:80"],
        approved_internal_cidrs=["10.20.0.0/16"],
        resolver=resolver,
    )
    importer = RestrictedGitImporter(staging_root=staging, limits=_limits())
    captured = {}

    def stop_before_network(args, **kwargs):
        captured["args"] = args
        captured.update(kwargs)
        raise RestrictedGitImportError("repository_unreachable")

    monkeypatch.setattr(importer, "_run_git", stop_before_network)

    with pytest.raises(RestrictedGitImportError, match="repository_unreachable"):
        importer.import_remote(
            guard,
            "http://g.testskydata.com/system/dbt-gamestat-ck.git",
            requested_ref="main",
            import_id="remote-http",
        )

    assert dns_calls == [("g.testskydata.com", 80)]
    assert captured["allowed_protocols"] == "http"
    assert "http.followRedirects=false" in captured["extra_configs"]
    assert "http.curloptResolve=g.testskydata.com:80:10.20.1.8" in captured["extra_configs"]
    assert "http://g.testskydata.com:80/system/dbt-gamestat-ck.git" in captured["args"]


def test_remote_http_transport_proxy_uses_proxy_env_without_ip_pinning(tmp_path, monkeypatch):
    staging = tmp_path / "imports"
    staging.mkdir()

    def unavailable(*_args, **_kwargs):
        raise socket.gaierror("local dns unavailable")

    guard = RepositoryNetworkGuard(
        allowlist=["http://g.testskydata.com:2222"],
        resolver=unavailable,
        use_transport_proxy=True,
    )
    importer = RestrictedGitImporter(staging_root=staging, limits=_limits())
    captured = {}

    def stop_before_network(args, **kwargs):
        captured["args"] = args
        captured.update(kwargs)
        raise RestrictedGitImportError("repository_unreachable")

    monkeypatch.setattr(importer, "_run_git", stop_before_network)

    with pytest.raises(RestrictedGitImportError, match="repository_unreachable"):
        importer.import_remote(
            guard,
            "http://g.testskydata.com:2222/system/dbt-gamestat-ck.git",
            requested_ref="HEAD",
            import_id="remote-http-proxy",
            proxy_environment={"http_proxy": "http://127.0.0.1:7897"},
        )

    assert captured["allowed_protocols"] == "http"
    assert "http.followRedirects=false" in captured["extra_configs"]
    assert not any(
        config.startswith("http.curloptResolve=")
        for config in captured["extra_configs"]
    )
    assert captured["environment_overrides"] == {"http_proxy": "http://127.0.0.1:7897"}
    assert "http://g.testskydata.com:2222/system/dbt-gamestat-ck.git" in captured["args"]


def test_remote_deploy_token_uses_ephemeral_askpass_fd_without_argv_env_or_file_value(
    tmp_path,
    monkeypatch,
):
    staging = tmp_path / "imports"
    staging.mkdir()
    raw_secret = "deploy-token-never-persisted"
    guard = RepositoryNetworkGuard(
        allowlist=["https://git.example.test:443"],
        resolver=lambda *_args, **_kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("93.184.216.34", 443))
        ],
    )
    importer = RestrictedGitImporter(staging_root=staging, limits=_limits())
    captured = {}

    def stop_after_credential_channel(args, **kwargs):
        captured["args"] = args
        captured.update(kwargs)
        descriptor = kwargs["pass_fds"][0]
        captured["leased_value"] = os.read(descriptor, 4096)
        helper_path = Path(kwargs["environment_overrides"]["GIT_ASKPASS"])
        captured["helper"] = helper_path.read_text(encoding="utf-8")
        raise RestrictedGitImportError("repository_auth_failed")

    monkeypatch.setattr(importer, "_run_git", stop_after_credential_channel)
    lease = DeployTokenLease(raw_secret)
    with pytest.raises(RestrictedGitImportError, match="repository_auth_failed"):
        importer.import_remote(
            guard,
            "https://git.example.test/repository.git",
            requested_ref="main",
            import_id="remote-private",
            credential_lease=lease,
            auth_username="git-reader",
        )
    lease.close()

    serialized_process_contract = repr({
        "args": captured["args"],
        "environment": captured["environment_overrides"],
        "configs": captured["extra_configs"],
        "helper": captured["helper"],
    })
    assert captured["leased_value"] == raw_secret.encode() + b"\n"
    assert raw_secret not in serialized_process_contract
    assert captured["environment_overrides"]["CODE_AGENT_AUTH_USERNAME"] == "git-reader"
    assert lease.cleared is True
    assert not (staging / ".remote-private.askpass.py").exists()
    assert not (staging / "remote-private").exists()


def test_remote_policy_denial_occurs_before_git_process_creation(tmp_path, monkeypatch):
    staging = tmp_path / "imports"
    staging.mkdir()
    guard = RepositoryNetworkGuard(
        allowlist=["https://git.example.test:443"],
        resolver=lambda *_args, **_kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("127.0.0.1", 443))
        ],
    )
    importer = RestrictedGitImporter(staging_root=staging, limits=_limits())
    monkeypatch.setattr(
        git_importer.subprocess,
        "Popen",
        lambda *_args, **_kwargs: pytest.fail("Git must not start for a denied IP"),
    )

    with pytest.raises(RepositoryNetworkPolicyError, match="repository_network_policy_denied"):
        importer.import_remote(
            guard,
            "https://git.example.test/repo.git",
            requested_ref="main",
            import_id="remote-denied",
        )


def test_remote_ssh_requires_managed_host_keys_and_uses_pinned_address(tmp_path, monkeypatch):
    staging = tmp_path / "imports"
    staging.mkdir()
    known_hosts = tmp_path / "known_hosts"
    known_hosts.write_text("git.example.test ssh-ed25519 AAAATEST\n", encoding="utf-8")
    guard = RepositoryNetworkGuard(
        allowlist=["ssh://git.example.test:22"],
        resolver=lambda *_args, **_kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("93.184.216.34", 22))
        ],
    )
    importer = RestrictedGitImporter(staging_root=staging, limits=_limits())
    captured = {}

    def stop_before_network(args, **kwargs):
        captured.update(kwargs)
        raise RestrictedGitImportError("repository_unreachable")

    monkeypatch.setattr(importer, "_run_git", stop_before_network)

    with pytest.raises(RestrictedGitImportError, match="repository_network_policy_denied"):
        importer.import_remote(
            guard,
            "ssh://git.example.test/repo.git",
            requested_ref="main",
            import_id="ssh-without-host-keys",
        )
    with pytest.raises(RestrictedGitImportError, match="repository_unreachable"):
        importer.import_remote(
            guard,
            "ssh://git.example.test/repo.git",
            requested_ref="main",
            import_id="ssh-pinned",
            ssh_known_hosts_file=known_hosts,
        )

    command = captured["environment_overrides"]["GIT_SSH_COMMAND"]
    assert captured["allowed_protocols"] == "ssh"
    assert "StrictHostKeyChecking=yes" in command
    assert "Hostname=93.184.216.34" in command
    assert "HostKeyAlias=git.example.test" in command
    assert f"UserKnownHostsFile={known_hosts}" in command
