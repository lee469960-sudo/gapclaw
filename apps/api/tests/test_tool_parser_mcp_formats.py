"""MCP parser compatibility for plain and MiniMax tool-call formats."""

from app.services.tool_parser import extract_tool_steps


def test_plain_mcp_is_case_insensitive_at_line_start():
    steps = extract_tool_steps('mcp: list_ads_views {}')

    assert len(steps) == 1
    assert steps[0].action == "mcp_tool_call"
    assert steps[0].reply == "MCP: list_ads_views {}"


def test_minimax_xml_mcp_action_is_normalized():
    reply = (
        '<tool_call><action tool="mcp">'
        '<parameter name="name">describe_ads_view</parameter>'
        '<parameter name="arguments">{"view_name":"view_result_user_info"}</parameter>'
        '</action></tool_call>'
    )

    steps = extract_tool_steps(reply)

    assert len(steps) == 1
    assert steps[0].action == "mcp_tool_call"
    assert steps[0].reply.startswith("MCP: describe_ads_view ")
    assert '"view_name":"view_result_user_info"' in steps[0].reply


def test_minimax_shell_wrapped_mcp_is_not_executed_as_shell():
    reply = (
        '<tool_call><action tool="shell">'
        '<parameter name="cmd">mcp: list_ads_views {}</parameter>'
        '</action></tool_call>'
    )

    steps = extract_tool_steps(reply)

    assert len(steps) == 1
    assert steps[0].action == "mcp_tool_call"
    assert steps[0].reply == "MCP: list_ads_views {}"


def test_protocol_example_in_prose_is_not_a_tool_call():
    steps = extract_tool_steps("请使用格式 MCP: list_ads_views {} 来查询。")

    assert steps == []


def test_shell_inside_think_block_is_never_executed():
    reply = "<think>\nSHELL: Actually I think the prior turn is complete\n</think>\nFINAL: 完成"

    steps = extract_tool_steps(reply)

    assert [(s.action, s.reply) for s in steps] == [("done", "FINAL: 完成")]


def test_minimax_shell_rejects_reasoning_or_xml_payload():
    reply = (
        '<tool_call><action tool="shell">'
        '<parameter name="cmd">&lt;think&gt;Let me inspect this&lt;/think&gt;</parameter>'
        '</action></tool_call>'
    )

    assert extract_tool_steps(reply) == []
