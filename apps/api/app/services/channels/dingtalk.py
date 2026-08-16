"""DingTalk robot adapter (HTTP callback)."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import time

import httpx

from app.services.channels.base import ChannelAdapter, InboundMessage, WebhookResult, register_adapter

logger = logging.getLogger(__name__)


@register_adapter
class DingTalkAdapter(ChannelAdapter):
    provider = "dingtalk"

    def _verify_sign(self, headers: dict[str, str], body: bytes) -> bool:
        secret = self.config.get("app_secret") or self.config.get("secret") or ""
        if not secret:
            return True
        ts = headers.get("timestamp") or headers.get("Timestamp") or ""
        sign = headers.get("sign") or headers.get("Sign") or ""
        if not ts or not sign:
            return False
        string_to_sign = f"{ts}\n{secret}".encode()
        h = hmac.new(secret.encode(), string_to_sign, digestmod=hashlib.sha256).digest()
        expected = base64.b64encode(h).decode()
        return hmac.compare_digest(expected, sign)

    async def handle_webhook(self, *, method: str, headers: dict[str, str], query: dict[str, str], body: bytes) -> WebhookResult:
        if not self._verify_sign(headers, body):
            return WebhookResult(status_code=403, body={"error": "invalid sign"}, skip_agent=True)
        try:
            data = json.loads(body.decode() or "{}")
        except Exception:
            return WebhookResult(status_code=400, body={"error": "invalid json"}, skip_agent=True)

        text = ""
        text_obj = data.get("text") or {}
        if isinstance(text_obj, dict):
            text = (text_obj.get("content") or "").strip()
        elif isinstance(text_obj, str):
            text = text_obj.strip()
        # Strip @bot
        if text.startswith("@"):
            parts = text.split(maxsplit=1)
            text = parts[1] if len(parts) > 1 else ""

        chat_id = str(
            data.get("conversationId")
            or data.get("chatbotUserId")
            or data.get("senderStaffId")
            or ""
        )
        # Prefer sessionWebhook for reply if present
        session_webhook = data.get("sessionWebhook") or ""
        msg_id = str(data.get("msgId") or data.get("msgid") or f"dt-{int(time.time()*1000)}")
        user_id = str(data.get("senderStaffId") or data.get("senderId") or "")
        chat_type = "group" if data.get("conversationType") == "2" else "p2p"

        if not text:
            return WebhookResult(body={"msgtype": "empty"}, skip_agent=True)

        inbound = InboundMessage(
            msg_id=msg_id,
            chat_id=chat_id or session_webhook or "dingtalk",
            user_id=user_id,
            text=text,
            chat_type=chat_type,
            raw={**data, "_session_webhook": session_webhook},
        )
        # ACK quickly; reply via sessionWebhook asynchronously
        return WebhookResult(body={"msgtype": "empty"}, inbound=inbound)

    async def send_text(self, chat_id: str, text: str, *, reply_to: dict | None = None) -> None:
        session_webhook = ""
        if reply_to:
            session_webhook = (reply_to.get("_session_webhook") or reply_to.get("sessionWebhook") or "")
        if not session_webhook:
            # Fallback: OpenAPI robot send (needs access token) — optional
            raise RuntimeError("钉钉缺少 sessionWebhook，无法回发（请用 HTTP 回调机器人）")
        for chunk in self.chunk_text(text, 2000):
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    session_webhook,
                    json={"msgtype": "text", "text": {"content": chunk}},
                )
                if resp.status_code >= 400:
                    raise RuntimeError(f"钉钉发送失败: {resp.status_code} {resp.text[:300]}")
