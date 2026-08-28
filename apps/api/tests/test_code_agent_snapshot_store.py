"""OpenSpec task 3.5: sanitized content-addressed source snapshots."""

from __future__ import annotations

import os
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

import pytest

from app.services.code_agent.git_importer import GitImportLimits, RestrictedGitImporter
from app.services.code_agent.snapshot_store import SnapshotSealError, SourceSnapshotStore


def _git(repository: Path, *args: str) -> str:
    environment = dict(os.environ)
    environment.update({
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_AUTHOR_NAME": "Test",
        "GIT_AUTHOR_EMAIL": "test@example.test",
        "GIT_COMMITTER_NAME": "Test",
        "GIT_COMMITTER_EMAIL": "test@example.test",
    })
    return subprocess.run(
        ["git", "-C", str(repository), *args],
        check=True,
        capture_output=True,
        text=True,
        env=environment,
    ).stdout.strip()


def _source_repository(tmp_path: Path) -> tuple[Path, str]:
    source = tmp_path / "source"
    source.mkdir()
    _git(source, "init", "-q", "-b", "main")
    (source / "README.md").write_text("baseline\n", encoding="utf-8")
    (source / "models").mkdir()
    (source / "models" / "events.sql").write_text("select 1\n", encoding="utf-8")
    _git(source, "add", ".")
    _git(source, "commit", "-q", "-m", "baseline")
    return source, _git(source, "rev-parse", "HEAD")


def _restricted_import(tmp_path: Path, source: Path, import_id: str) -> tuple[Path, str]:
    imports = tmp_path / "imports"
    imports.mkdir(exist_ok=True)
    importer = RestrictedGitImporter(
        staging_root=imports,
        limits=GitImportLimits(
            max_download_bytes=10 * 1024 * 1024,
            max_unpacked_bytes=10 * 1024 * 1024,
            max_files=100,
            max_file_bytes=1024 * 1024,
            timeout_seconds=10,
        ),
    )
    result = importer.import_from_staging(
        source,
        requested_ref="HEAD",
        import_id=import_id,
    )
    return Path(result.repository_path), result.resolved_commit


def _store(tmp_path: Path) -> SourceSnapshotStore:
    snapshots = tmp_path / "snapshots"
    snapshots.mkdir(exist_ok=True)
    return SourceSnapshotStore(snapshots)


def _writable_copy(snapshot: Path, destination: Path) -> Path:
    shutil.copytree(snapshot, destination)
    for path in [destination, *destination.rglob("*")]:
        if not path.is_symlink():
            path.chmod(0o700 if path.is_dir() else 0o600)
    return destination


def test_sealed_snapshot_has_no_remote_hooks_helpers_alternates_or_moving_refs(tmp_path):
    source, _ = _source_repository(tmp_path)
    imported, commit = _restricted_import(tmp_path, source, "import-a")
    _git(imported, "update-ref", "refs/heads/moving", commit)
    (imported / ".git" / "hooks").mkdir(exist_ok=True)
    (imported / ".git" / "hooks" / "post-checkout").write_text("malicious", encoding="utf-8")
    alternates = imported / ".git" / "objects" / "info" / "alternates"
    alternates.write_text(str(tmp_path / "outside-objects"), encoding="utf-8")
    with (imported / ".git" / "config").open("a", encoding="utf-8") as handle:
        handle.write("[credential]\n\thelper = malicious\n")
    store = _store(tmp_path)

    sealed = store.seal(imported, resolved_commit=commit)
    snapshot = Path(sealed.storage_path)
    config = (snapshot / ".git" / "config").read_text(encoding="utf-8")

    assert _git(snapshot, "remote") == ""
    assert _git(snapshot, "for-each-ref", "--format=%(refname)") == "refs/heads/snapshot"
    assert _git(snapshot, "rev-parse", "HEAD") == commit
    assert list((snapshot / ".git" / "hooks").iterdir()) == []
    assert not (snapshot / ".git" / "objects" / "info" / "alternates").exists()
    assert "credential" not in config
    assert "helper" not in config
    assert "remote" not in config
    store.verify(snapshot, expected_hash=sealed.content_hash, expected_commit=commit)


def test_snapshot_retains_status_diff_and_log_in_a_materialized_copy(tmp_path):
    source, _ = _source_repository(tmp_path)
    imported, commit = _restricted_import(tmp_path, source, "import-tools")
    sealed = _store(tmp_path).seal(imported, resolved_commit=commit)
    workspace = _writable_copy(Path(sealed.storage_path), tmp_path / "workspace")

    assert _git(workspace, "status", "--porcelain=v1") == ""
    assert commit in _git(workspace, "log", "-1", "--format=%H")
    (workspace / "README.md").write_text("changed\n", encoding="utf-8")
    assert "README.md" in _git(workspace, "status", "--porcelain=v1")
    assert "changed" in _git(workspace, "diff", "--", "README.md")


def test_same_exact_input_deduplicates_to_one_content_address(tmp_path):
    source, _ = _source_repository(tmp_path)
    first_import, commit = _restricted_import(tmp_path, source, "import-first")
    second_import, _ = _restricted_import(tmp_path, source, "import-second")
    store = _store(tmp_path)

    first = store.seal(first_import, resolved_commit=commit)
    second = store.seal(second_import, resolved_commit=commit)

    assert first.content_hash == second.content_hash
    assert first.storage_path == second.storage_path
    assert first.deduplicated is False
    assert second.deduplicated is True
    assert len([path for path in Path(first.storage_path).parent.iterdir() if not path.name.startswith(".")]) == 1


@pytest.mark.parametrize("target", ["README.md", ".git/config", ".git/snapshot-seal.json"])
def test_snapshot_content_or_seal_metadata_tampering_is_detected(tmp_path, target):
    source, _ = _source_repository(tmp_path)
    imported, commit = _restricted_import(tmp_path, source, f"import-{target.replace('/', '-')}")
    store = _store(tmp_path)
    sealed = store.seal(imported, resolved_commit=commit)
    snapshot = Path(sealed.storage_path)
    victim = snapshot / target
    victim.chmod(0o600)
    victim.write_text("tampered", encoding="utf-8")

    with pytest.raises(SnapshotSealError, match="snapshot_hash_mismatch"):
        store.verify(
            snapshot,
            expected_hash=sealed.content_hash,
            expected_commit=commit,
        )


def test_snapshot_permission_only_tampering_is_detected(tmp_path):
    source, _ = _source_repository(tmp_path)
    imported, commit = _restricted_import(tmp_path, source, "import-permissions")
    store = _store(tmp_path)
    sealed = store.seal(imported, resolved_commit=commit)
    snapshot = Path(sealed.storage_path)
    victim = snapshot / "README.md"
    victim.chmod(0o600)

    with pytest.raises(SnapshotSealError, match="snapshot_hash_mismatch"):
        store.verify(
            snapshot,
            expected_hash=sealed.content_hash,
            expected_commit=commit,
        )


def test_abandoned_staging_cleanup_never_removes_content_addressed_snapshot(tmp_path):
    store = _store(tmp_path)
    abandoned = Path(store.root) / ".seal-abandoned"
    abandoned.mkdir()
    (abandoned / "partial").write_text("partial", encoding="utf-8")
    old_time = datetime(2026, 1, 1).timestamp()
    os.utime(abandoned, (old_time, old_time))
    active = Path(store.root) / ".seal-active"
    active.mkdir()
    content = Path(store.root) / ("a" * 64)
    content.mkdir()

    removed = store.cleanup_abandoned_staging(older_than=datetime(2026, 8, 23))

    assert removed == (".seal-abandoned",)
    assert not abandoned.exists()
    assert active.exists()
    assert content.exists()
