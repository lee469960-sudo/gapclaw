"""DecisionEngine — reply analysis, stall detection, and loop decision primitives.

Provides stateless methods that the main ReAct loop uses to:
- Parse and classify LLM replies
- Decide the next action based on reply + tool outcome + state
- Detect stalls (no progress, empty replies, repeated failures)
- Determine when the task is complete
- Guide phase transitions for export tasks

All methods are static and take explicit parameters.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.services.agent_runtime.loop_state import AgentLoopState
    from app.services.agent_runtime.result_types import (
        Decision,
        ObservationOutcome,
    )
    from app.services.agent_runtime.types import AgentAction, VerificationResult


class DecisionEngine:
    """Stateless decision primitives for the ReAct agent loop.

    Does NOT own the loop — the caller (AgentRuntime / react_engine) drives
    iteration and calls these methods for guidance.
    """

    # ---- Reply classification ----

    @staticmethod
    def detect_action(reply: str, allowed: list[str] | None = None) -> tuple[str | None, str]:
        """Detect the primary action type and normalized tool line from a reply.

        Returns (action_type, normalized_line). action_type is one of:
        'mcp_tool_call', 'shell', 'file_write', 'file_read', 'patch',
        'think', 'final', None (no tool detected).
        """
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

    @staticmethod
    def classify_reply(reply: str) -> str:
        """Classify reply into one of: 'final', 'plan', 'tool', 'text', 'empty'.

        Used by the main loop to decide the next action.
        """
        if not reply or not reply.strip():
            return "empty"
        if DecisionEngine.is_final_reply(reply):
            return "final"
        if DecisionEngine.is_plan_reply(reply):
            return "plan"
        if DecisionEngine.looks_like_tool_call(reply):
            return "tool"
        return "text"

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

    # ---- Stall detection ----

    @staticmethod
    def is_stalling(
        state: AgentLoopState | None = None,
        *,
        empty_llm_streak: int | None = None,
        no_progress: int | None = None,
        max_empty_streak: int = 3,
        max_no_progress: int = 6,
        ran_any_tool: bool | None = None,
        tools_effective: bool = False,
        had_explicit_final: bool = False,
    ) -> bool:
        """True when the agent appears to be stalling.

        Conditions: repeated empty replies, no progress over many turns,
        or tools consistently ineffective without forward motion.

        If state is provided and individual params are None, values are
        read from the state object.
        """
        if had_explicit_final:
            return False

        if state is not None:
            if empty_llm_streak is None:
                empty_llm_streak = state.empty_llm_streak
            if no_progress is None:
                no_progress = state.no_progress
            if ran_any_tool is None:
                ran_any_tool = state.ran_any_tool

        empty_llm_streak = empty_llm_streak or 0
        no_progress = no_progress or 0
        ran_any_tool = ran_any_tool or False

        if empty_llm_streak >= max_empty_streak:
            return True
        if no_progress >= max_no_progress:
            return True
        if not ran_any_tool and not tools_effective and no_progress >= 3:
            return True
        return False

    # ---- Completion detection ----

    @staticmethod
    def is_task_complete(
        reply: str,
        *,
        had_explicit_final: bool = False,
        soft_finish_requested: bool = False,
        export_like: bool = False,
        export_phase: str = "",
        finalize_hint_injected: bool = False,
    ) -> bool:
        """True when the task should be considered complete.

        Handles both explicit FINAL and soft-finish scenarios
        (export finalize phase, forced completion).
        """
        if had_explicit_final:
            return True
        if DecisionEngine.is_final_reply(reply):
            return True
        if soft_finish_requested:
            return True
        if export_like and export_phase == "finalize" and finalize_hint_injected:
            return True
        return False

    # ---- Next-action suggestion ----

    @staticmethod
    def suggest_next_action(
        reply_class: str,
        *,
        export_like: bool = False,
        export_phase: str = "",
        ran_any_tool: bool = False,
        tools_effective: bool = False,
        progress_lines: list[str] | None = None,
    ) -> str:
        """Return a human-readable suggestion for the next loop action.

        Used for debugging and progress logging.
        """
        del progress_lines
        if reply_class == "empty":
            return "retry" if not ran_any_tool else "nudge_finish"
        if reply_class == "final":
            return "complete"
        if reply_class == "plan":
            if export_like and export_phase == "plan":
                return "validate_plan"
            return "accept_plan"
        if reply_class == "tool":
            if tools_effective:
                return "continue"
            return "repair_or_switch"
        if export_like and not ran_any_tool:
            return "prompt_discover"
        return "continue"

    # ---- Unified decide pipeline ----

    @staticmethod
    def decide(
        reply: str,
        *,
        state: AgentLoopState | None = None,
        tool_result: str | None = None,
        action: str = "",
        normalized: str = "",
        verification: VerificationResult | None = None,
        export_like: bool = False,
        export_phase: str = "",
        ran_any_tool: bool = False,
    ) -> Decision:
        """Main decision entry point for the ReAct loop.

        Takes the LLM reply, optional tool result, and current loop state,
        runs the full pipeline (classify → verify → check stalls/completion)
        and returns a Decision enum.

        This is the single method the main loop should call each iteration.
        """
        from app.services.agent_runtime.result_types import Decision

        # 1. Classify the reply
        reply_class = DecisionEngine.classify_reply(reply)

        # 2. Check completion
        if DecisionEngine.is_task_complete(
            reply,
            had_explicit_final=bool(state and state.had_explicit_final),
            soft_finish_requested=bool(state and state.soft_finish_requested),
            export_like=export_like,
            export_phase=export_phase,
            finalize_hint_injected=bool(state and state.finalize_hint_injected),
        ):
            return Decision.FINISH

        # 3. Handle empty replies
        if reply_class == "empty":
            if state and DecisionEngine.is_stalling(state):
                return Decision.REPLAN
            return Decision.CONTINUE

        # 4. Handle plan replies
        if reply_class == "plan":
            return Decision.CONTINUE

        # 5. Handle tool replies — verify execution outcome
        if reply_class == "tool" and tool_result is not None:
            outcome = DecisionEngine.classify_tool_outcome(
                tool_result, action=action, normalized=normalized,
            )
            # If we have a structured verification, use it to refine the decision
            if verification and not verification.success:
                if verification.next_action == "replan":
                    return Decision.REPLAN
                if verification.next_action == "ask_user":
                    return Decision.CONTINUE  # caller handles ask_user separately
            return DecisionEngine.outcome_to_decision(outcome)

        # 6. Text-only reply — check for stalling
        if reply_class == "text" and state:
            if DecisionEngine.is_stalling(state):
                return Decision.REPLAN

        return Decision.CONTINUE

    @staticmethod
    def decide_next_action(
        reply: str,
        *,
        state: AgentLoopState | None = None,
        tool_result: str | None = None,
        action: str = "",
        normalized: str = "",
        verification: VerificationResult | None = None,
        export_like: bool = False,
        export_phase: str = "",
        ran_any_tool: bool = False,
        tools_effective: bool = False,
    ) -> AgentAction:
        """Produce a structured AgentAction from the decision pipeline.

        Returns an AgentAction with type, target, arguments, and reason
        that the main loop can execute directly.
        """
        from app.services.agent_runtime.result_types import Decision
        from app.services.agent_runtime.types import ActionType, AgentAction

        decision = DecisionEngine.decide(
            reply,
            state=state,
            tool_result=tool_result,
            action=action,
            normalized=normalized,
            verification=verification,
            export_like=export_like,
            export_phase=export_phase,
            ran_any_tool=ran_any_tool,
        )

        reply_class = DecisionEngine.classify_reply(reply)

        if decision == Decision.FINISH:
            return AgentAction(
                type=ActionType.FINISH,
                reason="Task complete — FINAL detected or all criteria met",
                raw_reply=reply,
            )

        if decision == Decision.REPLAN:
            return AgentAction(
                type=ActionType.REPLAN,
                reason="Replan needed — stalling, repeated failures, or verification rejected",
                raw_reply=reply,
            )

        if reply_class == "tool":
            action_type_str, normalized_line = DecisionEngine.detect_action(reply)
            if action_type_str:
                act_type = ActionType.MCP if action_type_str in ("mcp_tool_call", "httpmcp_call") else ActionType.TOOL
                return AgentAction(
                    type=act_type,
                    target=action_type_str,
                    arguments={"normalized": normalized_line},
                    reason=f"Executing tool action: {action_type_str}",
                    raw_reply=reply,
                )
            # Fallback: action detection failed but reply contains tool markers
            act_type = ActionType.MCP if "MCP:" in reply or "HTTPMCP:" in reply else ActionType.TOOL
            return AgentAction(
                type=act_type,
                target=action or act_type.value,
                arguments={"normalized": normalized_line or reply},
                reason=f"Executing detected tool via markers",
                raw_reply=reply,
            )

        if reply_class == "plan":
            return AgentAction(
                type=ActionType.LLM,
                reason="Plan detected — accepting and proceeding",
                raw_reply=reply,
            )

        if reply_class == "empty":
            return AgentAction(
                type=ActionType.LLM,
                reason="Empty reply — nudging LLM to continue or finish",
                raw_reply=reply,
            )

        return AgentAction(
            type=ActionType.LLM,
            reason=f"Text reply ({reply_class}) — continuing with LLM reasoning",
            raw_reply=reply,
        )

    # ---- Outcome classification ----

    @staticmethod
    def classify_tool_outcome(
        tool_result: str | None,
        *,
        action: str = "",
        normalized: str = "",
    ) -> ObservationOutcome:
        """Classify a tool execution result into an outcome category.

        Returns an ObservationOutcome enum value.
        """
        from app.services.agent_runtime.result_types import ObservationOutcome

        if tool_result is None:
            return ObservationOutcome.TOOL_FAILURE

        result_text = str(tool_result).strip()
        if not result_text:
            return ObservationOutcome.TOOL_FAILURE

        from app.services.agent_runtime.tool_executor import ToolExecutor

        if action in ("mcp_tool_call", "httpmcp_call"):
            if ToolExecutor.is_tool_failure(result_text):
                if "缺少 view" in result_text or "缺少 view_name" in result_text:
                    return ObservationOutcome.PARAM_ERROR
                if "禁止" in result_text or "限流" in result_text or "拦截" in result_text:
                    return ObservationOutcome.PLAN_ERROR
                return ObservationOutcome.TOOL_FAILURE

        if action == "shell" and "traceback" in result_text.lower():
            return ObservationOutcome.TOOL_FAILURE

        # Empty/info-gap detection
        from app.services.agent_runtime.utils import _parse_mcp_rows
        rows = _parse_mcp_rows(result_text)
        if rows is not None and len(rows) == 0:
            return ObservationOutcome.INFO_GAP

        return ObservationOutcome.SUCCESS

    @staticmethod
    def outcome_to_decision(outcome: ObservationOutcome) -> Decision:
        """Map an observation outcome to the loop decision."""
        from app.services.agent_runtime.result_types import Decision, ObservationOutcome

        mapping = {
            ObservationOutcome.SUCCESS: Decision.CONTINUE,
            ObservationOutcome.PARAM_ERROR: Decision.REPAIR_PARAMS,
            ObservationOutcome.TOOL_FAILURE: Decision.SWITCH_APPROACH,
            ObservationOutcome.INFO_GAP: Decision.SEARCH_MORE,
            ObservationOutcome.PLAN_ERROR: Decision.REPLAN,
            ObservationOutcome.COMPLETE: Decision.FINISH,
        }
        return mapping.get(outcome, Decision.CONTINUE)
