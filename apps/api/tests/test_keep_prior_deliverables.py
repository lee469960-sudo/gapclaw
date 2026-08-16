"""Preserve prior deliverables across concurrent export runs."""

import time
from pathlib import Path

from openpyxl import Workbook

from app.services.react_engine import (
    _find_export_root_deliverables,
    _is_run_owned_deliverable,
)
from app.services.workplace import (
    ensure_workplace,
    quarantine_root_deliverables,
)


def _write_xlsx(path: Path) -> None:
    wb = Workbook()
    ws = wb.active
    ws.append(["用户ID", "总充值金额"])
    ws.append(["u1", 10])
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)


def test_quarantine_helper_still_moves_but_engine_should_not_call(tmp_path, monkeypatch):
    """Helper remains for admin use; documents concurrent hazard."""
    from app.services import workplace as wp

    sid = "sbx-keep-files"
    monkeypatch.setattr(wp, "_wp_root", lambda _s: tmp_path / _s / "workplace")
    root = ensure_workplace(sid)
    prev = root / "prev.xlsx"
    _write_xlsx(prev)
    moved = quarantine_root_deliverables(sid, "1799000000001")
    assert moved
    assert not prev.exists()
    assert any("_stale_" in m for m in moved)


def test_old_and_sibling_run_files_not_claimed(tmp_path, monkeypatch):
    from app.services import workplace as wp
    from app.models import Sandbox

    sid = "sbx-owned"
    monkeypatch.setattr(wp, "_wp_root", lambda _s: tmp_path / _s / "workplace")
    root = ensure_workplace(sid)
    old = root / "prev.xlsx"
    sibling = root / "用户分析_1798000000001.xlsx"
    mine = root / "用户分析_1799000000002.xlsx"
    untagged = root / "export.xlsx"
    _write_xlsx(old)
    _write_xlsx(sibling)
    time.sleep(0.05)
    started = time.time()
    time.sleep(0.05)
    _write_xlsx(mine)
    _write_xlsx(untagged)

    class _S:
        id = sid

    sandbox = _S()  # type: ignore[assignment]

    assert not _is_run_owned_deliverable(
        "prev.xlsx",
        sandbox=sandbox,  # type: ignore[arg-type]
        run_id="1799000000002",
        started_at=started,
        saved_paths=[],
    )
    assert not _is_run_owned_deliverable(
        "用户分析_1798000000001.xlsx",
        sandbox=sandbox,  # type: ignore[arg-type]
        run_id="1799000000002",
        started_at=started,
        saved_paths=[],
    )
    assert _is_run_owned_deliverable(
        "用户分析_1799000000002.xlsx",
        sandbox=sandbox,  # type: ignore[arg-type]
        run_id="1799000000002",
        started_at=started,
        saved_paths=[],
    )
    assert _is_run_owned_deliverable(
        "export.xlsx",
        sandbox=sandbox,  # type: ignore[arg-type]
        run_id="1799000000002",
        started_at=started,
        saved_paths=[],
    )
    # Explicit saved_paths always owned
    assert _is_run_owned_deliverable(
        "prev.xlsx",
        sandbox=sandbox,  # type: ignore[arg-type]
        run_id="1799000000002",
        started_at=started,
        saved_paths=["prev.xlsx"],
    )

    found = _find_export_root_deliverables(
        sandbox,  # type: ignore[arg-type]
        "",
        [],
        run_id="1799000000002",
        started_at=started,
    )
    assert "prev.xlsx" not in found
    assert "用户分析_1798000000001.xlsx" not in found
    assert "用户分析_1799000000002.xlsx" in found
    assert "export.xlsx" in found
    # Files still on disk (no quarantine)
    assert old.is_file()
    assert sibling.is_file()
    assert mine.is_file()
