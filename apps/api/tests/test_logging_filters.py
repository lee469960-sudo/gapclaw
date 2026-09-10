"""Logging filters for local runtime log hygiene."""

from __future__ import annotations

import logging

from app.logging_filters import SuppressCheckStatus304Filter


def _record(message: str) -> logging.LogRecord:
    return logging.LogRecord(
        name="uvicorn.access",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg=message,
        args=(),
        exc_info=None,
    )


def test_check_status_304_access_log_is_suppressed():
    filt = SuppressCheckStatus304Filter()
    record = _record(
        '127.0.0.1:50000 - "GET /pages/page_agent_chat.cgi?'
        'action=check_status&agent_id=a&session_id=s HTTP/1.1" 304'
    )

    assert filt.filter(record) is False


def test_check_status_non_304_access_log_is_preserved():
    filt = SuppressCheckStatus304Filter()
    record = _record(
        '127.0.0.1:50000 - "GET /pages/page_agent_chat.cgi?'
        'action=check_status&agent_id=a&session_id=s HTTP/1.1" 200'
    )

    assert filt.filter(record) is True


def test_unrelated_access_log_is_preserved():
    filt = SuppressCheckStatus304Filter()
    record = _record('127.0.0.1:50000 - "GET /health HTTP/1.1" 200')

    assert filt.filter(record) is True
