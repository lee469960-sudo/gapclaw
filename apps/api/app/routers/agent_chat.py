import json
import asyncio
import logging
from fastapi import APIRouter, Depends, Query, BackgroundTasks, UploadFile, File, Form, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db, SessionLocal
from app.deps import get_session_user
from app.models import (
    User, Agent, CodeProject, CodeProjectManifest, CodeAgentRun, CodeArtifact,
    ChatMessage, ChatNote, ChatSummary, AgentTick,
)
from app.schemas import ok, fail
from app.security import new_id, now_str
from app.services.agent_runtime import stop_chat, hub, is_running, run_agent
from app.services.session_summary import generate_session_summary
from app.services import tick_scheduler
from app.services.code_agent.control_plane import (
    ManifestUnavailableError,
    PolicyRejectedError,
    agent_would_use_claude_code,
    create_code_run,
)
from app.services.code_agent.claude_code_runtime import GRILL_CONFIRMATION
from app.services.code_agent.results import serialize_code_result
from app.services.code_agent.kill_switch import CodeKillSwitchError
from app.services.code_agent.review import (
    ArtifactReviewError,
    accept_sealed_artifact,
    artifact_file,
    load_reviewable_bundle,
    review_payload,
)
from app.services.code_agent.authorization import (
    CodeAuthorizationError,
    require_project_operator,
    require_project_reviewer,
)

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
    message_id: int | None = None
    limit: int | None = None
    artifact_id: str | None = None


def _run_chat_bg(
    agent_id: str,
    session_id: str,
    message: str,
    workplace_dir: str = "",
    code_run_id: str | None = None,
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
                    workplace_dir=workplace_dir,
                    code_run_id=code_run_id,
                )
            )
    finally:
        stop_chat(agent_id or "", session_id or "")
        db.close()


def _grill_objective(db: Session, agent_id: str, session_id: str) -> str:
    msgs = db.query(ChatMessage).filter(
        ChatMessage.agent_id == agent_id,
        ChatMessage.session_id == session_id,
    ).order_by(ChatMessage.id).all()
    parts: list[str] = []
    for message in msgs:
        text = str(message.content or "").strip()
        if not text or text == GRILL_CONFIRMATION:
            continue
        role = str(message.role or "").strip() or "user"
        parts.append(f"{role}: {text}")
    return "\n\n".join(parts).strip()


def _direct_execute_objective(message: str | None) -> tuple[bool, str]:
    text = str(message or "").strip()
    prefix = "开始执行:"
    if not text.startswith(prefix):
        return False, ""
    return True, text[len(prefix):].strip()


def _enqueue_code_chat(*args) -> str:
    from app.services.code_agent.queue import code_run_queue

    return code_run_queue.submit(_run_chat_bg, *args)


@router.get("")
async def chat_get(
    request: Request,
    action: str = Query(None),
    agent_id: str = Query(None),
    session_id: str = Query(None),
    path: str = Query(None),
    code_run_id: str = Query(None),
    artifact_id: str = Query(None),
    artifact_kind: str = Query(None),
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
    if action == "get_code_result":
        query = db.query(CodeAgentRun).filter(CodeAgentRun.agent_id == agent_id)
        if code_run_id:
            query = query.filter(CodeAgentRun.id == code_run_id)
        else:
            query = query.filter(CodeAgentRun.session_id == (session_id or ""))
        run = query.order_by(CodeAgentRun.created_at.desc()).first()
        if not run:
            return ok(None)
        project = db.query(CodeProject).filter(CodeProject.id == run.project_id).first()
        try:
            require_project_reviewer(user, project, db=db, resource=run)
        except CodeAuthorizationError as exc:
            return fail(exc.reason)
        artifact = (
            db.query(CodeArtifact).filter(CodeArtifact.id == run.artifact_id).first()
            if run.artifact_id else None
        )
        return ok(serialize_code_result(run, artifact))
    if action in {"review_code_artifact", "download_code_artifact"}:
        artifact = db.query(CodeArtifact).filter(CodeArtifact.id == artifact_id).first()
        run = (
            db.query(CodeAgentRun).filter(CodeAgentRun.id == artifact.run_id).first()
            if artifact else None
        )
        project = (
            db.query(CodeProject).filter(CodeProject.id == artifact.project_id).first()
            if artifact else None
        )
        try:
            bundle = load_reviewable_bundle(
                db,
                user=user,
                artifact=artifact,
                run=run,
                project=project,
            )
            if action == "review_code_artifact":
                return ok(review_payload(artifact, bundle))
            path_value, filename = artifact_file(bundle, artifact_kind or "patch")
            return FileResponse(path_value, filename=filename)
        except (ArtifactReviewError, CodeAuthorizationError) as exc:
            return fail(exc.reason)
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
        agent = db.query(Agent).filter(Agent.id == body.agent_id).first()
        if not agent:
            return fail("Agent 不存在")
        if (agent.profile or "standard") == "code":
            project = db.query(CodeProject).filter(CodeProject.id == agent.code_project_id).first()
            try:
                require_project_operator(user, project, db=db, operation="run:create")
                trigger_text = ""
                if agent_would_use_claude_code(db, agent):
                    message = (body.message or "").strip()
                    is_direct_execute, direct_objective = _direct_execute_objective(message)
                    if is_direct_execute:
                        if not direct_objective:
                            return fail("缺少任务目标，请使用「开始执行:<objective>」")
                        objective = direct_objective
                        trigger_text = message
                    else:
                        if message != GRILL_CONFIRMATION:
                            background_tasks.add_task(
                                _enqueue_code_chat,
                                body.agent_id,
                                body.session_id,
                                body.message or "",
                                "",
                                None,
                            )
                            return ok({"status": "grilling", "code_run_id": ""}, "请先确认需求")
                        objective = _grill_objective(
                            db, body.agent_id or "", body.session_id or "",
                        )
                        if not objective:
                            return fail("请先描述要改什么，确认后再回复「开始实现」")
                else:
                    objective = body.message or ""
                run = create_code_run(
                    db,
                    agent=agent,
                    actor=user,
                    project_id=agent.code_project_id,
                    objective=objective,
                    session_id=body.session_id or "",
                    trigger_text=trigger_text,
                )
                db.commit()
            except (
                CodeAuthorizationError,
                ManifestUnavailableError,
                PolicyRejectedError,
                CodeKillSwitchError,
            ) as exc:
                db.rollback()
                return fail(exc.reason)
            if run.status == "pending":
                background_tasks.add_task(
                    _enqueue_code_chat,
                    body.agent_id,
                    body.session_id,
                    body.message or "",
                    "",
                    run.id,
                )
            return ok({"status": run.status, "code_run_id": run.id}, "Code 任务已准备")
        background_tasks.add_task(
            _run_chat_bg,
            body.agent_id,
            body.session_id,
            body.message or "",
            (body.workplace_dir or "").strip(),
        )
        return ok({"status": "started"}, "已提交")

    if act == "stop_chat":
        aid = body.agent_id or ""
        sid = body.session_id or ""
        agent = db.query(Agent).filter(Agent.id == aid).first()
        if agent and (agent.profile or "standard") == "code":
            project = db.query(CodeProject).filter(
                CodeProject.id == agent.code_project_id
            ).first()
            run = (
                db.query(CodeAgentRun)
                .filter(
                    CodeAgentRun.agent_id == aid,
                    CodeAgentRun.session_id == sid,
                )
                .order_by(CodeAgentRun.created_at.desc())
                .first()
            )
            try:
                require_project_operator(user, project, db=db, resource=run)
            except CodeAuthorizationError as exc:
                return fail(exc.reason)
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

    if act == "accept_code_artifact":
        artifact = db.query(CodeArtifact).filter(CodeArtifact.id == body.artifact_id).first()
        run = (
            db.query(CodeAgentRun).filter(CodeAgentRun.id == artifact.run_id).first()
            if artifact else None
        )
        project = (
            db.query(CodeProject).filter(CodeProject.id == artifact.project_id).first()
            if artifact else None
        )
        try:
            require_project_reviewer(
                user,
                project,
                db=db,
                resource=artifact,
                operation="artifact:accept",
            )
            if not run or run.project_id != project.id:
                require_project_reviewer(
                    user,
                    None,
                    db=db,
                    resource=artifact,
                    operation="artifact:accept",
                )
            latest_manifest = (
                db.query(CodeProjectManifest)
                .filter(
                    CodeProjectManifest.project_id == run.project_id,
                    CodeProjectManifest.status == "published",
                )
                .order_by(CodeProjectManifest.version.desc())
                .first()
            )
            review = accept_sealed_artifact(
                db,
                user=user,
                artifact=artifact,
                run=run,
                project=project,
                latest_manifest=latest_manifest,
            )
            return ok({
                "review_id": review.id,
                "artifact_id": review.artifact_id,
                "action": review.action,
                "reviewer": review.reviewer,
                "manifest_hash": review.manifest_hash,
            }, "工件已接受")
        except (ArtifactReviewError, CodeAuthorizationError) as exc:
            return fail(exc.reason)

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
