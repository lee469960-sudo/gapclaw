"""End-to-end test for the single LLM-driven ReAct loop.

Covers the new contract: LLM → parse → security-gate → execute → observe →
FINAL. Completion is decided by the LLM alone; the engine only parses protocol
lines, blocks disallowed actions, and feeds results back.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.services.agent_runtime.runtime import AgentRuntime
from app.services.agent_runtime.hub import stop_chat
from app.services.llm_client import ChatResult


class _FakeDB:
    def __init__(self):
        self.added = []

    def add(self, row):
        self.added.append(row)

    def commit(self):
        pass


def _fake_ctx(**overrides):
    base = dict(
        agent=SimpleNamespace(
            id="a1", llm_timeout=30, sandbox_id=None, name="t",
            max_iterations=10, prompt="You are a test agent.", memory="",
        ),
        session_id="s1",
        chat_key="a1:s1",
        user_message="查询视图",
        username="u",
        db=_FakeDB(),
        llm=SimpleNamespace(id="llm1"),
        sandbox=None,
        mcp_ids=["m1"],
        skill_ids=[],
        skill_names=[],
        mcp_names=["ads-mcp"],
        skill_mds=[],
        httpmcp_ids=[],
        rag_ids=[],
        allowed_actions=["mcp_tool_call", "shell"],
        save_dir="",
        im_source="",
        note_content="",
        message_meta={},
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def test_single_loop_executes_tool_then_final():
    ctx = _fake_ctx()

    async def _run():
        with patch(
            "app.services.llm_client.chat_completion",
            new=AsyncMock(side_effect=[ChatResult(text="MCP: list_ads_views {}"), ChatResult(text="FINAL: 已完成")]),
        ), patch(
            "app.services.agent_runtime.system_prompt.SystemPromptBuilder.build_tools_desc",
            new=AsyncMock(return_value="- mcp ads-mcp: list_ads_views {}"),
        ), patch(
            "app.services.agent_tools.execute_action",
            new=AsyncMock(return_value="ok"),
        ) as exec_mock:
            result = await AgentRuntime().run(ctx)

        exec_mock.assert_awaited_once()
        action = exec_mock.await_args.args[0]
        assert action == "mcp_tool_call"
        return result

    assert asyncio.run(_run()) == "已完成"


def test_single_loop_blocks_disallowed_tool():
    ctx = _fake_ctx(allowed_actions=["shell"])  # mcp_tool_call not allowed

    async def _run():
        with patch(
            "app.services.llm_client.chat_completion",
            new=AsyncMock(side_effect=[ChatResult(text="MCP: list_ads_views {}"), ChatResult(text="FINAL: 无法使用 MCP")]),
        ), patch(
            "app.services.agent_runtime.system_prompt.SystemPromptBuilder.build_tools_desc",
            new=AsyncMock(return_value="- shell: SHELL: <cmd>"),
        ), patch(
            "app.services.agent_tools.execute_action",
            new=AsyncMock(return_value="should not run"),
        ) as exec_mock:
            result = await AgentRuntime().run(ctx)

        exec_mock.assert_not_awaited()
        return result

    assert asyncio.run(_run()) == "无法使用 MCP"


def test_batch_child_permission_denied_blocks_all_children_before_execution():
    ctx = _fake_ctx(allowed_actions=["file_read"])  # shell child is not allowed

    async def _run():
        with patch(
            "app.services.llm_client.chat_completion",
            new=AsyncMock(side_effect=[
                ChatResult(text=
                    'BATCH: {"mode":"parallel","children":['
                    '{"id":"r1","reply":"READ: a.py"},'
                    '{"id":"s1","reply":"SHELL: echo nope"}'
                    "]}"
                ),
                ChatResult(text="FINAL: batch blocked"),
            ]),
        ), patch(
            "app.services.agent_runtime.system_prompt.SystemPromptBuilder.build_tools_desc",
            new=AsyncMock(return_value="- read"),
        ), patch(
            "app.services.agent_tools.execute_action",
            new=AsyncMock(return_value="should not run"),
        ) as exec_mock:
            result = await AgentRuntime().run(ctx)

        exec_mock.assert_not_awaited()
        return result

    assert asyncio.run(_run()) == "batch blocked"


def test_batch_file_writes_require_transaction_before_any_child_executes():
    cases = [
        ("parallel", "WRITE: a.txt\nreplacement"),
        ("sequence", "PATCH: a.txt\nold\nnew"),
    ]

    for mode, child_reply in cases:
        ctx = _fake_ctx(allowed_actions=["file_write", "file_search_replace"])

        async def _chat(_llm, messages, **_kwargs):
            joined = "\n".join(str(m.get("content", "")) for m in messages)
            if "BATCH_RESULT:" in joined:
                assert '"batch_id": "batch-' in joined
                assert "write_requires_transaction" in joined
                assert '"retry_hint":' in joined
                return ChatResult(text="FINAL: write batch blocked")
            return ChatResult(text=(
                f'BATCH: {{"mode":"{mode}","children":['
                f'{{"id":"write","reply":{json.dumps(child_reply)}}}'
                "]}"
            ))

        async def _run():
            with patch(
                "app.services.llm_client.chat_completion",
                new=AsyncMock(side_effect=_chat),
            ), patch(
                "app.services.agent_runtime.system_prompt.SystemPromptBuilder.build_tools_desc",
                new=AsyncMock(return_value="- write\n- patch"),
            ), patch.object(
                AgentRuntime, "_reflect_final", new=AsyncMock(return_value=None)
            ), patch(
                "app.services.agent_tools.execute_action",
                new=AsyncMock(return_value="should not run"),
            ) as exec_mock:
                result = await AgentRuntime().run(ctx)

            exec_mock.assert_not_awaited()
            return result

        assert asyncio.run(_run()) == "write batch blocked"


def test_batch_rejects_cross_security_domain_children_before_execution():
    ctx = _fake_ctx(allowed_actions=["file_read", "mcp_tool_call"])
    seen_messages = []

    async def _chat(_llm, messages, **_kwargs):
        seen_messages.append(messages)
        if len(seen_messages) == 1:
            return ChatResult(text=
                'BATCH: {"mode":"parallel","children":['
                '{"id":"local","reply":"READ: a.py"},'
                '{"id":"remote","reply":"MCP: list_ads_views {}"}'
                "]}",
            )
        joined = "\n".join(str(m.get("content", "")) for m in messages)
        assert "跨安全域" in joined
        assert "cross_security_domain" in joined
        return ChatResult(text="FINAL: cross domain blocked")

    async def _run():
        with patch(
            "app.services.llm_client.chat_completion",
            new=AsyncMock(side_effect=_chat),
        ), patch(
            "app.services.agent_runtime.system_prompt.SystemPromptBuilder.build_tools_desc",
            new=AsyncMock(return_value="- read\n- mcp"),
        ), patch.object(
            AgentRuntime, "_reflect_final", new=AsyncMock(return_value=None)
        ), patch(
            "app.services.agent_tools.execute_action",
            new=AsyncMock(return_value="should not run"),
        ) as exec_mock:
            result = await AgentRuntime().run(ctx)

        exec_mock.assert_not_awaited()
        return result

    assert asyncio.run(_run()) == "cross domain blocked"


def test_batch_mcp_children_use_only_selected_mcp_ids():
    ctx = _fake_ctx(
        allowed_actions=["mcp_tool_call"],
        mcp_ids=["bound-mcp", "unselected-mcp"],
        mcp_names=["bound", "unselected"],
    )

    async def _chat(_llm, messages, **_kwargs):
        joined = "\n".join(str(m.get("content", "")) for m in messages)
        if "BATCH_RESULT:" in joined:
            return ChatResult(text="FINAL: selected mcp only")
        return ChatResult(text=
            'BATCH: {"mode":"parallel","children":['
            '{"id":"m1","reply":"MCP: tool_a {}"},'
            '{"id":"m2","reply":"MCP: tool_b {}"}'
            "]}",
        )

    async def _run():
        with patch(
            "app.services.llm_client.chat_completion",
            new=AsyncMock(side_effect=_chat),
        ), patch(
            "app.services.agent_runtime.system_prompt.SystemPromptBuilder.build_tools_desc",
            new=AsyncMock(return_value="- mcp bound"),
        ), patch.object(
            AgentRuntime, "_reflect_final", new=AsyncMock(return_value=None)
        ), patch(
            "app.services.agent_tools.execute_action",
            new=AsyncMock(return_value="ok"),
        ) as exec_mock:
            result = await AgentRuntime().run(ctx)

        assert exec_mock.await_count == 2
        for call in exec_mock.await_args_list:
            assert call.args[6] == []
            assert "unselected-mcp" not in call.args[6]
        return result

    assert asyncio.run(_run()) == "selected mcp only"


def test_transaction_batch_preflight_does_not_execute_until_write_engine_exists():
    ctx = _fake_ctx(allowed_actions=["file_read", "file_search"])

    async def _run():
        with patch(
            "app.services.llm_client.chat_completion",
            new=AsyncMock(side_effect=[
                ChatResult(text=
                    'BATCH: {"mode":"transaction","children":['
                    '{"id":"r1","reply":"READ: a.py"},'
                    '{"id":"q1","reply":"SEARCH: needle"}'
                    "]}"
                ),
                ChatResult(text="FINAL: batch preflighted"),
            ]),
        ), patch(
            "app.services.agent_runtime.system_prompt.SystemPromptBuilder.build_tools_desc",
            new=AsyncMock(return_value="- read\n- search"),
        ), patch.object(
            AgentRuntime, "_reflect_final", new=AsyncMock(return_value=None)
        ), patch(
            "app.services.agent_tools.execute_action",
            new=AsyncMock(return_value="should not run"),
        ) as exec_mock:
            result = await AgentRuntime().run(ctx)

        exec_mock.assert_not_awaited()
        return result

    assert asyncio.run(_run()) == "batch preflighted"


def test_parallel_batch_executes_children_and_compacts_mixed_results():
    ctx = _fake_ctx(allowed_actions=["file_read", "file_search"])
    seen_messages = []
    events = []

    async def _chat(_llm, messages, **_kwargs):
        seen_messages.append(messages)
        events.append(f"chat{len(seen_messages)}")
        if len(seen_messages) == 1:
            return ChatResult(text=
                'BATCH: {"mode":"parallel","children":['
                '{"id":"r1","reply":"READ: a.py"},'
                '{"id":"q1","reply":"SEARCH: needle"}'
                "]}",
            )
        joined = "\n".join(str(m.get("content", "")) for m in messages)
        assert "BATCH_RESULT:" in joined
        assert '"done": 1' in joined
        assert '"error": 1' in joined
        assert '"id": "r1"' in joined
        assert '"id": "q1"' in joined
        assert '"batch_id": "batch-' in joined
        assert '"retry_hint":' in joined
        return ChatResult(text="FINAL: batch summarized")

    async def _execute(action, _reply, *_args, **_kwargs):
        events.append(f"exec:{action}")
        if action == "file_read":
            return "read ok"
        raise RuntimeError("search failed")

    async def _run():
        with patch(
            "app.services.llm_client.chat_completion",
            new=AsyncMock(side_effect=_chat),
        ), patch(
            "app.services.agent_runtime.system_prompt.SystemPromptBuilder.build_tools_desc",
            new=AsyncMock(return_value="- read\n- search"),
        ), patch.object(
            AgentRuntime, "_reflect_final", new=AsyncMock(return_value=None)
        ), patch(
            "app.services.agent_tools.execute_action",
            new=AsyncMock(side_effect=_execute),
        ) as exec_mock:
            result = await AgentRuntime().run(ctx)

        assert exec_mock.await_count == 2
        assert len(seen_messages) == 2
        assert events == ["chat1", "exec:file_read", "exec:file_search", "chat2"]
        return result

    assert asyncio.run(_run()) == "batch summarized"


def test_parallel_batch_serializes_shell_children_to_avoid_exec_conflicts():
    ctx = _fake_ctx(allowed_actions=["shell"])
    active_shells = 0
    max_active_shells = 0
    events = []

    async def _chat(_llm, messages, **_kwargs):
        joined = "\n".join(str(m.get("content", "")) for m in messages)
        if "BATCH_RESULT:" in joined:
            return ChatResult(text="FINAL: shell serialized")
        return ChatResult(text=
            'BATCH: {"mode":"parallel","children":['
            '{"id":"s1","reply":"SHELL: echo one"},'
            '{"id":"s2","reply":"SHELL: echo two"}'
            "]}",
        )

    async def _execute(action, reply, *_args, **_kwargs):
        nonlocal active_shells, max_active_shells
        assert action == "shell"
        active_shells += 1
        max_active_shells = max(max_active_shells, active_shells)
        events.append(reply)
        await asyncio.sleep(0)
        active_shells -= 1
        return "ok"

    async def _run():
        with patch(
            "app.services.llm_client.chat_completion",
            new=AsyncMock(side_effect=_chat),
        ), patch(
            "app.services.agent_runtime.system_prompt.SystemPromptBuilder.build_tools_desc",
            new=AsyncMock(return_value="- shell"),
        ), patch.object(
            AgentRuntime, "_reflect_final", new=AsyncMock(return_value=None)
        ), patch(
            "app.services.agent_tools.execute_action",
            new=AsyncMock(side_effect=_execute),
        ) as exec_mock:
            result = await AgentRuntime().run(ctx)

        assert exec_mock.await_count == 2
        assert max_active_shells == 1
        assert events == ["SHELL: echo one", "SHELL: echo two"]
        return result

    assert asyncio.run(_run()) == "shell serialized"


def test_sequence_batch_skips_children_after_failure():
    ctx = _fake_ctx(allowed_actions=["file_read", "file_search"])
    seen_messages = []

    async def _chat(_llm, messages, **_kwargs):
        seen_messages.append(messages)
        if len(seen_messages) == 1:
            return ChatResult(text=
                'BATCH: {"mode":"sequence","children":['
                '{"id":"first","reply":"READ: a.py"},'
                '{"id":"second","reply":"SEARCH: missing"},'
                '{"id":"third","reply":"READ: c.py"}'
                "]}",
            )
        joined = "\n".join(str(m.get("content", "")) for m in messages)
        assert "BATCH_RESULT:" in joined
        assert '"mode": "sequence"' in joined
        assert '"done": 1' in joined
        assert '"error": 1' in joined
        assert '"skipped": 1' in joined
        assert '"id": "third"' in joined
        assert "previous_child_failed" in joined
        return ChatResult(text="FINAL: sequence summarized")

    async def _execute(action, reply, *_args, **_kwargs):
        if reply == "READ: a.py":
            return "first ok"
        if action == "file_search":
            raise RuntimeError("search failed")
        raise AssertionError("third child must be skipped")

    async def _run():
        with patch(
            "app.services.llm_client.chat_completion",
            new=AsyncMock(side_effect=_chat),
        ), patch(
            "app.services.agent_runtime.system_prompt.SystemPromptBuilder.build_tools_desc",
            new=AsyncMock(return_value="- read\n- search"),
        ), patch.object(
            AgentRuntime, "_reflect_final", new=AsyncMock(return_value=None)
        ), patch(
            "app.services.agent_tools.execute_action",
            new=AsyncMock(side_effect=_execute),
        ) as exec_mock:
            result = await AgentRuntime().run(ctx)

        assert exec_mock.await_count == 2
        return result

    assert asyncio.run(_run()) == "sequence summarized"


def test_parallel_batch_retries_transient_child_once_and_records_attempts():
    ctx = _fake_ctx(allowed_actions=["file_read"])
    calls = 0
    seen_messages = []

    async def _chat(_llm, messages, **_kwargs):
        seen_messages.append(messages)
        if len(seen_messages) == 1:
            return ChatResult(text=
                'BATCH: {"mode":"parallel","children":['
                '{"id":"r1","reply":"READ: a.py"}'
                "]}",
            )
        joined = "\n".join(str(m.get("content", "")) for m in messages)
        assert '"attempts": 2' in joined
        assert '"retries": [' in joined
        assert "transient read failure" in joined
        return ChatResult(text="FINAL: retry recorded")

    async def _execute(_action, _reply, *_args, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("transient read failure")
        return "read ok after retry"

    async def _run():
        with patch(
            "app.services.llm_client.chat_completion",
            new=AsyncMock(side_effect=_chat),
        ), patch(
            "app.services.agent_runtime.system_prompt.SystemPromptBuilder.build_tools_desc",
            new=AsyncMock(return_value="- read"),
        ), patch.object(
            AgentRuntime, "_reflect_final", new=AsyncMock(return_value=None)
        ), patch(
            "app.services.agent_tools.execute_action",
            new=AsyncMock(side_effect=_execute),
        ) as exec_mock:
            result = await AgentRuntime().run(ctx)

        assert exec_mock.await_count == 2
        return result

    assert asyncio.run(_run()) == "retry recorded"


def test_batch_file_reads_report_complete_and_incomplete_by_budget(monkeypatch, tmp_path):
    monkeypatch.setenv("REACT_BENCH_WORKSPACE_ROOT", str(tmp_path))
    ctx = _fake_ctx(allowed_actions=["file_read"])
    ctx.agent.tool_result_clip = 20
    seen_messages = []

    async def _chat(_llm, messages, **_kwargs):
        seen_messages.append(messages)
        if len(seen_messages) == 1:
            return ChatResult(text=
                'BATCH: {"mode":"parallel","children":['
                '{"id":"small","reply":"READ: small.txt"},'
                '{"id":"medium","reply":"READ: medium.txt"},'
                '{"id":"large","reply":"READ: large.txt"}'
                "]}",
            )
        joined = "\n".join(str(m.get("content", "")) for m in messages)
        assert '"id": "small"' in joined
        assert '"id": "medium"' in joined
        assert '"id": "large"' in joined
        assert joined.count('"complete": true') == 2
        assert '"complete": false' in joined
        assert '"omitted_chars":' in joined
        assert '"detail_path": "task/' in joined
        assert "LLLLLLLLLLLLLLLLLLLL" in joined
        assert "L" * 50 not in joined
        return ChatResult(text="FINAL: read metadata ok")

    async def _execute(_action, reply, *_args, **_kwargs):
        if reply == "READ: small.txt":
            return "small"
        if reply == "READ: medium.txt":
            return "medium content"
        return "L" * 50

    async def _run():
        with patch(
            "app.services.llm_client.chat_completion",
            new=AsyncMock(side_effect=_chat),
        ), patch(
            "app.services.agent_runtime.system_prompt.SystemPromptBuilder.build_tools_desc",
            new=AsyncMock(return_value="- read"),
        ), patch.object(
            AgentRuntime, "_reflect_final", new=AsyncMock(return_value=None)
        ), patch(
            "app.services.agent_tools.execute_action",
            new=AsyncMock(side_effect=_execute),
        ):
            return await AgentRuntime().run(ctx)

    assert asyncio.run(_run()) == "read metadata ok"
    written = list((tmp_path / "task").glob("*/batch_read_large.txt"))
    assert len(written) == 1
    assert written[0].read_text(encoding="utf-8") == "L" * 50
    assistant_messages = [row for row in ctx.db.added if getattr(row, "role", "") == "assistant"]
    meta = json.loads(assistant_messages[-1].meta)
    batch_step = next(step for step in meta["steps"] if step.get("action") == "tool_batch")
    large_child = next(child for child in batch_step["batch"]["children"] if child.get("id") == "large")
    assert large_child["complete"] is False
    assert large_child["detail_path"].startswith("task/")


def test_batch_coalesces_adjacent_file_read_ranges():
    ctx = _fake_ctx(allowed_actions=["file_read"])
    seen_messages = []

    async def _chat(_llm, messages, **_kwargs):
        seen_messages.append(messages)
        if len(seen_messages) == 1:
            return ChatResult(text=
                'BATCH: {"mode":"parallel","children":['
                '{"id":"r1","reply":"READ: notes.txt#L1-L2"},'
                '{"id":"r2","reply":"READ: notes.txt#L3-L4"}'
                "]}",
            )
        joined = "\n".join(str(m.get("content", "")) for m in messages)
        assert joined.count('"coalesced": true') >= 2
        assert '"range": {"path": "notes.txt", "start": 1, "end": 2}' in joined
        assert '"range": {"path": "notes.txt", "start": 3, "end": 4}' in joined
        assert "line1\\nline2" in joined
        assert "line3\\nline4" in joined
        return ChatResult(text="FINAL: coalesced")

    async def _execute(action, reply, *_args, **_kwargs):
        assert action == "file_read"
        assert reply == "READ: notes.txt"
        return "line1\nline2\nline3\nline4\nline5"

    async def _run():
        with patch(
            "app.services.llm_client.chat_completion",
            new=AsyncMock(side_effect=_chat),
        ), patch(
            "app.services.agent_runtime.system_prompt.SystemPromptBuilder.build_tools_desc",
            new=AsyncMock(return_value="- read"),
        ), patch.object(
            AgentRuntime, "_reflect_final", new=AsyncMock(return_value=None)
        ), patch(
            "app.services.agent_tools.execute_action",
            new=AsyncMock(side_effect=_execute),
        ) as exec_mock:
            result = await AgentRuntime().run(ctx)

        assert exec_mock.await_count == 1
        return result

    assert asyncio.run(_run()) == "coalesced"


def test_transaction_batch_dry_run_conflict_does_not_commit_any_patch():
    ctx = _fake_ctx(allowed_actions=["file_search_replace"])
    seen_messages = []

    async def _chat(_llm, messages, **_kwargs):
        seen_messages.append(messages)
        if len(seen_messages) == 1:
            return ChatResult(text=
                'BATCH: {"mode":"transaction","children":['
                '{"id":"p1","reply":"PATCH: a.txt\\nold\\nnew"},'
                '{"id":"p2","reply":"PATCH: b.txt\\nmissing\\nreplacement"}'
                "]}",
            )
        joined = "\n".join(str(m.get("content", "")) for m in messages)
        assert '"mode": "transaction"' in joined
        assert '"phase": "dry_run"' in joined
        assert '"validation_ok": false' in joined
        assert '"committed": 0' in joined
        assert "patch_conflict" in joined
        assert '"retry_hint":' in joined
        return ChatResult(text="FINAL: transaction blocked")

    calls = []

    async def _execute(action, reply, *_args, **_kwargs):
        calls.append((action, reply))
        assert action == "file_read"
        if reply == "READ: a.txt":
            return "old content"
        if reply == "READ: b.txt":
            return "different content"
        raise AssertionError(reply)

    async def _run():
        with patch(
            "app.services.llm_client.chat_completion",
            new=AsyncMock(side_effect=_chat),
        ), patch(
            "app.services.agent_runtime.system_prompt.SystemPromptBuilder.build_tools_desc",
            new=AsyncMock(return_value="- patch"),
        ), patch.object(
            AgentRuntime, "_reflect_final", new=AsyncMock(return_value=None)
        ), patch(
            "app.services.agent_tools.execute_action",
            new=AsyncMock(side_effect=_execute),
        ):
            return await AgentRuntime().run(ctx)

    assert asyncio.run(_run()) == "transaction blocked"
    assert calls == [("file_read", "READ: a.txt"), ("file_read", "READ: b.txt")]


def test_transaction_batch_commits_all_patches_after_successful_dry_run():
    ctx = _fake_ctx(allowed_actions=["file_search_replace"])
    seen_messages = []

    async def _chat(_llm, messages, **_kwargs):
        seen_messages.append(messages)
        if len(seen_messages) == 1:
            return ChatResult(text=
                'BATCH: {"mode":"transaction","children":['
                '{"id":"p1","reply":"PATCH: a.txt\\nold-a\\nnew-a"},'
                '{"id":"p2","reply":"PATCH: b.txt\\nold-b\\nnew-b"}'
                "]}",
            )
        joined = "\n".join(str(m.get("content", "")) for m in messages)
        assert '"validation_ok": true' in joined
        assert '"committed": 2' in joined
        assert '"status": "committed"' in joined
        return ChatResult(text="FINAL: transaction committed")

    calls = []

    async def _execute(action, reply, *_args, **_kwargs):
        calls.append((action, reply))
        if action == "file_read" and reply == "READ: a.txt":
            return "old-a content"
        if action == "file_read" and reply == "READ: b.txt":
            return "old-b content"
        if action == "file_search_replace":
            return f"patched {reply.splitlines()[0]}"
        raise AssertionError((action, reply))

    async def _run():
        with patch(
            "app.services.llm_client.chat_completion",
            new=AsyncMock(side_effect=_chat),
        ), patch(
            "app.services.agent_runtime.system_prompt.SystemPromptBuilder.build_tools_desc",
            new=AsyncMock(return_value="- patch"),
        ), patch.object(
            AgentRuntime, "_reflect_final", new=AsyncMock(return_value=None)
        ), patch(
            "app.services.agent_tools.execute_action",
            new=AsyncMock(side_effect=_execute),
        ):
            return await AgentRuntime().run(ctx)

    assert asyncio.run(_run()) == "transaction committed"
    assert calls == [
        ("file_read", "READ: a.txt"),
        ("file_read", "READ: b.txt"),
        ("file_search_replace", "PATCH: a.txt\nold-a\nnew-a"),
        ("file_search_replace", "PATCH: b.txt\nold-b\nnew-b"),
    ]


def test_transaction_batch_rejects_full_file_write_and_prevents_sibling_commit():
    ctx = _fake_ctx(allowed_actions=["file_write", "file_search_replace"])
    seen_messages = []

    async def _chat(_llm, messages, **_kwargs):
        seen_messages.append(messages)
        if len(seen_messages) == 1:
            return ChatResult(text=
                'BATCH: {"mode":"transaction","children":['
                '{"id":"w1","reply":"WRITE: a.txt\\nfull replacement"},'
                '{"id":"p1","reply":"PATCH: b.txt\\nold\\nnew"}'
                "]}",
            )
        joined = "\n".join(str(m.get("content", "")) for m in messages)
        assert "transaction_child_must_be_patch" in joined
        assert '"validation_ok": false' in joined
        assert '"committed": 0' in joined
        return ChatResult(text="FINAL: write rejected")

    async def _execute(action, reply, *_args, **_kwargs):
        assert action == "file_read"
        assert reply == "READ: b.txt"
        return "old content"

    async def _run():
        with patch(
            "app.services.llm_client.chat_completion",
            new=AsyncMock(side_effect=_chat),
        ), patch(
            "app.services.agent_runtime.system_prompt.SystemPromptBuilder.build_tools_desc",
            new=AsyncMock(return_value="- write\n- patch"),
        ), patch.object(
            AgentRuntime, "_reflect_final", new=AsyncMock(return_value=None)
        ), patch(
            "app.services.agent_tools.execute_action",
            new=AsyncMock(side_effect=_execute),
        ) as exec_mock:
            result = await AgentRuntime().run(ctx)

        # Only dry-run read for the valid patch sibling is allowed; no WRITE/PATCH commit.
        assert exec_mock.await_count == 1
        return result

    assert asyncio.run(_run()) == "write rejected"


def test_batch_step_serializes_expandable_child_details_for_history():
    ctx = _fake_ctx(allowed_actions=["file_read", "shell"])

    async def _chat(_llm, messages, **_kwargs):
        joined = "\n".join(str(m.get("content", "")) for m in messages)
        if "BATCH_RESULT:" in joined:
            return ChatResult(text="FINAL: observed")
        return ChatResult(text=
            'BATCH: {"mode":"sequence","children":['
            '{"id":"ok","reply":"READ: ok.txt"},'
            '{"id":"bad","reply":"SHELL: fail-token SECRET=abc123"},'
            '{"id":"skip","reply":"READ: later.txt"}'
            "]}",
        )

    async def _execute(action, reply, *_args, **_kwargs):
        if action == "file_read":
            return "small result"
        if action == "shell":
            return "[exit 1] boom"
        return "unexpected"

    async def _run():
        with patch(
            "app.services.llm_client.chat_completion",
            new=AsyncMock(side_effect=_chat),
        ), patch(
            "app.services.agent_runtime.system_prompt.SystemPromptBuilder.build_tools_desc",
            new=AsyncMock(return_value="- read\n- shell"),
        ), patch.object(
            AgentRuntime, "_reflect_final", new=AsyncMock(return_value=None)
        ), patch(
            "app.services.agent_tools.execute_action",
            new=AsyncMock(side_effect=_execute),
        ):
            return await AgentRuntime().run(ctx)

    assert asyncio.run(_run()) == "observed"
    assistant_messages = [row for row in ctx.db.added if getattr(row, "role", "") == "assistant"]
    assert assistant_messages
    meta = json.loads(assistant_messages[-1].meta)
    batch_step = next(step for step in meta["steps"] if step.get("action") == "tool_batch")
    batch = batch_step["batch"]
    assert batch["batch_id"].startswith("batch-")
    assert batch["mode"] == "sequence"
    assert batch["done"] == 1
    assert batch["error"] == 1
    assert batch["skipped"] == 1
    statuses = {child["id"]: child["status"] for child in batch["children"]}
    assert statuses == {"ok": "done", "bad": "error", "skip": "skipped"}
    assert batch["children"][0]["complete"] is True
    assert batch["children"][0]["args_preview"] == "READ: ok.txt"
    assert "SECRET=abc123" not in batch["children"][1]["args_preview"]
    assert batch["children"][1]["retry_hint"]

    from app.routers.agent_chat import _steps_tail_for_message

    history = _steps_tail_for_message(assistant_messages[-1].meta, limit=None)
    history_batch = next(step for step in history["steps"] if step.get("action") == "tool_batch")["batch"]
    assert [child["status"] for child in history_batch["children"]] == ["done", "error", "skipped"]


def test_cancellation_between_native_tool_calls_blocks_the_next_action():
    ctx = _fake_ctx(allowed_actions=["shell"])
    calls = [
        {"id": "c1", "function": {"name": "shell", "arguments": '{"cmd":"first"}'}},
        {"id": "c2", "function": {"name": "shell", "arguments": '{"cmd":"second"}'}},
    ]

    async def _execute(*_args, **_kwargs):
        stop_chat(ctx.agent.id, ctx.session_id)
        return "ok"

    async def _run():
        with patch(
            "app.services.llm_client.chat_completion",
            new=AsyncMock(return_value=ChatResult(tool_calls=calls)),
        ), patch(
            "app.services.agent_runtime.system_prompt.SystemPromptBuilder.build_tools_desc",
            new=AsyncMock(return_value="- shell"),
        ), patch(
            "app.services.agent_tools.execute_action",
            new=AsyncMock(side_effect=_execute),
        ) as exec_mock:
            result = await AgentRuntime().run(ctx)
        assert exec_mock.await_count == 1
        return result

    assert asyncio.run(_run()) == "[已停止]"
