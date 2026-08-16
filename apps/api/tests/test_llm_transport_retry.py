"""LLM transport failures should retry with a readable terminal error."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx

from app.services.llm_client import chat_completion, extract_chat_response_text


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
