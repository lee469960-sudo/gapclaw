"""16-col 充值用户: flex time window, pay create_time anchor, missing-user gate."""

from datetime import datetime, timedelta, timezone

from app.services.react_engine import (
    _blocking_roles_for_analyzed_delivery,
    _facts_ready_for_analyzed_delivery,
    _export_analyze_column_texts,
    _format_export_task_anchor,
    _is_pay_cohort_brief,
    _parse_export_time_window,
    _parse_export_todos,
)


_BRIEF_16 = (
    "导出美国时间6-1至8至今充值用户数据为excel\n"
    "1.注册时间\n"
    "2.用户ID\n"
    "3.注册渠道\n"
    "4.总充值金额\n"
    "5.总提现金额\n"
    "6.当前余额(SC)\n"
    "7.总下注金额(SC)\n"
    "8.总返奖金额(SC)\n"
    "9.下注次数(SC投注次数)\n"
    "10.流水倍数(时间段内总下注金额/总充值金额)\n"
    "11.连续充值次数\n"
    "12.SC投注金额最多的游戏\n"
    "13.是否被封禁\n"
    "14.是否有退款\n"
    "15.充值银行卡数量(APTPAY充值渠道使用的银行卡数量，需排重)\n"
    "16.提现银行卡数量(APTPAY提现渠道使用的银行卡数量，需排重)"
)

_BRIEF_16_TO_NOW = (
    "导出美国时间6-1日至今充值用户数据为excel,输出列如下:\n"
    "1.注册时间\n"
    "2.用户ID\n"
    "3.注册渠道\n"
    "4.总充值金额\n"
    "5.总提现金额\n"
    "6.当前余额(SC)\n"
    "7.总下注金额(SC)\n"
    "8.总返奖金额(SC)\n"
    "9.下注次数(SC投注次数)\n"
    "10.流水倍数(时间段内总下注金额/总充值金额)\n"
    "11.连续充值次数(两次游戏行为之间的充值次数，需要最多的次数)\n"
    "12.SC投注金额最多的游戏(该用户在游戏中SC投注最多的游戏，格式：游戏名称，游戏ID)\n"
    "13.是否被封禁(封禁类型：充值/提现/登录，根据封禁类型显示)\n"
    "14.是否有退款(是否有退款成功的订单，有则显示文本\"有\"，无则空)\n"
    "15.充值银行卡数量(APTPAY充值渠道使用的银行卡数量，需排重)\n"
    "16.提现银行卡数量(APTPAY提现渠道使用的银行卡数量，需排重)"
)


def test_flex_us_time_md_till_now_parses_ms():
    tw = _parse_export_time_window("导出美国时间6-1至8至今充值用户数据为excel")
    assert tw is not None
    assert tw["start_ms"] < tw["end_ms"]
    assert tw["utc_offset_hours"] == -4
    assert "美国东部" in tw["tz_label"] or "UTC-4" in tw["tz_label"]
    # Start = June 1 current year in ET
    tz = timezone(timedelta(hours=-4))
    now = datetime.now(tz)
    start = datetime.fromtimestamp(tw["start_ms"] / 1000.0, tz=tz)
    assert start.month == 6 and start.day == 1 and start.year == now.year
    # End exclusive = tomorrow 00:00 ET (至今)
    end = datetime.fromtimestamp(tw["end_ms"] / 1000.0, tz=tz)
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    assert end == today + timedelta(days=1)
    assert f"{now.year}-06-01" in tw["label"]
    assert tw["end_ms"] > tw["start_ms"]


def test_exact_16_col_pay_cohort_to_now_prompt_parses_contract():
    todos = _parse_export_todos(_BRIEF_16_TO_NOW)
    cols = _export_analyze_column_texts(todos)
    assert len(cols) == 16
    assert cols[0] == "注册时间"
    assert cols[-1].startswith("提现银行卡数量")

    tw = _parse_export_time_window(_BRIEF_16_TO_NOW)
    assert tw is not None
    assert tw["cohort"] == "pay"
    assert tw["utc_offset_hours"] == -4
    tz = timezone(timedelta(hours=-4))
    now = datetime.now(tz)
    start = datetime.fromtimestamp(tw["start_ms"] / 1000.0, tz=tz)
    end = datetime.fromtimestamp(tw["end_ms"] / 1000.0, tz=tz)
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    assert start.month == 6 and start.day == 1 and start.year == now.year
    assert end == today + timedelta(days=1)
    assert f"{now.year}-06-01" in tw["label"]


def test_pay_cohort_anchor_keeps_time_contract_resource_agnostic():
    assert _is_pay_cohort_brief(_BRIEF_16)
    tw = _parse_export_time_window(_BRIEF_16)
    assert tw is not None
    assert tw.get("cohort") == "pay"
    anchor = _format_export_task_anchor(
        source_brief=_BRIEF_16,
        time_window=tw,
        column_headers=["注册时间", "用户ID", "充值银行卡数量"],
        target_roles=["user", "pay", "cash", "bet", "channel", "game"],
    )
    assert "充值用户" in anchor
    assert "充值用户" in anchor
    assert "view_result_pay_order_log" not in anchor


def test_register_cohort_keeps_time_contract_resource_agnostic():
    brief = "导出美国时间2026-07-21至2026-07-31内新增注册用户数据"
    tw = _parse_export_time_window(brief)
    assert tw is not None
    assert tw.get("cohort") == "register"
    assert tw["start_ms"] < tw["end_ms"]


def test_contract_gaps_block_analyzed_delivery_without_role_exceptions():
    roles = []
    assert not _facts_ready_for_analyzed_delivery(
        export_target_roles=roles,
        missing_roles=["user"],
    )
    assert _blocking_roles_for_analyzed_delivery(
        export_target_roles=roles,
        missing_roles=["user"],
    ) == ["user"]
    # Any declared gap is handled uniformly.
    assert not _facts_ready_for_analyzed_delivery(
        export_target_roles=roles,
        missing_roles=["cash"],
    )
    assert not _facts_ready_for_analyzed_delivery(
        export_target_roles=roles,
        missing_roles=["channel"],
    )
    # Anti-example 1785758080386 shape: facts present, user missing
    assert not _facts_ready_for_analyzed_delivery(
        export_target_roles=roles,
        missing_roles=["user"],
    )


def test_full_year_range_unchanged():
    tw = _parse_export_time_window(
        "导出美国时间2026-07-21至2026-07-31内新增注册用户数据为Excel"
    )
    assert tw is not None
    assert "2026-07-21" in tw["label"]
    assert "2026-07-31" in tw["label"]
    assert tw["cohort"] == "register"
