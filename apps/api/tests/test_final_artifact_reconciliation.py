from pathlib import Path

from openpyxl import Workbook

from app.services.react_engine import (
    _normalize_final_paths,
    _reconcile_final_artifacts,
)
from app.services.workplace import download_path


class _Sandbox:
    id = "sbx-artifacts"


def _xlsx(path: Path) -> None:
    wb = Workbook()
    ws = wb.active
    ws.append(["用户ID"])
    ws.append(["1"])
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    wb.close()


def test_missing_export_claim_is_not_published_as_complete(tmp_path, monkeypatch):
    from app.services import workplace

    root = tmp_path / "workplace"
    root.mkdir()
    monkeypatch.setattr(workplace, "_wp_root", lambda _sid: root)

    final, paths = _reconcile_final_artifacts(
        "导出已完成。\n\n- `用户列表_123.xlsx`",
        ["用户列表_123.xlsx"],
        _Sandbox(),
        export_like=True,
    )

    assert paths == []
    assert "不能视为导出完成" in final
    assert "工作目录中未找到" in final
    assert "- `用户列表_123.xlsx`" not in final


def test_verified_root_export_and_task_sql_remain_publishable(tmp_path, monkeypatch):
    from app.services import workplace

    root = tmp_path / "workplace"
    _xlsx(root / "用户列表_123.xlsx")
    sql = root / "task" / "123" / "final_sql.sql"
    sql.parent.mkdir(parents=True)
    sql.write_text("SELECT 1", encoding="utf-8")
    monkeypatch.setattr(workplace, "_wp_root", lambda _sid: root)

    final, paths = _reconcile_final_artifacts(
        "已完成。\n- `用户列表_123.xlsx`\n- `task/123/final_sql.sql`",
        ["用户列表_123.xlsx", "task/123/final_sql.sql"],
        _Sandbox(),
        export_like=True,
    )

    assert paths == ["用户列表_123.xlsx", "task/123/final_sql.sql"]
    assert "未生成可下载" not in final
    assert _normalize_final_paths(final, paths).endswith("`task/123/final_sql.sql`")


def test_workplace_prefixed_export_claim_is_canonicalized_to_downloadable_root_path(tmp_path, monkeypatch):
    from app.services import workplace

    root = tmp_path / "workplace"
    _xlsx(root / "SC下注用户_2026-08-13_游戏10025.xlsx")
    monkeypatch.setattr(workplace, "_wp_root", lambda _sid: root)

    final, paths = _reconcile_final_artifacts(
        "已交付：`/workplace/SC下注用户_2026-08-13_游戏10025.xlsx`",
        ["workplace/SC下注用户_2026-08-13_游戏10025.xlsx"],
        _Sandbox(),
        export_like=True,
    )

    assert paths == ["SC下注用户_2026-08-13_游戏10025.xlsx"]
    assert "/workplace/" not in final
    assert "`SC下注用户_2026-08-13_游戏10025.xlsx`" in final
    assert "不能视为导出完成" not in final


def test_download_path_accepts_workplace_prefixed_rel(tmp_path, monkeypatch):
    from app.services import workplace

    root = tmp_path / "workplace"
    _xlsx(root / "SC下注用户_2026-08-13_游戏10025.xlsx")
    monkeypatch.setattr(workplace, "_wp_root", lambda _sid: root)

    direct = download_path(_Sandbox.id, "SC下注用户_2026-08-13_游戏10025.xlsx")
    prefixed = download_path(_Sandbox.id, "workplace/SC下注用户_2026-08-13_游戏10025.xlsx")

    assert direct is not None
    assert prefixed is not None
    assert direct.name == prefixed.name == "SC下注用户_2026-08-13_游戏10025.xlsx"
