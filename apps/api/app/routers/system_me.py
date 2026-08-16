import json
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_session_user
from app.models import User, RoleGrant
from app.schemas import ok, fail
from app.security import hash_password, new_token, now_str

router = APIRouter(prefix="/pages/system_me.cgi", tags=["me"])


class ActionBody(BaseModel):
    action: str
    old_password: str | None = None
    new_password: str | None = None
    index: int | None = None
    remark: str | None = None


@router.get("")
@router.post("")
async def me_handler(body: ActionBody | None = None, user: User = Depends(get_session_user), db: Session = Depends(get_db)):
    if body and body.action:
        if body.action == "change_password":
            from app.security import verify_password
            if not verify_password(body.old_password or "", user.password_hash):
                return fail("旧密码错误")
            user.password_hash = hash_password(body.new_password or "")
            db.commit()
            return ok(None, "密码修改成功")
        if body.action == "generate_token":
            tokens = json.loads(user.tokens or "[]")
            tok = {"token": new_token(), "created_at": now_str(), "remark": ""}
            tokens.append(tok)
            user.tokens = json.dumps(tokens)
            db.commit()
            return ok(tokens, "Token生成成功")
        if body.action == "delete_token":
            tokens = json.loads(user.tokens or "[]")
            idx = body.index or 0
            if 0 <= idx < len(tokens):
                tokens.pop(idx)
            user.tokens = json.dumps(tokens)
            db.commit()
            return ok(tokens, "Token删除成功")
        if body.action == "update_remark":
            tokens = json.loads(user.tokens or "[]")
            idx = body.index or 0
            if 0 <= idx < len(tokens):
                tokens[idx]["remark"] = body.remark or ""
            user.tokens = json.dumps(tokens)
            db.commit()
            return ok(tokens, "备注更新成功")
        return fail("未知操作")

    return ok({
        "username": user.username,
        "roles": json.loads(user.roles or "[]"),
        "tokens": json.loads(user.tokens or "[]"),
    })
