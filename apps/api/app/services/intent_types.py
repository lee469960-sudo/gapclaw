"""Intent data types shared by the resource-binding guardrail.

Extracted from the deleted intent_router.py so mcp_resource_bind can keep its
MCP-agnostic metric intent shape without the deterministic routing layer.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any


@dataclass
class MetricIntentItem:
    """MCP-agnostic metric intent (no view/role hardcodes)."""

    goal: str
    kind: str = "other"
    entity_hint: str = ""
    resource_hint: str = ""

    def to_dict(self) -> dict[str, str]:
        return {
            "goal": self.goal,
            "kind": self.kind,
            "entity_hint": self.entity_hint,
            "resource_hint": self.resource_hint,
        }


@dataclass
class TurnIntent:
    intent: str = "data_query"
    reason: str = ""
    wants_deliverable: bool = False
    query_goal: str = ""
    conversation_action: str = "respond"
    metrics: list[str] = field(default_factory=list)
    metric_intents: list[MetricIntentItem] = field(default_factory=list)
    time_window: dict[str, Any] | None = None
    task_relation: str = "unknown"
    verification_required: bool = False
    verification_targets: list[str] = field(default_factory=list)
    requested_artifacts: list[str] = field(default_factory=list)
    result_shape: str = "unspecified"
    temporal_grain: str = "unspecified"
    raw: dict[str, Any] = field(default_factory=dict)
    source: str = "fallback"  # llm | fallback

    @property
    def is_chat(self) -> bool:
        return self.intent == "chat"

    @property
    def is_export(self) -> bool:
        return self.intent == "export_report"

    @property
    def wants_session_summary(self) -> bool:
        return self.is_chat and self.conversation_action == "summarize_session"

    @property
    def needs_tools(self) -> bool:
        return self.intent != "chat"


def _strip_think_noise(text: str) -> str:
    """Remove model <think>…</think> blocks (and orphan closers) before JSON parse."""
    raw = text or ""
    raw = re.sub(r"<think\b[^>]*>[\s\S]*?</think>", "", raw, flags=re.I)
    raw = re.sub(r"</think>", "", raw, flags=re.I)
    raw = re.sub(r"<think\b[^>]*>", "", raw, flags=re.I)
    return raw.strip()


def _extract_json_object(text: str) -> dict[str, Any] | None:
    raw = _strip_think_noise(text or "")
    if not raw:
        return None
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.I)
        raw = re.sub(r"\s*```$", "", raw)
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else None
    except Exception:
        pass
    # Decode one JSON value at a time. A greedy ``{.*}`` slice makes valid
    # structured output unreadable when a provider adds brace-bearing prose
    # before or after the actual object.
    decoder = json.JSONDecoder()
    for index, char in enumerate(raw):
        if char != "{":
            continue
        try:
            data, _ = decoder.raw_decode(raw[index:])
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        if isinstance(data, dict):
            return data
    return None
