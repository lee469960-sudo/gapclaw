"""system-logs stats expose structured runtime classes without breaking old logs."""

from __future__ import annotations

from mcp_servers.system_logs import tools as log_tools


def test_log_stats_counts_structured_classes(tmp_path, monkeypatch):
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    (log_dir / "api.log").write_text(
        "\n".join([
            "2026-09-05 10:00:00 WARNING [x] LLM retry component=llm class=throttling provider=openai",
            "2026-09-05 10:00:01 WARNING [x] LLM retry component=llm class=network_connectivity provider=openai",
            "2026-09-05 10:00:02 WARNING [x] telegram poll failed aggregate component=telegram class=network_connectivity channel=c1",
            "2026-09-05 10:00:03 INFO [x] ordinary old log line",
        ]),
        encoding="utf-8",
    )
    monkeypatch.setenv("LOG_DIR", str(log_dir))

    stats = log_tools.log_stats("api", minutes=0)

    assert stats["structured_class_counts"] == {
        "llm:throttling": 1,
        "llm:network_connectivity": 1,
        "telegram:network_connectivity": 1,
    }
    assert stats["file"] == "api.log"


def test_log_stats_keeps_empty_structured_counts_for_plain_logs(tmp_path, monkeypatch):
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    (log_dir / "api.log").write_text(
        "2026-09-05 10:00:00 INFO [x] plain old log line\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("LOG_DIR", str(log_dir))

    stats = log_tools.log_stats("api", minutes=0)

    assert stats["structured_class_counts"] == {}
    assert stats["file_lines_total"] == 1
