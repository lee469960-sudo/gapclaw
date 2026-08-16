"""IM channel adapter interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class InboundMessage:
    msg_id: str
    chat_id: str
    user_id: str = ""
    sender_username: str = ""
    sender_display_name: str = ""
    text: str = ""
    chat_type: str = "p2p"  # p2p | group
    raw: dict = field(default_factory=dict)


@dataclass
class WebhookResult:
    """Immediate HTTP response for the platform (challenge ACK, etc.)."""

    status_code: int = 200
    body: Any = None  # dict | str
    content_type: str = "application/json"
    inbound: InboundMessage | None = None
    skip_agent: bool = False
    # Optional one-shot outbound (e.g. welcome on p2p chat entered), no Agent
    outbound_hint: dict | None = None  # {chat_id, text, reply_to?}


class ChannelAdapter(ABC):
    provider: str = ""

    def __init__(self, channel_id: str, config: dict):
        self.channel_id = channel_id
        self.config = config or {}

    @abstractmethod
    async def handle_webhook(
        self,
        *,
        method: str,
        headers: dict[str, str],
        query: dict[str, str],
        body: bytes,
    ) -> WebhookResult:
        ...

    @abstractmethod
    async def send_text(self, chat_id: str, text: str, *, reply_to: dict | None = None) -> None:
        ...

    async def send_document(
        self,
        chat_id: str,
        file_path: str,
        *,
        caption: str | None = None,
    ) -> None:
        """Optional: providers that support file push override this."""
        raise NotImplementedError(f"{self.provider} 不支持发送文件")

    def chunk_text(self, text: str, limit: int = 3500) -> list[str]:
        text = (text or "").strip() or "(空回复)"
        if len(text) <= limit:
            return [text]
        parts = []
        while text:
            parts.append(text[:limit])
            text = text[limit:]
        return parts


_REGISTRY: dict[str, type[ChannelAdapter]] = {}


def register_adapter(cls: type[ChannelAdapter]) -> type[ChannelAdapter]:
    _REGISTRY[cls.provider] = cls
    return cls


def get_adapter_class(provider: str) -> type[ChannelAdapter] | None:
    return _REGISTRY.get(provider)


def list_providers() -> list[str]:
    return sorted(_REGISTRY.keys())


def create_adapter(provider: str, channel_id: str, config: dict) -> ChannelAdapter:
    cls = get_adapter_class(provider)
    if not cls:
        raise ValueError(f"未知渠道类型: {provider}")
    return cls(channel_id, config)
