"""Auto-merge high-confidence lessons into export-report auto-lessons."""

from pathlib import Path

from app.services.skill_lesson import (
    auto_merge_export_lesson_into_skill,
    build_export_skill_lesson,
    extract_auto_lessons_block,
    lesson_is_high_confidence,
    materialize_export_skill_lesson,
)


def _skill_pkg(tmp_path: Path) -> Path:
    pkg = tmp_path / "ads-sync-hub"
    refs = pkg / "references"
    refs.mkdir(parents=True)
    (refs / "export-report.md").write_text(
        "# ADS 导出指引\n\n## 固定节\n\n"
        "<!-- auto-lessons -->\n"
        "（高置信反例自动追加）\n"
        "<!-- /auto-lessons -->\n",
        encoding="utf-8",
    )
    return pkg


def test_lesson_high_confidence_markers():
    assert lesson_is_high_confidence("- mcp_repeat: format_clause ×3")
    assert lesson_is_high_confidence("- row_incomplete：交付 1499 << 3748")
    assert lesson_is_high_confidence("- fact_starved：缺 cash")
    assert not lesson_is_high_confidence("#### 成功要点（可固化）\n1. ok\n")


def test_auto_merge_writes_and_dedupes_same_run(tmp_path):
    pkg = _skill_pkg(tmp_path)
    md = build_export_skill_lesson(
        run_id="run_a1",
        user_message="充值用户导出",
        mode="analyzed",
        covered_roles=["user", "pay", "cash", "bet"],
        missing_roles=[],
        fact_truncated_roles=["pay"],
        deliverable_rows=1499,
        cohort_uid_estimate=3748,
    )
    assert lesson_is_high_confidence(md)
    assert auto_merge_export_lesson_into_skill(pkg, md, run_id="run_a1")
    report = (pkg / "references" / "export-report.md").read_text(encoding="utf-8")
    block = extract_auto_lessons_block(report)
    assert "run_a1" in block
    assert "row_incomplete" in block or "满页截断" in block or "1499" in block
    # Same run again → no duplicate write
    assert not auto_merge_export_lesson_into_skill(pkg, md, run_id="run_a1")
    report2 = (pkg / "references" / "export-report.md").read_text(encoding="utf-8")
    assert report2.count("`run_a1`") == report.count("`run_a1`")


def test_auto_merge_skips_success_only(tmp_path):
    pkg = _skill_pkg(tmp_path)
    md = build_export_skill_lesson(
        run_id="ok1",
        mode="analyzed",
        covered_roles=["user", "pay", "cash", "bet"],
        missing_roles=[],
        fallback=False,
    )
    assert not auto_merge_export_lesson_into_skill(pkg, md, run_id="ok1")


def test_materialize_calls_auto_merge(tmp_path, monkeypatch):
    from app.services import skill_lesson as sl

    pkg = _skill_pkg(tmp_path)

    def _fake_ensure(sandbox_id: str) -> Path:
        root = tmp_path / sandbox_id
        root.mkdir(parents=True, exist_ok=True)
        return root

    monkeypatch.setattr(sl, "ensure_workplace", _fake_ensure)
    md, paths, section = materialize_export_skill_lesson(
        "sbx_auto",
        "run_m1",
        user_message="缺 cash",
        mode="fallback",
        covered_roles=["user", "pay"],
        missing_roles=["cash"],
        fetched_view_pages={"view_result_pay_order_log": 2},
        fallback=True,
        skill_dirs=[pkg],
    )
    assert paths
    assert "fact_starved" in md or "cash" in md
    block = extract_auto_lessons_block(
        (pkg / "references" / "export-report.md").read_text(encoding="utf-8")
    )
    assert "run_m1" in block
    assert "自动沉淀" in section or "auto-lessons" in section
