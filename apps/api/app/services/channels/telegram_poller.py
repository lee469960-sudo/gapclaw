"""Optional Telegram long-polling for channels without public webhook."""

from __future__ import annotations

import asyncio
import json
import logging
import time

import httpx

from app.database import SessionLocal
from app.models import ImChannel
from app.security import now_str
from app.services.channels.runtime import process_inbound
from app.services.channels.telegram import TelegramAdapter

logger = logging.getLogger(__name__)

_task: asyncio.Task | None = None
_offsets: dict[str, int] = {}
_poll_error_at: dict[str, float] = {}
_POLL_ERROR_THROTTLE_SEC = 60.0


def _record_poll_error(channel_id: str, message: str) -> None:
    """Persist getUpdates failures to channel.last_error, throttled."""
    now = time.monotonic()
    last = _poll_error_at.get(channel_id, 0.0)
    if now - last < _POLL_ERROR_THROTTLE_SEC:
        return
    _poll_error_at[channel_id] = now
    logger.warning("telegram getUpdates failed channel=%s: %s", channel_id, message)
    db = SessionLocal()
    try:
        row = db.query(ImChannel).filter(ImChannel.id == channel_id).first()
        if not row:
            return
        row.last_error = f"getUpdates: {message}"[:2000]
        row.last_event_at = now_str()
        db.commit()
    except Exception:
        logger.exception("failed to persist telegram poll error channel=%s", channel_id)
        db.rollback()
    finally:
        db.close()


async def _poll_once(channel: ImChannel) -> None:
    cfg = channel.get_config()
    # Default must match sync/UI (polling), not webhook — otherwise missing mode = dead channel
    if (cfg.get("mode") or "polling") != "polling":
        return
    token = cfg.get("bot_token") or ""
    if not token:
        return
    offset = _offsets.get(channel.id, 0)
    url = f"https://api.telegram.org/bot{token}/getUpdates"
    async with httpx.AsyncClient(timeout=35) as client:
        resp = await client.get(url, params={"timeout": 25, "offset": offset})
        data = resp.json()
    if not data.get("ok"):
        desc = data.get("description") or str(data)
        _record_poll_error(channel.id, str(desc))
        return
    adapter = TelegramAdapter(channel.id, cfg)
    for upd in data.get("result") or []:
        _offsets[channel.id] = int(upd["update_id"]) + 1
        parsed = await adapter.handle_webhook(
            method="POST",
            headers={},
            query={},
            body=json.dumps(upd, ensure_ascii=False).encode("utf-8"),
        )
        if parsed.inbound and not parsed.skip_agent:
            await process_inbound(channel.id, parsed.inbound)


async def _loop() -> None:
    while True:
        try:
            db = SessionLocal()
            try:
                rows = (
                    db.query(ImChannel)
                    .filter(ImChannel.provider == "telegram", ImChannel.enabled == True)  # noqa: E712
                    .all()
                )
                channels = [(r.id, r) for r in rows]
            finally:
                db.close()
            for _, ch in channels:
                try:
                    # re-load detached? use config from snapshot
                    db2 = SessionLocal()
                    try:
                        fresh = db2.query(ImChannel).filter(ImChannel.id == ch.id).first()
                        if fresh:
                            await _poll_once(fresh)
                    finally:
                        db2.close()
                except Exception:
                    logger.exception("telegram poll failed for %s", ch.id)
        except Exception:
            logger.exception("telegram poller loop error")
        await asyncio.sleep(1)


def start_telegram_poller() -> None:
    global _task
    if _task and not _task.done():
        return
    _task = asyncio.create_task(_loop())
    logger.info("telegram poller started")


def stop_telegram_poller() -> None:
    global _task
    if _task and not _task.done():
        _task.cancel()
    _task = None
