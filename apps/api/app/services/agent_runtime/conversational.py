"""ConversationalHandler — tool-free single-turn chat path.

Extracted from react_engine.py: fast path for pure conversation without
PLAN/MCP/FINAL protocol, with light fallback on LLM failure.
"""

from __future__ import annotations

import json
import logging
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.agent.models import Agent
    from app.models.llm_resource import LLMResource
    from sqlmodel import Session

logger = logging.getLogger(__name__)

_CONVERSATIONAL_HISTORY_MAX = 6


class ConversationalHandler:
    """Handles tool-free conversational turns.

    All methods take explicit parameters — no hidden state.
    Uses hub.publish for WebSocket streaming and _running dict for
    cancellation signalling.
    """

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

    @staticmethod
    def _guard_no_tool_protocol(messages: list[dict]) -> None:
        """Raise if tool protocol coaches leaked into conversational messages."""
        for m in messages:
            c = str(m.get("content") or "")
            if m.get("role") == "system" and (
                "可用工具:" in c or "【通用·规划闸门】" in c or "【导出策略" in c
            ):
                raise RuntimeError("conversational messages leaked tool protocol")

    # ---- Core turn execution ----

    @staticmethod
    async def run_turn(
        *,
        db: Session,
        agent: Agent,
        session_id: str,
        user_message: str,
        effective_message: str,
        llm: LLMResource | None,
        history: list,
        key: str,
        user_meta: dict,
    ) -> str:
        """Single-turn LLM reply without tools; light canned reply on LLM failure."""
        from app.security import now_str
        from app.services.agent_runtime.hub import _running, hub
        from app.services.agent_runtime.system_prompt import SystemPromptBuilder
        from app.services.llm_client import chat_completion
        from app.services.react_engine import (
            _append_rolling_summary,
            _clean_display_text,
            _clean_final_answer,
            _is_light_agent_interaction,
            _looks_like_tool_call,
        )
        from app.models.chat_message import ChatMessage

        used_fallback = False
        final = ""
        try:
            if not llm:
                raise RuntimeError("未配置 LLM")
            messages = ConversationalHandler.build_messages(agent, history, effective_message)
            ConversationalHandler._guard_no_tool_protocol(messages)

            reply = await chat_completion(
                llm,
                messages,
                max_tokens=1024,
                db=db,
                timeout=getattr(agent, "llm_timeout", None),
            )
            final = _clean_final_answer(reply or "")
            if not final.strip():
                raise RuntimeError("empty conversational reply")
            if _looks_like_tool_call(final) or re.search(
                r"(?im)^\s*(PLAN|FINAL)\s*[:：]",
                final,
            ):
                final = _clean_display_text(
                    re.sub(r"(?im)^\s*(PLAN|FINAL)\s*[:：]\s*", "", final)
                )
            if not final.strip():
                raise RuntimeError("protocol-only conversational reply")
        except Exception:
            logger.exception(
                "conversational turn failed agent=%s session=%s; using light fallback",
                agent.id,
                session_id,
            )
            final = SystemPromptBuilder.build_light_agent_reply(agent, user_message)
            used_fallback = True

        if not (final or "").strip():
            final = "（本轮未产生文字回复；详见执行过程）"

        step = {
            "type": "info",
            "action": "conversational_reply" if not used_fallback else "agent_identity_interaction",
            "title": "对话回复" if not used_fallback else "Agent 身份回应",
            "status": "done",
            "content": (final or "")[:500],
        }
        await hub.publish(key, {"type": "step", "op": "append", "index": 0, "step": step})

        meta = json.dumps({
            "steps": [step],
            "step_count": 1,
            "saved_paths": [],
            "conversational_reply": not used_fallback,
            "light_agent_interaction": used_fallback or _is_light_agent_interaction(user_message),
            **{
                key: user_meta[key]
                for key in (
                    "source", "channel_id", "chat_id", "chat_type", "user_id",
                    "sender_username", "sender_display_name",
                )
                if user_meta.get(key)
            },
        }, ensure_ascii=False)

        db.add(ChatMessage(
            agent_id=agent.id,
            session_id=session_id,
            role="assistant",
            content=final,
            meta=meta,
            created_at=now_str(),
        ))
        db.commit()

        try:
            await _append_rolling_summary(db, agent, session_id, llm, final, [])
        except Exception:
            logger.exception("rolling summary failed agent=%s session=%s", agent.id, session_id)

        await hub.publish(key, {
            "type": "done",
            "content": (final or "")[:500],
            "content_truncated": len(final or "") > 500,
            "workplace_changed": False,
        })
        _running[key] = False
        return final
