import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.services.agent_runtime.runtime import AgentRuntime, _bound_mcp_capability_hints
from app.services.llm_client import ChatResult


class _Query:
    def filter(self, *args, **kwargs):
        return self

    def order_by(self, *args, **kwargs):
        return self

    def all(self):
        return []


class _DB:
    def add(self, _row):
        pass

    def commit(self):
        pass

    def query(self, _model):
        return _Query()


class _MCPQuery(_Query):
    def __init__(self, mcp):
        self.mcp = mcp

    def first(self):
        return self.mcp


class _MetadataDB(_DB):
    def __init__(self, mcp):
        self.mcp = mcp

    def query(self, _model):
        return _MCPQuery(self.mcp)


def test_bound_mcp_metadata_snapshot_does_not_connect_or_list_tools():
    ctx = SimpleNamespace(
        db=_MetadataDB(SimpleNamespace(id="mcp1", name="okx-trader", tags="持仓", description="账户查询")),
        mcp_ids=["mcp1"],
    )
    assert _bound_mcp_capability_hints(ctx) == [{
        "id": "mcp1", "name": "okx-trader", "tags": "持仓", "description": "账户查询",
    }]


def test_bound_mcp_ordinary_message_uses_one_turn_chat_path():
    ctx = SimpleNamespace(
        agent=SimpleNamespace(
            id="a1", llm_timeout=30, name="test", history_length=3,
            memory="", prompt="",
        ),
        session_id="s1",
        chat_key="a1:s1",
        user_message="请解释一下什么是向量数据库",
        db=_DB(),
        llm=SimpleNamespace(id="llm1"),
        mcp_ids=["mcp1"],
        skill_ids=[],
        rag_ids=[],
        httpmcp_ids=[],
        allowed_actions=["mcp_tool_call"],
        profile="standard",
        code_execution=None,
        message_meta={},
        note_content="",
    )

    async def run():
        events = []
        with patch(
            "app.services.llm_client.chat_completion",
            new=AsyncMock(return_value="向量数据库用于语义检索。"),
        ) as completion, patch("app.services.agent_runtime.hub.hub") as hub:
            hub.publish = AsyncMock(side_effect=lambda _key, event: events.append(event))
            result = await AgentRuntime().run(ctx)
        completion.assert_awaited_once()
        done = [event for event in events if event.get("type") == "done"]
        assert done and done[-1]["runtime_metrics"]["route_mode"] == "chat"
        assert done[-1]["runtime_metrics"]["llm_turn_count"] == 1
        return result

    assert asyncio.run(run()) == "向量数据库用于语义检索。"


def test_repeated_modular_reply_stops_after_second_unchanged_round():
    ctx = SimpleNamespace(
        agent=SimpleNamespace(
            id="a1", llm_timeout=30, name="test", history_length=3,
            memory="", prompt="", max_iterations=20, mcp_soft_circuit=5,
        ),
        session_id="s2",
        chat_key="a1:s2",
        user_message="读取文件并总结",
        db=_DB(),
        llm=SimpleNamespace(id="llm1", provider="openai"),
        sandbox=None,
        mcp_ids=[], skill_ids=[], rag_ids=[], httpmcp_ids=[],
        allowed_actions=["shell"], profile="standard", code_execution=None,
        message_meta={}, note_content="", save_dir="", im_source="",
        skill_mds=[], skill_names=[], mcp_names=[], model_route_fallbacks=[],
        model_route_decision_id="", model_route_policy_id="",
        model_route_policy_version=0, model_route_duration_ms=0,
    )

    async def run():
        with patch(
            "app.services.llm_client.chat_completion",
            new=AsyncMock(side_effect=[ChatResult(text="我还在思考") , ChatResult(text="我还在思考")]),
        ) as completion, patch(
            "app.services.agent_runtime.system_prompt.SystemPromptBuilder.build_tools_desc",
            new=AsyncMock(return_value="- shell: SHELL: <cmd>"),
        ), patch("app.services.agent_runtime.hub.hub") as hub:
            hub.publish = AsyncMock()
            result = await AgentRuntime().run(ctx)
        assert completion.await_count == 2
        return result

    assert "重复" in asyncio.run(run())


def test_chat_retries_once_after_empty_reply():
    ctx = SimpleNamespace(
        agent=SimpleNamespace(id="a1", llm_timeout=30, name="test", history_length=3, memory="", prompt=""),
        session_id="s3", chat_key="a1:s3", user_message="你好", db=_DB(),
        llm=SimpleNamespace(id="llm1"), mcp_ids=["mcp1"], skill_ids=[], rag_ids=[],
        httpmcp_ids=[], allowed_actions=["mcp_tool_call"], profile="standard",
        code_execution=None, message_meta={}, note_content="",
    )

    async def run():
        with patch(
            "app.services.llm_client.chat_completion",
            new=AsyncMock(side_effect=["", "这是第二次直接回答。"]),
        ) as completion, patch("app.services.agent_runtime.hub.hub") as hub:
            hub.publish = AsyncMock()
            result = await AgentRuntime().run(ctx)
        assert completion.await_count == 2
        return result

    assert asyncio.run(run()) == "这是第二次直接回答。"


def test_human_wait_does_not_call_llm():
    ctx = SimpleNamespace(
        agent=SimpleNamespace(id="a1", llm_timeout=30, name="test"),
        session_id="s4", chat_key="a1:s4", user_message="请确认是否删除生产数据", db=_DB(),
        llm=SimpleNamespace(id="llm1"), mcp_ids=["mcp1"], skill_ids=[], rag_ids=[],
        httpmcp_ids=[], allowed_actions=["mcp_tool_call"], profile="standard",
        code_execution=None, message_meta={"execution_mode": "human_wait"}, note_content="",
    )

    async def run():
        with patch("app.services.llm_client.chat_completion", new=AsyncMock()) as completion, patch(
            "app.services.agent_runtime.hub.hub"
        ) as hub:
            hub.publish = AsyncMock()
            result = await AgentRuntime().run(ctx)
        completion.assert_not_awaited()
        return result

    assert "人工确认" in asyncio.run(run())


def test_unbound_named_mcp_stops_before_llm_and_records_reason():
    ctx = SimpleNamespace(
        agent=SimpleNamespace(id="a1", llm_timeout=30, name="test"),
        session_id="s5", chat_key="a1:s5", user_message="使用 okx-trader 查询当前持仓", db=_DB(),
        llm=SimpleNamespace(id="llm1"), mcp_ids=[], skill_ids=[], rag_ids=[], httpmcp_ids=[],
        allowed_actions=[], profile="standard", code_execution=None, message_meta={}, note_content="",
    )

    async def run():
        events = []
        with patch("app.services.llm_client.chat_completion", new=AsyncMock()) as completion, patch(
            "app.services.agent_runtime.hub.hub"
        ) as hub:
            hub.publish = AsyncMock(side_effect=lambda _key, event: events.append(event))
            result = await AgentRuntime().run(ctx)
        completion.assert_not_awaited()
        done = [event for event in events if event.get("type") == "done"]
        assert done[-1]["runtime_metrics"]["route_mode"] == "task"
        assert done[-1]["runtime_metrics"]["stop_reason"] == "mcp_not_bound"
        return result

    assert "未绑定" in asyncio.run(run())
