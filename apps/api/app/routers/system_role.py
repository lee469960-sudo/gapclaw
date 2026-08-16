import json
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import ALL_PAGES, get_session_user
from app.models import User, RoleGrant, RoleDefinition
from app.schemas import ok, fail

router = APIRouter(prefix="/pages/system_role.cgi", tags=["roles"])

BUILTIN_ROLES = [
    {"name": "master", "label": "超级管理员", "permissions": ALL_PAGES, "builtin": True},
    {"name": "admin", "label": "管理员", "permissions": ALL_PAGES, "builtin": True},
    {
        "name": "user",
        "label": "普通用户",
        "permissions": [
            "/pages/system_me.cgi",
            "/pages/page_agent.cgi",
            "/pages/page_group.cgi",
            "/pages/page_skills.cgi",
            "/pages/page_mcp.cgi",
            "/pages/page_llm.cgi",
            "/pages/page_files.cgi",
        ],
        "builtin": True,
    },
]


class RoleBody(BaseModel):
    action: str | None = None
    name: str | None = None
    label: str | None = None
    permissions: list[str] | None = None
    username: str | None = None
    roles: list[str] | None = None


def _require_admin(user: User):
    roles = json.loads(user.roles or "[]")
    if "master" not in roles and "admin" not in roles:
        return fail("无权限")
    return None


def _ensure_builtin_roles(db: Session) -> None:
    if db.query(RoleDefinition).count():
        return
    for item in BUILTIN_ROLES:
        db.add(RoleDefinition(
            name=item["name"],
            label=item["label"],
            permissions=json.dumps(item["permissions"]),
            builtin=item["builtin"],
        ))
    db.commit()


def _role_list(db: Session) -> list[dict]:
    _ensure_builtin_roles(db)
    return [r.to_dict() for r in db.query(RoleDefinition).order_by(RoleDefinition.id).all()]


@router.get("")
@router.post("")
async def roles_handler(body: RoleBody | None = None, user: User = Depends(get_session_user), db: Session = Depends(get_db)):
    action = (body.action if body else None) or "list"

    if action in ("save_role", "delete_role", "save_grant", "del_grant"):
        if err := _require_admin(user):
            return err

    if action == "save_role":
        name = (body.name or "").strip()
        if not name:
            return fail("请填写角色标识")
        label = (body.label or name).strip()
        perms = body.permissions or []
        rd = db.query(RoleDefinition).filter(RoleDefinition.name == name).first()
        if rd:
            rd.label = label
            rd.permissions = json.dumps(perms)
        else:
            if not name.replace("_", "").isalnum():
                return fail("角色标识仅支持字母、数字、下划线")
            db.add(RoleDefinition(
                name=name,
                label=label,
                permissions=json.dumps(perms),
                builtin=False,
            ))
        db.commit()
        return ok(_role_list(db), "保存成功")

    if action == "delete_role":
        name = (body.name or "").strip()
        rd = db.query(RoleDefinition).filter(RoleDefinition.name == name).first()
        if not rd:
            return fail("角色不存在")
        if rd.builtin:
            return fail("内置角色不可删除")
        for u in db.query(User).all():
            if name in json.loads(u.roles or "[]"):
                return fail(f"角色仍被用户 {u.username} 使用，无法删除")
        db.delete(rd)
        db.commit()
        return ok(_role_list(db), "删除成功")

    if action == "save_grant":
        g = db.query(RoleGrant).filter(RoleGrant.username == body.username).first()
        if not g:
            g = RoleGrant(username=body.username or "", roles=json.dumps(body.roles or []))
            db.add(g)
        else:
            g.roles = json.dumps(body.roles or [])
        db.commit()
        return ok(None, "保存成功")

    if action == "del_grant":
        db.query(RoleGrant).filter(RoleGrant.username == body.username).delete()
        db.commit()
        return ok(None, "删除成功")

    grants = db.query(RoleGrant).all()
    return ok({
        "pages": ALL_PAGES,
        "definitions": _role_list(db),
        "grants": [{"username": g.username, "roles": json.loads(g.roles or "[]")} for g in grants],
    })
