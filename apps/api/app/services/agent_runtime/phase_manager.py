"""PhaseManager — export state machine phase transitions and guards.

State machine: discover → plan → fetch → analyze → finalize

All methods take AgentLoopState explicitly and are stateless in themselves.
The state is mutated in-place via the state parameter.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from app.services.agent_runtime.loop_state import AgentLoopState
from app.services.agent_runtime.utils import (
    _EXPORT_ANALYZE_HINT,
    _EXPORT_PLAN_HINT,
    _EXPORT_QUERY_BUDGET_DEFAULT,
    _EXPORT_QUERY_BUDGET_FLOOR,
    _EXPORT_QUERY_BUDGET_FULL_FETCH_MAX,
    _KEEP_RECENT_TOOL_MSGS_EXPORT,
    _format_llm_mcp_progress,
    _is_type_b_export,
    _normalize_export_fetch_budget,
)

if TYPE_CHECKING:
    from app.services.export_trace import ExportTrace

logger = logging.getLogger(__name__)


class PhaseManager:
    """Manages export state machine phase transitions.

    All state is read from and written to AgentLoopState explicitly.
    No hidden side effects — every method signature shows what it needs.
    """

    # ---- Phase transition methods ----

    @staticmethod
    def enter_export_plan(
        state: AgentLoopState,
        *,
        reason: str = "",
    ) -> None:
        """Transition discover → plan."""
        if (
            not state.export_like
            or state.plan_done
            or state.export_phase in ("plan", "fetch", "analyze", "finalize")
        ):
            return
        state.export_phase = "plan"
        state.plan_hint_injected = False
        # Mark discover todos done via helper (handled by caller with todo list access)

    @staticmethod
    def enter_export_fetch(
        state: AgentLoopState,
        *,
        budget: int,
        reason: str = "",
        type_b: bool = False,
        pay_cohort: bool = False,
        full_fetch: bool = False,
        fact_page_cap: int | None = None,
        user_page_cap: int | None = None,
    ) -> None:
        """Transition plan → fetch. Computes adaptive budget and caps."""
        state.plan_done = True
        budget_int = int(budget or 0)
        mcp_budget, budget_cap = _normalize_export_fetch_budget(
            requested_budget=budget_int,
            current_cap=state.export_budget_cap,
            type_b=type_b,
            pay_cohort=pay_cohort,
            full_fetch=full_fetch or type_b,
            fact_page_cap=fact_page_cap,
            user_page_cap=user_page_cap,
        )
        state.mcp_query_budget = mcp_budget
        state.export_budget_cap = budget_cap
        state.export_phase = "fetch"

    @staticmethod
    def enter_export_analyze(state: AgentLoopState, *, reason: str = "") -> None:
        """Transition fetch → analyze. Resets analyze state."""
        if not state.export_like or state.export_phase in ("analyze", "finalize"):
            return
        state.export_phase = "analyze"
        state.analyze_hint_injected = False
        state.export_force_finalize = False

    @staticmethod
    def enter_export_finalize(state: AgentLoopState, *, reason: str = "") -> None:
        """Transition to finalize phase. No going back."""
        state.export_phase = "finalize"
        state.export_force_finalize = True

    # ---- Phase guard predicates ----

    @staticmethod
    def should_auto_accept_export_plan(
        state: AgentLoopState,
        *,
        schema_summary: dict | None = None,
    ) -> bool:
        """True when structured export contract can replace a prose PLAN turn."""
        del schema_summary
        if state.export_phase != "plan" or state.plan_done:
            return False
        if state.export_view_mode == "single_view":
            return False
        validation = state.export_task_validation
        if validation is not None:
            status = getattr(validation, "status", "") or ""
            if status and status != "pass":
                return False
        if not any(
            isinstance(c, dict) and c.get("header")
            for c in (state.export_column_plan or [])
        ):
            return False
        return True

    @staticmethod
    def roles_ready_for_analyze(
        state: AgentLoopState,
        *,
        missing_roles: list[str] | None = None,
        has_data: bool = False,
        fact_need_continue: list[str] | None = None,
    ) -> bool:
        """True when plan roles are covered and ready for SHELL analysis."""
        from app.services.agent_runtime.utils import _export_roles_ready_for_analyze

        return _export_roles_ready_for_analyze(
            missing_roles=missing_roles,
            has_data=has_data,
            user_fetch_complete=state.user_fetch_complete,
            user_pages=sum(
                n
                for v, n in (state.fetched_view_pages or {}).items()
                if _is_user_info_view(v)
            ),
            fact_need_continue=fact_need_continue,
            max_user_pages=getattr(state, "export_user_page_cap", 6),
            target_roles=state.export_target_roles,
        )

    @staticmethod
    def still_full_with_budget(
        state: AgentLoopState,
        *,
        fact_need_continue: list[str] | None = None,
    ) -> bool:
        """True when planned roles still look full-page with budget remaining."""
        if fact_need_continue is None:
            # Caller should compute this with full context
            return False
        return bool(fact_need_continue)

    @staticmethod
    def is_in_fetch_phase(state: AgentLoopState) -> bool:
        return state.export_like and state.export_phase == "fetch"

    @staticmethod
    def is_in_plan_phase(state: AgentLoopState) -> bool:
        return state.export_like and state.export_phase == "plan"

    @staticmethod
    def is_in_discover_phase(state: AgentLoopState) -> bool:
        return state.export_like and state.export_phase == "discover"

    @staticmethod
    def is_in_analyze_phase(state: AgentLoopState) -> bool:
        return state.export_like and state.export_phase == "analyze"

    @staticmethod
    def is_in_finalize_phase(state: AgentLoopState) -> bool:
        return state.export_like and state.export_phase == "finalize"

    # ---- Coach message builders ----

    @staticmethod
    def format_plan_hint(state: AgentLoopState, *, plan_hint_text: str = "") -> str:
        """Build the PLAN gate hint message."""
        base = (plan_hint_text or _EXPORT_PLAN_HINT).rstrip()
        bits = [base]
        cols = _export_analyze_column_texts(state.export_todos)
        if cols:
            bits.append(
                "【本轮输出列】请在 PLAN「输出列」中逐条抄写（完整表述，含括号）："
            )
            for i, c in enumerate(cols, 1):
                bits.append(f"{i}.{c}")
        tw = state.export_time_window
        if tw and tw.get("label"):
            bits.append(
                f"【字段与筛选】时间窗必须写：{tw.get('label')}；"
                "user_info 用 sql 过滤 register_time。"
            )
        bits.append(
            "PLAN 完成后请立即按【下一步建议】顺序 MCP query，勿散文收工。"
        )
        return "\n".join(bits)

    @staticmethod
    def format_analyze_hint(state: AgentLoopState) -> str:
        """Build the analyze phase coaching hint."""
        hint = _EXPORT_ANALYZE_HINT
        tw = state.export_time_window
        if tw:
            hint += (
                f"\n时间窗：{tw.get('label')}；"
                f"毫秒 `[{tw.get('start_ms')}, {tw.get('end_ms')})`。"
            )
        cols = _export_analyze_column_texts(state.export_todos)
        if cols:
            hint += "\n完整输出列（须作表头，逐条）：\n" + "\n".join(
                f"{i}.{c}" for i, c in enumerate(cols, 1)
            )
        col_plan = state.export_column_plan
        if col_plan:
            from app.services.export_column_plan import format_column_plan_summary
            hint += "\n\n" + format_column_plan_summary(col_plan)
        return hint

    @staticmethod
    def format_finalize_hint(
        state: AgentLoopState,
        *,
        save_dir: str = "",
        shell_enabled: bool = True,
    ) -> str:
        """Build the finalize-phase coaching hint."""
        from app.services.agent_runtime.utils import _finalize_hint

        return _finalize_hint(
            save_dir or "",
            export_like=state.export_like,
            shell_enabled=shell_enabled,
        )

    @staticmethod
    def discover_deadline(state: AgentLoopState) -> int:
        """Return iteration index at which discover phase times out."""
        from app.services.agent_runtime.utils import _EXPORT_DISCOVER_RATIO

        return max(1, int(state.max_iters * _EXPORT_DISCOVER_RATIO))

    @staticmethod
    def format_phase_progress(
        state: AgentLoopState,
        *,
        llm_iter: int = 0,
        reason: str = "",
    ) -> str:
        """Build a phase progress summary line."""
        return _format_llm_mcp_progress(
            llm_iter=llm_iter or 0,
            max_iters=state.max_iters,
            mcp_query_count=state.mcp_query_count,
            mcp_budget=state.mcp_query_budget,
        )


# ---- Re-exported helper accessors (used by both PhaseManager and react_engine) ----

def _is_user_info_view(view: str) -> bool:
    from app.services.agent_runtime.utils import _is_user_info_view as _f
    return _f(view)


def _export_analyze_column_texts(todos: list[dict] | None) -> list[str]:
    """Extract column header texts from analyze-phase todos."""
    return [
        str(t.get("text") or "").strip()
        for t in (todos or [])
        if isinstance(t, dict)
        and t.get("phase") == "analyze"
        and str(t.get("text") or "").strip()
    ]


def _mark_todos_phase(todos: list[dict], phase: str, done: bool = True) -> None:
    """Mark all todos for a given phase as done/undone."""
    for t in todos:
        if isinstance(t, dict) and t.get("phase") == phase:
            t["done"] = done
