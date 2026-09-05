"""react-engine-v16 tests.

Covers the four v16 requirements:
- R1 完成信号软转换: a tool-free positive completion declaration escalates to a
  FINAL candidate only after N consecutive rounds + an LLM confirm; negative /
  question phrasings never trigger (no new hard gate).
- R2 需求感知完成度复核: the Verifier decomposes goal into a checklist and
  judges field/schema accuracy, no longer blanket-passing "小瑕疵".
- R3 已完成清单注入: a completed-work checklist is injected every round and is
  exempt from tool-result trimming.
- R4 MCP 工具调用去重: MCP tools are deduped by tool name + normalized args
  (execute_ads_sql via SQL normalization); large results echo a fetch path.

No real LLM/MCP involved; ``chat_completion`` / tools / reflection are mocked.
"""

from __future__ import annotations

import asyncio
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.services.agent_runtime.runtime import (
    AgentRuntime,
    _build_completed_checklist,
    _looks_like_completion_declaration,
    _merge_subtasks,
)
from app.services.agent_runtime.context_manager import ContextManager
from app.services.mcp_client import McpSessionManager
from app.services.llm_client import ChatResult


class _FakeQuery:
    def filter(self, *_a, **_k):
        return self

    def order_by(self, *_a, **_k):
        return self

    def limit(self, *_a, **_k):
        return self

    def all(self):
        return []

    def first(self):
        return None


class _FakeDB:
    def __init__(self):
        self.added = []

    def add(self, row):
        self.added.append(row)

    def commit(self):
        pass

    def delete(self, row):
        pass

    def query(self, _model):
        return _FakeQuery()


def _fake_ctx(**overrides):
    base = dict(
        agent=SimpleNamespace(
            id="a1", llm_timeout=30, sandbox_id=None, name="t",
            max_iterations=50, prompt="You are a test agent.", memory="",
        ),
        session_id="s1",
        chat_key="a1:s1",
        user_message="导出报表",
        username="u",
        db=_FakeDB(),
        llm=SimpleNamespace(id="llm1"),
        sandbox=None,
        mcp_ids=["m1"],
        skill_ids=[],
        skill_names=[],
        mcp_names=["ads-mcp"],
        skill_mds=[],
        httpmcp_ids=[],
        rag_ids=[],
        allowed_actions=["mcp_tool_call", "shell"],
        save_dir="",
        im_source="",
        note_content="",
        message_meta={},
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _patched_runtime(chat, *, confirm=None, reflect=None, distill=None):
    """Context manager wiring the LLM/reflection seams for a fake loop run."""
    stack = ExitStack()
    stack.enter_context(patch("app.services.llm_client.chat_completion", new=chat))
    stack.enter_context(patch(
        "app.services.agent_runtime.system_prompt.SystemPromptBuilder.build_tools_desc",
        new=AsyncMock(return_value="- shell: SHELL: <cmd>"),
    ))
    if confirm is not None:
        stack.enter_context(patch.object(AgentRuntime, "_confirm_completion_signal", new=confirm))
    if reflect is not None:
        stack.enter_context(patch.object(AgentRuntime, "_reflect_final", new=reflect))
    if distill is not None:
        stack.enter_context(patch.object(AgentRuntime, "_distill_final", new=distill))
    return stack


# ---- R1 完成信号软转换 ----

def test_completion_declaration_regex():
    # 正向完成声明
    assert _looks_like_completion_declaration("已完成，交付 report.xlsx")
    assert _looks_like_completion_declaration("任务完成，最终交付结果")
    assert _looks_like_completion_declaration("交付物确认完成，已生成 report.xlsx")
    assert _looks_like_completion_declaration("无需再调用工具，所有子任务已完成")
    # 否定 / 疑问不触发
    assert not _looks_like_completion_declaration("无法完成，MCP 连接失败")
    assert not _looks_like_completion_declaration("还不能完成，缺渠道名称映射")
    assert not _looks_like_completion_declaration("这个任务完成了吗？")
    assert not _looks_like_completion_declaration("尚未完成，还差一列")


def test_completion_signal_soft_conversion():
    ctx = _fake_ctx()
    ctx.agent.max_iterations = 10
    seen: list[list[dict]] = []

    async def _chat(llm, messages, **_kw):
        seen.append([dict(m) for m in messages])
        return ChatResult(text="已完成，交付 report.xlsx")

    reflect = AsyncMock(return_value=None)

    async def _run():
        with _patched_runtime(_chat, confirm=AsyncMock(return_value=True), reflect=reflect):
            return await AgentRuntime().run(ctx)

    result = asyncio.run(_run())

    assert result == "已完成，交付 report.xlsx"
    # 2 轮完成声明 → LLM 确认 → 当作 FINAL 候选送复核（一次），随即收尾。
    assert len(seen) == 2
    reflect.assert_awaited_once()


def test_completion_signal_negative_not_triggered():
    ctx = _fake_ctx()
    ctx.agent.max_iterations = 4
    confirm = AsyncMock(return_value=True)
    reflect = AsyncMock(return_value=None)

    async def _chat(llm, messages, **_kw):
        return ChatResult(text="无法完成，MCP 连接失败")

    async def _run():
        with _patched_runtime(
            _chat,
            confirm=confirm,
            reflect=reflect,
            distill=AsyncMock(return_value="（超时）"),
        ):
            return await AgentRuntime().run(ctx)

    result = asyncio.run(_run())

    assert result == "（超时）"
    confirm.assert_not_awaited()
    reflect.assert_not_awaited()


def test_completion_signal_confirm_false_resets():
    # 完成声明连续出现，但 LLM 确认判定「非完成」→ 不送 FINAL，计数清零。
    ctx = _fake_ctx()
    ctx.agent.max_iterations = 4
    confirm = AsyncMock(return_value=False)
    reflect = AsyncMock(return_value=None)

    async def _chat(llm, messages, **_kw):
        return ChatResult(text="已完成，交付 report.xlsx")

    async def _run():
        with _patched_runtime(
            _chat,
            confirm=confirm,
            reflect=reflect,
            distill=AsyncMock(return_value="（超时）"),
        ):
            return await AgentRuntime().run(ctx)

    asyncio.run(_run())

    confirm.assert_awaited()  # streak 到 2 后确认过
    reflect.assert_not_awaited()  # 但确认判否 → 未送 FINAL


def test_repeated_completion_after_file_write_converges_without_extra_confirm():
    ctx = _fake_ctx(allowed_actions=["file_write"])
    ctx.agent.max_iterations = 10
    calls = 0

    async def _chat(llm, messages, **_kw):
        nonlocal calls
        calls += 1
        if calls == 1:
            return ChatResult(text="WRITE: report.md\nok")
        return ChatResult(text="交付物确认完成。--- # 报告\n已生成 report.md")

    confirm = AsyncMock(return_value=False)
    reflect = AsyncMock(return_value=None)

    async def _run():
        with _patched_runtime(_chat, confirm=confirm, reflect=reflect):
            return await AgentRuntime().run(ctx)

    result = asyncio.run(_run())

    assert "交付物确认完成" in result
    assert calls == 3
    confirm.assert_not_awaited()
    reflect.assert_awaited_once()


def test_repeated_cached_mcp_reference_hard_stops_before_budget_exhaustion():
    calls = 0

    async def _tool_executor(action, normalized):
        nonlocal calls
        calls += 1
        assert action == "mcp_tool_call"
        return "该结果已缓存/已落盘，请直接读取 task/1/mcp_result_1.json，勿重复调用。"

    ctx = _fake_ctx(
        allowed_actions=["mcp_tool_call"],
        tool_executor=_tool_executor,
    )
    ctx.agent.max_iterations = 20

    async def _chat(llm, messages, **_kw):
        return ChatResult(text='MCP: daily {"ts_code":"600519.SH","trade_date":"20260901"}')

    async def _run():
        with _patched_runtime(
            _chat,
            distill=AsyncMock(return_value="不应走到预算耗尽"),
        ):
            return await AgentRuntime().run(ctx)

    result = asyncio.run(_run())

    assert calls == 3
    assert "连续 3 次重复请求已缓存的 MCP 结果" in result
    assert "200" not in result


# ---- R2 需求感知完成度复核 ----


def test_reflect_prompt_is_checklist_aware():
    ctx = _fake_ctx()
    state = SimpleNamespace(
        goal="导出 16 列充值用户数据（含渠道名称映射）",
        saved_paths=["report.xlsx"],
        subtasks=[],
        progress_lines=[],
    )
    captured: dict = {}

    async def _chat(llm, messages, **_kw):
        captured["messages"] = messages
        return "PASS"

    async def _run():
        with patch("app.services.llm_client.chat_completion", new=_chat):
            return await AgentRuntime()._reflect_final(ctx, state, "已完成，交付 report.xlsx")

    result = asyncio.run(_run())

    assert result is None  # PASS → 接受
    prompt = captured["messages"][0]["content"]
    assert "逐条拆成核对项" in prompt
    assert "字段口径、数字、映射关系必须精确" in prompt
    assert "小瑕疵一律 PASS" not in prompt  # 去掉了旧豁免


def test_reflect_fail_extracts_targeted_fix_list():
    ctx = _fake_ctx()
    state = SimpleNamespace(
        goal="导出 16 列充值用户数据（含渠道名称映射）",
        saved_paths=["report.xlsx"],
        subtasks=[],
        progress_lines=[],
    )
    fail_verdict = (
        "FAIL: 渠道名称未映射\n"
        "修复清单：\n"
        "- 在 SQL 中 join 渠道维表，补充 channel_name 映射\n"
        "修订 PLAN：\n"
        "- [x] 导出充值用户数据\n"
        "- [ ] 补充渠道名称映射\n"
    )

    async def _chat(llm, messages, **_kw):
        return fail_verdict

    async def _run():
        with patch("app.services.llm_client.chat_completion", new=_chat):
            return await AgentRuntime()._reflect_final(ctx, state, "已完成，交付 report.xlsx")

    report = asyncio.run(_run())

    assert report is not None
    assert "渠道名称未映射" in report["missing"]
    assert report["fix_list"] == ["在 SQL 中 join 渠道维表，补充 channel_name 映射"]
    assert "补充渠道名称映射" in report["revised_plan"]


def test_merge_subtasks_preserves_done_status():
    prev = [{"text": "导出充值用户数据", "status": "done"}]
    new = [
        {"text": "导出充值用户数据", "status": "pending"},  # 重发 PLAN 忘了勾选
        {"text": "补充渠道名称映射", "status": "pending"},
    ]
    merged = _merge_subtasks(prev, new)
    assert merged[0]["status"] == "done"  # 完成态保留，不因重发 PLAN 回退
    assert merged[1]["status"] == "pending"


# ---- R3 已完成清单注入 ----


def test_build_completed_checklist_assembles_sources():
    state = SimpleNamespace(
        goal="导出报表",
        subtasks=[
            {"text": "导数据", "status": "done"},
            {"text": "做渠道映射", "status": "pending"},
        ],
        saved_paths=["report.xlsx"],
        progress_lines=["已写入 report.xlsx", "已缓存 MCP 结果 /task/m1.json"],
        query_cache={"k1": {"path": "/task/m1.json", "tool": "execute_ads_sql"}},
        resumed=False,
    )
    txt = _build_completed_checklist(state, {"mcp:execute_ads_sql": 3})
    assert "【已完成子任务】" in txt
    assert "- [x] 导数据" in txt
    assert "- [ ] 做渠道映射" in txt
    assert "【已写文件】" in txt
    assert "report.xlsx" in txt
    assert "【已尝试工具】" in txt
    assert "execute_ads_sql ×3" in txt
    assert "【进度】" in txt
    assert "已缓存 MCP 结果 /task/m1.json" in txt


def test_build_completed_checklist_resume_header():
    state = SimpleNamespace(
        subtasks=[{"text": "a", "status": "done"}, {"text": "b", "status": "pending"}],
        saved_paths=[],
        progress_lines=[],
        query_cache={},
        resumed=True,
    )
    txt = _build_completed_checklist(state)
    assert "上次执行到此" in txt
    assert "已完成 1/2 子任务，还差 1 项" in txt


def test_set_completed_checklist_updates_in_place():
    cm = ContextManager()
    cm.set_task_context(goal="g", plan="p", view_catalog="v", checklist="【已完成清单】\n（暂无）")
    cm.set_completed_checklist("【已完成清单】\n- [x] a")
    tc = [m for m in cm.messages if m["role"] == "system" and "【任务目标】" in m.get("content", "")]
    assert len(tc) == 1  # 清单在 task_context 稳定层原位更新，不产生重复消息
    content = tc[0]["content"]
    assert "g" in content and "p" in content and "v" in content
    assert "- [x] a" in content
    assert "（暂无）" not in content


def test_checklist_survives_trim():
    cm = ContextManager()
    cm.set_base(system_prompt="sys")
    cm.set_task_context(goal="g", checklist="【已完成清单】\n- [x] a")
    for _ in range(30):
        cm.push_tool_result("工具结果 " + "x" * 50)
    cm.trim_tool_results(keep_recent=2)
    joined = "\n".join(m.get("content", "") for m in cm.messages)
    assert "【已完成清单】" in joined
    assert "- [x] a" in joined  # 裁剪后清单仍在


# ---- R4 MCP 工具调用去重 + 大结果取回指引 ----


class _MCP:
    def __init__(self, mid="m1"):
        self.id = mid


def test_dedup_key_ads_sql_normalized():
    mgr = McpSessionManager()
    mcp = _MCP()
    k1 = mgr._dedup_key(mcp, "execute_ads_sql", {"sql": "SELECT * FROM t;"})
    k2 = mgr._dedup_key(mcp, "execute_ads_sql", {"sql": "  select * from t  "})
    k3 = mgr._dedup_key(mcp, "execute_ads_sql", {"sql": "select * from t"})
    assert k1 == k2 == k3  # 去空白/大小写/尾分号 → 同一 key


def test_dedup_key_ads_sql_limit_offset_distinct():
    mgr = McpSessionManager()
    mcp = _MCP()
    k1 = mgr._dedup_key(mcp, "execute_ads_sql", {"sql": "select * from t limit 10"})
    k2 = mgr._dedup_key(mcp, "execute_ads_sql", {"sql": "select * from t limit 20"})
    k3 = mgr._dedup_key(mcp, "execute_ads_sql", {"sql": "select * from t limit 10 offset 5"})
    assert k1 != k2  # LIMIT 不同 → 不去重
    assert k1 != k3  # OFFSET 不同 → 不去重


def test_dedup_key_generic_mcp_tool_normalized_args():
    mgr = McpSessionManager()
    mcp = _MCP()
    k1 = mgr._dedup_key(mcp, "query_ads_view", {"view": "v", "date": "2024"})
    k2 = mgr._dedup_key(mcp, "query_ads_view", {"date": "2024", "view": "v"})
    k3 = mgr._dedup_key(mcp, "query_ads_view", {"view": "w", "date": "2024"})
    assert k1 == k2  # 键序归一化
    assert k1 != k3  # 参数不同 → 不去重


def test_cached_reference_echo():
    mgr = McpSessionManager()
    out = mgr._cached_reference({"path": "task/ts/mcp_result_0.json", "size": 123, "shape": "[3 项]"})
    assert "该结果已缓存/已落盘" in out
    assert "task/ts/mcp_result_0.json" in out
    assert "请 READ 取回" in out
    assert "勿重跑" in out


def test_oversized_materialize_echoes_full_path(tmp_path):
    class _Sandbox:
        id = "s1"

    mgr = McpSessionManager(run_ts="1700000000000", sandbox=_Sandbox())
    mcp = _MCP()
    big = "BIG" * 3000  # 12000 chars > 6000 default large_result_chars
    with patch("app.services.workplace.workplace_root", side_effect=lambda sid="default": tmp_path):
        out = mgr._materialize(mcp, "query_ads_view", {"view": "x"}, big, "k")
    assert "已全量写入" in out
    assert "task/1700000000000/mcp_result_0.json" in out  # 完整取回路径，非截断片段
