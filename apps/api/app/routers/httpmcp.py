import json

import httpx
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import can_access_resource, get_session_user
from app.models import User, HttpMcp
from app.schemas import ok, fail
from app.security import new_id, now_str
from app.services.httpmcp_runner import call_httpmcp, normalize_tools, sync_legacy_fields

router = APIRouter(prefix="/pages/page_httpmcp.cgi", tags=["httpmcp"])


class HttpMcpBody(BaseModel):
    action: str | None = None
    id: str | None = None
    name: str | None = None
    version: str = "1.0.0"
    url: str = ""
    method: str = "POST"
    headers: str = "{}"
    body_template: str = ""
    description: str = ""
    auth: list | None = None
    tools: list | None = None
    visibility: str = "private"
    allowed_users: list[str] | None = None
    test_body: dict | None = None
    variables: dict | None = None
    tool: str | None = None


@router.get("")
@router.post("")
async def httpmcp_handler(body: HttpMcpBody | None = None, user: User = Depends(get_session_user), db: Session = Depends(get_db)):
    action = (body.action if body else None) or "list"

    if action == "list":
        items = [h for h in db.query(HttpMcp).all() if can_access_resource(user, h.visibility, h.allowed_users, h.creator)]
        return ok([h.to_dict() for h in items])

    if action in ("create", "update"):
        if body and body.id:
            h = db.query(HttpMcp).filter(HttpMcp.id == body.id).first()
            if not h:
                return fail("不存在")
        else:
            h = HttpMcp(id=new_id(), creator=user.username)
            db.add(h)
        h.name = body.name or h.name or "未命名"
        h.version = (body.version or h.version or "1.0.0").strip()
        h.description = body.description if body.description is not None else (h.description or "")
        h.visibility = body.visibility
        h.allowed_users = json.dumps(body.allowed_users or [])
        if body.auth is not None:
            h.auth_json = json.dumps(body.auth, ensure_ascii=False)
        if body.tools is not None:
            tools = normalize_tools(body.tools)
            h.tools_json = json.dumps(tools, ensure_ascii=False)
            sync_legacy_fields(h, tools)
        elif body.url:
            # Legacy create path
            h.url = body.url
            h.method = body.method
            h.headers = body.headers
            h.body_template = body.body_template
        h.modified_at = now_str()
        db.commit()
        return ok(h.to_dict(), "保存成功")

    if action == "delete":
        db.query(HttpMcp).filter(HttpMcp.id == body.id).delete()
        db.commit()
        return ok(None, "删除成功")

    if action == "test":
        h = db.query(HttpMcp).filter(HttpMcp.id == body.id).first()
        if not h:
            return fail("不存在")
        vars_ = dict(body.variables or body.test_body or {})
        if body.tool:
            vars_["tool"] = body.tool
        if h.tools_json and h.tools_json not in ("[]", "") or h.url:
            result = await call_httpmcp(h, vars_)
            try:
                parsed = json.loads(result)
            except Exception:
                parsed = result
            return ok(parsed)

        headers = json.loads(h.headers or "{}")
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.request(h.method, h.url, headers=headers, json=body.test_body or {})
            return ok({"status": resp.status_code, "body": resp.text[:4000], "headers": dict(resp.headers)})

    if action == "call":
        h = db.query(HttpMcp).filter(HttpMcp.id == body.id).first()
        if not h:
            return fail("不存在")
        vars_ = dict(body.variables or body.test_body or {})
        if body.tool:
            vars_["tool"] = body.tool
        result = await call_httpmcp(h, vars_)
        return ok({"result": result})

    return fail("未知操作")
