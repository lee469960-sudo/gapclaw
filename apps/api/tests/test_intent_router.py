"""Tests for LLM turn intent router (no regex primary path)."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.services.intent_router import (
    _extract_json_object,
    analyze_turn_intent,
    fallback_turn_intent,
    format_time_window_system_hint,
    looks_like_task_message,
    parse_turn_intent_payload,
    resolve_time_window_from_intent,
    resolve_context_timezone,
    should_force_chat_override,
    should_force_tools_override,
    should_hard_stop_task_spec,
    task_policy_from_intent,
)
from app.services.task_policy import detect_export_report_task, detect_task_policy

# 2026-08-06 full day half-open ms (engine-computed)
_ET_AUG6_START = 1785988800000
_ET_AUG6_END = 1786075200000
_UTC_AUG6_START = 1785974400000
_UTC_AUG6_END = 1786060800000
_CST_AUG6_START = 1785945600000
_CST_AUG6_END = 1786032000000


def test_extract_json_object_with_think_wrapper():
    raw = (
        "<think>需要 export_report</think>"
        '{"intent":"export_report","reason":"导出","wants_deliverable":true,'
        '"query_goal":"导出报表","metrics":["注册"],"time_window":null}'
    )
    data = _extract_json_object(raw)
    assert data is not None
    assert data["intent"] == "export_report"


def test_extract_json_object_ignores_braces_in_provider_prose():
    raw = 'analysis {not json}\nFINAL {"intent":"chat","metrics":[]}'
    assert _extract_json_object(raw) == {"intent": "chat", "metrics": []}


def test_parse_session_summary_as_llm_conversation_action():
    turn = parse_turn_intent_payload({
        "intent": "chat",
        "reason": "回顾当前会话中的任务与返工过程",
        "wants_deliverable": False,
        "query_goal": "总结当前会话",
        "conversation_action": "summarize_session",
        "metrics": [],
        "time_window": None,
    })
    assert turn.is_chat is True
    assert turn.wants_session_summary is True


def test_llm_session_summary_action_is_not_overridden_by_task_terms():
    llm = SimpleNamespace(type="llm", model="x")
    payload = (
        '{"intent":"chat","reason":"总结当前会话","wants_deliverable":false,'
        '"query_goal":"总结SQL导出任务与返工原因",'
        '"conversation_action":"summarize_session","metrics":[],"time_window":null}'
    )

    async def _run():
        with patch(
            "app.services.llm_client.chat_completion",
            new=AsyncMock(return_value=payload),
        ):
            return await analyze_turn_intent(
                llm,
                "梳理当前会话里的 SQL、导出任务、错误版本和修复结果",
            )

    turn = asyncio.run(_run())
    assert turn.wants_session_summary is True
    assert turn.intent == "chat"


def test_calendar_et_half_open_ms():
    tw = resolve_time_window_from_intent({
        "start_date": "2026-08-06",
        "end_date": "2026-08-06",
        "inclusive_end_day": True,
        "tz": "ET",
        "label": "2026-08-06 美国东部全日",
    })
    assert tw is not None
    assert tw["source"] == "calendar"
    assert tw["start_ms"] == _ET_AUG6_START
    assert tw["end_ms"] == _ET_AUG6_END
    assert tw["start_date"] == "2026-08-06"


def test_calendar_utc_and_cst():
    utc = resolve_time_window_from_intent({
        "start_date": "2026-08-06",
        "end_date": "2026-08-06",
        "inclusive_end_day": True,
        "tz": "UTC",
    })
    assert utc["start_ms"] == _UTC_AUG6_START
    assert utc["end_ms"] == _UTC_AUG6_END

    cst = resolve_time_window_from_intent({
        "start_date": "2026-08-06",
        "end_date": "2026-08-06",
        "inclusive_end_day": True,
        "tz": "CST",
    })
    assert cst["start_ms"] == _CST_AUG6_START
    assert cst["end_ms"] == _CST_AUG6_END


def test_context_timezone_user_overrides_session_note():
    assert resolve_context_timezone("获取10号笔记", "默认使用北京时间") == "CST"
    assert resolve_context_timezone("按美国东部时间获取10号笔记", "默认使用北京时间") == "ET"


def test_parse_calendar_fields_without_ms():
    turn = parse_turn_intent_payload({
        "intent": "data_query",
        "reason": "单日注册人数",
        "wants_deliverable": False,
        "query_goal": "统计8月6日新增注册人数",
        "metrics": ["注册人数"],
        "time_window": {
            "start_date": "2026-08-06",
            "end_date": "2026-08-06",
            "inclusive_end_day": True,
            "tz": "ET",
            "label": "2026-08-06 美国东部全日",
        },
    })
    assert turn.intent == "data_query"
    assert turn.query_goal.startswith("统计")
    assert turn.metrics == ["注册人数"]
    assert turn.time_window["start_ms"] == _ET_AUG6_START
    assert turn.time_window["source"] == "calendar"
    hint = format_time_window_system_hint(
        turn.time_window,
        query_goal=turn.query_goal,
        metrics=turn.metrics,
    )
    assert "注册人数" in hint
    assert "2026-08-06" in hint
    assert str(_ET_AUG6_START) in hint
    assert "引擎换算" in hint


def test_time_series_result_shape_reaches_query_and_presentation_contract():
    turn = parse_turn_intent_payload({
        "intent": "data_query",
        "reason": "按日运营序列",
        "wants_deliverable": False,
        "query_goal": "按日展示完整运营数据",
        "result_shape": "time_series",
        "temporal_grain": "day",
        "metrics": [{"goal": "运营指标", "kind": "other"}],
        "time_window": {
            "start_date": "2026-08-01",
            "end_date": "2026-08-14",
            "inclusive_end_day": True,
            "tz": "ET",
        },
    })
    assert turn.result_shape == "time_series"
    assert turn.temporal_grain == "day"
    hint = format_time_window_system_hint(
        turn.time_window,
        query_goal=turn.query_goal,
        metrics=turn.metrics,
        result_shape=turn.result_shape,
        temporal_grain=turn.temporal_grain,
    )
    assert "result_shape=time_series" in hint
    assert "temporal_grain=day" in hint
    assert "完整序列" in hint
    assert "Markdown 表格" in hint


def test_raw_ms_fallback_when_no_calendar():
    turn = parse_turn_intent_payload({
        "intent": "data_query",
        "time_window": {
            "label": "legacy",
            "start_ms": 1754539200000,
            "end_ms": 1754625600000,
            "tz": "ET",
        },
    })
    assert turn.time_window["source"] == "llm_ms_raw"
    assert turn.time_window["start_ms"] == 1754539200000


def test_parse_export_report():
    turn = parse_turn_intent_payload({
        "intent": "export_report",
        "reason": "多列 xlsx",
        "wants_deliverable": True,
        "time_window": None,
    })
    policy = task_policy_from_intent(turn)
    assert policy.export_like is True
    assert policy.policy_id == "export_report"


def test_parse_chat():
    turn = parse_turn_intent_payload({"intent": "chat", "reason": "寒暄"})
    assert turn.is_chat
    assert not turn.needs_tools


def test_fallback_prefers_data_query_not_export():
    turn = fallback_turn_intent("获取一下8月7日的新增注册人数")
    assert turn.intent == "data_query"
    assert task_policy_from_intent(turn).export_like is False


def test_fallback_light_chat():
    turn = fallback_turn_intent("你好", is_light_chat=True)
    assert turn.intent == "chat"


def test_fallback_task_not_forced_chat():
    turn = fallback_turn_intent("获取一下系统日志", is_light_chat=True)
    assert turn.intent == "data_query"


def test_should_force_chat_override_pure_greeting_only():
    assert should_force_chat_override(
        "你好", is_light_chat=True, llm_intent="data_query"
    )
    assert not should_force_chat_override(
        "获取一下系统日志", is_light_chat=True, llm_intent="other_tools"
    )
    assert looks_like_task_message("获取一下系统日志")


def test_should_never_hard_stop_task_spec():
    assert should_hard_stop_task_spec("clarification") is False
    assert should_hard_stop_task_spec("error") is False
    assert should_hard_stop_task_spec("pass") is False


def test_skill_strong_alone_not_export_via_regex_policy():
    """Regression: export skill + 新增注册 must not force export_like."""
    skill = "export-report\n用户报表\n分析列"
    msg = "获取一下8月7日的新增注册人数"
    is_exp, reason = detect_export_report_task(msg, skill)
    assert is_exp is False
    assert detect_task_policy(msg, skill_blob=skill).export_like is False


def test_export_xlsx_still_detectable():
    msg = "导出 14 列注册用户分析报表 xlsx\n1. 用户ID\n2. 注册时间\n3. 总充值\n4. 总提现"
    is_exp, _ = detect_export_report_task(msg, "export-report")
    assert is_exp is True


def test_analyze_turn_intent_calendar_only_no_ms():
    llm = SimpleNamespace(type="llm", model="x")
    payload = (
        '{"intent":"data_query","reason":"查数","wants_deliverable":false,'
        '"query_goal":"8月6日注册人数","metrics":["注册人数"],'
        '"time_window":{"start_date":"2026-08-06","end_date":"2026-08-06",'
        '"inclusive_end_day":true,"tz":"ET","label":"2026-08-06 ET全日"}}'
    )
    now = datetime(2026, 8, 7, 12, 0, 0, tzinfo=timezone.utc)

    async def _run():
        with patch(
            "app.services.llm_client.chat_completion",
            new=AsyncMock(return_value=payload),
        ):
            return await analyze_turn_intent(
                llm,
                "获取一下8月6日的新增注册人数",
                has_export_skill=True,
                now=now,
            )

    turn = asyncio.run(_run())
    assert turn.source == "llm"
    assert turn.intent == "data_query"
    assert turn.metrics == ["注册人数"]
    assert turn.time_window["start_ms"] == _ET_AUG6_START
    assert turn.time_window["source"] == "calendar"


def test_intent_prompt_treats_multi_field_user_list_as_deliverable():
    llm = SimpleNamespace(type="llm", model="x")
    payload = (
        '{"intent":"export_report","reason":"多字段用户明细交付",'
        '"wants_deliverable":true,"query_goal":"导出下注用户列表",'
        '"metrics":["用户ID","下注笔数"],"time_window":null}'
    )
    mock = AsyncMock(return_value=payload)

    async def _run():
        with patch("app.services.llm_client.chat_completion", new=mock):
            return await analyze_turn_intent(
                llm,
                "导出昨天有下注行为的用户列表，包含用户ID、下注笔数",
            )

    turn = asyncio.run(_run())
    assert turn.intent == "export_report"
    assert turn.wants_deliverable is True
    system_prompt = mock.await_args.args[1][0]["content"]
    assert "即使没有出现 Excel/xlsx" in system_prompt


def test_analyze_turn_intent_preserves_llm_task_relation_and_context():
    llm = SimpleNamespace(type="llm", model="x")
    payload = (
        '{"intent":"export_report","reason":"继续上一轮全量导出",'
        '"wants_deliverable":true,"query_goal":"导出全部数据",'
        '"metrics":[],"time_window":null,"task_relation":"continue"}'
    )
    mock = AsyncMock(return_value=payload)

    async def _run():
        with patch("app.services.llm_client.chat_completion", new=mock):
            return await analyze_turn_intent(
                llm,
                "全量导出 excel",
                conversation_context=(
                    "user: 导出六列充值用户数据\n"
                    "assistant: 上轮只导出了样本"
                ),
            )

    turn = asyncio.run(_run())
    assert turn.task_relation == "continue"
    messages = mock.await_args.args[1]
    relation_context = [
        message["content"] for message in messages
        if message["content"].startswith("【最近对话与运行证据】")
    ]
    assert relation_context
    assert "上轮只导出了样本" in relation_context[0]


def test_parse_turn_intent_preserves_requested_artifacts():
    turn = parse_turn_intent_payload({
        "intent": "data_query",
        "reason": "读取最近任务产物",
        "wants_deliverable": False,
        "query_goal": "输出 SQL",
        "task_relation": "continue",
        "requested_artifacts": ["sql", "methods", "sql", "unsupported"],
    })

    assert turn.task_relation == "continue"
    assert turn.requested_artifacts == ["sql", "methods"]


def test_analyze_turn_intent_retries_unknown_relation_when_prior_task_exists():
    llm = SimpleNamespace(type="llm", model="x")
    undecided = (
        '{"intent":"other_tools","reason":"准备检查后再处理",'
        '"wants_deliverable":false,"query_goal":"重新导出",'
        '"metrics":[],"time_window":null,"task_relation":"unknown"}'
    )
    decided = (
        '{"intent":"export_report","reason":"修订上一轮并重新导出",'
        '"wants_deliverable":true,"query_goal":"修复并重新导出",'
        '"metrics":[],"time_window":null,"task_relation":"revise",'
        '"verification_required":true,"verification_targets":["xlsx文件"]}'
    )
    mock = AsyncMock(side_effect=[undecided, decided])

    async def _run():
        with patch("app.services.llm_client.chat_completion", new=mock):
            return await analyze_turn_intent(
                llm,
                "重新导出",
                conversation_context="运行状态: 上轮已有 SQL 与 xlsx",
                require_decided_relation=True,
            )

    turn = asyncio.run(_run())
    assert turn.intent == "export_report"
    assert turn.task_relation == "revise"
    assert mock.await_count == 2
    retry_messages = mock.await_args_list[1].args[1]
    assert any(
        "task_relation" in message["content"] and "不能返回 unknown" in message["content"]
        for message in retry_messages
    )


def test_analyze_turn_intent_retries_when_context_reply_omits_task_relation():
    llm = SimpleNamespace(type="llm", model="x")
    missing = (
        '{"intent":"export_report","reason":"导出",'
        '"wants_deliverable":true,"query_goal":"导出全部数据",'
        '"metrics":[],"time_window":null}'
    )
    complete = (
        '{"intent":"export_report","reason":"继续最近任务",'
        '"wants_deliverable":true,"query_goal":"导出全部数据",'
        '"metrics":[],"time_window":null,"task_relation":"continue"}'
    )
    mock = AsyncMock(side_effect=[missing, complete])

    async def _run():
        with patch("app.services.llm_client.chat_completion", new=mock):
            return await analyze_turn_intent(
                llm,
                "全量导出 excel",
                conversation_context="user: 上一轮导出六列数据",
            )

    turn = asyncio.run(_run())
    assert turn.task_relation == "continue"
    assert mock.await_count == 2


def test_analyze_turn_intent_marks_user_challenge_for_runtime_verification():
    llm = SimpleNamespace(type="llm", model="x")
    payload = (
        '{"intent":"data_query","reason":"用户质疑上一轮导出数据量",'
        '"wants_deliverable":false,"query_goal":"重新核验导出数据量",'
        '"metrics":[],"time_window":null,"task_relation":"revise",'
        '"verification_required":true,'
        '"verification_targets":["MCP总行数","xlsx数据行数"]}'
    )
    mock = AsyncMock(return_value=payload)

    async def _run():
        with patch("app.services.llm_client.chat_completion", new=mock):
            return await analyze_turn_intent(
                llm,
                "这个数据量真的对吗？请严格验证",
                conversation_context="assistant: 上一轮已导出 12000 行",
            )

    turn = asyncio.run(_run())
    assert turn.task_relation == "revise"
    assert turn.verification_required
    assert turn.verification_targets == ["MCP总行数", "xlsx数据行数"]


def test_analyze_turn_intent_enforces_note_timezone_and_separates_note_from_user():
    llm = SimpleNamespace(type="llm", model="x")
    payload = (
        '{"intent":"data_query","reason":"取笔记","wants_deliverable":false,'
        '"query_goal":"获取10号笔记","metrics":[],"time_window":'
        '{"start_date":"2026-08-10","end_date":"2026-08-10",'
        '"inclusive_end_day":true,"tz":"ET","label":"错误的ET"}}'
    )
    mock = AsyncMock(return_value=payload)

    async def _run():
        with patch("app.services.llm_client.chat_completion", new=mock):
            return await analyze_turn_intent(
                llm,
                "获取10号笔记",
                context_note="首先时间是 北京时间 (北京时区)",
                now=datetime(2026, 8, 11, 4, 0, tzinfo=timezone.utc),
            )

    turn = asyncio.run(_run())
    assert turn.time_window["tz_key"] == "CST"
    assert turn.time_window["start_date"] == "2026-08-10"
    messages = mock.await_args.args[1]
    assert messages[-1]["role"] == "user"
    assert "北京时间" not in messages[-1]["content"]
    assert any("会话备注·约束" in m["content"] for m in messages if m["role"] == "system")


def test_non_json_fallback_resolves_day_in_note_timezone():
    llm = SimpleNamespace(type="llm", model="x")
    mock = AsyncMock(return_value="<think>分析中</think>不是 JSON")

    async def _run():
        with patch("app.services.llm_client.chat_completion", new=mock):
            return await analyze_turn_intent(
                llm,
                "获取10号笔记",
                context_note="默认使用北京时间",
                now=datetime(2026, 8, 11, 4, 0, tzinfo=timezone.utc),
                max_attempts=2,
            )

    turn = asyncio.run(_run())
    assert turn.source == "fallback"
    assert turn.query_goal == "获取10号笔记"
    assert turn.time_window["tz_key"] == "CST"
    assert turn.time_window["start_date"] == "2026-08-10"


def test_analyze_turn_intent_respects_task_despite_light_flag():
    llm = SimpleNamespace(type="llm", model="x")
    payload = (
        '{"intent":"other_tools","reason":"查日志","wants_deliverable":false,'
        '"query_goal":"获取系统日志","metrics":[],"time_window":null}'
    )

    async def _run():
        with patch(
            "app.services.llm_client.chat_completion",
            new=AsyncMock(return_value=payload),
        ):
            return await analyze_turn_intent(
                llm,
                "获取一下系统日志",
                is_light_chat=True,
            )

    turn = asyncio.run(_run())
    assert turn.intent == "other_tools"


def test_analyze_turn_intent_forces_chat_for_hello():
    llm = SimpleNamespace(type="llm", model="x")
    payload = (
        '{"intent":"data_query","reason":"误判","wants_deliverable":false,'
        '"query_goal":"","metrics":[],"time_window":null}'
    )

    async def _run():
        with patch(
            "app.services.llm_client.chat_completion",
            new=AsyncMock(return_value=payload),
        ):
            return await analyze_turn_intent(
                llm,
                "你好",
                is_light_chat=True,
            )

    turn = asyncio.run(_run())
    assert turn.intent == "chat"


def test_force_tools_override_for_rework_chat_mislabel():
    msg = "重新处理这三个问题并排查返奖数据缺失"
    assert looks_like_task_message(msg)
    assert should_force_tools_override(msg, llm_intent="chat")
    assert not should_force_tools_override("你好", llm_intent="chat")

    llm = SimpleNamespace(type="llm", model="x")
    payload = (
        '{"intent":"chat","reason":"误判寒暄","wants_deliverable":false,'
        '"query_goal":"","conversation_action":"respond","metrics":[],"time_window":null}'
    )

    async def _run():
        with patch(
            "app.services.llm_client.chat_completion",
            new=AsyncMock(return_value=payload),
        ):
            return await analyze_turn_intent(llm, msg)

    turn = asyncio.run(_run())
    assert turn.intent == "other_tools"
    assert turn.needs_tools


def test_analyze_turn_intent_retries_non_json_then_errors(caplog):
    import logging

    llm = SimpleNamespace(type="llm", model="x")
    mock = AsyncMock(return_value="not json at all")

    async def _run():
        with patch("app.services.llm_client.chat_completion", new=mock):
            with caplog.at_level(logging.ERROR):
                return await analyze_turn_intent(
                    llm,
                    "查一下注册人数",
                    agent_id="ag1",
                    session_id="sess1",
                    max_attempts=3,
                )

    turn = asyncio.run(_run())
    assert turn.source == "fallback"
    assert mock.await_count == 3
    assert any(
        "intent_router: fallback" in r.message
        and "agent=ag1" in r.message
        and "session=sess1" in r.message
        for r in caplog.records
        if r.levelno >= logging.ERROR
    )


def test_analyze_turn_intent_retries_schema_then_ok():
    llm = SimpleNamespace(type="llm", model="x")
    bad = '{"intent":"nope","reason":"","wants_deliverable":"yes"}'
    good = (
        '{"intent":"data_query","reason":"查数","wants_deliverable":false,'
        '"query_goal":"注册人数","metrics":["注册人数"],"time_window":null}'
    )
    mock = AsyncMock(side_effect=[bad, good])

    async def _run():
        with patch("app.services.llm_client.chat_completion", new=mock):
            return await analyze_turn_intent(
                llm,
                "注册人数",
                agent_id="a",
                session_id="s",
                max_attempts=3,
            )

    turn = asyncio.run(_run())
    assert turn.source == "llm"
    assert turn.intent == "data_query"
    assert mock.await_count == 2


def test_analyze_turn_intent_repairs_non_json_and_preserves_named_business_concept():
    llm = SimpleNamespace(type="llm", model="x")
    invalid = "<think>我需要先分析用户可能想看哪些运营指标，但尚未输出契约"
    valid = (
        '{"intent":"data_query","reason":"跨日期常规运营时间序列",'
        '"wants_deliverable":false,'
        '"query_goal":"按日查看8月1日至今的常规运营统计数据",'
        '"result_shape":"time_series","temporal_grain":"day",'
        '"metrics":[{"goal":"常规运营统计数据","kind":"other",'
        '"entity_hint":"operations"}],'
        '"time_window":{"start_date":"2026-08-01","end_date":"2026-08-14",'
        '"inclusive_end_day":true,"tz":"ET","label":"8月1日至今"}}'
    )
    mock = AsyncMock(side_effect=[invalid, valid])

    async def _run():
        with patch("app.services.llm_client.chat_completion", new=mock):
            return await analyze_turn_intent(
                llm,
                "请帮我查询一下，8月1日到今天的常规运营统计数据。",
                now=datetime(2026, 8, 14, 12, 0, tzinfo=timezone.utc),
            )

    turn = asyncio.run(_run())
    assert turn.source == "llm"
    assert turn.result_shape == "time_series"
    assert turn.temporal_grain == "day"
    assert turn.query_goal == "按日查看8月1日至今的常规运营统计数据"
    assert turn.metric_intents[0].goal == "常规运营统计数据"
    assert mock.await_count == 2
    retry_messages = mock.await_args_list[1].args[1]
    assert any(message["role"] == "assistant" and "尚未输出契约" in message["content"] for message in retry_messages)
    assert any(
        message["role"] == "user"
        and "non_json" in message["content"]
        and "保留当前用户消息中的命名业务概念" in message["content"]
        for message in retry_messages
    )
