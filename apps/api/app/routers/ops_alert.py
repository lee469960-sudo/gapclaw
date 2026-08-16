"""Ops alert hook for cloudflared ERR → IM + im_event_logs."""

from __future__ import annotations

import logging
import os

from fastapi import APIRouter, BackgroundTasks, Header
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import ImChannel
from app.schemas import fail, ok
from app.services.channels.base import create_adapter
from app.services.channels.runtime import log_event

logger = logging.getLogger(__name__)
router = APIRouter(tags=["ops"])


class OpsAlertBody(BaseModel):
    source: str = "cloudflared"
    message: str = ""
    detail: str = ""
    level: str = "error"


def _ops_secret() -> str:
    return (os.environ.get("OPS_ALERT_SECRET") or "").strip()


def _ops_channel_id() -> str:
    return (os.environ.get("OPS_ALERT_CHANNEL_ID") or "").strip()


async def _deliver_ops_alert(body: OpsAlertBody) -> None:
    db: Session = SessionLocal()
    try:
        channel_id = _ops_channel_id() or "ops"
        log_event(
            db,
            channel_id,
            f"[ops/{body.source}] {body.message}"[:2000],
            level=(body.level or "error")[:16],
            detail=(body.detail or "")[:8000],
        )
        db.commit()
        cid = _ops_channel_id()
        if not cid:
            return
        channel = db.query(ImChannel).filter(ImChannel.id == cid).first()
        if not channel or not channel.enabled:
            return
        cfg = channel.get_config()
        chat_id = str(cfg.get("ops_chat_id") or cfg.get("default_chat_id") or "").strip()
        if not chat_id:
            return
        adapter = create_adapter(channel.provider, channel.id, cfg)
        text = f"【OPS告警】[{body.source}] {body.message}"
        if body.detail:
            text = f"{text}\n{body.detail[:1500]}"
        await adapter.send_text(chat_id, text)
        log_event(db, channel.id, "OPS 告警已推送 IM", "info", body.message[:500])
        db.commit()
    except Exception:
        logger.exception("ops alert deliver failed")
        try:
            db.rollback()
        except Exception:
            pass
    finally:
        db.close()


@router.post("/hooks/ops/alert")
async def ops_alert(
    body: OpsAlertBody,
    background_tasks: BackgroundTasks,
    x_ops_secret: str | None = Header(default=None, alias="X-Ops-Secret"),
):
    secret = _ops_secret()
    if not secret:
        return fail("OPS_ALERT_SECRET 未配置", code=503)
    provided = (x_ops_secret or "").strip()
    if provided != secret:
        return fail("unauthorized", code=401)
    if not (body.message or "").strip():
        return fail("message required")
    background_tasks.add_task(_deliver_ops_alert, body)
    return ok({"queued": True})
