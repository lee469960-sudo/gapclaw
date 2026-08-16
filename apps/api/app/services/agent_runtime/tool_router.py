"""ToolRouter — build the tool catalog description for the system prompt.

No domain/phase filtering: the full set of enabled actions is always exposed,
and the LLM decides which tool to use each round.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.agent.models import Agent
    from sqlmodel import Session


class ToolRouter:
    """Builds compact tool description blocks for the system prompt."""

    @staticmethod
    async def build_tools_block(
        db: Session,
        agent: Agent,
        allowed_actions: list[str],
        skill_ids: list[str],
        mcp_ids: list[str],
        rag_ids: list[str],
        *,
        save_dir: str = "",
        im_source: str = "",
    ) -> str:
        """Build the full tool catalog block (delegates to SystemPromptBuilder)."""
        from app.services.agent_runtime.system_prompt import SystemPromptBuilder

        return await SystemPromptBuilder.build_tools_desc(
            db=db,
            agent=agent,
            allowed=allowed_actions,
            skill_ids=skill_ids,
            mcp_ids=mcp_ids,
            rag_ids=rag_ids,
            save_dir=save_dir,
            im_source=im_source,
        )

    @staticmethod
    def build_minimal_tools_block(
        *,
        save_dir: str = "",
        allowed_actions: list[str] | None = None,
        mcp_ids: list[str] | None = None,
        db: Session | None = None,
    ) -> str:
        """Build a minimal tools block as a fallback when the full catalog fails.

        Only includes essential tools (READ, WRITE, SHELL, FINAL) without MCP details.
        When mcp_ids are provided, includes a minimal MCP hint so the LLM knows MCP is available.
        """
        actions = set(allowed_actions or [])
        lines = [
            "【可用工具·精简模式】",
            "【格式】每次只输出一种工具调用，且必须从行首开始。",
        ]
        if save_dir:
            lines.append(
                f"【路径规则】最终交付文件写入 `{save_dir}/`；过程产物写入 `task/<毫秒时间戳>/`。"
            )
        if "shell" in actions:
            lines.append("- SHELL: <POSIX /bin/sh command>（禁止混入思考或解释文字）")
        if "file_read" in actions:
            lines.append("- READ: <path>（文件可读内容；目录可列出条目；READ: workplace 根目录）")
        if "file_write" in actions:
            lines.append("- WRITE: <path>\\n<content>（自动创建父目录，无需 mkdir）")
        if "shell" not in actions and {"file_read", "file_write"} & actions:
            lines.append(
                "【无 Shell 模式】目录浏览和文本落盘仅使用 READ/WRITE；"
                "不要输出 SHELL，也不要因缺少 mkdir/ls 中止任务。"
            )
        if mcp_ids and "mcp_tool_call" in actions:
            try:
                from app.models import MCP
                for mid in mcp_ids:
                    mcp = db.query(MCP).filter(MCP.id == mid).first() if db else None
                    name = (mcp.name if mcp else mid[:12]) if (mcp or mid) else "unknown"
                    lines.append(
                        f"- mcp {name}: MCP 工具（MCP: <tool_name> {{args}}）。"
                        "请参考已绑定 MCP 的文档使用正确的工具名和参数。"
                    )
            except Exception:
                lines.append("- mcp: MCP 工具已绑定（详情暂不可用，请重试或检查 MCP 连接）")
        lines.append("- FINAL: <answer>")
        return "\n".join(lines)
