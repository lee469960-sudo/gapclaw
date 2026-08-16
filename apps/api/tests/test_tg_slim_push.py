"""TG send: one latest/versioned file; IM completion slim reply."""

import asyncio
from pathlib import Path
from types import SimpleNamespace

from app.services.react_engine import (
    _find_deliverable_by_hint,
    _handle_send_existing_to_channel,
    _resolve_one_send_file,
    format_im_completion_reply,
)


def test_find_v6_not_v5(tmp_path, monkeypatch):
    sid = "sbx_v"
    root = tmp_path / sid
    root.mkdir()
    (root / "user_export_2026-07-21_2026-07-31_v5.xlsx").write_bytes(b"PK5")
    (root / "user_export_2026-07-21_2026-07-31_v6.xlsx").write_bytes(b"PK6")
    (root / "export_1785591112089.xlsx").write_bytes(b"PKe")

    monkeypatch.setattr(
        "app.services.react_engine.ensure_workplace",
        lambda s: root if s == sid else Path("/nope"),
    )
    monkeypatch.setattr(
        "app.services.react_engine.download_path",
        lambda s, rel: (root / Path(rel).name) if s == sid else None,
    )
    monkeypatch.setattr(
        "app.services.react_engine.is_valid_deliverable_file",
        lambda p: Path(p).suffix.lower() == ".xlsx" and Path(p).is_file(),
    )
    monkeypatch.setattr(
        "app.services.react_engine.list_recent_data_files",
        lambda s, limit=40: [
            "export_1785591112089.xlsx",
            "user_export_2026-07-21_2026-07-31_v6.xlsx",
            "user_export_2026-07-21_2026-07-31_v5.xlsx",
        ][:limit],
    )

    rel = _find_deliverable_by_hint(sid, "将v6的excel文件发送到tg")
    assert rel is not None
    assert "v6" in Path(rel).name
    assert "v5" not in Path(rel).name


def test_resolve_one_defaults_to_single_recent(monkeypatch):
    agent = SimpleNamespace(sandbox_id="sbx", id="a1")
    called = {}

    def _recent(*a, **k):
        called["limit"] = k.get("limit")
        return ["a.xlsx", "b.xlsx", "c.xlsx"]

    monkeypatch.setattr(
        "app.services.react_engine._normalize_workplace_files",
        lambda *a, **k: [],
    )
    monkeypatch.setattr(
        "app.services.react_engine._extract_deliverable_filenames",
        lambda m: [],
    )
    monkeypatch.setattr(
        "app.services.react_engine._find_deliverable_by_hint",
        lambda *a, **k: None,
    )
    monkeypatch.setattr(
        "app.services.react_engine._collect_recent_export_rels",
        _recent,
    )
    monkeypatch.setattr(
        "app.services.react_engine._pick_newest_rel",
        lambda sid, rels: rels[0] if rels else None,
    )

    one, err = _resolve_one_send_file(None, agent, "sess", "发送到tg")
    assert err == ""
    assert one == "a.xlsx"
    assert called.get("limit") == 8  # collect pool then pick one


def test_send_v6_pushes_only_one(monkeypatch):
    agent = SimpleNamespace(sandbox_id="sbx", id="a1")
    channel = SimpleNamespace(provider="telegram")
    pushed: list[str] = []

    monkeypatch.setattr(
        "app.services.react_engine._resolve_one_send_file",
        lambda *a, **k: ("user_export_v6.xlsx", ""),
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
            None, agent, "sess", {}, "将v6的excel文件发送到tg",
        )
    )
    assert pushed == ["user_export_v6.xlsx"]
    assert "已推送到" in text
    assert "v5" not in text


def test_im_completion_success_one_file():
    reply = "### 报表统计\n| a | b |\n"
    text, paths = format_im_completion_reply(
        reply,
        saved_paths=["task/x.json", "user_export_v6.xlsx", "old.xlsx"],
        sandbox_id="",
    )
    assert text == reply.strip()
    assert len(paths) == 1
    assert paths[0] in ("user_export_v6.xlsx", "old.xlsx")


def test_im_completion_returns_real_agent_gap_content():
    reply = (
        "一些前言\n\n### 完整性/缺口说明\n"
        "- 拉取仍缺 role：cash\n"
        "- 缺口类型：fact_starved\n\n"
        "### 建议写入 Skill\n- x\n"
    )
    text, paths = format_im_completion_reply(
        reply,
        saved_paths=["export_raw.xlsx"],
        sandbox_id="",
    )
    assert text == reply.strip()
    assert paths == ["export_raw.xlsx"]
    assert "建议写入 Skill" in text


def test_im_completion_passthrough_push_ack():
    text, paths = format_im_completion_reply(
        "已推送到 `telegram`：`a.xlsx`",
        saved_paths=["a.xlsx"],
    )
    assert text.startswith("已推送到")
    assert paths == ["a.xlsx"]
