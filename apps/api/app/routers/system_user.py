import json
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import ALL_PAGES, get_session_user, can_access_page
from app.models import User, RoleGrant
from app.schemas import ok, fail
from app.security import hash_password

router = APIRouter(prefix="/pages/system_user.cgi", tags=["users"])


class UserBody(BaseModel):
    action: str | None = None
    username: str | None = None
    old_username: str | None = None
    password: str | None = None
    roles: list[str] | None = None
    assignable_roles: list[str] | None = None
    disabled: bool | None = None
    permission: list[str] | None = None


def _require_admin(user: User):
    roles = json.loads(user.roles or "[]")
    if "master" not in roles and "admin" not in roles:
        return fail("无权限")
    return None


@router.get("")
async def list_users(action: str = Query("list"), user: User = Depends(get_session_user), db: Session = Depends(get_db)):
    if action == "list":
        if err := _require_admin(user):
            return err
        return ok([u.to_dict() for u in db.query(User).all()])
    if action == "get":
        return ok(user.to_dict())
    return fail("未知操作")


@router.post("")
async def user_action(body: UserBody, user: User = Depends(get_session_user), db: Session = Depends(get_db)):
    action = body.action or "create"

    if action == "list":
        if err := _require_admin(user):
            return err
        return ok([u.to_dict() for u in db.query(User).all()])

    if action == "get":
        u = db.query(User).filter(User.username == body.username).first()
        if not u:
            return fail("用户不存在")
        return ok(u.to_dict())

    if action in ("create", "update", "toggle", "delete", "load_grants", "save_grant", "del_grant"):
        if err := _require_admin(user):
            return err

    if action == "create":
        if db.query(User).filter(User.username == body.username).first():
            return fail("用户名已存在")
        roles = body.roles or ["user"]
        if isinstance(roles, str):
            roles = [r.strip() for r in roles.split(",") if r.strip()]
        u = User(
            username=body.username or "",
            password_hash=hash_password(body.password or "123456"),
            roles=json.dumps(roles),
            permissions=json.dumps(body.permission or []),
            assignable_roles=json.dumps(body.assignable_roles or []),
        )
        db.add(u)
        db.commit()
        return ok(u.to_dict(), "创建成功")

    if action == "update":
        uname = body.old_username or body.username
        u = db.query(User).filter(User.username == uname).first()
        if not u:
            return fail("用户不存在")
        if body.username and body.username != uname:
            u.username = body.username
        if body.password:
            u.password_hash = hash_password(body.password)
        if body.roles is not None:
            u.roles = json.dumps(body.roles)
        if body.permission is not None:
            u.permissions = json.dumps(body.permission)
        if body.assignable_roles is not None:
            u.assignable_roles = json.dumps(body.assignable_roles)
        db.commit()
        return ok(u.to_dict(), "更新成功")

    if action == "toggle":
        u = db.query(User).filter(User.username == body.username).first()
        if not u:
            return fail("用户不存在")
        u.disabled = body.disabled if body.disabled is not None else not u.disabled
        db.commit()
        return ok(u.to_dict(), "操作成功")

    if action == "delete":
        u = db.query(User).filter(User.username == body.username).first()
        if not u:
            return fail("用户不存在")
        db.delete(u)
        db.commit()
        return ok(None, "删除成功")

    if action == "load_grants":
        grants = db.query(RoleGrant).all()
        return ok([{"username": g.username, "roles": json.loads(g.roles or "[]")} for g in grants])

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

    return fail("未知操作")
