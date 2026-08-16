import hashlib

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import create_session, delete_session, get_session_user, resolve_user_permissions
from app.menu_config import APP_VERSION, MENU_GROUPS, PAGE_ROUTE_MAP
from app.models import User
from app.schemas import ok, fail
from app.security import verify_password
from app.services.captcha import create_captcha, verify_captcha
from app.services.site_config import get_site_config

router = APIRouter(tags=["auth"])


class LoginBody(BaseModel):
    username: str
    password: str
    captcha: str = ""


@router.get("/captcha.cgi")
async def captcha(response: Response):
    captcha_id, code = create_captcha()
    response.set_cookie("captcha_id", captcha_id, httponly=True, max_age=300, samesite="lax")
    return ok({"code": code})


@router.post("/login.cgi")
async def login(body: LoginBody, request: Request, response: Response, db: Session = Depends(get_db)):
    captcha_id = request.cookies.get("captcha_id")
    if not verify_captcha(captcha_id, body.captcha):
        return fail("验证码错误或已过期")
    response.delete_cookie("captcha_id")
    user = db.query(User).filter(User.username == body.username).first()
    if not user or user.disabled or not verify_password(body.password, user.password_hash):
        return fail("用户名或密码错误")
    sid = create_session(db, user.username)
    response.set_cookie("session_id", sid, httponly=True, max_age=720 * 3600)
    return ok({"redirect": "/render.cgi", "username": user.username}, "登录成功")


@router.get("/logout.cgi")
async def logout(request: Request, response: Response, db: Session = Depends(get_db)):
    sid = request.cookies.get("session_id")
    if sid:
        delete_session(db, sid)
    response.delete_cookie("session_id")
    return ok(None, "已退出")


@router.get("/render.cgi")
async def render_shell(user: User = Depends(get_session_user), db: Session = Depends(get_db)):
    from app.models import RoleDefinition
    from app.deps import resolve_user_permissions

    _ensure_roles = db.query(RoleDefinition).count()
    if not _ensure_roles:
        from app.routers.system_role import BUILTIN_ROLES
        for item in BUILTIN_ROLES:
            db.add(RoleDefinition(
                name=item["name"],
                label=item["label"],
                permissions=json.dumps(item["permissions"]),
                builtin=item["builtin"],
            ))
        db.commit()

    page_codes = {path: hashlib.md5(path.encode()).hexdigest()[:8] for path in PAGE_ROUTE_MAP}
    allowed_pages = resolve_user_permissions(user, db)

    flat_menus = []
    menu_groups = []
    for group in MENU_GROUPS:
        items = []
        for cgi_path, label in group["items"]:
            if cgi_path not in allowed_pages:
                continue
            route = PAGE_ROUTE_MAP.get(cgi_path, "/")
            entry = {"path": cgi_path, "route": route, "label": label, "code": page_codes.get(cgi_path, "")}
            flat_menus.append(entry)
            items.append(entry)
        if items:
            menu_groups.append({"title": group["title"], "items": items})

    import json
    roles = json.loads(user.roles or "[]")
    permissions = json.loads(user.permissions or "[]")

    site = get_site_config(db)

    return ok({
        "username": user.username,
        "roles": roles,
        "permissions": permissions,
        "menus": flat_menus,
        "menu_groups": menu_groups,
        "page_codes": page_codes,
        "version": APP_VERSION,
        "site_name": site["site_name"],
        "site_logo": site["site_logo"],
        "footer": site["footer"],
    })


@router.get("/site-brand.cgi")
async def site_brand(db: Session = Depends(get_db)):
    """Public site branding for login page (no auth required)."""
    return ok(get_site_config(db))
