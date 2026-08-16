from app.services import export_build_report as report
from app.services.workplace import count_data_rows, read_deliverable_headers


def test_write_query_result_deliverable_preserves_contract_order(tmp_path, monkeypatch):
    monkeypatch.setattr(report, "ensure_workplace", lambda _sid: tmp_path)
    rel = report.write_query_result_deliverable(
        "sandbox",
        [{"第二列": 2, "第一列": 1}],
        column_headers=["第一列", "第二列"],
        column_plan=[
            {"header": "第一列", "数据来源": "`live_alpha`", "统计方法": "a"},
            {"header": "第二列", "数据来源": "`live_beta`", "统计方法": "b"},
        ],
        preferred_name="result.xlsx",
        title="contract export",
    )
    assert rel == "result.xlsx"
    path = tmp_path / rel
    assert read_deliverable_headers(path) == ["第一列", "第二列"]
    assert count_data_rows(path) == 1


def test_export_filename_uses_task_title_and_run_id():
    name = report.export_filename_for_title(
        "2026年7月充值用户分析 / 全量导出",
        run_id="1786640087786",
    )
    assert name == "2026年7月充值用户分析_全量导出_1786640087786.xlsx"
