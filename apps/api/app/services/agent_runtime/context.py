"""Immutable AgentContext — snapshot of the agent invocation context.

Constructed once per invocation from run_react_loop parameters.
"""

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from app.models import Agent, LLMResource, Sandbox


@dataclass(frozen=True)
class CodeExecutionContext:
    """Immutable control-plane snapshot attached only to an explicit Code run."""

    run_id: str
    project_id: str
    manifest_id: str
    manifest_version: int
    repository: str
    base_commit: str
    task_contract_json: str
    effective_policy_json: str
    timeout_seconds: int
    workspace_path: str
    source_facts_json: str
    runner_facts_json: str
    container_id: str
    image: str


@dataclass(frozen=True)
class AgentContext:
    """Immutable snapshot of the agent invocation context.

    All fields are resolved once at startup. The context is read-only
    and shared across all components during a single invocation.
    """

    # Core invocation params
    db: Session = field(compare=False, hash=False)
    agent: Agent = field(compare=False, hash=False)
    session_id: str
    user_message: str

    # Resolved entities (computed once at startup)
    llm: LLMResource | None = field(compare=False, hash=False)
    sandbox: Sandbox | None = field(compare=False, hash=False)
    allowed_actions: list[str] = field(default_factory=list)
    profile: str = "standard"
    code_execution: CodeExecutionContext | None = None
    tool_executor: Any = field(default=None, compare=False, hash=False)

    # Skill / MCP / RAG / HttpMcp bindings
    skill_ids: list[str] = field(default_factory=list)
    mcp_ids: list[str] = field(default_factory=list)
    rag_ids: list[str] = field(default_factory=list)
    httpmcp_ids: list[str] = field(default_factory=list)

    # Skill content
    skill_mds: list[tuple[str, str]] = field(default_factory=list)
    skill_names: list[str] = field(default_factory=list)
    mcp_names: list[str] = field(default_factory=list)

    # Workplace / files
    save_dir: str = ""

    # Message metadata
    note_content: str = ""
    message_meta: dict | None = field(default=None)

    # Runtime key
    chat_key: str = ""  # f"{agent.id}:{session_id}"

    # IM / channel context
    im_source: str = ""

    @classmethod
    def from_params(
        cls,
        *,
        db: Session,
        agent: Agent,
        session_id: str,
        user_message: str,
        message_meta: dict | None = None,
        llm: LLMResource | None = None,
        sandbox: Sandbox | None = None,
        allowed_actions: list[str] | None = None,
        profile: str = "standard",
        code_execution: CodeExecutionContext | None = None,
        tool_executor=None,
        skill_ids: list[str] | None = None,
        mcp_ids: list[str] | None = None,
        rag_ids: list[str] | None = None,
        httpmcp_ids: list[str] | None = None,
        skill_mds: list[tuple[str, str]] | None = None,
        skill_names: list[str] | None = None,
        mcp_names: list[str] | None = None,
        save_dir: str = "",
        note_content: str = "",
        im_source: str = "",
    ) -> "AgentContext":
        chat_key = f"{agent.id}:{session_id}"
        return cls(
            db=db,
            agent=agent,
            session_id=session_id,
            user_message=user_message,
            llm=llm,
            sandbox=sandbox,
            allowed_actions=list(allowed_actions or []),
            profile=profile,
            code_execution=code_execution,
            tool_executor=tool_executor,
            skill_ids=list(skill_ids or []),
            mcp_ids=list(mcp_ids or []),
            rag_ids=list(rag_ids or []),
            httpmcp_ids=list(httpmcp_ids or []),
            skill_mds=list(skill_mds or []),
            skill_names=list(skill_names or []),
            mcp_names=list(mcp_names or []),
            save_dir=save_dir,
            note_content=note_content,
            message_meta=message_meta,
            chat_key=chat_key,
            im_source=im_source,
        )
