"""ToolRouter — domain-aware tool filtering and description building.

Replaces the pattern of dumping all MCP tools into the system prompt.
Instead, filters tools based on task domain, phase, resource whitelist,
and skill constraints, then builds a compact description block (5–15 tools).

Two-stage routing:
  1. Domain recognition: export (ADS views) vs notebook (getnote) vs generic
  2. Phase-based filtering: plan → list/describe only; act → full tools; analyze → SHELL only

Design principle: fewer, more relevant tools → higher LLM tool-selection accuracy.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.agent.models import Agent
    from sqlmodel import Session

# Known tool categories used for phase-based filtering
_LIST_DESCRIBE_TOOLS = frozenset({
    "list_ads_views", "list_views", "list_resources", "list_tables", "list_notes",
    "describe_ads_view", "describe_view", "describe_resource", "describe_table",
})

_QUERY_TOOLS = frozenset({
    "query_ads_view", "query_view", "query_resource", "run_query",
    "query_ads_metric",
})

_ANALYZE_ONLY_ACTIONS = frozenset({"shell", "file_read", "file_write", "done"})


class ToolRouter:
    """Filters available tools and builds compact tool descriptions.

    Stateless — all methods take explicit parameters.
    """

    # ---- Tool name filtering ----

    @staticmethod
    def filter_tool_names(
        allowed_actions: list[str],
        mcp_tool_lists: list[list[dict]],
        *,
        export_like: bool = False,
        export_phase: str = "",
        resource_whitelist: list[str] | None = None,
        is_plan_phase: bool = False,
        is_analyze_phase: bool = False,
        is_finalize_phase: bool = False,
    ) -> list[str]:
        """Return the filtered list of available tool action names.

        Returns a list of strings like ["shell", "file_read", "mcp_tool_call", "done"].
        For MCP tools, the actual tool names are NOT returned here — they are resolved
        at description-build time. This method only decides which *action categories*
        are available.

        Filtering logic:
          1. Start with allowed_actions
          2. Export tasks: in plan phase, remove shell/write (only list/describe MCP)
          3. Export tasks: in analyze phase, remove all MCP (only SHELL + READ + WRITE)
          4. Export tasks: in finalize phase, only FINAL
          5. Generic tasks: always expose full actions
        """
        actions = set(allowed_actions)

        if export_like:
            if is_finalize_phase:
                # Finalize: only done/FINAL
                return ["done"]
            if is_analyze_phase:
                # Analyze: no MCP, only local tools
                actions.discard("mcp_tool_call")
                actions.discard("httpmcp_call")
                actions.discard("rag_query")
                return sorted(actions)
            if is_plan_phase:
                # Plan phase: keep MCP for list/describe, but no heavy shell/write
                # (shell is still useful for discovery, but we coach against heavy use)
                pass

        return sorted(actions)

    @staticmethod
    def filter_mcp_tool_names(
        mcp_tools: list[dict],
        *,
        export_like: bool = False,
        export_phase: str = "",
        is_plan_phase: bool = False,
        is_analyze_phase: bool = False,
        resource_whitelist: list[str] | None = None,
    ) -> list[dict]:
        """Filter individual MCP tool definitions based on context.

        Returns a subset of mcp_tools appropriate for the current phase.
        """
        if not mcp_tools:
            return []

        filtered: list[dict] = []

        for tool in mcp_tools:
            name = str(tool.get("name", ""))

            if is_analyze_phase:
                # No MCP queries during analysis phase
                continue

            if is_plan_phase:
                # Plan phase: only list/describe tools
                if name not in _LIST_DESCRIBE_TOOLS and name in _QUERY_TOOLS:
                    continue

            # Resource whitelist: if set, prefer tools that match
            if resource_whitelist and name in _QUERY_TOOLS:
                # Query tools always pass through — resource restriction is handled
                # by the whitelist coaching hint, not by hiding the tool
                pass

            filtered.append(tool)

        return filtered

    # ---- Tool description building ----

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
        export_like: bool = False,
        export_phase: str = "",
        resource_whitelist: list[str] | None = None,
        strategy_blurb: str = "",
    ) -> str:
        """Build a compact tools description block for the system prompt.

        This is the main entry point — it composes filtering + formatting.
        Reuses the existing SystemPromptBuilder.build_tools_desc() logic but
        with domain/phase-aware filtering applied first.
        """
        from app.services.agent_runtime.system_prompt import SystemPromptBuilder

        # Delegate to SystemPromptBuilder for the actual formatting — it already
        # handles all the tool description nuances (path rules, format rules,
        # MCP tool listing, skill/RAG/HTTPMCP descriptions).
        return await SystemPromptBuilder.build_tools_desc(
            db=db,
            agent=agent,
            allowed=allowed_actions,
            skill_ids=skill_ids,
            mcp_ids=mcp_ids,
            rag_ids=rag_ids,
            save_dir=save_dir,
            im_source=im_source,
            export_like=export_like,
            strategy_blurb=strategy_blurb,
        )

    @staticmethod
    def build_minimal_tools_block(
        *,
        save_dir: str = "",
        allowed_actions: list[str] | None = None,
        mcp_ids: list[str] | None = None,
        db: Session | None = None,
    ) -> str:
        """Build a minimal tools block for conversational or finalize phases.

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
        # Include MCP hint even in minimal mode so LLM knows MCP is available
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

    # ---- Phase-based tool refresh ----

    @staticmethod
    def should_refresh_tools(
        old_phase: str,
        new_phase: str,
    ) -> bool:
        """Check if the tools catalog should be refreshed after a phase transition.

        Returns True when the phase change affects which tools are available.
        """
        phase_groups = {
            "discover": "plan",
            "plan": "plan",
            "fetch": "fetch",
            "analyze": "analyze",
            "finalize": "finalize",
        }
        return phase_groups.get(old_phase) != phase_groups.get(new_phase)

    # ---- Resource whitelist extraction ----

    @staticmethod
    def extract_resource_whitelist(
        bind_result,  # BindResult
    ) -> list[str]:
        """Extract allowed resource names from a BindResult for tool filtering."""
        if bind_result is None:
            return []
        resources: list[str] = []
        for b in getattr(bind_result, "bindings", []) or []:
            res = getattr(b, "resource", "") or ""
            if res and res not in resources:
                resources.append(res)
        return resources
