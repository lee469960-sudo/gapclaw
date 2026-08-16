"""Unit tests for task_policy detection and resource-intent routing."""

from app.services.task_policy import (
    ViewIntentRoute,
    classify_view_intent_llm,
    detect_export_report_task,
    detect_task_policy,
    parse_generic_plan,
)


def test_name_only_export_is_generic():
    msg = "导出用户数据，只要：\n1. 用户ID\n2. 姓\n3. 名"
    ok, reason = detect_export_report_task(msg)
    assert ok is False
    assert detect_task_policy(msg).policy_id == "generic"
    assert reason == "light_identity"


def test_full_register_report_is_export():
    msg = (
        "导出美国时间 2026-07-21 至 2026-07-27 新增注册用户报表，列：\n"
        "1. 注册时间\n2. 用户ID\n3. 注册渠道\n4. 总充值金额\n"
        "5. 总提现金额\n6. 流水倍数\n7. 是否有退款\n8. SC投注金额最多的游戏\n"
    )
    ok, reason = detect_export_report_task(msg)
    assert ok is True
    assert detect_task_policy(msg).export_like is True


def test_parse_generic_plan():
    reply = (
        "PLAN:\n"
        "- 目标: 查一下渠道列表\n"
        "- 步骤: list_ads_views → query\n"
        "- 完成标准: 回答含渠道数量\n"
        "- 工具预算: 最多 5 次\n"
    )
    parsed = parse_generic_plan(reply)
    assert parsed is not None
    assert parsed["budget"] == 5
    assert any("渠道" in c for c in parsed["completion_lines"])


def test_classify_view_intent_llm_no_llm_returns_multi_fact_mode():
    route = ViewIntentRoute(mode="ambiguous", pinned_views=[], reason="x")
    out = __import__("asyncio").run(
        classify_view_intent_llm(
            None,
            None,
            "导出封禁用户数据",
            route,
            timeout=1,
        )
    )
    assert out.mode == "multi_fact"
