"""Telegram poller structured failure and recovery logging."""

from __future__ import annotations

from unittest.mock import Mock

import httpx

from app.services.channels import telegram_poller


def setup_function():
    telegram_poller._poll_failure_streaks.clear()


def test_first_telegram_poll_failure_logs_structured_context_without_token(monkeypatch):
    logger = Mock()
    monkeypatch.setattr(telegram_poller, "logger", logger)

    exc = httpx.ConnectError("boom bot123:secret")
    telegram_poller._record_poll_failure("chan-1", exc)

    logger.error.assert_called_once()
    args = logger.error.call_args.args
    kwargs = logger.error.call_args.kwargs
    assert "component=telegram" in args[0]
    assert "class=%s" in args[0]
    assert args[1:] == ("network_connectivity", "chan-1", "ConnectError", 1, 1.0)
    assert kwargs["exc_info"] is True
    assert "secret" not in args[0]


def test_repeated_same_class_failure_logs_compact_aggregate(monkeypatch):
    logger = Mock()
    monkeypatch.setattr(telegram_poller, "logger", logger)

    telegram_poller._record_poll_failure("chan-1", httpx.ConnectError("one"))
    telegram_poller._record_poll_failure("chan-1", httpx.ReadError("two"))

    logger.error.assert_called_once()
    logger.warning.assert_called_once()
    args = logger.warning.call_args.args
    assert "aggregate" in args[0]
    assert args[1:6] == ("network_connectivity", "chan-1", "ConnectError", 2, 1.0)


def test_changed_failure_class_starts_new_traceback_log(monkeypatch):
    logger = Mock()
    monkeypatch.setattr(telegram_poller, "logger", logger)

    telegram_poller._record_poll_failure("chan-1", httpx.ConnectError("one"))
    telegram_poller._record_poll_failure("chan-1", RuntimeError("two"))

    assert logger.error.call_count == 2
    second_args = logger.error.call_args_list[1].args
    assert second_args[1:] == ("polling_error", "chan-1", "RuntimeError", 1, 1.0)


def test_success_after_failure_logs_recovery(monkeypatch):
    logger = Mock()
    monkeypatch.setattr(telegram_poller, "logger", logger)

    telegram_poller._record_poll_failure("chan-1", httpx.ConnectError("one"))
    telegram_poller._record_poll_success("chan-1")
    telegram_poller._record_poll_success("chan-1")

    logger.info.assert_called_once()
    args = logger.info.call_args.args
    assert "telegram poll recovered" in args[0]
    assert args[1:5] == ("chan-1", "network_connectivity", "ConnectError", 1)
