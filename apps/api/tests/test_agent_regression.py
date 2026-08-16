"""Regression fixtures: light identity / analytical report / generic Q&A.

These encode the plan's 2–3 fixed checks so Skill+engine stay aligned without
needing a live LLM/MCP run.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.react_engine import (
    _build_conversational_messages,
    _build_conversational_system,
    _build_light_agent_reply,
    _build_model_understanding_system,
    _build_session_summary_fallback,
    _build_session_summary_messages,
    _append_mcp_markdown,
    _clean_final_answer,
    _extract_view_from_sql,
    _format_generic_query_observation,
    _is_light_agent_interaction,
    _is_task_followup_message,
    _merge_structured_presentation,
    _needs_generic_plan_gate,
    _parse_export_todos,
    _plan_output_columns_section,
    _record_mcp_result,
    _rows_to_markdown_table,
    _run_conversational_turn,
    _session_recently_used_tools,
    _step_preview,
    _trim_conversational_history,
)
from app.services.task_policy import (
    detect_task_policy,
    parse_generic_plan,
    skill_snapshot_for_prompt,
)
from app.services.intent_router import TurnIntent, build_task_relation_context


# --- Case 1: 轻量身份导出（姓/名）→ generic，勿吸入分析报表 ---

LIGHT_IDENTITY_MSG = (
    "请根据 ID.txt 导出用户身份信息：\n"
    "1. 用户ID\n"
    "2. 姓\n"
    "3. 名\n"
)

POLLUTED_PLAN = """
PLAN:
- 目标: 按 ID 导出姓/名
- 任务类型: A-轻量身份
- 需要角色: 仅 user（user_info 视图自带 姓/名 字段,无需其他维度）
- 输出列:
  1. 用户ID
  2. 姓
  3. 名
- 步骤:
  1. READ: ID.txt 读取待查 ID 列表
  2. MCP query_ads_view 拉取对应用户的 姓/名
  3. SHELL 用 pandas 写 user_identity_info.xlsx(列: 用户ID/姓/名)到当前目录
- 完成标准: 当前目录 xlsx
"""


def test_regression_light_identity_is_generic():
    p = detect_task_policy(LIGHT_IDENTITY_MSG)
    assert p.policy_id == "generic"
    assert p.export_like is False
    assert p.reason == "light_identity"


def test_regression_polluted_plan_not_in_analyze_todos():
    """PLAN 步骤/角色不得进入「未命中分析列」。"""
    sec = _plan_output_columns_section(POLLUTED_PLAN)
    assert "READ:" not in sec
    assert "MCP" not in sec
    assert "需要角色" not in sec
    todos = _parse_export_todos(LIGHT_IDENTITY_MSG, POLLUTED_PLAN)
    analyze = [t["text"] for t in todos if t.get("phase") == "analyze"]
    assert analyze == ["用户ID", "姓", "名"]
    joined = " ".join(analyze)
    assert "READ" not in joined
    assert "SHELL" not in joined
    assert "需要角色" not in joined


# --- Case 2: 分析报表 → export_report + live resource binding ---

ANALYTICAL_MSG = (
    "导出美国时间 2026-07-21 至 2026-07-27 新增注册用户报表：\n"
    "1. 注册时间\n2. 用户ID\n3. 注册渠道\n4. 总充值金额\n"
    "5. 总提现金额\n6. 流水倍数\n7. 是否有退款\n8. SC投注金额最多的游戏\n"
)


def test_regression_analytical_report_is_export():
    p = detect_task_policy(ANALYTICAL_MSG)
    assert p.export_like is True
    assert p.policy_id == "export_report"


# --- Case 3: 通用问答 → generic PLAN 可解析 ---

QA_MSG = "用 list_ads_views 看一下有哪些用户相关视图，总结给我。"


def test_regression_generic_qa_policy_and_plan():
    p = detect_task_policy(QA_MSG)
    assert p.policy_id == "generic"
    assert p.require_plan_gate is True
    assert _needs_generic_plan_gate(QA_MSG) is True
    parsed = parse_generic_plan(
        "PLAN:\n"
        "- 目标: 列出用户相关视图\n"
        "- 步骤: list_ads_views\n"
        "- 完成标准: 回答含视图名列表\n"
        "- 工具预算: 最多 4 次\n"
    )
    assert parsed is not None
    assert parsed["budget"] == 4
    assert any("视图" in c for c in parsed["completion_lines"])


def test_needs_generic_plan_gate_skips_pure_chat():
    assert not _needs_generic_plan_gate("今天怎么样")
    assert not _needs_generic_plan_gate("你可以帮我做哪些事情?")
    assert not _needs_generic_plan_gate("你最擅长做什么")
    assert not _needs_generic_plan_gate("dba的职责是什么?")
    assert not _needs_generic_plan_gate("你的职责是什么")
    assert _needs_generic_plan_gate("导出一份 xlsx 报表")
    assert _needs_generic_plan_gate("读一下 workplace 里的配置文件")
    assert _needs_generic_plan_gate("用 list_ads_views 看一下有哪些视图")


def test_needs_generic_plan_gate_routes_sql_and_dbt_to_tools():
    assert _needs_generic_plan_gate("写一条查询用户充值金额的 SQL")
    assert _needs_generic_plan_gate("帮我写sql语句统计昨天注册用户")
    assert _needs_generic_plan_gate("进行 dbt 建模，给支付订单建一个 mart 表")
    assert _needs_generic_plan_gate("帮我做 dbt model")
    assert _needs_generic_plan_gate("按技能生成 stg_ 层模型")
    # Pure chat must stay off the tool ring
    assert not _needs_generic_plan_gate("你最擅长做什么")
    assert not _needs_generic_plan_gate("今天怎么样")


def _fake_db_with_assistant_meta(meta: dict):
    row = SimpleNamespace(id=1, meta=json.dumps(meta))
    q = MagicMock()
    q.filter.return_value = q
    q.order_by.return_value = q
    q.limit.return_value = q
    q.all.return_value = [row]
    return SimpleNamespace(query=lambda *_args, **_kwargs: q)


def test_needs_generic_plan_gate_followup_keeps_tool_ring():
    assert _is_task_followup_message("真跑一次")
    assert _is_task_followup_message("重试一次")
    assert _is_task_followup_message("重新处理这三个问题并排查返奖")
    assert _needs_generic_plan_gate("真跑一次") is True
    assert _needs_generic_plan_gate("重试一次") is True
    assert _needs_generic_plan_gate("再查一次") is True
    assert _needs_generic_plan_gate("重新处理这三个问题并排查返奖数据缺失") is True

    db = _fake_db_with_assistant_meta({
        "steps": [{"action": "mcp_tool_call", "tool": "query_ads_view"}],
    })
    assert _session_recently_used_tools(db, "a1", "s1") is True
    # Short follow-up after a toolful turn (no followup keyword, not greeting)
    assert _needs_generic_plan_gate(
        "就这个视图",
        db=db,
        agent_id="a1",
        session_id="s1",
    ) is True
    # Pure chat without prior tools stays conversational
    assert _needs_generic_plan_gate("今天怎么样") is False
    assert _needs_generic_plan_gate(
        "今天怎么样",
        db=db,
        agent_id="a1",
        session_id="s1",
    ) is False


def test_recent_task_context_distinguishes_plan_only_reply_from_execution():
    history = [
        SimpleNamespace(role="user", content="银行卡数量失败，修复后重新导出", meta="{}"),
        SimpleNamespace(
            role="assistant",
            content="我先排查字段，再修复 SQL 重新导出。",
            meta=json.dumps({
                "steps": [{"action": "skill_loaded"}, {"action": "mcp_loaded"}],
                "saved_paths": [],
            }),
        ),
    ]
    context = build_task_relation_context(
        history,
        {
            "task_title": "充值用户分析",
            "source_brief": "导出充值用户分析表",
            "analyze_columns": ["用户ID", "充值银行卡数量"],
            "deliverable": "充值用户分析_100.xlsx",
        },
    )
    assert "我先排查字段" in context
    assert "assistant_runtime" in context
    assert '"saved_paths": []' in context
    assert '"task_title": "充值用户分析"' in context


def test_clean_final_answer_strips_english_meta_reasoning():
    raw = (
        "SELECT 1 AS ok;\n\n"
        "Let me write a single-line FINAL answer now.\n"
        "I haven't yet called the tool.\n"
        "But the platform seems to think tool budget is exhausted.\n"
        "<结论>.\n"
        "注册用户数约 1200。"
    )
    cleaned = _clean_final_answer(raw)
    assert "SELECT 1 AS ok" in cleaned
    assert "注册用户数约 1200" in cleaned
    assert "Let me write" not in cleaned
    assert "I haven't yet" not in cleaned
    assert "tool budget" not in cleaned
    assert "<结论>" not in cleaned


def test_generic_query_results_default_to_markdown_table():
    rows = [{"指标": "注册", "数值": 22971}, {"指标": "充值", "数值": 4059}]
    table = _rows_to_markdown_table(rows)
    assert "| 指标 |" in table
    assert "| 注册 |" in table

    raw = json.dumps(rows, ensure_ascii=False)
    obs = _format_generic_query_observation(raw, rows)
    assert obs.startswith("### 查询结果")
    assert "| 指标 |" in obs
    assert "共 2 行" in obs

    mcp: list[dict] = []
    _record_mcp_result(
        mcp,
        'MCP: query_ads_view {"view":"view_result_user_info"}',
        raw,
        export_like=False,
    )
    assert mcp and "preview_md" in mcp[0]
    final = _append_mcp_markdown("查询完成。", mcp, export_like=False)
    assert "### 查询结果" in final
    assert "| 指标 |" in final
    # Export path must not inject preview tables into FINAL
    mcp_export: list[dict] = []
    _record_mcp_result(
        mcp_export,
        'MCP: query_ads_view {"view":"view_result_user_info"}',
        raw,
        export_like=True,
    )
    assert mcp_export and "preview_md" not in mcp_export[0]


def test_generic_execute_rows_are_available_to_final_without_tool_name_assumption():
    rows = [{"date": f"2026-08-{day:02d}", "users": day} for day in range(1, 15)]
    mcp: list[dict] = []
    _record_mcp_result(
        mcp,
        'MCP: execute_ads_sql {"sql":"SELECT ..."}',
        json.dumps(rows, ensure_ascii=False),
        export_like=False,
        result_shape="time_series",
    )
    final = _append_mcp_markdown("查询完成。", mcp, export_like=False)
    assert "| 2026-08-01 | 1 |" in final
    assert "| 2026-08-14 | 14 |" in final
    assert "展示前" not in final


def test_time_series_final_keeps_prior_complete_table():
    candidate = (
        "### 查询结果\n\n"
        "| 日期 | 新增 |\n"
        "| --- | ---: |\n"
        "| 2026-08-01 | 357 |"
    )
    out = _merge_structured_presentation(
        "已完成，明细请见之前结果。",
        candidate,
        result_shape="time_series",
    )
    assert "| 2026-08-01 | 357 |" in out
    assert "已完成" in out


def test_summary_history_prefers_telegram_initiator_identity():
    messages = _build_session_summary_messages(
        agent=SimpleNamespace(name="dba", description="DBA", prompt="BASE"),
        history=[SimpleNamespace(
            role="user",
            content="查询运营数据",
            created_at="2026-08-14 14:13:50",
            meta=json.dumps({
                "source": "im:telegram",
                "user_id": "7807708328",
                "sender_username": "MaChao2020",
                "sender_display_name": "Ma Chao",
            }, ensure_ascii=False),
        )],
        user_message="总结会话",
    )
    sent = "\n".join(message["content"] for message in messages)
    assert "发送者=@MaChao2020（Ma Chao）" in sent
    assert "发送者=7807708328" not in sent


def test_conversational_messages_exclude_tools_and_trim_export_history():
    agent = SimpleNamespace(
        name="dba",
        description="数据库管理员",
        prompt="SECRET_BASE_PROMPT",
    )
    history = [
        SimpleNamespace(role="user", content="导出报表"),
        SimpleNamespace(
            role="assistant",
            content="FINAL: 已落盘\n" + ("x" * 400) + "\nlist_ads_views ok\nquery_ads_view",
        ),
        SimpleNamespace(role="user", content="你最擅长做什么"),
    ]
    msgs = _build_conversational_messages(agent, history, "你最擅长做什么")
    assert msgs[0]["role"] == "system"
    sys = msgs[0]["content"]
    assert "对话模式" in sys
    assert "禁止输出 PLAN:" in sys or "禁止输出 PLAN" in sys
    assert "可用工具:" not in sys
    assert "SECRET_BASE_PROMPT" in sys  # base prompt is allowed in system
    assert "【通用·规划闸门】" not in "\n".join(m["content"] for m in msgs)
    assert msgs[-1] == {"role": "user", "content": "你最擅长做什么"}
    # export dump collapsed
    joined = "\n".join(m["content"] for m in msgs[1:-1])
    assert "list_ads_views" not in joined
    assert "细节已省略" in joined or "导出执行" in joined

    trimmed = _trim_conversational_history(history)
    assert any("省略" in (m.get("content") or "") for m in trimmed if m["role"] == "assistant")


def test_conversational_system_does_not_leak_tool_coaches():
    agent = SimpleNamespace(name="ads助手", description="分析助手", prompt="Be helpful.")
    sys = _build_conversational_system(agent)
    assert "Agent「ads助手」" in sys
    assert "FINAL:" in sys  # mentioned as forbidden
    assert "可用工具:" not in sys
    assert "规划闸门" not in sys


def test_conversational_turn_uses_llm_without_tools_and_falls_back():
    agent = SimpleNamespace(
        id="a1",
        name="dba",
        description="数据库管理员 Agent",
        prompt="SECRET",
        llm_timeout=30,
    )
    llm = SimpleNamespace(id="llm1")
    history = [
        SimpleNamespace(role="user", content="你好"),
        SimpleNamespace(role="assistant", content="你好，我是 dba。"),
    ]

    async def _ok():
        with patch(
            "app.services.react_engine.chat_completion",
            new=AsyncMock(return_value="我擅长 SQL 与数据导出协助。"),
        ) as mock_cc, patch(
            "app.services.react_engine.hub.publish",
            new=AsyncMock(),
        ), patch(
            "app.services.react_engine._append_rolling_summary",
            new=AsyncMock(),
        ), patch(
            "app.services.react_engine._running",
            {},
        ):
            db = SimpleNamespace(add=lambda *_: None, commit=lambda: None)
            out = await _run_conversational_turn(
                db=db,
                agent=agent,
                session_id="s1",
                user_message="你最擅长做什么",
                effective_message="你最擅长做什么",
                llm=llm,
                history=history,
                key="a1:s1",
                user_meta={},
            )
            assert "SQL" in out
            assert "FINAL:" not in out
            call_msgs = mock_cc.await_args.args[1]
            blob = "\n".join(m["content"] for m in call_msgs)
            assert "可用工具:" not in blob
            assert "【通用·规划闸门】" not in blob

    async def _fallback():
        with patch(
            "app.services.react_engine.chat_completion",
            new=AsyncMock(side_effect=RuntimeError("boom")),
        ), patch(
            "app.services.react_engine.hub.publish",
            new=AsyncMock(),
        ), patch(
            "app.services.react_engine._append_rolling_summary",
            new=AsyncMock(),
        ), patch(
            "app.services.react_engine._running",
            {},
        ):
            db = SimpleNamespace(add=lambda *_: None, commit=lambda: None)
            out = await _run_conversational_turn(
                db=db,
                agent=agent,
                session_id="s1",
                user_message="你是谁",
                effective_message="你是谁",
                llm=llm,
                history=[],
                key="a1:s1",
                user_meta={},
            )
            assert "Agent「dba」" in out
            assert "SECRET" not in out

    asyncio.run(_ok())
    asyncio.run(_fallback())


def test_session_summary_intent_is_model_contract_not_text_rule():
    summary = TurnIntent(
        intent="chat",
        conversation_action="summarize_session",
        source="llm",
    )
    ordinary = TurnIntent(intent="chat", conversation_action="respond", source="llm")
    assert summary.wants_session_summary is True
    assert ordinary.wants_session_summary is False


def test_session_summary_fallback_is_structured_not_identity():
    out = _build_session_summary_fallback(
        history=[],
        user_message="总结一下会话内容",
        note_content="",
    )
    assert "### 会话概览" in out
    assert "暂无可总结" in out
    assert "我是 Agent" not in out


def test_session_summary_messages_keep_full_current_session_history():
    history = [
        SimpleNamespace(role="user", content=f"任务 {index}", meta="{}")
        for index in range(1, 13)
    ]
    history.append(SimpleNamespace(role="user", content="请复盘当前会话", meta="{}"))
    messages = _build_session_summary_messages(
        agent=SimpleNamespace(name="dba", description="DBA", prompt="BASE"),
        history=history,
        user_message="请复盘当前会话",
    )
    contents = [message["content"] for message in messages]
    assert "任务 1" in contents
    assert "任务 12" in contents
    assert "请复盘当前会话" not in contents[:-1]
    assert all("会话滚动总结" not in content for content in contents)


def test_session_summary_messages_include_message_provenance_and_artifacts():
    messages = _build_session_summary_messages(
        agent=SimpleNamespace(name="dba", description="DBA", prompt="BASE"),
        history=[SimpleNamespace(
            role="assistant",
            content="重导完成",
            created_at="2026-08-14 08:06:36",
            meta=json.dumps({
                "source": "im:telegram",
                "task_title": "游戏10025用户导出",
                "saved_paths": ["游戏10025.xlsx"],
            }, ensure_ascii=False),
        )],
        user_message="复盘",
    )
    sent = "\n".join(message["content"] for message in messages)
    assert "时间=2026-08-14 08:06:36" in sent
    assert "来源=im:telegram" in sent
    assert "任务标题=游戏10025用户导出" in sent
    assert "交付文件=游戏10025.xlsx" in sent


def test_conversational_turn_summary_request_uses_summary_path():
    agent = SimpleNamespace(
        id="a1",
        name="dba",
        description="数据库管理员 Agent",
        prompt="SECRET",
        llm_timeout=30,
    )
    llm = SimpleNamespace(id="llm1")
    history = [
        SimpleNamespace(role="user", content="帮我修复导出人数不对的问题"),
        SimpleNamespace(role="assistant", content="已定位到人数口径多加了 bet_sc > 0 过滤，准备修复并重导。", meta='{"saved_paths":["report.xlsx"]}'),
        SimpleNamespace(role="user", content="总结一下会话内容"),
    ]

    class _FakeQuery:
        def filter(self, *_args, **_kwargs):
            return self

        def first(self):
            return SimpleNamespace(content="- [2026-08-14 10:00:00] 修复人数口径并重导 report.xlsx")

    added = []

    class _FakeDb:
        def query(self, *_args, **_kwargs):
            return _FakeQuery()

        def add(self, row):
            added.append(row)

        def commit(self):
            return None

    async def _run():
        with patch(
            "app.services.react_engine.chat_completion",
            new=AsyncMock(return_value=(
                "### 会话概览\n"
                "- 当前主题：修复导出人数口径并重导。\n"
                "- 用户核心诉求：确认人数差异原因并输出正确结果。\n"
                "- 已完成进展：已经定位到多余过滤条件。\n"
                "- 关键约束/口径：按真实会话归纳，不编造。\n"
                "- 交付产物：`report.xlsx`\n"
                "- 待继续项：按修复后的口径再次导出。\n\n"
                "### 关键进展\n"
                "- 已确认旧口径多加了 bet_sc > 0。\n"
                "- 下一步是按修复 SQL 重导。"
            )),
        ) as mock_cc, patch(
            "app.services.react_engine.hub.publish",
            new=AsyncMock(),
        ), patch(
            "app.services.react_engine._append_rolling_summary",
            new=AsyncMock(),
        ) as mock_roll, patch(
            "app.services.react_engine._running",
            {},
        ):
            out = await _run_conversational_turn(
                db=_FakeDb(),
                agent=agent,
                session_id="s1",
                user_message="总结一下会话内容",
                effective_message="总结一下会话内容",
                llm=llm,
                history=history,
                key="a1:s1",
                user_meta={},
                turn_intent=TurnIntent(
                    intent="chat",
                    conversation_action="summarize_session",
                    source="llm",
                ),
            )
            assert "### 会话概览" in out
            assert "report.xlsx" in out
            sent = "\n".join(m["content"] for m in mock_cc.await_args.args[1])
            assert "唯一任务是总结当前会话" in sent
            assert "不要自我介绍" in sent
            assert not mock_roll.await_count
            assert added and "\"session_summary_reply\": true" in added[0].meta.lower()

    asyncio.run(_run())


def test_regression_skill_snapshot_mentions_export_rules():
    snap = skill_snapshot_for_prompt([
        ("ads-sync-hub", "# ADS\n## 类型 A\n- 输出列只写表头\n- 禁止混入 READ\n"),
    ])
    assert "已绑定 Skill" in snap
    assert "输出列" in snap or "轻量" in snap


def test_light_agent_interaction_uses_identity_not_export_flow():
    agent = SimpleNamespace(
        name="ads助手",
        description="帮助理解需求并协调数据工具",
        prompt="SECRET_BASE_PROMPT_SHOULD_NOT_LEAK",
    )
    assert _is_light_agent_interaction("您好")
    assert _is_light_agent_interaction("你是谁")
    assert _is_light_agent_interaction("你可以帮我做哪些事情?")
    assert _is_light_agent_interaction("能帮我做什么")
    assert not _is_light_agent_interaction("您好，导出美国时间6-1日至今充值用户")

    reply = _build_light_agent_reply(agent, "你可以帮我做哪些事情?")
    assert "Agent「ads助手」" in reply
    assert "帮助理解需求并协调数据工具" in reply
    assert "直接告诉我你想做什么" in reply
    assert "FINAL:" not in reply
    assert "PLAN:" not in reply
    assert "list_ads_views" not in reply
    assert "完成标准" not in reply
    assert "SECRET_BASE_PROMPT_SHOULD_NOT_LEAK" not in reply
    assert "TaskSpec" not in reply

    who = _build_light_agent_reply(agent, "你是谁")
    assert "Agent「ads助手」" in who
    assert "FINAL:" not in who
    assert "SECRET_BASE_PROMPT_SHOULD_NOT_LEAK" not in who
    assert "TaskSpec" not in who


def test_model_understanding_system_preserves_identity_and_boundaries():
    agent = SimpleNamespace(
        name="ads助手",
        description="面向业务数据分析",
        prompt="SECRET_BASE_PROMPT_SHOULD_NOT_LEAK",
    )
    policy = detect_task_policy("请分析一下这份文件")
    sys = _build_model_understanding_system(agent, policy)
    assert "Agent「ads助手」" in sys
    assert "需求理解协议" in sys
    assert "不要把所有对话都套成数据导出" in sys
    assert "模型只负责理解需求" in sys
    assert "引擎执行" in sys
    assert "SECRET_BASE_PROMPT_SHOULD_NOT_LEAK" not in sys


def test_final_answer_strips_protocol_marker_for_display():
    assert _clean_final_answer("FINAL: 你好，我可以帮你。") == "你好，我可以帮你。"
    assert _clean_final_answer("FINAL:\n我是 Agent。") == "我是 Agent。"


def test_final_answer_drops_reasoning_before_last_final_marker():
    raw = (
        "完成。\nActually I should re-output with a proper marker.\n"
        "FINAL: **结果**\n\n```sql\nSELECT 1;\n```"
    )
    assert _clean_final_answer(raw) == "**结果**\n\n```sql\nSELECT 1;\n```"


def test_step_preview_drops_final_meta_reasoning():
    raw = (
        "Actually I think the previous turn already had FINAL marker.\n"
        "Let me structure the response.\n"
        "FINAL: sql有语法错误，请检查字段名。"
    )
    assert _step_preview(raw) == "sql有语法错误，请检查字段名。"
