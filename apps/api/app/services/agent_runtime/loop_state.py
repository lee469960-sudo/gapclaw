"""Mutable AgentLoopState — per-run state that evolves across loop iterations.

Single mutable container the LLM-driven loop reads and writes. Only the
protocol/execution fields survive; no phase/budget/verification trackers.
"""

from dataclasses import dataclass, field

_PROGRESS_MAX_LINES = 80


@dataclass
class AgentLoopState:
    """Mutable state that evolves across iterations of the LLM-driven loop."""

    final: str = ""
    last_reply: str = ""
    run_steps: list[dict] = field(default_factory=list)
    saved_paths: list[str] = field(default_factory=list)
    files_written: int = 0
    progress_lines: list[str] = field(default_factory=list)
    subtasks: list[dict] = field(default_factory=list)  # [{text, status}] status ∈ done/pending
    resumed: bool = False  # True when this run resumed from a persisted checkpoint
    goal: str = ""  # Original task goal (stable across resume; may differ from the latest user message)
    # Long-task checkpoint extension (resume/perf):
    run_ts: str = ""  # Task output dir suffix (reused across resume so files aren't orphaned)
    mcp_results: list[dict] = field(default_factory=list)  # [{seq, path, tool, args, size}]
    query_cache: dict = field(default_factory=dict)  # {dedup_key: {path, tool, size}}
    plan_text: str = ""  # Raw PLAN body (carries view_map; restored on resume)
    no_progress_streak: int = 0  # Consecutive no-progress rounds (v15 R1′ breakthrough-hint trigger, non-gating)
    completion_signal_streak: int = 0  # Consecutive positive completion-declaration rounds (v16 R1, soft — non-gating)
    fix_only_until_final: bool = False  # After an effective reflect FAIL, drop PLAN until the next FINAL
    verifier_gaps: dict = field(default_factory=dict)  # {gap_id: {card, signatures, evidence}}
    terminal_reason: str = ""  # Explicit bounded-finalization reason for resume/UI
    selected_mcp_ids: list[str] = field(default_factory=list)  # LLM-routed subset for this run
    mcp_route_attempts: int = 0  # Bounded in-run supplementary route attempts
    mcp_route_events: list[dict] = field(default_factory=list)  # Redacted route audit records

    def add_progress(self, line: str) -> None:
        """Append a progress line, avoiding dupes and capping length."""
        line = (line or "").strip()
        if not line:
            return
        if self.progress_lines and self.progress_lines[-1] == line:
            return
        self.progress_lines.append(line)
        if len(self.progress_lines) > _PROGRESS_MAX_LINES:
            del self.progress_lines[: -_PROGRESS_MAX_LINES]
