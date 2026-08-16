"""Execute ADS schema-discovery metadata actions."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from app.services.export_schema_discovery import (
    ViewSchemaHint,
    describe_views_from_schema_actions,
    described_schema_views,
    parse_describe_schema_hint,
    schema_list_captured,
)


SchemaMcpCall = Callable[[dict[str, Any], str, str], Awaitable[str]]
SchemaFailureCheck = Callable[[str], bool]
SchemaCallResultHook = Callable[["SchemaDiscoveryCall"], Awaitable[None]]


@dataclass
class SchemaDiscoveryCall:
    action_type: str
    normalized: str
    view: str = ""
    ok: bool = False
    error: str = ""
    text: str = ""
    schema_hint: ViewSchemaHint | None = None


@dataclass
class SchemaDiscoveryExecutionResult:
    ran: int = 0
    ads_views_list_text: str = ""
    schema_hints: dict[str, ViewSchemaHint] = field(default_factory=dict)
    calls: list[SchemaDiscoveryCall] = field(default_factory=list)


async def execute_schema_discovery_actions(
    actions: list[dict[str, Any]] | None,
    *,
    execute_mcp: SchemaMcpCall,
    is_failure: SchemaFailureCheck,
    schema_discovery: dict[str, Any] | None = None,
    schema_hints: dict[str, ViewSchemaHint] | None = None,
    fallback_views: list[str] | tuple[str, ...] | None = None,
    on_call_result: SchemaCallResultHook | None = None,
) -> SchemaDiscoveryExecutionResult:
    """Execute schema metadata MCP calls and parse successful describe output.

    The caller owns UI, trace writes, and column-plan refresh. This helper only
    decides which MCP lines to run and parses successful results.
    """
    result = SchemaDiscoveryExecutionResult(
        schema_hints=dict(schema_hints or {}),
    )
    schema = schema_discovery if isinstance(schema_discovery, dict) else {}
    acts = [a for a in (actions or []) if isinstance(a, dict)]

    needs_list = any(
        str(a.get("action_type") or "") == "discover_ads_views"
        for a in acts
    )
    if needs_list and not schema_list_captured(schema):
        action = {"action_type": "discover_ads_views"}
        normalized = "MCP: list_ads_views {}"
        text = await execute_mcp(action, normalized, "")
        call = SchemaDiscoveryCall(
            action_type="discover_ads_views",
            normalized=normalized,
            ok=bool(text) and not is_failure(text),
            text=text or "",
            error="" if text else "list_ads_views failed",
        )
        if call.ok:
            result.ran += 1
            result.ads_views_list_text = text or ""
        elif text:
            call.error = text[:800]
        result.calls.append(call)
        if on_call_result is not None:
            await on_call_result(call)

    wanted_views = describe_views_from_schema_actions(
        acts,
        fallback_views=fallback_views,
    )
    described = described_schema_views(
        schema,
        schema_hints=result.schema_hints,
    )
    for view in [v for v in dict.fromkeys(wanted_views) if v not in described]:
        action = {"action_type": "describe_ads_views", "views": [view]}
        normalized = (
            "MCP: describe_ads_view "
            + json.dumps({"view_name": view}, ensure_ascii=False)
        )
        text = await execute_mcp(action, normalized, view)
        call = SchemaDiscoveryCall(
            action_type="describe_ads_views",
            normalized=normalized,
            view=view,
            ok=False,
            text=text or "",
            error="" if text else "describe_ads_view failed",
        )
        if text and not is_failure(text):
            hint = parse_describe_schema_hint(text or "", view=view)
            if hint.fields:
                result.schema_hints[view] = hint
                result.ran += 1
                call.ok = True
                call.schema_hint = hint
                described.add(view)
            else:
                call.error = "describe_ads_view 未解析到字段"
        elif text:
            call.error = text[:800]
        result.calls.append(call)
        if on_call_result is not None:
            await on_call_result(call)

    return result
