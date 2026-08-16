import json
from datetime import datetime

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from app.database import SessionLocal
from app.deps import can_access_page, _json_list
from app.menu_config import resolve_page_permission_path
from app.models import User, Session as DbSession


class PagePermissionMiddleware(BaseHTTPMiddleware):
    SKIP_PREFIXES = (
        "/health",
        "/login.cgi",
        "/logout.cgi",
        "/captcha.cgi",
        "/render.cgi",
        "/site-brand.cgi",
        "/statics",
        "/docs",
        "/openapi.json",
        "/redoc",
    )

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if any(path.startswith(p) for p in self.SKIP_PREFIXES):
            return await call_next(request)
        if not path.startswith("/pages/"):
            return await call_next(request)

        db = SessionLocal()
        try:
            user = _resolve_user(request, db)
            if not user:
                return JSONResponse({"code": 401, "msg": "未登录", "data": None}, status_code=401)
            check_path = resolve_page_permission_path(path)
            if not can_access_page(user, check_path, db):
                return JSONResponse({"code": 403, "msg": "无权限访问", "data": None}, status_code=403)
        finally:
            db.close()

        return await call_next(request)


def _resolve_user(request: Request, db) -> User | None:
    session_id = request.cookies.get("session_id")
    if session_id:
        sess = db.query(DbSession).filter(DbSession.session_id == session_id).first()
        if sess and sess.expires_at >= datetime.utcnow():
            return db.query(User).filter(User.username == sess.username, User.disabled == False).first()

    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        token = auth[7:]
        for u in db.query(User).filter(User.disabled == False).all():
            for t in _json_list(u.tokens):
                tok = t.get("token") if isinstance(t, dict) else t
                if tok == token:
                    return u
    return None
