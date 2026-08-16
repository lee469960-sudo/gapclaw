"""ExportOrchestrator — coordinates the export state machine phases.

Discover → Plan → Fetch → Analyze → Finalize

Composes the existing export_*.py modules and PhaseManager,
taking AgentLoopState for explicit state management.

This is the top-level coordinator for export tasks, used by the
AgentRuntime main loop.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.services.agent_runtime.loop_state import AgentLoopState
    from app.services.agent_runtime.phase_manager import PhaseManager

logger = logging.getLogger(__name__)


class ExportOrchestrator:
    """Coordinates the five-phase export state machine.

    Does NOT own the loop — the main ReAct loop calls these methods
    at the appropriate points, passing AgentLoopState explicitly.

    Each phase method delegates to the existing export_*.py domain modules
    and updates AgentLoopState in-place.
    """

    # ---- Discover phase ----

    @staticmethod
    def should_run_discovery(state: AgentLoopState) -> bool:
        """True when schema discovery actions should be executed now."""
        return (
            state.export_like
            and state.export_phase == "discover"
            and bool(state.export_column_plan)
        )

    @staticmethod
    async def run_schema_discovery(
        state: AgentLoopState,
        *,
        db,
        agent,
        sandbox,
        skill_ids: list[str],
        mcp_ids: list[str],
        httpmcp_ids: list[str],
        rag_ids: list[str],
        push_step=None,
        patch_last_step=None,
    ) -> int:
        """Execute schema discovery actions (list/describe) before PLAN/fetch.

        Returns the number of discovery actions executed.
        """
        if not state.export_like or not sandbox or not state.export_run_id:
            return 0

        from app.services.export_column_plan import build_query_graph
        from app.services.export_schema_discovery import build_schema_discovery_actions
        from app.services.export_trace import write_export_trace

        repair_plan = (
            state.export_trace.repair_plan
            if isinstance(getattr(state.export_trace, "repair_plan", None), dict)
            else {}
        )

        planned_views = [
            str(n.get("view") or "").strip()
            for n in build_query_graph(
                state.export_column_plan,
                state.export_time_window,
                schema_fields_by_view={
                    str(v): list(getattr(h, "fields", None) or [])
                    for v, h in (state.ads_schema_hints or {}).items()
                    if v and getattr(h, "fields", None)
                },
                bound_only=True,
            )
            if isinstance(n, dict) and str(n.get("view") or "").strip()
        ]

        actions = build_schema_discovery_actions(
            repair_plan=repair_plan,
            schema_discovery=(
                state.export_trace.schema_discovery
                if isinstance(getattr(state.export_trace, "schema_discovery", None), dict)
                else state.export_schema_discovery
            ),
            planned_views=planned_views,
            schema_hints=state.ads_schema_hints,
        )
        if not actions:
            return 0

        from app.services.agent_tools import execute_action

        count = 0
        for action in actions:
            view = str(action.get("view") or "")
            normalized = str(action.get("mcp_call") or "")
            action_type = str(action.get("action_type") or "")
            if not normalized:
                continue

            if push_step:
                if action_type == "discover_ads_views":
                    await push_step({
                        "type": "tool", "action": "mcp_tool_call",
                        "title": "RepairPlan · list_ads_views",
                        "content": "【schema 修复】自动获取 MCP 接口/视图列表",
                        "status": "running",
                    })
                else:
                    await push_step({
                        "type": "tool", "action": "mcp_tool_call",
                        "title": f"RepairPlan · describe {view}",
                        "content": f"【schema 修复】自动获取 `{view}` 字段/表备注",
                        "status": "running",
                    })

            try:
                result = await execute_action(
                    "mcp_tool_call", normalized,
                    db, agent, sandbox,
                    skill_ids, mcp_ids, httpmcp_ids, rag_ids,
                ) or ""
            except Exception as ex:
                if patch_last_step:
                    await patch_last_step(status="error", content=str(ex)[:400])
                continue

            if result and action_type == "discover_ads_views":
                from app.services.export_schema_discovery import record_schema_list
                state.ads_views_list_text = result
                state.export_schema_discovery = record_schema_list(
                    state.export_trace, result,
                )

            if sandbox and state.export_run_id:
                write_export_trace(sandbox.id, state.export_run_id, state.export_trace)

            if patch_last_step:
                await patch_last_step(status="done", content=f"`{view}` schema 已更新" if view else "schema 已更新")
            count += 1

        return count

    # ---- Plan phase ----

    @staticmethod
    def check_plan_auto_accept(state: AgentLoopState) -> bool:
        """Check if the plan can be auto-accepted without LLM review.

        Uses structured contract validation to skip a prose PLAN turn.
        """
        from app.services.export_schema_discovery import export_summarize_schema_discovery

        schema_now = export_summarize_schema_discovery(
            schema_discovery=(
                state.export_trace.schema_discovery
                if isinstance(getattr(state.export_trace, "schema_discovery", None), dict)
                else state.export_schema_discovery
            ),
            export_contract=(
                state.export_trace.export_contract
                if isinstance(getattr(state.export_trace, "export_contract", None), dict)
                else None
            ),
        )

        from app.services.agent_runtime.phase_manager import PhaseManager

        return PhaseManager.should_auto_accept_export_plan(
            state, schema_summary=schema_now,
        )

    # ---- Fetch phase ----

    @staticmethod
    def check_roles_ready(state: AgentLoopState) -> bool:
        """Check if all planned roles have been fetched."""
        from app.services.agent_runtime.phase_manager import PhaseManager

        return PhaseManager.roles_ready_for_analyze(state)

    @staticmethod
    def get_missing_roles(
        state: AgentLoopState,
        *,
        sandbox_id: str = "",
    ) -> list[str]:
        """Get list of export roles still missing data."""
        from app.services.react_engine import _missing_export_roles

        sid = sandbox_id or ""
        if not sid or not state.export_run_id:
            return list(state.export_target_roles or [])

        try:
            return _missing_export_roles(
                sid,
                state.export_run_id,
                state.export_target_roles,
                abandoned_roles=state.export_abandoned_roles,
            )
        except Exception:
            return list(state.export_target_roles or [])

    @staticmethod
    def format_missing_roles_note(
        state: AgentLoopState,
        *,
        missing: list[str] | None = None,
        budget_left: int = 0,
        full_views: list[str] | None = None,
    ) -> str:
        """Format a human-readable note about missing roles."""
        from app.services.react_engine import _format_missing_roles_note

        return _format_missing_roles_note(
            missing or [],
            budget_left=budget_left,
            full_views=full_views or [],
            dim_views=state.export_dim_views or None,
        )

    # ---- Finalize phase ----

    @staticmethod
    def try_finalize(
        state: AgentLoopState,
        *,
        reason: str = "",
        prefer_fallback: bool = False,
    ) -> bool:
        """Attempt to finalize the export (platform write or hard finish).

        Returns True if finalization was successful.
        """
        from app.services.agent_runtime.phase_manager import PhaseManager

        if state.export_phase == "finalize":
            return True
        PhaseManager.enter_export_finalize(state, reason=reason)
        return False

    # ---- Phase progress ----

    @staticmethod
    def format_progress(
        state: AgentLoopState,
        *,
        llm_iter: int = 0,
        reason: str = "",
    ) -> str:
        """Format a phase progress summary line."""
        from app.services.agent_runtime.phase_manager import PhaseManager

        return PhaseManager.format_phase_progress(
            state, llm_iter=llm_iter, reason=reason,
        )
