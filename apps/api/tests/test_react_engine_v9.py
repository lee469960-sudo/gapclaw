"""react-engine-v9 regression tests.

Covers the change's three requirement groups:
  R1 (agent-runtime) 无依赖同轮批量引导 — prompt wording
  R2 (agent-runtime) 大结果落盘回显 — READ/SHELL dump + echo
  R3 (agent-runtime) token 估算安全余量 — allowed_out margin
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import patch

from app.services.agent_runtime.runtime import (
    _build_stuck_hint,
    _materialize_tool_result,
)
from app.services.agent_runtime.system_prompt import SystemPromptBuilder
from app.services.llm_client import fit_messages_to_context


# ---- R1: 同轮批量引导 (task 4.1) ----

def test_tools_desc_prompts_batch_and_forbids_single():
    desc = asyncio.run(SystemPromptBuilder.build_tools_desc(
        db=None, agent=None,
        allowed=["shell", "file_read", "file_search"],
        skill_ids=[], mcp_ids=[], rag_ids=[], httpmcp_ids=None,
    ))
    assert "无依赖" in desc
    assert "可同轮" in desc
    assert "BATCH:" in desc
    assert '"mode":"parallel"' in desc
    assert 'mode:"sequence"' in desc
    assert 'mode:"transaction"' in desc
    assert "不要跨安全域/MCP/权限边界混批" in desc
    assert "多个相关 SHELL 不要拆成多个 parallel child" in desc
    assert "禁止把 `WRITE:` 全文件覆盖放进 transaction" in desc
    assert "一次只输出一个工具" not in desc
    assert "调用一个工具" not in desc


def test_minimal_tools_desc_includes_batch_contract():
    desc = SystemPromptBuilder.build_minimal_tools_desc(
        allowed_actions=["file_read", "file_search_replace"],
    )
    assert "BATCH:" in desc
    assert '"mode":"parallel"' in desc
    assert 'mode:"sequence"' in desc
    assert 'mode:"transaction"' in desc
    assert "只允许 `PATCH:` child" in desc
    assert "不要跨安全域/MCP/权限边界混批" in desc
    assert "多个相关 SHELL 优先合成一条完整脚本/命令" in desc


def test_coach_hint_prompts_batch_not_single():
    state = SimpleNamespace(progress_lines=[])
    hint = _build_stuck_hint("重复文字", state)
    assert "无依赖可同轮" in hint
    assert "调用一个" not in hint
    assert "一次一个" not in hint


# ---- R2: 大结果落盘回显 (task 4.2) ----

def test_materialize_over_threshold_dumps_and_echoes(tmp_path):
    big = "line\n" * 1000  # 5000 chars, over 4000 threshold
    with patch("app.services.workplace.workplace_root", return_value=tmp_path):
        ctx_text, path = _materialize_tool_result(
            "shell", big, run_ts="t1", sandbox=None, seq=0,
        )
    assert path == "task/t1/shell_result_0.txt"
    assert "已全量写入" in ctx_text
    assert "前 10 行预览" in ctx_text
    assert "5000 字符" in ctx_text
    assert (tmp_path / "task/t1/shell_result_0.txt").read_text() == big
    # 按需取回引导
    assert ("READ" in ctx_text) or ("SEARCH" in ctx_text)


def test_materialize_under_threshold_unchanged(tmp_path):
    small = "short\n" * 10
    with patch("app.services.workplace.workplace_root", return_value=tmp_path):
        ctx_text, path = _materialize_tool_result(
            "shell", small, run_ts="t1", sandbox=None, seq=0,
        )
    assert ctx_text == small
    assert path is None


def test_materialize_search_never_dumps(tmp_path):
    big = "x" * 5000
    with patch("app.services.workplace.workplace_root", return_value=tmp_path):
        ctx_text, path = _materialize_tool_result(
            "file_search", big, run_ts="t1", sandbox=None, seq=0,
        )
    assert ctx_text == big
    assert path is None


def test_materialize_read_dumps_to_read_result(tmp_path):
    big = "y" * 5000
    with patch("app.services.workplace.workplace_root", return_value=tmp_path):
        _, path = _materialize_tool_result(
            "file_read", big, run_ts="t1",
            sandbox=SimpleNamespace(id="s1"), seq=3,
        )
    assert path == "task/t1/file_read_result_3.txt"


# ---- R3: allowed_out 安全余量 (task 4.3) ----

def test_allowed_out_shaves_safety_margin():
    msgs = [{"role": "system", "content": "sys"}]
    for i in range(60):
        msgs.append({"role": "user", "content": f"m{i:02d} " + "x" * 2000})
    _, allowed_out = fit_messages_to_context(
        msgs, max_context_tokens=60000, max_output_tokens=8192,
    )
    assert allowed_out < 8192


# ---- 长任务不超窗 (task 4.4) ----

def test_long_task_context_stays_bounded(tmp_path):
    from app.services.agent_runtime.context_manager import ContextManager

    cm = ContextManager()
    big = "row\n" * 1500  # 6000 chars, over threshold → dumped to short echo
    with patch("app.services.workplace.workplace_root", return_value=tmp_path):
        for i in range(20):
            echo, _ = _materialize_tool_result(
                "shell", big, run_ts="t1", sandbox=None, seq=i,
            )
            cm.push_tool_result(echo, action="shell", clip=6000)
    msgs, allowed_out = fit_messages_to_context(
        list(cm.messages), max_context_tokens=128000, max_output_tokens=8192,
    )
    total_est = sum((len(m.get("content") or "") + 1) // 2 for m in msgs)
    assert total_est < 30000  # echoes are small, not 20 * 6000
    assert allowed_out > 0
