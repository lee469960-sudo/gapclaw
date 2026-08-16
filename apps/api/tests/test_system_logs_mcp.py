"""Tests for system-logs MCP tools (path guards, tail/search/stats)."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from mcp_servers.system_logs import tools as log_tools


@pytest.fixture()
def fake_logs(tmp_path, monkeypatch):
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    api = log_dir / "api.log"
    api.write_text(
        "\n".join(
            [
                'INFO:     127.0.0.1:1 - "GET /health HTTP/1.1" 200 OK',
                "2026-08-07 12:00:00 ERROR something failed",
                "Traceback (most recent call last):",
                "ValueError: boom",
                'INFO:     127.0.0.1:2 - "GET /x HTTP/1.1" 500 Internal',
                "2026-08-07 12:01:00 WARNING slow query",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (log_dir / "web.log").write_text("vite ready\n", encoding="utf-8")
    monkeypatch.setenv("LOG_DIR", str(log_dir))
    return log_dir


def test_list_log_sources(fake_logs):
    data = log_tools.list_log_sources()
    assert data["log_dir"] == str(fake_logs.resolve())
    names = {s["source"] for s in data["sources"]}
    assert {"api", "web", "cloudflared", "im_events"} <= names
    api = next(s for s in data["sources"] if s["source"] == "api")
    assert api["exists"] is True


def test_tail_and_grep(fake_logs):
    out = log_tools.tail_log("api", lines=10, grep="ERROR")
    assert "ERROR something failed" in out
    assert "vite ready" not in out


def test_unknown_source_rejected(fake_logs):
    out = log_tools.tail_log("../etc/passwd", lines=10)
    assert "未知 source" in out or "错误" in out


def test_path_traversal_blocked(fake_logs, monkeypatch):
    # Even if someone patches SOURCE_FILES, _safe_source_path only allows known keys
    assert log_tools._safe_source_path("api") is not None
    assert log_tools._safe_source_path("../../.env") is None


def test_search_error_and_stats(fake_logs):
    hits = log_tools.search_log(source="api", level="ERROR", limit=20)
    assert "ERROR" in hits or "Traceback" in hits
    stats = log_tools.log_stats(source="api", minutes=0)
    assert stats["source"] == "api"
    assert stats["http_status_counts"].get("200") == 1
    assert stats["http_status_counts"].get("500") == 1
    assert stats["level_counts"].get("ERROR", 0) >= 1


def test_dispatch_clip(fake_logs, monkeypatch):
    monkeypatch.setattr(log_tools, "MAX_CHARS", 80)
    huge = fake_logs / "api.log"
    huge.write_text(("x" * 200 + "\n") * 5, encoding="utf-8")
    out = log_tools.dispatch("tail_log", {"source": "api", "lines": 50})
    assert "已截断" in out


def test_skill_seed_asset_exists():
    root = Path(__file__).resolve().parents[1] / "seed_assets" / "system-log-analyst"
    assert (root / "SKILL.md").is_file()
    text = (root / "SKILL.md").read_text(encoding="utf-8")
    assert "list_log_sources" in text
    assert "日志诊断摘要" in text


def test_needs_generic_plan_gate_for_log_analysis():
    from app.services.react_engine import _needs_generic_plan_gate

    assert _needs_generic_plan_gate("分析一下最近系统日志并给出优化建议") is True
    assert _needs_generic_plan_gate("今天怎么样") is False
