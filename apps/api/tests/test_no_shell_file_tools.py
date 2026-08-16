import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock

from app.services import agent_tools
from app.services.agent_runtime.tool_router import ToolRouter
from app.services.react_engine import (
    _adapt_export_prompt_for_capabilities,
    _build_export_fallback_appendix,
    _export_write_grace_done,
)


def test_read_lists_directory_and_write_creates_parent(tmp_path, monkeypatch):
    monkeypatch.setattr(agent_tools, "_wp_root", lambda _sandbox: tmp_path)
    monkeypatch.setattr(
        agent_tools,
        "format_dir_listing",
        lambda _sid, rel="": "\n".join(
            p.name for p in (tmp_path / rel).iterdir()
        ),
    )
    sandbox = SimpleNamespace(id="s1")

    result = agent_tools._write_workplace(
        sandbox,
        "nodes/2026-08-10/2026-08-10-笔记.md",
        "# 笔记",
    )
    listing = agent_tools._read_workplace(sandbox, "nodes/2026-08-10")

    assert "父目录已自动创建" in result
    assert "2026-08-10-笔记.md" in listing
    assert (tmp_path / "nodes/2026-08-10/2026-08-10-笔记.md").read_text() == "# 笔记"


def test_minimal_tool_catalog_explains_no_shell_mode():
    block = ToolRouter.build_minimal_tools_block(
        allowed_actions=["file_read", "file_write"],
    )

    assert "无 Shell 模式" in block
    assert "自动创建父目录" in block
    assert "SHELL: <command>" not in block


def test_export_prompt_uses_platform_delivery_without_shell():
    prompt = _adapt_export_prompt_for_capabilities(
        "- SHELL 分析步骤:\n拉数后必须用 SHELL 分析落盘；"
        "SHELL 写当前目录交付物。",
        shell_enabled=False,
    )

    assert "SHELL 分析步骤" not in prompt
    assert "必须用 SHELL" not in prompt
    assert "SHELL 写当前目录交付物" not in prompt
    assert "平台" in prompt


def test_no_shell_export_skips_write_grace_immediately():
    assert _export_write_grace_done(
        shell_enabled=False,
        analyze_rounds_used=0,
        analyze_max_rounds=3,
    )
    assert not _export_write_grace_done(
        shell_enabled=True,
        analyze_rounds_used=3,
        analyze_max_rounds=3,
    )


def test_no_shell_fallback_never_tells_user_to_run_shell():
    appendix = _build_export_fallback_appendix(
        missing_roles=["pay", "cash"],
        shell_enabled=False,
    )

    assert "SHELL" not in appendix
    assert "引擎自动关联、写表并校验" in appendix


def test_execute_action_hard_blocks_disabled_shell(monkeypatch):
    called = False

    def _unexpected_shell(*_args, **_kwargs):
        nonlocal called
        called = True
        raise AssertionError("disabled shell must not execute")

    monkeypatch.setattr(agent_tools, "_exec_shell", _unexpected_shell)
    agent = SimpleNamespace(allowed_actions='["file_read", "file_write"]')
    result = asyncio.run(
        agent_tools.execute_action(
            "shell",
            "SHELL: python3 task/report.py",
            MagicMock(),
            agent,
            None,
            [],
            [],
        )
    )

    assert "permission_denied" in result
    assert called is False
