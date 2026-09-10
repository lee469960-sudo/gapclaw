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
    _parse_verifier_gap_cards,
    _validated_gap_cards,
    _is_high_risk_gap_action,
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


async def _async_result(text: str):
    return ChatResult(text=text)


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


def test_parse_verifier_gap_cards_requires_all_evidence_fields():
    complete = (
        "FAIL: 尚缺核验\nGAP:\nrequirement: 确认存储引擎\n"
        "missing_evidence: view 的 engine 定义\naction: READ: task/view.json\n"
        "criterion: engine 字段为 ReplacingMergeTree"
    )
    assert _parse_verifier_gap_cards(complete) == [{
        "requirement": "确认存储引擎",
        "missing_evidence": "view 的 engine 定义",
        "action": "READ: task/view.json",
        "criterion": "engine 字段为 ReplacingMergeTree",
    }]
    assert _parse_verifier_gap_cards("FAIL: 再核对一下\nGAP:\nrequirement: x") == []


def test_validated_gap_cards_reject_duplicate_and_cached_read():
    state = AgentLoopState(
        query_cache={"mcp:x": {"path": "task/cached.json"}},
        verifier_gaps={"需求\x1f缺证据\x1f判定": {"signatures": ["shell: echo ok"]}},
    )
    cards = [
        {"requirement": "x", "missing_evidence": "x", "action": "READ: task/cached.json", "criterion": "x"},
        {"requirement": "需求", "missing_evidence": "缺证据", "action": "SHELL: echo ok", "criterion": "判定"},
        {"requirement": "需求2", "missing_evidence": "缺证据2", "action": "SHELL: echo new", "criterion": "判定2"},
    ]
    assert [c["action"] for c in _validated_gap_cards(state, cards)] == ["SHELL: echo new"]


def test_gap_action_risk_is_conservative_for_shell():
    assert not _is_high_risk_gap_action("READ: task/a.json")
    assert not _is_high_risk_gap_action("SHELL: rg engine task")
    assert _is_high_risk_gap_action("SHELL: cat task/a > out.txt")
    assert _is_high_risk_gap_action("SHELL: rm task/a.json")
    assert _is_high_risk_gap_action("SHELL: curl https://example.test")
    assert _is_high_risk_gap_action("SHELL: deploy production")
    assert _is_high_risk_gap_action("SHELL: python tool.py")


def test_exhausted_low_risk_gap_returns_qualified_final():
    db = _make_db()
    ctx = _fake_ctx(db)
    gap_id = "确认引擎\x1f缺定义\x1f能判断是否需要 FINAL"
    _save_run_state(ctx, AgentLoopState(
        goal="判断是否需要 FINAL",
        verifier_gaps={gap_id: {"signatures": ["read: task/a", "shell: rg engine task"]}},
    ))
    card = {"requirement": "确认引擎", "missing_evidence": "缺定义", "action": "READ: task/b", "criterion": "能判断是否需要 FINAL"}

    result = _run(ctx, lambda *_a, **_k: _async_result("FINAL: 业务等价"), reflect=AsyncMock(return_value={"missing": "缺定义", "gaps": [card]}))
    assert "业务等价" in result
    assert "条件性结论" in result


def test_exhausted_high_risk_gap_requests_confirmation():
    db = _make_db()
    ctx = _fake_ctx(db)
    gap_id = "部署\x1f缺发布证据\x1f发布成功"
    _save_run_state(ctx, AgentLoopState(
        goal="部署", verifier_gaps={gap_id: {"signatures": ["shell: a", "shell: b"]}},
    ))
    card = {"requirement": "部署", "missing_evidence": "缺发布证据", "action": "SHELL: deploy production", "criterion": "发布成功"}
    result = _run(ctx, lambda *_a, **_k: _async_result("FINAL: 已准备"), reflect=AsyncMock(return_value={"missing": "缺发布证据", "gaps": [card]}))
    assert "确认/授权" in result


# ---- loop ----


def test_pending_plan_subtasks_do_not_block_evidence_backed_final():
    db = _make_db()
    ctx = _fake_ctx(db, max_iterations=3)
    _save_run_state(ctx, AgentLoopState(
        goal="导出报表", subtasks=[{"text": "步骤1", "status": "pending"}],
    ))
    reflect = AsyncMock(return_value=None)

    async def _chat(llm, messages, **_kw):
        return ChatResult(text="FINAL: 完成")

    result = _run(ctx, _chat, reflect=reflect)

    assert result == "完成"
    reflect.assert_awaited_once()


def test_replacing_merge_tree_business_equivalence_pending_review_converges():
    db = _make_db()
    ctx = _fake_ctx(db)
    _save_run_state(ctx, AgentLoopState(
        goal="判断 ADS 视图是否需要 FINAL",
        subtasks=[{"text": "复核 ReplacingMergeTree 是否必须 FINAL", "status": "pending"}],
    ))

    async def _chat(_llm, _messages, **_kw):
        return ChatResult(text="FINAL: ADS 已在 ETL 链路去重；业务等价，无需 FINAL。")

    result = _run(ctx, _chat, reflect=AsyncMock(return_value=None))
    assert "业务等价" in result


def test_pending_plan_final_keeps_existing_final_priority_over_same_round_tools():
    db = _make_db()
    ctx = _fake_ctx(db)
    _save_run_state(ctx, AgentLoopState(
        goal="导出报表", subtasks=[{"text": "步骤1", "status": "pending"}],
    ))
    async def _chat(llm, messages, **_kw):
        return ChatResult(text="SHELL: echo hi\nFINAL: 完成")

    exec_mock = AsyncMock(return_value="ok")
    reflect = AsyncMock(return_value=None)
    result = _run(ctx, _chat, reflect=reflect, execute=exec_mock)
    assert result == "完成"
    exec_mock.assert_not_awaited()
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


def test_legacy_prose_reflect_fail_is_nonblocking():
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
    assert result == "完成"
    assert main_n["n"] == 1


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


def test_verifier_gap_state_round_trips_and_legacy_checkpoint_defaults():
    db = _make_db()
    ctx = _fake_ctx(db)
    _save_run_state(ctx, AgentLoopState(
        goal="导出报表",
        progress_lines=["已查询视图"],
        verifier_gaps={
            "gap-1": {
                "card": {"requirement": "确认引擎", "action": "READ: task/result.json"},
                "signatures": ["read\\x00task/result.json"],
                "evidence": ["已落盘 task/result.json"],
            }
        },
        terminal_reason="low_risk_gap_exhausted",
    ))

    loaded = _load_run_state(ctx)
    assert loaded is not None
    assert loaded.verifier_gaps["gap-1"]["signatures"] == ["read\\x00task/result.json"]
    assert loaded.terminal_reason == "low_risk_gap_exhausted"

    # Old checkpoints have neither field and remain resumable.
    from app.models import AgentRunState
    row = ctx.db.query(AgentRunState).first()
    row.state = '{"goal":"旧任务","progress_lines":["已有进度"]}'
    ctx.db.commit()
    legacy = _load_run_state(ctx)
    assert legacy is not None
    assert legacy.verifier_gaps == {}
    assert legacy.terminal_reason == ""
