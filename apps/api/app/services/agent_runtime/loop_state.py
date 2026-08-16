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
    ran_any_tool: bool = False
    max_iters: int = 50

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
