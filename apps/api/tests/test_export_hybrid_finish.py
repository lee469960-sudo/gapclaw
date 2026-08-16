"""Hybrid export finish: roles→analyze, engine-run script, Type-B skip PLAN, MCP reopen."""

from pathlib import Path

from app.services.react_engine import (
    _EXPORT_ANALYZE_MAX_ROUNDS,
    _export_analyze_max_rounds,
    _format_export_final,
    _parse_export_todos,
    _export_analyze_column_texts,
    _type_b_prefilled_plan_ready,
    _shell_looks_like_explore,
    _missing_export_roles,
)
from app.services.workplace import find_export_build_scripts, ensure_workplace

_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "type_b_us_jul2026_brief.txt"


def test_analyze_max_rounds_raised_for_14_cols():
    brief = _FIXTURE.read_text(encoding="utf-8")
    todos = _parse_export_todos(brief)
    assert _EXPORT_ANALYZE_MAX_ROUNDS >= 12
    assert _export_analyze_max_rounds(todos) >= 12


def test_type_b_prefilled_plan_requires_schema_discovery():
    brief = _FIXTURE.read_text(encoding="utf-8")
    todos = _parse_export_todos(brief)
    cols = _export_analyze_column_texts(todos)
    assert len(cols) == 14
    roles = ["user", "pay", "cash", "bet", "channel", "game"]
    assert _type_b_prefilled_plan_ready(todos, roles, "multi_fact") is False
    assert _type_b_prefilled_plan_ready(todos, roles, "single_view") is False
    assert _type_b_prefilled_plan_ready(todos, ["user"], "multi_fact") is False


def test_engine_executed_final_label():
    md = _format_export_final(
        user_message="导出测试",
        file_rel="用户分析_1.xlsx",
        mode="analyzed",
        engine_executed=True,
        filter_condition="2026-07-21",
    )
    assert "引擎代执行" in md
    assert "原始回退" not in md
    assert "已完成！" in md
    assert "交付类型" not in md


def test_find_export_build_scripts(tmp_path, monkeypatch):
    sid = "test-hybrid-sbx"
    # Point workplace root at tmp
    from app.services import workplace as wp

    monkeypatch.setattr(wp, "_wp_root", lambda _sid: tmp_path / _sid / "workplace")
    root = ensure_workplace(sid)
    tmp = root / "tmp"
    tmp.mkdir(parents=True)
    script = tmp / "build_report.py"
    script.write_text("print('ok')\n", encoding="utf-8")
    found = find_export_build_scripts(sid)
    assert found
    assert found[0].name == "build_report.py"


def test_explore_shell_detected():
    assert _shell_looks_like_explore("SHELL: ls task/") is True
    assert _shell_looks_like_explore("SHELL: python3 /tmp/build_report.py") is False


def test_missing_roles_none_sandbox():
    assert _missing_export_roles(None, "r1", ["pay"]) == ["pay"]
