"""Shared result types and enums for the agent runtime components."""

from dataclasses import dataclass, field
from enum import Enum, auto


class Decision(Enum):
    """Outcome of the decision engine after observing tool results."""
    CONTINUE = auto()        # Success — proceed to next step
    REPAIR_PARAMS = auto()   # Wrong params — fix and retry
    SWITCH_APPROACH = auto()  # Tool failure — use different tool/approach
    SEARCH_MORE = auto()     # Insufficient info — discover/describe first
    REPLAN = auto()          # Plan error — return to plan phase
    FINISH = auto()          # Goal complete — finalize


class ObservationOutcome(Enum):
    SUCCESS = "success"
    PARAM_ERROR = "param_error"
    TOOL_FAILURE = "tool_failure"
    INFO_GAP = "info_gap"
    PLAN_ERROR = "plan_error"
    COMPLETE = "complete"


class SchemaAlignment(Enum):
    """Result of soft-aligning SQL columns against describe schema."""
    OK = "ok"        # All columns matched — execute query as-is
    SOFT = "soft"    # Some columns unknown but core columns aligned — execute with notes
    HARD = "hard"    # View not in describe — execute with SELECT * fallback


@dataclass
class ToolStep:
    """A parsed tool call from the LLM reply."""
    action: str  # shell, mcp_tool_call, file_write, file_read, done, etc.
    normalized: str  # The raw action line
    args: dict = field(default_factory=dict)


@dataclass
class ToolResult:
    """Result of executing a single tool step."""
    action: str
    success: bool
    output: str
    error: str = ""
    rows_affected: int | None = None
    checkpoint_path: str = ""


@dataclass
class ObservationResult:
    """Processed observation from tool execution results.

    Structured analysis of tool output that feeds into DecisionEngine.decide().
    Provides engine-side classification independent of LLM interpretation.
    """
    outcome: ObservationOutcome
    errors: list[str] = field(default_factory=list)
    coaching_hints: list[str] = field(default_factory=list)
    should_repair: bool = False
    should_replan: bool = False
    should_finish: bool = False
    mcp_query_used: bool = False
    files_written: int = 0
    rows_fetched: int = 0
    # Extended fields for structured observe→decide pipeline
    data_landed: bool = False       # Whether data was written to disk (task/page_N.json)
    row_count: int = 0              # Number of rows returned
    page_full: bool = False         # Whether the page was full (>= 90% of page limit, still truncated)
    view_name: str = ""             # View involved (for query_ads_view / describe_ads_view)
    error_class: str = ""           # Classified error tag (timeout, unknown_column, etc.)
    suggested_action: str = ""      # Engine-suggested next action for LLM
    coach_hint: str = ""            # Coach text to inject into system message
    schema_alignment: str = ""      # SchemaAlignment value: ok / soft / hard
    retry_count: int = 0            # Number of engine-level retries performed


@dataclass
class ThinkResult:
    """Result of the think phase (LLM call)."""
    reply: str
    steps: list[ToolStep] = field(default_factory=list)
    is_clarify: bool = False
    is_final: bool = False


@dataclass
class TransitionResult:
    """Result of a phase transition check."""
    phase_changed: bool = False
    new_phase: str = ""
    inject_message: str | None = None
