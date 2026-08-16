"""Telegram private/group inbound and reply transport behavior."""

import asyncio
import json

from app.services.channels.base import InboundMessage
from app.services.channels.runtime import _address_group_sender
from app.services.channels.telegram import TelegramAdapter
from app.services.channels.telegram_markdown import telegram_html_chunks
from app.services.channels.telegram_rich import (
    prepare_telegram_rich_markdown,
    rewrite_wide_markdown_tables,
)


def _parse(adapter: TelegramAdapter, update: dict):
    return asyncio.run(
        adapter.handle_webhook(
            method="POST",
            headers={},
            query={},
            body=json.dumps(update, ensure_ascii=False).encode("utf-8"),
        )
    )


def test_private_message_keeps_real_text_and_sender():
    adapter = TelegramAdapter("tg1", {"bot_token": "token"})
    result = _parse(
        adapter,
        {
            "update_id": 10,
            "message": {
                "message_id": 20,
                "text": "你好啊",
                "chat": {"id": 30, "type": "private"},
                "from": {
                    "id": 40,
                    "username": "starter",
                    "first_name": "Start",
                    "last_name": "User",
                },
            },
        },
    )
    assert result.skip_agent is False
    assert result.inbound is not None
    assert result.inbound.text == "你好啊"
    assert result.inbound.sender_username == "starter"
    assert result.inbound.sender_display_name == "Start User"


def test_group_ignores_message_without_bot_mention():
    adapter = TelegramAdapter(
        "tg1", {"bot_token": "token", "bot_username": "data_bot"},
    )
    result = _parse(
        adapter,
        {
            "update_id": 11,
            "message": {
                "message_id": 21,
                "text": "大家好",
                "chat": {"id": -100, "type": "supergroup"},
                "from": {"id": 41, "username": "starter"},
            },
        },
    )
    assert result.skip_agent is True
    assert result.inbound is None


def test_group_mention_starts_task_and_strips_only_bot_mention():
    adapter = TelegramAdapter(
        "tg1", {"bot_token": "token", "bot_username": "data_bot"},
    )
    text = "@data_bot 请总结本次会话"
    result = _parse(
        adapter,
        {
            "update_id": 12,
            "message": {
                "message_id": 22,
                "text": text,
                "entities": [{"type": "mention", "offset": 0, "length": 9}],
                "chat": {"id": -100, "type": "supergroup"},
                "from": {"id": 42, "username": "starter"},
            },
        },
    )
    assert result.skip_agent is False
    assert result.inbound is not None
    assert result.inbound.text == "请总结本次会话"
    assert result.inbound.chat_type == "group"


def test_group_resolves_bot_username_from_telegram(monkeypatch):
    calls: list[str] = []

    async def fake_bot_api(token, method, payload=None):
        calls.append(method)
        return {"ok": True, "result": {"username": "resolved_bot"}}

    monkeypatch.setattr("app.services.channels.telegram._bot_api", fake_bot_api)
    adapter = TelegramAdapter("tg1", {"bot_token": "unique-token"})
    result = _parse(
        adapter,
        {
            "update_id": 14,
            "message": {
                "message_id": 24,
                "text": "@resolved_bot 查询今天数据",
                "entities": [{"type": "mention", "offset": 0, "length": 13}],
                "chat": {"id": -101, "type": "group"},
                "from": {"id": 44, "username": "starter"},
            },
        },
    )
    assert calls == ["getMe"]
    assert result.inbound is not None
    assert result.inbound.text == "查询今天数据"


def test_group_mention_offsets_handle_emoji_utf16_units():
    adapter = TelegramAdapter(
        "tg1", {"bot_token": "token", "bot_username": "data_bot"},
    )
    text = "📊 @data_bot 导出报表"
    result = _parse(
        adapter,
        {
            "update_id": 13,
            "message": {
                "message_id": 23,
                "text": text,
                "entities": [{"type": "mention", "offset": 3, "length": 9}],
                "chat": {"id": -100, "type": "group"},
                "from": {"id": 43, "username": "starter"},
            },
        },
    )
    assert result.inbound is not None
    assert result.inbound.text == "📊  导出报表"


def test_group_completion_mentions_initiator_without_rewriting_reply():
    inbound = InboundMessage(
        msg_id="1",
        chat_id="-100",
        user_id="42",
        sender_username="starter",
        text="任务",
        chat_type="group",
    )
    reply = "以下是 Agent 的真实完整回复。"
    assert _address_group_sender("telegram", inbound, reply) == f"@starter\n{reply}"


def test_send_text_uses_send_rich_message_with_markdown(monkeypatch):
    calls: list[tuple[str, dict]] = []

    async def fake_bot_api(token, method, payload=None):
        calls.append((method, payload or {}))
        return {"ok": True, "result": {}}

    monkeypatch.setattr("app.services.channels.telegram._bot_api", fake_bot_api)
    adapter = TelegramAdapter("tg1", {"bot_token": "token"})
    asyncio.run(
        adapter.send_text(
            "-100",
            "真实回复",
            reply_to={"message": {"message_id": 88}},
        )
    )
    assert len(calls) == 1
    method, payload = calls[0]
    assert method == "sendRichMessage"
    assert payload["chat_id"] == "-100"
    assert payload["rich_message"] == {"markdown": "真实回复"}
    assert payload["reply_parameters"]["message_id"] == 88
    assert "parse_mode" not in payload


def test_send_text_keeps_markdown_table_for_rich_message(monkeypatch):
    message_calls: list[tuple[str, dict]] = []

    async def fake_bot_api(token, method, payload=None):
        message_calls.append((method, payload or {}))
        return {"ok": True, "result": {}}

    monkeypatch.setattr("app.services.channels.telegram._bot_api", fake_bot_api)
    adapter = TelegramAdapter("tg1", {"bot_token": "token"})
    source = "## 查询结果\n\n| 日期 | 注册人数 |\n|---|---:|\n| 2026-08-14 | 100 |"
    asyncio.run(
        adapter.send_text(
            "-100",
            source,
            reply_to={"message": {"message_id": 99}},
        )
    )

    assert len(message_calls) == 1
    method, payload = message_calls[0]
    assert method == "sendRichMessage"
    md = payload["rich_message"]["markdown"]
    assert "| 日期 | 注册人数 |" in md
    assert "| 2026-08-14 | 100 |" in md
    assert "<pre>" not in md
    assert payload["reply_parameters"]["message_id"] == 99


def test_send_text_falls_back_to_send_message_when_rich_fails(monkeypatch):
    calls: list[tuple[str, dict]] = []

    async def fake_bot_api(token, method, payload=None):
        calls.append((method, payload or {}))
        if method == "sendRichMessage":
            return {"ok": False, "description": "method not found"}
        return {"ok": True, "result": {}}

    monkeypatch.setattr("app.services.channels.telegram._bot_api", fake_bot_api)
    adapter = TelegramAdapter("tg1", {"bot_token": "token"})
    asyncio.run(
        adapter.send_text(
            "-100",
            "### 查询结果\n\n| 指标 | 数值 |\n|---|---:|\n| 注册 | 100 |",
            reply_to={"message": {"message_id": 77}},
        )
    )
    assert calls[0][0] == "sendRichMessage"
    assert calls[1][0] == "sendMessage"
    html = calls[1][1]["text"]
    assert calls[1][1]["parse_mode"] == "HTML"
    assert "<pre>" not in html
    assert "<b>查询结果</b>" in html
    assert "注册" in html


def test_markdown_fallback_html_keeps_structure_without_table_pre():
    source = (
        "### 查询结果\n\n"
        "| 指标 | 数值 |\n"
        "|---|---:|\n"
        "| 注册人数（人） | 26,981 |\n"
        "| 总充值金额（元） | 2,726,134.61 |\n\n"
        "**口径**：美国东部时间\n\n"
        "- `view_result_user_info`\n"
        "- `view_result_pay_order_log`"
    )
    rendered = "\n".join(telegram_html_chunks(source))
    assert "###" not in rendered
    assert "<b>查询结果</b>" in rendered
    assert "<pre>" not in rendered
    assert "注册人数（人）" in rendered
    assert "26,981" in rendered
    assert "<b>口径</b>：美国东部时间" in rendered
    assert "• <code>view_result_user_info</code>" in rendered


def test_markdown_preview_tolerates_list_without_blank_line():
    source = "**数据来源**：\n- `view_a`\n- `view_b`"
    rendered = "\n".join(telegram_html_chunks(source))
    assert rendered == "<b>数据来源</b>：\n• <code>view_a</code>\n• <code>view_b</code>"


def test_time_series_table_keeps_every_day_in_rich_markdown():
    rows = [
        "| 日期 | 注册 | 充值 | 提现 | SC下注 | ROI |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
        *[
            f"| 2026-08-{day:02d} | {day} | {day * 10} | {day * 8} | {day * 20} | 0.8 |"
            for day in range(1, 15)
        ],
    ]
    md = prepare_telegram_rich_markdown("### 查询结果\n\n" + "\n".join(rows))
    assert all(f"2026-08-{day:02d}" in md for day in range(1, 15))
    assert "| 日期 | 注册 | 充值 | 提现 | SC下注 | ROI |" in md
    assert "<pre>" not in md


def test_withdraw_metrics_table_stays_markdown_for_rich():
    source = (
        "### 三、提现指标\n\n"
        "| 日期 | 提现用户 | 提现金额($) | 创建提现 | 完成提现 | 充提差($) |\n"
        "|---|---:|---:|---:|---:|---:|\n"
        "| 8/05 | 101 | **84,117** | 237 | 224 | 8,381 |\n"
        "| 8/06 | 99 | **47,560** | 176 | 161 | 24,427 |\n"
    )
    md = prepare_telegram_rich_markdown(source)
    assert "### 三、提现指标" in md
    assert "| 日期 | 提现用户 | 提现金额($) |" in md
    assert "**84,117**" in md
    assert "47,560" in md
    assert "<pre>" not in md


def test_wide_ops_table_splits_into_standard_markdown_tables():
    header = (
        "| 日期 | 新增注册 | 累计注册 | 登录用户 | 充值用户 | 充值金额 | "
        "提现用户 | 提现金额 | 下注用户 | 下注笔数 | ROI |"
    )
    sep = "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"
    rows = [
        f"| 8/{day:02d} | {day} | {20000 + day} | {3000 + day} | {200 + day} | "
        f"{50000 + day} | {80 + day} | {40000 + day} | {1800 + day} | {200000 + day} | 0.9 |"
        for day in range(1, 8)
    ]
    source = "## 最近7天常规运营统计\n\n" + "\n".join([header, sep, *rows])
    md = rewrite_wide_markdown_tables(source, max_cols=6)
    assert "## 最近7天常规运营统计" in md
    assert md.count("| 日期 |") >= 2
    assert all(f"| 8/{day:02d} |" in md for day in range(1, 8))
    # Each sub-table stays within 6 columns (key + ≤5 metrics)
    for line in md.splitlines():
        if line.startswith("|") and "---" not in line.replace(" ", ""):
            cols = [c for c in line.strip("|").split("|")]
            if "日期" in line or line.strip().startswith("| 8/"):
                assert len(cols) <= 6
    assert "<pre>" not in md
    assert "```" not in md


def test_recent_7day_ops_send_rich_message_payload(monkeypatch):
    """End-to-end style: 最近7天常规运营统计 goes out as Rich Markdown."""
    calls: list[tuple[str, dict]] = []

    async def fake_bot_api(token, method, payload=None):
        calls.append((method, payload or {}))
        return {"ok": True, "result": {}}

    monkeypatch.setattr("app.services.channels.telegram._bot_api", fake_bot_api)
    adapter = TelegramAdapter("tg1", {"bot_token": "token"})
    source = (
        "## 最近7天常规运营统计\n\n"
        "> 口径：美国东部时间\n\n"
        "| 日期 | 新增注册 | 累计注册 | 登录用户 | 充值用户 | 充值金额 | "
        "提现用户 | 提现金额 | 下注用户 | SC下注 | ROI |\n"
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |\n"
        "| 8/08 | 120 | 20100 | 3100 | 210 | 51000 | 90 | 41000 | 1900 | 210000 | 0.85 |\n"
        "| 8/09 | 130 | 20230 | 3200 | 220 | 52000 | 95 | 42000 | 1950 | 220000 | 0.86 |\n\n"
        "**说明**：常规运营汇总\n\n"
        "- `view_result_user_info`\n"
        "- `view_result_pay_order_log`"
    )
    asyncio.run(adapter.send_text("-100", source))
    assert calls and calls[0][0] == "sendRichMessage"
    md = calls[0][1]["rich_message"]["markdown"]
    assert "## 最近7天常规运营统计" in md
    assert "**说明**" in md
    assert "> 口径" in md or "口径" in md
    assert "| 日期 |" in md
    assert "8/08" in md and "8/09" in md
    assert "<pre>" not in md
    # Wide table split into business groups but still Markdown tables
    assert md.count("| 日期 |") >= 2


def test_long_code_block_is_split_without_broken_html():
    chunks = telegram_html_chunks("```sql\n" + ("SELECT 1;\n" * 800) + "```", limit=500)
    assert len(chunks) > 1
    assert all(len(chunk) <= 500 for chunk in chunks)
    assert all(chunk.startswith("<pre>") and chunk.endswith("</pre>") for chunk in chunks)
