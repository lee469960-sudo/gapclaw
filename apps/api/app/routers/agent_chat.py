import json
import asyncio
import logging
from fastapi import APIRouter, Depends, Query, BackgroundTasks, UploadFile, File, Form, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db, SessionLocal
from app.deps import get_session_user
from app.models import User, Agent, ChatMessage, ChatNote, ChatSummary, AgentTick
from app.schemas import ok, fail
from app.security import new_id, now_str
from app.services.react_engine import stop_chat, hub, is_running, generate_session_summary
from app.services.agent_runtime import run_agent
from app.services import tick_scheduler

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/pages/page_agent_chat.cgi", tags=["agent-chat"])

_HISTORY_STEPS_LIMIT = 0  # 0 = return all steps (no display cap)


def _slim_meta_for_history(raw: str | None) -> dict:
    """Drop heavy steps/mcp_tables from list payload; keep counts for UI badge."""
    try:
        meta = json.loads(raw or "{}")
    except Exception:
        return {}
    if not isinstance(meta, dict):
        return {}
    steps = meta.get("steps") or []
    n_steps = len(steps) if isinstance(steps, list) else 0
    try:
        stored = int(meta.get("step_count") or 0)
    except (TypeError, ValueError):
        stored = 0
    # Prefer real step list length; never drop a persisted positive count to 0
    step_count = max(n_steps, stored)
    out: dict = {
        "step_count": step_count,
        "saved_paths": list(meta.get("saved_paths") or [])[:20],
    }
    for k in ("cte_attempts_used", "cte_attempt_limit", "export_run_id"):
        if meta.get(k) is not None:
            out[k] = meta.get(k)
    attempt_errors = meta.get("cte_attempt_errors") or []
    if isinstance(attempt_errors, list) and attempt_errors:
        out["cte_attempt_errors"] = [
            {
                "attempt": row.get("attempt"),
                "stage": row.get("stage"),
                "error": row.get("error"),
            }
            for row in attempt_errors[:8]
            if isinstance(row, dict)
        ]
    for k in (
        "source",
        "channel_id",
        "chat_id",
        "chat_type",
        "user_id",
        "sender_username",
        "sender_display_name",
    ):
        if meta.get(k):
            out[k] = meta[k]
    return out


def _steps_tail_for_message(raw: str | None, limit: int | None = None) -> dict:
    try:
        meta = json.loads(raw or "{}")
    except Exception:
        return {"steps": [], "step_count": 0, "older": 0}
    if not isinstance(meta, dict):
        return {"steps": [], "step_count": 0, "older": 0}
    steps = meta.get("steps") or []
    if not isinstance(steps, list):
        steps = []
    total = len(steps)
    # limit<=0 or None → full history (no fold/cap)
    if limit is None or int(limit) <= 0:
        lim = total if total > 0 else 0
        tail = steps
    else:
        lim = max(1, int(limit))
        tail = steps[-lim:] if total > lim else steps
    # Strip bulky fields for wire
    slim = []
    for s in tail:
        if not isinstance(s, dict):
            continue
        item = {
            "type": s.get("type"),
            "action": s.get("action"),
            "title": s.get("title"),
            "status": s.get("status"),
            "iteration": s.get("iteration"),
            "hidden": s.get("hidden"),
        }
        preview = s.get("preview")
        if isinstance(preview, str) and preview.strip():
            item["preview"] = preview[:300]
        content = s.get("content")
        if isinstance(content, str) and content.strip() and s.get("status") == "error":
            item["content"] = content[:400]
        slim.append(item)
    try:
        stored = int(meta.get("step_count") or 0)
    except (TypeError, ValueError):
        stored = 0
    return {
        "steps": slim,
        "step_count": max(total, stored),
        "older": max(0, total - len(slim)),
    }


class ChatBody(BaseModel):
    agent_id: str | None = None
    session_id: str | None = None
    message: str | None = None
    action: str | None = None
    tick_id: str | None = None
    enabled: bool | None = None
    cron: str | None = None
    content: str | None = None
    path: str | None = None
    name: str | None = None
    new_name: str | None = None
    dest: str | None = None
    message_ids: list[int] | None = None
    workplace_dir: str | None = None
    workplace_files: list[str] | None = None
    message_id: int | None = None
    limit: int | None = None


def _run_chat_bg(
    agent_id: str,
    session_id: str,
    message: str,
    username: str,
    workplace_dir: str = "",
    workplace_files: list[str] | None = None,
):
    db = SessionLocal()
    try:
        agent = db.query(Agent).filter(Agent.id == agent_id).first()
        if agent:
            asyncio.run(
                run_agent(
                    db,
                    agent,
                    session_id,
                    message,
                    username,
                    workplace_dir=workplace_dir,
                    workplace_files=workplace_files or [],
                )
            )
    finally:
        stop_chat(agent_id or "", session_id or "")
        db.close()


@router.get("")
async def chat_get(
    request: Request,
    action: str = Query(None),
    agent_id: str = Query(None),
    session_id: str = Query(None),
    path: str = Query(None),
    message_id: int = Query(None),
    limit: int = Query(None),
    user: User = Depends(get_session_user),
    db: Session = Depends(get_db),
):
    if action == "get_history":
        msgs = db.query(ChatMessage).filter(
            ChatMessage.agent_id == agent_id,
            ChatMessage.session_id == session_id,
        ).order_by(ChatMessage.id).all()
        return ok([{
            "role": m.role,
            "content": m.content,
            "id": m.id,
            "created_at": m.created_at,
            "meta": _slim_meta_for_history(m.meta),
        } for m in msgs])
    if action == "get_message_steps":
        if not message_id:
            return fail("缺少 message_id")
        row = db.query(ChatMessage).filter(
            ChatMessage.id == message_id,
            ChatMessage.agent_id == agent_id,
        ).first()
        if not row:
            return fail("消息不存在")
        return ok(_steps_tail_for_message(row.meta, limit if limit is not None else 0))
    if action == "get_note":
        note = db.query(ChatNote).filter(ChatNote.agent_id == agent_id, ChatNote.session_id == session_id).first()
        return ok({"content": note.content if note else ""})
    if action == "get_summary":
        s = db.query(ChatSummary).filter(ChatSummary.agent_id == agent_id, ChatSummary.session_id == session_id).first()
        return ok({"content": s.content if s else ""})
    if action == "get_result":
        msgs = db.query(ChatMessage).filter(ChatMessage.agent_id == agent_id, ChatMessage.session_id == session_id).order_by(ChatMessage.id.desc()).limit(1).all()
        return ok({"content": msgs[0].content if msgs else ""})
    if action == "list_workplace":
        from app.services.workplace import list_dir
        agent = db.query(Agent).filter(Agent.id == agent_id).first()
        sb = agent.sandbox_id if agent else "default"
        return ok(list_dir(sb, path or ""))
    if action == "list_workplace_dirs":
        from app.services.workplace import list_dirs
        agent = db.query(Agent).filter(Agent.id == agent_id).first()
        return ok(list_dirs(agent.sandbox_id if agent else "default"))
    if action == "view_workplace":
        from app.services.workplace import wp_action
        agent = db.query(Agent).filter(Agent.id == agent_id).first()
        result = wp_action(agent.sandbox_id if agent else "default", "view_workplace", type("B", (), {"path": path})())
        return ok(result)
    if action == "download_workplace":
        from app.services.workplace import download_path
        agent = db.query(Agent).filter(Agent.id == agent_id).first()
        fp = download_path(agent.sandbox_id if agent else "default", path or "")
        if fp:
            return FileResponse(fp, filename=fp.name)
        return fail("文件不存在")
    if action == "check_status":
        running = bool(is_running(agent_id or "", session_id or ""))
        etag = f'"r-{1 if running else 0}-{agent_id or ""}-{session_id or ""}"'
        if_none = (request.headers.get("if-none-match") or "").strip()
        if if_none and if_none == etag:
            return Response(status_code=304, headers={
                "ETag": etag,
                "Cache-Control": "private, max-age=0, must-revalidate",
            })
        return JSONResponse(
            content=ok({"running": running}),
            headers={
                "ETag": etag,
                "Cache-Control": "private, max-age=0, must-revalidate",
            },
        )
    return ok({})


@router.post("")
async def chat_post(
    body: ChatBody,
    background_tasks: BackgroundTasks,
    action: str = Query(None),
    user: User = Depends(get_session_user),
    db: Session = Depends(get_db),
):
    act = action or body.action

    if act == "submit_chat":
        background_tasks.add_task(
            _run_chat_bg,
            body.agent_id,
            body.session_id,
            body.message or "",
            user.username,
            (body.workplace_dir or "").strip(),
            list(body.workplace_files or []),
        )
        return ok({"status": "started"}, "已提交")

    if act == "stop_chat":
        aid = body.agent_id or ""
        sid = body.session_id or ""
        stop_chat(aid, sid)
        try:
            await hub.publish(f"{aid}:{sid}", {
                "type": "done",
                "content": "[已停止]",
                "content_truncated": False,
                "stopped": True,
                "workplace_changed": False,
            })
        except Exception:
            logger.exception("stop_chat publish failed agent=%s session=%s", aid, sid)
        return ok(None, "已停止")

    if act == "clear_history":
        db.query(ChatMessage).filter(ChatMessage.agent_id == body.agent_id, ChatMessage.session_id == body.session_id).delete()
        db.query(ChatSummary).filter(
            ChatSummary.agent_id == body.agent_id,
            ChatSummary.session_id == body.session_id,
        ).delete()
        db.commit()
        return ok(None, "已清空")

    if act == "delete_messages":
        if body.message_ids:
            db.query(ChatMessage).filter(ChatMessage.id.in_(body.message_ids)).delete(synchronize_session=False)
            db.commit()
        return ok(None, "已删除")

    if act == "save_note":
        note = db.query(ChatNote).filter(ChatNote.agent_id == body.agent_id, ChatNote.session_id == body.session_id).first()
        if not note:
            note = ChatNote(agent_id=body.agent_id, session_id=body.session_id)
            db.add(note)
        note.content = body.content or ""
        db.commit()
        return ok(None, "保存成功")

    if act == "save_summary" or act == "delete_summary":
        if act == "delete_summary":
            db.query(ChatSummary).filter(ChatSummary.agent_id == body.agent_id, ChatSummary.session_id == body.session_id).delete()
        else:
            s = db.query(ChatSummary).filter(ChatSummary.agent_id == body.agent_id, ChatSummary.session_id == body.session_id).first()
            if not s:
                s = ChatSummary(agent_id=body.agent_id, session_id=body.session_id)
                db.add(s)
            s.content = body.content or ""
        db.commit()
        return ok(None, "操作成功")

    if act == "generate_summary":
        agent = db.query(Agent).filter(Agent.id == body.agent_id).first()
        if not agent:
            return fail("Agent 不存在")
        if not body.session_id:
            return fail("缺少 session_id")
        try:
            content = await generate_session_summary(db, agent, body.session_id)
        except Exception as e:
            logger.exception(
                "generate_summary failed agent=%s session=%s",
                body.agent_id,
                body.session_id,
            )
            return fail(str(e) or "生成总结失败")
        return ok({"content": content}, "已生成总结")

    if act in ("list_ticks", "add_tick", "update_tick", "toggle_tick", "delete_tick", "ping"):
        if act == "add_tick":
            enabled = body.enabled if body.enabled is not None else True
            t = AgentTick(
                tick_id=new_id(),
                agent_id=body.agent_id,
                session_id=body.session_id,
                cron=body.cron or "0 * * * *",
                message=body.message or "",
                enabled=enabled,
                creator=user.username,
            )
            db.add(t)
            db.commit()
            if enabled:
                tick_scheduler.add_tick_job(t.tick_id, t.cron)
            return ok({"tick_id": t.tick_id}, "添加成功")
        if act == "toggle_tick":
            t = db.query(AgentTick).filter(AgentTick.tick_id == body.tick_id).first()
            if t:
                t.enabled = body.enabled if body.enabled is not None else not t.enabled
                db.commit()
                if t.enabled:
                    tick_scheduler.add_tick_job(t.tick_id, t.cron)
                else:
                    tick_scheduler.remove_tick_job(t.tick_id)
            return ok(None, "操作成功")
        if act == "delete_tick":
            t = db.query(AgentTick).filter(AgentTick.tick_id == body.tick_id).first()
            if t:
                tick_scheduler.remove_tick_job(t.tick_id)
            db.query(AgentTick).filter(AgentTick.tick_id == body.tick_id).delete()
            db.commit()
            return ok(None, "删除成功")
        if act == "update_tick":
            t = db.query(AgentTick).filter(AgentTick.tick_id == body.tick_id).first()
            if not t:
                return fail("定时器不存在")
            if body.cron is not None:
                t.cron = body.cron
            if body.message is not None:
                t.message = body.message
            if body.enabled is not None:
                t.enabled = body.enabled
            db.commit()
            tick_scheduler.remove_tick_job(t.tick_id)
            if t.enabled:
                tick_scheduler.add_tick_job(t.tick_id, t.cron)
            return ok(None, "更新成功")
        if act == "list_ticks":
            ticks = db.query(AgentTick).filter(AgentTick.agent_id == body.agent_id, AgentTick.session_id == body.session_id).all()
            return ok([{"tick_id": t.tick_id, "cron": t.cron, "message": t.message, "enabled": t.enabled} for t in ticks])
        if act == "ping":
            return ok({"pong": True})

    wp_actions = (
        "mkdir_workplace", "rename_workplace", "move_workplace", "delete_workplace",
        "save_workplace", "save_excel_workplace", "view_workplace", "zip_workplace", "unzip_workplace",
    )
    if act in wp_actions:
        from app.services.workplace import wp_action
        agent = db.query(Agent).filter(Agent.id == body.agent_id).first()
        result = wp_action(agent.sandbox_id if agent else "default", act, body)
        return ok(result) if result.get("ok", True) else fail(result.get("msg", "失败"))

    return fail("未知操作")


@router.post("/upload")
async def chat_upload(
    agent_id: str = Form(...),
    path: str = Form(""),
    file: UploadFile = File(...),
    user: User = Depends(get_session_user),
    db: Session = Depends(get_db),
):
    from app.services.workplace import upload_file
    agent = db.query(Agent).filter(Agent.id == agent_id).first()
    if not agent:
        return fail("Agent 不存在")
    content = await file.read()
    result = upload_file(agent.sandbox_id or "default", path or "", file.filename or "upload.bin", content)
    if not result.get("ok"):
        return fail(result.get("msg", "上传失败"))
    return ok({"path": result["path"]}, "上传成功")


@router.post("/transcribe")
async def chat_transcribe(
    file: UploadFile = File(...),
    user: User = Depends(get_session_user),
):
    from app.services.asr_service import AsrError, transcribe_audio

    content = await file.read()
    try:
        text = await transcribe_audio(
            file.filename or "recording.webm",
            content,
            file.content_type,
        )
    except AsrError as e:
        return fail(e.message)
    return ok({"text": text}, "转写成功")
