"""Export skill lesson drafts from process gaps (cash starve / two-run diff)."""

from pathlib import Path

from app.services.skill_lesson import (
    build_export_skill_lesson,
    format_skill_lesson_final_section,
    gap_tags_for_rolling,
    materialize_export_skill_lesson,
    write_export_skill_lesson,
)


def test_cash_starved_lesson_has_copyable_sop():
    md = build_export_skill_lesson(
        run_id="1785428966776",
        user_message="导出用户充值提现投注分析表",
        mode="fallback",
        deliverable="export_1785428966776.xlsx",
        covered_roles=["user", "pay", "bet", "channel", "game"],
        missing_roles=["cash"],
        fetched_view_pages={
            "view_result_pay_order_log": 3,
            "view_result_user_bet_log": 2,
            "view_result_user_info": 3,
        },
        fallback=True,
        prev_run_id="prev1",
        prev_covered=["user", "pay", "bet"],
        prev_missing=["cash", "channel"],
    )
    assert "### 过程对比" in md
    assert "cash" in md
    assert "fact_starved" in md
    assert "建议写入 Skill" in md
    assert "list/describe" in md
    assert "禁止深翻" in md
    assert "Type B" in md or "各至少 1 页" in md


def test_two_run_diff_shows_newly_covered():
    md = build_export_skill_lesson(
        run_id="run2",
        mode="analyzed",
        covered_roles=["user", "pay", "cash", "bet"],
        missing_roles=[],
        prev_run_id="run1",
        prev_covered=["user", "pay"],
        prev_missing=["cash", "bet"],
    )
    assert "本轮新增补齐：cash、bet" in md or ("cash" in md and "新增补齐" in md)
    assert "本轮仍缺：无" in md


def test_lesson_includes_repair_plan_section():
    md = build_export_skill_lesson(
        run_id="repair1",
        mode="analyzed",
        covered_roles=["user", "pay"],
        missing_roles=["bet"],
        repair_plan={
            "status": "repairable",
            "source_status": "repairable",
            "actions": [
                {
                    "action_type": "fetch_noncore_or_keep_incomplete",
                    "role": "bet",
                    "column": "总下注金额(SC)",
                    "node_keys": ["agg:bet:bet_sum"],
                    "views": ["view_result_gameuser_betstat_everyday_bygame"],
                    "reason": "非核心列未完整：总下注金额(SC)",
                }
            ],
            "preserve_done_node_keys": ["agg:pay:pay_sum"],
            "skip_failed_node_keys": ["top_n:bet:game"],
        },
    )
    assert "结构化修复计划" in md
    assert "repair_plan:fetch_noncore_or_keep_incomplete" in md
    assert "agg:bet:bet_sum" in md
    assert "preserve_done_node_keys" in md
    assert "skip_failed_node_keys" in md

    sec = format_skill_lesson_final_section(
        md,
        run_id="repair1",
        missing_roles=["bet"],
        fallback=False,
    )
    assert "repair_plan:fetch_noncore_or_keep_incomplete" in sec


def test_final_section_skip_when_complete():
    md = build_export_skill_lesson(
        run_id="ok1",
        mode="analyzed",
        covered_roles=["user", "pay", "cash"],
        missing_roles=[],
        fallback=False,
    )
    sec = format_skill_lesson_final_section(
        md, run_id="ok1", missing_roles=[], fallback=False,
    )
    assert "### 建议写入 Skill" in sec
    assert "无缺口可跳过" in sec


def test_gap_tags_for_rolling():
    assert "[缺口:cash]" in gap_tags_for_rolling(missing_roles=["cash"], fallback=False)
    assert "[原始回退]" in gap_tags_for_rolling(missing_roles=[], fallback=True)
    assert gap_tags_for_rolling(missing_roles=[], fallback=False) == ""


def test_write_lesson_paths(tmp_path, monkeypatch):
    sid = "sbx_lesson_test"
    wp = tmp_path / sid
    wp.mkdir()

    def _fake_ensure(sandbox_id: str) -> Path:
        root = tmp_path / sandbox_id
        root.mkdir(parents=True, exist_ok=True)
        return root

    monkeypatch.setattr(
        "app.services.skill_lesson.ensure_workplace",
        _fake_ensure,
    )
    # previous run state for process compare
    prev = _fake_ensure(sid) / "task" / "run_prev"
    prev.mkdir(parents=True)
    (prev / "_run_state.json").write_text(
        '{"covered_roles":["user","pay"],"missing_roles":["cash"]}',
        encoding="utf-8",
    )

    md, paths, section = materialize_export_skill_lesson(
        sid,
        "run_cur",
        user_message="要 cash",
        mode="fallback",
        covered_roles=["user", "pay", "bet"],
        missing_roles=["cash"],
        fetched_view_pages={"view_result_pay_order_log": 3},
        fallback=True,
    )
    assert "lessons/export_run_cur.md" in paths
    assert "task/run_cur/_skill_lesson.md" in paths
    lesson_file = _fake_ensure(sid) / "lessons" / "export_run_cur.md"
    assert lesson_file.is_file()
    text = lesson_file.read_text(encoding="utf-8")
    assert "list/describe" in text
    assert "过程对比" in text
    assert "### 建议写入 Skill" in section
    assert write_export_skill_lesson(sid, "run_cur", md)
