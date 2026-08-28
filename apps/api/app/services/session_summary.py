"""Session rolling-summary helpers.

Appends one rolling summary entry per completed assistant turn to ChatSummary,
kept for the conversation view. Extracted from the deleted react_engine so the
chat router no longer depends on the monolithic engine.
"""

from __future__ import annotations

import json
import logging
import re
from typing import TYPE_CHECKING

from app.models import ChatMessage, ChatSummary, LLMResource
from app.security import now_str
from app.services.tool_parser import clean_display_text
from app.services.llm_client import chat_completion

if TYPE_CHECKING:
    from sqlalchemy.orm import Session
    from app.models import Agent

logger = logging.getLogger(__name__)


def _heuristic_rolling_line(
    stamp: str,
    final: str,
    saved_paths: list[str],
) -> str:
    cleaned = re.sub(r"\s+", " ", clean_display_text(final or "")).strip()
    first = cleaned[:120] if cleaned else "完成一轮任务"
    if "。" in first:
        first = first.split("。")[0] + "。"
    files = ""
    if saved_paths:
        names = ", ".join(p.split("/")[-1] for p in saved_paths[:3])
        files = f" 文件：{names}。"
    return f"- [{stamp}] {first}{files}".strip()


async def _make_rolling_line(
    llm,
    db: Session,
    final: str,
    saved_paths: list[str],
    timeout: int | None = None,
    user_message: str = "",
) -> str:
    stamp = now_str()[:16]  # YYYY-MM-DD HH:MM
    if not llm:
        return _heuristic_rolling_line(stamp, final, saved_paths)
    paths_txt = ", ".join(saved_paths[:5]) if saved_paths else "无"
    prompt = (
        f"请根据本轮「用户要求」和「助手最终回复」，写【恰好一行】滚动总结，"
        f"重点保留用户提出的关键约束/口径（如过滤条件、字段口径、数量要求），格式严格为：\n"
        f"[{stamp}] 完成……（含关键数字若有）。核心表/要点……\n"
        f"不要换行，不超过 180 字，不要加前缀说明。\n\n"
        f"用户要求：{(user_message or '')[:600] or '（无）'}\n\n"
        f"已保存文件：{paths_txt}\n\n助手回复：\n{(final or '')[:1500]}"
    )
    try:
        line = await chat_completion(
            llm,
            [{"role": "user", "content": prompt}],
            max_tokens=256,
            db=db,
            timeout=timeout,
        )
        line = re.sub(r"\s+", " ", (line or "").strip())
        if not line:
            return _heuristic_rolling_line(stamp, final, saved_paths)
        if not line.startswith("["):
            line = f"[{stamp}] {line}"
        if not line.startswith("- "):
            line = f"- {line}"
        return line[:280]
    except Exception:
        logger.exception("rolling summary llm failed")
        return _heuristic_rolling_line(stamp, final, saved_paths)


def _summary_max_rounds(agent: Agent) -> int:
    """总结保留轮次，复用 history_length（历史轮次，默认 10）。"""
    try:
        return max(1, int(getattr(agent, "history_length", None) or 10))
    except (TypeError, ValueError):
        return 10


async def _append_rolling_summary(
    db: Session,
    agent: Agent,
    session_id: str,
    llm,
    final: str,
    saved_paths: list[str] | None = None,
    chat_id: str = "",
    user_message: str = "",
) -> str:
    """Append one rolling summary entry to ChatSummary.content."""
    _q = (
        ChatSummary.agent_id == agent.id,
        ChatSummary.session_id == session_id,
        ChatSummary.chat_id == (chat_id or ""),
    )
    if not (final or "").strip() or final.strip() in ("[已停止]", "未配置 LLM"):
        s = db.query(ChatSummary).filter(*_q).first()
        return (s.content if s else "") or ""
    if final.startswith("LLM 错误:"):
        s = db.query(ChatSummary).filter(*_q).first()
        return (s.content if s else "") or ""

    llm_timeout = getattr(agent, "llm_timeout", None)
    entry = await _make_rolling_line(
        llm, db, final, saved_paths or [], timeout=llm_timeout,
        user_message=user_message or "",
    )
    s = db.query(ChatSummary).filter(*_q).first()
    if not s:
        s = ChatSummary(agent_id=agent.id, session_id=session_id, chat_id=(chat_id or ""), content="")
        db.add(s)
    entries = [ln.strip() for ln in (s.content or "").splitlines() if ln.strip()]
    entries.append(entry)
    s.content = "\n".join(entries[-_summary_max_rounds(agent):])
    db.commit()
    return s.content


async def _maybe_summarize(db: Session, agent: Agent, session_id: str, llm, chat_id: str = "") -> str:
    """Append one rolling entry from the latest assistant turn."""
    last = (
        db.query(ChatMessage)
        .filter(
            ChatMessage.agent_id == agent.id,
            ChatMessage.session_id == session_id,
            ChatMessage.role == "assistant",
        )
        .order_by(ChatMessage.id.desc())
        .first()
    )
    if not last or not (last.content or "").strip():
        raise RuntimeError("当前会话暂无助手回复，无法生成滚动总结")
    final = last.content or ""
    saved: list[str] = []
    if last.meta:
        try:
            saved = list(json.loads(last.meta).get("saved_paths") or [])
        except Exception:
            saved = []
    user_message = ""
    try:
        u = (
            db.query(ChatMessage)
            .filter(
                ChatMessage.agent_id == agent.id,
                ChatMessage.session_id == session_id,
                ChatMessage.role == "user",
            )
            .order_by(ChatMessage.id.desc())
            .first()
        )
        user_message = (u.content or "") if u else ""
    except Exception:
        user_message = ""
    return await _append_rolling_summary(
        db, agent, session_id, llm, final, saved, chat_id=chat_id, user_message=user_message,
    )


async def generate_session_summary(db: Session, agent: Agent, session_id: str, chat_id: str = "") -> str:
    """Append one rolling summary entry from the latest assistant turn."""
    llm = db.query(LLMResource).filter(LLMResource.id == agent.llm_id).first()
    return await _maybe_summarize(db, agent, session_id, llm, chat_id=chat_id)
