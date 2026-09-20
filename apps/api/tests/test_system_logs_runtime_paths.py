from pathlib import Path

from mcp_servers.system_logs import tools


def test_log_dir_recovers_stale_container_root(monkeypatch, tmp_path):
    log_dir = tmp_path / ".local" / "logs"
    log_dir.mkdir(parents=True)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("LOG_DIR", "/.local/logs")

    assert tools.log_dir() == log_dir.resolve()


def test_im_events_error_does_not_require_sqlite(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)

    result = tools._im_events_tail(lines=1)

    assert "DATABASE_URL" in result
    assert "SQLite 或 PostgreSQL" in result
