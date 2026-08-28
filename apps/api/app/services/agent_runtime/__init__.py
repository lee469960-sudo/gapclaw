"""Agent Runtime — single LLM-driven ReAct agent orchestration."""

from app.services.agent_runtime.conversational import ConversationalHandler
from app.services.agent_runtime.context import AgentContext, CodeExecutionContext
from app.services.agent_runtime.context_manager import ContextManager
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
    # Context & State
    "AgentContext",
    "CodeExecutionContext",
    "AgentLoopState",
    "ContextManager",
    # Managers
    "SystemPromptBuilder",
]
