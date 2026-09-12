import json
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import can_access_resource, get_session_user
from app.models import User, LLMResource
from app.schemas import ok, fail
from app.security import encrypt_secret, new_id, now_str, is_masked_secret
from app.services.llm_client import test_llm_chat, normalize_openai_base_url

router = APIRouter(prefix="/pages/page_llm.cgi", tags=["llm"])


class LLMBody(BaseModel):
    action: str | None = None
    id: str | None = None
    type: str = "llm"
    name: str | None = None
    provider: str = "openai"
    base_url: str = ""
    api_key: str = ""
    model: str = ""
    members: list[str] | None = None
    routing_capabilities: dict | None = None
    description: str = ""
    max_context_tokens: int = 128000
    max_output_tokens: int = 4096
    visibility: str = "private"
    allowed_users: list[str] | None = None
    message: str | None = None
    scope: str | None = None


def _filter_llm(items: list[LLMResource], user: User, scope: str = "all") -> list[LLMResource]:
    visible = _filter_visible(items, user)
    if scope == "mine":
        return [i for i in visible if i.creator == user.username]
    return visible


def _filter_visible(items: list[LLMResource], user: User) -> list[LLMResource]:
    return [i for i in items if can_access_resource(user, i.visibility, i.allowed_users, i.creator)]


@router.get("")
async def llm_get(action: str = Query("list"), id: str = Query(None), scope: str = Query("all"), user: User = Depends(get_session_user), db: Session = Depends(get_db)):
    if action == "list":
        items = _filter_llm(db.query(LLMResource).all(), user, scope)
        return ok([i.to_dict() for i in items])
    if action == "get" and id:
        item = db.query(LLMResource).filter(LLMResource.id == id).first()
        if not item or not can_access_resource(user, item.visibility, item.allowed_users, item.creator):
            return fail("不存在或无权限")
        return ok(item.to_dict(mask_key=False))
    return fail("未知操作")


@router.post("")
async def llm_post(body: LLMBody, user: User = Depends(get_session_user), db: Session = Depends(get_db)):
    action = body.action or ("update" if body.id else "create")

    if action == "list":
        items = _filter_llm(db.query(LLMResource).all(), user, body.scope or "all")
        return ok([i.to_dict() for i in items])

    if action == "get":
        item = db.query(LLMResource).filter(LLMResource.id == body.id).first()
        if not item:
            return fail("不存在")
        return ok(item.to_dict(mask_key=False))

    if action in ("create", "update"):
        if action == "update" and body.id:
            item = db.query(LLMResource).filter(LLMResource.id == body.id).first()
            if not item:
                return fail("不存在")
        else:
            item = LLMResource(id=new_id(), creator=user.username)
            db.add(item)
        item.type = body.type
        item.name = body.name or item.name or "未命名"
        item.provider = body.provider
        item.base_url = normalize_openai_base_url(body.base_url, body.provider)
        provider = (body.provider or "").strip().lower()
        if body.api_key:
            if is_masked_secret(body.api_key):
                return fail("API Key 无效：检测到脱敏占位符，请填写完整密钥")
            item.api_key_enc = encrypt_secret(body.api_key)
        elif action == "create" and body.type == "llm" and provider == "ollama":
            item.api_key_enc = encrypt_secret("ollama")
        elif action == "create" and body.type == "llm":
            return fail("请填写 API Key")
        item.model = body.model
        if body.type == "llm" and body.routing_capabilities is not None:
            capabilities = body.routing_capabilities
            if not isinstance(capabilities, dict):
                return fail("路由能力必须是对象")
            item.routing_capabilities = json.dumps(capabilities, ensure_ascii=False)
        elif body.type != "llm":
            item.routing_capabilities = "{}"
        # react-engine-v11 R1: 写入时校验模型组成员，拦截自引用 / 组套组 / 不存在成员
        # （运行时 R2 环检测兜底旧坏数据 / 直接改库）。叶子模型无成员概念，清空 members。
        if body.type == "group":
            members = list(body.members or [])
            for mid in members:
                if mid == item.id:
                    return fail("模型组不能包含自身（自引用）")
                member = db.query(LLMResource).filter(LLMResource.id == mid).first()
                if not member:
                    return fail(f"模型组成员不存在：{mid}")
                if member.type != "llm":
                    return fail("模型组成员必须是叶子模型（type=llm），禁止组套组")
            item.members = json.dumps(members)
        else:
            item.members = json.dumps([])
        item.description = body.description
        item.max_context_tokens = body.max_context_tokens
        item.max_output_tokens = body.max_output_tokens
        item.visibility = body.visibility
        item.allowed_users = json.dumps(body.allowed_users or [])
        item.modified_at = now_str()
        db.commit()
        return ok(item.to_dict(), "保存成功")

    if action == "delete":
        item = db.query(LLMResource).filter(LLMResource.id == body.id).first()
        if item:
            db.delete(item)
            db.commit()
        return ok(None, "删除成功")

    if action == "test":
        item = db.query(LLMResource).filter(LLMResource.id == body.id).first()
        if not item:
            return fail("不存在")
        reply = await test_llm_chat(item, body.message or "你好", db=db)
        return ok({"reply": reply})

    return fail("未知操作")
