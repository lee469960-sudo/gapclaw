from types import SimpleNamespace
import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.agent_runtime.mcp_routing import (
    McpRouteCandidate,
    McpRouteDecision,
    apply_empty_route_fallback,
    build_mcp_route_candidates,
    route_mcp_candidates,
)
from app.services.agent_runtime.loop_state import AgentLoopState
from app.services.agent_runtime.runtime import _record_mcp_route_event
from app.services.agent_runtime.system_prompt import SystemPromptBuilder
from app.services.agent_tools import execute_action
from app.services.agent_runtime.runtime import AgentRuntime
from app.services.llm_client import ChatResult
from app.models import MCP


def test_mcp_to_dict_exposes_routing_metadata_status():
    legacy = MCP(id="legacy", name="Legacy MCP", tags=" ", description="")
    described = MCP(id="described", name="Docs MCP", tags="docs", description="")

    assert legacy.to_dict()["routing_eligible"] is True
    assert legacy.to_dict()["routing_status"] == "missing_capability_metadata"
    assert described.to_dict()["routing_eligible"] is True
    assert described.to_dict()["routing_status"] == "eligible"


def test_route_audit_events_are_structured_and_redacted():
    state = AgentLoopState()
    decision = McpRouteDecision(["ads"], reason="Use authorization: Bearer route-secret")

    _record_mcp_route_event(
        state,
        user_message="导出订单 authorization: Bearer user-secret",
        candidates=_candidates(), decision=decision, trigger="initial",
        supplement_index=0,
        mcp_load_results=[
            {"mcp_id": "ads", "status": "catalog_loaded"},
            {"mcp_id": "clickhouse", "status": "tools_list_failed"},
        ],
    )
    _record_mcp_route_event(
        state,
        user_message="导出订单 authorization: Bearer user-secret",
        candidates=_candidates(), decision=McpRouteDecision([], reason="no match"),
        trigger="agent_capability_request",
        supplement_index=1, mcp_load_results=[],
    )

    initial, supplement = state.mcp_route_events
    assert initial == {
        "request_summary": "user_request(chars=38, nonempty_lines=1, credential_markers=2)",
        "candidate_mcp_ids": ["clickhouse", "ads"],
        "selected_mcp_ids": ["ads"],
        "reason": "Use authorization=[REDACTED]",
        "trigger": "initial",
        "supplement_index": 0,
        "loaded_mcp_ids": ["ads"],
        "load_result": "catalog_loaded",
        "mcp_load_results": [
            {"mcp_id": "ads", "status": "catalog_loaded"},
            {"mcp_id": "clickhouse", "status": "tools_list_failed"},
        ],
    }
    assert supplement["trigger"] == "agent_capability_request"
    assert supplement["supplement_index"] == 1
    assert supplement["load_result"] == "no_new_selection"
    assert "user-secret" not in json.dumps(state.mcp_route_events)
    assert "route-secret" not in json.dumps(state.mcp_route_events)
    assert "导出订单" not in json.dumps(state.mcp_route_events)


def test_route_audit_reason_cannot_echo_the_complete_user_request():
    state = AgentLoopState()
    user_message = "导出华东区订单并包含客户邮箱"
    _record_mcp_route_event(
        state,
        user_message=user_message,
        candidates=_candidates(),
        decision=McpRouteDecision(["ads"], reason=f"选择 ADS，因为用户请求：{user_message}"),
        trigger="initial", supplement_index=0,
        mcp_load_results=[{"mcp_id": "ads", "status": "catalog_loaded"}],
    )

    event = state.mcp_route_events[0]
    assert event["reason"] == "选择 ADS，因为用户请求：[USER_REQUEST_REDACTED]"
    assert user_message not in json.dumps(event, ensure_ascii=False)


def test_route_audit_reason_keeps_up_to_four_thousand_characters():
    state = AgentLoopState()
    _record_mcp_route_event(
        state,
        user_message="导出订单",
        candidates=_candidates(),
        decision=McpRouteDecision(["ads"], reason="x" * 4_500),
        trigger="initial", supplement_index=0,
        mcp_load_results=[{"mcp_id": "ads", "status": "catalog_loaded"}],
    )

    assert len(state.mcp_route_events[0]["reason"]) == 4_000


def _db(*mcps):
    db = MagicMock()
    db.query.return_value.filter.return_value.first.side_effect = mcps
    return db


def test_route_candidates_require_mcp_action_without_querying_resources():
    db = _db(SimpleNamespace(id="m1", name="analytics", tags="data", description="query", modified_at=""))

    assert build_mcp_route_candidates(db, ["m1"], []) == []
    db.query.assert_not_called()


def test_route_candidates_include_bound_mcps_without_metadata_in_binding_order():
    described = SimpleNamespace(
        id="m1", name="ClickHouse docs", tags="", description="ClickHouse performance tuning", modified_at="v1"
    )
    empty = SimpleNamespace(id="m2", name="legacy", tags=" ", description=" ", modified_at="v2")
    tagged = SimpleNamespace(
        id="m3", name="ADS sync", tags="query, export", description="", modified_at="v3"
    )
    db = _db(described, empty, tagged)

    candidates = build_mcp_route_candidates(
        db, ["m1", "m2", "m3", "m1"], ["mcp_tool_call"]
    )

    assert [candidate.id for candidate in candidates] == ["m1", "m2", "m3"]
    assert candidates[0].to_prompt_dict() == {
        "id": "m1",
        "name": "ClickHouse docs",
        "tags": "",
        "description": "ClickHouse performance tuning",
    }
    assert candidates[1].name == "legacy"
    assert candidates[2].tags == "query, export"


def _candidates():
    return [
        McpRouteCandidate("clickhouse", "ClickHouse docs", "performance", "Query tuning", "v1"),
        McpRouteCandidate("ads", "ADS sync", "query, export", "Export order data", "v1"),
    ]


def test_route_candidates_uses_metadata_only_and_accepts_multiple_valid_ids():
    response = json.dumps({
        "selected_mcp_ids": ["clickhouse", "ads"],
        "reason": "requires tuning and export",
        "confidence": 0.9,
    })
    with patch(
        "app.services.agent_runtime.mcp_routing.chat_completion",
        new=AsyncMock(return_value=response),
    ) as route_call:
        decision = asyncio.run(route_mcp_candidates(
            llm=SimpleNamespace(), db=MagicMock(), user_message="优化并导出订单", candidates=_candidates()
        ))

    assert decision.selected_mcp_ids == ["clickhouse", "ads"]
    assert decision.reason == "requires tuning and export"
    prompt = route_call.await_args.args[1]
    body = json.loads(prompt[1]["content"])
    assert body["candidates"] == [candidate.to_prompt_dict() for candidate in _candidates()]
    assert "inputSchema" not in prompt[1]["content"]


def test_route_candidates_fails_closed_for_invalid_or_low_confidence_responses():
    for response, failure in (("not json", "invalid_json"), (json.dumps({
        "selected_mcp_ids": ["clickhouse"], "confidence": 0.2,
    }), "low_confidence")):
        with patch(
            "app.services.agent_runtime.mcp_routing.chat_completion",
            new=AsyncMock(return_value=response),
        ):
            decision = asyncio.run(route_mcp_candidates(
                llm=SimpleNamespace(), db=MagicMock(), user_message="优化", candidates=_candidates()
            ))
        assert decision.selected_mcp_ids == []
        assert decision.failure == failure


def test_explicit_mcp_name_is_a_router_signal_but_unknown_ids_are_rejected():
    response = json.dumps({
        "selected_mcp_ids": ["outside-binding", "ads"],
        "reason": "user named it",
        "confidence": 0.9,
    })
    with patch(
        "app.services.agent_runtime.mcp_routing.chat_completion",
        new=AsyncMock(return_value=response),
    ) as route_call:
        decision = asyncio.run(route_mcp_candidates(
            llm=SimpleNamespace(), db=MagicMock(), user_message="请用 ADS sync 导出订单", candidates=_candidates()
        ))

    assert decision.selected_mcp_ids == ["ads"]
    body = json.loads(route_call.await_args.args[1][1]["content"])
    assert body["explicitly_named_mcp_ids"] == ["ads"]


def test_empty_route_fallback_selects_all_eligible_when_nothing_is_loaded():
    decision = apply_empty_route_fallback(McpRouteDecision([], failure="low_confidence"), _candidates())
    assert decision.selected_mcp_ids == ["clickhouse", "ads"]
    assert decision.reason == "empty_route_fallback_all_eligible"


def test_empty_route_fallback_does_not_expand_an_existing_selection():
    decision = apply_empty_route_fallback(
        McpRouteDecision([], failure="low_confidence"),
        _candidates(),
        already_selected=["clickhouse"],
    )
    assert decision.selected_mcp_ids == []
    assert decision.failure == "low_confidence"


def test_route_does_not_run_for_unavailable_or_unauthorized_candidates():
    db = _db(SimpleNamespace(id="m1", name="analytics", tags="data", description="query", modified_at=""))
    candidates = build_mcp_route_candidates(db, ["m1"], [])

    decision = asyncio.run(route_mcp_candidates(
        llm=SimpleNamespace(), db=db, user_message="use analytics", candidates=candidates
    ))

    assert decision.selected_mcp_ids == []
    assert decision.failure == "no_candidates"


def test_native_mcp_tools_are_hidden_when_agent_has_no_mcp_bindings():
    names = {
        item["function"]["name"]
        for item in SystemPromptBuilder.build_tool_schemas(
            ["mcp_tool_call"], mcp_configured=False,
        )
    }

    assert "mcp_tool_call" not in names
    assert "mcp_route_request" not in names

    configured_names = {
        item["function"]["name"]
        for item in SystemPromptBuilder.build_tool_schemas(
            ["mcp_tool_call"], mcp_configured=True,
        )
    }
    assert {"mcp_tool_call", "mcp_route_request"} <= configured_names


def test_selected_subset_is_the_only_catalog_discovery_input():
    selected = SimpleNamespace(id="clickhouse", name="ClickHouse docs")
    db = _db(selected)

    async def build_catalog():
        with patch(
            "app.services.agent_runtime.utils._get_mcp_tools_cached",
            new=AsyncMock(return_value=([{"name": "explain_query"}], "")),
        ) as get_tools:
            text = await SystemPromptBuilder.build_tools_desc(
                db=db,
                agent=SimpleNamespace(),
                allowed=["mcp_tool_call"],
                skill_ids=[],
                mcp_ids=["clickhouse"],
                rag_ids=[],
            )
        return text, get_tools

    text, get_tools = asyncio.run(build_catalog())
    assert "explain_query" in text
    assert text.mcp_load_results == [{"mcp_id": "clickhouse", "status": "catalog_loaded"}]
    get_tools.assert_awaited_once_with(selected)


def test_selected_catalog_reports_an_actual_per_mcp_discovery_failure():
    selected = SimpleNamespace(id="ads", name="ADS sync")
    db = _db(selected)

    async def build_catalog():
        with patch(
            "app.services.agent_runtime.utils._get_mcp_tools_cached",
            new=AsyncMock(return_value=([], "timeout")),
        ):
            return await SystemPromptBuilder.build_tools_desc(
                db=db, agent=SimpleNamespace(), allowed=["mcp_tool_call"],
                skill_ids=[], mcp_ids=["ads"], rag_ids=[],
            )

    text = asyncio.run(build_catalog())

    assert text.mcp_load_results == [{"mcp_id": "ads", "status": "tools_list_failed"}]


def test_selected_multiple_mcps_dispatch_to_the_catalog_owner():
    first = SimpleNamespace(id="clickhouse", name="ClickHouse docs")
    second = SimpleNamespace(id="ads", name="ADS sync")
    db = _db(first, second)
    with patch(
        "app.services.agent_tools._get_mcp_tools_cached",
        new=AsyncMock(side_effect=[([{"name": "explain_query"}], ""), ([{"name": "export_orders"}], "")]),
    ), patch(
        "app.services.agent_tools.call_mcp_tool",
        new=AsyncMock(return_value="exported"),
    ) as call_tool:
        result = asyncio.run(execute_action(
            "mcp_tool_call", 'MCP: export_orders {"date":"2026-09-10"}',
            db, SimpleNamespace(), None, [], ["clickhouse", "ads"], [],
        ))

    assert result == "exported"
    call_tool.assert_awaited_once_with(second, "export_orders", {"date": "2026-09-10"})


def test_runtime_supplements_only_new_mcp_and_bounds_requests():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.database import Base
    from app.services.agent_runtime.mcp_routing import McpRouteDecision

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    ctx = SimpleNamespace(
        agent=SimpleNamespace(id="a1", llm_timeout=30, sandbox_id=None, name="t", max_iterations=4, prompt="", memory=""),
        session_id="s1", chat_key="a1:s1", user_message="analyze then export", db=db,
        llm=SimpleNamespace(id="llm1", provider="anthropic"), sandbox=None,
        mcp_ids=["m1", "m2"], skill_ids=[], skill_names=[], mcp_names=[], skill_mds=[],
        httpmcp_ids=[], rag_ids=[], allowed_actions=["mcp_tool_call"], save_dir="", im_source="",
        note_content="", message_meta={"execution_mode": "task"},
    )
    candidates = _candidates()
    builds = []

    async def build_tools(**kwargs):
        builds.append(list(kwargs["mcp_ids"]))
        return "catalog"

    async def main_chat(*_args, **_kwargs):
        return ChatResult(text="MCP_ROUTE: need export" if len(builds) == 1 else "FINAL: done")

    with patch("app.services.agent_runtime.mcp_routing.build_mcp_route_candidates", return_value=candidates), patch(
        "app.services.agent_runtime.mcp_routing.route_mcp_candidates",
        new=AsyncMock(side_effect=[
            McpRouteDecision(["clickhouse"]), McpRouteDecision(["ads"]),
        ]),
    ), patch("app.services.llm_client.chat_completion", new=main_chat), patch(
        "app.services.agent_runtime.system_prompt.SystemPromptBuilder.build_tools_desc", new=build_tools,
    ), patch.object(AgentRuntime, "_reflect_final", new=AsyncMock(return_value=None)):
        result = asyncio.run(AgentRuntime().run(ctx))

    assert result == "done"
    assert builds == [["clickhouse"], ["clickhouse", "ads"]]


def test_bound_mcp_task_retries_tool_free_final_once():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.database import Base

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    ctx = SimpleNamespace(
        agent=SimpleNamespace(id="a-tool-retry", llm_timeout=30, sandbox_id=None, name="t", max_iterations=4, prompt="", memory=""),
        session_id="s-tool-retry", chat_key="a-tool-retry:s-tool-retry", user_message="查询沪深300涨停股票", db=db,
        llm=SimpleNamespace(id="llm1", provider="anthropic"), sandbox=None,
        mcp_ids=["clickhouse"], skill_ids=[], skill_names=[], mcp_names=[], skill_mds=[],
        httpmcp_ids=[], rag_ids=[], allowed_actions=["mcp_tool_call"], save_dir="", im_source="",
        note_content="", message_meta={"execution_mode": "task"},
    )
    replies = iter(["FINAL: 我先并行获取数据", "MCP: query_stocks {\"index\":\"沪深300\"}", "FINAL: 已完成"])
    with patch("app.services.agent_runtime.mcp_routing.build_mcp_route_candidates", return_value=_candidates()[:1]), patch(
        "app.services.agent_runtime.mcp_routing.route_mcp_candidates",
        new=AsyncMock(return_value=McpRouteDecision(["clickhouse"])),
    ), patch("app.services.llm_client.chat_completion", new=AsyncMock(side_effect=lambda *_a, **_k: ChatResult(text=next(replies)))), patch(
        "app.services.agent_runtime.system_prompt.SystemPromptBuilder.build_tools_desc", new=AsyncMock(return_value="catalog"),
    ), patch("app.services.agent_tools.execute_action", new=AsyncMock(return_value='{"rows": []}')), patch.object(
        AgentRuntime, "_reflect_final", new=AsyncMock(return_value=None),
    ) as execute_mock:
        assert asyncio.run(AgentRuntime().run(ctx)) == "已完成"

    execute_mock.assert_awaited_once()


def test_runtime_blocks_a_third_supplement_request():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.database import Base
    from app.services.agent_runtime.mcp_routing import McpRouteDecision

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    ctx = SimpleNamespace(
        agent=SimpleNamespace(id="a2", llm_timeout=30, sandbox_id=None, name="t", max_iterations=5, prompt="", memory=""),
        session_id="s2", chat_key="a2:s2", user_message="need several sources", db=db,
        llm=SimpleNamespace(id="llm1", provider="anthropic"), sandbox=None,
        mcp_ids=["m1", "m2"], skill_ids=[], skill_names=[], mcp_names=[], skill_mds=[],
        httpmcp_ids=[], rag_ids=[], allowed_actions=["mcp_tool_call"], save_dir="", im_source="",
        note_content="", message_meta={"execution_mode": "task"},
    )
    replies = iter(["MCP_ROUTE: first", "MCP_ROUTE: second", "MCP_ROUTE: third", "FINAL: done"])
    route = AsyncMock(side_effect=[
        McpRouteDecision(["clickhouse"]), McpRouteDecision(["ads"]), McpRouteDecision([]),
    ])
    with patch("app.services.agent_runtime.mcp_routing.build_mcp_route_candidates", return_value=_candidates()), patch(
        "app.services.agent_runtime.mcp_routing.route_mcp_candidates", new=route,
    ), patch("app.services.llm_client.chat_completion", new=AsyncMock(side_effect=lambda *_a, **_k: ChatResult(text=next(replies)))), patch(
        "app.services.agent_runtime.system_prompt.SystemPromptBuilder.build_tools_desc", new=AsyncMock(return_value="catalog"),
    ), patch.object(AgentRuntime, "_reflect_final", new=AsyncMock(return_value=None)):
        assert asyncio.run(AgentRuntime().run(ctx)) == "done"

    assert route.await_count == 3  # initial route plus exactly two supplements


def test_runtime_auto_supplements_after_selected_mcp_lacks_requested_tool():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.database import Base
    from app.services.agent_runtime.mcp_routing import McpRouteDecision

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    ctx = SimpleNamespace(
        agent=SimpleNamespace(id="a3", llm_timeout=30, sandbox_id=None, name="t", max_iterations=3, prompt="", memory=""),
        session_id="s3", chat_key="a3:s3", user_message="export orders", db=db,
        llm=SimpleNamespace(id="llm1", provider="anthropic"), sandbox=None,
        mcp_ids=["m1", "m2"], skill_ids=[], skill_names=[], mcp_names=[], skill_mds=[],
        httpmcp_ids=[], rag_ids=[], allowed_actions=["mcp_tool_call"], save_dir="", im_source="",
        note_content="", message_meta={},
    )
    builds = []
    async def build_tools(**kwargs):
        builds.append(list(kwargs["mcp_ids"]))
        return "catalog"
    replies = iter(["MCP: export_orders {}", "FINAL: done"])
    route = AsyncMock(side_effect=[McpRouteDecision(["clickhouse"]), McpRouteDecision(["ads"])])
    with patch("app.services.agent_runtime.mcp_routing.build_mcp_route_candidates", return_value=_candidates()), patch(
        "app.services.agent_runtime.mcp_routing.route_mcp_candidates", new=route,
    ), patch("app.services.llm_client.chat_completion", new=AsyncMock(side_effect=lambda *_a, **_k: ChatResult(text=next(replies)))), patch(
        "app.services.agent_runtime.system_prompt.SystemPromptBuilder.build_tools_desc", new=build_tools,
    ), patch("app.services.agent_tools.execute_action", new=AsyncMock(return_value="未在绑定 MCP 中找到工具 export_orders")), patch.object(
        AgentRuntime, "_reflect_final", new=AsyncMock(return_value=None),
    ):
        assert asyncio.run(AgentRuntime().run(ctx)) == "done"

    assert route.await_count == 2
    assert builds == [["clickhouse"], ["clickhouse", "ads"]]


def test_runtime_auto_selects_and_retries_once_after_empty_mcp_selection():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.database import Base
    from app.services.agent_runtime.mcp_routing import McpRouteDecision

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    ctx = SimpleNamespace(
        agent=SimpleNamespace(id="a4", llm_timeout=30, sandbox_id=None, name="t", max_iterations=3, prompt="", memory=""),
        session_id="s4", chat_key="a4:s4", user_message="export orders", db=db,
        llm=SimpleNamespace(id="llm1", provider="anthropic"), sandbox=None,
        mcp_ids=["m1", "m2"], skill_ids=[], skill_names=[], mcp_names=[], skill_mds=[],
        httpmcp_ids=[], rag_ids=[], allowed_actions=["mcp_tool_call"], save_dir="", im_source="",
        note_content="", message_meta={},
    )
    builds = []

    async def build_tools(**kwargs):
        builds.append(list(kwargs["mcp_ids"]))
        return "catalog"

    replies = iter(["MCP: export_orders {}", "FINAL: done"])
    route = AsyncMock(side_effect=[McpRouteDecision([])])
    execute = AsyncMock(return_value="exported")
    with patch("app.services.agent_runtime.mcp_routing.build_mcp_route_candidates", return_value=_candidates()), patch(
        "app.services.agent_runtime.mcp_routing.route_mcp_candidates", new=route,
    ), patch("app.services.llm_client.chat_completion", new=AsyncMock(side_effect=lambda *_a, **_k: ChatResult(text=next(replies)))), patch(
        "app.services.agent_runtime.system_prompt.SystemPromptBuilder.build_tools_desc", new=build_tools,
    ), patch("app.services.agent_tools.execute_action", new=execute), patch.object(
        AgentRuntime, "_reflect_final", new=AsyncMock(return_value=None),
    ):
        assert asyncio.run(AgentRuntime().run(ctx)) == "done"

    assert route.await_count == 1
    assert execute.await_count == 1
    assert execute.await_args.args[6] == ["clickhouse", "ads"]
    assert builds == [["clickhouse", "ads"]]


def test_batch_mcp_child_auto_selects_and_retries_after_empty_selection():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.database import Base
    from app.services.agent_runtime.mcp_routing import McpRouteDecision

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    ctx = SimpleNamespace(
        agent=SimpleNamespace(id="a5", llm_timeout=30, sandbox_id=None, name="t", max_iterations=3, prompt="", memory=""),
        session_id="s5", chat_key="a5:s5", user_message="export orders", db=db,
        llm=SimpleNamespace(id="llm1", provider="anthropic"), sandbox=None,
        mcp_ids=["m1", "m2"], skill_ids=[], skill_names=[], mcp_names=[], skill_mds=[],
        httpmcp_ids=[], rag_ids=[], allowed_actions=["mcp_tool_call"], save_dir="", im_source="",
        note_content="", message_meta={},
    )
    builds = []

    async def build_tools(**kwargs):
        builds.append(list(kwargs["mcp_ids"]))
        return "catalog"

    replies = iter([
        'BATCH: {"mode":"parallel","children":['
        '{"id":"m1","reply":"MCP: export_orders {}"}]}',
        "FINAL: done",
    ])
    route = AsyncMock(side_effect=[McpRouteDecision([])])
    execute = AsyncMock(return_value="exported")
    with patch("app.services.agent_runtime.mcp_routing.build_mcp_route_candidates", return_value=_candidates()), patch(
        "app.services.agent_runtime.mcp_routing.route_mcp_candidates", new=route,
    ), patch("app.services.llm_client.chat_completion", new=AsyncMock(side_effect=lambda *_a, **_k: ChatResult(text=next(replies)))), patch(
        "app.services.agent_runtime.system_prompt.SystemPromptBuilder.build_tools_desc", new=build_tools,
    ), patch("app.services.agent_tools.execute_action", new=execute), patch.object(
        AgentRuntime, "_reflect_final", new=AsyncMock(return_value=None),
    ):
        assert asyncio.run(AgentRuntime().run(ctx)) == "done"

    assert route.await_count == 1
    assert execute.await_count == 1
    assert execute.await_args.args[6] == ["clickhouse", "ads"]
    assert builds == [["clickhouse", "ads"]]
