"""im_events list_log_sources size_bytes grows after insert."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from mcp_servers.system_logs import tools as log_tools


def test_im_events_size_bytes_grows(tmp_path, monkeypatch):
    db_path = tmp_path / "gap.db"
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE im_event_logs ("
        "id INTEGER PRIMARY KEY, channel_id TEXT, level TEXT, "
        "message TEXT, detail TEXT, created_at TEXT)"
    )
    conn.commit()
    conn.close()

    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("LOG_DIR", str(tmp_path / "logs"))
    (tmp_path / "logs").mkdir()

    before = log_tools._im_events_source_meta()
    assert before["exists"] is True
    size_before = int(before["size_bytes"] or 0)

    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO im_event_logs (channel_id, level, message, detail, created_at) "
        "VALUES ('c1', 'info', 'hello event', '', '2026-08-09 12:00:00')"
    )
    conn.commit()
    conn.close()

    after = log_tools._im_events_source_meta()
    assert after["exists"] is True
    assert int(after["size_bytes"] or 0) > size_before
    assert after["mtime"] == "2026-08-09 12:00:00"

    listed = log_tools.list_log_sources()
    im = next(s for s in listed["sources"] if s["source"] == "im_events")
    assert int(im["size_bytes"] or 0) > size_before
