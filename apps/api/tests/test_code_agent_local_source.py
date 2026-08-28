"""OpenSpec task 3.3: canonical no-follow local repository staging."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from app.services.code_agent import local_source
from app.services.code_agent.local_source import (
    LocalRepositoryImporter,
    LocalRepositoryPolicyError,
)


def _importer(tmp_path, source_root):
    staging = tmp_path / "staging"
    staging.mkdir()
    return LocalRepositoryImporter(
        allowed_roots=[str(source_root)],
        staging_root=staging,
    )


def test_local_repository_is_copied_to_disjoint_staging_without_binding_source(tmp_path):
    source_root = tmp_path / "approved"
    repository = source_root / "repo"
    nested = repository / "models"
    nested.mkdir(parents=True)
    (repository / "README.md").write_text("original", encoding="utf-8")
    (nested / "events.sql").write_text("select 1", encoding="utf-8")
    importer = _importer(tmp_path, source_root)

    result = importer.import_to_staging(str(repository), import_id="import-1")
    staging = Path(result.staging_path)
    (repository / "README.md").write_text("changed", encoding="utf-8")

    assert staging != repository
    assert staging.is_dir() and not staging.is_symlink()
    assert (staging / "README.md").read_text(encoding="utf-8") == "original"
    assert (staging / "models" / "events.sql").read_text(encoding="utf-8") == "select 1"
    assert result.file_count == 2
    assert result.byte_count == len(b"originalselect 1")
    assert result.source_bound is False


@pytest.mark.parametrize("kind", ["outside", "traversal", "file_url", "relative"])
def test_arbitrary_and_traversal_paths_are_rejected_before_staging(tmp_path, kind):
    source_root = tmp_path / "approved"
    repository = source_root / "repo"
    repository.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    importer = _importer(tmp_path, source_root)
    locator = {
        "outside": str(outside),
        "traversal": f"{repository}/../repo",
        "file_url": f"file://{repository}",
        "relative": "repo",
    }[kind]

    with pytest.raises(LocalRepositoryPolicyError, match="repository_source_not_allowed"):
        importer.import_to_staging(locator, import_id=f"import-{kind}")

    assert list((tmp_path / "staging").iterdir()) == []


def test_source_directory_symlink_is_rejected(tmp_path):
    source_root = tmp_path / "approved"
    actual = source_root / "actual"
    actual.mkdir(parents=True)
    link = source_root / "repo"
    link.symlink_to(actual, target_is_directory=True)
    importer = _importer(tmp_path, source_root)

    with pytest.raises(LocalRepositoryPolicyError):
        importer.import_to_staging(str(link), import_id="import-link")


def test_nested_symlink_never_reads_outside_content_and_cleans_partial_staging(tmp_path):
    source_root = tmp_path / "approved"
    repository = source_root / "repo"
    repository.mkdir(parents=True)
    (repository / "safe.txt").write_text("safe", encoding="utf-8")
    outside_secret = tmp_path / "outside-secret.txt"
    outside_secret.write_text("must-not-be-read", encoding="utf-8")
    (repository / "escape.txt").symlink_to(outside_secret)
    importer = _importer(tmp_path, source_root)

    with pytest.raises(LocalRepositoryPolicyError):
        importer.import_to_staging(str(repository), import_id="import-symlink")

    assert not (tmp_path / "staging" / "import-symlink").exists()


def test_path_replacement_with_symlink_during_copy_is_rejected_before_open(
    tmp_path,
    monkeypatch,
):
    source_root = tmp_path / "approved"
    repository = source_root / "repo"
    repository.mkdir(parents=True)
    victim = repository / "victim.txt"
    victim.write_text("safe", encoding="utf-8")
    outside_secret = tmp_path / "outside-secret.txt"
    outside_secret.write_text("must-not-be-read", encoding="utf-8")
    importer = _importer(tmp_path, source_root)
    original_entries = local_source._directory_entries
    replaced = False

    def replace_after_scan(directory_fd):
        nonlocal replaced
        entries = original_entries(directory_fd)
        if not replaced and "victim.txt" in entries:
            replaced = True
            victim.unlink()
            victim.symlink_to(outside_secret)
        return entries

    monkeypatch.setattr(local_source, "_directory_entries", replace_after_scan)

    with pytest.raises(LocalRepositoryPolicyError):
        importer.import_to_staging(str(repository), import_id="import-race")

    assert replaced is True
    assert not (tmp_path / "staging" / "import-race").exists()


def test_approved_root_replacement_after_importer_initialization_is_rejected(tmp_path):
    source_root = tmp_path / "approved"
    repository = source_root / "repo"
    repository.mkdir(parents=True)
    (repository / "original.txt").write_text("original", encoding="utf-8")
    importer = _importer(tmp_path, source_root)
    original_root = tmp_path / "approved-original"
    source_root.rename(original_root)
    replacement_repository = source_root / "repo"
    replacement_repository.mkdir(parents=True)
    (replacement_repository / "replacement.txt").write_text(
        "must-not-be-read",
        encoding="utf-8",
    )

    with pytest.raises(LocalRepositoryPolicyError):
        importer.import_to_staging(
            str(replacement_repository),
            import_id="import-root-race",
        )

    assert not (tmp_path / "staging" / "import-root-race").exists()


def test_new_repository_under_same_approved_root_keeps_root_identity_valid(tmp_path):
    source_root = tmp_path / "approved"
    source_root.mkdir()
    importer = _importer(tmp_path, source_root)
    repository = source_root / "created-after-importer"
    repository.mkdir()
    (repository / "README.md").write_text("allowed", encoding="utf-8")

    result = importer.import_to_staging(str(repository), import_id="import-new-repo")

    assert (Path(result.staging_path) / "README.md").read_text(encoding="utf-8") == "allowed"


def test_staging_and_approved_source_roots_must_be_disjoint(tmp_path):
    source_root = tmp_path / "approved"
    staging = source_root / "staging"
    staging.mkdir(parents=True)

    with pytest.raises(LocalRepositoryPolicyError):
        LocalRepositoryImporter(
            allowed_roots=[str(source_root)],
            staging_root=staging,
        )


def test_non_regular_source_entry_is_rejected_without_blocking(tmp_path):
    source_root = tmp_path / "approved"
    repository = source_root / "repo"
    repository.mkdir(parents=True)
    fifo = repository / "pipe"
    os.mkfifo(fifo)
    importer = _importer(tmp_path, source_root)

    with pytest.raises(LocalRepositoryPolicyError):
        importer.import_to_staging(str(repository), import_id="import-fifo")

    assert not (tmp_path / "staging" / "import-fifo").exists()


@pytest.mark.parametrize("import_id", ["../escape", "", "/absolute", "bad/id"])
def test_import_id_cannot_escape_staging_root(tmp_path, import_id):
    source_root = tmp_path / "approved"
    repository = source_root / "repo"
    repository.mkdir(parents=True)
    importer = _importer(tmp_path, source_root)

    with pytest.raises(LocalRepositoryPolicyError):
        importer.import_to_staging(str(repository), import_id=import_id)
