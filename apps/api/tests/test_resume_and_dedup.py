"""Resume context injection (task 4.4) + MCP query dedup (task 4.5)."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.services.agent_runtime.loop_state import AgentLoopState
from app.services.agent_runtime.runtime import AgentRuntime, _save_run_state
from app.services.llm_client import ChatResult
from app.services.mcp_client import McpSessionManager


def _make_db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _fake_ctx(db):
    return SimpleNamespace(
        agent=SimpleNamespace(
            id="a1", llm_timeout=30, sandbox_id=None, name="t",
            max_iterations=10, prompt="You are a test agent.", memory="",
        ),
        session_id="s1",
        chat_key="a1:s1",
        user_message="查询视图",
        username="u",
        db=db,
        llm=SimpleNamespace(id="llm1"),
        sandbox=None,
        mcp_ids=["m1"],
        skill_ids=[],
        skill_names=[],
        mcp_names=["ads-mcp"],
        skill_mds=[],
        httpmcp_ids=[],
        rag_ids=[],
        allowed_actions=["mcp_tool_call"],
        save_dir="",
        im_source="",
        note_content="",
        message_meta={},
    )


def test_resume_injects_rendered_plan_into_context():
    db = _make_db()
    ctx = _fake_ctx(db)
    _save_run_state(ctx, AgentLoopState(
        goal="导出 16 列 Excel",
        subtasks=[
            {"text": "步骤1", "status": "done"},
            {"text": "步骤2", "status": "pending"},
        ],
        plan_text="- 视图绑定: view_map={\"a\":\"b\"}",
    ))

    captured_messages: list[list[dict]] = []

    async def _chat(llm, messages, **kwargs):
        captured_messages.append(list(messages))
        # Main loop uses collect_native=True (ChatResult); _reflect_final uses str.
        if kwargs.get("collect_native"):
            return ChatResult(text="FINAL: 完成")
        return "FINAL: 完成"

    async def _run():
        with patch("app.services.llm_client.chat_completion", new=_chat), patch(
            "app.services.agent_runtime.system_prompt.SystemPromptBuilder.build_tools_desc",
            new=AsyncMock(return_value="- MCP: list_ads_views {}"),
        ):
            await AgentRuntime().run(ctx)

    asyncio.run(_run())

    # First LLM call is the main loop; inspect its system messages for the resumed plan.
    system = "\n".join(
        m["content"] for m in captured_messages[0] if m["role"] == "system"
    )
    assert "- [x] 步骤1" in system
    assert "- [ ] 步骤2" in system
    assert "view_map" in system  # raw plan_text carried into context


class _FakeMCP:
    def __init__(self, mid="m1"):
        self.id = mid
        self.protocol = "stdio"
        self.command = "npx"
        self.command_args = ["-y", "x"]
        self.command_env = {}
        self.headers = "{}"


class _FakeStdio:
    instances = []

    def __init__(self, command, args, env):
        _FakeStdio.instances.append(self)
        self.closed = False

    async def start(self):
        return None

    async def close(self):
        self.closed = True

    async def request(self, method, params=None):
        if method == "initialize":
            return {"result": {}}
        return {"result": {"content": [{"type": "text", "text": "BIG" * 4000}]}}

    async def notify(self, method, params=None):
        return None


def test_query_cache_dedup_single_call_and_file(tmp_path):
    _FakeStdio.instances = []
    query_cache: dict = {}
    mcp_results: list = []

    mgr = McpSessionManager(
        query_cache=query_cache, mcp_results=mcp_results, run_ts="1700000000000"
    )
    mcp = _FakeMCP()

    def _root(sid="default"):
        return tmp_path

    with patch("app.services.mcp_client._StdioSession", _FakeStdio):
        with patch("app.services.workplace.workplace_root", _root):
            r1 = asyncio.run(mgr.call_tool(mcp, "query_ads_view", {"view": "x"}))
            r2 = asyncio.run(mgr.call_tool(mcp, "query_ads_view", {"view": "x"}))
            asyncio.run(mgr.close())

    assert len(_FakeStdio.instances) == 1  # session reused, no re-spawn
    assert "mcp_result_0.json" in r1  # oversized result materialized
    assert "已缓存" in r2  # dedup hit, not re-called
    assert len(mcp_results) == 1  # only one result file recorded
    assert (tmp_path / "task" / "1700000000000" / "mcp_result_0.json").exists()
    # The second call must NOT have re-issued tools/call: still a single materialized file.
    assert "mcp_result_1.json" not in r2


def test_query_cache_lru_evicts_oldest(tmp_path):
    _FakeStdio.instances = []
    query_cache: dict = {}
    mcp_results: list = []

    mgr = McpSessionManager(
        query_cache=query_cache, mcp_results=mcp_results,
        run_ts="1700000000000", max_cache_entries=2,
    )
    mcp = _FakeMCP()

    def _root(sid="default"):
        return tmp_path

    with patch("app.services.mcp_client._StdioSession", _FakeStdio):
        with patch("app.services.workplace.workplace_root", _root):
            asyncio.run(mgr.call_tool(mcp, "query_a", {"v": 1}))
            asyncio.run(mgr.call_tool(mcp, "query_b", {"v": 2}))
            asyncio.run(mgr.call_tool(mcp, "query_c", {"v": 3}))
            asyncio.run(mgr.close())

    # Cap enforced: 3 distinct keys → LRU drops the oldest-inserted (query_a).
    assert len(query_cache) == 2
    assert all("query_a" not in k for k in query_cache)
    assert any("query_b" in k for k in query_cache)
    assert any("query_c" in k for k in query_cache)
    assert len(mcp_results) == 3  # every result still materialized on disk
