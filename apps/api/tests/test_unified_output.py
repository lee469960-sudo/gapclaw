import json

from app.services.agent_runtime.unified_output import (
    OUTPUT_KEYS,
    build_unified_output,
    visible_reply,
)


def _assert_envelope(envelope: dict) -> None:
    assert tuple(envelope.keys()) == OUTPUT_KEYS
    assert envelope["version"] == "1.0"
    assert envelope["actions"] == []
    assert envelope["type"] != "action"


def test_complete_with_structure_is_ok_data():
    envelope = build_unified_output(
        "complete",
        "查询完成，共 3 条。",
        {"count": 3, "fields": ["id", "name"], "saved_paths": ["out/report.csv"], "status": "done"},
    )
    _assert_envelope(envelope)
    assert envelope["status"] == "ok"
    assert envelope["type"] == "data"
    assert envelope["message"] == "查询完成，共 3 条。"
    assert envelope["data"]["count"] == 3
    assert envelope["data"]["saved_paths"] == ["out/report.csv"]


def test_budget_is_partial_answer_and_states_the_gap():
    envelope = build_unified_output(
        "budget",
        "已完成用户列表查询。还缺渠道名称映射。",
        {"saved_paths": ["out/users.csv"], "pending": ["补充渠道名称"]},
    )
    _assert_envelope(envelope)
    assert envelope["status"] == "partial"
    assert envelope["type"] == "answer"
    assert "已完成" in envelope["message"]
    assert "渠道名称" in envelope["message"]


def test_cancel_is_partial_and_explains_the_stop():
    envelope = build_unified_output(
        "cancel",
        "[已停止]",
        {"progress": ["已读取订单表"], "pending": ["尚未汇总金额"]},
    )
    _assert_envelope(envelope)
    assert envelope["status"] == "partial"
    assert envelope["type"] == "answer"
    assert "停止" in envelope["message"]
    assert "已读取订单表" in envelope["message"]
    assert "尚未汇总金额" in envelope["message"]
    assert "[已停止]" not in envelope["message"]


def test_need_input_is_clarify():
    envelope = build_unified_output("need_input", "未完成的高风险动作需要你的确认/授权后才能执行。")
    _assert_envelope(envelope)
    assert envelope["status"] == "need_input"
    assert envelope["type"] == "clarify"


def test_error_status():
    envelope = build_unified_output("error", "LLM 服务连续调用失败，任务已暂停，请稍后继续。")
    _assert_envelope(envelope)
    assert envelope["status"] == "error"
    assert envelope["type"] == "error"
    assert "暂停" in envelope["message"]


def test_no_tool_complete_is_ok_answer_with_empty_data():
    envelope = build_unified_output("complete", "你好，有什么可以帮你？")
    _assert_envelope(envelope)
    assert envelope["status"] == "ok"
    assert envelope["type"] == "answer"
    assert envelope["data"] == {}
    assert envelope["actions"] == []


def test_data_drops_long_text_and_code_and_stays_empty_without_structure():
    blob = "SELECT * FROM orders WHERE " + ("x" * 500)
    envelope = build_unified_output(
        "complete",
        "没有可解析的结构。",
        {
            "stdout": blob,
            "snippet": "def unfinished_alpha():\n    return 1",
            "note": "这是一段超过八十个字符的说明，不应该进入结构化字段，因为它是原文而不是字段名或路径。",
        },
    )
    _assert_envelope(envelope)
    assert envelope["data"] == {}
    assert envelope["type"] == "answer"
    assert envelope["actions"] == []

    empty = build_unified_output("complete", "普通回答", None)
    assert empty["data"] == {}
    assert empty["actions"] == []


def test_partial_message_drops_unfinished_code_and_tool_text():
    raw = "\n".join([
        "已查询到 3 条记录，汇总还没做完。",
        "```python",
        "def unfinished_alpha():",
        "    return open('secret').read()",
        "```",
        "MCP: okx-trader get_positions",
        "工具原文 " + ("A" * 500),
    ])
    envelope = build_unified_output("budget", raw, {"count": 3})
    assert "unfinished_alpha" not in envelope["message"]
    assert "okx-trader" not in envelope["message"]
    assert "```" not in envelope["message"]
    assert "已查询到 3 条记录" in envelope["message"]
    assert envelope["data"] == {"count": 3}
    assert json.dumps(envelope["data"], ensure_ascii=False).find("unfinished_alpha") < 0


def test_partial_message_drops_cached_mcp_artifacts():
    raw = "\n".join([
        "任务未完成，已自动结束（连续 3 次重复请求已缓存的 MCP 结果，自动停止避免资源浪费）。",
        "已产生的中间产物：",
        "- 已缓存 MCP 结果 task/1790584533496/mcp_result_0.json",
        "- 已缓存 MCP 结果 task/1790584533496/mcp_result_1.json",
        "- 已缓存 MCP 结果 task/1790584533496/mcp_result_2.json",
        "- 已缓存 MCP 结果 task/1790584533496/mcp_result_3.json",
        "- 已写入 report.xlsx",
    ])
    envelope = build_unified_output("incomplete", raw, {
        "progress": ["已缓存 MCP 结果 task/1790584533496/mcp_result_0.json", "已写入 report.xlsx"],
        "saved_paths": ["task/1790584533496/mcp_result_0.json", "report.xlsx"],
    })
    assert "mcp_result_" not in envelope["message"]
    assert "已缓存 MCP 结果" not in envelope["message"]
    assert "已产生的中间产物" not in envelope["message"]
    assert "已自动结束" in envelope["message"]
    assert "report.xlsx" in envelope["message"]
    assert "mcp_result_" not in json.dumps(envelope["data"], ensure_ascii=False)
    assert envelope["data"]["saved_paths"] == ["report.xlsx"]


def test_group_assistant_record_keeps_the_agent_envelope():
    from app.services.agent_runtime.unified_output import group_assistant_record

    envelope = build_unified_output("complete", "查询完成，共 3 条。", {"count": 3})
    content, meta = group_assistant_record("ignored raw", envelope)
    assert content == "查询完成，共 3 条。"
    assert meta["output"]["status"] == "ok"
    assert meta["output"]["message"] == content


def test_group_assistant_record_builds_an_envelope_when_the_agent_row_has_none():
    from app.services.agent_runtime.unified_output import group_assistant_record

    content, meta = group_assistant_record("群里的普通回答", None)
    assert content == "群里的普通回答"
    assert set(meta["output"]) == {"version", "status", "type", "message", "data", "actions"}
    assert meta["output"]["actions"] == []


def test_visible_reply_unwraps_envelope_json():
    envelope = build_unified_output("complete", "查询完成，共 3 条。", {"count": 3})
    sent = visible_reply(json.dumps(envelope, ensure_ascii=False))
    assert sent == "查询完成，共 3 条。"
    assert "version" not in sent
    assert "actions" not in sent
