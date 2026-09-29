import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from app.models import Agent, MCP, Skill
from app.routers.agent_chat import _steps_tail_for_message
from app.services.agent_runtime.capability_router import (
    ROUTE_BLOCKED_CAPABILITY,
    ROUTE_CAPABILITY_CANDIDATE,
    ROUTE_EXPLICIT_CAPABILITY_REQUEST,
    ROUTE_MISSING_CAPABILITY,
    ROUTE_NORMAL_CHAT,
    build_bound_capability_profiles,
    build_candidate_capabilities_context,
    capability_profiles_to_classifier_hints,
    route_capabilities,
    sanitize_capability_routing_overrides,
    sanitize_routing_hints,
)


def _db(*rows):
    db = MagicMock()
    db.query.return_value.filter.return_value.first.side_effect = rows
    return db


def test_models_expose_optional_routing_metadata_without_requiring_migration():
    assert MCP(id="m1", name="legacy").to_dict()["routing"] == {}
    assert Skill(id="s1", name="legacy").to_dict()["routing"] == {}
    assert Agent(id="a1", name="agent").to_dict()["capability_routing"] == {}

    mcp = MCP(
        id="m2",
        name="ClickHouse",
        routing=json.dumps({"keywords": ["ck", "sql"], "enabled": True}),
    )
    assert mcp.to_dict()["routing"] == {"keywords": ["ck", "sql"], "enabled": True}


def test_routing_hint_sanitization_ignores_malformed_values_and_keeps_permissions_separate():
    assert sanitize_routing_hints({
        "enabled": 0,
        "keywords": "clickhouse, ck\nsql",
        "aliases": ["ClickHouse", "", "ClickHouse"],
        "tags": 123,
        "description": "  query   data  ",
        "mcp_tool_call": True,
        "api_key": "secret",
    }) == {
        "enabled": False,
        "keywords": ["clickhouse", "ck", "sql"],
        "aliases": ["ClickHouse"],
        "tags": ["123"],
        "description": "query data",
    }
    assert sanitize_capability_routing_overrides({
        " m1 ": {"keywords": ["sql"]},
        "": {"keywords": ["ignored"]},
        "m2": "not-json",
    }) == {"m1": {"keywords": ["sql"]}}


def test_effective_profile_merges_global_hints_and_agent_overrides():
    mcp = MCP(
        id="m1",
        name="ClickHouse Query",
        tags="database",
        description="global description",
        routing=json.dumps({
            "keywords": ["clickhouse", "sql"],
            "aliases": ["ck"],
            "tags": ["query"],
            "description": "global route description",
            "enabled": True,
        }),
    )
    agent = SimpleNamespace(capability_routing=json.dumps({
        "m1": {
            "keywords": ["慢查询", "sql"],
            "aliases": ["CH"],
            "tags": ["analytics"],
            "description": "binding description",
            "enabled": False,
        }
    }))

    profiles = build_bound_capability_profiles(
        _db(mcp),
        agent,
        mcp_ids=["m1"],
        allowed_actions=["mcp_tool_call"],
    )

    assert len(profiles) == 1
    profile = profiles[0]
    assert profile.enabled is False
    assert profile.allowed is True
    assert profile.description == "binding description"
    assert profile.keywords == ["clickhouse", "sql", "慢查询"]
    assert profile.aliases == ["ck", "CH"]
    assert profile.tags == ["database", "query", "analytics"]


def test_classifier_hints_include_keywords_aliases_and_skip_disabled_or_unpermitted_profiles():
    enabled = SimpleNamespace(
        id="m1",
        name="ClickHouse",
        tags="database",
        description="query",
        routing=json.dumps({"keywords": ["sql"], "aliases": ["ck"]}),
    )
    disabled = SimpleNamespace(
        id="m2",
        name="Disabled",
        tags="",
        description="",
        routing=json.dumps({"enabled": False, "keywords": ["hidden"]}),
    )
    profiles = build_bound_capability_profiles(
        _db(enabled, disabled),
        SimpleNamespace(capability_routing="{}"),
        mcp_ids=["m1", "m2"],
        allowed_actions=["mcp_tool_call"],
    )
    hints = capability_profiles_to_classifier_hints(profiles)
    assert hints == [{
        "id": "m1",
        "name": "ClickHouse",
        "tags": "database, sql, ck",
        "description": "query",
        "type": "mcp",
    }]


def test_keyword_alias_and_chinese_phrase_route_bound_mcp_and_skill_without_llm():
    mcp = SimpleNamespace(
        type="mcp",
        id="m1",
        name="clickhouse-query",
        tags=["database", "query"],
        description="ClickHouse SQL performance",
        keywords=["clickhouse", "sql", "慢查询"],
        aliases=["ck"],
        enabled=True,
        allowed=True,
    )
    skill = SimpleNamespace(
        type="skill",
        id="s1",
        name="sql-review",
        tags=["sql", "review"],
        description="SQL 性能分析",
        keywords=["sql优化", "慢查询", "索引"],
        aliases=[],
        enabled=True,
        allowed=True,
    )

    route = route_capabilities("帮我看看这个 ClickHouse SQL 为什么这么慢", [mcp, skill])

    assert route.mode == ROUTE_CAPABILITY_CANDIDATE
    assert [(item.type, item.id) for item in route.candidates] == [("mcp", "m1"), ("skill", "s1")]
    assert "clickhouse" in [term.casefold() for term in route.candidates[0].hit_terms]
    assert build_candidate_capabilities_context(route).startswith("【候选能力】")


def test_explicit_disabled_missing_and_normal_chat_modes_are_safe():
    disabled = SimpleNamespace(
        type="mcp",
        id="m1",
        name="okx-trader",
        tags=[],
        description="",
        keywords=["持仓"],
        aliases=[],
        enabled=False,
        allowed=True,
    )
    assert route_capabilities("使用 okx-trader 查询持仓", [disabled]).mode == ROUTE_BLOCKED_CAPABILITY

    missing = route_capabilities("使用 okx-trader 查询持仓", [], named_resources=["okx-trader"])
    assert missing.mode == ROUTE_MISSING_CAPABILITY
    assert missing.missing == ["okx-trader"]

    enabled = SimpleNamespace(
        type="mcp",
        id="m2",
        name="clickhouse-query",
        tags=["database"],
        description="",
        keywords=["clickhouse"],
        aliases=["ck"],
        enabled=True,
        allowed=True,
    )
    explicit = route_capabilities("@clickhouse-query 查询慢 SQL", [enabled])
    assert explicit.mode == ROUTE_EXPLICIT_CAPABILITY_REQUEST
    assert route_capabilities("解释一下什么是数据库", [enabled]).mode == ROUTE_NORMAL_CHAT


def test_candidate_topk_ordering_and_router_error_degrade_safely():
    profiles = [
        SimpleNamespace(
            type="mcp",
            id=f"m{i}",
            name=f"clickhouse-{i}",
            tags=["sql"],
            description="",
            keywords=["clickhouse", f"rank{i}"],
            aliases=[],
            enabled=True,
            allowed=True,
        )
        for i in range(7)
    ] + [
        SimpleNamespace(
            type="skill",
            id=f"s{i}",
            name=f"sql-review-{i}",
            tags=["sql"],
            description="",
            keywords=["clickhouse", f"review{i}"],
            aliases=[],
            enabled=True,
            allowed=True,
        )
        for i in range(5)
    ]

    route = route_capabilities("帮我查询 clickhouse sql", profiles)

    assert route.mode == ROUTE_CAPABILITY_CANDIDATE
    assert [item.type for item in route.candidates].count("mcp") == 5
    assert [item.type for item in route.candidates].count("skill") == 3
    assert [(item.type, item.id) for item in route.candidates[:3]] == [
        ("mcp", "m0"),
        ("mcp", "m1"),
        ("mcp", "m2"),
    ]

    broken = route_capabilities("帮我查询 clickhouse", [object()])
    assert broken.mode == ROUTE_NORMAL_CHAT
    assert broken.candidates == []
    assert broken.error


def test_route_trace_history_and_frontend_surfaces_are_allowlisted():
    raw = json.dumps({
        "steps": [{
            "type": "capability_route",
            "action": "capability_route",
            "title": "能力路由",
            "status": "done",
            "detail": {
                "route_mode": "capability_candidate",
                "candidate_count": 1,
                "candidate_ids": [{"type": "mcp", "id": "m1", "score": "12"}],
                "matched_terms": ["sql"],
                "blocked": [],
                "missing": [],
                "system_prompt": "must-not-leak",
                "mcp_route": {
                    "candidate_mcp_ids": ["m1", "m2"],
                    "selected_mcp_ids": ["m1"],
                    "failure": "",
                },
            },
        }]
    })

    steps = _steps_tail_for_message(raw)["steps"]

    assert steps[0]["detail"]["route_mode"] == "capability_candidate"
    assert steps[0]["detail"]["candidate_ids"] == [{"type": "mcp", "id": "m1", "score": 12}]
    assert "system_prompt" not in json.dumps(steps[0]["detail"], ensure_ascii=False)
    frontend = Path("apps/web/src/views/AgentChat.vue").read_text(encoding="utf-8")
    assert "function capabilityRouteStepDetail" in frontend
    assert "step.type === 'capability_route'" in frontend
