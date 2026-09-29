import json
from pathlib import Path
from types import SimpleNamespace
import asyncio
from unittest.mock import AsyncMock, patch

from app.routers.agent_chat import _slim_meta_for_history
from app.services.agent_runtime.runtime import AgentRuntime
from app.services.agent_runtime.unified_output import build_unified_output, visible_reply


ROOT = Path(__file__).resolve().parents[3]


class _Query:
    def filter(self, *args, **kwargs):
        return self

    def order_by(self, *args, **kwargs):
        return self

    def all(self):
        return []

    def first(self):
        return None


class _DB:
    def __init__(self):
        self.rows = []

    def add(self, row):
        self.rows.append(row)

    def commit(self):
        pass

    def query(self, _model):
        return _Query()


def _ctx(db, meta=None):
    return SimpleNamespace(
        agent=SimpleNamespace(id="a1"),
        session_id="s1",
        db=db,
        message_meta=meta if meta is not None else {},
    )


def test_each_exit_saves_markdown_body_and_envelope():
    cases = [
        ("complete", "查询完成，共 3 条。", {"count": 3}, "ok", "data"),
        ("budget", "已完成列表。还缺汇总。", {"pending": ["汇总"]}, "partial", "answer"),
        ("cancel", "[已停止]", {"progress": ["已读取订单"]}, "partial", "answer"),
        ("error", "LLM 服务连续调用失败，任务已暂停，请稍后继续。", None, "error", "error"),
        ("need_input", "当前请求需要人工确认，自动推理已暂停。", None, "need_input", "clarify"),
        ("complete", "你好。", None, "ok", "answer"),
    ]
    for reason, text, structured, status, output_type in cases:
        db = _DB()
        meta = {"unified_output_reason": reason}
        if structured:
            meta["unified_output_structured"] = structured
        stored = AgentRuntime._save_assistant_message(
            _ctx(db, meta),
            text,
            steps=[{"type": "info", "action": "note", "title": "记录", "status": "done"}],
        )
        row = db.rows[-1]
        assert row.role == "assistant"
        assert not row.content.strip().startswith("{")
        assert "version" not in row.content
        assert stored == row.content
        envelope = json.loads(row.meta)["output"]
        assert tuple(envelope.keys()) == ("version", "status", "type", "message", "data", "actions")
        assert envelope["status"] == status
        assert envelope["type"] == output_type
        assert envelope["message"] == row.content
        assert envelope["actions"] == []
        persisted = json.loads(row.meta)
        assert "unified_output_reason" not in persisted
        assert persisted["steps"][0]["type"] == "info"
        assert "version" not in persisted["steps"][0]
        if reason == "cancel":
            assert "停止" in row.content
            assert "已读取订单" in row.content
            assert "[已停止]" not in row.content


def test_in_progress_model_text_is_not_an_assistant_row():
    source = (ROOT / "apps/api/app/services/agent_runtime/runtime.py").read_text(encoding="utf-8")
    assert source.count('role="assistant"') == 1
    web = (ROOT / "apps/web/src/views/AgentChat.vue").read_text(encoding="utf-8")
    assert "messagesAfterPoll(lockBubble, messages.value, incoming)" in web
    assert "const lockBubble = running.value || streaming.value" in web


def test_unbound_mcp_stops_in_human_text_without_calling_the_resource():
    ctx = SimpleNamespace(
        agent=SimpleNamespace(id="a1", llm_timeout=30, name="test"),
        session_id="s5",
        chat_key="a1:s5",
        user_message="使用 okx-trader 查询当前持仓",
        db=_DB(),
        llm=SimpleNamespace(id="llm1"),
        mcp_ids=[],
        skill_ids=[],
        rag_ids=[],
        httpmcp_ids=[],
        allowed_actions=[],
        profile="standard",
        code_execution=None,
        message_meta={},
        note_content="",
    )

    async def run():
        with patch("app.services.llm_client.chat_completion", new=AsyncMock()) as completion, patch(
            "app.services.agent_runtime.hub.hub"
        ) as hub:
            hub.publish = AsyncMock()
            result = await AgentRuntime().run(ctx)
        completion.assert_not_awaited()
        return result

    result = asyncio.run(run())
    row = ctx.db.rows[-1]
    envelope = json.loads(row.meta)["output"]
    assert "未绑定" in result
    assert result == row.content
    assert not result.strip().startswith("{")
    assert envelope["status"] == "error"
    assert envelope["type"] == "error"
    assert envelope["message"] == result
    assert "okx-trader" in result


def test_missing_channel_still_writes_the_session():
    ctx = SimpleNamespace(
        agent=SimpleNamespace(id="a1", llm_timeout=30, name="test"),
        session_id="s4",
        chat_key="a1:s4",
        user_message="请确认是否删除生产数据",
        db=_DB(),
        llm=SimpleNamespace(id="llm1"),
        mcp_ids=["mcp1"],
        skill_ids=[],
        rag_ids=[],
        httpmcp_ids=[],
        allowed_actions=["mcp_tool_call"],
        profile="standard",
        code_execution=None,
        message_meta={"execution_mode": "human_wait"},
        note_content="",
    )

    async def run():
        with patch("app.services.llm_client.chat_completion", new=AsyncMock()) as completion, patch(
            "app.services.agent_runtime.hub.hub"
        ) as hub:
            hub.publish = AsyncMock()
            result = await AgentRuntime().run(ctx)
        completion.assert_not_awaited()
        return result

    result = asyncio.run(run())
    row = ctx.db.rows[-1]
    envelope = json.loads(row.meta)["output"]
    assert "人工确认" in result
    assert result == row.content
    assert envelope["status"] == "need_input"
    assert envelope["message"] == result
    assert not hasattr(ctx, "channel_id") or not ctx.channel_id


def test_channel_send_text_is_the_message_not_the_envelope():
    envelope = build_unified_output("complete", "查询完成，共 3 条。", {"count": 3})
    sent = visible_reply(json.dumps(envelope, ensure_ascii=False))
    assert sent == "查询完成，共 3 条。"
    assert '"version"' not in sent
    assert '"actions"' not in sent
    source = (ROOT / "apps/api/app/services/channels/runtime.py").read_text(encoding="utf-8")
    assert "visible_reply(await run_agent(" in source


def test_history_keeps_output_and_old_rows_keep_content():
    envelope = build_unified_output("partial" if False else "budget", "已完成一部分，还差汇总。")
    slim = _slim_meta_for_history(json.dumps({
        "step_count": 1,
        "steps": [{"type": "info"}],
        "output": envelope,
    }, ensure_ascii=False))
    assert slim["output"]["message"] == "已完成一部分，还差汇总。"
    assert slim["output"]["status"] == "partial"
    assert slim["step_count"] == 1
    old = _slim_meta_for_history(json.dumps({"step_count": 2, "steps": [{}, {}]}))
    assert "output" not in old
    assert old["step_count"] == 2


def test_web_renders_content_when_output_is_absent():
    agent = (ROOT / "apps/web/src/views/AgentChat.vue").read_text(encoding="utf-8")
    group = (ROOT / "apps/web/src/views/GroupChat.vue").read_text(encoding="utf-8")
    helper = (ROOT / "apps/web/src/utils/assistantDisplay.js").read_text(encoding="utf-8")
    assert "assistantVisibleText(m)" in agent
    assert "assistantVisibleText(m)" in group
    assert "return typeof message?.content === 'string' ? message.content : ''" in helper
