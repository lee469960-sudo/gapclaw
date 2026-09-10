"""Optional Telegram long-polling for channels without public webhook."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass

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
_POLL_RETRY_DELAY_SEC = 1.0


@dataclass
class _PollFailureStreak:
    failure_class: str
    exception_type: str
    count: int = 0
    first_seen: float = 0.0
    last_seen: float = 0.0


_poll_failure_streaks: dict[str, _PollFailureStreak] = {}


def _poll_failure_class(exc: BaseException) -> str:
    if isinstance(exc, httpx.TimeoutException):
        return "timeout"
    if isinstance(exc, httpx.TransportError):
        return "network_connectivity"
    return "polling_error"


def _record_poll_failure(
    channel_id: str,
    exc: BaseException,
    *,
    retry_delay_s: float = _POLL_RETRY_DELAY_SEC,
    exc_info: bool = True,
) -> None:
    now = time.monotonic()
    failure_class = _poll_failure_class(exc)
    exception_type = type(exc).__name__
    prev = _poll_failure_streaks.get(channel_id)
    changed = prev is None or prev.failure_class != failure_class
    if changed:
        streak = _PollFailureStreak(
            failure_class=failure_class,
            exception_type=exception_type,
            count=1,
            first_seen=now,
            last_seen=now,
        )
        _poll_failure_streaks[channel_id] = streak
        logger.error(
            "telegram poll failed component=telegram class=%s channel=%s "
            "exception=%s repeat_count=%d retry_delay_s=%.1f",
            failure_class,
            channel_id,
            exception_type,
            streak.count,
            retry_delay_s,
            exc_info=exc_info,
        )
        return

    prev.count += 1
    prev.last_seen = now
    logger.warning(
        "telegram poll failed aggregate component=telegram class=%s channel=%s "
        "exception=%s repeat_count=%d retry_delay_s=%.1f duration_s=%.1f",
        prev.failure_class,
        channel_id,
        prev.exception_type,
        prev.count,
        retry_delay_s,
        prev.last_seen - prev.first_seen,
    )


def _record_poll_success(channel_id: str) -> None:
    streak = _poll_failure_streaks.pop(channel_id, None)
    if not streak:
        return
    logger.info(
        "telegram poll recovered component=telegram channel=%s "
        "previous_class=%s exception=%s repeat_count=%d duration_s=%.1f",
        channel_id,
        streak.failure_class,
        streak.exception_type,
        streak.count,
        time.monotonic() - streak.first_seen,
    )


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


async def _poll_once(channel: ImChannel) -> bool:
    cfg = channel.get_config()
    # Default must match sync/UI (polling), not webhook — otherwise missing mode = dead channel
    if (cfg.get("mode") or "polling") != "polling":
        return False
    token = cfg.get("bot_token") or ""
    if not token:
        return False
    offset = _offsets.get(channel.id, 0)
    url = f"https://api.telegram.org/bot{token}/getUpdates"
    async with httpx.AsyncClient(timeout=35) as client:
        resp = await client.get(url, params={"timeout": 25, "offset": offset})
        data = resp.json()
    if not data.get("ok"):
        desc = data.get("description") or str(data)
        _record_poll_error(channel.id, str(desc))
        _record_poll_failure(
            channel.id,
            RuntimeError("telegram getUpdates returned ok=false"),
            exc_info=False,
        )
        return False
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
    return True


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
                            if await _poll_once(fresh):
                                _record_poll_success(ch.id)
                    finally:
                        db2.close()
                except Exception as exc:
                    _record_poll_failure(ch.id, exc)
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
