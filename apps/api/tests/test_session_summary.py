"""Unit tests for the session rolling-summary round cap.

Covers _summary_max_rounds (reuses history_length) and
_append_rolling_summary's round-based truncation. No LLM involved.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from app.services.session_summary import _append_rolling_summary, _summary_max_rounds


class _FakeSummary:
    def __init__(self, content=""):
        self.content = content


class _FakeQuery:
    def __init__(self, result):
        self._result = result

    def filter(self, *a, **k):
        return self

    def first(self):
        return self._result


class _FakeDB:
    def __init__(self, summary):
        self._summary = summary
        self.added = []

    def query(self, model):
        return _FakeQuery(self._summary)

    def add(self, row):
        self.added.append(row)

    def commit(self):
        pass


def test_summary_max_rounds_clamps():
    assert _summary_max_rounds(SimpleNamespace(history_length=30)) == 30
    assert _summary_max_rounds(SimpleNamespace(history_length=0)) == 10
    assert _summary_max_rounds(SimpleNamespace(history_length=None)) == 10
    assert _summary_max_rounds(SimpleNamespace(history_length=-5)) == 1


def test_append_rolling_summary_caps_rounds():
    agent = SimpleNamespace(id="a1", history_length=2, llm_timeout=None)
    summary = _FakeSummary(
        content="- [2026-08-17 10:00] 第一轮。\n"
        "- [2026-08-17 10:01] 第二轮。\n"
        "- [2026-08-17 10:02] 第三轮。"
    )
    db = _FakeDB(summary)

    async def _run():
        return await _append_rolling_summary(
            db, agent, "s1", None, "已完成导出", ["out.xlsx"]
        )

    result = asyncio.run(_run())
    lines = [ln for ln in result.splitlines() if ln.strip()]
    assert len(lines) == 2
    assert "第一轮" not in result
    assert "第二轮" not in result
    assert "第三轮" in result
    assert "已完成导出" in result
