"""Feishu / Lark bot adapter (event subscription + reply)."""

from __future__ import annotations

import json
import logging
import re
import time

import httpx

from app.services.channels.base import ChannelAdapter, InboundMessage, WebhookResult, register_adapter

logger = logging.getLogger(__name__)


def _feishu_error(data: dict | None, fallback: str = "") -> str:
    if not data:
        return fallback or "unknown"
    code = data.get("code")
    msg = data.get("msg") or data.get("error") or ""
    return f"code={code} msg={msg}" if code is not None else (msg or fallback or str(data)[:300])


def _extract_message_meta(data: dict) -> dict:
    """Normalize message_id / open_id / chat_type from event payload for reply."""
    header = data.get("header") or {}
    event = data.get("event") or {}
    message = event.get("message") or event
    sender = event.get("sender") or {}
    sender_id = sender.get("sender_id") or {}
    open_id = (
        sender_id.get("open_id")
        or sender.get("open_id")
        or ""
    )
    chat_type = message.get("chat_type") or event.get("chat_type") or "p2p"
    msg_id = message.get("message_id") or header.get("event_id") or ""
    chat_id = message.get("chat_id") or event.get("chat_id") or ""
    return {
        "message_id": str(msg_id),
        "open_id": str(open_id),
        "chat_id": str(chat_id),
        "chat_type": str(chat_type),
    }


@register_adapter
class FeishuAdapter(ChannelAdapter):
    provider = "feishu"

    async def handle_webhook(self, *, method: str, headers: dict[str, str], query: dict[str, str], body: bytes) -> WebhookResult:
        try:
            data = json.loads(body.decode() or "{}")
        except Exception:
            return WebhookResult(status_code=400, body={"error": "invalid json"}, skip_agent=True)

        # URL verification challenge
        if data.get("type") == "url_verification" or data.get("challenge"):
            token = self.config.get("verification_token") or ""
            if token and data.get("token") and data.get("token") != token:
                logger.warning("feishu challenge rejected: verification_token mismatch channel=%s", self.channel_id)
                return WebhookResult(status_code=403, body={"error": "bad token"}, skip_agent=True)
            logger.info("feishu challenge ok channel=%s", self.channel_id)
            return WebhookResult(body={"challenge": data.get("challenge")}, skip_agent=True)

        # Encrypted events need encrypt_key; v1 only supports plaintext
        if data.get("encrypt"):
            logger.warning(
                "feishu encrypted event ignored channel=%s — disable Encrypt Key or use plaintext events",
                self.channel_id,
            )
            return WebhookResult(body={"ok": True}, skip_agent=True)

        header = data.get("header") or {}
        event = data.get("event") or {}
        event_type = header.get("event_type") or data.get("type") or ""

        # User opened p2p chat — log only (do NOT auto-reply; spam broke real chats)
        if event_type == "im.chat.access_event.bot_p2p_chat_entered_v1":
            logger.info("feishu p2p chat entered channel=%s", self.channel_id)
            return WebhookResult(body={"ok": True}, skip_agent=True)

        # Read receipts etc. — ignore quietly
        if event_type in (
            "im.message.message_read_v1",
            "im.message.reaction.created_v1",
            "im.message.reaction.deleted_v1",
        ):
            return WebhookResult(body={"ok": True}, skip_agent=True)

        if event_type not in ("im.message.receive_v1", "message", "im.message.receive_v1".lower()):
            # Also accept schema 1.0 message events
            if "message" not in event and "message_id" not in event:
                logger.info(
                    "feishu event skipped channel=%s type=%s (need im.message.receive_v1)",
                    self.channel_id,
                    event_type or "(empty)",
                )
                return WebhookResult(body={"ok": True}, skip_agent=True)

        message = event.get("message") or event
        sender = event.get("sender") or {}
        msg_type = message.get("message_type") or message.get("msg_type") or ""
        if msg_type and msg_type != "text":
            return WebhookResult(body={"ok": True}, skip_agent=True)

        content_raw = message.get("content") or "{}"
        try:
            content = json.loads(content_raw) if isinstance(content_raw, str) else content_raw
        except Exception:
            content = {"text": str(content_raw)}
        text = (content.get("text") or "").strip()
        # Strip Feishu @ placeholders like @_user_1 (group @bot)
        text = re.sub(r"@_user_\d+\s*", "", text).strip()
        if text.startswith("@"):
            parts = text.split(maxsplit=1)
            text = parts[1] if len(parts) > 1 else ""

        meta = _extract_message_meta(data)
        chat_id = meta["chat_id"]
        msg_id = meta["message_id"] or f"fs-{int(time.time() * 1000)}"
        user_id = meta["open_id"] or (
            (sender.get("sender_id") or {}).get("user_id")
            or sender.get("open_id")
            or ""
        )
        chat_type = meta["chat_type"]
        if not text or not chat_id:
            if meta.get("chat_type") == "group":
                logger.info(
                    "feishu group message skipped channel=%s (empty text after @strip or no chat_id)",
                    self.channel_id,
                )
            return WebhookResult(body={"ok": True}, skip_agent=True)

        # Enrich raw for send_text reply routing
        raw = dict(data) if isinstance(data, dict) else {}
        raw["_gap"] = {
            "message_id": str(msg_id),
            "open_id": str(user_id),
            "chat_id": str(chat_id),
            "chat_type": str(chat_type),
        }

        return WebhookResult(
            body={"ok": True},
            inbound=InboundMessage(
                msg_id=str(msg_id),
                chat_id=str(chat_id),
                user_id=str(user_id),
                text=text,
                chat_type=str(chat_type),
                raw=raw,
            ),
        )

    async def _tenant_token(self) -> str:
        app_id = self.config.get("app_id") or ""
        app_secret = self.config.get("app_secret") or ""
        if not app_id or not app_secret:
            raise RuntimeError("飞书未配置 app_id / app_secret")
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(
                "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
                json={"app_id": app_id, "app_secret": app_secret},
            )
            data = resp.json()
            if data.get("code") != 0:
                raise RuntimeError(f"飞书 token 失败: {_feishu_error(data)}")
            return data["tenant_access_token"]

    def _reply_context(self, chat_id: str, reply_to: dict | None) -> dict:
        gap = (reply_to or {}).get("_gap") if isinstance(reply_to, dict) else None
        if isinstance(gap, dict) and gap:
            return {
                "message_id": str(gap.get("message_id") or ""),
                "open_id": str(gap.get("open_id") or ""),
                "chat_id": str(gap.get("chat_id") or chat_id),
                "chat_type": str(gap.get("chat_type") or "p2p"),
            }
        # Fallback: parse raw event if present
        if isinstance(reply_to, dict):
            meta = _extract_message_meta(reply_to)
            if meta.get("chat_id") or meta.get("message_id"):
                return meta
        return {
            "message_id": "",
            "open_id": "",
            "chat_id": chat_id,
            "chat_type": "p2p",
        }

    async def _post_message(self, token: str, *, url: str, payload: dict) -> None:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                url,
                headers={"Authorization": f"Bearer {token}"},
                json=payload,
            )
            try:
                data = resp.json()
            except Exception:
                data = None
            ok = isinstance(data, dict) and data.get("code") == 0
            if ok:
                return
            if resp.status_code < 400 and isinstance(data, dict) and data.get("code") in (0, None):
                return
            raise RuntimeError(f"飞书发送失败: {_feishu_error(data, resp.text[:300])}")

    async def send_text(self, chat_id: str, text: str, *, reply_to: dict | None = None) -> None:
        token = await self._tenant_token()
        ctx = self._reply_context(chat_id, reply_to)
        message_id = ctx.get("message_id") or ""
        open_id = ctx.get("open_id") or ""
        chat_type = (ctx.get("chat_type") or "p2p").lower()
        real_chat_id = ctx.get("chat_id") or chat_id

        for chunk in self.chunk_text(text, 3500):
            content = json.dumps({"text": chunk}, ensure_ascii=False)
            # 1) Prefer reply-to-message (best for mobile bot dialog)
            if message_id:
                try:
                    await self._post_message(
                        token,
                        url=f"https://open.feishu.cn/open-apis/im/v1/messages/{message_id}/reply",
                        payload={"msg_type": "text", "content": content},
                    )
                    continue
                except Exception as e:
                    logger.warning("feishu reply API failed, fallback to send: %s", e)

            # 2) p2p → open_id; group → chat_id
            if chat_type in ("p2p", "private") and open_id:
                receive_id_type = "open_id"
                receive_id = open_id
            else:
                receive_id_type = "chat_id"
                receive_id = real_chat_id

            try:
                await self._post_message(
                    token,
                    url=f"https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type={receive_id_type}",
                    payload={
                        "receive_id": receive_id,
                        "msg_type": "text",
                        "content": content,
                    },
                )
            except Exception:
                # Last resort: try the other id type
                if receive_id_type == "open_id" and real_chat_id:
                    await self._post_message(
                        token,
                        url="https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=chat_id",
                        payload={
                            "receive_id": real_chat_id,
                            "msg_type": "text",
                            "content": content,
                        },
                    )
                elif receive_id_type == "chat_id" and open_id:
                    await self._post_message(
                        token,
                        url="https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=open_id",
                        payload={
                            "receive_id": open_id,
                            "msg_type": "text",
                            "content": content,
                        },
                    )
                else:
                    raise
