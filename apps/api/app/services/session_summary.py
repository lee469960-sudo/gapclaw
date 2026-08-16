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
from app.services.agent_runtime.decision_engine import DecisionEngine
from app.services.llm_client import chat_completion

if TYPE_CHECKING:
    from sqlalchemy.orm import Session
    from app.models import Agent

logger = logging.getLogger(__name__)

_ROLLING_MAX_ENTRIES = 40


def _heuristic_rolling_line(
    stamp: str,
    final: str,
    saved_paths: list[str],
    gap_tag: str = "",
) -> str:
    cleaned = re.sub(r"\s+", " ", DecisionEngine.clean_display_text(final or "")).strip()
    first = cleaned[:120] if cleaned else "完成一轮任务"
    if "。" in first:
        first = first.split("。")[0] + "。"
    files = ""
    if saved_paths:
        names = ", ".join(p.split("/")[-1] for p in saved_paths[:3])
        files = f" 文件：{names}。"
    return f"- [{stamp}] {first}{files}{gap_tag or ''}".strip()


async def _make_rolling_line(
    llm,
    db: Session,
    final: str,
    saved_paths: list[str],
    timeout: int | None = None,
    gap_tag: str = "",
) -> str:
    stamp = now_str()[:16]  # YYYY-MM-DD HH:MM
    if not llm:
        return _heuristic_rolling_line(stamp, final, saved_paths, gap_tag=gap_tag)
    paths_txt = ", ".join(saved_paths[:5]) if saved_paths else "无"
    prompt = (
        f"请根据本轮助手最终回复，写【恰好一行】滚动总结，格式严格为：\n"
        f"[{stamp}] 完成……（含关键数字若有）。核心表/要点……\n"
        f"不要换行，不超过 180 字，不要加前缀说明。\n\n"
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
            return _heuristic_rolling_line(stamp, final, saved_paths, gap_tag=gap_tag)
        if not line.startswith("["):
            line = f"[{stamp}] {line}"
        if not line.startswith("- "):
            line = f"- {line}"
        if gap_tag and gap_tag.strip() not in line:
            line = f"{line.rstrip()}{gap_tag}"
        return line[:280]
    except Exception:
        logger.exception("rolling summary llm failed")
        return _heuristic_rolling_line(stamp, final, saved_paths, gap_tag=gap_tag)


def _summary_max_chars(agent: Agent) -> int:
    """Agent.summary_max_words is treated as a character budget (字 ≈ chars)."""
    try:
        n = int(getattr(agent, "summary_max_words", None) or 2000)
    except (TypeError, ValueError):
        n = 2000
    return max(50, min(n, 50000))


def _clamp_summary_text(text: str, max_chars: int) -> str:
    """Keep newest rolling lines; drop from head until within max_chars."""
    text = (text or "").strip()
    if not text or len(text) <= max_chars:
        return text
    entries = [ln.strip() for ln in text.splitlines() if ln.strip()]
    while entries and len("\n".join(entries)) > max_chars:
        entries.pop(0)
    joined = "\n".join(entries)
    if len(joined) > max_chars:
        joined = joined[-max_chars:]
    return joined


async def _append_rolling_summary(
    db: Session,
    agent: Agent,
    session_id: str,
    llm,
    final: str,
    saved_paths: list[str] | None = None,
    gap_tag: str = "",
) -> str:
    """Append one rolling summary entry to ChatSummary.content."""
    if not (final or "").strip() or final.strip() in ("[已停止]", "未配置 LLM"):
        s = db.query(ChatSummary).filter(
            ChatSummary.agent_id == agent.id, ChatSummary.session_id == session_id
        ).first()
        return (s.content if s else "") or ""
    if final.startswith("LLM 错误:"):
        s = db.query(ChatSummary).filter(
            ChatSummary.agent_id == agent.id, ChatSummary.session_id == session_id
        ).first()
        return (s.content if s else "") or ""

    llm_timeout = getattr(agent, "llm_timeout", None)
    entry = await _make_rolling_line(
        llm, db, final, saved_paths or [], timeout=llm_timeout, gap_tag=gap_tag or "",
    )
    s = db.query(ChatSummary).filter(
        ChatSummary.agent_id == agent.id, ChatSummary.session_id == session_id
    ).first()
    if not s:
        s = ChatSummary(agent_id=agent.id, session_id=session_id, content="")
        db.add(s)
    entries = [ln.strip() for ln in (s.content or "").splitlines() if ln.strip()]
    entries.append(entry)
    if len(entries) > _ROLLING_MAX_ENTRIES:
        entries = entries[-_ROLLING_MAX_ENTRIES:]
    s.content = _clamp_summary_text("\n".join(entries), _summary_max_chars(agent))
    db.commit()
    return s.content


async def _maybe_summarize(db: Session, agent: Agent, session_id: str, llm) -> str:
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
    return await _append_rolling_summary(db, agent, session_id, llm, final, saved)


async def generate_session_summary(db: Session, agent: Agent, session_id: str) -> str:
    """Append one rolling summary entry from the latest assistant turn."""
    llm = db.query(LLMResource).filter(LLMResource.id == agent.llm_id).first()
    return await _maybe_summarize(db, agent, session_id, llm)
