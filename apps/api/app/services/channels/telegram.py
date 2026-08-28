"""Telegram Bot API adapter (webhook)."""

from __future__ import annotations

import json
import logging
import time
from typing import Any
from urllib.parse import urlparse

import httpx

from app.services.channels.base import ChannelAdapter, InboundMessage, WebhookResult, register_adapter
from app.services.channels.telegram_markdown import telegram_html_chunks
from app.services.channels.telegram_rich import iter_rich_markdown_chunks

logger = logging.getLogger(__name__)

_bot_username_cache: dict[str, str] = {}

# Bot API `getFile` download limit: documents larger than this are rejected
# before any download is attempted (react-engine-v6 R1).
TG_MAX_DOCUMENT_BYTES = 20 * 1024 * 1024


async def download_document_to_workspace(
    token: str,
    document: dict[str, Any],
    sandbox_id: str,
) -> str:
    """Download a TG ``document`` into the sandbox workspace; return its rel path.

    ``file_id`` → ``getFile`` → ``file_path`` → download bytes → ``upload_file``.
    Raises ``RuntimeError`` on a missing ``file_id``, oversize (>20MB), ``getFile``
    failure, or a workspace write failure. The returned path is workspace-relative
    and readable by the agent via the existing ``READ:``/``SHELL:`` protocol.
    """
    from app.services.workplace import upload_file

    doc = document if isinstance(document, dict) else {}
    file_id = str(doc.get("file_id") or "").strip()
    if not file_id:
        raise RuntimeError("TG 附件缺少 file_id")

    size = int(doc.get("file_size") or 0)
    data = await _bot_api(token, "getFile", {"file_id": file_id})
    if not data.get("ok"):
        raise RuntimeError(f"getFile 失败: {data.get('description') or data}")
    result = data.get("result") if isinstance(data.get("result"), dict) else {}
    file_path = str(result.get("file_path") or "").strip()
    if not file_path:
        raise RuntimeError("getFile 返回空 file_path")
    if size <= 0:
        size = int(result.get("file_size") or 0)
    if size > TG_MAX_DOCUMENT_BYTES:
        raise RuntimeError(f"附件超过 20MB 上限（{size} bytes），无法下载")

    file_name = str(doc.get("file_name") or "").strip() or (
        file_path.rsplit("/", 1)[-1] if "/" in file_path else file_path
    ) or "attachment"

    url = f"https://api.telegram.org/file/bot{token}/{file_path}"
    async with httpx.AsyncClient(timeout=120) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        content = resp.content

    res = upload_file(sandbox_id, "", file_name, content)
    if not res.get("ok"):
        raise RuntimeError(f"写入 workspace 失败: {res.get('msg') or res}")
    return str(res["path"])


def build_webhook_url(public_base: str, channel_id: str, webhook_secret: str, provider: str = "telegram") -> str:
    base = (public_base or "").strip().rstrip("/")
    path = f"/hooks/channels/{provider}/{channel_id}/{webhook_secret}"
    return f"{base}{path}" if base else path


def _is_public_https(url: str) -> tuple[bool, str]:
    try:
        u = urlparse(url)
    except Exception:
        return False, "Webhook URL 无效"
    if u.scheme != "https":
        return False, "Webhook 须为 HTTPS（请配置 PUBLIC_BASE_URL 公网域名）"
    host = (u.hostname or "").lower()
    if not host or host in ("localhost", "127.0.0.1", "0.0.0.0", "::1"):
        return False, "Webhook 不能使用 localhost（请配置 PUBLIC_BASE_URL）"
    return True, ""


async def _bot_api(token: str, method: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    url = f"https://api.telegram.org/bot{token}/{method}"
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(url, json=payload or {})
        try:
            data = resp.json()
        except Exception:
            data = {"ok": False, "description": resp.text[:300]}
        if not isinstance(data, dict):
            return {"ok": False, "description": "invalid telegram response"}
        return data


async def _send_photo_bytes(
    token: str,
    chat_id: str,
    image_data: bytes,
    *,
    reply_message_id: Any = None,
) -> dict[str, Any]:
    url = f"https://api.telegram.org/bot{token}/sendPhoto"
    data: dict[str, str] = {"chat_id": str(chat_id)}
    if reply_message_id is not None:
        data["reply_parameters"] = json.dumps({
            "message_id": reply_message_id,
            "allow_sending_without_reply": True,
        })
    async with httpx.AsyncClient(timeout=120) as client:
        response = await client.post(
            url,
            data=data,
            files={"photo": ("report.png", image_data, "image/png")},
        )
    try:
        result = response.json()
    except Exception:
        result = {"ok": False, "description": response.text[:300]}
    return result if isinstance(result, dict) else {"ok": False, "description": "invalid telegram response"}


async def sync_telegram_registration(
    *,
    bot_token: str,
    mode: str,
    enabled: bool,
    webhook_url: str,
) -> dict[str, Any]:
    """Register or clear Telegram webhook according to channel mode. Does not raise."""
    token = (bot_token or "").strip()
    mode_norm = (mode or "polling").strip().lower()
    if mode_norm not in ("webhook", "polling"):
        mode_norm = "polling"

    if not token:
        return {"ok": False, "mode": mode_norm, "message": "未配置 bot_token"}

    # Polling or disabled: clear webhook so getUpdates can work / stop inbound
    if not enabled or mode_norm == "polling":
        try:
            data = await _bot_api(token, "deleteWebhook", {"drop_pending_updates": False})
        except Exception as e:
            logger.exception("telegram deleteWebhook failed")
            return {"ok": False, "mode": mode_norm, "message": f"deleteWebhook 失败: {e}"}
        if not data.get("ok"):
            desc = data.get("description") or data
            return {"ok": False, "mode": mode_norm, "message": f"deleteWebhook 失败: {desc}"}
        msg = "已清除 Webhook，使用 Long Polling" if enabled else "渠道已停用，已清除 Webhook"
        return {"ok": True, "mode": mode_norm, "message": msg}

    ok_url, reason = _is_public_https(webhook_url)
    if not ok_url:
        return {"ok": False, "mode": mode_norm, "message": reason, "url": webhook_url}

    try:
        data = await _bot_api(
            token,
            "setWebhook",
            {"url": webhook_url, "drop_pending_updates": True},
        )
    except Exception as e:
        logger.exception("telegram setWebhook failed")
        return {"ok": False, "mode": mode_norm, "message": f"setWebhook 失败: {e}", "url": webhook_url}
    if not data.get("ok"):
        desc = data.get("description") or data
        msg = f"setWebhook 失败: {desc}"
        desc_s = str(desc)
        if (
            "Failed to resolve host" in desc_s
            or "Name or service not known" in desc_s
            or "resolve host" in desc_s.lower()
        ):
            msg += (
                "。公网域名已失效（常见于 trycloudflare 临时隧道重启后旧域名不可解析）。"
                "请重新跑 scripts/feishu-cloudflared.sh、确认 apps/api/.env 的 PUBLIC_BASE_URL、"
                "重启 API 后再点「同步 Webhook」；或将渠道改为 polling。"
            )
        return {"ok": False, "mode": mode_norm, "message": msg, "url": webhook_url}
    return {
        "ok": True,
        "mode": mode_norm,
        "message": "已注册 Webhook",
        "url": webhook_url,
    }


async def resync_all_telegram_channels(public_base: str) -> None:
    """Re-apply webhook/polling registration using current PUBLIC_BASE_URL (startup)."""
    from app.database import SessionLocal
    from app.models import ImChannel

    db = SessionLocal()
    try:
        rows = (
            db.query(ImChannel)
            .filter(ImChannel.provider == "telegram", ImChannel.enabled == True)  # noqa: E712
            .all()
        )
        for row in rows:
            cfg = row.get_config()
            url = build_webhook_url(public_base, row.id, row.webhook_secret or "", row.provider)
            result = await sync_telegram_registration(
                bot_token=str(cfg.get("bot_token") or ""),
                mode=str(cfg.get("mode") or "polling"),
                enabled=True,
                webhook_url=url,
            )
            msg = str(result.get("message") or "")
            if result.get("ok"):
                logger.info("telegram startup sync ok channel=%s: %s", row.id, msg)
                if row.last_error and (
                    row.last_error.startswith("getUpdates:")
                    or "Webhook" in row.last_error
                    or "setWebhook" in row.last_error
                ):
                    row.last_error = ""
            else:
                logger.warning("telegram startup sync failed channel=%s: %s", row.id, msg)
                row.last_error = msg[:2000]
            db.commit()
    except Exception:
        logger.exception("telegram startup resync failed")
        db.rollback()
    finally:
        db.close()


@register_adapter
class TelegramAdapter(ChannelAdapter):
    provider = "telegram"

    async def _bot_username(self) -> str:
        configured = str(self.config.get("bot_username") or "").strip().lstrip("@")
        if configured:
            return configured
        token = str(self.config.get("bot_token") or "").strip()
        if not token:
            return ""
        cached = _bot_username_cache.get(token)
        if cached:
            return cached
        try:
            data = await _bot_api(token, "getMe")
        except Exception:
            logger.exception("telegram getMe failed channel=%s", self.channel_id)
            return ""
        result = data.get("result") if isinstance(data, dict) else None
        username = str((result or {}).get("username") or "").strip().lstrip("@")
        if username:
            _bot_username_cache[token] = username
        return username

    @staticmethod
    def _entity_text(text: str, entity: dict[str, Any]) -> str:
        """Telegram entity offsets are UTF-16 code units, not Python indexes."""
        try:
            start = max(0, int(entity.get("offset") or 0)) * 2
            end = start + max(0, int(entity.get("length") or 0)) * 2
        except (TypeError, ValueError):
            return ""
        raw = text.encode("utf-16-le")
        return raw[start:end].decode("utf-16-le", errors="ignore")

    @staticmethod
    def _without_entities(text: str, entities: list[dict[str, Any]]) -> str:
        raw = text.encode("utf-16-le")
        spans: list[tuple[int, int]] = []
        for entity in entities:
            try:
                start = max(0, int(entity.get("offset") or 0)) * 2
                end = start + max(0, int(entity.get("length") or 0)) * 2
            except (TypeError, ValueError):
                continue
            spans.append((start, min(end, len(raw))))
        for start, end in sorted(spans, reverse=True):
            raw = raw[:start] + raw[end:]
        return raw.decode("utf-16-le", errors="ignore").strip()

    async def _group_task_text(self, message: dict[str, Any], text: str) -> str:
        bot_username = await self._bot_username()
        if not bot_username:
            return ""
        entities = message.get("entities") or message.get("caption_entities") or []
        if not isinstance(entities, list):
            return ""
        addressed: list[dict[str, Any]] = []
        expected = f"@{bot_username}".casefold()
        for entity in entities:
            if not isinstance(entity, dict):
                continue
            entity_type = str(entity.get("type") or "")
            value = self._entity_text(text, entity)
            if entity_type == "mention" and value.casefold() == expected:
                addressed.append(entity)
            elif entity_type == "bot_command" and "@" in value:
                target = value.rsplit("@", 1)[-1].casefold()
                if target == bot_username.casefold():
                    addressed.append(entity)
        if not addressed:
            return ""
        return self._without_entities(text, addressed)

    def _allowed(self, chat_id: str) -> bool:
        raw = self.config.get("allowed_chat_ids") or ""
        if isinstance(raw, list):
            allow = [str(x).strip() for x in raw if str(x).strip()]
        else:
            allow = [x.strip() for x in str(raw).split(",") if x.strip()]
        if not allow:
            return True
        return str(chat_id) in allow

    async def handle_webhook(self, *, method: str, headers: dict[str, str], query: dict[str, str], body: bytes) -> WebhookResult:
        try:
            data = json.loads(body.decode() or "{}")
        except Exception:
            return WebhookResult(status_code=400, body={"ok": False}, skip_agent=True)

        message = data.get("message") or data.get("edited_message") or {}
        text = (message.get("text") or message.get("caption") or "").strip()
        document = message.get("document")
        chat = message.get("chat") or {}
        chat_id = str(chat.get("id") or "")
        user = message.get("from") or {}
        user_id = str(user.get("id") or "")
        sender_username = str(user.get("username") or "").strip().lstrip("@")
        sender_display_name = " ".join(
            str(user.get(key) or "").strip()
            for key in ("first_name", "last_name")
            if str(user.get(key) or "").strip()
        )
        msg_id = str(message.get("message_id") or data.get("update_id") or f"tg-{int(time.time()*1000)}")
        chat_type = "group" if chat.get("type") in ("group", "supergroup") else "p2p"

        if not chat_id:
            return WebhookResult(body={"ok": True}, skip_agent=True)
        # A pure document (no text/caption) must still trigger the agent (R1);
        # photo/voice/video/audio carry no `document` key, so they keep skipping.
        if not text and not isinstance(document, dict):
            return WebhookResult(body={"ok": True}, skip_agent=True)
        if not self._allowed(chat_id):
            return WebhookResult(body={"ok": True, "ignored": "chat not allowed"}, skip_agent=True)

        if chat_type == "group":
            text = await self._group_task_text(message, text)
            if not text:
                return WebhookResult(body={"ok": True, "ignored": "bot not mentioned"}, skip_agent=True)
        elif text.startswith("/"):
            parts = text.split(maxsplit=1)
            text = parts[1] if len(parts) > 1 else text

        return WebhookResult(
            body={"ok": True},
            inbound=InboundMessage(
                msg_id=msg_id,
                chat_id=chat_id,
                user_id=user_id,
                sender_username=sender_username,
                sender_display_name=sender_display_name,
                text=text,
                chat_type=chat_type,
                raw=data,
            ),
        )

    async def send_text(self, chat_id: str, text: str, *, reply_to: dict | None = None) -> None:
        token = self.config.get("bot_token") or ""
        if not token:
            raise RuntimeError("Telegram 未配置 bot_token")
        message = (reply_to or {}).get("message") or (reply_to or {}).get("edited_message") or reply_to or {}
        reply_message_id = message.get("message_id") if isinstance(message, dict) else None

        # Primary: Bot API sendRichMessage with standard Markdown (tables stay Markdown).
        rich_error: Any = None
        rich_chunks = list(iter_rich_markdown_chunks(text))
        for index, md_chunk in enumerate(rich_chunks):
            payload: dict[str, Any] = {
                "chat_id": chat_id,
                "rich_message": {"markdown": md_chunk},
            }
            if index == 0 and reply_message_id is not None:
                payload["reply_parameters"] = {
                    "message_id": reply_message_id,
                    "allow_sending_without_reply": True,
                }
            data = await _bot_api(str(token), "sendRichMessage", payload)
            if not data.get("ok"):
                rich_error = data
                logger.warning(
                    "telegram sendRichMessage failed channel=%s chat=%s detail=%s",
                    self.channel_id,
                    chat_id,
                    data,
                )
                if index > 0:
                    # Partial delivery already happened; do not re-send via HTML.
                    raise RuntimeError(f"Telegram sendRichMessage 部分失败: {data}")
                break
        else:
            return

        # Fallback only when the first sendRichMessage fails (not the default path).
        logger.warning(
            "telegram falling back to sendMessage HTML channel=%s chat=%s rich_error=%s",
            self.channel_id,
            chat_id,
            rich_error,
        )
        for index, chunk in enumerate(telegram_html_chunks(text, 3500)):
            payload = {
                "chat_id": chat_id,
                "text": chunk,
                "parse_mode": "HTML",
            }
            if index == 0 and reply_message_id is not None:
                payload["reply_parameters"] = {
                    "message_id": reply_message_id,
                    "allow_sending_without_reply": True,
                }
            data = await _bot_api(str(token), "sendMessage", payload)
            if not data.get("ok"):
                raise RuntimeError(f"Telegram 发送失败: rich={rich_error}; html={data}")

    async def send_document(
        self,
        chat_id: str,
        file_path: str,
        *,
        caption: str | None = None,
    ) -> None:
        from pathlib import Path

        token = self.config.get("bot_token") or ""
        if not token:
            raise RuntimeError("Telegram 未配置 bot_token")
        path = Path(file_path)
        if not path.is_file():
            raise RuntimeError(f"文件不存在: {file_path}")
        max_bytes = 45 * 1024 * 1024
        size = path.stat().st_size
        if size > max_bytes:
            raise RuntimeError(f"文件过大（{size} bytes），Telegram 单文件上限约 45MB")
        url = f"https://api.telegram.org/bot{token}/sendDocument"
        data: dict[str, Any] = {"chat_id": str(chat_id)}
        if caption:
            data["caption"] = str(caption)[:1024]
        async with httpx.AsyncClient(timeout=120) as client:
            with path.open("rb") as f:
                resp = await client.post(
                    url,
                    data=data,
                    files={"document": (path.name, f)},
                )
            result = resp.json()
            if not result.get("ok"):
                raise RuntimeError(f"Telegram 发送文件失败: {result}")
