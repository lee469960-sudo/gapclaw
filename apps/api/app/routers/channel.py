"""Admin CRUD for IM channels."""

from __future__ import annotations

import json
from fastapi import APIRouter, BackgroundTasks, Depends, Query, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.deps import can_access_resource, get_session_user
from app.models import User, Agent, ImChannel, ImEventLog
from app.schemas import ok, fail
from app.security import new_id, new_token, now_str, is_masked_secret
from app.services import channels as channel_pkg  # noqa: F401 — register adapters
from app.services.channels.base import list_providers, create_adapter
from app.services.channels.runtime import process_inbound_bg, log_event, ensure_im_web_session
from app.services.channels.base import InboundMessage
from app.services.channels.telegram import build_webhook_url, sync_telegram_registration

router = APIRouter(prefix="/pages/page_channel.cgi", tags=["channel"])

SECRET_KEYS = {
    "app_secret", "secret", "bot_token", "access_token", "encrypt_key",
    "encoding_aes_key", "client_secret", "token", "verification_token",
}


class ChannelBody(BaseModel):
    action: str | None = None
    id: str | None = None
    name: str | None = None
    provider: str | None = None
    enabled: bool | None = None
    agent_id: str | None = None
    config: dict | None = None
    text: str | None = None
    chat_id: str | None = None
    limit: int | None = None
    scope: str | None = None
    visibility: str | None = None
    allowed_users: list[str] | None = None


def _channel_dict(db: Session, row: ImChannel) -> dict:
    d = row.to_dict(mask_secrets=True)
    d["web_session_id"] = ""
    if row.agent_id:
        agent = db.query(Agent).filter(Agent.id == row.agent_id).first()
        if agent:
            try:
                d["web_session_id"] = ensure_im_web_session(db, agent, row.provider)
            except Exception:
                d["web_session_id"] = ""
    return d


def _merge_config(existing: dict, incoming: dict | None) -> dict:
    cfg = dict(existing or {})
    if not incoming:
        return cfg
    for k, v in incoming.items():
        if v is None:
            continue
        if k in SECRET_KEYS and isinstance(v, str) and is_masked_secret(v):
            continue
        cfg[k] = v
    return cfg


def _filter_channels(items: list[ImChannel], user: User, scope: str = "all") -> list[ImChannel]:
    visible = [
        c for c in items
        if can_access_resource(user, c.visibility or "private", c.allowed_users or "[]", c.creator)
    ]
    if scope == "mine":
        return [c for c in visible if c.creator == user.username]
    return visible


def _get_channel(db: Session, channel_id: str | None, user: User) -> ImChannel | None:
    if not channel_id:
        return None
    row = db.query(ImChannel).filter(ImChannel.id == channel_id).first()
    if not row:
        return None
    if not can_access_resource(user, row.visibility or "private", row.allowed_users or "[]", row.creator):
        return None
    return row


async def _maybe_sync_telegram(row: ImChannel, public_base: str) -> dict | None:
    if row.provider != "telegram":
        return None
    cfg = row.get_config()
    url = build_webhook_url(public_base, row.id, row.webhook_secret or "", row.provider)
    return await sync_telegram_registration(
        bot_token=str(cfg.get("bot_token") or ""),
        mode=str(cfg.get("mode") or "polling"),
        enabled=bool(row.enabled),
        webhook_url=url,
    )


@router.get("")
@router.post("")
async def channel_handler(
    request: Request,
    background_tasks: BackgroundTasks,
    body: ChannelBody | None = None,
    action: str | None = Query(None),
    scope: str = Query("all"),
    user: User = Depends(get_session_user),
    db: Session = Depends(get_db),
):
    if request.method == "GET":
        act = action or "list"
        body = body or ChannelBody()
        list_scope = scope or "all"
    else:
        act = (body.action if body else None) or "list"
        list_scope = (body.scope if body else None) or "all"

    public_base = get_settings().public_base_url_normalized

    if act == "providers":
        return ok({
            "providers": list_providers(),
            "labels": {
                "mock": "Mock（联调）",
                "feishu": "飞书",
                "dingtalk": "钉钉",
                "telegram": "Telegram",
                "qq": "QQ / OneBot",
                "wecom": "企业微信",
            },
            "public_base_url": public_base,
        })

    if act == "list":
        rows = db.query(ImChannel).order_by(ImChannel.modified_at.desc()).all()
        rows = _filter_channels(rows, user, list_scope)
        return ok({
            "channels": [_channel_dict(db, r) for r in rows],
            "public_base_url": public_base,
        })

    if act == "get":
        row = _get_channel(db, body.id if body else None, user)
        if not row:
            return fail("不存在或无权限")
        return ok(_channel_dict(db, row))

    if act in ("create", "update", "save"):
        if body.id:
            row = _get_channel(db, body.id, user)
            if not row:
                return fail("不存在或无权限")
        else:
            provider = (body.provider or "").strip()
            if provider not in list_providers():
                return fail("未知渠道类型")
            row = ImChannel(
                id=new_id(),
                provider=provider,
                webhook_secret=new_token()[:24],
                creator=user.username,
                visibility="private",
                allowed_users="[]",
            )
            db.add(row)

        if body.name is not None:
            row.name = body.name.strip() or row.name or "未命名渠道"
        if body.provider and not body.id:
            row.provider = body.provider
        if body.enabled is not None:
            row.enabled = bool(body.enabled)
        if body.agent_id is not None:
            if body.agent_id:
                ag = db.query(Agent).filter(Agent.id == body.agent_id).first()
                if not ag:
                    return fail("Agent 不存在")
                if not can_access_resource(user, ag.visibility, ag.allowed_users, ag.creator):
                    return fail("无权绑定该 Agent")
            row.agent_id = body.agent_id or ""
        if body.visibility is not None:
            row.visibility = body.visibility if body.visibility in ("public", "private") else "private"
        if body.allowed_users is not None:
            row.allowed_users = json.dumps(body.allowed_users or [])
        if body.config is not None:
            row.set_config(_merge_config(row.get_config(), body.config))
        if not row.webhook_secret:
            row.webhook_secret = new_token()[:24]
        row.modified_at = now_str()
        db.commit()
        db.refresh(row)
        payload = _channel_dict(db, row)
        tg_sync = await _maybe_sync_telegram(row, public_base)
        if tg_sync is not None:
            payload["telegram_sync"] = tg_sync
            try:
                log_event(
                    db,
                    row.id,
                    "Telegram Webhook 同步" if tg_sync.get("ok") else "Telegram Webhook 同步失败",
                    "info" if tg_sync.get("ok") else "warn",
                    tg_sync.get("message") or "",
                )
                db.commit()
            except Exception:
                db.rollback()
        return ok(payload, "保存成功")

    if act == "delete":
        row = _get_channel(db, body.id if body else None, user)
        if not row:
            return fail("不存在或无权限")
        db.query(ImEventLog).filter(ImEventLog.channel_id == row.id).delete()
        db.delete(row)
        db.commit()
        return ok(None, "删除成功")

    if act == "rotate_webhook":
        row = _get_channel(db, body.id if body else None, user)
        if not row:
            return fail("不存在或无权限")
        row.webhook_secret = new_token()[:24]
        row.modified_at = now_str()
        db.commit()
        db.refresh(row)
        payload = _channel_dict(db, row)
        tg_sync = await _maybe_sync_telegram(row, public_base)
        if tg_sync is not None:
            payload["telegram_sync"] = tg_sync
        return ok(payload, "已轮换 webhook")

    if act == "sync_telegram":
        row = _get_channel(db, body.id if body else None, user)
        if not row:
            return fail("不存在或无权限")
        if row.provider != "telegram":
            return fail("仅 Telegram 渠道支持同步")
        tg_sync = await _maybe_sync_telegram(row, public_base) or {
            "ok": False,
            "message": "同步失败",
        }
        try:
            log_event(
                db,
                row.id,
                "Telegram Webhook 同步" if tg_sync.get("ok") else "Telegram Webhook 同步失败",
                "info" if tg_sync.get("ok") else "warn",
                tg_sync.get("message") or "",
            )
            db.commit()
        except Exception:
            db.rollback()
        payload = _channel_dict(db, row)
        payload["telegram_sync"] = tg_sync
        return ok(payload, tg_sync.get("message") or "已同步")

    if act == "logs":
        row = _get_channel(db, body.id if body else None, user)
        if not row:
            return fail("不存在或无权限")
        q = db.query(ImEventLog).filter(ImEventLog.channel_id == row.id).order_by(ImEventLog.id.desc())
        rows = q.limit(min((body.limit if body else None) or 50, 200)).all()
        return ok([
            {
                "id": r.id,
                "level": r.level,
                "message": r.message,
                "detail": r.detail,
                "created_at": r.created_at,
            }
            for r in rows
        ])

    if act == "test_mock":
        row = _get_channel(db, body.id if body else None, user)
        if not row:
            return fail("不存在或无权限")
        if row.provider != "mock":
            return fail("仅 Mock 渠道支持一键测试")
        if not row.agent_id:
            return fail("请先绑定 Agent")
        if not row.enabled:
            return fail("渠道未启用")
        inbound = InboundMessage(
            msg_id=f"test-{new_id()}",
            chat_id=(body.chat_id or "mock-chat").strip(),
            user_id="tester",
            text=(body.text or "你好，这是一条 Mock 渠道测试消息").strip(),
            chat_type="p2p",
            raw={},
        )
        log_event(db, row.id, "手动触发 Mock 测试", "info", inbound.text)
        db.commit()
        background_tasks.add_task(process_inbound_bg, row.id, inbound)
        return ok({"status": "started"}, "已提交，稍后查看日志 / Mock outbox")

    if act == "mock_outbox":
        row = _get_channel(db, body.id if body else None, user)
        if not row:
            return fail("不存在或无权限")
        from app.services.channels.mock import MockAdapter
        items = [x for x in MockAdapter.outbox if x.get("channel_id") == row.id]
        return ok(items[-50:])

    return fail("未知操作")
