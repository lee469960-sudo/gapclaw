"""QQ bot adapter — OneBot v11 HTTP webhook (default) + simple official-style payload."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time

import httpx

from app.services.channels.base import ChannelAdapter, InboundMessage, WebhookResult, register_adapter

logger = logging.getLogger(__name__)


@register_adapter
class QQAdapter(ChannelAdapter):
    provider = "qq"

    def _verify(self, headers: dict[str, str], body: bytes) -> bool:
        secret = self.config.get("secret") or ""
        if not secret:
            return True
        sig = headers.get("x-signature") or headers.get("X-Signature") or ""
        if not sig:
            return False
        expected = "sha1=" + hmac.new(secret.encode(), body, hashlib.sha1).hexdigest()
        return hmac.compare_digest(expected, sig)

    async def handle_webhook(self, *, method: str, headers: dict[str, str], query: dict[str, str], body: bytes) -> WebhookResult:
        if not self._verify(headers, body):
            return WebhookResult(status_code=403, body={"error": "bad signature"}, skip_agent=True)
        try:
            data = json.loads(body.decode() or "{}")
        except Exception:
            return WebhookResult(status_code=400, body={"error": "invalid json"}, skip_agent=True)

        post_type = data.get("post_type") or ""
        if post_type and post_type != "message":
            return WebhookResult(body={"ok": True}, skip_agent=True)

        message_type = data.get("message_type") or data.get("chat_type") or "private"
        raw_msg = data.get("raw_message") or data.get("message") or data.get("content") or ""
        if isinstance(raw_msg, list):
            # OneBot CQ segments
            texts = []
            for seg in raw_msg:
                if isinstance(seg, dict) and seg.get("type") == "text":
                    texts.append(seg.get("data", {}).get("text") or "")
            text = "".join(texts).strip()
        else:
            text = str(raw_msg).strip()

        user_id = str(data.get("user_id") or data.get("sender", {}).get("user_id") or "")
        if message_type in ("group", "guild"):
            chat_id = str(data.get("group_id") or data.get("guild_id") or "")
            chat_type = "group"
        else:
            chat_id = user_id or str(data.get("self_id") or "qq")
            chat_type = "p2p"

        msg_id = str(data.get("message_id") or data.get("id") or f"qq-{int(time.time()*1000)}")
        if not text or not chat_id:
            return WebhookResult(body={"ok": True}, skip_agent=True)

        allow_groups = str(self.config.get("allowed_group_ids") or "")
        if chat_type == "group" and allow_groups:
            allowed = {x.strip() for x in allow_groups.split(",") if x.strip()}
            if chat_id not in allowed:
                return WebhookResult(body={"ok": True, "ignored": True}, skip_agent=True)

        return WebhookResult(
            body={"ok": True},
            inbound=InboundMessage(
                msg_id=msg_id,
                chat_id=chat_id,
                user_id=user_id,
                text=text,
                chat_type=chat_type,
                raw=data,
            ),
        )

    async def send_text(self, chat_id: str, text: str, *, reply_to: dict | None = None) -> None:
        base = (self.config.get("onebot_http_url") or "").rstrip("/")
        access = self.config.get("access_token") or ""
        if not base:
            raise RuntimeError("QQ/OneBot 未配置 onebot_http_url")
        headers = {}
        if access:
            headers["Authorization"] = f"Bearer {access}"
        raw = reply_to or {}
        is_group = (raw.get("message_type") == "group") or bool(raw.get("group_id"))
        for chunk in self.chunk_text(text, 1500):
            if is_group:
                url = f"{base}/send_group_msg"
                payload = {"group_id": int(chat_id) if str(chat_id).isdigit() else chat_id, "message": chunk}
            else:
                url = f"{base}/send_private_msg"
                payload = {"user_id": int(chat_id) if str(chat_id).isdigit() else chat_id, "message": chunk}
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(url, headers=headers, json=payload)
                if resp.status_code >= 400:
                    raise RuntimeError(f"QQ 发送失败: {resp.status_code} {resp.text[:300]}")
