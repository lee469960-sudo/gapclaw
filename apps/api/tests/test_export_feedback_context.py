"""Export revision context preserves the prior contract and time window."""

import json
from pathlib import Path

from app.services.react_engine import (
    _build_export_repair_message,
    _build_export_repair_system_hint,
    _format_export_final,
    _hydrate_time_window_from_state,
    _parse_export_todos,
    _resolve_repair_source_brief,
    _export_task_title,
)
from app.services.skill_lesson import (
    find_latest_run_state,
    find_repair_base_run_state,
    infer_repair_gaps,
)


def test_repair_message_clean_brief_keeps_filter_and_columns():
    prior = (
        "导出美国时间2026-07-21至2026-07-31内新增注册用户数据为Excel,输出列如下:\n"
        "1.注册时间\n"
        "2.用户ID\n"
        "3.注册渠道\n"
        "4.总充值金额\n"
        "5.总提现金额\n"
        "6.当前余额(SC)\n"
        "7.总下注金额(SC)\n"
        "8.总返奖金额(SC)\n"
        "9.下注次数(SC投注次数)\n"
        "10.流水倍数\n"
    )
    prior_state = {
        "source_brief": prior,
        "missing_roles": [],
        "covered_roles": ["user", "pay", "cash"],
        "target_roles": ["user", "pay", "cash", "bet", "channel", "game"],
        "fetched_view_pages": {
            "view_result_user_info": 1,
            "view_result_pay_order_log": 1,
            "view_result_cash_order_log": 1,
        },
        "time_window": {
            "label": "美国东部时间(UTC-4) 2026-07-21 至 2026-07-31",
            "start_ms": 1,
            "end_ms": 2,
            "tz_label": "美国东部时间(UTC-4)",
        },
        "analyze_columns": [
            "注册时间", "用户ID", "注册渠道", "总充值金额", "总提现金额",
        ],
        "repair_plan": {
            "status": "repairable",
            "actions": [
                {
                    "action_type": "fetch_noncore_or_keep_incomplete",
                    "role": "bet",
                    "column": "总下注金额(SC)",
                    "reason": "非核心列未完整：总下注金额(SC)",
                }
            ],
            "preserve_done_node_keys": ["agg:pay:pay_sum"],
            "skip_failed_node_keys": ["top_n:bet:game"],
        },
    }
    synthetic, filt = _build_export_repair_message(
        "按缺口补齐重新导出",
        prior_user="ignored when source_brief set",
        prior_state=prior_state,
    )
    # Filter label is resolved later via _resolve_export_time_window — do not lock prior TW here
    assert filt == ""
    assert "- 请优先补齐" not in synthetic
    assert "上轮已覆盖" not in synthetic
    assert "【本轮补齐】按缺口重导" in synthetic
    assert "这个数据不全" not in synthetic

    hint = _build_export_repair_system_hint(
        user_message="按缺口补齐重新导出",
        prior_state=prior_state,
        prior_run_id="1785642565039",
    )
    assert "bet" in hint
    assert "RepairPlan" in hint
    assert "fetch_noncore_or_keep_incomplete" in hint
    assert "agg:pay:pay_sum" not in hint
    assert "top_n:bet:game" not in hint
    assert "禁止把「数据不全」当作筛选条件" in hint

    # Column parse must keep real headers, not repair meta
    todos = _parse_export_todos(synthetic.split("【本轮补齐】")[0])
    cols = [t["text"] for t in todos if t.get("phase") == "analyze"]
    assert "注册时间" in cols
    assert "用户ID" in cols
    assert not any("请优先补齐" in c for c in cols)


def test_resolve_source_brief_prefers_run_state():
    brief = _resolve_repair_source_brief(
        prior_user="chat prior",
        prior_state={"source_brief": "state brief 2026-07-21"},
    )
    assert brief == "state brief 2026-07-21"


def test_export_task_title_inherits_prior_title_for_revision():
    assert _export_task_title(
        query_goal="修复银行卡数量并重新导出",
        source_brief="本轮修复",
        prior_state={"task_title": "充值用户分析"},
    ) == "充值用户分析"


def test_export_task_title_uses_model_goal_for_new_task():
    assert _export_task_title(
        query_goal="导出美国充值用户分析表",
        source_brief="长篇原始需求",
    ) == "导出美国充值用户分析表"


def test_hydrate_time_window_from_state():
    tw = _hydrate_time_window_from_state({
        "time_window": {
            "label": "美国东部时间(UTC-4) 2026-07-21 至 2026-07-31",
            "start_ms": 10,
            "end_ms": 20,
            "tz_label": "美国东部时间(UTC-4)",
        },
    })
    assert tw is not None
    assert tw["start_ms"] == 10
    assert "2026-07-21" in tw["label"]


def test_infer_repair_gaps_when_missing_empty():
    gaps = infer_repair_gaps({
        "missing_roles": [],
        "covered_roles": ["user", "pay"],
        "target_roles": ["user", "pay", "cash", "bet"],
        "fetched_view_pages": {
            "view_result_user_info": 1,
            "view_result_pay_order_log": 1,
        },
    })
    assert "cash" in gaps
    assert "bet" in gaps


def test_infer_repair_gaps_zero_page_facts_without_target():
    gaps = infer_repair_gaps({
        "missing_roles": [],
        "covered_roles": [],
        "target_roles": [],
        "fetched_view_pages": {},
    })
    assert gaps == []


def test_format_final_uses_explicit_filter():
    out = _format_export_final(
        user_message="导出测试",
        file_rel="a.xlsx",
        filter_condition="美国东部时间 2026-07-21 至 2026-07-31",
        analysis_md="ok",
    )
    assert "2026-07-21" in out


def test_find_latest_run_state(tmp_path, monkeypatch):
    sid = "sbx_fb"
    root = tmp_path / sid
    older = root / "task" / "111"
    newer = root / "task" / "222"
    older.mkdir(parents=True)
    newer.mkdir(parents=True)
    (older / "_run_state.json").write_text(
        '{"missing_roles":["pay"],"deliverable":"old.xlsx"}', encoding="utf-8",
    )
    (newer / "_run_state.json").write_text(
        '{"missing_roles":["cash"],"deliverable":"new.xlsx"}', encoding="utf-8",
    )
    monkeypatch.setattr(
        "app.services.skill_lesson.ensure_workplace",
        lambda s: root if s == sid else Path("/nope"),
    )
    rid, st = find_latest_run_state(sid)
    assert rid == "222"
    assert st["missing_roles"] == ["cash"]


def test_find_repair_base_skips_empty_shell(tmp_path, monkeypatch):
    sid = "sbx_repair"
    root = tmp_path / sid
    good = root / "task" / "1785642565039"
    bad = root / "task" / "1785643674602"
    good.mkdir(parents=True)
    bad.mkdir(parents=True)
    (good / "_run_state.json").write_text(
        json.dumps({
            "phase": "fetch",
            "mcp_query_count": 4,
            "source_brief": "导出 2026-07-21 至 2026-07-31\n1.用户ID\n2.充值",
            "covered_roles": ["user", "pay", "cash"],
            "missing_roles": [],
            "fetched_view_pages": {
                "view_result_user_info": 1,
                "view_result_pay_order_log": 1,
                "view_result_cash_order_log": 1,
            },
            "target_roles": ["user", "pay", "cash", "bet"],
        }),
        encoding="utf-8",
    )
    (bad / "_run_state.json").write_text(
        json.dumps({
            "phase": "discover",
            "mcp_query_count": 0,
            "fallback": True,
            "covered_roles": [],
            "missing_roles": ["user", "pay", "cash", "bet"],
            "fetched_view_pages": {},
            "todos": [
                {"phase": "analyze", "text": "请优先补齐仍缺 role：上一轮未齐套角色"},
            ],
        }),
        encoding="utf-8",
    )
    # newer mtime on bad shell
    import os
    import time
    now = time.time()
    os.utime(good / "_run_state.json", (now - 100, now - 100))
    os.utime(bad / "_run_state.json", (now, now))

    monkeypatch.setattr(
        "app.services.skill_lesson.ensure_workplace",
        lambda s: root if s == sid else Path("/nope"),
    )
    # latest is the bad shell
    latest = find_latest_run_state(sid)
    assert latest and latest[0] == "1785643674602"
    # repair base skips it
    base = find_repair_base_run_state(sid)
    assert base is not None
    assert base[0] == "1785642565039"
    assert "2026-07-21" in base[1]["source_brief"]


def test_find_repair_base_skips_query_failed_source_only_shell(tmp_path, monkeypatch):
    sid = "sbx_contract_resume"
    root = tmp_path / sid
    valid = root / "task" / "100"
    empty = root / "task" / "200"
    valid.mkdir(parents=True)
    empty.mkdir(parents=True)
    (valid / "_run_state.json").write_text(json.dumps({
        "phase": "finalize",
        "completeness": "query_failed",
        "source_brief": "导出六列数据",
        "analyze_columns": ["用户ID", "注册时间"],
        "export_contract": {
            "task_spec": {"requested_columns": ["用户ID", "注册时间"]},
        },
    }), encoding="utf-8")
    (empty / "_run_state.json").write_text(json.dumps({
        "phase": "finalize",
        "completeness": "query_failed",
        "source_brief": "全量导出 excel",
        "analyze_columns": [],
        "export_contract": {
            "task_spec": {"requested_columns": []},
        },
    }), encoding="utf-8")
    import os
    import time
    now = time.time()
    os.utime(valid / "_run_state.json", (now - 10, now - 10))
    os.utime(empty / "_run_state.json", (now, now))
    monkeypatch.setattr(
        "app.services.skill_lesson.ensure_workplace",
        lambda sandbox: root if sandbox == sid else Path("/nope"),
    )

    base = find_repair_base_run_state(sid)
    assert base is not None
    assert base[0] == "100"
