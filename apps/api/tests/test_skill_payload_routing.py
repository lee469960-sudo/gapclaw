"""Exercise the complete request that previously stopped before the ReAct loop."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import ChatMessage, Skill
from app.services.agent_runtime.execution_policy import (
    ExecutionMode, classify_request, extract_named_resource_mentions, requires_human_wait,
)
from app.services.agent_runtime.runtime import AgentRuntime
from app.services.llm_client import ChatResult
from app.services.skill_loader import load_skill_mds


INSTRUCTION = """制作一个SKILL包：
  1. 先调用 SKILL_MD 读取 SKILL.md；
  2. 根据下面内容创建完整 Skill 包；
  3. 使用 WRITE 写入工作区；
  4. 返回实际生成的文件路径。
"""
POLICY = """## Agent 追问规则

当收到任务后，先判断现有信息是否足以执行。

### 1. 可以直接执行时

如果已经具备完成任务所需的信息：

* 不要追问。
* 不要重复确认用户已经提供的信息。
* 根据现有上下文自行做合理判断。
* 优先直接执行并给出结果。

### 2. 必须追问时

只有以下情况才允许追问：

* 缺少关键参数，导致任务无法执行。
* 存在两个或以上差异很大的方案，且选择会造成明显影响。
* 涉及生产环境、数据删除、覆盖、发布、回滚、权限、安全等高风险操作。
* 无法从代码、配置、日志、环境变量或已有上下文中自行确认。

### 3. 追问方式

一次最多提出 **1～3 个关键问题**。

问题必须：

* 简短。
* 明确。
* 可直接回答。
* 说明为什么需要这个信息。
* 尽量提供默认选项。

例如：

> 需要确认 2 个信息后才能继续：
>
> 1. 当前操作环境是 dev / pre / prod？
> 2. 是否允许自动修改并重启服务？
>
> 如果没有特殊要求，我默认使用 dev，并允许自动修改和重启。

### 4. 禁止无意义追问

不要询问：

* “是否继续？”
* “需要我执行吗？”
* “这样可以吗？”
* “要不要我帮你修改？”
* 已经从上下文中能够确定的问题。

用户已经明确要求完成任务时，默认就是授权你继续完成正常范围内的操作。

### 5. 不确定但风险较低

如果信息不完整，但存在合理默认值：

1. 明确写出假设。
2. 使用最安全、最小影响的方案。
3. 继续执行。
4. 最后说明使用了哪些假设。

格式：

> 假设：xxx
> 基于该假设继续执行。

### 6. 高风险操作

对于以下操作必须先验证：

* production 发布
* DROP / DELETE / TRUNCATE
* 覆盖配置
* 删除 Docker Volume
* 强制 Git push
* 修改数据库权限
* 修改 Secret / Token
* 数据迁移
* 服务大范围重启

优先先执行：

* inspect
* diff
* dry-run
* validate
* backup/checkpoint

只有缺少无法自行判断的关键决策时，再向用户追问。

## 核心原则

**能查就查，能判断就判断，能安全执行就执行；只有真正阻塞任务时才追问用户。**

追问不是默认流程，而是最后手段。
"""


@pytest.mark.parametrize("fenced", [True, False])
def test_complete_skill_request_is_task_and_not_unbound_or_human_wait(fenced):
    request = INSTRUCTION + (f"```\n{POLICY}```" if fenced else POLICY)
    assert classify_request(request, skill_names=["skill-creator"]) is ExecutionMode.TASK
    assert not requires_human_wait(request)
    assert extract_named_resource_mentions(request) == []


@pytest.mark.parametrize("message,metadata", [
    ("创建一个 Skill 包，然后发布到生产环境", {}),
    (INSTRUCTION + f"```\n{POLICY}```", {"requires_human_confirmation": True}),
])
def test_skill_creation_does_not_bypass_actual_risk_or_explicit_confirmation(message, metadata):
    assert requires_human_wait(message, metadata)


@pytest.mark.parametrize("fenced,named_skill", [(True, False), (False, False), (True, True)])
def test_complete_request_enters_react_reads_bound_skill_and_writes_file(tmp_path, monkeypatch, fenced, named_skill):
    # Real Skill loader, tool parsing/execution, filesystem write and message
    # persistence; only external LLM and final reflection are substituted.
    from app.config import get_settings

    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("REACT_BENCH_WORKSPACE_ROOT", str(tmp_path / "workplace"))
    get_settings.cache_clear()
    root = tmp_path / "skills" / "skill1" / "skill-creator"
    root.mkdir(parents=True)
    (root / "SKILL.md").write_text("# skill-creator\nCreate a Skill package.", encoding="utf-8")
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    db.add(Skill(id="skill1", name="skill-creator", zip_path="test.zip"))
    db.commit()
    ctx = SimpleNamespace(
        agent=SimpleNamespace(id="skill-agent", name="test", prompt="", memory="",
                              llm_timeout=30, history_length=3, sandbox_id=None, max_iterations=5),
        session_id="full-request", chat_key="skill-agent:full-request", username="u",
        user_message=("使用 skill-creator。\n" if named_skill else "") + INSTRUCTION
        + (f"```\n{POLICY}```" if fenced else POLICY), db=db,
        llm=SimpleNamespace(id="llm1", provider="openai"), sandbox=None,
        skill_ids=["skill1"], skill_names=["skill-creator"],
        skill_mds=load_skill_mds(db, ["skill1"]), mcp_ids=[], mcp_names=[],
        httpmcp_ids=[], rag_ids=[], allowed_actions=["skill_read_md", "file_write"],
        profile="standard", code_execution=None, message_meta={}, note_content="",
        save_dir="", im_source="", model_route_fallbacks=[],
    )
    completion = AsyncMock(side_effect=[
        ChatResult(text="SKILL_MD: skill-creator"),
        ChatResult(text=f"WRITE: agent-follow-up-rules/SKILL.md\n{POLICY}"),
        ChatResult(text="FINAL: 已生成 agent-follow-up-rules/SKILL.md"),
    ])
    try:
        with patch("app.services.llm_client.chat_completion", new=completion), patch.object(
            AgentRuntime, "_run_conversational", new=AsyncMock()
        ) as chat, patch.object(
            AgentRuntime, "_reflect_final", new=AsyncMock(return_value=None)
        ), patch("app.services.agent_runtime.hub.hub.publish", new=AsyncMock()):
            result = asyncio.run(AgentRuntime().run(ctx))
        chat.assert_not_awaited()
        assert "已生成" in result
        assert completion.await_count == 3
        assert ctx.message_meta["runtime_metrics"]["route_mode"] == "task"
        assert (tmp_path / "workplace" / "agent-follow-up-rules" / "SKILL.md").read_text() == POLICY.rstrip()
        assert "Create a Skill package" in str(completion.await_args_list[0].args[1])
        message = db.query(ChatMessage).filter(ChatMessage.role == "assistant").first()
        steps = json.loads(message.meta)["steps"]
        assert {"skill_loaded", "skill_read_md", "file_write"}.issubset({s.get("action") for s in steps})
        assert all(s["status"] == "done" for s in steps if s.get("type") == "tool")
    finally:
        db.close()
        engine.dispose()
        get_settings.cache_clear()
