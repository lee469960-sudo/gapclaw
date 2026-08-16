"""Exec-card visibility: zero-step placeholder + history step_count."""

from __future__ import annotations

import json

from app.routers.agent_chat import _slim_meta_for_history, _steps_tail_for_message
from app.services.react_engine import _ensure_visible_run_steps


def test_ensure_visible_steps_adds_placeholder_when_empty():
    out = _ensure_visible_run_steps([])
    assert len(out) == 1
    assert out[0]["action"] == "no_tools"
    assert "未调用" in out[0]["title"]
    assert out[0]["status"] == "done"


def test_ensure_visible_steps_export_with_mcp_mentions_query():
    out = _ensure_visible_run_steps([], export_like=True, has_mcp=True)
    assert len(out) == 1
    assert "query_ads_view" in out[0]["title"]
    assert "clickhouse" in out[0]["title"].lower()


def test_ensure_visible_steps_keeps_existing():
    steps = [{"type": "tool", "action": "mcp_tool_call", "title": "MCP", "status": "done"}]
    out = _ensure_visible_run_steps(steps, export_like=True, has_mcp=True)
    assert out == steps


def test_slim_meta_keeps_stored_step_count_when_steps_empty():
    raw = json.dumps({"steps": [], "step_count": 1, "saved_paths": []})
    out = _slim_meta_for_history(raw)
    assert out["step_count"] == 1


def test_slim_meta_keeps_im_task_initiator():
    raw = json.dumps({
        "steps": [],
        "step_count": 1,
        "source": "im:telegram",
        "channel_id": "tg1",
        "chat_id": "-100",
        "chat_type": "group",
        "user_id": "42",
        "sender_username": "starter",
        "sender_display_name": "Start User",
    })
    out = _slim_meta_for_history(raw)
    assert out["source"] == "im:telegram"
    assert out["chat_type"] == "group"
    assert out["sender_username"] == "starter"
    assert out["sender_display_name"] == "Start User"
    assert out["user_id"] == "42"


def test_slim_meta_keeps_cte_attempt_summary_and_saved_paths():
    raw = json.dumps({
        "steps": [],
        "step_count": 1,
        "saved_paths": [
            "task/1786639999999/draft_sql.sql",
            "task/1786639999999/final_sql.sql",
        ],
        "cte_attempts_used": 3,
        "cte_attempt_limit": 5,
        "cte_attempt_errors": [
            {"attempt": 2, "stage": "query_count", "error": "timeout"},
            {"attempt": 3, "stage": "query_page", "error": "page failed"},
        ],
        "export_run_id": "1786639999999",
    })
    out = _slim_meta_for_history(raw)
    assert out["cte_attempts_used"] == 3
    assert out["cte_attempt_limit"] == 5
    assert out["export_run_id"] == "1786639999999"
    assert out["saved_paths"] == [
        "task/1786639999999/draft_sql.sql",
        "task/1786639999999/final_sql.sql",
    ]
    assert [row["stage"] for row in out["cte_attempt_errors"]] == [
        "query_count",
        "query_page",
    ]


def test_steps_tail_reports_max_of_list_and_stored():
    raw = json.dumps({"steps": [], "step_count": 1})
    out = _steps_tail_for_message(raw, 0)
    assert out["step_count"] == 1
    assert out["steps"] == []
