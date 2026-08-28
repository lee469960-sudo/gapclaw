"""OpenSpec task 6.2: in-runner code tool helper (read/search/edit/git/test/shell).

The helper is the trusted, dependency-free program baked into the runner image. It
must be impossible to escape the Workspace: absolute paths, ``..``, symlinks,
shell concatenation and Git write operations all fail closed, writes are atomic,
and every output is bounded with a ``truncated`` flag.
"""

from __future__ import annotations

import json
import subprocess
import sys

import pytest

from app.services.code_agent import runner_helper as helper
from app.services.code_agent.runner_protocol import RUNNER_HELPER_VERSION


def _request(tool, output_limit_bytes=100_000, **params):
    return {
        "run_id": "run1",
        "container_id": "container1",
        "tool": tool,
        "parameters": params,
        "request_id": "req1",
        "output_limit_bytes": output_limit_bytes,
        "helper_version": RUNNER_HELPER_VERSION,
        "schema_version": 1,
    }


def _make_workspace(tmp_path):
    ws = tmp_path / "workspace"
    (ws / "src").mkdir(parents=True)
    (ws / "src" / "main.py").write_text("print('hi')\n")
    (ws / "README.md").write_text("a readme\nwith a needle here\n")
    return ws


def _make_git_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "t@example.com"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "t"], check=True)
    (repo / "a.txt").write_text("hello\n")
    subprocess.run(["git", "-C", str(repo), "add", "a.txt"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "init"], check=True)
    return repo


# --- version pin ----------------------------------------------------------------


def test_helper_version_matches_protocol():
    assert helper.HELPER_VERSION == RUNNER_HELPER_VERSION


# --- read -----------------------------------------------------------------------


def test_read_within_workspace(tmp_path):
    ws = _make_workspace(tmp_path)
    resp = helper.run_tool(_request("read", path="src/main.py"), workspace=str(ws))
    assert resp["error"] == ""
    assert resp["exit_code"] == 0
    assert "print('hi')" in resp["stdout"]


def test_read_rejects_absolute_path(tmp_path):
    ws = _make_workspace(tmp_path)
    outside = tmp_path / "outside.txt"
    outside.write_text("secret")
    resp = helper.run_tool(_request("read", path=str(outside)), workspace=str(ws))
    assert resp["error"] == "code_path_not_allowed"


def test_read_rejects_parent_traversal(tmp_path):
    ws = _make_workspace(tmp_path)
    secret = tmp_path / "secret.txt"
    secret.write_text("secret")
    resp = helper.run_tool(_request("read", path="../secret.txt"), workspace=str(ws))
    assert resp["error"] == "code_path_not_allowed"


def test_read_rejects_symlink_escape(tmp_path):
    ws = _make_workspace(tmp_path)
    outside = tmp_path / "outside.txt"
    outside.write_text("secret")
    (ws / "link.txt").symlink_to(outside)
    resp = helper.run_tool(_request("read", path="link.txt"), workspace=str(ws))
    assert resp["error"] == "code_path_not_allowed"


def test_read_rejects_symlink_dir_escape(tmp_path):
    ws = _make_workspace(tmp_path)
    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()
    (outside_dir / "x.txt").write_text("secret")
    (ws / "linkdir").symlink_to(outside_dir)
    resp = helper.run_tool(_request("read", path="linkdir/x.txt"), workspace=str(ws))
    assert resp["error"] == "code_path_not_allowed"


def test_read_truncates_large_file(tmp_path):
    ws = _make_workspace(tmp_path)
    big = "x" * 5000
    (ws / "big.txt").write_text(big)
    resp = helper.run_tool(
        _request("read", path="big.txt", output_limit_bytes=100), workspace=str(ws)
    )
    assert resp["truncated"] is True
    assert len(resp["stdout"]) == 100


# --- edit -----------------------------------------------------------------------


def test_edit_writes_atomically(tmp_path):
    ws = _make_workspace(tmp_path)
    resp = helper.run_tool(
        _request("edit", path="src/main.py", content="print('new')\n"), workspace=str(ws)
    )
    assert resp["error"] == ""
    assert (ws / "src" / "main.py").read_text() == "print('new')\n"
    assert resp["changed_files"] == ["src/main.py"]
    # no leftover temp file from the atomic write
    assert [p.name for p in (ws / "src").iterdir()] == ["main.py"]


def test_edit_rejects_absolute_path(tmp_path):
    ws = _make_workspace(tmp_path)
    outside = tmp_path / "outside.txt"
    resp = helper.run_tool(
        _request("edit", path=str(outside), content="pwn"), workspace=str(ws)
    )
    assert resp["error"] == "code_path_not_allowed"
    assert not outside.exists()


def test_edit_rejects_parent_traversal(tmp_path):
    ws = _make_workspace(tmp_path)
    resp = helper.run_tool(
        _request("edit", path="../pwn.txt", content="pwn"), workspace=str(ws)
    )
    assert resp["error"] == "code_path_not_allowed"
    assert not (tmp_path / "pwn.txt").exists()


def test_edit_rejects_symlink_target(tmp_path):
    ws = _make_workspace(tmp_path)
    outside = tmp_path / "outside.txt"
    outside.write_text("original")
    (ws / "link.txt").symlink_to(outside)
    resp = helper.run_tool(
        _request("edit", path="link.txt", content="pwn"), workspace=str(ws)
    )
    assert resp["error"] == "code_path_not_allowed"
    assert outside.read_text() == "original"


def test_edit_rejects_oversized_content(tmp_path):
    ws = _make_workspace(tmp_path)
    resp = helper.run_tool(
        _request("edit", path="big.txt", content="x" * (helper.MAX_EDIT_BYTES + 1)),
        workspace=str(ws),
    )
    assert resp["error"] == "code_tool_invalid_input"
    assert not (ws / "big.txt").exists()


# --- search ---------------------------------------------------------------------


def test_search_finds_matches(tmp_path):
    ws = _make_workspace(tmp_path)
    resp = helper.run_tool(_request("search", query="needle"), workspace=str(ws))
    assert resp["error"] == ""
    assert "README.md:2:" in resp["stdout"]


def test_search_scopes_to_path(tmp_path):
    ws = _make_workspace(tmp_path)
    resp = helper.run_tool(_request("search", query="needle", path="src"), workspace=str(ws))
    assert resp["error"] == ""
    assert "needle" not in resp["stdout"]


def test_search_rejects_symlink_root_escape(tmp_path):
    ws = _make_workspace(tmp_path)
    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()
    (outside_dir / "x.txt").write_text("needle secret")
    (ws / "linkdir").symlink_to(outside_dir)
    resp = helper.run_tool(_request("search", query="needle", path="linkdir"), workspace=str(ws))
    assert resp["error"] == "code_path_not_allowed"


# --- git ------------------------------------------------------------------------


def test_git_status_on_clean_repo(tmp_path):
    repo = _make_git_repo(tmp_path)
    resp = helper.run_tool(
        _request("git", operation="status"), workspace=str(repo), git_dir=str(repo / ".git")
    )
    assert resp["error"] == ""
    assert resp["exit_code"] == 0


def test_git_status_uses_workspace_git_by_default(tmp_path):
    repo = _make_git_repo(tmp_path)
    resp = helper.run_tool(_request("git", operation="status"), workspace=str(repo))
    assert resp["error"] == ""
    assert resp["exit_code"] == 0


def test_git_status_shows_modified_file(tmp_path):
    repo = _make_git_repo(tmp_path)
    (repo / "a.txt").write_text("changed\n")
    resp = helper.run_tool(
        _request("git", operation="status"), workspace=str(repo), git_dir=str(repo / ".git")
    )
    assert "a.txt" in resp["stdout"]


def test_git_log_lists_commit(tmp_path):
    repo = _make_git_repo(tmp_path)
    resp = helper.run_tool(
        _request("git", operation="log"), workspace=str(repo), git_dir=str(repo / ".git")
    )
    assert resp["error"] == ""
    assert "init" in resp["stdout"]


def test_git_diff_empty_on_clean_repo(tmp_path):
    repo = _make_git_repo(tmp_path)
    resp = helper.run_tool(
        _request("git", operation="diff"), workspace=str(repo), git_dir=str(repo / ".git")
    )
    assert resp["error"] == ""
    assert resp["stdout"].strip() == ""


def test_git_rejects_write_operation(tmp_path):
    repo = _make_git_repo(tmp_path)
    resp = helper.run_tool(
        _request("git", operation="commit"), workspace=str(repo), git_dir=str(repo / ".git")
    )
    assert resp["error"] == "code_git_write_rejected"


# --- shell / test: no shell concatenation ---------------------------------------


def test_shell_runs_fixed_command_without_injection(tmp_path):
    ws = _make_workspace(tmp_path)
    resp = helper.run_tool(
        _request("shell", command="echo pwned > pwned.txt"), workspace=str(ws)
    )
    assert resp["error"] == ""
    # the '>' is a literal argument, not a redirect — no file is created
    assert not (ws / "pwned.txt").exists()
    assert "pwned > pwned.txt" in resp["stdout"]


def test_shell_rejects_git_commit_even_if_dispatched(tmp_path):
    repo = _make_git_repo(tmp_path)
    resp = helper.run_tool(_request("shell", command="git commit"), workspace=str(repo))
    assert resp["error"] == "code_git_write_rejected"


def test_test_runs_command(tmp_path):
    ws = _make_workspace(tmp_path)
    (ws / "t.txt").write_text("1")
    resp = helper.run_tool(
        _request("test", command="python3 -c 'print(2 + 2)'"), workspace=str(ws)
    )
    assert resp["error"] == ""
    assert resp["stdout"].strip() == "4"


def test_command_exit_code_propagated(tmp_path):
    ws = _make_workspace(tmp_path)
    resp = helper.run_tool(_request("shell", command="python3 -c 'import sys; sys.exit(3)'"), workspace=str(ws))
    assert resp["error"] == ""
    assert resp["exit_code"] == 3


def test_command_timeout_fails_closed(tmp_path):
    ws = _make_workspace(tmp_path)
    resp = helper.run_tool(
        _request("shell", command="python3 -c 'import time; time.sleep(5)'"),
        workspace=str(ws),
        timeout=1,
    )
    assert resp["error"] == "runner_command_timed_out"


def test_large_output_truncated(tmp_path):
    ws = _make_workspace(tmp_path)
    resp = helper.run_tool(
        _request("shell", command="python3 -c 'print(\"x\" * 200000)'", output_limit_bytes=1000),
        workspace=str(ws),
    )
    assert resp["truncated"] is True
    assert len(resp["stdout"]) == 1000


# --- dispatch / fail-closed -----------------------------------------------------


def test_unknown_tool_rejected(tmp_path):
    ws = _make_workspace(tmp_path)
    resp = helper.run_tool(_request("exec", command="ls"), workspace=str(ws))
    assert resp["error"] == "code_tool_unknown"
    assert resp["exit_code"] == 1


def test_missing_required_parameter_rejected(tmp_path):
    ws = _make_workspace(tmp_path)
    resp = helper.run_tool(_request("test"), workspace=str(ws))
    assert resp["error"] == "code_tool_invalid_input"


def test_main_round_trips_via_stdin(tmp_path, monkeypatch):
    ws = _make_workspace(tmp_path)
    monkeypatch.setenv("CODE_AGENT_WORKSPACE", str(ws))
    monkeypatch.setenv("CODE_AGENT_GIT_DIR", str(tmp_path / "nonexistent.git"))
    payload = json.dumps(_request("read", path="src/main.py"))
    proc = subprocess.run(
        [sys.executable, "-m", "app.services.code_agent.runner_helper"],
        input=payload,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0
    resp = json.loads(proc.stdout)
    assert resp["error"] == ""
    assert "print('hi')" in resp["stdout"]
