import asyncio
import json

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import Agent, LLMResource, User
from app.routers.agent import AgentBody, agent_post
from app.routers.model_routing import ModelRoutingBody, model_routing_post


def _db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _admin():
    return User(id=1, username="admin", password_hash="x", roles='["admin"]')


def _call(body, user, db):
    return asyncio.run(model_routing_post(ModelRoutingBody(**body), user=user, db=db))


def _agent_call(body, user, db):
    return asyncio.run(agent_post(AgentBody(**body), user=user, db=db))


def test_leaf_capabilities_and_role_groups_validate_members_without_changing_legacy_group():
    db = _db()
    user = _admin()
    db.add_all([
        LLMResource(id="leafok", name="Leaf", api_key_enc="x", type="llm", creator="admin"),
        LLMResource(id="leafbad", name="Bad", api_key_enc="x", type="llm", creator="admin"),
        LLMResource(id="legacy", name="Legacy", type="group", members='["leafok"]', creator="admin"),
    ])
    db.commit()
    try:
        capability = _call({
            "action": "capability_save", "llm_id": "leafok",
            "capabilities": {
                "roles": ["general", "react_code"], "modalities": ["text"],
                "runtimes": ["react"], "cost_tier": "low", "latency_tier": "fast",
            },
        }, user, db)
        assert capability["code"] == 0
        assert capability["data"]["routing_capabilities"]["roles"] == ["general", "react_code"]

        incompatible = _call({
            "action": "role_group_create", "name": "Bad", "role": "general",
            "preferred_llm_id": "leafbad",
        }, user, db)
        assert incompatible["code"] == 1
        assert "未声明 general" in incompatible["msg"]

        non_leaf = _call({
            "action": "role_group_create", "name": "Legacy", "role": "general",
            "preferred_llm_id": "legacy",
        }, user, db)
        assert non_leaf["code"] == 1
        assert "叶子模型" in non_leaf["msg"]

        saved = _call({
            "action": "role_group_create", "name": "General", "role": "general",
            "preferred_llm_id": "leafok", "budget": 100, "timeout": 30,
        }, user, db)
        assert saved["code"] == 0
        assert saved["data"]["preferred_llm_id"] == "leafok"
        assert db.get(LLMResource, "legacy").members == '["leafok"]'
    finally:
        db.close()


def test_multimodal_role_group_requires_declared_media_and_legacy_groups_cannot_receive_capabilities():
    db = _db()
    user = _admin()
    db.add_all([
        LLMResource(id="vision", name="Vision", api_key_enc="x", type="llm", creator="admin"),
        LLMResource(id="legacy", name="Legacy", type="group", members="[]", creator="admin"),
    ])
    db.commit()
    try:
        assert _call({
            "action": "capability_save", "llm_id": "vision",
            "capabilities": {"roles": ["multimodal"], "modalities": ["text"], "runtimes": ["react"]},
        }, user, db)["code"] == 0
        no_media = _call({
            "action": "role_group_create", "name": "Vision", "role": "multimodal", "preferred_llm_id": "vision",
        }, user, db)
        assert no_media["code"] == 1
        assert "媒体输入能力" in no_media["msg"]
        legacy_capability = _call({
            "action": "capability_save", "llm_id": "legacy", "capabilities": {},
        }, user, db)
        assert legacy_capability["code"] == 1
        assert "叶子模型" in legacy_capability["msg"]
    finally:
        db.close()


def test_policy_requires_direct_router_and_authorized_agent_binding():
    db = _db()
    admin = _admin()
    db.add_all([
        LLMResource(id="router", name="Router", api_key_enc="x", type="llm", creator="admin"),
        LLMResource(id="legacy", name="Legacy", type="group", members="[]", creator="admin"),
    ])
    db.commit()
    try:
        assert _call({
            "action": "capability_save", "llm_id": "router",
            "capabilities": {"roles": ["general"], "modalities": ["text"], "runtimes": ["react"]},
        }, admin, db)["code"] == 0
        group = _call({
            "action": "role_group_create", "name": "General", "role": "general", "preferred_llm_id": "router",
        }, admin, db)
        assert group["code"] == 0
        invalid_router = _call({
            "action": "policy_create", "name": "Bad", "router_llm_id": "legacy",
            "role_group_ids": [group["data"]["id"]], "default_role": "general",
        }, admin, db)
        assert invalid_router["code"] == 1
        policy = _call({
            "action": "policy_create", "name": "Default", "router_llm_id": "router",
            "role_group_ids": [group["data"]["id"]], "default_role": "general",
        }, admin, db)
        assert policy["code"] == 0

        bound = _agent_call({"action": "create", "name": "Routed", "llm": "router", "routing_policy_id": policy["data"]["id"]}, admin, db)
        assert bound["code"] == 0
        assert bound["data"]["routing_policy_name"] == "Default"
        detached = _agent_call({
            "action": "update", "id": bound["data"]["id"], "name": "Routed",
            "routing_policy_id": "",
        }, admin, db)
        assert detached["code"] == 0
        assert detached["data"]["routing_policy_id"] == ""
        assert db.get(Agent, bound["data"]["id"]).llm_id == "router"
        direct = _agent_call({"action": "create", "name": "Direct", "llm": "router"}, admin, db)
        assert direct["code"] == 0
        assert direct["data"]["routing_policy_id"] == ""

        outsider = User(id=2, username="other", password_hash="x", roles='["user"]')
        denied = _agent_call({"action": "create", "name": "Denied", "llm": "router", "routing_policy_id": policy["data"]["id"]}, outsider, db)
        assert denied["code"] == 1
        assert denied["msg"] == "routing_policy_unauthorized"
    finally:
        db.close()
