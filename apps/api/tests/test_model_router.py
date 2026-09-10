import json
import asyncio
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import Agent, ChatMessage, LLMResource, ModelRoleGroup, ModelRouteDecision, ModelRoutingPolicy, User
from app.services.agent_runtime.context import AgentContext
from app.services.agent_runtime.runtime import AgentRuntime
from app.services.llm_client import ChatResult, LLMProviderThrottled, LLMTransportError
from app.services.model_router import ModelRouteCandidate, build_ordered_fallback_candidates, build_route_candidates, frozen_llm_for_run, parse_route_selection, persist_route_decision, required_modalities_for_message, route_model


def test_candidate_builder_filters_by_runtime_modality_context_health_and_authorization():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    admin = User(id=1, username="admin", password_hash="x", roles='["admin"]')
    cap = lambda **extra: json.dumps({"roles": ["general"], "modalities": ["text"], "runtimes": ["react"], "enabled": True, **extra})
    db.add_all([
        LLMResource(id="router", name="Router", type="llm", creator="admin", max_context_tokens=1000, routing_capabilities=cap()),
        LLMResource(id="ok", name="Ok", type="llm", creator="admin", max_context_tokens=1000, routing_capabilities=cap()),
        LLMResource(id="visionless", name="NoVision", type="llm", creator="admin", max_context_tokens=1000, routing_capabilities=cap()),
        LLMResource(id="disabled", name="Disabled", type="llm", creator="admin", max_context_tokens=1000, routing_capabilities=cap(enabled=False)),
        LLMResource(id="short", name="Short", type="llm", creator="admin", max_context_tokens=10, routing_capabilities=cap()),
        LLMResource(id="embedding", name="Embedding", type="embedding", creator="admin", max_context_tokens=1000, routing_capabilities=cap()),
    ])
    db.add_all([
        ModelRoleGroup(id="g1", name="Ok", role="general", preferred_llm_id="ok", creator="admin"),
        ModelRoleGroup(id="g2", name="Disabled", role="general", preferred_llm_id="disabled", creator="admin"),
        ModelRoleGroup(id="g3", name="Short", role="general", preferred_llm_id="short", creator="admin"),
        ModelRoleGroup(id="g4", name="Embedding", role="general", preferred_llm_id="embedding", creator="admin"),
    ])
    policy = ModelRoutingPolicy(id="p1", name="P", router_llm_id="router", role_group_ids='["g1", "g2", "g3", "g4"]', default_role="general", creator="admin")
    db.add(policy)
    db.commit()
    try:
        result = build_route_candidates(db, policy, admin, required_context_tokens=100)
        assert [candidate.llm_id for candidate in result.candidates] == ["ok"]
        assert {item["reason"] for item in result.exclusions} == {"model_disabled", "context_insufficient", "not_leaf_model"}
        media = build_route_candidates(db, policy, admin, modalities={"image"})
        assert media.candidates == []
        assert "modality_unsupported" in {item["reason"] for item in media.exclusions}
    finally:
        db.close()


def test_router_validates_response_and_uses_default_candidate_without_widening(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    user = User(id=1, username="admin", password_hash="x", roles='["admin"]')
    caps = json.dumps({"roles": ["general"], "modalities": ["text"], "runtimes": ["react"], "enabled": True})
    db.add_all([
        LLMResource(id="router", name="Router", type="llm", creator="admin", routing_capabilities=caps),
        LLMResource(id="worker", name="Worker", type="llm", creator="admin", routing_capabilities=caps),
        ModelRoleGroup(id="group", name="General", role="general", preferred_llm_id="worker", creator="admin"),
    ])
    policy = ModelRoutingPolicy(id="policy", name="Policy", router_llm_id="router", role_group_ids='["group"]', default_role="general", creator="admin")
    db.add(policy)
    db.commit()
    try:
        candidates = build_route_candidates(db, policy, user).candidates
        assert candidates[0].llm_id == "worker"
        assert parse_route_selection('{"role":"general","model_id":"worker","reason":"ok"}', policy, candidates).candidate.llm_id == "worker"
        assert parse_route_selection("not json", policy, candidates).candidate.llm_id == "worker"
        assert parse_route_selection('{"role":"general","model_id":"router"}', policy, candidates).failure == "out_of_candidate_set"

        async def timed_out(*args, **kwargs):
            raise TimeoutError()
        monkeypatch.setattr("app.services.model_router.chat_completion", timed_out)
        assert __import__("asyncio").run(route_model(db, policy, "task", candidates)).candidate.llm_id == "worker"
    finally:
        db.close()


def test_unhealthy_candidate_is_excluded_before_router_selection(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    user = User(id=1, username="admin", password_hash="x", roles='["admin"]')
    caps = json.dumps({"roles": ["general"], "modalities": ["text"], "runtimes": ["react"], "enabled": True})
    db.add_all([
        user,
        LLMResource(id="router", name="r", type="llm", creator="admin", routing_capabilities=caps),
        LLMResource(id="unhealthy", name="u", type="llm", creator="admin", routing_capabilities=caps),
        LLMResource(id="healthy", name="h", type="llm", creator="admin", routing_capabilities=caps),
        ModelRoleGroup(id="unhealthy-group", name="u", role="general", preferred_llm_id="unhealthy", creator="admin"),
        ModelRoleGroup(id="healthy-group", name="h", role="general", preferred_llm_id="healthy", creator="admin"),
    ])
    policy = ModelRoutingPolicy(id="policy", name="p", router_llm_id="router", role_group_ids='["unhealthy-group", "healthy-group"]', default_role="general", creator="admin")
    db.add(policy)
    db.commit()

    def throttle_only_unhealthy(llm):
        if llm.id == "unhealthy":
            raise LLMProviderThrottled("circuit open")

    monkeypatch.setattr("app.services.model_router._raise_if_llm_throttle_circuit_open", throttle_only_unhealthy)
    try:
        built = build_route_candidates(db, policy, user)
        assert [candidate.llm_id for candidate in built.candidates] == ["healthy"]
        assert {"model_id": "unhealthy", "reason": "provider_throttled"} in built.exclusions
    finally:
        db.close()


def test_ordered_fallbacks_only_use_the_selected_role_groups_explicit_leaf_order():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    user = User(id=1, username="admin", password_hash="x", roles='["admin"]')
    caps = json.dumps({"roles": ["general"], "modalities": ["text"], "runtimes": ["react"], "enabled": True})
    db.add_all([
        user,
        LLMResource(id="router", name="Router", type="llm", creator="admin", routing_capabilities=caps),
        LLMResource(id="primary", name="Primary", type="llm", creator="admin", routing_capabilities=caps),
        LLMResource(id="second", name="Second", type="llm", creator="admin", routing_capabilities=caps),
        LLMResource(id="third", name="Third", type="llm", creator="admin", routing_capabilities=caps),
        ModelRoleGroup(id="group", name="General", role="general", preferred_llm_id="primary", fallback_llm_ids='["third", "second"]', creator="admin"),
    ])
    policy = ModelRoutingPolicy(id="policy", name="Policy", router_llm_id="router", role_group_ids='["group"]', default_role="general", creator="admin")
    db.add(policy)
    db.commit()
    try:
        selected = build_route_candidates(db, policy, user).candidates[0]
        assert [candidate.llm_id for candidate in build_ordered_fallback_candidates(db, policy, user, selected)] == ["third", "second"]
    finally:
        db.close()


def test_attachment_metadata_requires_media_but_media_words_remain_text_only():
    assert required_modalities_for_message({"attachments": [{"content_type": "image/png"}]}) == {"image"}
    assert required_modalities_for_message({"attachments": [{"filename": "recording.m4a"}]}) == {"audio"}
    assert required_modalities_for_message({"attachments": [{"media_type": "video"}]}) == {"video"}
    assert required_modalities_for_message({}) == {"text"}


def test_standard_runtime_routes_real_attachment_to_matching_media_candidate_not_media_words(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    text_caps = json.dumps({"roles": ["general"], "modalities": ["text"], "runtimes": ["react"], "enabled": True})
    image_caps = json.dumps({"roles": ["multimodal"], "modalities": ["image"], "runtimes": ["react"], "enabled": True})
    db.add_all([
        User(id=1, username="admin", password_hash="x", roles='["admin"]'),
        LLMResource(id="router", name="Router", type="llm", creator="admin", routing_capabilities=text_caps),
        LLMResource(id="text", name="Text", type="llm", creator="admin", routing_capabilities=text_caps),
        LLMResource(id="vision", name="Vision", type="llm", creator="admin", routing_capabilities=image_caps),
        ModelRoleGroup(id="general", name="General", role="general", preferred_llm_id="text", creator="admin"),
        ModelRoleGroup(id="media", name="Media", role="multimodal", preferred_llm_id="vision", creator="admin"),
        ModelRoutingPolicy(id="policy", name="Policy", router_llm_id="router", role_group_ids='["general", "media"]', default_role="general", creator="admin"),
        Agent(id="agent", name="Agent", creator="admin", llm_id="text", routing_policy_id="policy", allowed_actions="[]"),
    ])
    db.commit()
    candidates_seen: list[list[str]] = []
    selected_llms: list[str] = []

    async def fake_router(llm, messages, **kwargs):
        candidates = json.loads(messages[-1]["content"])["candidates"]
        candidates_seen.append([item["model_id"] for item in candidates])
        chosen = candidates[0]
        return json.dumps({"role": chosen["role"], "model_id": chosen["model_id"]})

    async def fake_run(self, ctx):
        selected_llms.append(ctx.llm.id)
        return "ok"

    async def no_summary(*args, **kwargs):
        return ""

    monkeypatch.setattr("app.services.model_router.chat_completion", fake_router)
    monkeypatch.setattr("app.services.agent_runtime.runtime.AgentRuntime.run", fake_run)
    monkeypatch.setattr("app.services.session_summary.generate_session_summary", no_summary)
    try:
        from app.services.agent_runtime.runtime import _run_agent_impl

        agent = db.get(Agent, "agent")
        assert asyncio.run(_run_agent_impl(db, agent, "image-session", "please inspect", message_meta={"attachments": [{"content_type": "image/jpeg"}]})) == "ok"
        assert asyncio.run(_run_agent_impl(db, agent, "text-session", "please optimize image processing")) == "ok"
        assert candidates_seen == [["vision"], ["text"]]
        assert selected_llms == ["vision", "text"]
    finally:
        db.close()


def test_route_decision_persists_and_resolves_one_frozen_leaf_for_a_task():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    user = User(id=1, username="admin", password_hash="x", roles='["admin"]')
    caps = json.dumps({"roles": ["general"], "modalities": ["text"], "runtimes": ["react"], "enabled": True})
    db.add_all([
        LLMResource(id="router", name="Router", type="llm", creator="admin", routing_capabilities=caps),
        LLMResource(id="worker", name="Worker", type="llm", creator="admin", routing_capabilities=caps),
        ModelRoleGroup(id="group", name="General", role="general", preferred_llm_id="worker", creator="admin"),
    ])
    policy = ModelRoutingPolicy(id="policy", name="Policy", router_llm_id="router", role_group_ids='["group"]', default_role="general", creator="admin", version=4)
    db.add(policy)
    db.commit()
    try:
        built = build_route_candidates(db, policy, user)
        selection = parse_route_selection('{"role":"general","model_id":"worker"}', policy, built.candidates)
        decision = persist_route_decision(db, agent_id="agent", session_id="session", policy=policy, selection=selection, candidates=built.candidates, exclusions=built.exclusions)
        assert decision.policy_version == 4
        assert frozen_llm_for_run(db, "agent", "session").id == "worker"
        assert frozen_llm_for_run(db, "agent", "other") is None
    finally:
        db.close()


def test_standard_runtime_uses_routed_leaf_before_loop_and_direct_leaf_without_policy(monkeypatch):
    """A routing policy chooses the immutable loop LLM; legacy Agents stay direct."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    caps = json.dumps({"roles": ["general"], "modalities": ["text"], "runtimes": ["react"], "enabled": True})
    db.add_all([
        User(id=1, username="admin", password_hash="x", roles='["admin"]'),
        LLMResource(id="router", name="Router", type="llm", creator="admin", routing_capabilities=caps),
        LLMResource(id="worker", name="Worker", type="llm", creator="admin", routing_capabilities=caps),
        LLMResource(id="direct", name="Direct", type="llm", creator="admin", routing_capabilities=caps),
        ModelRoleGroup(id="group", name="General", role="general", preferred_llm_id="worker", creator="admin"),
        ModelRoutingPolicy(id="policy", name="Policy", router_llm_id="router", role_group_ids='["group"]', default_role="general", creator="admin"),
        Agent(id="routed", name="Routed", creator="admin", llm_id="direct", routing_policy_id="policy", allowed_actions="[]"),
        Agent(id="legacy", name="Legacy", creator="admin", llm_id="direct", allowed_actions="[]"),
    ])
    db.commit()
    seen: list[tuple[str, str]] = []

    async def fake_router(llm, messages, **kwargs):
        assert llm.id == "router"
        return '{"role":"general","model_id":"worker","reason":"configured candidate"}'

    async def fake_run(self, ctx):
        seen.append((ctx.llm.id, ctx.model_route_decision_id))
        return ctx.llm.id

    async def no_summary(*args, **kwargs):
        return ""

    monkeypatch.setattr("app.services.model_router.chat_completion", fake_router)
    monkeypatch.setattr("app.services.agent_runtime.runtime.AgentRuntime.run", fake_run)
    monkeypatch.setattr("app.services.session_summary.generate_session_summary", no_summary)
    try:
        from app.services.agent_runtime.runtime import _run_agent_impl

        assert __import__("asyncio").run(_run_agent_impl(db, db.get(Agent, "routed"), "routed-session", "write SQL")) == "worker"
        assert __import__("asyncio").run(_run_agent_impl(db, db.get(Agent, "legacy"), "legacy-session", "chat")) == "direct"
        assert seen[0][0] == "worker" and seen[0][1]
        assert seen[1] == ("direct", "")
        assert frozen_llm_for_run(db, "routed", "routed-session").id == "worker"
        assert frozen_llm_for_run(db, "legacy", "legacy-session") is None
    finally:
        db.close()


@pytest.mark.parametrize(
    ("role", "message_meta"),
    [
        ("general", {}),
        ("react_code", {}),
        ("planner", {}),
        ("multimodal", {"attachments": [{"content_type": "image/png"}]}),
        ("fast", {}),
    ],
)
def test_configured_role_metadata_selects_and_freezes_only_its_permitted_leaf(monkeypatch, role, message_meta):
    """Every supported role routes by configured capabilities, never model names."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    modalities = ["image"] if role == "multimodal" else ["text"]
    worker_id = f"leaf-{role}-7"
    router_caps = json.dumps({
        "roles": ["general", "react_code", "planner", "multimodal", "fast"],
        "modalities": ["text", "image"], "runtimes": ["react"], "enabled": True,
    })
    worker_caps = json.dumps({"roles": [role], "modalities": modalities, "runtimes": ["react"], "enabled": True})
    db.add_all([
        User(id=1, username="admin", password_hash="x", roles='["admin"]'),
        LLMResource(id="router-leaf", name="arbitrary-a", type="llm", creator="admin", routing_capabilities=router_caps),
        LLMResource(id=worker_id, name="arbitrary-b", type="llm", creator="admin", routing_capabilities=worker_caps),
        ModelRoleGroup(id=f"group-{role}", name="configured-group", role=role, preferred_llm_id=worker_id, creator="admin"),
        ModelRoutingPolicy(id=f"policy-{role}", name="configured-policy", router_llm_id="router-leaf", role_group_ids=json.dumps([f"group-{role}"]), default_role=role, creator="admin"),
        Agent(id=f"agent-{role}", name="standard-agent", creator="admin", llm_id="router-leaf", routing_policy_id=f"policy-{role}", allowed_actions="[]"),
    ])
    db.commit()
    selected: list[str] = []

    async def fake_router(llm, messages, **kwargs):
        candidates = json.loads(messages[-1]["content"])["candidates"]
        assert candidates == [{"role": role, "model_id": worker_id}]
        return json.dumps({"role": role, "model_id": worker_id})

    async def fake_run(self, ctx):
        selected.append(ctx.llm.id)
        return "ok"

    async def no_summary(*args, **kwargs):
        return ""

    monkeypatch.setattr("app.services.model_router.chat_completion", fake_router)
    monkeypatch.setattr("app.services.agent_runtime.runtime.AgentRuntime.run", fake_run)
    monkeypatch.setattr("app.services.session_summary.generate_session_summary", no_summary)
    try:
        from app.services.agent_runtime.runtime import _run_agent_impl

        session_id = f"session-{role}"
        agent = db.get(Agent, f"agent-{role}")
        assert asyncio.run(_run_agent_impl(db, agent, session_id, "same neutral task", message_meta=message_meta)) == "ok"
        assert selected == [worker_id]
        decision = db.query(ModelRouteDecision).filter_by(agent_id=agent.id, session_id=session_id).one()
        assert (decision.role, decision.llm_id) == (role, worker_id)
        assert frozen_llm_for_run(db, agent.id, session_id).id == worker_id
    finally:
        db.close()


def _routed_loop_context(db):
    caps = json.dumps({"roles": ["general"], "modalities": ["text"], "runtimes": ["react"], "enabled": True})
    db.add_all([
        LLMResource(id="primary", name="Primary", type="llm", creator="admin", routing_capabilities=caps),
        LLMResource(id="fallback", name="Fallback", type="llm", creator="admin", routing_capabilities=caps),
        ModelRoleGroup(id="group", name="General", role="general", preferred_llm_id="primary", fallback_llm_ids='["fallback"]', creator="admin"),
        ModelRoutingPolicy(id="policy", name="Policy", router_llm_id="router", role_group_ids='["group"]', default_role="general", creator="admin", version=2),
        Agent(id="agent", name="Agent", creator="admin", llm_id="primary", routing_policy_id="policy", allowed_actions='["shell"]', max_iterations=5),
    ])
    db.commit()
    primary = db.get(LLMResource, "primary")
    fallback = db.get(LLMResource, "fallback")
    candidate = ModelRouteCandidate(
        role="general", group_id="group", llm_id="fallback", fallback_llm_ids=[],
        budget=0, timeout=0, capabilities=json.loads(caps),
    )
    ctx = AgentContext.from_params(
        db=db, agent=db.get(Agent, "agent"), session_id="session", user_message="do work",
        llm=primary, allowed_actions=["shell"], model_route_decision_id="initial-decision",
        model_route_policy_id="policy", model_route_policy_version=2,
        model_route_fallbacks=[candidate],
    )
    return ctx, primary, fallback


@pytest.mark.parametrize("failure", [
    lambda: TimeoutError("timed out"),
    lambda: LLMTransportError("network unavailable"),
    lambda: LLMProviderThrottled("provider circuit open"),
])
def test_routed_runtime_retries_once_with_ordered_fallback_only_for_pre_output_infrastructure_failure(monkeypatch, failure):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    ctx, primary, fallback = _routed_loop_context(db)
    calls: list[str] = []

    async def fake_chat(llm, messages, **kwargs):
        calls.append(llm.id)
        if llm.id == primary.id:
            raise failure()
        return ChatResult(text="FINAL: done")

    async def fake_tools(**kwargs):
        return "- shell: SHELL: <cmd>"

    async def accepted_final(*args, **kwargs):
        return None

    monkeypatch.setattr("app.services.llm_client.chat_completion", fake_chat)
    monkeypatch.setattr("app.services.agent_runtime.system_prompt.SystemPromptBuilder.build_tools_desc", fake_tools)
    monkeypatch.setattr(AgentRuntime, "_reflect_final", accepted_final)
    try:
        assert asyncio.run(AgentRuntime().run(ctx)) == "done"
        assert calls == [primary.id, fallback.id]
        decision = db.query(ModelRouteDecision).one()
        assert decision.llm_id == fallback.id
        assert json.loads(decision.detail)["kind"] == "pre_output_fallback"
        assert frozen_llm_for_run(db, "agent", "session").id == fallback.id
    finally:
        db.close()


def test_routed_runtime_never_switches_models_after_native_content_or_tool_execution(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    ctx, primary, fallback = _routed_loop_context(db)
    calls: list[str] = []
    tool_calls: list[str] = []

    async def fake_chat(llm, messages, **kwargs):
        calls.append(llm.id)
        if len(calls) == 1:
            return ChatResult(content="executing tool", tool_calls=[{
                "id": "call-1", "function": {"name": "shell", "arguments": '{"cmd":"echo ok"}'},
            }])
        if len(calls) == 2:
            raise LLMTransportError("after output")
        return ChatResult(text="FINAL: done")

    async def fake_tools(**kwargs):
        return "- shell: SHELL: <cmd>"

    async def fake_execute(*args, **kwargs):
        tool_calls.append("shell")
        return "ok"

    async def accepted_final(*args, **kwargs):
        return None

    monkeypatch.setattr("app.services.llm_client.chat_completion", fake_chat)
    monkeypatch.setattr("app.services.agent_runtime.system_prompt.SystemPromptBuilder.build_tools_desc", fake_tools)
    monkeypatch.setattr("app.services.agent_tools.execute_action", fake_execute)
    monkeypatch.setattr(AgentRuntime, "_reflect_final", accepted_final)
    try:
        assert asyncio.run(AgentRuntime().run(ctx)) == "done"
        assert calls == [primary.id, primary.id, primary.id]
        assert tool_calls == ["shell"]
        assert fallback.id not in calls
        assert db.query(ModelRouteDecision).count() == 0
    finally:
        db.close()


def test_model_route_execution_event_is_redacted_and_omits_prompt_key_and_unselected_output(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    ctx, primary, _fallback = _routed_loop_context(db)
    db.add(ModelRouteDecision(
        id="initial-decision", agent_id="agent", session_id="session", policy_id="policy",
        policy_version=2, role="general", llm_id=primary.id,
        detail=json.dumps({
            "candidate_ids": ["primary", "other"],
            "exclusions": [{"model_id": "blocked", "reason": "model_disabled"}],
            "full_prompt": "user secret prompt",
            "api_key": "sk-secret",
            "unselected_tool_output": "private tool result",
        }),
    ))
    db.commit()

    async def fake_chat(llm, messages, **kwargs):
        return ChatResult(text="FINAL: done")

    async def fake_tools(**kwargs):
        return "- shell: SHELL: <cmd>"

    async def accepted_final(*args, **kwargs):
        return None

    monkeypatch.setattr("app.services.llm_client.chat_completion", fake_chat)
    monkeypatch.setattr("app.services.agent_runtime.system_prompt.SystemPromptBuilder.build_tools_desc", fake_tools)
    monkeypatch.setattr(AgentRuntime, "_reflect_final", accepted_final)
    try:
        assert asyncio.run(AgentRuntime().run(ctx)) == "done"
        message = db.query(ChatMessage).filter(ChatMessage.role == "assistant").one()
        steps = json.loads(message.meta)["steps"]
        event = next(step for step in steps if step["action"] == "model_route")
        assert event["collapsed"] is True
        assert event["detail"] == {
            "version": 1, "kind": "selection", "decision_id": "initial-decision",
            "policy_id": "policy", "policy_version": 2, "role": "general",
            "frozen_model_id": "primary", "candidate_ids": ["primary", "other"],
            "exclusions": [{"model_id": "blocked", "reason": "model_disabled"}],
            "failure": "", "duration_ms": 0,
        }
        assert "secret" not in json.dumps(event)
        assert "unselected_tool_output" not in json.dumps(event)
    finally:
        db.close()


def test_model_route_execution_detail_is_default_collapsed_and_ui_expandable():
    from app.routers.agent_chat import _steps_tail_for_message

    result = _steps_tail_for_message(json.dumps({"steps": [{
        "type": "model_route", "action": "model_route", "title": "模型路由",
        "status": "done", "collapsed": True,
        "detail": {
            "version": 1, "kind": "selection", "decision_id": "decision",
            "policy_id": "policy", "policy_version": 2, "role": "planner",
            "frozen_model_id": "worker", "candidate_ids": ["worker"],
            "exclusions": [{"model_id": "blocked", "reason": "model_disabled"}],
            "duration_ms": 17, "full_prompt": "never render", "api_key": "sk-never-render",
        },
    }]}))

    step = result["steps"][0]
    assert step["collapsed"] is True
    assert step["detail"]["frozen_model_id"] == "worker"
    assert step["detail"]["duration_ms"] == 17
    assert "never render" not in json.dumps(step)
    assert "sk-never-render" not in json.dumps(step)

    component = (Path(__file__).resolve().parents[2] / "web" / "src" / "views" / "AgentChat.vue").read_text(encoding="utf-8")
    assert "function isStepDetailExpandable(step)" in component
    assert "step?.type === 'model_route'" in component
    assert "function modelRouteStepDetail(step)" in component
    assert "function executionStepDetail(step)" in component
