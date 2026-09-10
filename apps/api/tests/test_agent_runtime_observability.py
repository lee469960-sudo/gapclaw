"""Agent runtime observability helpers."""

from __future__ import annotations

from unittest.mock import Mock

from app.services.agent_runtime.observability import NoProgressHintLogAggregator


def test_no_progress_hint_logs_aggregate_fields():
    logger = Mock()
    agg = NoProgressHintLogAggregator(logger)

    agg.log_hint("agent-1", iteration=5, streak=5)
    agg.log_hint("agent-1", iteration=10, streak=10)

    assert logger.info.call_count == 2
    first_args = logger.info.call_args_list[0].args
    second_args = logger.info.call_args_list[1].args
    assert "component=agent_runtime" in first_args[0]
    assert "class=no_progress" in first_args[0]
    assert first_args[1:] == ("agent-1", 5, 5, 1, 5)
    assert second_args[1:] == ("agent-1", 10, 10, 2, 5)


def test_no_progress_hint_aggregation_resets_after_progress():
    logger = Mock()
    agg = NoProgressHintLogAggregator(logger)

    agg.log_hint("agent-1", iteration=5, streak=5)
    agg.reset("agent-1")
    agg.log_hint("agent-1", iteration=15, streak=5)

    assert logger.info.call_count == 2
    second_args = logger.info.call_args_list[1].args
    assert second_args[1:] == ("agent-1", 15, 5, 1, 5)


def test_no_progress_hint_aggregation_is_per_agent():
    logger = Mock()
    agg = NoProgressHintLogAggregator(logger)

    agg.log_hint("agent-1", iteration=5, streak=5)
    agg.log_hint("agent-2", iteration=7, streak=7)

    assert logger.info.call_args_list[0].args[1:] == ("agent-1", 5, 5, 1, 5)
    assert logger.info.call_args_list[1].args[1:] == ("agent-2", 7, 7, 1, 7)
