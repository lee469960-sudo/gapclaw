"""Agent Runtime — modular, stateless ReAct agent orchestration."""

from app.services.agent_runtime.budget_manager import BudgetManager
from app.services.agent_runtime.conversational import ConversationalHandler
from app.services.agent_runtime.context import AgentContext
from app.services.agent_runtime.context_manager import ContextManager
from app.services.agent_runtime.decision_engine import DecisionEngine
from app.services.agent_runtime.export import ExportOrchestrator, PaginationEngine, RepairContext
from app.services.agent_runtime.hub import ChatStreamHub, hub, is_running, stop_chat, _running
from app.services.agent_runtime.runtime import AgentRuntime, run_agent
from app.services.agent_runtime.loop_state import AgentLoopState
from app.services.agent_runtime.message_manager import MessageManager
from app.services.agent_runtime.phase_manager import PhaseManager
from app.services.agent_runtime.result_types import (
    Decision,
    ObservationOutcome,
    ObservationResult,
    SchemaAlignment,
    ThinkResult,
    ToolResult,
    ToolStep,
    TransitionResult,
)
from app.services.agent_runtime.system_prompt import SystemPromptBuilder
from app.services.agent_runtime.tool_executor import ToolExecutor
from app.services.agent_runtime.types import (
    ActionType,
    AgentAction,
    FailureType,
    PlanStep,
    RunStatus,
    VerificationResult,
)
from app.services.agent_runtime.replanner import Replanner, ReplanContext
from app.services.agent_runtime.verifier import Verifier

__all__ = [
    # Hub
    "AgentRuntime",
    "run_agent",
    "ChatStreamHub",
    "hub",
    "is_running",
    "stop_chat",
    "_running",
    # Conversational
    "ConversationalHandler",
    "DecisionEngine",
    # Export
    "ExportOrchestrator",
    "PaginationEngine",
    "RepairContext",
    # Context & State
    "AgentContext",
    "AgentLoopState",
    "ContextManager",
    # Managers
    "BudgetManager",
    "MessageManager",
    "PhaseManager",
    "SystemPromptBuilder",
    "ToolExecutor",
    # Result types
    "Decision",
    "ObservationOutcome",
    "ObservationResult",
    "SchemaAlignment",
    "ThinkResult",
    "ToolResult",
    "ToolStep",
    "TransitionResult",
    # Plan/Verification types
    "ActionType",
    "AgentAction",
    "FailureType",
    "PlanStep",
    "RunStatus",
    "VerificationResult",
    "Replanner",
    "ReplanContext",
    "Verifier",
]
