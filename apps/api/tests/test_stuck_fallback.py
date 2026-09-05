"""Unit tests for the soft stuck-loop nudge and honest no-FINAL fallback.

Covers the pure helpers added to runtime.py: _is_duplicate_reply and
_forced_stop_reply. No LLM/MCP involved.
"""

from __future__ import annotations

from app.services.agent_runtime.loop_state import AgentLoopState
from app.services.agent_runtime.runtime import (
    _budget_near_hint,
    _distill_hint,
    _forced_stop_reply,
    _is_duplicate_reply,
    _is_mcp_connect_failure,
    _looks_like_bare_python,
    _looks_like_raw_code,
    _rescue_leaked_code,
    _tool_fail_hint,
    _tool_result_failed,
)


def test_is_duplicate_reply_exact():
    assert _is_duplicate_reply("blocked", "blocked") is True


def test_is_duplicate_reply_substring_variant():
    assert _is_duplicate_reply("blocked", "--blocked--") is True
    assert _is_duplicate_reply("tool_blocked", "blocked") is True


def test_is_duplicate_reply_whitespace_case_insensitive():
    assert _is_duplicate_reply("  Blocked  ", "blocked") is True


def test_is_duplicate_reply_distinct():
    assert _is_duplicate_reply("blocked", "正在查询数据") is False


def test_is_duplicate_reply_long_same_prefix():
    prev = "交付物确认完成。--- # 沪深300近5日涨停选股 " + "A" * 700
    cur = "交付物确认完成。--- # 沪深300近5日涨停选股 " + "A" * 650 + "B" * 50
    assert _is_duplicate_reply(prev, cur) is True


def test_is_duplicate_reply_empty():
    assert _is_duplicate_reply("", "blocked") is False
    assert _is_duplicate_reply("blocked", "") is False


def test_forced_stop_reply_marks_intermediate_artifacts():
    state = AgentLoopState(
        progress_lines=["已写入 task/1/block.json"],
        saved_paths=["out.xlsx"],
        last_reply="blocked",
    )
    reply = _forced_stop_reply(state, "达到 200 轮上限且未收到 FINAL 结束信号")
    assert "已写入 task/1/block.json" in reply
    assert "out.xlsx" in reply
    assert "blocked" not in reply
    assert "中间产物" in reply


def test_forced_stop_reply_keeps_useful_last_reply():
    state = AgentLoopState(last_reply="这是一段有用的总结")
    reply = _forced_stop_reply(state, "达到 50 轮上限且未收到 FINAL 结束信号")
    assert "有用的总结" in reply


def test_forced_stop_reply_generic_when_empty():
    state = AgentLoopState()
    reply = _forced_stop_reply(state, "达到 50 轮上限且未收到 FINAL 结束信号")
    assert "已自动结束" in reply
    assert "50" in reply


def test_tool_result_failed_prefixes():
    assert _tool_result_failed("MCP 错误: timeout") is True
    assert _tool_result_failed("MCP 调用失败") is True
    assert _tool_result_failed("工具执行异常: boom") is True
    assert _tool_result_failed("[exit 1] cat: no such file") is True


def test_tool_result_failed_ok_and_empty():
    assert _tool_result_failed("ok result") is False
    assert _tool_result_failed("") is False


def test_is_mcp_connect_failure_transport_level():
    # Transport/connect failures key the soft circuit at the *connection* level,
    # so varied tool names can't reset the streak (the "200 rounds no result" bug).
    assert _is_mcp_connect_failure("MCP 调用失败: ConnectTimeout") is True
    assert _is_mcp_connect_failure("MCP 调用失败: timeout") is True
    assert _is_mcp_connect_failure("  MCP 调用失败: err") is True


def test_is_mcp_connect_failure_not_tool_specific():
    assert _is_mcp_connect_failure("Unknown tool: list_ads_views") is False
    assert _is_mcp_connect_failure("MCP 错误: bad args") is False
    assert _is_mcp_connect_failure("ok") is False
    assert _is_mcp_connect_failure("") is False


def test_tool_fail_hint_threshold():
    assert _tool_fail_hint("shell", "cat x", 4, 5) is None
    hint = _tool_fail_hint("shell", "cat x", 5, 5)
    assert hint is not None
    assert "shell" in hint
    assert "5 次" in hint


def test_budget_near_hint_checkpoints():
    assert _budget_near_hint(5) is not None
    assert _budget_near_hint(2) is not None
    assert _budget_near_hint(10) is None
    assert _budget_near_hint(0) is None
    assert "5 轮" in _budget_near_hint(5)


def test_distill_hint_mentions_tool_and_count():
    hint = _distill_hint("describe_ads_view", 4)
    assert "describe_ads_view" in hint
    assert "4 次" in hint
    assert "映射蒸馏" in hint


def test_looks_like_bare_python_assignment():
    assert _looks_like_bare_python("df=df.merge(blk,on='uid',how='left')") is True
    assert _looks_like_bare_python("ch['id']=ch['id'].astype('int64')") is True
    assert _looks_like_bare_python("df.to_excel('out.xlsx', index=False)") is True


def test_looks_like_bare_python_prose_is_false():
    assert _looks_like_bare_python("请继续处理数据") is False
    assert _looks_like_bare_python("") is False
    assert _looks_like_bare_python("FINAL: 完成") is False


def test_looks_like_raw_code_covers_bare_python():
    assert _looks_like_raw_code("df=df.merge(blk,on='uid',how='left')") is True
    assert _looks_like_raw_code("ch['id']=ch['id'].astype('int64')") is True


def test_rescue_leaked_code_ignores_pandas_fragment():
    # 片段引用上一轮进程的变量，不可独立运行，不应自动执行
    assert _rescue_leaked_code("df=df.merge(blk,on='uid',how='left')") is None
    # df 是 shell 的磁盘命令，但不能把 pandas 的 df 变量当 shell 命令执行
    assert _rescue_leaked_code("df.merge(blk,on='uid')") is None
    # 完整脚本（含 import）仍应被救援
    assert _rescue_leaked_code("import pandas as pd\ndf=pd.read_csv('a.csv')") is not None


def test_rescue_leaked_code_still_rescues_shell():
    assert _rescue_leaked_code("ls -la") is not None
