"""Agent Runtime — single LLM-driven ReAct agent orchestration."""

from app.services.agent_runtime.conversational import ConversationalHandler
from app.services.agent_runtime.context import AgentContext
from app.services.agent_runtime.context_manager import ContextManager
from app.services.agent_runtime.decision_engine import DecisionEngine
from app.services.agent_runtime.hub import (
    ChatStreamHub,
    hub,
    is_running,
    stop_chat,
    _running,
)
from app.services.agent_runtime.runtime import AgentRuntime, run_agent
from app.services.agent_runtime.loop_state import AgentLoopState
from app.services.agent_runtime.system_prompt import SystemPromptBuilder
from app.services.agent_runtime.tool_executor import ToolExecutor
from app.services.agent_runtime.tool_router import ToolRouter

__all__ = [
    # Runtime
    "AgentRuntime",
    "run_agent",
    # Hub
    "ChatStreamHub",
    "hub",
    "is_running",
    "stop_chat",
    "_running",
    # Conversational
    "ConversationalHandler",
    # Parsing
    "DecisionEngine",
    # Context & State
    "AgentContext",
    "AgentLoopState",
    "ContextManager",
    # Managers
    "SystemPromptBuilder",
    "ToolExecutor",
    "ToolRouter",
]
