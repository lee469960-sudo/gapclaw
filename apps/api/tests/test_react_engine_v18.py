"""react-engine-v18: evidence gate, vague FAIL→PASS, fix-only (no replan), count reset.

Scoped to Standard/DBA `_run_modular`. No live LLM; chat / reflect / tools are mocked.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.services.agent_runtime.loop_state import AgentLoopState
from app.services.agent_runtime.runtime import (
    AgentRuntime,
    _apply_plan,
    _effective_fix_list,
    _load_run_state,
    _open_subtask_texts,
    _save_run_state,
    _subtasks_ready_for_final,
)
from app.services.llm_client import ChatResult


def _make_db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _fake_ctx(db, *, max_iterations=10, allowed_actions=("shell",)):
    return SimpleNamespace(
        agent=SimpleNamespace(
            id="a1", llm_timeout=30, sandbox_id=None, name="t",
            max_iterations=max_iterations, prompt="You are a test agent.", memory="",
        ),
        session_id="s1",
        chat_key="a1:s1",
        user_message="导出报表",
        username="u",
        db=db,
        llm=SimpleNamespace(id="llm1"),
        sandbox=None,
        mcp_ids=["m1"],
        skill_ids=[],
        skill_names=[],
        mcp_names=["ads-mcp"],
        skill_mds=[],
        httpmcp_ids=[],
        rag_ids=[],
        allowed_actions=list(allowed_actions),
        save_dir="",
        im_source="",
        note_content="",
        message_meta={},
    )


def _run(ctx, chat, *, reflect=None, distill=None, execute=None, apply_plan=None):
    async def _go():
        from contextlib import ExitStack

        with ExitStack() as stack:
            stack.enter_context(patch("app.services.llm_client.chat_completion", new=chat))
            stack.enter_context(patch(
                "app.services.agent_runtime.system_prompt.SystemPromptBuilder.build_tools_desc",
                new=AsyncMock(return_value="- shell: SHELL: <cmd>"),
            ))
            if reflect is not None:
                stack.enter_context(patch.object(AgentRuntime, "_reflect_final", new=reflect))
            if distill is not None:
                stack.enter_context(patch.object(AgentRuntime, "_distill_final", new=distill))
            if execute is not None:
                stack.enter_context(patch("app.services.agent_tools.execute_action", new=execute))
            if apply_plan is not None:
                stack.enter_context(patch(
                    "app.services.agent_runtime.runtime._apply_plan", new=apply_plan,
                ))
            return await AgentRuntime().run(ctx)

    return asyncio.run(_go())


# ---- helpers ----


def test_subtasks_ready_for_final():
    assert _subtasks_ready_for_final([]) is True
    assert _subtasks_ready_for_final(None) is True
    assert _subtasks_ready_for_final([{"text": "", "status": "pending"}]) is True
    assert _subtasks_ready_for_final([{"text": "a", "status": "done"}]) is True
    assert _subtasks_ready_for_final([{"text": "a", "status": "pending"}]) is False
    assert _subtasks_ready_for_final([
        {"text": "a", "status": "done"},
        {"text": "b", "status": "pending"},
    ]) is False
    assert _open_subtask_texts([
        {"text": "a", "status": "done"},
        {"text": "补 SQL", "status": "pending"},
    ]) == ["补 SQL"]


def test_effective_fix_list_drops_boilerplate():
    assert _effective_fix_list([]) == []
    assert _effective_fix_list(["任务尚未完成"]) == []
    assert _effective_fix_list(["尚未完成", "再检查一下", "请再核对"]) == []
    assert _effective_fix_list(["任务未完成", "还未完成", "没有完成", "未完成"]) == []
    assert _effective_fix_list(["还需努力", "请再检查", "再核对一下"]) == []
    assert _effective_fix_list(["补最终 SQL"]) == ["补最终 SQL"]
    assert _effective_fix_list(["任务尚未完成", "补最终 SQL"]) == ["补最终 SQL"]


# ---- loop ----


def test_incomplete_subtasks_skip_reflect():
    db = _make_db()
    ctx = _fake_ctx(db, max_iterations=3)
    _save_run_state(ctx, AgentLoopState(
        goal="导出报表", subtasks=[{"text": "步骤1", "status": "pending"}],
    ))
    reflect = AsyncMock(return_value=None)
    distill = AsyncMock(return_value="（未完成）")

    async def _chat(llm, messages, **_kw):
        return ChatResult(text="FINAL: 完成")

    result = _run(ctx, _chat, reflect=reflect, distill=distill)

    assert result == "（未完成）"
    reflect.assert_not_awaited()


def test_incomplete_final_still_runs_same_round_tools():
    db = _make_db()
    ctx = _fake_ctx(db)
    _save_run_state(ctx, AgentLoopState(
        goal="导出报表", subtasks=[{"text": "步骤1", "status": "pending"}],
    ))
    replies = [
        "SHELL: echo hi\nFINAL: 完成",
        "PLAN:\n- [x] 步骤1\nFINAL: 完成",
    ]
    idx = {"n": 0}

    async def _chat(llm, messages, **_kw):
        text = replies[min(idx["n"], len(replies) - 1)]
        idx["n"] += 1
        return ChatResult(text=text)

    exec_mock = AsyncMock(return_value="ok")
    reflect = AsyncMock(return_value=None)
    result = _run(ctx, _chat, reflect=reflect, execute=exec_mock)
    assert result == "完成"
    exec_mock.assert_awaited()
    assert reflect.await_count == 1


def test_vague_fail_accepted_as_pass():
    db = _make_db()
    ctx = _fake_ctx(db)

    async def _chat(llm, messages, **_kw):
        return ChatResult(text="FINAL: 报表已导出")

    reflect = AsyncMock(return_value={
        "missing": "任务尚未完成",
        "fix_list": ["任务尚未完成"],
        "revised_plan": "- [ ] 再做一遍",
    })
    result = _run(ctx, _chat, reflect=reflect)
    assert result == "报表已导出"
    reflect.assert_awaited_once()


def test_empty_fix_list_fail_accepted_as_pass():
    db = _make_db()
    ctx = _fake_ctx(db)

    async def _chat(llm, messages, **_kw):
        return ChatResult(text="FINAL: 完成")

    reflect = AsyncMock(return_value={
        "missing": "任务尚未完成",
        "fix_list": [],
        "revised_plan": "- [ ] 全新计划",
    })
    result = _run(ctx, _chat, reflect=reflect)
    assert result == "完成"


def test_effective_fail_drops_later_plan():
    db = _make_db()
    ctx = _fake_ctx(db)
    _save_run_state(ctx, AgentLoopState(
        goal="导出报表",
        subtasks=[{"text": "步骤1", "status": "done"}],
    ))
    replies = [
        "FINAL: 完成",
        "PLAN:\n- [ ] 全新错误计划\n- [ ] 不该出现",
        "FINAL: 完成",
    ]
    idx = {"n": 0}

    async def _chat(llm, messages, **_kw):
        text = replies[min(idx["n"], len(replies) - 1)]
        idx["n"] += 1
        return ChatResult(text=text)

    reflect_n = {"n": 0}

    async def _reflect(_self, _ctx, _state, candidate):
        reflect_n["n"] += 1
        if reflect_n["n"] == 1:
            return {
                "missing": "缺最终 SQL",
                "fix_list": ["补最终 SQL"],
                "revised_plan": "- [ ] 全新错误计划",
            }
        return None

    apply_spy = []

    def _spy_apply(ctx, state, cm, plan_text):
        apply_spy.append(plan_text)
        return _apply_plan(ctx, state, cm, plan_text)

    result = _run(
        ctx, _chat,
        reflect=_reflect,
        apply_plan=_spy_apply,
    )
    assert result == "完成"
    assert apply_spy == []
    loaded = _load_run_state(ctx)
    assert loaded is None  # successful FINAL clears checkpoint


def test_tool_success_resets_reflect_fail_count():
    db = _make_db()
    ctx = _fake_ctx(db, max_iterations=10)
    _save_run_state(ctx, AgentLoopState(
        goal="导出报表",
        subtasks=[{"text": "步骤1", "status": "done"}],
    ))
    replies = [
        "FINAL: 完成",
        "SHELL: echo hi",
        "FINAL: 完成",
        "FINAL: 完成",
        "FINAL: 完成",
    ]
    main_n = {"n": 0}

    async def _chat(llm, messages, **_kw):
        text = replies[min(main_n["n"], len(replies) - 1)]
        main_n["n"] += 1
        return ChatResult(text=text)

    async def _reflect(_self, _ctx, _state, candidate):
        return {
            "missing": "缺最终 SQL",
            "fix_list": ["补最终 SQL"],
            "revised_plan": "",
        }

    result = _run(
        ctx, _chat,
        reflect=_reflect,
        execute=AsyncMock(return_value="ok"),
    )
    # 1 FAIL + tool reset + 3 consecutive FAILs → 5 main rounds, then converge.
    assert result == "完成"
    assert main_n["n"] == 5


def test_fix_only_flag_survives_checkpoint():
    db = _make_db()
    ctx = _fake_ctx(db)
    _save_run_state(ctx, AgentLoopState(
        goal="导出报表",
        subtasks=[{"text": "步骤1", "status": "done"}],
        fix_only_until_final=True,
    ))
    loaded = _load_run_state(ctx)
    assert loaded is not None
    assert loaded.fix_only_until_final is True
