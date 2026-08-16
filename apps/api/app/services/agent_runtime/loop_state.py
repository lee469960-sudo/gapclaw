"""Mutable AgentLoopState — all state that evolves across loop iterations.

Single mutable container replacing ~80 nonlocal variables in run_react_loop.
Components read and write state explicitly via this object.
"""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class AgentLoopState:
    """Mutable state that evolves across iterations of the ReAct loop.

    Fields are organized into logical groups. All have sensible defaults
    so the dataclass can be constructed with just the needed overrides.
    """

    # ---- Core loop ----
    final: str = ""
    last_reply: str = ""
    run_steps: list[dict] = field(default_factory=list)
    saved_paths: list[str] = field(default_factory=list)
    files_written: int = 0

    # ---- Progress / termination ----
    progress_lines: list[str] = field(default_factory=list)
    no_progress: int = 0
    llm_failure_streak: int = 0
    empty_llm_streak: int = 0
    text_only_streak: int = 0
    had_explicit_final: bool = False
    soft_finish_requested: bool = False
    finalize_hint_injected: bool = False
    tools_effective_this_turn: bool = False
    ran_any_tool: bool = False
    # Bound-MCP "no ClickHouse/MCP" excuse soft-rejects this turn
    mcp_excuse_soft_rejects: int = 0
    mcp_excuse_claim_hit: bool = False

    # ---- Iteration / budget ----
    max_iters: int = 50
    trim_keep: int = 12
    trim_cap: int = 500
    export_finalize_from: int = 0

    # ---- MCP execution tracking ----
    mcp_results: list[dict] = field(default_factory=list)
    mcp_error_sigs: list[str] = field(default_factory=list)
    mcp_empty_view_fails: int = 0
    mcp_tool_calls: dict[str, int] = field(default_factory=dict)
    mcp_tool_fails: dict[str, int] = field(default_factory=dict)
    mcp_identical_counts: dict[str, int] = field(default_factory=dict)
    mcp_class_fails: dict[str, int] = field(default_factory=dict)
    mcp_class_samples: dict[str, str] = field(default_factory=dict)
    mcp_class_tools: dict[str, str] = field(default_factory=dict)
    mcp_hard_block_classes: set[str] = field(default_factory=set)
    mcp_hard_block_tools: set[str] = field(default_factory=set)
    mcp_class_block_hits: int = 0
    mcp_abort_requested: bool = False
    mcp_hard_block_sigs: set[str] = field(default_factory=set)
    mcp_hard_block_hits: int = 0

    # ---- Soft fail limits ----
    soft_fail_limit: int = 5
    class_fail_limit: int = 999

    # ---- Generic policy (non-export tool path) ----
    generic_phase: str = ""
    generic_plan_done: bool = False
    generic_plan_attempts: int = 0
    generic_plan_hint_injected: bool = False
    generic_todos: list[dict] = field(default_factory=list)
    generic_tool_budget: int = 8
    generic_tool_count: int = 0
    skip_generic_plan: bool = False

    # ---- Export: task identity ----
    export_like: bool = False
    export_phase: str = ""  # discover | plan | fetch | analyze | finalize
    export_run_id: str = ""
    export_run_started_at: float = 0.0
    task_brief: str = ""
    export_source_brief: str = ""

    # ---- Export: plan gate ----
    plan_done: bool = False
    plan_attempts: int = 0
    plan_hint_injected: bool = False
    export_discover_ready: bool = False
    export_discover_queries: int = 0

    # ---- Export: budget ----
    mcp_query_count: int = 0
    mcp_query_budget: int = 14
    export_budget_cap: int = 14
    export_force_finalize: bool = False
    pay_cohort: bool = False
    is_type_b: bool = False
    export_fact_page_cap: int = 0
    export_user_page_cap: int = 0
    analyze_rounds_used: int = 0
    analyze_hint_injected: bool = False
    analyze_empty_reject_count: int = 0
    analyze_max_rounds: int = 0

    # ---- Export: column plan / todos ----
    export_todos: list[dict] = field(default_factory=list)
    export_column_plan: list[dict] = field(default_factory=list)
    export_target_roles: list[str] = field(default_factory=list)
    export_resource_whitelist: list[str] = field(default_factory=list)

    # ---- Export: view/routing ----
    export_view_mode: str = "multi_fact"
    export_pinned_views: list[str] = field(default_factory=list)
    export_full_fetch: bool = False

    # ---- Export: fetch state ----
    fetched_view_pages: dict[str, int] = field(default_factory=dict)
    fetched_view_last_rows: dict[str, int] = field(default_factory=dict)
    export_failed_views: set[str] = field(default_factory=set)
    export_abandoned_roles: set[str] = field(default_factory=set)
    export_done_node_keys: set[str] = field(default_factory=set)
    export_role_fail_counts: dict[str, int] = field(default_factory=dict)
    user_fetch_complete: bool = False
    export_uid_batch_index: int = 0
    export_prior_fetch_hint: str = ""
    export_cohort_uid_estimate: int | None = None

    # ---- Export: schema discovery ----
    ads_views_list_text: str = ""
    ads_schema_hints: dict[str, Any] = field(default_factory=dict)
    export_schema_discovery: dict[str, Any] = field(default_factory=dict)
    export_dim_views: dict[str, str] = field(default_factory=dict)
    export_dim_force_rounds: int = 0
    export_dim_budget_exhausted: bool = False
    export_fetch_idle_rounds: int = 0

    # ---- Export: contract / validation ----
    export_task_spec: Any = None
    export_task_validation: Any = None
    export_contract: Any = None
    export_trace: Any = None

    # ---- Export: time window ----
    export_time_window: dict | None = None
    export_tw_from_prior: bool = False
    export_filter_condition: str = ""

    # ---- Export: deliverables ----
    export_fallback: bool = False
    export_missing_cols: list[str] = field(default_factory=list)
    analyze_write_shell_ok: bool = False
    analyze_explore_shells: int = 0
    engine_script_ran: bool = False
    engine_script_executed: bool = False
    export_read_idle_rounds: int = 0

    # ---- Export: repair / fill ----
    export_repair_like: bool = False
    export_repair_prior_state: dict | None = None
    export_repair_prior_run_id: str = ""
    export_repair_system_hint: str = ""
    export_turn_intent: str = "unknown"
    export_turn_digest: str = ""
    export_fill_scope: Any = None
    export_fill_columns: list[str] = field(default_factory=list)
    export_fill_target_rel: str = ""
    export_deliverable_headers: list[str] = field(default_factory=list)
    export_prior_same_session: bool = True
    export_repair_prefetched: Any = None
