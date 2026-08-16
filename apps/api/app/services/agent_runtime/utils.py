"""Minimal shared helpers for the single-engine agent_runtime.

Only the non-export utilities that survived the consolidation: MCP tool metadata
helpers used by SystemPromptBuilder.
"""

from __future__ import annotations

import re
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.orm import Session
    from app.models import MCP

# ---- MCP tool metadata cache ----

_mcp_tools_cache: dict[str, tuple[float, list[dict]]] = {}
_MCP_TOOLS_TTL_SEC: float = 600.0
_MCP_TOOLS_PROMPT_LIMIT: int = 30

# Known MCP tool call examples (ads-sync-hub / getnote). Used in prompts.
_MCP_TOOL_EXAMPLES: dict[str, str] = {
    "list_ads_views": "MCP: list_ads_views {}",
    "describe_ads_view": 'MCP: describe_ads_view {"view_name":"<待确认资源>"}',
    "query_ads_view": (
        'MCP: query_ads_view {"view":"<已绑定资源>",'
        '"where":{"<已确认字段>":"<条件值>"}}'
    ),
    "query_ads_metric": 'MCP: query_ads_metric {"metric_id":"daily_stat_by_date","params":{"stat_date":"2025-01-01"}}',
    "list_notes": 'MCP: list_notes {"since_id":0}',
    "recall": 'MCP: recall {"query":"关键词"}',
}


def _bound_mcp_names(db: Session, mcp_ids: list[str]) -> list[str]:
    """Return display names for bound MCP resources."""
    from app.models import MCP  # lazy import

    names: list[str] = []
    for mid in mcp_ids or []:
        mcp = db.query(MCP).filter(MCP.id == mid).first()
        if mcp:
            names.append(mcp.name or mid)
    return names


def _tool_input_schema(tool: dict) -> dict:
    schema = tool.get("inputSchema") or tool.get("input_schema") or {}
    return schema if isinstance(schema, dict) else {}


def _tool_required_fields(tool: dict) -> list[str]:
    schema = _tool_input_schema(tool)
    req = schema.get("required")
    if isinstance(req, list):
        return [str(x) for x in req if x]
    return []


async def _get_mcp_tools_cached(mcp: MCP) -> list[dict]:
    mid = mcp.id or ""
    now = time.monotonic()
    hit = _mcp_tools_cache.get(mid)
    if hit and now - hit[0] < _MCP_TOOLS_TTL_SEC and hit[1]:
        return hit[1]
    try:
        from app.services.mcp_client import connect_mcp_detail  # lazy import
        detail = await connect_mcp_detail(mcp)
    except Exception:
        return hit[1] if hit else []
    tools = detail.get("tools") or []
    if tools:
        _mcp_tools_cache[mid] = (now, tools)
    return tools


def _format_mcp_tools_for_prompt(mcp_name: str, tools: list[dict]) -> list[str]:
    lines = [
        f"- mcp {mcp_name}: 必须使用下列真实工具名（禁止编造 search_notes 等），格式 MCP: <工具名> {{json args}}",
    ]
    names = {str(t.get("name")) for t in tools if isinstance(t, dict) and t.get("name")}
    if "describe_ads_view" in names or "query_ads_view" in names:
        lines.append(
            "  【硬规则】describe_ads_view / query_ads_view 禁止空参数 {}；"
            "未知视图名时先 list_ads_views；describe 用 view_name，query 用 view。"
            " SOP：list_ads_views → describe_ads_view(view_name) → query_ads_view(view)。"
        )
    for t in tools[:_MCP_TOOLS_PROMPT_LIMIT]:
        if not isinstance(t, dict) or not t.get("name"):
            continue
        name = str(t["name"])
        desc = re.sub(r"\s+", " ", str(t.get("description") or "")).strip()[:80]
        req = _tool_required_fields(t)
        req_bit = f" required=[{', '.join(req)}]" if req else ""
        example = _MCP_TOOL_EXAMPLES.get(name)
        if example:
            lines.append(f"  - {example}{('  # ' + desc) if desc else ''}{req_bit}")
        elif desc:
            lines.append(f"  - MCP: {name} {{...}}  # {desc}{req_bit}")
        else:
            lines.append(f"  - MCP: {name} {{...}}{req_bit}")
    if len(tools) > _MCP_TOOLS_PROMPT_LIMIT:
        lines.append(f"  - …共 {len(tools)} 个工具，其余名称以服务端 tools/list 为准")
    return lines
