"""Runtime observability helpers that keep logs concise and structured."""

from __future__ import annotations

import logging
from dataclasses import dataclass


@dataclass
class _NoProgressAggregate:
    emitted: int = 0
    last_streak: int = 0


class NoProgressHintLogAggregator:
    """Aggregate repeated no-progress hint logs per agent."""

    def __init__(self, logger: logging.Logger) -> None:
        self._logger = logger
        self._state: dict[str, _NoProgressAggregate] = {}

    def reset(self, agent_id: str) -> None:
        self._state.pop(agent_id, None)

    def log_hint(self, agent_id: str, iteration: int, streak: int) -> None:
        state = self._state.setdefault(agent_id, _NoProgressAggregate())
        state.emitted += 1
        interval = streak - state.last_streak if state.last_streak else streak
        state.last_streak = streak
        self._logger.info(
            "modular_loop no_progress_hint aggregate "
            "component=agent_runtime class=no_progress agent=%s iter=%d "
            "streak=%d repeat_count=%d interval=%d",
            agent_id,
            iteration,
            streak,
            state.emitted,
            interval,
        )
