import json
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import can_access_resource, get_session_user
from app.models import User, MCP
from app.schemas import ok, fail
from app.security import new_id, now_str
from app.services.mcp_client import call_mcp_tool, connect_mcp_detail

router = APIRouter(prefix="/pages/page_mcp.cgi", tags=["mcp"])


class MCPBody(BaseModel):
    action: str | None = None
    id: str | None = None
    name: str | None = None
    tags: str = ""
    url: str = ""
    headers: str = "{}"
    protocol: str = "sse"
    command: str = ""
    command_args: list | None = None
    command_env: dict | None = None
    description: str = ""
    visibility: str = "private"
    allowed_users: list[str] | None = None
    tool: str | None = None
    args: dict | None = None
    scope: str | None = None


def _filter_mcps(items: list[MCP], user: User, scope: str = "all") -> list[MCP]:
    visible = [m for m in items if can_access_resource(user, m.visibility, m.allowed_users, m.creator)]
    if scope == "mine":
        return [m for m in visible if m.creator == user.username]
    return visible


def _get_mcp_or_fail(db: Session, mid: str, user: User) -> MCP | None:
    m = db.query(MCP).filter(MCP.id == mid).first()
    if not m:
        return None
    if not can_access_resource(user, m.visibility, m.allowed_users, m.creator):
        return None
    return m


@router.get("")
async def mcp_get(
    action: str = Query("list"),
    id: str = Query(None),
    scope: str = Query("all"),
    user: User = Depends(get_session_user),
    db: Session = Depends(get_db),
):
    if action == "list":
        items = _filter_mcps(db.query(MCP).all(), user, scope)
        return ok([m.to_dict() for m in items])
    if action == "get" and id:
        m = _get_mcp_or_fail(db, id, user)
        if not m:
            return fail("不存在")
        return ok(m.to_dict())
    return fail("未知操作")


@router.post("")
async def mcp_post(body: MCPBody, user: User = Depends(get_session_user), db: Session = Depends(get_db)):
    action = body.action or "create"

    if action == "list":
        items = _filter_mcps(db.query(MCP).all(), user, body.scope or "all")
        return ok([m.to_dict() for m in items])

    if action in ("create", "update"):
        if body.id:
            m = db.query(MCP).filter(MCP.id == body.id).first()
            if not m:
                return fail("不存在")
        else:
            m = MCP(id=new_id(), creator=user.username)
            db.add(m)
        m.name = body.name or m.name or "未命名"
        m.tags = body.tags
        m.url = body.url
        m.headers = body.headers
        m.protocol = body.protocol
        m.command = body.command or ""
        if body.command_args is not None:
            m.command_args = json.dumps(body.command_args, ensure_ascii=False)
        if body.command_env is not None:
            m.command_env = json.dumps(body.command_env, ensure_ascii=False)
        m.description = body.description
        m.visibility = body.visibility
        m.allowed_users = json.dumps(body.allowed_users or [])
        m.modified_at = now_str()
        db.commit()
        return ok(m.to_dict(), "保存成功")

    if action == "delete":
        m = db.query(MCP).filter(MCP.id == body.id).first()
        if m:
            db.delete(m)
            db.commit()
        return ok(None, "删除成功")

    if action == "connect":
        m = _get_mcp_or_fail(db, body.id or "", user)
        if not m:
            return fail("不存在")
        detail = await connect_mcp_detail(m)
        return ok(detail)

    if action == "call_tool":
        m = _get_mcp_or_fail(db, body.id or "", user)
        if not m:
            return fail("不存在")
        result = await call_mcp_tool(m, body.tool or "", body.args or {})
        return ok({"result": result})

    if action == "list_tools":
        m = _get_mcp_or_fail(db, body.id or "", user)
        if not m:
            return fail("不存在")
        detail = await connect_mcp_detail(m)
        return ok(detail)

    return fail("未知操作")
