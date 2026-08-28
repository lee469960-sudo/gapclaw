"""react-engine-v7 regression tests.

Covers the change's four requirement groups:
  R1 (agent-runtime) MCP pagination params surfaced + same-turn fill-in guidance
  R2 (agent-runtime) attach= delivery annotation guidance
  R3 (agent-runtime) independent 【查询效率】 prompt section
  R4 (channels)   TG attachment send (explicit attach paths + newest-file fallback)
"""

from __future__ import annotations

import asyncio
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.services.agent_runtime.utils import _format_mcp_tools_for_prompt
from app.services.channels.reply import (
    format_im_completion_reply,
    parse_attach_paths,
    resolve_attach_paths,
    strip_attach_markers,
)


# ---- R1: MCP pagination params surfaced (task 5.1) ----

def test_format_mcp_tools_surfaces_pagination_params():
    tools = [
        {"name": "query_ads_view", "description": "query a view",
         "inputSchema": {
             "type": "object",
             "properties": {
                 "view": {"type": "string"},
                 "offset": {"type": "integer"},
                 "limit": {"type": "integer"},
                 "page_size": {"type": "integer"},
             },
             "required": ["view"],  # offset/limit/page_size are non-required
         }},
    ]
    joined = "\n".join(_format_mcp_tools_for_prompt("ads", tools))
    assert "分页参数=[" in joined
    assert "offset" in joined
    assert "limit" in joined
    assert "page_size" in joined


def test_format_mcp_tools_no_pagination_when_absent():
    tools = [{"name": "get_note", "description": "fetch a note"}]
    joined = "\n".join(_format_mcp_tools_for_prompt("notes", tools))
    assert "分页参数" not in joined


# ---- R2 / R4: attach annotation parsing (task 5.2) ----

def test_parse_attach_single_inline():
    assert parse_attach_paths("报告完成\nattach=task/1/report.xlsx") == ["task/1/report.xlsx"]


def test_parse_attach_multi_inline():
    assert parse_attach_paths("done\nattach=a.xlsx,b.xlsx") == ["a.xlsx", "b.xlsx"]


def test_parse_attach_standalone_line():
    assert parse_attach_paths("done\nATTACH: a.xlsx, b.xlsx") == ["a.xlsx", "b.xlsx"]


def test_parse_attach_none_when_absent():
    assert parse_attach_paths("done, no files here") == []


def test_parse_attach_strips_backticks():
    assert parse_attach_paths("attach=`task/1/a.xlsx`") == ["task/1/a.xlsx"]


def test_strip_attach_markers_removes_annotation():
    text = "报告完成\nattach=task/1/report.xlsx"
    assert strip_attach_markers(text) == "报告完成"


def test_strip_attach_removes_standalone_line():
    text = "报告完成\nATTACH: a.xlsx\n谢谢"
    assert strip_attach_markers(text) == "报告完成\n谢谢"


def test_resolve_attach_paths_rejects_traversal(tmp_path):
    (tmp_path / "report.xlsx").write_bytes(b"x")
    with patch("app.services.workplace._wp_root", return_value=tmp_path):
        out = resolve_attach_paths(
            ["../../etc/passwd", "report.xlsx", "/etc/passwd"],
            "sandbox1",
        )
    assert out == ["report.xlsx"]


# ---- R4: TG multi-attach send + newest-file fallback (task 5.3) ----

def test_resolve_attach_paths_multiple_valid(tmp_path):
    (tmp_path / "a.csv").write_bytes(b"x")
    (tmp_path / "b.csv").write_bytes(b"x")
    with patch("app.services.workplace._wp_root", return_value=tmp_path):
        out = resolve_attach_paths(["a.csv", "b.csv", "missing.csv"], "sandbox1")
    assert out == ["a.csv", "b.csv"]


def test_push_channel_documents_sends_multiple(tmp_path):
    from app.services.channels import runtime as crt

    (tmp_path / "a.csv").write_bytes(b"x")
    (tmp_path / "b.csv").write_bytes(b"x")

    sent: list[str] = []

    class _Adapter:
        async def send_document(self, chat_id, file_path, caption=None):
            sent.append(str(file_path))

    channel = SimpleNamespace(id="ch1", provider="telegram", get_config=lambda: {})
    with patch("app.services.workplace._wp_root", return_value=tmp_path), \
         patch("app.services.channels.runtime.create_adapter", return_value=_Adapter()), \
         patch("app.services.channels.runtime.log_event"):
        out = asyncio.run(crt.push_channel_documents(
            None, channel, "123", "sandbox1", ["a.csv", "b.csv"],
        ))
    assert out == ["a.csv", "b.csv"]
    assert len(sent) == 2


def test_format_im_completion_reply_fallback_picks_newest(tmp_path):
    a = tmp_path / "a.csv"
    b = tmp_path / "b.csv"
    a.write_bytes(b"x")
    b.write_bytes(b"x")
    os.utime(a, (1000, 1000))
    os.utime(b, (2000, 2000))
    with patch("app.services.workplace._wp_root", return_value=tmp_path):
        text, paths = format_im_completion_reply(
            "done", saved_paths=["a.csv", "b.csv"], sandbox_id="sandbox1",
        )
    assert text == "done"
    assert paths == ["b.csv"]  # newest root csv wins when no attach=


# ---- R1/R2/R3 prompt assertions (task 5.4) ----

class _FakeQuery:
    def __init__(self, result):
        self._result = result

    def filter(self, *a, **k):
        return self

    def first(self):
        return self._result


class _FakeDB:
    def __init__(self, mcp=None, skill=None):
        self._mcp = mcp
        self._skill = skill

    def query(self, model):
        name = getattr(model, "__name__", "")
        if name == "MCP":
            return _FakeQuery(self._mcp)
        if name == "Skill":
            return _FakeQuery(self._skill)
        return _FakeQuery(None)


def test_system_prompt_has_query_efficiency_pagination_attach():
    from app.services.agent_runtime.system_prompt import SystemPromptBuilder

    fake_mcp = SimpleNamespace(id="m1", name="ads")
    tool = {
        "name": "query_ads_view", "description": "query",
        "inputSchema": {
            "type": "object",
            "properties": {"offset": {"type": "integer"}, "limit": {"type": "integer"}},
            "required": ["view"],
        },
    }
    db = _FakeDB(mcp=fake_mcp)

    async def _go():
        with patch(
            "app.services.agent_runtime.utils._get_mcp_tools_cached",
            new=AsyncMock(return_value=([tool], "")),
        ):
            return await SystemPromptBuilder.build_tools_desc(
                db=db,
                agent=SimpleNamespace(),
                allowed=["mcp_tool_call", "shell", "file_read", "file_write"],
                skill_ids=[], mcp_ids=["m1"], rag_ids=[],
                save_dir="", im_source="",
            )

    text = asyncio.run(_go())
    assert "【查询效率】" in text
    assert "【分页补齐】" in text
    assert "【附件标注】" in text
    assert "attach=" in text
