import json
import asyncio
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_session_user
from app.models import User, AgentGroup, GroupChatMessage
from app.schemas import ok, fail
from app.security import now_str
from app.services.workflow_runner import run_workflow, stop_workflow

router = APIRouter(prefix="/pages/page_group_chat.cgi", tags=["group-chat"])


class GroupChatBody(BaseModel):
    group_id: str | None = None
    session_id: str | None = None
    message: str | None = None
    action: str | None = None


@router.get("")
async def group_chat_get(
    group_id: str = Query(None),
    session_id: str = Query(None),
    action: str = Query("get_history"),
    user: User = Depends(get_session_user),
    db: Session = Depends(get_db),
):
    if not group_id:
        return fail("缺少 group_id 参数")
    g = db.query(AgentGroup).filter(AgentGroup.id == group_id).first()
    if not g:
        return fail("群不存在")
    if action == "get_history":
        limit = max(0, int(g.history_length or 10)) * 2
        q = db.query(GroupChatMessage).filter(
            GroupChatMessage.group_id == group_id,
            GroupChatMessage.session_id == session_id,
        )
        if limit <= 0:
            msgs = q.order_by(GroupChatMessage.id).all()
        else:
            msgs = q.order_by(GroupChatMessage.id.desc()).limit(limit).all()[::-1]
        return ok({
            "group": g.to_dict(),
            "messages": [{"role": m.role, "content": m.content, "agent_id": m.agent_id, "created_at": m.created_at} for m in msgs],
            "session_id": session_id,
        })
    return ok({"group": g.to_dict(), "messages": [], "session_id": session_id})


@router.post("")
async def group_chat_post(body: GroupChatBody, user: User = Depends(get_session_user), db: Session = Depends(get_db)):
    if not body.group_id:
        return fail("缺少 group_id 参数")
    g = db.query(AgentGroup).filter(AgentGroup.id == body.group_id).first()
    if not g:
        return fail("群不存在")

    if body.action == "clear_history":
        db.query(GroupChatMessage).filter(GroupChatMessage.group_id == body.group_id, GroupChatMessage.session_id == body.session_id).delete()
        db.commit()
        return ok(None, "已清空")

    if body.action == "stop_workflow":
        stop_workflow(body.group_id, body.session_id or "", db)
        return ok(None, "已停止")

    if body.action == "submit_chat":
        db.add(GroupChatMessage(
            group_id=body.group_id,
            session_id=body.session_id or "",
            role="user",
            content=body.message or "",
            created_at=now_str(),
        ))
        db.commit()
        result = await run_workflow(db, g, body.session_id or "", body.message or "", user.username)
        for r in result.get("results", []):
            if "reply" in r:
                db.add(GroupChatMessage(
                    group_id=body.group_id,
                    session_id=body.session_id or "",
                    role="assistant",
                    content=r["reply"],
                    agent_id=r.get("agent_id", ""),
                    created_at=now_str(),
                ))
        db.commit()
        return ok({"results": result.get("results", []), "status": result.get("status"), "mode": result.get("mode")}, "已提交")

    return ok({"group": g.to_dict()})
