"""Small logging filters for local runtime log hygiene."""

from __future__ import annotations

import logging


class SuppressCheckStatus304Filter(logging.Filter):
    """Drop expected AgentChat check_status 304 polling access logs."""

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        if "check_status" not in msg:
            return True
        if "/pages/page_agent_chat.cgi" not in msg:
            return True
        return '" 304' not in msg and " 304" not in msg
