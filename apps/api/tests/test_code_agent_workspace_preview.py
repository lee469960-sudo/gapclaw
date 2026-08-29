from pathlib import Path

import pytest

from app.services.code_agent.workspace_preview import (
    WorkspacePreviewError,
    list_workspace,
    read_workspace_file,
    git_workspace_status,
)


def test_workspace_preview_lists_and_reads_only_safe_text(tmp_path: Path):
    (tmp_path / "models").mkdir()
    (tmp_path / "models" / "a.sql").write_text("select 1\n", encoding="utf-8")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".claude").mkdir()
    listing = list_workspace(tmp_path)
    assert listing["entries"][0]["path"] == "models"
    assert read_workspace_file(tmp_path, "models/a.sql")["content"] == "select 1\n"
    with pytest.raises(WorkspacePreviewError, match="workspace_path_not_allowed"):
        read_workspace_file(tmp_path, ".git/config")


def test_workspace_preview_rejects_escape_and_binary(tmp_path: Path):
    (tmp_path / "x.bin").write_bytes(b"\x00\xff")
    with pytest.raises(WorkspacePreviewError, match="workspace_path_not_allowed"):
        read_workspace_file(tmp_path, "../x")
    with pytest.raises(WorkspacePreviewError, match="workspace_binary_or_unreadable"):
        read_workspace_file(tmp_path, "x.bin")


def test_git_workspace_status_is_scoped_to_run_workspace(tmp_path: Path):
    import subprocess
    subprocess.run(["git", "-C", str(tmp_path), "init", "-q"], check=True)
    (tmp_path / "changed.sql").write_text("select 1", encoding="utf-8")
    (tmp_path / ".git" / "runtime-state").write_text("internal", encoding="utf-8")
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / "runtime").write_text("ignored", encoding="utf-8")
    result = git_workspace_status(tmp_path)
    assert result["clean"] is False
    assert {item["path"] for item in result["changed_files"]} == {"changed.sql"}


def test_git_workspace_status_uses_run_baseline_instead_of_parent_or_snapshot_index(tmp_path: Path):
    import hashlib
    import json
    import subprocess

    subprocess.run(["git", "-C", str(tmp_path), "init", "-q"], check=True)
    (tmp_path / "model.sql").write_text("select 1\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", "model.sql"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-qm", "base"], check=True)
    baseline_digest = hashlib.sha256(b"select 1\n").hexdigest()
    control = tmp_path.parent / "control"
    control.mkdir()
    (control / "baseline.json").write_text(
        json.dumps({"model.sql": f"file:{baseline_digest}"}), encoding="utf-8"
    )
    (tmp_path / "model.sql").write_text("select 2\n", encoding="utf-8")
    result = git_workspace_status(tmp_path)
    assert result["branch"] == "master" or result["branch"] == "main"
    assert result["clean"] is False
    assert result["changed_files"] == [{"status": " M", "path": "model.sql"}]


def test_git_workspace_status_rejects_an_ancestor_repository(tmp_path: Path):
    import subprocess

    subprocess.run(["git", "-C", str(tmp_path), "init", "-q"], check=True)
    workspace = tmp_path / "run" / "workspace"
    workspace.mkdir(parents=True)
    (workspace / "model.sql").write_text("select 1\n", encoding="utf-8")
    with pytest.raises(WorkspacePreviewError, match="workspace_mount_invalid"):
        git_workspace_status(workspace)


def test_workspace_preview_excludes_runtime_and_git_metadata_from_tree(tmp_path: Path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("print('ok')\n", encoding="utf-8")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text("[core]\n", encoding="utf-8")
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / "CLAUDE.md").write_text("runtime instructions", encoding="utf-8")

    listing = list_workspace(tmp_path, depth=2)
    paths = {entry["path"] for entry in listing["entries"]}

    assert paths == {"src"}
    assert all(not path.startswith((".git", ".claude")) for path in paths)
    with pytest.raises(WorkspacePreviewError, match="workspace_path_not_allowed"):
        read_workspace_file(tmp_path, ".claude/CLAUDE.md")


def test_workspace_preview_requires_git_mount(tmp_path: Path):
    (tmp_path / "file.txt").write_text("x", encoding="utf-8")
    with pytest.raises(WorkspacePreviewError, match="workspace_mount_invalid"):
        git_workspace_status(tmp_path)
