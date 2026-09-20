"""ConversationalHandler — tool-free single-turn chat path.

Message construction and history trimming for pure conversation without
PLAN/MCP/FINAL protocol.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.services.agent_runtime.execution_policy import normalize_response_style

if TYPE_CHECKING:
    from app.agent.models import Agent

_CONVERSATIONAL_HISTORY_MAX = 6


def _request_terms(text: str) -> set[str]:
    """Small, redaction-safe set of meaningful request terms."""
    import re

    stop = {"请", "帮我", "一下", "这个", "那个", "如何", "怎么", "需要", "可以", "能够", "please"}
    terms = set()
    for token in re.findall(r"[A-Za-z][A-Za-z0-9_-]{2,}|[\u4e00-\u9fff]{2,}", text or ""):
        token = token.strip().lower()
        if token not in stop:
            terms.add(token)
    for token in re.findall(
        r"总结|归纳|拆解|分析|梳理|对比|提取|整理|项目|现状|风险|建议|结论|要点|来源|日期|主题|笔记",
        text or "",
    ):
        terms.add(token.lower())
    return terms


def assess_response_quality(user_message: str, reply: str, *, response_style: str = "adaptive") -> dict:
    """Deterministic quality gate for chat replies; never includes raw content."""
    import re

    text = str(reply or "").strip()
    request = str(user_message or "").strip()
    style = normalize_response_style(response_style)
    reasons: list[str] = []
    complex_request = bool(
        re.search(r"(?:总结|归纳|拆解|分析|梳理|对比|提取|整理|来源|日期|主题|笔记|条件如下|要求如下|\n\s*\d+[.、)])", request, re.I)
    )
    if complex_request and len(text) < 48:
        reasons.append("too_short")
    if text and re.search(r"(?:我可以帮你|请提供更多|收到你的消息|我会处理|你的问题是)[。.!！]?\s*$", text, re.I):
        reasons.append("boilerplate")
    terms = _request_terms(request)
    if complex_request and terms:
        covered = sum(1 for term in terms if term in text.lower())
        if covered < max(1, min(2, len(terms) // 2 or 1)):
            reasons.append("missing_entities")
    structure_requested = style in {"structured", "analytical"} or bool(
        re.search(r"(?:总结|拆解|分析|梳理|对比|提取|归纳)", request, re.I)
    )
    if structure_requested and complex_request and len(text) >= 60 and not re.search(r"(?:结论|摘要|要点|说明|\n\s*[-*•]|\n\s*\d+[.、)])", text):
        reasons.append("missing_structure")
    if text and request and len(text) <= len(request) * 1.15 and request[:24].lower() in text.lower():
        reasons.append("repetition")
    return {
        "checked": bool(complex_request or style in {"structured", "analytical"}),
        "passed": not reasons,
        "reasons": reasons[:4],
        "final_status": "passed" if not reasons else "incomplete",
    }


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

        style = normalize_response_style(getattr(agent, "response_style", "adaptive"))
        msgs: list[dict] = [{
            "role": "system",
            "content": SystemPromptBuilder.build_conversational_system(agent, response_style=style),
        }]
        n_rounds = max(1, int(getattr(agent, "history_length", None) or 3))
        trimmed = ConversationalHandler.trim_history(history, max_msgs=n_rounds * 2)
        # Drop trailing user (just committed); we append effective_message once
        if trimmed and trimmed[-1].get("role") == "user":
            trimmed = trimmed[:-1]
        msgs.extend(trimmed)
        msgs.append({"role": "user", "content": (effective_message or "").strip() or "你好"})
        return msgs
