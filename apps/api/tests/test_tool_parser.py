"""Unit tests for tool_parser RUN_SKILL wiring and blank-reply handling."""

from __future__ import annotations

from app.services.tool_parser import extract_tool_steps


def test_run_skill_parses_to_skill_run_script():
    steps = extract_tool_steps("RUN_SKILL: report")
    assert steps and steps[0].action == "skill_run_script"
    assert steps[0].reply == "RUN_SKILL: report"


def test_run_skill_stops_multiline_write_payload():
    steps = extract_tool_steps("WRITE: a.json\n{}\nRUN_SKILL: report")
    actions = [s.action for s in steps]
    assert "file_write" in actions
    assert "skill_run_script" in actions


def test_blank_reply_yields_no_steps():
    assert extract_tool_steps("   ") == []
    assert extract_tool_steps("") == []


def test_final_recognized_with_markdown_prefixes():
    for text in (
        "**FINAL:** 完成",
        "- FINAL: 完成",
        "1. FINAL: 完成",
        "## FINAL: 完成",
        "> FINAL：完成",
    ):
        steps = extract_tool_steps(text)
        assert any(getattr(s, "is_final", False) for s in steps), text
        final = next(s for s in steps if getattr(s, "is_final", False))
        assert "完成" in final.reply, text


def test_mcp_multiline_json_args_not_truncated():
    steps = extract_tool_steps('MCP: list_notes\n{\n  "cursor": "abc",\n  "limit": 50\n}')
    assert steps and steps[0].action == "mcp_tool_call"
    assert '"cursor"' in steps[0].reply and '"limit"' in steps[0].reply


def test_think_block_stripped_yields_no_steps():
    assert extract_tool_steps("<think>推理中</think>") == []
    assert extract_tool_steps("<think>未闭合") == []
    assert extract_tool_steps("<think") == []


def test_write_body_preserves_nested_index_tokens():
    steps = extract_tool_steps("WRITE: a.py\nx = [t[0] for t in tgs]\n")
    assert steps and steps[0].action == "file_write"
    assert "[t[0]" in steps[0].reply
    assert "0] for t in tgs]" in steps[0].reply
