import json
from datetime import datetime
from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.database import get_db
from app.menu_config import ALL_PAGES
from app.models import User, Session as DbSession, RoleDefinition
from app.security import new_token, session_expiry


# Re-export for routers

MASTER_PAGES = ALL_PAGES


def _json_list(v):
    if isinstance(v, str):
        try:
            return json.loads(v)
        except Exception:
            return []
    return v or []


def can_access_resource(user: User, visibility: str, allowed_users: str, creator: str) -> bool:
    roles = _json_list(user.roles)
    if "master" in roles or "admin" in roles:
        return True
    if user.username == creator:
        return True
    if visibility == "public":
        return True
    allowed = _json_list(allowed_users)
    return user.username in allowed


def resolve_user_permissions(user: User, db: Session) -> set[str]:
    roles = _json_list(user.roles)
    perms: set[str] = set()
    for role_name in roles:
        rd = db.query(RoleDefinition).filter(RoleDefinition.name == role_name).first()
        if rd:
            perms.update(_json_list(rd.permissions))
    perms.update(_json_list(user.permissions))
    if not perms:
        perms.add("/pages/system_me.cgi")
    return perms


def can_access_page(user: User, page: str, db: Session | None = None) -> bool:
    if db is not None:
        return page in resolve_user_permissions(user, db)
    roles = _json_list(user.roles)
    if "master" in roles or "admin" in roles:
        return True
    perms = _json_list(user.permissions)
    if not perms:
        return page in ["/pages/system_me.cgi"]
    return page in perms


def get_session_user(request: Request, db: Session = Depends(get_db)) -> User:
    session_id = request.cookies.get("session_id")
    if not session_id:
        auth = request.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            token = auth[7:]
            user = db.query(User).filter(User.disabled == False).all()
            for u in user:
                tokens = _json_list(u.tokens)
                for t in tokens:
                    if isinstance(t, dict) and t.get("token") == token:
                        return u
                    if isinstance(t, str) and t == token:
                        return u
        raise HTTPException(status_code=401, detail="未登录")

    sess = db.query(DbSession).filter(DbSession.session_id == session_id).first()
    if not sess or sess.expires_at < datetime.utcnow():
        raise HTTPException(status_code=401, detail="会话已过期")
    user = db.query(User).filter(User.username == sess.username, User.disabled == False).first()
    if not user:
        raise HTTPException(status_code=401, detail="用户不存在")
    return user


def get_optional_user(request: Request, db: Session = Depends(get_db)) -> User | None:
    try:
        return get_session_user(request, db)
    except HTTPException:
        return None


def create_session(db: Session, username: str) -> str:
    sid = new_token()
    db.add(DbSession(session_id=sid, username=username, expires_at=session_expiry()))
    db.commit()
    return sid


def delete_session(db: Session, session_id: str) -> None:
    db.query(DbSession).filter(DbSession.session_id == session_id).delete()
    db.commit()


def resolve_ws_user(data: dict, db: Session, cookie_session: str | None = None) -> User | None:
    session_id = cookie_session or data.get("session_id_cookie")
    if session_id:
        sess = db.query(DbSession).filter(DbSession.session_id == session_id).first()
        if sess and sess.expires_at >= datetime.utcnow():
            return db.query(User).filter(User.username == sess.username, User.disabled == False).first()
    token = data.get("token") or data.get("bearer")
    if token:
        for u in db.query(User).filter(User.disabled == False).all():
            for t in _json_list(u.tokens):
                tok = t.get("token") if isinstance(t, dict) else t
                if tok == token:
                    return u
    return None
