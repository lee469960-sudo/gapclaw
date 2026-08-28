"""Self-contained CodeAgent runner helper (stdlib only, no ``app.*`` imports).

This module is baked into the trusted runner image and executed inside the
container once per tool call: it reads a single ``RunnerToolRequest`` JSON line on
stdin and writes a single ``RunnerToolResponse`` JSON line to stdout. It performs
no shell concatenation — read/search/edit use Workspace-relative fd containment
(``O_NOFOLLOW``), edit uses an atomic temp-file + rename, git only runs the frozen
status/diff/log commands, and every output is bounded with a ``truncated`` flag.

The version string below must stay in sync with
``app.services.code_agent.runner_protocol.RUNNER_HELPER_VERSION`` (asserted in the
protocol test).
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
import tempfile

HELPER_VERSION = "1"

DEFAULT_WORKSPACE = "/workspace"
DEFAULT_GIT_DIR = "/workspace/.git"
DEFAULT_OUTPUT_LIMIT = 100_000
DEFAULT_TIMEOUT = 1800
MAX_READ_BYTES = 2_000_000
MAX_EDIT_BYTES = 10_000_000
MAX_SEARCH_FILES = 10_000
MAX_SEARCH_MATCHES = 200

# Frozen read-only Git operations only; the exact argv is fixed and never
# assembled from request input beyond selecting one of these three.
GIT_OPERATIONS = {
    "status": ["status", "--short", "--untracked-files=all"],
    "diff": ["diff", "--no-ext-diff", "--no-textconv", "HEAD", "--"],
    "log": ["log", "-n", "20", "--oneline", "HEAD"],
}


class HelperError(Exception):
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


def _split_rel(relpath: str) -> list[str]:
    """Reject absolute, empty, ``.`` or ``..`` path components."""
    raw = str(relpath or "").replace("\\", "/")
    if not raw or raw.startswith("/"):
        raise HelperError("code_path_not_allowed")
    parts: list[str] = []
    for part in raw.split("/"):
        if part in ("", ".", ".."):
            raise HelperError("code_path_not_allowed")
        parts.append(part)
    if not parts:
        raise HelperError("code_path_not_allowed")
    return parts


def _walk_parent(workspace_fd: int, relpath: str) -> tuple[int, str]:
    """Return an owned parent-directory fd and leaf name, never following symlinks.

    The returned fd is always caller-owned (a dup of ``workspace_fd`` for a
    leaf-only path), so callers may close it unconditionally without double-closing
    the workspace fd.
    """
    parts = _split_rel(relpath)
    fd = os.dup(workspace_fd)
    for part in parts[:-1]:
        nfd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
        os.close(fd)
        fd = nfd
    return fd, parts[-1]


def _read(workspace: str, relpath: str, limit: int) -> tuple[str, bool]:
    workspace_fd = os.open(workspace, os.O_RDONLY | os.O_DIRECTORY)
    try:
        parent_fd, name = _walk_parent(workspace_fd, relpath)
        try:
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent_fd)
        finally:
            os.close(parent_fd)
        try:
            data = os.read(fd, min(limit, MAX_READ_BYTES) + 1)
        finally:
            os.close(fd)
        truncated = len(data) > min(limit, MAX_READ_BYTES)
        return data[: min(limit, MAX_READ_BYTES)].decode("utf-8", errors="replace"), truncated
    except FileNotFoundError:
        raise HelperError("code_file_not_found")
    except NotADirectoryError:
        raise HelperError("code_path_not_allowed")
    except OSError:
        raise HelperError("code_path_not_allowed")
    finally:
        os.close(workspace_fd)


def _edit(workspace: str, relpath: str, content: str, _limit: int) -> int:
    data = content.encode("utf-8")
    if len(data) > MAX_EDIT_BYTES:
        raise HelperError("code_tool_invalid_input")
    workspace_fd = os.open(workspace, os.O_RDONLY | os.O_DIRECTORY)
    try:
        parent_fd, name = _walk_parent(workspace_fd, relpath)
        try:
            try:
                st = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
            except FileNotFoundError:
                st = None
            if st is not None and st.st_mode & 0o170000 == 0o120000:
                raise HelperError("code_path_not_allowed")
            tmp = f".{name}.tmp.{os.urandom(6).hex()}"
            fd = os.open(
                tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644, dir_fd=parent_fd
            )
            try:
                os.write(fd, data)
                os.fsync(fd)
            finally:
                os.close(fd)
            os.replace(tmp, name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
            return len(data)
        finally:
            os.close(parent_fd)
    except HelperError:
        raise
    except OSError:
        raise HelperError("code_path_not_allowed")
    finally:
        os.close(workspace_fd)


def _safe_root(workspace: str, relpath: str) -> str:
    """Validate no-symlink containment of a directory, then return its path."""
    if str(relpath or "") in ("", "."):
        return os.path.realpath(workspace)
    parts = _split_rel(relpath)
    root = os.path.realpath(workspace)
    fds = [os.open(root, os.O_RDONLY | os.O_DIRECTORY)]
    try:
        for part in parts:
            fds.append(
                os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fds[-1])
            )
    except OSError:
        raise HelperError("code_path_not_allowed")
    finally:
        for fd in reversed(fds):
            os.close(fd)
    return os.path.join(root, *parts)


def _search(workspace: str, query: str, relpath: str, limit: int) -> tuple[str, bool]:
    if not str(query or ""):
        raise HelperError("code_tool_invalid_input")
    root = _safe_root(workspace, relpath)
    needle = query.lower()
    matches: list[str] = []
    if os.path.isfile(root) and not os.path.islink(root):
        candidates = [root]
    else:
        candidates = []
        for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
            dirnames[:] = [
                d for d in dirnames if not os.path.islink(os.path.join(dirpath, d))
            ]
            for filename in filenames:
                candidates.append(os.path.join(dirpath, filename))
            if len(candidates) >= MAX_SEARCH_FILES:
                break
    for path in candidates:
        if os.path.islink(path) or not os.path.isfile(path):
            continue
        try:
            if os.path.getsize(path) > MAX_READ_BYTES:
                continue
        except OSError:
            continue
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                for line_no, line in enumerate(fh, 1):
                    if needle in line.lower():
                        matches.append(
                            f"{os.path.relpath(path, workspace)}:{line_no}:{line[:300]}"
                        )
                        if len(matches) >= MAX_SEARCH_MATCHES:
                            break
        except OSError:
            continue
        if len(matches) >= MAX_SEARCH_MATCHES:
            break
    text = "\n".join(matches)
    return text, len(text) > limit


def _bounded_run(
    argv: list[str],
    *,
    cwd: str,
    timeout: int,
    limit: int,
    env: dict[str, str] | None = None,
) -> tuple[int, str, str, bool]:
    try:
        proc = subprocess.run(
            argv,
            cwd=cwd,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise HelperError("runner_command_timed_out") from exc
    except OSError as exc:
        raise HelperError("runner_exec_failed") from exc
    stdout = (proc.stdout or b"").decode("utf-8", errors="replace")
    stderr = (proc.stderr or b"").decode("utf-8", errors="replace")
    truncated = len(stdout) > limit or len(stderr) > limit
    return proc.returncode, stdout[:limit], stderr[:limit], truncated


def _git(workspace: str, git_dir: str, operation: str, limit: int) -> tuple[int, str, str, bool]:
    if operation not in GIT_OPERATIONS:
        raise HelperError("code_git_write_rejected")
    if git_dir == DEFAULT_GIT_DIR and workspace != DEFAULT_WORKSPACE:
        git_dir = os.path.join(workspace, ".git")
    if not os.path.isdir(git_dir):
        raise HelperError("code_git_query_failed")
    fd, index_path = tempfile.mkstemp(prefix="code-agent-git-index-")
    os.close(fd)
    env = dict(os.environ)
    env.update({
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_PAGER": "cat",
        "GIT_INDEX_FILE": index_path,
        "GIT_ALTERNATE_OBJECT_DIRECTORIES": "",
    })
    base = ["git", "-c", "core.hooksPath=/dev/null", "--git-dir", git_dir, "--work-tree", workspace]
    try:
        subprocess.run(
            [*base, "read-tree", "HEAD"],
            env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            check=False, timeout=30,
        )
        return _bounded_run(
            [*base, *GIT_OPERATIONS[operation]], cwd=workspace, timeout=30, limit=limit, env=env
        )
    except HelperError:
        raise
    except OSError:
        raise HelperError("code_git_query_failed")
    finally:
        try:
            os.unlink(index_path)
        except OSError:
            pass


def _response(request: dict, *, exit_code: int = 0, stdout: str = "", stderr: str = "",
              changed_files: tuple[str, ...] = (), truncated: bool = False, error: str = "") -> dict:
    return {
        "run_id": str(request.get("run_id", "")),
        "container_id": str(request.get("container_id", "")),
        "tool": str(request.get("tool", "")),
        "helper_version": HELPER_VERSION,
        "exit_code": exit_code,
        "stdout": stdout,
        "stderr": stderr,
        "output_limit_bytes": int(request.get("output_limit_bytes") or DEFAULT_OUTPUT_LIMIT),
        "changed_files": list(changed_files),
        "truncated": truncated,
        "error": error,
    }


def run_tool(
    request: dict,
    *,
    workspace: str = DEFAULT_WORKSPACE,
    git_dir: str = DEFAULT_GIT_DIR,
    timeout: int = DEFAULT_TIMEOUT,
) -> dict:
    """Dispatch a validated request to the matching tool and return a response dict."""
    tool = str(request.get("tool", ""))
    params = request.get("parameters") or {}
    limit = int(request.get("output_limit_bytes") or DEFAULT_OUTPUT_LIMIT)
    try:
        if tool == "read":
            text, truncated = _read(workspace, str(params.get("path", "")), limit)
            return _response(request, stdout=text, truncated=truncated)
        if tool == "search":
            text, truncated = _search(
                workspace, str(params.get("query", "")), str(params.get("path", "")), limit
            )
            return _response(request, stdout=text, truncated=truncated)
        if tool == "edit":
            nbytes = _edit(workspace, str(params.get("path", "")), str(params.get("content", "")), limit)
            return _response(
                request,
                stdout=json.dumps({"path": str(params.get("path", "")), "bytes": nbytes}),
                changed_files=(str(params.get("path", "")),),
            )
        if tool == "git":
            exit_code, stdout, stderr, truncated = _git(
                workspace, git_dir, str(params.get("operation", "")), limit
            )
            return _response(
                request, exit_code=exit_code, stdout=stdout, stderr=stderr, truncated=truncated
            )
        if tool in ("test", "shell"):
            command = str(params.get("command", "")).strip()
            if not command:
                raise HelperError("code_tool_invalid_input")
            try:
                argv = shlex.split(command)
            except ValueError:
                raise HelperError("code_shell_escape_rejected")
            if len(argv) >= 2 and argv[0] == "git" and argv[1] == "commit":
                raise HelperError("code_git_write_rejected")
            exit_code, stdout, stderr, truncated = _bounded_run(
                argv, cwd=workspace, timeout=timeout, limit=limit
            )
            return _response(
                request, exit_code=exit_code, stdout=stdout, stderr=stderr, truncated=truncated
            )
        raise HelperError("code_tool_unknown")
    except HelperError as exc:
        return _response(request, exit_code=1, error=exc.reason)


def main(argv: list[str] | None = None) -> int:
    workspace = os.environ.get("CODE_AGENT_WORKSPACE", DEFAULT_WORKSPACE)
    git_dir = os.environ.get("CODE_AGENT_GIT_DIR", DEFAULT_GIT_DIR)
    timeout = int(os.environ.get("CODE_AGENT_TIMEOUT", DEFAULT_TIMEOUT))
    line = sys.stdin.readline()
    try:
        request = json.loads(line) if line.strip() else None
    except json.JSONDecodeError:
        request = None
    if not isinstance(request, dict):
        sys.stdout.write(json.dumps(_response({}, exit_code=1, error="runner_tool_request_invalid")) + "\n")
        return 1
    response = run_tool(request, workspace=workspace, git_dir=git_dir, timeout=timeout)
    sys.stdout.write(json.dumps(response) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
