"""Regression coverage for native Anthropic Messages API requests."""

import asyncio
from types import SimpleNamespace
from unittest.mock import patch

from app.security import encrypt_secret
from app.services.llm_client import (
    anthropic_base_url,
    anthropic_messages_url,
    is_anthropic_provider,
    test_llm_chat as run_llm_test_chat,
)


class _Response:
    def raise_for_status(self):
        return None

    def json(self):
        return {
            "content": [
                {"type": "text", "text": "Anthropic is reachable"},
                {"type": "tool_use", "id": "tool-1"},
            ]
        }


class _Client:
    calls = []

    def __init__(self, **_kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def post(self, endpoint, **kwargs):
        type(self).calls.append((endpoint, kwargs))
        return _Response()


def _llm(provider="anthropic", base_url="https://api.anthropic.com"):
    return SimpleNamespace(
        type="llm",
        provider=provider,
        base_url=base_url,
        api_key_enc=encrypt_secret("sk-ant-test"),
        model="claude-sonnet-4-5",
        max_output_tokens=4096,
    )


def test_anthropic_provider_aliases_and_endpoint_normalization():
    assert is_anthropic_provider("anthropic")
    assert is_anthropic_provider("cloud_claude")
    assert not is_anthropic_provider("openai")
    assert anthropic_base_url("https://api.anthropic.com/v1") == "https://api.anthropic.com"
    assert anthropic_base_url("https://proxy.example/chat/completions") == "https://proxy.example"
    assert anthropic_messages_url("https://api.anthropic.com") == "https://api.anthropic.com/v1/messages"
    assert anthropic_messages_url("https://api.anthropic.com/v1") == "https://api.anthropic.com/v1/messages"
    assert anthropic_messages_url("https://proxy.example/v1/messages") == "https://proxy.example/v1/messages"


def test_test_llm_chat_uses_native_anthropic_messages_request():
    _Client.calls = []

    async def _run():
        with patch("app.services.llm_client.httpx.AsyncClient", _Client):
            return await run_llm_test_chat(_llm(), "ping")

    assert asyncio.run(_run()) == "Anthropic is reachable"
    endpoint, request = _Client.calls[0]
    assert endpoint == "https://api.anthropic.com/v1/messages"
    assert request["headers"]["x-api-key"] == "sk-ant-test"
    assert request["headers"]["anthropic-version"] == "2023-06-01"
    assert "Authorization" not in request["headers"]
    assert request["json"] == {
        "model": "claude-sonnet-4-5",
        "messages": [{"role": "user", "content": "ping"}],
        "max_tokens": 1024,
    }
