"""ToolExecutor — thin wrapper around agent_tools.execute_action.

Execution only. Security checks (allowed_actions) live in the loop before
execution; the LLM interprets results and decides next steps. No rule-based
failure classification, tracking, or retry logic.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.agent.models import Agent
    from app.models.sandbox import Sandbox
    from sqlmodel import Session


class ToolExecutor:
    """Wraps tool execution with no additional policy."""

    @staticmethod
    async def execute(
        action: str,
        normalized: str,
        db: Session,
        agent: Agent,
        sandbox: Sandbox | None,
        skill_ids: list[str],
        mcp_ids: list[str],
        httpmcp_ids: list[str],
        rag_ids: list[str],
    ) -> str | None:
        """Execute a tool action via agent_tools.execute_action."""
        from app.services.agent_tools import execute_action

        return await execute_action(
            action,
            normalized,
            db,
            agent,
            sandbox,
            skill_ids,
            mcp_ids,
            httpmcp_ids,
            rag_ids,
        )

    @staticmethod
    def ensure_sandbox_running(db: Session, sandbox: Sandbox | None) -> bool:
        """Ensure the sandbox is running; start if needed."""
        if not sandbox:
            return False
        try:
            from app.services.sandbox_util import ensure_sandbox_running as _ensure
            return _ensure(db, sandbox)
        except Exception:
            return False
