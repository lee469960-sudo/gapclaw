"""RepairContext — builds repair hints for export gap-fill and column-fill.

Composes existing export_repair_plan, export_fill_scope, and run_state
modules to build the repair context used by the export orchestrator.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.services.agent_runtime.loop_state import AgentLoopState


class RepairContext:
    """Builds repair context for export gap-fill and column-fill runs.

    All methods are static and operate on AgentLoopState explicitly.
    """

    @staticmethod
    def resolve_source_brief(
        state: AgentLoopState,
        *,
        prior_state: dict | None = None,
        prior_run_id: str = "",
        user_message: str = "",
    ) -> str:
        """Resolve the repair source brief from prior state or user message."""
        from app.services.react_engine import _resolve_repair_source_brief

        return _resolve_repair_source_brief(
            prior_state=prior_state,
            prior_run_id=prior_run_id,
            user_message=user_message,
            task_brief=state.task_brief,
        )

    @staticmethod
    def build_system_hint(
        state: AgentLoopState,
        *,
        prior_state: dict | None = None,
        prior_run_id: str = "",
        session_id: str = "",
    ) -> str:
        """Build the repair system hint for injection into the prompt."""
        if prior_state is None or not prior_run_id:
            return ""

        ps = prior_state if isinstance(prior_state, dict) else {}
        same_session = prior_run_id == state.export_run_id or str(
            ps.get("session_id") or ""
        ) == session_id

        parts: list[str] = []
        if ps.get("completeness"):
            parts.append(f"上轮完整性={ps.get('completeness')}")
        if ps.get("missing_columns"):
            parts.append(
                "上轮缺列：" + "、".join(str(c) for c in ps.get("missing_columns", []))
            )
        if ps.get("fact_truncated_roles"):
            parts.append(
                "上轮事实截断：" + "、".join(str(r) for r in ps.get("fact_truncated_roles", []))
            )
        if ps.get("need_continue_roles"):
            parts.append(
                "上轮需续翻：" + "、".join(str(r) for r in ps.get("need_continue_roles", []))
            )
        digest = str(ps.get("digest") or "").strip()
        if digest:
            parts.append(f"上轮摘要：{digest[:300]}")

        if not parts:
            return ""
        header = "【上轮续修·同窗】" if same_session else "【上轮续修·跨窗】"
        return header + "\n" + "\n".join(parts)

    @staticmethod
    def is_repair_intent(user_message: str) -> bool:
        """True when the user message signals an export gap/column repair."""
        from app.services.react_engine import _is_export_repair_intent
        return _is_export_repair_intent(user_message)

    @staticmethod
    def find_base_run_state(
        sandbox_id: str,
        *,
        session_id: str = "",
    ) -> tuple[str, dict] | None:
        """Find the prior export run state for repair context."""
        from app.services.react_engine import find_repair_base_run_state
        return find_repair_base_run_state(sandbox_id, session_id=session_id)

    @staticmethod
    def prior_headers(state_dict: dict | None) -> list[str]:
        """Extract deliverable headers from a prior run state dict."""
        from app.services.react_engine import prior_headers_from_state
        return prior_headers_from_state(state_dict)
