"""Prevent 100-round stall: block task/*.py writes, dim-force degrade, fetch idle escape."""

from app.services.agent_tools import (
    _block_task_py_write_message,
    _exec_shell,
    _is_task_py_rel,
    _write_workplace,
)
from app.services.react_engine import (
    _EXPORT_DIM_FORCE_MAX_ROUNDS,
    _EXPORT_FETCH_IDLE_ESCAPE,
)


def test_dim_force_and_idle_escape_constants():
    assert _EXPORT_DIM_FORCE_MAX_ROUNDS == 3
    assert _EXPORT_FETCH_IDLE_ESCAPE == 4


def test_is_task_py_rel():
    assert _is_task_py_rel("task/1785723871280/build_report.py") is True
    assert _is_task_py_rel("task/foo/bar.py") is True
    assert _is_task_py_rel("tmp/build_report.py") is False
    assert _is_task_py_rel("task/page_1.json") is False
    assert _is_task_py_rel("report.py") is False


def test_write_workplace_blocks_task_py(tmp_path, monkeypatch):
    from app.services import agent_tools as at

    class _Sb:
        id = "sbx-block"

    monkeypatch.setattr(at, "_wp_root", lambda _s: tmp_path / "wp")
    monkeypatch.setattr(at, "_sandbox_id", lambda _s: "sbx-block")
    out = _write_workplace(_Sb(), "task/1/build_report.py", "print(1)\n")  # type: ignore[arg-type]
    assert "拦截" in out
    assert "热重载" in out or "/tmp/build_report.py" in out
    assert not (tmp_path / "wp" / "task" / "1" / "build_report.py").exists()


def test_exec_shell_blocks_task_py_redirect():
    out = _exec_shell(None, "cat > task/1785/build_report.py <<'EOF'\nprint(1)\nEOF", 10)
    assert "拦截" in out
    assert "task/" in out or "/tmp" in out


def test_block_message_mentions_tmp():
    msg = _block_task_py_write_message("task/x/build_report.py")
    assert "/tmp/build_report.py" in msg
