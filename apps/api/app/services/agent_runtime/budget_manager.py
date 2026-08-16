"""BudgetManager — adaptive budget computation, page caps, and COUNT estimates.

Manages dynamic query budget and per-view page caps during export runs.
All methods take explicit parameters — no hidden state.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.services.agent_runtime.utils import (
    _EXPORT_FACT_PAGE_CAP_MAX,
    _EXPORT_MAX_PAGES_PER_VIEW,
    _EXPORT_MAX_PAGES_PER_VIEW_PAY_COHORT,
    _EXPORT_MAX_PAGES_USER,
    _EXPORT_MAX_PAGES_USER_MAX,
    _EXPORT_PAGE_LIMIT,
    _EXPORT_QUERY_BUDGET_DEFAULT,
    _EXPORT_QUERY_BUDGET_FLOOR,
    _EXPORT_QUERY_BUDGET_FULL_FETCH_MAX,
    _EXPORT_QUERY_BUDGET_TYPE_B,
    _EXPORT_QUERY_BUDGET_TYPE_B_MAX,
    _EXPORT_QUERY_BUDGET_TYPE_B_PAY_MAX,
    _EXPORT_USER_PAGE_LIMIT,
    _COHORT_ROW_COMPLETE_RATIO,
    _compute_fact_page_cap,
    _compute_full_fetch_budget,
    _compute_user_page_cap,
    _needed_pages_for_estimate,
)

if TYPE_CHECKING:
    from app.services.agent_runtime.loop_state import AgentLoopState


class BudgetManager:
    """Manages adaptive query budget and per-view page caps.

    All methods are static — state is read from and written to
    AgentLoopState explicitly.
    """

    # ---- Page cap computation ----

    @staticmethod
    def compute_user_page_cap(cohort_uid_estimate: int | None = None) -> int:
        """Dynamic user_info page cap from cohort estimate."""
        return _compute_user_page_cap(cohort_uid_estimate)

    @staticmethod
    def compute_fact_page_cap(
        *,
        pay_cohort: bool = False,
        cohort_uid_estimate: int | None = None,
    ) -> int:
        """Dynamic per-fact-view page cap from COUNT estimate."""
        return _compute_fact_page_cap(
            pay_cohort=pay_cohort,
            cohort_uid_estimate=cohort_uid_estimate,
        )

    @staticmethod
    def needed_pages_for_estimate(
        estimate: int | None,
        *,
        page_limit: int = _EXPORT_PAGE_LIMIT,
    ) -> int:
        """Pages needed for estimate with +2 slack."""
        return _needed_pages_for_estimate(estimate, page_limit=page_limit)

    # ---- Full-fetch budget ----

    @staticmethod
    def compute_full_fetch_budget(
        *,
        type_b: bool,
        pay_cohort: bool,
        fact_page_cap: int,
        current: int = 0,
        user_page_cap: int | None = None,
    ) -> int:
        """Raise query budget so user + fact roles can reach short-page under cap."""
        return _compute_full_fetch_budget(
            type_b=type_b,
            pay_cohort=pay_cohort,
            fact_page_cap=fact_page_cap,
            current=current,
            user_page_cap=user_page_cap,
        )

    # ---- Adaptive budget from prior state ----

    @staticmethod
    def compute_adaptive_export_budget(
        *,
        type_b: bool,
        prior_state: dict | None,
        current_tw_label: str = "",
        current_tw: dict | None = None,
        pay_cohort: bool = False,
        allow_prior_mcp_examples: bool = True,
    ) -> tuple[int, str]:
        """Return (budget, coach_hint) from prior truncation / row-gap signals."""
        from app.services.agent_runtime.utils import _tw_ms_tuple

        base = _EXPORT_QUERY_BUDGET_TYPE_B if type_b else _EXPORT_QUERY_BUDGET_DEFAULT
        cap = _EXPORT_QUERY_BUDGET_TYPE_B_MAX if type_b else _EXPORT_QUERY_BUDGET_DEFAULT
        if pay_cohort and type_b:
            cap = max(cap, _EXPORT_QUERY_BUDGET_TYPE_B_PAY_MAX)
        if not isinstance(prior_state, dict):
            if pay_cohort and type_b:
                return min(cap, max(base, base + 4)), (
                    "【充值用户】事实表可深翻至短页；勿浅拉 1 页即收工。"
                )
            return base, ""
        prior_tw = prior_state.get("time_window") or {}
        if not isinstance(prior_tw, dict):
            prior_tw = {}
        cur_tw = current_tw if isinstance(current_tw, dict) else None
        prior_label = str(prior_tw.get("label") or "").strip()
        cur_label = ""
        if cur_tw:
            cur_label = str(cur_tw.get("label") or "").strip()
        if not cur_label:
            cur_label = (current_tw_label or "").strip()
        sa, ea = _tw_ms_tuple(cur_tw)
        sb, eb = _tw_ms_tuple(prior_tw)
        if sa is not None and ea is not None and sb is not None and eb is not None:
            same_tw = sa == sb and ea == eb
        elif cur_label and prior_label:
            same_tw = prior_label in cur_label or cur_label in prior_label
        else:
            same_tw = False
        try:
            user_complete = prior_state.get("user_fetch_complete")
        except Exception:
            user_complete = None
        truncated = bool(prior_state.get("user_truncated")) or bool(
            prior_state.get("fact_truncated_roles")
        )
        user_incomplete = user_complete is False
        prior_est = prior_state.get("cohort_uid_estimate")
        try:
            prior_est_i = int(prior_est) if prior_est is not None else None
        except (TypeError, ValueError):
            prior_est_i = None
        try:
            prior_rows = prior_state.get("deliverable_rows")
            prior_rows_i = int(prior_rows) if prior_rows is not None else None
        except (TypeError, ValueError):
            prior_rows_i = None
        row_gap = BudgetManager._cohort_rows_incomplete(
            deliverable_rows=prior_rows_i,
            cohort_uid_estimate=prior_est_i,
        )
        completeness = str(prior_state.get("completeness") or "").strip()
        need_cont = [
            str(r).strip()
            for r in (prior_state.get("need_continue_roles") or [])
            if str(r).strip()
        ]
        incomplete_comp = completeness in (
            "truncated", "fact_starved", "iters_exhausted", "no_data", "fallback",
        )
        if not same_tw or not (
            truncated or user_incomplete or row_gap or pay_cohort or incomplete_comp or need_cont
        ):
            learn = BudgetManager._prior_state_learning_hint(prior_state)
            if learn and same_tw:
                return base, learn
            return base, ""
        try:
            prior_budget = int(prior_state.get("budget") or base)
        except (TypeError, ValueError):
            prior_budget = base
        bump = 6 if (pay_cohort and (truncated or row_gap or incomplete_comp)) else 4
        if completeness in ("iters_exhausted", "fact_starved"):
            bump = max(bump, 6)
        budget = min(cap, max(base, prior_budget + bump))
        try:
            up = int(prior_state.get("user_pages") or 0)
        except (TypeError, ValueError):
            up = 0
        facts = [
            str(r).strip()
            for r in (prior_state.get("fact_truncated_roles") or [])
            if str(r).strip()
        ]
        if need_cont:
            for r in need_cont:
                if r not in facts:
                    facts.append(r)
        hint = (
            f"【上轮拉取缺口】completeness={completeness or '未知'}；"
            f"user_pages={up}，短页={'是' if user_complete else '否'}；"
            "本轮须续翻至短页或达帽后再写表。"
        )
        digest = str(prior_state.get("digest") or "").strip()
        if digest:
            hint += f" 上轮摘要：{digest}"
        if facts:
            hint += " 明细满页截断/须续翻：" + "、".join(facts) + "（同窗 OFFSET 续页）。"
        if row_gap and prior_est_i and prior_rows_i is not None:
            hint += (
                f" 交付行数={prior_rows_i} << 目标≈{prior_est_i}；"
                "先拉全 pay cohort 再 join。"
            )
        if allow_prior_mcp_examples and same_tw:
            shown = 0
            for a in (prior_state.get("next_actions") or [])[:2]:
                if not isinstance(a, dict):
                    continue
                mcp = str(a.get("mcp_example") or "").strip()
                if mcp:
                    hint += f" 建议：{mcp}"
                    shown += 1
                if shown >= 2:
                    break
        if pay_cohort:
            hint += f" 充值用户事实表页帽≤{_EXPORT_MAX_PAGES_PER_VIEW_PAY_COHORT}。"
        hint += f" 本轮预算已调至 {budget}。"
        return budget, hint

    @staticmethod
    def _cohort_rows_incomplete(
        *,
        deliverable_rows: int | None,
        cohort_uid_estimate: int | None,
        ratio: float = _COHORT_ROW_COMPLETE_RATIO,
    ) -> bool:
        """True when deliverable rows are well below the cohort estimate."""
        if deliverable_rows is None or cohort_uid_estimate is None:
            return False
        if cohort_uid_estimate <= 0:
            return False
        return (deliverable_rows / cohort_uid_estimate) < ratio

    @staticmethod
    def _prior_state_learning_hint(prior_state: dict | None) -> str:
        """Inject prior digest + next_actions into system prompt."""
        if not isinstance(prior_state, dict):
            return ""
        digest = str(prior_state.get("digest") or "").strip()
        actions = prior_state.get("next_actions") or []
        bits: list[str] = []
        if digest:
            bits.append(f"上轮摘要：{digest}")
        n = 0
        for a in actions:
            if not isinstance(a, dict):
                continue
            mcp = str(a.get("mcp_example") or "").strip()
            hint = str(a.get("hint") or "").strip()
            if mcp:
                bits.append(f"上轮建议续查：{mcp}")
                n += 1
            elif hint:
                bits.append(f"上轮建议：{hint}")
                n += 1
            if n >= 2:
                break
        if not bits:
            return ""
        return "【上轮 run_state 记忆】\n" + "\n".join(bits)

    # ---- COUNT estimate → dynamic cap bump (stateful) ----

    @staticmethod
    def apply_cohort_estimate_from_count(
        state: AgentLoopState,
        n: int,
    ) -> None:
        """Update estimate + dynamically raise page cap / query budget mid-run.

        Operates on AgentLoopState in-place. Converts the former
        _apply_cohort_estimate_from_count closure to an explicit method.
        """
        if n is None or int(n) <= 0:
            return
        n = int(n)
        # Keep larger prior estimate
        if state.export_cohort_uid_estimate and n <= state.export_cohort_uid_estimate:
            n = max(n, state.export_cohort_uid_estimate)
        state.export_cohort_uid_estimate = n

        new_cap = BudgetManager.compute_fact_page_cap(
            pay_cohort=state.pay_cohort,
            cohort_uid_estimate=n,
        )
        state.export_fact_page_cap = max(state.export_fact_page_cap, new_cap)
        state.export_user_page_cap = max(
            state.export_user_page_cap,
            BudgetManager.compute_user_page_cap(n),
        )
        if state.is_type_b:
            bumped = BudgetManager.compute_full_fetch_budget(
                type_b=True,
                pay_cohort=state.pay_cohort,
                fact_page_cap=state.export_fact_page_cap,
                current=state.mcp_query_budget,
                user_page_cap=state.export_user_page_cap,
            )
            if bumped > state.mcp_query_budget:
                state.mcp_query_budget = bumped
            state.export_budget_cap = min(
                _EXPORT_QUERY_BUDGET_FULL_FETCH_MAX,
                max(state.export_budget_cap, state.mcp_query_budget),
            )

        from app.services.agent_runtime.message_manager import MessageManager

        MessageManager.append_progress(
            state.progress_lines,
            f"COUNT估量≈{n} → 用户页帽={state.export_user_page_cap} "
            f"事实页帽={state.export_fact_page_cap} 预算={state.mcp_query_budget}",
        )

    # ---- Convenience: normalize fetch budget ----

    @staticmethod
    def normalize_fetch_budget(
        *,
        requested_budget: int,
        current_cap: int,
        type_b: bool,
        pay_cohort: bool,
        full_fetch: bool,
        fact_page_cap: int,
        user_page_cap: int,
    ) -> tuple[int, int]:
        """Clamp PLAN budget, keeping Type-B/pay-cohort above engine minimums."""
        from app.services.agent_runtime.utils import _normalize_export_fetch_budget

        return _normalize_export_fetch_budget(
            requested_budget=requested_budget,
            current_cap=current_cap,
            type_b=type_b,
            pay_cohort=pay_cohort,
            full_fetch=full_fetch,
            fact_page_cap=fact_page_cap,
            user_page_cap=user_page_cap,
        )

    @staticmethod
    def parse_plan_budget(
        reply: str,
        *,
        max_budget: int | None = None,
    ) -> int | None:
        """If reply has a PLAN: block, return clamped query budget; else None."""
        from app.services.agent_runtime.utils import _parse_export_plan_budget

        return _parse_export_plan_budget(reply, max_budget=max_budget)
