"""Native function-calling path: tool_calls normalization + tools/reasoning_split wiring.

No live LLM; httpx is mocked (same pattern as test_llm_transport_retry.py).
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import patch

from app.services.llm_client import (
    chat_completion,
    extract_chat_response_text,
)
from app.services.agent_runtime.runtime import _clear_run_state


# ---- tool_calls -> protocol normalization ----


def _msg(tool_calls, content=None, extra=None):
    message = {}
    if content is not None:
        message["content"] = content
    if tool_calls is not None:
        message["tool_calls"] = tool_calls
    if extra:
        message.update(extra)
    return {"choices": [{"message": message}]}


def test_done_becomes_final():
    text = extract_chat_response_text(_msg([{
        "function": {"name": "done", "arguments": '{"answer": "导出完成"}'},
    }]))
    assert text == "FINAL: 导出完成"


def test_shell_becomes_shell_protocol():
    text = extract_chat_response_text(_msg([{
        "function": {"name": "shell", "arguments": '{"cmd": "ls -la"}'},
    }]))
    assert text == "SHELL: ls -la"


def test_file_write_becomes_write_protocol():
    text = extract_chat_response_text(_msg([{
        "function": {
            "name": "file_write",
            "arguments": '{"path": "/a/b.txt", "content": "hello"}',
        },
    }]))
    assert text == "WRITE: /a/b.txt\nhello"


def test_mcp_tool_call_normalizes_to_mcp_protocol():
    text = extract_chat_response_text(_msg([{
        "function": {
            "name": "mcp_tool_call",
            "arguments": '{"tool_name": "describe_ads_view", '
                        '"arguments": {"view_name": "view_result_user_info"}}',
        },
    }]))
    assert text.startswith("MCP: describe_ads_view ")
    assert '"view_name": "view_result_user_info"' in text


def test_tool_calls_priority_over_content():
    text = extract_chat_response_text(_msg(
        [{"function": {"name": "shell", "arguments": '{"cmd": "pwd"}'}}],
        content="我先看看当前目录",
    ))
    assert text == "SHELL: pwd"


def test_reasoning_only_round_maps_to_empty():
    # MiniMax reasoning_split=True: thinking lives in reasoning_details, no tool_call.
    text = extract_chat_response_text(_msg(
        None,
        content=None,
        extra={"reasoning_details": [{"text": "考虑一下"}]},
    ))
    assert text == ""


def test_reasoning_content_only_maps_to_empty():
    text = extract_chat_response_text(_msg(
        None,
        content=None,
        extra={"reasoning_content": "纯思考"},
    ))
    assert text == ""


# ---- tools / reasoning_split wiring into the request body ----


class _FakeResponse:
    def raise_for_status(self):
        return None

    def json(self):
        return {"choices": [{"message": {"content": "ok"}}]}


class _CaptureClient:
    body = None

    def __init__(self, **_kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def post(self, *_args, **kwargs):
        type(self).body = kwargs.get("json")
        return _FakeResponse()


def _llm(base_url, provider="openai"):
    return SimpleNamespace(
        type="llm",
        api_key_enc="encrypted",
        base_url=base_url,
        provider=provider,
        model="test-model",
        max_output_tokens=128,
        max_context_tokens=4096,
    )


def _run_chat(llm, tools=None):
    async def _run():
        with patch("app.services.llm_client.decrypt_secret", return_value="secret"):
            with patch("app.services.llm_client.httpx.AsyncClient", _CaptureClient):
                return await chat_completion(
                    llm,
                    [{"role": "user", "content": "hello"}],
                    tools=tools,
                )

    return asyncio.run(_run())


def test_minimax_request_sends_tools_and_reasoning_split():
    tools = [{"type": "function", "function": {"name": "shell", "parameters": {}}}]
    _CaptureClient.body = None
    out = _run_chat(_llm("https://api.minimaxi.com/v1", provider="minimax"), tools=tools)
    assert out == "ok"
    assert _CaptureClient.body["tools"] == tools
    assert _CaptureClient.body["reasoning_split"] is True


def test_minimax_request_sends_reasoning_split_without_tools():
    _CaptureClient.body = None
    _run_chat(_llm("https://api.minimaxi.com/v1", provider="minimax"))
    assert "tools" not in _CaptureClient.body
    assert _CaptureClient.body["reasoning_split"] is True


def test_openai_request_sends_tools_without_reasoning_split():
    tools = [{"type": "function", "function": {"name": "shell", "parameters": {}}}]
    _CaptureClient.body = None
    _run_chat(_llm("https://example.test/v1", provider="openai"), tools=tools)
    assert _CaptureClient.body["tools"] == tools
    assert "reasoning_split" not in _CaptureClient.body


def test_openai_request_without_tools_omits_tools_key():
    _CaptureClient.body = None
    _run_chat(_llm("https://example.test/v1", provider="openai"))
    assert "tools" not in _CaptureClient.body


# ---- cancel / finish clears the checkpoint ----


class _FakeQuery:
    def __init__(self, row):
        self._row = row

    def filter(self, *_args, **_kwargs):
        return self

    def first(self):
        return self._row


class _FakeDB:
    def __init__(self, row):
        self._row = row
        self.deleted = []
        self.committed = 0

    def query(self, *_args):
        return _FakeQuery(self._row)

    def delete(self, row):
        self.deleted.append(row)

    def commit(self):
        self.committed += 1


def _ctx(row):
    return SimpleNamespace(
        agent=SimpleNamespace(id="a1"),
        session_id="s1",
        db=_FakeDB(row),
    )


def test_clear_run_state_deletes_existing_checkpoint():
    row = object()
    ctx = _ctx(row)
    _clear_run_state(ctx)
    assert ctx.db.deleted == [row]
    assert ctx.db.committed == 1


def test_clear_run_state_noop_when_no_checkpoint():
    ctx = _ctx(None)
    _clear_run_state(ctx)
    assert ctx.db.deleted == []
    assert ctx.db.committed == 0
