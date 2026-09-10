import json

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import can_access_resource, get_session_user
from app.models import LLMResource, ModelRoleGroup, ModelRoutingPolicy, User
from app.schemas import fail, ok
from app.security import new_id, now_str


router = APIRouter(prefix="/pages/page_model_routing.cgi", tags=["model-routing"])

ROLES = {"general", "react_code", "planner", "multimodal", "fast"}
ROUTING_RUNTIME = "react"
MEDIA_MODALITIES = {"image", "audio", "video"}


class ModelRoutingBody(BaseModel):
    action: str | None = None
    id: str | None = None
    llm_id: str | None = None
    capabilities: dict | None = None
    name: str | None = None
    role: str | None = None
    preferred_llm_id: str | None = None
    fallback_llm_ids: list[str] | None = None
    router_llm_id: str | None = None
    role_group_ids: list[str] | None = None
    default_role: str | None = None
    budget: int = 0
    timeout: int = 0
    visibility: str = "private"
    allowed_users: list[str] | None = None
    scope: str | None = None


def _visible(items, user: User, scope: str = "all"):
    result = [item for item in items if can_access_resource(user, item.visibility, item.allowed_users, item.creator)]
    return [item for item in result if item.creator == user.username] if scope == "mine" else result


def _normalized_capabilities(value: dict | None) -> tuple[dict | None, str]:
    value = value or {}
    if not isinstance(value, dict):
        return None, "capabilities 必须是对象"
    roles = value.get("roles", [])
    modalities = value.get("modalities", ["text"])
    runtimes = value.get("runtimes", [])
    if not isinstance(roles, list) or not isinstance(modalities, list) or not isinstance(runtimes, list):
        return None, "roles、modalities 与 runtimes 必须是数组"
    roles = sorted({str(item).strip() for item in roles if str(item).strip()})
    modalities = sorted({str(item).strip() for item in modalities if str(item).strip()})
    runtimes = sorted({str(item).strip() for item in runtimes if str(item).strip()})
    if any(role not in ROLES for role in roles):
        return None, "包含不支持的角色"
    for field in ("cost_tier", "latency_tier"):
        if value.get(field, "") and not isinstance(value[field], str):
            return None, f"{field} 必须是字符串"
    return {
        "roles": roles,
        "modalities": modalities,
        "runtimes": runtimes,
        "cost_tier": str(value.get("cost_tier") or ""),
        "latency_tier": str(value.get("latency_tier") or ""),
        "enabled": bool(value.get("enabled", True)),
    }, ""


def _model_group_member_error(db: Session, llm_id: str, role: str) -> str:
    member = db.get(LLMResource, llm_id)
    if member is None:
        return f"模型不存在：{llm_id}"
    if member.type != "llm":
        return "角色模型组成员必须是叶子模型（type=llm）"
    try:
        capabilities = json.loads(member.routing_capabilities or "{}")
    except json.JSONDecodeError:
        capabilities = {}
    if not isinstance(capabilities, dict):
        capabilities = {}
    if role not in capabilities.get("roles", []):
        return f"模型 {llm_id} 未声明 {role} 角色能力"
    if ROUTING_RUNTIME not in capabilities.get("runtimes", []):
        return f"模型 {llm_id} 不支持 {ROUTING_RUNTIME} 运行时"
    if role == "multimodal" and not (MEDIA_MODALITIES & set(capabilities.get("modalities", []))):
        return f"模型 {llm_id} 未声明媒体输入能力"
    return ""


@router.get("")
async def model_routing_get(
    action: str = Query("capability_list"), id: str = Query(None), scope: str = Query("all"),
    user: User = Depends(get_session_user), db: Session = Depends(get_db),
):
    if action == "capability_list":
        items = _visible(db.query(LLMResource).filter(LLMResource.type == "llm").all(), user, scope)
        return ok([item.to_dict() for item in items])
    if action == "role_group_list":
        return ok([item.to_dict() for item in _visible(db.query(ModelRoleGroup).all(), user, scope)])
    if action == "policy_list":
        return ok([item.to_dict() for item in _visible(db.query(ModelRoutingPolicy).all(), user, scope)])
    if action == "role_group_get" and id:
        item = db.get(ModelRoleGroup, id)
        if item is None or not can_access_resource(user, item.visibility, item.allowed_users, item.creator):
            return fail("不存在或无权限")
        return ok(item.to_dict())
    return fail("未知操作")


@router.post("")
async def model_routing_post(
    body: ModelRoutingBody, user: User = Depends(get_session_user), db: Session = Depends(get_db),
):
    action = body.action or ""
    if action == "capability_save":
        item = db.get(LLMResource, body.llm_id or "")
        if item is None or not can_access_resource(user, item.visibility, item.allowed_users, item.creator):
            return fail("模型不存在或无权限")
        if item.type != "llm":
            return fail("仅叶子模型可以配置路由能力")
        capabilities, reason = _normalized_capabilities(body.capabilities)
        if reason:
            return fail(reason)
        item.routing_capabilities = json.dumps(capabilities, ensure_ascii=False)
        item.modified_at = now_str()
        db.commit()
        return ok(item.to_dict(), "保存成功")

    if action in {"role_group_create", "role_group_update"}:
        item = None
        if action == "role_group_update":
            item = db.get(ModelRoleGroup, body.id or "")
            if item is None or not can_access_resource(user, item.visibility, item.allowed_users, item.creator):
                return fail("模型组不存在或无权限")
        role = str(body.role or (item.role if item else "") or "").strip()
        if role not in ROLES:
            return fail("请选择支持的角色")
        preferred = str(body.preferred_llm_id or "").strip()
        if not preferred:
            return fail("请选择首选叶子模型")
        members = [preferred, *list(body.fallback_llm_ids or [])]
        if len(set(members)) != len(members):
            return fail("首选与降级模型不能重复")
        for member_id in members:
            reason = _model_group_member_error(db, member_id, role)
            if reason:
                return fail(reason)
        if item is None:
            item = ModelRoleGroup(id=new_id(), creator=user.username)
            db.add(item)
        item.name = str(body.name or item.name or "未命名角色模型组").strip()
        item.role = role
        item.preferred_llm_id = preferred
        item.fallback_llm_ids = json.dumps(members[1:])
        item.budget = max(0, int(body.budget or 0))
        item.timeout = max(0, int(body.timeout or 0))
        item.visibility = body.visibility
        item.allowed_users = json.dumps(body.allowed_users or [])
        item.version = (item.version or 0) + 1 if action == "role_group_update" else 1
        item.modified_at = now_str()
        db.commit()
        return ok(item.to_dict(), "保存成功")

    if action == "role_group_delete":
        item = db.get(ModelRoleGroup, body.id or "")
        if item is None or not can_access_resource(user, item.visibility, item.allowed_users, item.creator):
            return fail("模型组不存在或无权限")
        db.delete(item)
        db.commit()
        return ok(None, "删除成功")

    if action in {"policy_create", "policy_update"}:
        item = None
        if action == "policy_update":
            item = db.get(ModelRoutingPolicy, body.id or "")
            if item is None or not can_access_resource(user, item.visibility, item.allowed_users, item.creator):
                return fail("路由策略不存在或无权限")
        router_llm_id = str(body.router_llm_id or (item.router_llm_id if item else "")).strip()
        router_llm = db.get(LLMResource, router_llm_id)
        if router_llm is None or router_llm.type != "llm":
            return fail("Router LLM 必须是单个叶子模型")
        if not can_access_resource(user, router_llm.visibility, router_llm.allowed_users, router_llm.creator):
            return fail("Router LLM 不存在或无权限")
        group_ids = list(body.role_group_ids if body.role_group_ids is not None else (
            json.loads(item.role_group_ids or "[]") if item else []
        ))
        if not group_ids or len(set(group_ids)) != len(group_ids):
            return fail("请选择不重复的角色模型组")
        groups = [db.get(ModelRoleGroup, group_id) for group_id in group_ids]
        if any(group is None or not can_access_resource(user, group.visibility, group.allowed_users, group.creator) for group in groups):
            return fail("角色模型组不存在或无权限")
        default_role = str(body.default_role or (item.default_role if item else "")).strip()
        if default_role not in {group.role for group in groups}:
            return fail("默认角色必须映射到已选择的角色模型组")
        if item is None:
            item = ModelRoutingPolicy(id=new_id(), creator=user.username)
            db.add(item)
        item.name = str(body.name or item.name or "未命名路由策略").strip()
        item.router_llm_id = router_llm_id
        item.role_group_ids = json.dumps(group_ids)
        item.default_role = default_role
        item.visibility = body.visibility
        item.allowed_users = json.dumps(body.allowed_users or [])
        item.version = (item.version or 0) + 1 if action == "policy_update" else 1
        item.modified_at = now_str()
        db.commit()
        return ok(item.to_dict(), "保存成功")

    if action == "policy_delete":
        item = db.get(ModelRoutingPolicy, body.id or "")
        if item is None or not can_access_resource(user, item.visibility, item.allowed_users, item.creator):
            return fail("路由策略不存在或无权限")
        db.delete(item)
        db.commit()
        return ok(None, "删除成功")
    return fail("未知操作")
