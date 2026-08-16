"""Mutable AgentLoopState — per-run state that evolves across loop iterations.

Single mutable container the LLM-driven loop reads and writes. Only the
protocol/execution fields survive; no phase/budget/verification trackers.
"""

from dataclasses import dataclass, field


@dataclass
class AgentLoopState:
    """Mutable state that evolves across iterations of the LLM-driven loop."""

    final: str = ""
    last_reply: str = ""
    run_steps: list[dict] = field(default_factory=list)
    saved_paths: list[str] = field(default_factory=list)
    files_written: int = 0
    progress_lines: list[str] = field(default_factory=list)
    ran_any_tool: bool = False
    max_iters: int = 50
