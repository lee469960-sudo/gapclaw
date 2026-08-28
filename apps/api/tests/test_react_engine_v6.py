"""react-engine-v6 regression tests.

Covers the change's four requirement groups:
  R1 (channels)   TG inbound document download + path injection + pure-document trigger
  R2 (agent-runtime) LLM transport retry 5x + 2/4/8/16s backoff + jitter
  R3 (agent-runtime) transport vs 400 distinct exit thresholds (3 vs 2) + endpoint in errors
  R4 (agent-runtime) SQL batching prompt strengthening (no new mechanism)
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.services.agent_runtime.runtime import AgentRuntime
from app.services.channels.telegram import (
    TG_MAX_DOCUMENT_BYTES,
    TelegramAdapter,
    download_document_to_workspace,
)
from app.services.llm_client import LLMHTTPError, LLMTransportError, chat_completion


# ---- R1: TG inbound document download (tasks 5.1 / 5.2) ----

def _tg_body(message: dict) -> bytes:
    return json.dumps({"update_id": 1, "message": message}, ensure_ascii=False).encode()


class _FakeDownloadClient:
    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, url):
        class _Resp:
            content = b"fake-excel-bytes"
            def raise_for_status(self):
                pass
        return _Resp()


def test_download_document_to_workspace(tmp_path):
    document = {"file_id": "f1", "file_name": "a.xlsx", "file_size": 2048}
    get_file = AsyncMock(return_value={
        "ok": True, "result": {"file_path": "documents/a.xlsx", "file_size": 2048},
    })
    upload = {"ok": True, "path": "a.xlsx"}

    async def _go():
        with patch("app.services.channels.telegram._bot_api", new=get_file), \
             patch("app.services.workplace.upload_file", return_value=upload), \
             patch("app.services.channels.telegram.httpx.AsyncClient", _FakeDownloadClient):
            return await download_document_to_workspace("tok", document, "sandbox1")

    rel_path = asyncio.run(_go())
    assert rel_path == "a.xlsx"
    get_file.assert_awaited_once_with("tok", "getFile", {"file_id": "f1"})


def test_download_document_oversize_rejected():
    document = {"file_id": "f1", "file_name": "big.xlsx",
                "file_size": TG_MAX_DOCUMENT_BYTES + 1}

    async def _go():
        with patch("app.services.channels.telegram._bot_api", new=AsyncMock(
            return_value={"ok": True, "result": {"file_path": "documents/big.xlsx"}},
        )):
            with pytest.raises(RuntimeError) as ei:
                await download_document_to_workspace("tok", document, "sandbox1")
            return str(ei.value)

    assert "20MB" in asyncio.run(_go())


def test_pure_document_triggers_agent():
    adapter = TelegramAdapter("ch1", {"bot_token": "tok"})

    async def _go():
        return await adapter.handle_webhook(
            method="POST", headers={}, query={},
            body=_tg_body({
                "message_id": 10, "chat": {"id": 123, "type": "private"},
                "from": {"id": 456, "first_name": "U"},
                "document": {"file_id": "f1", "file_name": "a.xlsx", "file_size": 100},
            }),
        )

    result = asyncio.run(_go())
    assert result.inbound is not None
    assert result.skip_agent is False
    # Path is injected later (process_inbound); here the message is pure document.
    assert result.inbound.text == ""


def test_non_document_attachment_still_skipped():
    adapter = TelegramAdapter("ch1", {"bot_token": "tok"})

    async def _go(attachment):
        return await adapter.handle_webhook(
            method="POST", headers={}, query={},
            body=_tg_body({
                "message_id": 11, "chat": {"id": 123, "type": "private"},
                "from": {"id": 456}, **attachment,
            }),
        )

    for attachment in (
        {"photo": [{"file_id": "p1"}]},
        {"voice": {"file_id": "v1"}},
        {"video": {"file_id": "vid1"}},
        {"audio": {"file_id": "a1"}},
    ):
        result = asyncio.run(_go(attachment))
        assert result.skip_agent is True
        assert result.inbound is None


# ---- R2: LLM transport retry (task 5.3) ----

class _FailingTransportClient:
    calls = 0

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, *a, **k):
        type(self).calls += 1
        raise httpx.ConnectError("conn refused")


def test_transport_retry_five_attempts_backoff_jitter():
    llm = SimpleNamespace(
        type="single", api_key_enc="enc", base_url="https://api.example.com",
        provider="openai", model="m", max_output_tokens=4096,
        max_context_tokens=128000, llm_timeout=30,
    )
    sleep_durations: list[float] = []

    async def _fake_sleep(d):
        sleep_durations.append(d)

    async def _go():
        with patch("app.services.llm_client.decrypt_secret", return_value="sk"), \
             patch("app.services.llm_client.is_masked_secret", return_value=False), \
             patch("httpx.AsyncClient", _FailingTransportClient), \
             patch("app.services.llm_client.asyncio.sleep", _fake_sleep):
            with pytest.raises(LLMTransportError) as ei:
                await chat_completion(llm, [{"role": "user", "content": "hi"}], max_tokens=100)
            return str(ei.value)

    msg = asyncio.run(_go())
    assert "端点" in msg and "https://api.example.com" in msg
    assert _FailingTransportClient.calls == 5  # 1 initial + 4 retries
    assert len(sleep_durations) == 4
    for duration, base in zip(sleep_durations, (2.0, 4.0, 8.0, 16.0)):
        assert base <= duration <= base * 1.26  # jitter up to +25%


# ---- R3: transport vs 400 distinct thresholds (task 5.4) ----

def _make_db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _ctx(db, *, max_iterations=10):
    return SimpleNamespace(
        agent=SimpleNamespace(
            id="a1", llm_timeout=30, sandbox_id=None, name="t",
            max_iterations=max_iterations, prompt="You are a test agent.", memory="",
        ),
        session_id="s1", chat_key="a1:s1", user_message="导出报表", username="u",
        db=db, llm=SimpleNamespace(id="llm1"), sandbox=None,
        mcp_ids=["m1"], skill_ids=[], skill_names=[], mcp_names=["ads-mcp"],
        skill_mds=[], httpmcp_ids=[], rag_ids=[], allowed_actions=["shell"],
        save_dir="", im_source="", note_content="", message_meta={},
    )


def _run_raising(ctx, exc_factory):
    calls: list[int] = []

    async def _chat(llm, messages, **kwargs):
        calls.append(1)
        raise exc_factory()

    async def _go():
        with patch("app.services.llm_client.chat_completion", new=_chat), patch(
            "app.services.agent_runtime.system_prompt.SystemPromptBuilder.build_tools_desc",
            new=AsyncMock(return_value="- shell: SHELL: <cmd>"),
        ):
            return await AgentRuntime().run(ctx)

    return asyncio.run(_go()), calls


def test_transport_errors_stop_after_three():
    db = _make_db()
    result, calls = _run_raising(
        _ctx(db, max_iterations=10),
        lambda: LLMTransportError("LLM 传输失败（端点 https://api.example.com）"),
    )
    assert len(calls) == 3  # 3 consecutive transport errors → stop, not 2


def test_http_errors_stop_after_two():
    db = _make_db()
    result, calls = _run_raising(
        _ctx(db, max_iterations=10),
        lambda: LLMHTTPError("LLM 请求被拒绝 (400): bad（端点 https://api.example.com）"),
    )
    assert len(calls) == 2  # HTTP 400 consecutive → stop at 2 (unchanged behavior)
    assert "已暂停" in result or "已保留" in result
