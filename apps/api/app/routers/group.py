import json
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import can_access_resource, get_session_user
from app.models import User, AgentGroup, GroupWorkflow
from app.schemas import ok, fail
from app.security import new_id, now_str

router = APIRouter(prefix="/pages/page_group.cgi", tags=["group"])


class GroupBody(BaseModel):
    action: str | None = None
    id: str | None = None
    group_id: str | None = None
    name: str | None = None
    description: str = ""
    announcement: str = ""
    visibility: str = "private"
    allowed_users: list[str] | None = None
    history_length: int = 10
    agent_id: str | None = None
    session_id: str | None = None
    session_name: str | None = None
    source_session_id: str | None = None
    enabled: bool | None = None
    task: str | None = None
    scope: str | None = None
    keyword: str | None = None


def _filter_groups(items: list[AgentGroup], user: User, scope: str = "all") -> list[AgentGroup]:
    visible = [g for g in items if can_access_resource(user, g.visibility, g.allowed_users, g.creator)]
    if scope == "mine":
        return [g for g in visible if g.creator == user.username]
    return visible


def _get_group_or_fail(db: Session, gid: str, user: User) -> AgentGroup | None:
    g = db.query(AgentGroup).filter(AgentGroup.id == gid).first()
    if not g:
        return None
    if not can_access_resource(user, g.visibility, g.allowed_users, g.creator):
        return None
    return g


def _session_name(group: AgentGroup, session_id: str) -> str:
    for s in json.loads(group.session_list or "[]"):
        if s.get("session_id") == session_id:
            return s.get("name") or session_id
    return session_id


@router.get("")
async def group_get(
    action: str = Query("list"),
    id: str = Query(None),
    scope: str = Query("all"),
    keyword: str = Query(None),
    user: User = Depends(get_session_user),
    db: Session = Depends(get_db),
):
    if action == "list":
        items = _filter_groups(db.query(AgentGroup).all(), user, scope)
        return ok([g.to_dict() for g in items])
    if action == "get" and id:
        g = _get_group_or_fail(db, id, user)
        if not g:
            return fail("不存在")
        return ok(g.to_dict())
    if action == "list_tasks":
        accessible = {g.id: g for g in _filter_groups(db.query(AgentGroup).all(), user, "all")}
        kw = (keyword or "").strip().lower()
        tasks = []
        for w in db.query(GroupWorkflow).order_by(GroupWorkflow.id.desc()).all():
            g = accessible.get(w.group_id)
            if not g:
                continue
            item = {
                "id": w.id,
                "task": w.task,
                "group_id": w.group_id,
                "group_name": g.name,
                "session_id": w.session_id,
                "session_name": _session_name(g, w.session_id),
                "status": w.status,
                "creator": w.creator,
                "created_at": w.created_at,
                "mode": w.mode,
            }
            if kw:
                hay = " ".join(str(item.get(k, "")) for k in ("task", "group_name", "session_name", "session_id", "creator")).lower()
                if kw not in hay:
                    continue
            tasks.append(item)
        return ok(tasks)
    return fail("未知操作")


@router.post("")
async def group_post(body: GroupBody, user: User = Depends(get_session_user), db: Session = Depends(get_db)):
    action = body.action or "create"
    gid = body.group_id or body.id

    if action == "list":
        items = _filter_groups(db.query(AgentGroup).all(), user, body.scope or "all")
        return ok([g.to_dict() for g in items])

    if action in ("create", "update"):
        if body.id:
            g = _get_group_or_fail(db, body.id, user)
            if not g:
                return fail("不存在")
        else:
            sid = new_id()
            g = AgentGroup(
                id=new_id(),
                creator=user.username,
                created_at=now_str(),
                session_list=json.dumps([{"name": "default", "session_id": sid}]),
            )
            db.add(g)
        g.name = body.name or g.name or "未命名"
        g.description = body.description
        g.announcement = body.announcement
        g.visibility = body.visibility
        g.allowed_users = json.dumps(body.allowed_users or [])
        if body.history_length is not None:
            g.history_length = max(0, int(body.history_length))
        g.modified_at = now_str()
        db.commit()
        return ok(g.to_dict(), "保存成功")

    if action == "delete":
        g = db.query(AgentGroup).filter(AgentGroup.id == body.id).first()
        if g and (g.creator == user.username or user.username == "admin"):
            db.delete(g)
            db.commit()
        return ok(None, "删除成功")

    if action == "add_member":
        g = _get_group_or_fail(db, gid or "", user)
        if not g:
            return fail("不存在")
        members = json.loads(g.members or "[]")
        if any(m.get("agent_id") == body.agent_id for m in members):
            return fail("该 Agent 已在群中")
        members.append({
            "agent_id": body.agent_id,
            "session_id": body.session_id or body.agent_id,
            "name": body.name or "",
        })
        g.members = json.dumps(members)
        g.modified_at = now_str()
        db.commit()
        return ok(members, "添加成功")

    if action == "remove_member":
        g = _get_group_or_fail(db, gid or "", user)
        if not g:
            return fail("不存在")
        members = [m for m in json.loads(g.members or "[]") if m.get("agent_id") != body.agent_id]
        g.members = json.dumps(members)
        g.modified_at = now_str()
        db.commit()
        return ok(members, "移除成功")

    if action == "update_member_name":
        g = _get_group_or_fail(db, gid or "", user)
        if not g:
            return fail("不存在")
        members = json.loads(g.members or "[]")
        for m in members:
            if m.get("agent_id") == body.agent_id and m.get("session_id") == body.session_id:
                m["name"] = body.name or m.get("name")
        g.members = json.dumps(members)
        db.commit()
        return ok(members, "修改成功")

    if action == "add_group_session":
        g = _get_group_or_fail(db, gid or "", user)
        if not g:
            return fail("不存在")
        sessions = json.loads(g.session_list or "[]")
        sid = body.session_id or new_id()
        sessions.append({"name": body.session_name or "新会话", "session_id": sid})
        g.session_list = json.dumps(sessions)
        g.modified_at = now_str()
        db.commit()
        return ok(sessions, "创建成功")

    if action in ("update_group_session", "clone_group_session", "delete_group_session"):
        g = _get_group_or_fail(db, gid or "", user)
        if not g:
            return fail("不存在")
        sessions = json.loads(g.session_list or "[]")
        if action == "update_group_session":
            for s in sessions:
                if s["session_id"] == body.session_id:
                    s["name"] = body.session_name or s["name"]
        elif action == "clone_group_session":
            sessions.append({"name": body.session_name or "克隆", "session_id": new_id()})
        elif action == "delete_group_session":
            if len(sessions) <= 1:
                return fail("至少保留一个群会话")
            sessions = [s for s in sessions if s["session_id"] != body.session_id]
        g.session_list = json.dumps(sessions)
        g.modified_at = now_str()
        db.commit()
        return ok(sessions, "操作成功")

    if action == "list_workflows":
        g = _get_group_or_fail(db, gid or "", user)
        if not g:
            return fail("不存在")
        wfs = db.query(GroupWorkflow).filter(GroupWorkflow.group_id == gid, GroupWorkflow.session_id == body.session_id).all()
        return ok([{
            "id": w.id, "task": w.task, "mode": w.mode, "enabled": w.enabled,
            "status": w.status, "creator": w.creator, "created_at": w.created_at,
        } for w in wfs])

    if action == "toggle_workflow":
        wfs = db.query(GroupWorkflow).filter(GroupWorkflow.group_id == gid, GroupWorkflow.session_id == body.session_id).all()
        for w in wfs:
            w.enabled = body.enabled if body.enabled is not None else not w.enabled
        db.commit()
        return ok(None, "操作成功")

    if action == "stop_workflow":
        wfs = db.query(GroupWorkflow).filter(GroupWorkflow.group_id == gid, GroupWorkflow.session_id == body.session_id).all()
        for w in wfs:
            w.status = "stopped"
        db.commit()
        return ok(None, "已停止")

    if action == "delete_workflow":
        if body.id:
            db.query(GroupWorkflow).filter(GroupWorkflow.id == body.id).delete()
        else:
            db.query(GroupWorkflow).filter(
                GroupWorkflow.group_id == gid,
                GroupWorkflow.session_id == body.session_id,
            ).delete()
        db.commit()
        return ok(None, "已删除")

    if action == "add_workflow":
        w = GroupWorkflow(
            group_id=gid,
            session_id=body.session_id or "",
            task=body.task or body.description or body.name or "",
            mode="auto",
            enabled=True,
            status="idle",
            creator=user.username,
            created_at=now_str(),
        )
        db.add(w)
        db.commit()
        return ok({"id": w.id}, "工作流已创建")

    if action == "run_workflow":
        g = _get_group_or_fail(db, gid or "", user)
        if not g:
            return fail("不存在")
        from app.services.workflow_runner import run_workflow
        import asyncio
        task = body.task or body.description or body.name or "群任务"
        result = asyncio.run(run_workflow(db, g, body.session_id or "", task, user.username))
        return ok(result, "工作流已启动")

    return fail("未知操作")
