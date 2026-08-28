"""Tests for long-task: subtask parsing/tracking, checkpoint roundtrip, resume."""

from __future__ import annotations

from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.services.agent_runtime.loop_state import AgentLoopState
from app.services.agent_runtime.runtime import (
    _clear_run_state,
    _load_run_state,
    _merge_subtasks,
    _render_subtask_list,
    _save_run_state,
)
from app.services.tool_parser import parse_subtasks


# ---- pure subtask parsing ----

def test_parse_subtasks_checkbox():
    got = parse_subtasks("- [x] 步骤1\n- [ ] 步骤2\n- [X] 步骤3")
    assert got == [
        {"text": "步骤1", "status": "done"},
        {"text": "步骤2", "status": "pending"},
        {"text": "步骤3", "status": "done"},
    ]


def test_parse_subtasks_numbered_and_bullet():
    assert parse_subtasks("1. 步骤1\n2、步骤2\n3) 步骤3") == [
        {"text": "步骤1", "status": "pending"},
        {"text": "步骤2", "status": "pending"},
        {"text": "步骤3", "status": "pending"},
    ]
    assert parse_subtasks("- 步骤1\n* 步骤2") == [
        {"text": "步骤1", "status": "pending"},
        {"text": "步骤2", "status": "pending"},
    ]


def test_parse_subtasks_free_text_returns_empty():
    assert parse_subtasks("我将导出 16 列 Excel") == []


def test_merge_subtasks_preserves_done():
    prev = [{"text": "步骤1", "status": "done"}, {"text": "步骤2", "status": "pending"}]
    new = [{"text": "步骤1", "status": "pending"}, {"text": "步骤2", "status": "pending"}]
    assert _merge_subtasks(prev, new) == [
        {"text": "步骤1", "status": "done"},
        {"text": "步骤2", "status": "pending"},
    ]


def test_render_subtask_list():
    subtasks = [{"text": "步骤1", "status": "done"}, {"text": "步骤2", "status": "pending"}]
    assert _render_subtask_list(subtasks) == "- [x] 步骤1\n- [ ] 步骤2"


# ---- checkpoint roundtrip + resume ----

def _make_db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _ctx(db):
    return SimpleNamespace(
        agent=SimpleNamespace(id="a1"),
        session_id="s1",
        user_message="导出 16 列 Excel",
        db=db,
    )


def _state():
    return AgentLoopState(
        goal="导出 16 列 Excel",
        subtasks=[
            {"text": "步骤1", "status": "done"},
            {"text": "步骤2", "status": "pending"},
        ],
        progress_lines=["已写入 task/1/a.json"],
        saved_paths=["a.json"],
        files_written=1,
        last_reply="进行中",
        run_ts="1700000000000",
        mcp_results=[
            {"seq": 0, "path": "task/1700000000000/mcp_result_0.json",
             "tool": "query_ads_view", "args": {"view": "x"}, "size": 8000},
        ],
        query_cache={
            "m1\x00query_ads_view\x00{\"view\": \"x\"}": {
                "path": "task/1700000000000/mcp_result_0.json",
                "tool": "query_ads_view", "size": 8000,
            },
        },
        plan_text="- [x] 步骤1\n- [ ] 步骤2",
    )


def test_run_state_roundtrip_and_resume():
    db = _make_db()
    _save_run_state(_ctx(db), _state())

    loaded = _load_run_state(_ctx(db))
    assert loaded is not None
    assert loaded.resumed is True
    assert loaded.goal == "导出 16 列 Excel"
    assert loaded.subtasks[0] == {"text": "步骤1", "status": "done"}
    assert loaded.subtasks[1]["status"] == "pending"
    assert loaded.progress_lines == ["已写入 task/1/a.json"]
    assert loaded.saved_paths == ["a.json"]
    assert loaded.files_written == 1
    # Extension fields (task 4.1/4.2) must roundtrip losslessly.
    assert loaded.run_ts == "1700000000000"
    assert loaded.mcp_results[0]["path"] == "task/1700000000000/mcp_result_0.json"
    assert loaded.mcp_results[0]["tool"] == "query_ads_view"
    assert loaded.query_cache["m1\x00query_ads_view\x00{\"view\": \"x\"}"]["path"] == (
        "task/1700000000000/mcp_result_0.json"
    )
    assert loaded.plan_text == "- [x] 步骤1\n- [ ] 步骤2"


def test_load_run_state_returns_none_without_checkpoint():
    db = _make_db()
    assert _load_run_state(_ctx(db)) is None


def test_clear_run_state_removes_checkpoint():
    db = _make_db()
    _save_run_state(_ctx(db), _state())
    assert _load_run_state(_ctx(db)) is not None
    _clear_run_state(_ctx(db))
    assert _load_run_state(_ctx(db)) is None
