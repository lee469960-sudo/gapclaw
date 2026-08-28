"""Minimal shared helpers for the single-engine agent_runtime.

Only the non-export utilities that survived the consolidation: MCP tool metadata
helpers used by SystemPromptBuilder.
"""

from __future__ import annotations

import json
import re
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.orm import Session
    from app.models import MCP

# ---- MCP tool metadata cache ----

_mcp_tools_cache: dict[str, tuple[float, list[dict], str]] = {}
_MCP_TOOLS_TTL_SEC: float = 600.0
_MCP_TOOLS_PROMPT_LIMIT: int = 30

# Known MCP tool call examples (getnote). Used in prompts. Ads SOP lives in the
# ads-sync-hub Skill's references, not in the engine catalog (see design D12).
_MCP_TOOL_EXAMPLES: dict[str, str] = {
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


# Pagination-related inputSchema params surfaced in the tool catalog so the model
# knows a tool supports paging (react-engine-v7 R1). Includes non-required fields.
_PAGINATION_PARAM_KEYS = ("offset", "limit", "page", "page_size", "cursor")


def _tool_pagination_params(tool: dict) -> list[str]:
    schema = _tool_input_schema(tool)
    props = schema.get("properties")
    if not isinstance(props, dict):
        return []
    return [k for k in _PAGINATION_PARAM_KEYS if k in props]


async def _get_mcp_tools_cached(mcp: MCP) -> tuple[list[dict], str]:
    """Return (tools, error) for a bound MCP, cached for ``_MCP_TOOLS_TTL_SEC``.

    ``error`` is "" on success (or when reachable but tool list is empty) and a
    short message on connect/list failure. The prompt surfaces a non-empty error
    as a soft "数据源不可达" warning instead of hardcoding tool names.
    """
    mid = mcp.id or ""
    now = time.monotonic()
    hit = _mcp_tools_cache.get(mid)
    if hit and now - hit[0] < _MCP_TOOLS_TTL_SEC:
        return hit[1], hit[2]
    try:
        from app.services.mcp_client import connect_mcp_detail  # lazy import
        detail = await connect_mcp_detail(mcp)
    except Exception as exc:
        err = f"{type(exc).__name__}: {exc}"[:200]
        _mcp_tools_cache[mid] = (now, [], err)
        return [], err
    tools = detail.get("tools") or []
    err = str(detail.get("error") or "").strip()
    _mcp_tools_cache[mid] = (now, tools, err)
    return tools, err


def _format_mcp_tools_for_prompt(mcp_name: str, tools: list[dict]) -> list[str]:
    lines = [
        f"- mcp {mcp_name}: 必须使用下列真实工具名（禁止编造 search_notes 等），格式 MCP: <工具名> {{json args}}",
    ]
    for t in tools[:_MCP_TOOLS_PROMPT_LIMIT]:
        if not isinstance(t, dict) or not t.get("name"):
            continue
        name = str(t["name"])
        desc = re.sub(r"\s+", " ", str(t.get("description") or "")).strip()[:80]
        req = _tool_required_fields(t)
        req_bit = f" required=[{', '.join(req)}]" if req else ""
        paging = _tool_pagination_params(t)
        paging_bit = f" 分页参数=[{', '.join(paging)}]" if paging else ""
        example = _MCP_TOOL_EXAMPLES.get(name)
        if example:
            lines.append(f"  - {example}{('  # ' + desc) if desc else ''}{req_bit}{paging_bit}")
        elif desc:
            lines.append(f"  - MCP: {name} {{...}}  # {desc}{req_bit}{paging_bit}")
        else:
            lines.append(f"  - MCP: {name} {{...}}{req_bit}{paging_bit}")
    if len(tools) > _MCP_TOOLS_PROMPT_LIMIT:
        lines.append(f"  - …共 {len(tools)} 个工具，其余名称以服务端 tools/list 为准")
    return lines


# ---- react-engine-v14 R3: ADS 数据视图目录缓存（跨会话 + TTL）----

# Mirrors the tools/list TTL cache pattern: module-level dict keyed on MCP id,
# so a list_ads_views / describe result survives across sessions in-process.
_ADS_VIEW_TTL_SEC: float = 3600.0
_ads_view_cache: dict[str, tuple[float, list[str]]] = {}  # mcp_id -> (ts, view names)
_ads_view_map_cache: dict[str, tuple[float, dict[str, str]]] = {}  # mcp_id -> (ts, {view: fields})

# Tool-name sets (best-effort; neutral — never a gate). list/describe 类工具名可能
# 因 MCP 而异，这里只作为「结果可蒸馏成目录」的软提示依据。
_ADS_LIST_TOOL_NAMES = frozenset({"list_ads_views", "list_views", "list_tables", "list_resources"})
_ADS_DESCRIBE_TOOL_NAMES = frozenset({"describe_ads_view", "describe_view", "describe_table"})
_ADS_VIEW_NAME_KEYS = ("view", "view_name", "view_id", "name", "table", "table_name")


def _looks_like_tool_error(text: str) -> bool:
    """Cheap failure heuristic for MCP tool results (mirrors runtime intent)."""
    t = (text or "").strip()
    if not t:
        return True
    low = t.lower()
    for marker in ("未在绑定 mcp", "no mcp", "工具执行异常", "permission_denied", "not found", "error"):
        if marker in low:
            return True
    return False


def _extract_ads_view_names(text: str) -> list[str]:
    """Best-effort view-name extraction from a list_ads_views result.

    Accepts a JSON list of strings, a list of dicts (picking a name-like key),
    or a dict whose value is such a list. Dedupes, preserving order.
    """
    try:
        data = json.loads(text)
    except Exception:
        return []
    items: list = []
    if isinstance(data, list):
        items = data
    elif isinstance(data, dict):
        for v in data.values():
            if isinstance(v, list):
                items = v
                break
    names: list[str] = []
    for it in items or []:
        if isinstance(it, str):
            names.append(it)
        elif isinstance(it, dict):
            picked = None
            for k in _ADS_VIEW_NAME_KEYS:
                if isinstance(it.get(k), str):
                    picked = it[k]
                    break
            if picked is None:
                for v in it.values():
                    if isinstance(v, str):
                        picked = v
                        break
            if picked:
                names.append(picked)
    seen: set[str] = set()
    out: list[str] = []
    for n in names:
        n = n.strip()
        if n and n not in seen:
            seen.add(n)
            out.append(n)
    return out


def _first_view_name_arg(args: dict) -> str:
    """Pull a view/table name from a describe tool's args dict, else ''."""
    if not isinstance(args, dict):
        return ""
    for k in _ADS_VIEW_NAME_KEYS:
        v = args.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    for v in args.values():
        if isinstance(v, str) and v.strip():
            return v.strip()
    return ""


def record_ads_view_catalog(mcp_id: str, tool: str, args: dict, result: str) -> None:
    """R3: cache a list_ads_views view-name list / a describe_ads_view field
    summary keyed on MCP id, for cross-session task_context injection.

    Soft only: no-op on error/empty results, never throws, never gates.
    """
    if _looks_like_tool_error(result):
        return
    mcp_id = mcp_id or ""
    now = time.monotonic()
    if tool in _ADS_LIST_TOOL_NAMES:
        names = _extract_ads_view_names(result)
        if names:
            _ads_view_cache[mcp_id] = (now, names)
    elif tool in _ADS_DESCRIBE_TOOL_NAMES:
        view = _first_view_name_arg(args)
        if not view:
            return
        try:
            from app.services.mcp_client import _json_keys_summary  # lazy: avoid cycle
        except Exception:
            return
        summary = _json_keys_summary(result)
        if not summary:
            return
        prev = _ads_view_map_cache.get(mcp_id)
        mapping = dict(prev[1]) if prev else {}
        mapping[view] = summary
        _ads_view_map_cache[mcp_id] = (now, mapping)


def get_ads_views_cached(mcp_ids: list[str]) -> tuple[list[str], dict[str, str]]:
    """Return (view_names, view_map) merged across bound MCPs, TTL-filtered."""
    now = time.monotonic()
    names: list[str] = []
    mapping: dict[str, str] = {}
    for mid in mcp_ids or []:
        hit = _ads_view_cache.get(mid)
        if hit and now - hit[0] < _ADS_VIEW_TTL_SEC:
            names.extend(hit[1])
        mhit = _ads_view_map_cache.get(mid)
        if mhit and now - mhit[0] < _ADS_VIEW_TTL_SEC:
            mapping.update(mhit[1])
    return names, mapping


def build_ads_view_catalog(mcp_ids: list[str]) -> str:
    """Render cached ADS view names + view→field mapping as a task_context block."""
    names, mapping = get_ads_views_cached(mcp_ids)
    if not names and not mapping:
        return ""
    lines: list[str] = []
    if names:
        lines.append("可用视图名：")
        lines.extend(f"- {n}" for n in names[:200])
    if mapping:
        lines.append("已确认字段口径（view → 结构摘要）：")
        lines.extend(f"- {v}: {s}" for v, s in list(mapping.items())[:80])
    return "\n".join(lines)
