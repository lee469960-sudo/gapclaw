"""ConversationalHandler — tool-free single-turn chat path.

Message construction and history trimming for pure conversation without
PLAN/MCP/FINAL protocol.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.agent.models import Agent

_CONVERSATIONAL_HISTORY_MAX = 6


class ConversationalHandler:
    """Handles tool-free conversational turns."""

    # ---- History trimming ----

    @staticmethod
    def trim_history(
        history: list,
        *,
        max_msgs: int = _CONVERSATIONAL_HISTORY_MAX,
    ) -> list[dict]:
        """Recent short turns only; blunt large export/tool dumps that derail chat."""
        out: list[dict] = []
        for h in list(history or [])[-max_msgs:]:
            if isinstance(h, dict):
                role = str(h.get("role") or "")
                content = str(h.get("content") or "")
            else:
                role = str(getattr(h, "role", "") or "")
                content = str(getattr(h, "content", "") or "")
            if role not in ("user", "assistant"):
                continue
            if role == "assistant" and len(content) > 200 and (
                "FINAL:" in content
                or "【导出" in content
                or "list_ads_views" in content
                or "query_ads_view" in content
                or "已落盘" in content
                or "原始回退" in content
            ):
                content = "（上一轮为工具/导出执行，细节已省略）"
            elif len(content) > 800:
                content = content[:700] + "\n…(已截断)"
            out.append({"role": role, "content": content})
        return out

    # ---- Message construction ----

    @staticmethod
    def build_messages(
        agent: Agent | None,
        history: list,
        effective_message: str,
    ) -> list[dict]:
        """Build messages for conversational path: no tools / skills / export coaches."""
        from app.services.agent_runtime.system_prompt import SystemPromptBuilder

        msgs: list[dict] = [
            {"role": "system", "content": SystemPromptBuilder.build_conversational_system(agent)},
        ]
        trimmed = ConversationalHandler.trim_history(history)
        # Drop trailing user (just committed); we append effective_message once
        if trimmed and trimmed[-1].get("role") == "user":
            trimmed = trimmed[:-1]
        msgs.extend(trimmed)
        msgs.append({"role": "user", "content": (effective_message or "").strip() or "你好"})
        return msgs
