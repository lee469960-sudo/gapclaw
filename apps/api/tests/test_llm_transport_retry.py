"""LLM transport failures should retry with a readable terminal error."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import httpx
import pytest

from app.services.llm_client import (
    LLMHTTPError,
    LLMTransportError,
    _clear_llm_throttle_circuit,
    chat_completion,
    extract_chat_response_text,
)


def _render_log_calls(calls) -> str:
    lines = []
    for call in calls:
        template = call.args[0]
        args = call.args[1:]
        lines.append(template % args if args else template)
    return "\n".join(lines)


class _FakeResponse:
    def raise_for_status(self):
        return None

    def json(self):
        return {"choices": [{"message": {"content": "ok"}}]}


class _FlakyClient:
    calls = 0

    def __init__(self, **_kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def post(self, *_args, **_kwargs):
        type(self).calls += 1
        if type(self).calls < 3:
            raise httpx.ReadTimeout("")
        return _FakeResponse()


def test_chat_completion_retries_transport_errors():
    _clear_llm_throttle_circuit()
    _FlakyClient.calls = 0
    llm = SimpleNamespace(
        type="llm",
        api_key_enc="encrypted",
        base_url="https://example.test/v1",
        provider="openai",
        model="test-model",
        max_output_tokens=128,
        max_context_tokens=4096,
    )

    async def _run():
        with patch("app.services.llm_client.decrypt_secret", return_value="secret"):
            with patch("app.services.llm_client.httpx.AsyncClient", _FlakyClient):
                with patch(
                    "app.services.llm_client.asyncio.sleep",
                    new=AsyncMock(),
                ):
                    return await chat_completion(
                        llm,
                        [{"role": "user", "content": "hello"}],
                    )

    assert asyncio.run(_run()) == "ok"
    assert _FlakyClient.calls == 3


class _AlwaysTransportFailClient:
    calls = 0

    def __init__(self, **_kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def post(self, *_args, **_kwargs):
        type(self).calls += 1
        raise httpx.ConnectError("secret-token")


class _StatusResponse:
    def __init__(self, status: int):
        self.status = status

    def raise_for_status(self):
        req = httpx.Request("POST", "https://example.test/v1/chat/completions")
        resp = httpx.Response(self.status, request=req)
        raise httpx.HTTPStatusError(f"{self.status} error", request=req, response=resp)


class _AlwaysStatusClient:
    calls = 0

    def __init__(self, status=429):
        self.status = status

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def post(self, *_args, **_kwargs):
        type(self).calls += 1
        return _StatusResponse(self.status)


def _llm(**overrides):
    base = {
        "type": "llm",
        "api_key_enc": "encrypted",
        "base_url": "https://secret.example.test/v1?api_key=token",
        "provider": "openai",
        "model": "test-model",
        "max_output_tokens": 128,
        "max_context_tokens": 4096,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def test_chat_completion_logs_structured_429_throttling_without_secrets():
    _clear_llm_throttle_circuit()
    _AlwaysStatusClient.calls = 0
    log = Mock()

    async def _run():
        with patch("app.services.llm_client.decrypt_secret", return_value="secret"):
            with patch(
                "app.services.llm_client.httpx.AsyncClient",
                lambda **kw: _AlwaysStatusClient(status=429),
            ):
                with patch("app.services.llm_client.asyncio.sleep", new=AsyncMock()):
                    with patch("app.services.llm_client.random.uniform", return_value=0):
                        with patch("app.services.llm_client.logger", log):
                            await chat_completion(_llm(), [{"role": "user", "content": "hi"}])

    with pytest.raises(LLMHTTPError) as exc:
        asyncio.run(_run())

    assert _AlwaysStatusClient.calls == 1
    assert "限流" in str(exc.value)
    joined = _render_log_calls(log.warning.call_args_list)
    assert "component=llm" in joined
    assert "class=throttling" in joined
    assert "status=429" in joined
    assert "429" in joined
    assert "secret.example.test/v1" in joined
    assert "api_key" not in joined
    assert "token" not in joined
    assert "secret-token" not in joined


def test_chat_completion_logs_transport_exhaustion_as_network_connectivity():
    _clear_llm_throttle_circuit()
    _AlwaysTransportFailClient.calls = 0
    log = Mock()

    async def _run():
        with patch("app.services.llm_client.decrypt_secret", return_value="secret"):
            with patch("app.services.llm_client.httpx.AsyncClient", _AlwaysTransportFailClient):
                with patch("app.services.llm_client.asyncio.sleep", new=AsyncMock()):
                    with patch("app.services.llm_client.random.uniform", return_value=0):
                        with patch("app.services.llm_client.logger", log):
                            await chat_completion(_llm(), [{"role": "user", "content": "hi"}])

    with pytest.raises(LLMTransportError):
        asyncio.run(_run())

    assert _AlwaysTransportFailClient.calls == 5
    joined = _render_log_calls(log.warning.call_args_list)
    assert "component=llm" in joined
    assert "class=network_connectivity" in joined
    assert "max_attempts=5" in joined
    assert "ConnectError" in joined
    assert "api_key" not in joined
    assert "token" not in joined
    assert "secret-token" not in joined


def test_chat_response_direct_tool_call_becomes_mcp_protocol():
    text = extract_chat_response_text({
        "choices": [{
            "message": {
                "tool_calls": [{
                    "function": {
                        "name": "list_ads_views",
                        "arguments": "{}",
                    },
                }],
            },
        }],
    })

    assert text == "MCP: list_ads_views {}"


def test_chat_response_mcp_wrapper_is_unpacked():
    text = extract_chat_response_text({
        "choices": [{
            "message": {
                "content": None,
                "tool_calls": [{
                    "function": {
                        "name": "mcp",
                        "arguments": (
                            '{"tool_name":"describe_ads_view",'
                            '"arguments":{"view_name":"view_result_user_info"}}'
                        ),
                    },
                }],
            },
        }],
    })

    assert text.startswith("MCP: describe_ads_view ")
    assert '"view_name": "view_result_user_info"' in text
