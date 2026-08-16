"""FINAL export presentation: 导出概况 + 字段说明 + download."""

from pathlib import Path

import openpyxl

from app.services.export_column_plan import (
    apply_binding_hints_to_column_plan,
    apply_schema_hints_to_column_plan,
    build_column_plan,
)
from app.services.react_engine import (
    _append_mcp_markdown,
    _build_export_analysis_appendix,
    _format_export_final,
    _strip_gfm_tables_preserving_appendix,
)


_GOLDEN_16 = [
    "注册时间",
    "用户ID",
    "注册渠道",
    "总充值金额",
    "总提现金额",
    "当前余额(SC)",
    "总下注金额(SC)",
    "总返奖金额(SC)",
    "下注次数",
    "流水倍数",
    "连续充值次数",
    "SC投注金额最多的游戏",
    "是否被封禁",
    "是否有退款",
    "充值银行卡数量",
    "提现银行卡数量",
]


def _write_fixture_xlsx(path: Path) -> None:
    wb = openpyxl.Workbook()
    ws = wb.active
    headers = [
        "用户ID",
        "总充值金额",
        "总下注金额(SC)",
        "是否被封禁",
        "是否有退款",
        "充值银行卡数量",
        "提现银行卡数量",
    ]
    ws.append(headers)
    ws.append([1, 100.5, 10, "充值", "有", 2, 1])
    ws.append([2, 50.0, 0, "", "", 0, 0])
    ws.append([3, 200.25, 5, "登录", "", 1, 0])
    wb.save(path)


def test_format_export_final_screenshot_shape():
    body = _format_export_final(
        user_message="导出昨天充值提现报表",
        file_rel="用户分析_1.xlsx",
        file_size=2048,
        row_hint=1200,
        mode="analyzed",
        todo_done=10,
        todo_total=12,
        filter_condition="美国东部时间 2026-07-01 至 2026-07-31",
    )
    assert "已完成！" in body
    assert "ClickHouse" in body or "MCP" in body
    assert "### 导出概况" in body
    assert "### 下载文件" in body
    assert "`用户分析_1.xlsx`" in body or "用户分析_1.xlsx" in body
    assert "交付类型" not in body
    assert "TODO 完成" not in body
    assert "### 统计结果" not in body


def test_appendix_from_xlsx_has_overview_and_field_docs(tmp_path):
    xlsx = tmp_path / "users.xlsx"
    _write_fixture_xlsx(xlsx)
    headers = [
        "用户ID",
        "总充值金额",
        "总下注金额(SC)",
        "是否被封禁",
        "是否有退款",
        "充值银行卡数量",
        "提现银行卡数量",
    ]
    plan = build_column_plan(_GOLDEN_16)
    md = _build_export_analysis_appendix(
        path=xlsx,
        headers=headers,
        todos=[{"phase": "analyze", "text": h} for h in _GOLDEN_16],
        time_window={"label": "美国东部时间 2026-07-01 至 07-31"},
        row_hint=3,
        column_plan=plan,
        file_size=xlsx.stat().st_size,
    )
    assert "### 导出概况" in md
    assert "### 字段说明" in md
    assert "数据来源" in md
    assert "统计方法" in md
    assert "待 MCP list/describe + LLM 绑定" in md
    assert "view_result_pay_order_log" not in md
    assert "$350.75" in md or "350.75" in md  # 100.5+50+200.25
    assert "总用户数" in md
    assert "有 SC 下注" in md
    assert "被封禁" in md
    assert "有退款" in md


def test_golden16_source_method_keywords():
    plan = build_column_plan(_GOLDEN_16)
    blob = "\n".join(
        f"{c.get('数据来源')} {c.get('统计方法')}" for c in plan
    )
    assert "view_result_pay_order_log" in blob
    assert "status=2" in blob
    assert "channel_id=11" in blob
    assert "status=4" in blob or "status=4" in str(plan)


def test_appendix_uses_resolved_column_docs_after_binding_and_describe(tmp_path):
    xlsx = tmp_path / "ban.xlsx"
    _write_fixture_xlsx(xlsx)
    plan = build_column_plan(["是否被封禁"])
    plan = apply_binding_hints_to_column_plan(
        plan,
        bindings=[{"goal": "是否被封禁", "resource": "view_result_user_ban_status"}],
    )
    plan = apply_schema_hints_to_column_plan(
        plan,
        schema_hints={
            "view_result_user_ban_status": {
                "fields": ["uid", "ban_type"],
                "comments": {"ban_type": "封禁类型"},
                "view_comment": "用户封禁状态",
            }
        },
    )
    md = _build_export_analysis_appendix(
        path=xlsx,
        headers=["是否被封禁"],
        todos=[{"phase": "analyze", "text": "是否被封禁"}],
        time_window={"label": "美国东部时间 2026-07-01 至 07-31"},
        row_hint=3,
        column_plan=plan,
        file_size=xlsx.stat().st_size,
    )
    assert "`view_result_user_ban_status`" in md
    assert "待 MCP list/describe + LLM 绑定" not in md


def test_append_mcp_views_only_no_source_rows():
    final = (
        "### 导出概况\n| 指标 | 数值 |\n| --- | --- |\n| 总用户数 | 3 |\n\n"
        "### 字段说明（共 1 列）\n| # | 列名 | 数据来源 | 统计方法 |"
    )
    mcp = [
        {"tool": "query_ads_view", "view": "view_result_pay_order_log", "row_count": 100},
        {"tool": "query_ads_view", "view": "view_result_cash_order_log", "row_count": 80},
        {"tool": "query_ads_view", "view": "view_result_pay_order_log", "row_count": 50},
    ]
    out = _append_mcp_markdown(
        final,
        mcp,
        export_like=True,
        fetched_view_pages={"view_result_user_info": 2},
    )
    assert "### 数据来源（MCP 视图）" in out
    assert "`view_result_pay_order_log`" in out
    assert "`view_result_cash_order_log`" in out
    assert "`view_result_user_info`" in out
    assert "### 数据 ·" not in out


def test_overview_table_survives_strip():
    body = _format_export_final(
        user_message="x",
        file_rel="a.xlsx",
        row_hint=3,
        mode="analyzed",
        analysis_md=(
            "### 导出概况\n- ok\n\n"
            "### 字段说明（共 1 列）\n| # | 列名 | 数据来源 | 统计方法 |\n"
            "| --- | --- | --- | --- |\n| 1 | 用户ID | `view_result_user_info` | uid |"
        ),
    )
    junk = "样例\n| a | b |\n| --- | --- |\n| 1 | 2 |\n\n" + body
    cleaned = _strip_gfm_tables_preserving_appendix(junk)
    assert "### 导出概况" in cleaned
    assert "### 字段说明" in cleaned
    assert "已完成！" in cleaned
