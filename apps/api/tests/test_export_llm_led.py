"""OpenClaw-style: LLM-led export — honest fallback, shell card, skill-pointer coach."""

from pathlib import Path

from app.services.react_engine import (
    _export_next_action_coach,
    _format_export_final,
    _format_export_task_anchor,
    _format_shell_task_card,
    _parse_export_todos,
    _export_analyze_column_texts,
    _build_export_fallback_appendix,
    _is_type_b_export,
    _should_auto_accept_export_plan,
    _type_b_prefilled_plan_ready,
)
from app.services.export_column_plan import build_column_plan
from app.services.task_policy import skill_snapshot_for_prompt

_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "type_b_us_jul2026_brief.txt"


def test_fallback_final_is_honest_not_analyzed():
    md = _format_export_final(
        user_message="导出测试",
        file_rel="export_123.xlsx",
        mode="fallback",
        filter_condition="美国东部时间 2026-07-21 至 2026-07-31",
        analysis_md=_build_export_fallback_appendix(analyze_incomplete=True),
    )
    assert "原始回退" in md
    assert "分析交付" not in md
    assert "非" in md or "不是" in md or "原始" in md
    assert "### 完整性/缺口说明" in md


def test_platform_assisted_labeled():
    md = _format_export_final(
        user_message="导出测试",
        file_rel="用户分析_1.xlsx",
        mode="analyzed",
        platform_assisted=True,
        filter_condition="2026-07-21",
    )
    assert "平台辅助" in md
    assert "已完成！" in md
    assert "交付类型" not in md
    assert md.count("平台辅助 join") == 1


def test_platform_assisted_note_not_duplicated_when_appendix_prefixed():
    prefixed = (
        "> 说明：本文件由**平台辅助 join**生成（LLM SHELL 未产出合格中文分析表）。\n\n"
        "### 导出概况\n| 指标 | 数值 |\n| --- | --- |\n| 总用户数 | 1 |\n"
    )
    md = _format_export_final(
        user_message="导出测试",
        file_rel="用户分析_1.xlsx",
        mode="analyzed",
        platform_assisted=True,
        analysis_md=prefixed,
        filter_condition="2026-07-21",
    )
    assert md.count("平台辅助 join") == 1


def test_shell_task_card_lists_columns():
    brief = _FIXTURE.read_text(encoding="utf-8")
    cols = _export_analyze_column_texts(_parse_export_todos(brief))
    card = _format_shell_task_card(
        column_headers=cols,
        page_map="user → page_1.json",
        run_id="1785652192047",
    )
    assert "SHELL 写表任务卡" in card
    assert "/tmp/build_report.py" in card
    assert "注册时间" in card
    assert "是否有退款" in card
    assert "1785652192047" in card


def test_coach_is_skill_pointer_not_mcp_json_dump():
    text = _export_next_action_coach(
        phase="fetch",
        fetched_view_pages={"view_result_user_info": 1},
        target_roles=["user", "pay", "cash", "bet", "channel"],
        covered_roles=["user"],
        missing_roles=["pay", "cash", "bet", "channel"],
        time_window={"label": "美东 2026-07-21 至 2026-07-31"},
        column_headers=["注册时间", "用户ID"],
        budget_left=8,
    )
    assert "export-report" in text or "SKILL_MD" in text
    assert "仍缺 role" in text
    assert "pay" in text and "cash" in text
    # No full MCP JSON template blobs
    assert "query_ads_view {" not in text
    assert '"sql":' not in text


def test_anchor_still_has_14_cols():
    brief = _FIXTURE.read_text(encoding="utf-8")
    cols = _export_analyze_column_texts(_parse_export_todos(brief))
    assert len(cols) == 14
    anchor = _format_export_task_anchor(
        source_brief=brief,
        time_window={"label": "美国东部时间(UTC-4) 2026-07-21 至 2026-07-31"},
        column_headers=cols,
        target_roles=["user", "pay", "cash", "bet", "channel", "game"],
    )
    assert "14." in anchor
    assert "任务锚点" in anchor


def test_skill_snapshot_prefers_export_sop():
    snap = skill_snapshot_for_prompt([
        ("other-skill", "# Other\n- hello\n"),
        (
            "ads-sync-hub",
            "# ADS\n## 拉取顺序\n- user 短页\n必须：pay/cash/bet\n"
            "view_result_pay_order_log\nSHELL to_excel\n",
        ),
    ])
    assert "ads-sync" in snap.lower() or "拉取顺序" in snap
    assert "SHELL" in snap or "to_excel" in snap or "必须" in snap


def test_type_b_column_plan_no_longer_skips_schema_discovery():
    todos = _parse_export_todos(
        "导出用户：1. 用户ID 2. 总充值金额 3. 总提现金额 4. 注册渠道"
    )
    plan = build_column_plan(_export_analyze_column_texts(todos))
    assert not _type_b_prefilled_plan_ready(
        todos,
        ["user", "pay", "cash", "channel"],
        "multi_fact",
        column_plan=plan,
    )


def test_is_type_b_export_opens_full_fetch_gate():
    plan = build_column_plan(["用户ID", "总充值金额", "总提现金额"])
    assert _is_type_b_export("multi_fact", plan)
    assert _is_type_b_export("light_identity", plan)
    assert not _is_type_b_export("single_view", plan)
    assert not _is_type_b_export("multi_fact", None)
    assert not _is_type_b_export("multi_fact", [])


def test_structured_contract_auto_accepts_export_plan_before_schema_complete():
    plan = build_column_plan(["用户ID", "总充值金额"])
    assert _should_auto_accept_export_plan(
        export_phase="plan",
        plan_done=False,
        export_view_mode="multi_fact",
        column_plan=plan,
        task_validation_status="pass",
        schema_summary={"required": True, "complete": True},
    )
    assert _should_auto_accept_export_plan(
        export_phase="plan",
        plan_done=False,
        export_view_mode="multi_fact",
        column_plan=plan,
        task_validation_status="pass",
        schema_summary={"required": True, "complete": False},
    )
    assert not _should_auto_accept_export_plan(
        export_phase="plan",
        plan_done=False,
        export_view_mode="single_view",
        column_plan=plan,
        task_validation_status="pass",
        schema_summary={"required": True, "complete": True},
    )
