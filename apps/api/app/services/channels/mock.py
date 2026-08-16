"""Mock channel for end-to-end testing without a real IM platform."""

from __future__ import annotations

import json
import time

from app.services.channels.base import ChannelAdapter, InboundMessage, WebhookResult, register_adapter


@register_adapter
class MockAdapter(ChannelAdapter):
    provider = "mock"

    # In-memory outbox for tests / UI preview
    outbox: list[dict] = []

    async def handle_webhook(self, *, method: str, headers: dict[str, str], query: dict[str, str], body: bytes) -> WebhookResult:
        try:
            data = json.loads(body.decode() or "{}")
        except Exception:
            data = {}
        text = (data.get("text") or data.get("message") or query.get("text") or "").strip()
        chat_id = str(data.get("chat_id") or query.get("chat_id") or "mock-chat")
        user_id = str(data.get("user_id") or query.get("user_id") or "mock-user")
        msg_id = str(data.get("msg_id") or f"mock-{int(time.time() * 1000)}")
        if not text:
            return WebhookResult(body={"ok": False, "error": "text required"}, skip_agent=True)
        return WebhookResult(
            body={"ok": True, "accepted": True},
            inbound=InboundMessage(
                msg_id=msg_id,
                chat_id=chat_id,
                user_id=user_id,
                text=text,
                chat_type=str(data.get("chat_type") or "p2p"),
                raw=data if isinstance(data, dict) else {},
            ),
        )

    async def send_text(self, chat_id: str, text: str, *, reply_to: dict | None = None) -> None:
        MockAdapter.outbox.append({
            "channel_id": self.channel_id,
            "chat_id": chat_id,
            "text": text,
            "ts": time.time(),
        })
