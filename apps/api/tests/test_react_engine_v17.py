"""react-engine-v17 tests: empty soft-retry, length continuation, truncated FINAL block.

No live LLM; httpx is mocked.
"""

from __future__ import annotations

import asyncio
import json
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.models import LLMResource
from app.services.agent_runtime.runtime import AgentRuntime
from app.services.llm_client import (
    ChatResult,
    _build_chat_result,
    chat_completion,
    extract_chat_response_text,
    fit_messages_to_context,
)


# ---- helpers ----


class _SeqResponse:
    def __init__(self, payload: dict):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class _SeqClient:
    """Returns successive JSON payloads from ``queue`` (class-level)."""

    queue: list[dict] = []
    calls = 0
    bodies: list[dict] = []

    def __init__(self, **_kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def post(self, *_args, **kwargs):
        type(self).calls += 1
        type(self).bodies.append(kwargs.get("json") or {})
        if not type(self).queue:
            raise AssertionError("no more queued responses")
        return _SeqResponse(type(self).queue.pop(0))


def _llm(**overrides):
    base = dict(
        type="llm",
        api_key_enc="encrypted",
        base_url="https://example.test/v1",
        provider="openai",
        model="test-model",
        max_output_tokens=4096,
        max_context_tokens=128000,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _choice(content=None, tool_calls=None, finish_reason="stop", extra=None):
    message = {}
    if content is not None:
        message["content"] = content
    if tool_calls is not None:
        message["tool_calls"] = tool_calls
    if extra:
        message.update(extra)
    return {
        "choices": [{
            "finish_reason": finish_reason,
            "message": message,
        }],
    }


async def _run_chat(llm, messages=None, **kwargs):
    with patch("app.services.llm_client.decrypt_secret", return_value="secret"):
        with patch("app.services.llm_client.httpx.AsyncClient", _SeqClient):
            with patch("app.services.llm_client.asyncio.sleep", new=AsyncMock()):
                return await chat_completion(
                    llm,
                    messages or [{"role": "user", "content": "hi"}],
                    **kwargs,
                )


# ---- R1: empty 200 soft path ----


def test_extract_empty_content_returns_empty_not_raise():
    text = extract_chat_response_text(_choice(content="", tool_calls=[]))
    assert text == ""


def test_empty_200_retries_then_soft_empty():
    _SeqClient.queue = [
        _choice(content="", finish_reason="stop"),
        _choice(content="", finish_reason="stop"),
    ]
    _SeqClient.calls = 0
    _SeqClient.bodies = []
    result = asyncio.run(_run_chat(_llm(), collect_native=True))
    assert isinstance(result, ChatResult)
    assert result.text == ""
    assert result.content == ""
    assert not result.has_tool_calls
    assert result.output_truncated is False
    # Initial + empty retry
    assert _SeqClient.calls == 2
    assert _SeqClient.bodies[-1]["max_tokens"] >= 2048


def test_reasoning_only_no_empty_retry():
    _SeqClient.queue = [
        _choice(content=None, finish_reason="stop", extra={"reasoning_details": [{"t": "x"}]}),
    ]
    _SeqClient.calls = 0
    result = asyncio.run(_run_chat(_llm(base_url="https://api.minimaxi.com/v1"), collect_native=True))
    assert result.text == ""
    assert _SeqClient.calls == 1  # no bump retry


def test_group_empty_200_does_not_failover():
    """Empty soft return from member 1 must not kick to member 2 / wrap as 全部失败."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.database import Base

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    db.add(LLMResource(
        id="leaf1", type="llm", name="leaf1", provider="openai",
        base_url="https://a.test/v1", api_key_enc="enc", model="m1",
        max_output_tokens=4096, max_context_tokens=128000,
    ))
    db.add(LLMResource(
        id="leaf2", type="llm", name="leaf2", provider="openai",
        base_url="https://b.test/v1", api_key_enc="enc", model="m2",
        max_output_tokens=4096, max_context_tokens=128000,
    ))
    db.add(LLMResource(
        id="g1", type="group", name="g1", members=json.dumps(["leaf1", "leaf2"]),
    ))
    db.commit()
    group = db.query(LLMResource).filter(LLMResource.id == "g1").first()

    _SeqClient.queue = [
        _choice(content="", finish_reason="stop"),
        _choice(content="", finish_reason="stop"),
    ]
    _SeqClient.calls = 0
    _SeqClient.bodies = []

    result = asyncio.run(_run_chat(group, db=db, collect_native=True))
    assert isinstance(result, ChatResult)
    assert result.text == ""
    # leaf1 initial + empty retry only; leaf2 never hit (soft success, not raise).
    assert _SeqClient.calls == 2
    assert all(b.get("model") == "m1" for b in _SeqClient.bodies)


# ---- R2: length continuation ----


def test_length_continuation_stitches_full_text():
    _SeqClient.queue = [
        _choice(content="第一部分", finish_reason="length"),
        _choice(content="第二部分", finish_reason="stop"),
    ]
    _SeqClient.calls = 0
    result = asyncio.run(_run_chat(_llm(), collect_native=True))
    assert result.text == "第一部分第二部分"
    assert result.output_truncated is False
    assert _SeqClient.calls == 2


def test_length_still_truncated_after_two_continuations():
    _SeqClient.queue = [
        _choice(content="A", finish_reason="length"),
        _choice(content="B", finish_reason="length"),
        _choice(content="C", finish_reason="length"),
    ]
    _SeqClient.calls = 0
    result = asyncio.run(_run_chat(_llm(), collect_native=True))
    assert result.text == "ABC"
    assert result.output_truncated is True
    assert _SeqClient.calls == 3  # initial + 2 continuations


def test_no_finish_reason_does_not_continue():
    _SeqClient.queue = [
        _choice(content="完整短答", finish_reason="stop"),
    ]
    _SeqClient.calls = 0
    result = asyncio.run(_run_chat(_llm(), collect_native=True))
    assert result.text == "完整短答"
    assert result.output_truncated is False
    assert _SeqClient.calls == 1


# ---- R4: truncated / unexecutable tool_calls ----


def test_unexecutable_tool_calls_treated_as_empty():
    broken = [{"id": "c1", "function": {"name": "shell", "arguments": '{"cmd":'}}]  # truncated JSON
    _SeqClient.queue = [
        _choice(content="", tool_calls=broken, finish_reason="length"),
        _choice(content="", tool_calls=broken, finish_reason="length"),
    ]
    _SeqClient.calls = 0
    result = asyncio.run(_run_chat(_llm(), collect_native=True))
    assert not result.has_tool_calls
    assert result.text == ""


def test_build_chat_result_length_tools_not_executable():
    data = _choice(
        content="",
        tool_calls=[{
            "id": "c1",
            "function": {"name": "shell", "arguments": '{"cmd": "ls"}'},
        }],
        finish_reason="length",
    )
    res = _build_chat_result(data)
    assert not res.has_tool_calls
    assert res.output_truncated is True


def test_fit_min_allowed_out_raises_floor():
    msgs = [{"role": "user", "content": "x" * 100}]
    _, allowed = fit_messages_to_context(
        msgs,
        max_context_tokens=128000,
        max_output_tokens=8192,
        min_allowed_out=2048,
    )
    assert allowed >= 2048


# ---- R3: runtime blocks FINAL when output_truncated ----


class _FakeQuery:
    def filter(self, *_a, **_k):
        return self

    def order_by(self, *_a, **_k):
        return self

    def limit(self, *_a, **_k):
        return self

    def all(self):
        return []

    def first(self):
        return None


class _FakeDB:
    def __init__(self):
        self.added = []

    def add(self, row):
        self.added.append(row)

    def commit(self):
        pass

    def delete(self, row):
        pass

    def query(self, _model):
        return _FakeQuery()


def _fake_ctx(**overrides):
    base = dict(
        agent=SimpleNamespace(
            id="a1", llm_timeout=30, sandbox_id=None, name="t",
            max_iterations=5, prompt="You are a test agent.", memory="",
        ),
        session_id="s1",
        chat_key="a1:s1",
        user_message="写一篇长文",
        username="u",
        db=_FakeDB(),
        llm=SimpleNamespace(id="llm1"),
        sandbox=None,
        mcp_ids=[],
        skill_ids=[],
        skill_names=[],
        mcp_names=[],
        skill_mds=[],
        httpmcp_ids=[],
        rag_ids=[],
        allowed_actions=["shell"],
        save_dir="",
        im_source="",
        note_content="",
        message_meta={},
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def test_runtime_blocks_final_when_output_truncated():
    ctx = _fake_ctx()
    reflect = AsyncMock(return_value=None)
    calls = {"n": 0}

    async def _chat(llm, messages, **_kw):
        calls["n"] += 1
        if calls["n"] == 1:
            return ChatResult(
                text="FINAL: 这是被截断的答",
                content="FINAL: 这是被截断的答",
                output_truncated=True,
            )
        return ChatResult(text="FINAL: 完整答案在这里")

    async def _run():
        with ExitStack() as stack:
            stack.enter_context(patch("app.services.llm_client.chat_completion", new=_chat))
            stack.enter_context(patch(
                "app.services.agent_runtime.system_prompt.SystemPromptBuilder.build_tools_desc",
                new=AsyncMock(return_value="- shell: SHELL: <cmd>"),
            ))
            stack.enter_context(patch.object(AgentRuntime, "_reflect_final", new=reflect))
            return await AgentRuntime().run(ctx)

    result = asyncio.run(_run())
    assert result == "完整答案在这里"
    # First truncated FINAL must NOT reach Verifier; second complete one does.
    reflect.assert_awaited_once()
    assert calls["n"] >= 2
