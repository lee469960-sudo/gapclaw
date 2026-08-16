"""DecisionEngine — pure protocol parsing and reply cleaning.

Stateless primitives the LLM-driven loop uses to:
- Detect and extract tool invocations from LLM replies
- Detect FINAL replies
- Strip protocol markers / model monologue for user-facing display

No classification, stall detection, completion checks, or decision pipeline —
the LLM owns those. This module only parses what the model emitted.
"""

from __future__ import annotations

import re


class DecisionEngine:
    """Stateless parsing/cleaning primitives for the ReAct agent loop."""

    # ---- Tool detection ----

    @staticmethod
    def detect_action(reply: str, allowed: list[str] | None = None) -> tuple[str | None, str]:
        """Detect the primary action type and normalized tool line from a reply."""
        from app.services.tool_parser import detect_action_from_reply

        return detect_action_from_reply(reply, allowed or [])

    @staticmethod
    def extract_tool_steps(reply: str) -> list:
        """Extract structured tool steps from an LLM reply."""
        from app.services.tool_parser import extract_tool_steps

        return extract_tool_steps(reply)

    @staticmethod
    def looks_like_tool_call(text: str) -> bool:
        """True if the text contains tool-call markers (MCP:/SHELL:/WRITE: etc.)."""
        t = text or ""
        return bool(
            re.search(r"(?im)^\s*(MCP|SHELL|WRITE|READ|PATCH|THINK|HTTPMCP)\s*[:：]", t)
            and not re.search(r"(?im)^\s*FINAL\s*[:：]", t)
        )

    @staticmethod
    def is_final_reply(reply: str) -> bool:
        """True if the reply is a FINAL (task complete)."""
        return bool(re.search(r"(?im)^\s*FINAL\s*[:：]", reply or ""))

    @staticmethod
    def is_plan_reply(reply: str) -> bool:
        """True if the reply contains a PLAN block."""
        return bool(re.search(r"(?im)^\s*PLAN\s*[:：]", reply or ""))

    # ---- Reply cleaning ----

    @staticmethod
    def strip_llm_artifacts(text: str) -> str:
        """Remove LLM artifacts (think tags, trailing monologue, etc.)."""
        from app.services.tool_parser import _strip_llm_artifacts

        return _strip_llm_artifacts(text or "")

    @staticmethod
    def clean_display_text(text: str) -> str:
        """Strip protocol markers from display text for user-facing output."""
        t = DecisionEngine.strip_llm_artifacts(text or "")
        t = re.sub(r"(?<![A-Za-z/])(SHELL:|READ:|PATCH:|THINK:|MCP:|HTTPMCP:)\s*[^\n]+", "", t)
        t = re.sub(
            r"(?<![A-Za-z/])WRITE:\s*\S[^\n]*(?:\n(?!(?:SHELL:|WRITE:|READ:|PATCH:|FINAL:|THINK:|MCP:|HTTPMCP:))[^\n]*)*",
            "",
            t,
        )
        t = re.sub(r"\n{3,}", "\n\n", t)
        return t.strip()

    @staticmethod
    def clean_final_answer(text: str) -> str:
        """User-facing assistant content: strip protocol markers and leaked model monologue."""
        from app.services.tool_parser import extract_final_payload

        t = DecisionEngine.clean_display_text(extract_final_payload(text or ""))
        t = re.sub(r"(?im)^\s*FINAL\s*[:：]\s*", "", t).strip()
        t = re.sub(r"</?think>", "", t, flags=re.I)
        t = re.sub(
            r"(?is)(?:^|\n)\s*(?:"
            r"Let me write(?:\s+it)?(?:\s+now)?\.?"
            r"|I haven'?t yet[^\n]*"
            r"|the platform seems to think[^\n]*"
            r"|tool budget[^\n]*"
            r"|I should now[^\n]*"
            r")",
            "",
            t,
        )
        return t.strip()
