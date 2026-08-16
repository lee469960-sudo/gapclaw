"""Core data structures for the Agent Runtime's intelligent loop.

These types power the DecisionEngine → Verifier → Replanner pipeline.
They are separate from result_types.py (tool-level results) and define
plan-level and verification-level abstractions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


# ---- Plan structures ----


@dataclass
class PlanStep:
    """A single step in the agent's plan — describes WHAT to achieve, not HOW.

    Unlike the old TaskStep (which pre-specified MCP/tool/arguments at plan time),
    PlanStep only describes the goal and acceptance criteria. The actual execution
    action (which tool, what arguments) is decided per-iteration by DecisionEngine.
    """

    id: str  # e.g. "step-1", "step-schema-discovery"
    goal: str  # What this step must achieve (natural language)
    description: str = ""  # How to achieve it (guidance, not a prescription)
    status: str = "pending"  # pending | in_progress | completed | failed | skipped
    depends_on: list[str] = field(default_factory=list)  # step IDs that must complete first
    acceptance_criteria: list[str] = field(default_factory=list)  # Verifiable conditions
    retry_count: int = 0
    max_retries: int = 3
    plan_version: int = 1  # Incremented on replan


# ---- Action structures ----


class ActionType(Enum):
    """What kind of action the agent should take this iteration."""

    TOOL = "tool"  # Local tool (shell, file_write, file_read)
    MCP = "mcp"  # MCP server tool call
    LLM = "llm"  # Pure reasoning — no tool call, LLM should think more
    REPLAN = "replan"  # Modify remaining plan based on new information
    ASK_USER = "ask_user"  # Need user clarification before proceeding
    FINISH = "finish"  # Task complete — must pass Verifier
    WAIT = "wait"  # Wait for external event (future)


@dataclass
class AgentAction:
    """An action decided by DecisionEngine for the current iteration.

    This is the output of decide_next_action() — a structured decision,
    not a raw string parsed from the LLM reply.
    """

    type: ActionType
    target: str = ""  # Tool name, MCP tool name, etc.
    arguments: dict = field(default_factory=dict)  # Tool arguments
    reason: str = ""  # Why this action was chosen
    expected_result: str = ""  # What the engine expects to observe after execution
    step_id: str = ""  # Which PlanStep this action belongs to
    raw_reply: str = ""  # The raw LLM reply that produced this action (for trace)


# ---- Verification structures ----


@dataclass
class VerificationResult:
    """Output of checking whether a tool result achieved its intended goal.

    This is the P0 core type — it strictly separates "tool executed OK" from
    "goal achieved". A tool returning success=True does NOT mean the step is
    complete; only VerificationResult.success=True means that.
    """

    success: bool
    confidence: float = 1.0  # 0.0 — 1.0, how sure the Verifier is
    reason: str = ""  # Human-readable explanation
    next_action: str = "continue"  # continue | retry | replan | finish | ask_user
    suggestion: str = ""  # Actionable hint for the LLM (injected as coach hint)
    matched_criteria: list[str] = field(default_factory=list)  # Which criteria were met
    missed_criteria: list[str] = field(default_factory=list)  # Which criteria were NOT met
    evidence: str = ""  # What concrete evidence supports the conclusion
    source: str = "engine"  # "engine" (rule-based) or "llm" (LLM-verified)


# ---- Failure classification ----


class FailureType(Enum):
    """Classified failure reason for systematic recovery decisions.

    Each type maps to a different recovery strategy in the DecisionEngine.
    """

    TIMEOUT = "timeout"  # Network/MCP timeout — retry 1-2x with backoff
    INVALID_ARGUMENT = "invalid_argument"  # Wrong params — let LLM fix arguments
    PERMISSION_DENIED = "permission_denied"  # Auth error — ask user or skip
    TOOL_UNAVAILABLE = "tool_unavailable"  # MCP/tool down — try alternative tool
    BAD_RESULT = "bad_result"  # Tool ran OK but produced useless/malformed output
    CONTEXT_OVERFLOW = "context_overflow"  # Context window exceeded — trim & retry
    LLM_ERROR = "llm_error"  # LLM API error — retry or fail over
    VERIFICATION_FAILED = "verification_failed"  # Verifier rejected the result
    GOAL_NOT_ACHIEVED = "goal_not_achieved"  # Step ran out of retries without meeting criteria


# ---- Run state ----


class RunStatus(Enum):
    """State machine states for a Run (P1 — currently used only for status tracking)."""

    CREATED = "created"
    PLANNING = "planning"
    RUNNING = "running"
    WAITING_TOOL = "waiting_tool"
    VERIFYING = "verifying"
    REPLANNING = "replanning"
    WAITING_USER = "waiting_user"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
