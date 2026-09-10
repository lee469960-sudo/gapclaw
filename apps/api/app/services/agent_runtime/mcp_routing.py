"""MCP candidate selection primitives used before catalog discovery.

The helpers in this module deliberately operate on stored MCP metadata only.
They must never connect to an MCP endpoint or inspect its tool catalog.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from app.models import MCP
from app.services.llm_client import chat_completion

logger = logging.getLogger(__name__)

_MIN_ROUTE_CONFIDENCE = 0.5


@dataclass(frozen=True)
class McpRouteCandidate:
    """A bound MCP that contains enough capability metadata for LLM routing."""

    id: str
    name: str
    tags: str
    description: str
    modified_at: str

    def to_prompt_dict(self) -> dict[str, str]:
        return {
            "id": self.id,
            "name": self.name,
            "tags": self.tags,
            "description": self.description,
        }


@dataclass(frozen=True)
class McpRouteDecision:
    """Validated LLM routing result; an empty selection is always safe."""

    selected_mcp_ids: list[str]
    reason: str = ""
    needs_more_capability: bool = False
    failure: str = ""


def _route_prompt(
    user_message: str,
    candidates: list[McpRouteCandidate],
    *,
    selected_mcp_ids: list[str] | None = None,
    trigger: str = "initial",
) -> list[dict[str, str]]:
    """Build a metadata-only routing request, intentionally excluding catalogs."""
    candidate_data = [candidate.to_prompt_dict() for candidate in candidates]
    task_text = (user_message or "").strip()
    task_folded = task_text.casefold()
    explicitly_named = [
        candidate.id
        for candidate in candidates
        if any(
            value and value.casefold() in task_folded
            for value in (candidate.id, candidate.name)
        )
    ]
    context = {
        "task": task_text[:4000],
        "trigger": trigger,
        "already_selected_mcp_ids": list(selected_mcp_ids or []),
        "explicitly_named_mcp_ids": explicitly_named,
        "candidates": candidate_data,
    }
    return [
        {
            "role": "system",
            "content": (
                "你是 MCP 能力路由器。仅依据任务和候选能力元数据选择完成任务所需的最小 MCP 集合。"
                "不得假设候选之外的资源，不得调用工具。只输出 JSON 对象，格式为 "
                '{"selected_mcp_ids":["..."],"reason":"...","confidence":0.0,"needs_more_capability":false}。'
                "当无法可靠判断时返回空数组且 confidence 小于 0.5。"
            ),
        },
        {"role": "user", "content": json.dumps(context, ensure_ascii=False)},
    ]


def _parse_route_response(
    response: str,
    candidates: list[McpRouteCandidate],
) -> McpRouteDecision:
    """Parse a strict, candidate-bounded routing response without raising."""
    try:
        data: Any = json.loads((response or "").strip())
    except (TypeError, ValueError, json.JSONDecodeError):
        return McpRouteDecision([], failure="invalid_json")
    if not isinstance(data, dict):
        return McpRouteDecision([], failure="invalid_shape")
    raw_ids = data.get("selected_mcp_ids")
    confidence = data.get("confidence")
    if not isinstance(raw_ids, list) or isinstance(confidence, bool):
        return McpRouteDecision([], failure="invalid_shape")
    try:
        confidence = float(confidence)
    except (TypeError, ValueError):
        return McpRouteDecision([], failure="invalid_confidence")
    if not 0 <= confidence <= 1 or confidence < _MIN_ROUTE_CONFIDENCE:
        return McpRouteDecision([], failure="low_confidence")

    allowed_ids = {candidate.id for candidate in candidates}
    selected: list[str] = []
    for raw_id in raw_ids:
        mcp_id = str(raw_id or "").strip()
        if mcp_id in allowed_ids and mcp_id not in selected:
            selected.append(mcp_id)
    return McpRouteDecision(
        selected_mcp_ids=selected,
        reason=str(data.get("reason") or "").strip()[:500],
        needs_more_capability=bool(data.get("needs_more_capability")),
        failure="" if selected or not raw_ids else "unrecognized_ids",
    )


async def route_mcp_candidates(
    *,
    llm,
    db: Session,
    user_message: str,
    candidates: list[McpRouteCandidate],
    selected_mcp_ids: list[str] | None = None,
    trigger: str = "initial",
    timeout: int | None = None,
) -> McpRouteDecision:
    """Ask the Agent LLM to choose from eligible MCP candidates only."""
    if not candidates or not llm:
        return McpRouteDecision([], failure="no_candidates")
    messages = _route_prompt(
        user_message, candidates, selected_mcp_ids=selected_mcp_ids, trigger=trigger
    )
    try:
        response = await chat_completion(
            llm, messages, max_tokens=400, db=db, timeout=timeout
        )
    except Exception as exc:
        logger.warning("MCP routing failed trigger=%s: %s", trigger, type(exc).__name__)
        return McpRouteDecision([], failure="llm_error")
    if not isinstance(response, str):
        return McpRouteDecision([], failure="invalid_response")
    return _parse_route_response(response, candidates)


def build_mcp_route_candidates(
    db: Session,
    mcp_ids: list[str],
    allowed_actions: list[str],
) -> list[McpRouteCandidate]:
    """Return authorized, describable bound MCPs without performing MCP I/O."""
    if "mcp_tool_call" not in set(allowed_actions or []):
        return []
    # Lightweight unit-test/runtime adapters may provide only persistence
    # methods (for saving chat messages) rather than the SQLAlchemy query API.
    # Candidate routing is an optional optimisation; retain the normal tool
    # path instead of making those adapters fail before the first LLM call.
    if not hasattr(db, "query"):
        return []

    candidates: list[McpRouteCandidate] = []
    seen: set[str] = set()
    for mcp_id in mcp_ids or []:
        mcp_id = str(mcp_id or "").strip()
        if not mcp_id or mcp_id in seen:
            continue
        seen.add(mcp_id)
        mcp = db.query(MCP).filter(MCP.id == mcp_id).first()
        if not mcp:
            continue
        tags = str(mcp.tags or "").strip()
        description = str(mcp.description or "").strip()
        if not tags and not description:
            continue
        candidates.append(
            McpRouteCandidate(
                id=str(mcp.id or mcp_id),
                name=str(mcp.name or mcp_id).strip() or mcp_id,
                tags=tags,
                description=description,
                modified_at=str(mcp.modified_at or ""),
            )
        )
    return candidates
