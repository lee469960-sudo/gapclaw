import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.services.agent_runtime.conversational import assess_response_quality
from app.services.agent_runtime.execution_policy import (
    ExecutionMode,
    classify_request,
    is_complex_conversation,
    normalize_response_style,
)
from app.services.agent_runtime.runtime import AgentRuntime
from app.services.agent_runtime.conversational import ConversationalHandler


class _Query:
    def filter(self, *args, **kwargs):
        return self

    def order_by(self, *args, **kwargs):
        return self

    def all(self):
        return []


class _DB:
    def query(self, _model):
        return _Query()

    def add(self, _row):
        pass

    def commit(self):
        pass


def test_complexity_routes_complex_chat_without_tools_and_keeps_small_talk_simple():
    assert classify_request("请梳理这个主题的结论、关键要点和限制") is ExecutionMode.CHAT
    assert is_complex_conversation("请分析并对比两个方案，分别说明优缺点")
    assert not is_complex_conversation("你好，在吗？")
    assert not is_complex_conversation("什么是向量数据库")


def test_response_style_contract_defaults_and_safe_fallback():
    assert normalize_response_style("") == "adaptive"
    assert normalize_response_style("structured") == "structured"
    assert normalize_response_style("unknown-style") == "adaptive"
    assert is_complex_conversation("帮我回答这个问题", response_style="structured") is False


def test_structured_style_is_injected_into_conversational_prompt():
    messages = ConversationalHandler.build_messages(
        SimpleNamespace(name="得到大脑", description="内容整理", prompt="回答问题", response_style="structured", history_length=3),
        [],
        "梳理主题笔记",
    )
    assert "回复风格" in messages[0]["content"]
    assert "结论/摘要" in messages[0]["content"]


def test_quality_gate_is_redacted_and_detects_short_structured_reply():
    result = assess_response_quality(
        "请总结项目现状并梳理三个风险和对应建议",
        "我可以帮你处理。",
        response_style="structured",
    )
    assert result["checked"] is True
    assert result["passed"] is False
    assert "too_short" in result["reasons"]
    assert "项目现状" not in str(result)


def test_conversational_quality_retries_once_without_tools():
    ctx = SimpleNamespace(
        agent=SimpleNamespace(
            id="a1", llm_timeout=30, name="brain", history_length=3,
            memory="", prompt="", response_style="structured",
        ),
        session_id="quality-1", chat_key="a1:quality-1",
        user_message="请梳理项目现状，并总结风险与建议",
        db=_DB(), llm=SimpleNamespace(id="llm1"), mcp_ids=["mcp1"],
        skill_ids=[], rag_ids=[], httpmcp_ids=[], allowed_actions=["mcp_tool_call"],
        profile="standard", code_execution=None, message_meta={}, note_content="",
    )

    async def run():
        events = []
        with patch(
            "app.services.llm_client.chat_completion",
            new=AsyncMock(side_effect=[
                "我可以帮你处理。",
                "结论：项目现状需要分阶段治理。\n\n要点：风险包括依赖、数据和发布，建议先做验证。\n说明：建议先做验证。",
            ]),
        ) as completion, patch("app.services.agent_runtime.hub.hub") as hub:
            hub.publish = AsyncMock(side_effect=lambda _key, event: events.append(event))
            result = await AgentRuntime().run(ctx)
        assert completion.await_count == 2
        assert ctx.message_meta["runtime_metrics"]["quality_retry_count"] == 1
        assert ctx.message_meta["runtime_metrics"]["quality_status"] == "passed"
        assert any(
            event.get("step", {}).get("action") == "quality_check" for event in events
        )
        assert not any(event.get("step", {}).get("action") == "mcp_tool_call" for event in events)
        return result

    result = asyncio.run(run())
    assert result.startswith("结论：")
