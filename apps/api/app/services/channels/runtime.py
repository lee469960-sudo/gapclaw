"""Channel runtime: dedup, session map, agent invoke, reply, rate limit, logs."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import defaultdict, deque

from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import Agent, ChatMessage, ImChannel, ImDedup, ImEventLog, ImSession
from app.security import new_id, now_str
from app.services.channels.base import InboundMessage, WebhookResult, create_adapter
from app.services.react_engine import format_im_completion_reply
from app.services.agent_runtime import run_agent
from app.services.workplace import download_path

logger = logging.getLogger(__name__)

# Simple per-channel rate limit: max N messages / window
_RATE_LIMIT = 20
_RATE_WINDOW = 60.0
_rate_buckets: dict[str, deque] = defaultdict(deque)
_running_keys: set[str] = set()


def _rate_ok(channel_id: str) -> bool:
    now = time.time()
    q = _rate_buckets[channel_id]
    while q and now - q[0] > _RATE_WINDOW:
        q.popleft()
    if len(q) >= _RATE_LIMIT:
        return False
    q.append(now)
    return True


def log_event(db: Session, channel_id: str, message: str, level: str = "info", detail: str = "") -> None:
    db.add(ImEventLog(
        channel_id=channel_id,
        level=level,
        message=message[:2000],
        detail=(detail or "")[:8000],
        created_at=now_str(),
    ))
    # keep last 200 per channel
    rows = (
        db.query(ImEventLog)
        .filter(ImEventLog.channel_id == channel_id)
        .order_by(ImEventLog.id.desc())
        .offset(200)
        .all()
    )
    for r in rows:
        db.delete(r)


async def push_channel_documents(
    db: Session,
    channel: ImChannel,
    chat_id: str,
    sandbox_id: str,
    paths: list[str],
) -> list[str]:
    """Send workplace files via channel adapter.send_document. Returns sent rel paths."""
    if not paths or not sandbox_id or not channel:
        return []
    adapter = create_adapter(channel.provider, channel.id, channel.get_config())
    sent: list[str] = []
    for rel in paths:
        fp = download_path(sandbox_id, rel)
        if not fp:
            log_event(db, channel.id, f"附件跳过（不存在）: {rel}", "warn")
            continue
        try:
            await adapter.send_document(chat_id, str(fp), caption=fp.name)
            log_event(db, channel.id, f"已推送附件: {rel}", "info")
            sent.append(rel)
        except Exception as e:
            logger.warning("IM send_document failed channel=%s path=%s: %s", channel.id, rel, e)
            log_event(db, channel.id, f"附件推送失败: {rel}", "warn", str(e))
    return sent


# Backward-compatible name
push_telegram_documents = push_channel_documents


def _last_assistant_saved_paths(
    db: Session,
    agent_id: str,
    session_id: str,
) -> list[str]:
    last = (
        db.query(ChatMessage)
        .filter(
            ChatMessage.agent_id == agent_id,
            ChatMessage.session_id == session_id,
            ChatMessage.role == "assistant",
        )
        .order_by(ChatMessage.id.desc())
        .first()
    )
    if not last or not last.meta:
        return []
    try:
        return list(json.loads(last.meta).get("saved_paths") or [])
    except Exception:
        return []


def _address_group_sender(provider: str, inbound: InboundMessage, text: str) -> str:
    """Address the initiator on group-channel completion without changing reply content."""
    body = (text or "").strip()
    if provider != "telegram" or inbound.chat_type != "group":
        return body
    if inbound.sender_username:
        recipient = f"@{inbound.sender_username}"
    elif inbound.sender_display_name:
        recipient = inbound.sender_display_name
    elif inbound.user_id:
        recipient = f"用户 {inbound.user_id}"
    else:
        return body
    return f"{recipient}\n{body}" if body else recipient


async def _push_saved_documents(
    db: Session,
    adapter,
    channel: ImChannel,
    agent: Agent,
    session_id: str,
    chat_id: str,
    *,
    paths: list[str] | None = None,
) -> None:
    """Push the deliverable paths selected by the completion formatter."""
    del adapter  # use create_adapter inside push_channel_documents
    if paths is None:
        paths = _last_assistant_saved_paths(db, agent.id, session_id)
    if not paths or not agent.sandbox_id:
        return
    await push_telegram_documents(db, channel, chat_id, agent.sandbox_id, paths)


_PROVIDER_WEB_SESSION = {
    "feishu": "飞书",
    "dingtalk": "钉钉",
    "telegram": "Telegram",
    "qq": "QQ",
    "wecom": "企业微信",
    "mock": "Mock渠道",
}


def ensure_im_web_session(db: Session, agent: Agent, provider: str) -> str:
    """One shared Agent session per IM provider so Web UI can open「飞书」and see channel history."""
    name = _PROVIDER_WEB_SESSION.get(provider, f"IM:{provider}")
    source = f"im:{provider}"
    sessions = json.loads(agent.session_list or "[]")
    for s in sessions:
        if s.get("source") == source or s.get("name") == name:
            changed = False
            if s.get("name") != name:
                s["name"] = name
                changed = True
            if s.get("source") != source:
                s["source"] = source
                changed = True
            if changed:
                agent.session_list = json.dumps(sessions, ensure_ascii=False)
                db.commit()
            return str(s["session_id"])

    # Migrate legacy per-chat sessions IM:feishu:oc_xxx → reuse busiest as「飞书」
    from app.models import ChatMessage
    legacy = [s for s in sessions if str(s.get("name") or "").startswith(f"IM:{provider}")]
    if legacy:
        def _msg_count(sid: str) -> int:
            return db.query(ChatMessage).filter(
                ChatMessage.agent_id == agent.id,
                ChatMessage.session_id == sid,
            ).count()

        best = max(legacy, key=lambda s: _msg_count(str(s.get("session_id") or "")))
        best["name"] = name
        best["source"] = source
        agent.session_list = json.dumps(sessions, ensure_ascii=False)
        db.commit()
        return str(best["session_id"])

    sid = new_id()
    sessions.append({"name": name, "session_id": sid, "source": source})
    agent.session_list = json.dumps(sessions, ensure_ascii=False)
    db.commit()
    return sid


def ensure_agent_session(db: Session, agent: Agent, external_key: str, label: str) -> str:
    sessions = json.loads(agent.session_list or "[]")
    for s in sessions:
        if s.get("session_id") == external_key:
            return external_key
    sessions.append({"name": label[:64] or "IM", "session_id": external_key})
    agent.session_list = json.dumps(sessions, ensure_ascii=False)
    db.commit()
    return external_key


def get_or_create_im_session(
    db: Session,
    channel: ImChannel,
    inbound: InboundMessage,
) -> ImSession:
    if not (channel.agent_id or "").strip():
        raise RuntimeError("请先绑定 Agent：在消息渠道中选择 Agent 后才能对话")

    agent = db.query(Agent).filter(Agent.id == channel.agent_id).first()
    if not agent:
        raise RuntimeError("渠道未绑定有效 Agent")

    # Shared web-visible session (e.g. 「飞书」) for this agent + provider
    web_sid = ensure_im_web_session(db, agent, channel.provider)

    row = (
        db.query(ImSession)
        .filter(
            ImSession.channel_id == channel.id,
            ImSession.external_chat_id == inbound.chat_id,
        )
        .first()
    )

    if row:
        row.external_user_id = inbound.user_id or row.external_user_id
        row.updated_at = now_str()
        if row.agent_id != agent.id or row.agent_session_id != web_sid:
            row.agent_id = agent.id
            row.agent_session_id = web_sid
        db.commit()
        db.refresh(row)
        return row

    row = ImSession(
        channel_id=channel.id,
        external_chat_id=inbound.chat_id,
        external_user_id=inbound.user_id or "",
        agent_id=agent.id,
        agent_session_id=web_sid,
        updated_at=now_str(),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def try_dedup(db: Session, channel_id: str, msg_id: str) -> bool:
    """Return True if this is a NEW message (should process)."""
    if not msg_id:
        return True
    exists = (
        db.query(ImDedup)
        .filter(ImDedup.channel_id == channel_id, ImDedup.msg_id == msg_id)
        .first()
    )
    if exists:
        return False
    db.add(ImDedup(channel_id=channel_id, msg_id=msg_id, created_at=now_str()))
    db.commit()
    return True


async def process_inbound(channel_id: str, inbound: InboundMessage) -> None:
    db = SessionLocal()
    try:
        channel = db.query(ImChannel).filter(ImChannel.id == channel_id).first()
        if not channel or not channel.enabled:
            return
        if not try_dedup(db, channel.id, inbound.msg_id):
            log_event(db, channel.id, f"跳过重复消息 {inbound.msg_id}", "info")
            db.commit()
            return
        if not _rate_ok(channel.id):
            channel.last_error = "rate limited"
            log_event(db, channel.id, "触发限流，丢弃消息", "warn", inbound.text[:500])
            db.commit()
            return

        run_key = f"{channel.id}:{inbound.chat_id}"
        if run_key in _running_keys:
            log_event(db, channel.id, "同会话仍在处理中，跳过", "warn", inbound.msg_id)
            db.commit()
            return
        _running_keys.add(run_key)

        adapter = None
        try:
            if not (channel.agent_id or "").strip():
                raise RuntimeError("请先绑定 Agent：在消息渠道中选择 Agent 后才能对话")

            im_sess = get_or_create_im_session(db, channel, inbound)
            agent = db.query(Agent).filter(Agent.id == channel.agent_id).first()
            if not agent:
                raise RuntimeError("渠道未绑定有效 Agent")

            username = (
                inbound.sender_username
                or inbound.sender_display_name
                or channel.creator
                or "admin"
            )
            log_event(db, channel.id, f"收到消息 chat={inbound.chat_id} agent={agent.id}", "info", inbound.text[:1000])
            channel.last_event_at = now_str()
            channel.last_error = ""
            db.commit()

            adapter = create_adapter(channel.provider, channel.id, channel.get_config())
            # Best-effort ack so mobile user sees activity while Agent runs
            try:
                await adapter.send_text(
                    inbound.chat_id,
                    f"已收到，正在由 Agent「{agent.name}」处理…",
                    reply_to=inbound.raw,
                )
            except Exception as ack_err:
                logger.warning("IM ack failed channel=%s: %s", channel_id, ack_err)

            reply = await run_agent(
                db,
                agent,
                im_sess.agent_session_id,
                inbound.text,
                username,
                message_meta={
                    "source": f"im:{channel.provider}",
                    "channel_id": channel.id,
                    "chat_id": inbound.chat_id,
                    "chat_type": inbound.chat_type,
                    "user_id": inbound.user_id,
                    "sender_username": inbound.sender_username,
                    "sender_display_name": inbound.sender_display_name,
                },
            )

            saved = _last_assistant_saved_paths(db, agent.id, im_sess.agent_session_id)
            slim_text, push_paths = format_im_completion_reply(
                reply or "",
                saved_paths=saved,
                sandbox_id=agent.sandbox_id or "",
            )
            slim_text = _address_group_sender(channel.provider, inbound, slim_text)
            await adapter.send_text(
                inbound.chat_id, slim_text or "任务结束", reply_to=inbound.raw,
            )
            if push_paths:
                await _push_saved_documents(
                    db,
                    adapter,
                    channel,
                    agent,
                    im_sess.agent_session_id,
                    inbound.chat_id,
                    paths=push_paths,
                )
            log_event(db, channel.id, f"已回发 agent={agent.id}", "info", (slim_text or "")[:1000])
            channel.last_event_at = now_str()
            db.commit()
        except Exception as e:
            logger.exception("IM process failed channel=%s", channel_id)
            channel = db.query(ImChannel).filter(ImChannel.id == channel_id).first()
            if channel:
                channel.last_error = str(e)[:2000]
                log_event(db, channel.id, "处理失败", "error", str(e))
                db.commit()
            # Notify user on Telegram (and other IMs) instead of silent failure
            try:
                if not adapter and channel:
                    adapter = create_adapter(channel.provider, channel.id, channel.get_config())
                if adapter and inbound.chat_id:
                    err_text = str(e).strip() or "未知错误"
                    if len(err_text) > 500:
                        err_text = err_text[:500] + "…"
                    await adapter.send_text(
                        inbound.chat_id,
                        _address_group_sender(
                            channel.provider,
                            inbound,
                            f"处理失败：{err_text}",
                        ),
                        reply_to=inbound.raw,
                    )
            except Exception as send_err:
                logger.warning(
                    "IM error reply failed channel=%s: %s",
                    channel_id,
                    send_err,
                )
        finally:
            _running_keys.discard(run_key)
    finally:
        db.close()


def process_inbound_bg(channel_id: str, inbound: InboundMessage) -> None:
    asyncio.run(process_inbound(channel_id, inbound))


async def dispatch_webhook(
    db: Session,
    channel: ImChannel,
    *,
    method: str,
    headers: dict[str, str],
    query: dict[str, str],
    body: bytes,
) -> tuple[WebhookResult, InboundMessage | None]:
    adapter = create_adapter(channel.provider, channel.id, channel.get_config())
    result = await adapter.handle_webhook(method=method, headers=headers, query=query, body=body)
    channel.last_event_at = now_str()
    # Surface challenge / ignored callbacks in admin logs (no Agent run)
    if result.skip_agent or not result.inbound:
        detail = ""
        try:
            detail = (body or b"")[:500].decode("utf-8", errors="replace")
        except Exception:
            detail = ""
        if isinstance(result.body, dict) and result.body.get("challenge"):
            log_event(db, channel.id, "飞书 URL 校验通过（challenge）", "info")
        elif result.status_code >= 400:
            log_event(db, channel.id, f"Webhook 拒绝 status={result.status_code}", "error", str(result.body)[:500])
        elif channel.provider == "feishu" and b"encrypt" in (body or b""):
            log_event(db, channel.id, "收到加密事件但未配置解密，已忽略（请关闭 Encrypt Key）", "warn")
        elif channel.provider == "feishu" and result.outbound_hint:
            log_event(
                db,
                channel.id,
                "已发送渠道提示消息",
                "info",
            )
        elif channel.provider == "feishu" and method.upper() == "POST" and body:
            # Non-message callbacks — skip noisy read receipts in admin logs
            if b"im.message.message_read_v1" in (body or b""):
                pass
            elif b"bot_p2p_chat_entered_v1" in (body or b""):
                log_event(db, channel.id, "用户进入私聊", "info")
            elif b"im.message.receive_v1" not in (body or b"") and b"url_verification" not in (body or b""):
                log_event(db, channel.id, "收到飞书回调但非文本消息事件", "info", detail[:300])
    if result.inbound and not result.skip_agent:
        return result, result.inbound
    db.commit()
    return result, None


async def send_outbound_hint(channel_id: str, hint: dict) -> None:
    """Best-effort one-shot send (welcome etc.) without running Agent."""
    db = SessionLocal()
    try:
        channel = db.query(ImChannel).filter(ImChannel.id == channel_id).first()
        if not channel or not channel.enabled:
            return
        adapter = create_adapter(channel.provider, channel.id, channel.get_config())
        await adapter.send_text(
            str(hint.get("chat_id") or ""),
            str(hint.get("text") or ""),
            reply_to=hint.get("reply_to") if isinstance(hint.get("reply_to"), dict) else None,
        )
        log_event(db, channel.id, "已发送提示消息", "info", str(hint.get("text") or "")[:200])
        db.commit()
    except Exception as e:
        logger.warning("outbound_hint failed channel=%s: %s", channel_id, e)
        try:
            log_event(db, channel_id, "提示消息发送失败", "error", str(e)[:500])
            db.commit()
        except Exception:
            pass
    finally:
        db.close()


def send_outbound_hint_bg(channel_id: str, hint: dict) -> None:
    asyncio.run(send_outbound_hint(channel_id, hint))
