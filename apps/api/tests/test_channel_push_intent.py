"""Send-to-bound-channel intent must not misroute into export quarantine."""

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.services.react_engine import (
    _extract_deliverable_filenames,
    _find_deliverable_by_name,
    _handle_send_existing_to_channel,
    _infer_provider_from_message,
    _is_send_existing_to_channel_intent,
    _is_send_existing_to_tg_intent,
    _normalize_workplace_files,
)


def test_send_to_tg_with_filename_intent():
    msg = "将 6月运营投入产出分析.xlsx 发送到 tg"
    assert _is_send_existing_to_channel_intent(msg)
    assert _is_send_existing_to_tg_intent(msg)  # alias
    assert _extract_deliverable_filenames(msg) == ["6月运营投入产出分析.xlsx"]
    assert _infer_provider_from_message(msg) == "telegram"


def test_send_selected_excel_loose_to_tg_intent():
    msg = "发送 选中的 excel文件到tg"
    assert _is_send_existing_to_channel_intent(msg)
    assert _infer_provider_from_message(msg) == "telegram"


def test_analysis_in_filename_not_requery():
    msg = "把用户分析报表.xlsx 推送到飞书"
    assert _is_send_existing_to_channel_intent(msg)
    assert _infer_provider_from_message(msg) == "feishu"


def test_requery_export_not_push_intent():
    msg = "重新查询分析导出注册用户报表"
    assert not _is_send_existing_to_channel_intent(msg)


def test_send_to_channel_generic():
    assert _is_send_existing_to_channel_intent("把报表.xlsx 发送到绑定渠道")


def test_normalize_workplace_files(tmp_path, monkeypatch):
    sid = "sbx_sel"
    root = tmp_path / sid / "workplace"
    root.mkdir(parents=True)
    (root / "a.xlsx").write_bytes(b"PK")
    (root / "readme.txt").write_text("x")

    monkeypatch.setattr(
        "app.services.react_engine.download_path",
        lambda s, rel: root / Path(rel).name if s == sid else None,
    )
    monkeypatch.setattr(
        "app.services.react_engine.is_valid_deliverable_file",
        lambda p: Path(p).suffix.lower() == ".xlsx" and Path(p).is_file(),
    )
    assert _normalize_workplace_files(sid, ["a.xlsx", "readme.txt", "../x.xlsx"]) == ["a.xlsx"]


def test_workplace_files_preferred_over_recent(monkeypatch):
    agent = SimpleNamespace(sandbox_id="sbx", id="a1")
    channel = SimpleNamespace(provider="telegram")
    pushed: list[str] = []

    monkeypatch.setattr(
        "app.services.react_engine._normalize_workplace_files",
        lambda sid, paths: list(paths or []),
    )
    monkeypatch.setattr(
        "app.services.react_engine._collect_recent_export_rels",
        lambda *a, **k: ["recent.xlsx"],
    )
    monkeypatch.setattr(
        "app.services.react_engine._resolve_im_push_target",
        lambda *a, **k: (channel, "chat1", None),
    )

    async def _push(db, ch, chat_id, sid, files):
        pushed.extend(files)
        return list(files), []

    monkeypatch.setattr(
        "app.services.react_engine._push_exports_to_channel",
        _push,
    )

    text = asyncio.run(
        _handle_send_existing_to_channel(
            None,
            agent,
            "sess",
            {},
            "发送选中的到tg",
            workplace_files=["picked.xlsx"],
        )
    )
    assert pushed == ["picked.xlsx"]
    assert "picked.xlsx" in text
    assert "recent.xlsx" not in text


def test_selected_without_files_prompts_checkbox(monkeypatch):
    agent = SimpleNamespace(sandbox_id="sbx", id="a1")
    recent_called = {"n": 0}

    def _recent(*a, **k):
        recent_called["n"] += 1
        return ["recent.xlsx"]

    monkeypatch.setattr(
        "app.services.react_engine._normalize_workplace_files",
        lambda *a, **k: [],
    )
    monkeypatch.setattr(
        "app.services.react_engine._collect_recent_export_rels",
        _recent,
    )
    monkeypatch.setattr(
        "app.services.react_engine._resolve_im_push_target",
        lambda *a, **k: (SimpleNamespace(provider="telegram"), "c", None),
    )
    monkeypatch.setattr(
        "app.services.react_engine._push_exports_to_channel",
        AsyncMock(return_value=([], [])),
    )

    text = asyncio.run(
        _handle_send_existing_to_channel(
            None,
            agent,
            "sess",
            {},
            "发送 选中的 excel文件到tg",
            workplace_files=[],
        )
    )
    assert recent_called["n"] == 0
    assert "勾选" in text


def test_find_deliverable_in_stale(tmp_path, monkeypatch):
    sid = "sbx_push_test"
    root = tmp_path / sid
    root.mkdir()
    monkeypatch.setattr(
        "app.services.react_engine.ensure_workplace",
        lambda s: root,
    )
    monkeypatch.setattr(
        "app.services.react_engine.is_valid_deliverable_file",
        lambda p: str(p).endswith(".xlsx"),
    )
    stale = root / "task" / "_stale_123"
    stale.mkdir(parents=True)
    (stale / "6月运营投入产出分析.xlsx").write_bytes(b"PK\x03\x04fake")

    rel = _find_deliverable_by_name(sid, "6月运营投入产出分析.xlsx")
    assert rel == "task/_stale_123/6月运营投入产出分析.xlsx"
