"""PaginationEngine — auto-OFFSET pagination for ClickHouse MCP queries.

Coordinates with the fetch phase to automatically page through results
when a view's last page is full (indicating more data exists).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.services.agent_runtime.loop_state import AgentLoopState


class PaginationEngine:
    """Manages auto-OFFSET pagination for export data fetches.

    All methods are static and take AgentLoopState explicitly.
    Delegates to existing export modules for domain logic.
    """

    # ---- Page cap computation ----

    @staticmethod
    def page_limit_for_view(view: str | None = None) -> int:
        """Return the per-page LIMIT for a given view."""
        from app.services.react_engine import _page_limit_for_view

        return _page_limit_for_view(view)

    @staticmethod
    def max_pages_for_view(
        view: str,
        *,
        pay_cohort: bool = False,
        fact_page_cap: int = 0,
        user_page_cap: int = 0,
    ) -> int:
        """Max pages allowed for a view under current caps."""
        from app.services.agent_runtime.utils import _max_pages_for_view

        return _max_pages_for_view(
            view,
            pay_cohort=pay_cohort,
            fact_page_cap=fact_page_cap or None,
            user_page_cap=user_page_cap or None,
        )

    @staticmethod
    def is_full_page_rows(
        row_count: int,
        *,
        view: str | None = None,
        page_limit: int | None = None,
    ) -> bool:
        """True when the last page has max rows, indicating more data."""
        from app.services.agent_runtime.utils import _is_full_page_rows

        lim = page_limit or PaginationEngine.page_limit_for_view(view)
        return _is_full_page_rows(row_count, view=view, page_limit=lim)

    # ---- OFFSET continuation ----

    @staticmethod
    def should_auto_offset_continue(
        state: AgentLoopState,
        *,
        last_page_rows: int,
        pages_done: int,
        page_cap: int,
        budget_left: int,
        auto_done: int,
        view: str | None = None,
    ) -> bool:
        """True when engine should fire another same-view OFFSET page."""
        from app.services.agent_runtime.utils import (
            _EXPORT_AUTO_OFFSET_MAX,
            _is_full_page_rows,
        )

        phase = state.export_phase
        if phase != "fetch":
            return False
        if auto_done >= _EXPORT_AUTO_OFFSET_MAX:
            return False
        if budget_left < 2:
            return False
        if pages_done >= max(1, page_cap):
            return False
        # User views: don't auto-OFFSET unless full page
        from app.services.agent_runtime.utils import _is_user_info_view
        if view and _is_user_info_view(view):
            return last_page_rows >= PaginationEngine.page_limit_for_view(view)
        return _is_full_page_rows(last_page_rows, view=view)

    @staticmethod
    def build_offset_example(
        view: str,
        sql_base: str,
        pages_done: int,
        *,
        limit: int | None = None,
    ) -> str:
        """Build an MCP query example with the next OFFSET value."""
        from app.services.react_engine import _format_offset_mcp_example

        lim = limit or PaginationEngine.page_limit_for_view(view)
        return _format_offset_mcp_example(view, sql_base, pages_done, limit=lim)

    @staticmethod
    def sql_base_for_pagination(
        sql_now: str,
        role: str,
        time_window: dict | None,
    ) -> str:
        """Strip LIMIT/OFFSET from the already-executed bound-resource SQL."""
        from app.services.agent_runtime.utils import _is_count_sql

        base = (sql_now or "").strip().rstrip(";")
        if base and not _is_count_sql(base):
            import re
            base = re.sub(r"(?is)\s+LIMIT\s+\d+(\s+OFFSET\s+\d+)?\s*$", "", base).strip()
            base = re.sub(r"(?is)\s+OFFSET\s+\d+\s*$", "", base).strip()
            if base:
                return base
        del role, time_window
        return ""

    # ---- Fetch state helpers ----

    @staticmethod
    def needs_continue(
        state: AgentLoopState,
        *,
        budget_left: int = 0,
        fact_page_cap: int | None = None,
        user_page_cap: int | None = None,
        pay_cohort: bool = False,
        full_fetch: bool = False,
    ) -> list[str]:
        """Which target roles still need page continuation."""
        from app.services.agent_runtime.utils import _export_roles_needing_page_continue

        return _export_roles_needing_page_continue(
            target_roles=state.export_target_roles,
            fetched_view_pages=state.fetched_view_pages,
            last_page_rows_by_view=state.fetched_view_last_rows,
            budget_left=budget_left,
            fact_page_cap=fact_page_cap,
            user_page_cap=user_page_cap,
            pay_cohort=pay_cohort,
            full_fetch=full_fetch,
        )
