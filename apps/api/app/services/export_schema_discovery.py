"""Runtime MCP schema discovery helpers for ADS exports."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any


_FIELD_NAME_RE = re.compile(r"\b([a-zA-Z_][a-zA-Z0-9_]*)\b")
_FIELD_KEYS = ("name", "field", "column", "column_name", "字段", "字段名")
_COMMENT_KEYS = ("comment", "remark", "remarks", "description", "备注", "说明")
SCHEMA_DISCOVERY_ACTION_TYPES = frozenset({
    "discover_ads_views",
    "describe_ads_views",
})


@dataclass
class ViewSchemaHint:
    view: str = ""
    fields: list[str] = field(default_factory=list)
    comments: dict[str, str] = field(default_factory=dict)
    view_comment: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "view": self.view,
            "fields": list(self.fields),
            "comments": dict(self.comments),
            "view_comment": self.view_comment,
        }


def _dedupe(items: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for item in items:
        s = str(item or "").strip()
        if not s or s.lower() in seen:
            continue
        seen.add(s.lower())
        out.append(s)
    return out


def _load_jsonish(text: str) -> Any:
    raw = (text or "").strip()
    if not raw:
        return None
    try:
        return json.loads(raw)
    except Exception:
        pass
    m = re.search(r"(\{.*\}|\[.*\])", raw, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except Exception:
        return None


def _walk_schema(obj: Any, fields: list[str], comments: dict[str, str]) -> None:
    if isinstance(obj, dict):
        field = ""
        for key in _FIELD_KEYS:
            if obj.get(key):
                field = str(obj.get(key) or "").strip()
                break
        if field:
            fields.append(field)
            for key in _COMMENT_KEYS:
                if obj.get(key):
                    comments[field] = str(obj.get(key) or "").strip()
                    break
        for value in obj.values():
            _walk_schema(value, fields, comments)
    elif isinstance(obj, list):
        for item in obj:
            _walk_schema(item, fields, comments)


def _extract_view_comment(obj: Any, text: str) -> str:
    if isinstance(obj, dict):
        for key in ("table_comment", "view_comment", "comment", "remark", "description", "表备注", "表说明"):
            val = obj.get(key)
            if val and not any(k in obj for k in _FIELD_KEYS):
                return str(val).strip()
    for line in (text or "").splitlines():
        m = re.search(r"(?:表备注|表说明|view_comment|table_comment)\s*[:：]\s*(.+)", line, re.I)
        if m:
            return m.group(1).strip()
    return ""


def parse_describe_schema_hint(
    text: str,
    *,
    view: str = "",
) -> ViewSchemaHint:
    fields: list[str] = []
    comments: dict[str, str] = {}
    obj = _load_jsonish(text)
    view_comment = _extract_view_comment(obj, text)
    if obj is not None:
        _walk_schema(obj, fields, comments)
    if not fields:
        for line in (text or "").splitlines():
            clean = line.strip().strip("|")
            if not clean:
                continue
            # Markdown/table-ish: name | type | comment
            parts = [p.strip() for p in clean.split("|") if p.strip()]
            candidates = parts or re.split(r"[\s,，:：]+", clean)
            for cand in candidates[:2]:
                m = _FIELD_NAME_RE.fullmatch(cand.strip("`'\" "))
                if not m:
                    continue
                name = m.group(1)
                if name.lower() in {"name", "field", "type", "comment", "字段", "备注"}:
                    continue
                fields.append(name)
                if len(parts) >= 3:
                    comments.setdefault(name, parts[-1])
                break
    fields = _dedupe(fields)
    comments = {k: v for k, v in comments.items() if k in set(fields) and v}
    return ViewSchemaHint(
        view=view,
        fields=fields,
        comments=comments,
        view_comment=view_comment,
    )


def schema_cache_to_view_fields(
    cache: dict[str, ViewSchemaHint | dict[str, Any]] | None,
) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for view, hint in (cache or {}).items():
        if isinstance(hint, ViewSchemaHint):
            fields = hint.fields
        elif isinstance(hint, dict):
            fields = hint.get("fields") or []
        else:
            fields = []
        vals = [str(f).strip() for f in fields if str(f).strip()]
        if vals:
            out[str(view)] = vals
    return out


def schema_list_captured(schema_discovery: dict[str, Any] | None) -> bool:
    schema = schema_discovery if isinstance(schema_discovery, dict) else {}
    listed = schema.get("list_ads_views")
    return bool(listed is True or (isinstance(listed, dict) and listed.get("captured")))


def described_schema_views(
    schema_discovery: dict[str, Any] | None,
    *,
    schema_hints: dict[str, ViewSchemaHint | dict[str, Any]] | None = None,
) -> set[str]:
    schema = schema_discovery if isinstance(schema_discovery, dict) else {}
    described = schema.get("described_views")
    out = {
        str(view).strip()
        for view in (schema_hints or {}).keys()
        if str(view).strip()
    }
    if isinstance(described, dict):
        out.update(str(view).strip() for view in described.keys() if str(view).strip())
    return out


def filter_schema_discovery_actions(
    repair_plan: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    repair = repair_plan if isinstance(repair_plan, dict) else {}
    out: list[dict[str, Any]] = []
    for action in repair.get("actions") or []:
        if not isinstance(action, dict):
            continue
        if str(action.get("action_type") or "") in SCHEMA_DISCOVERY_ACTION_TYPES:
            out.append(dict(action))
    return out


def build_schema_discovery_actions(
    *,
    repair_plan: dict[str, Any] | None = None,
    schema_discovery: dict[str, Any] | None = None,
    planned_views: list[str] | tuple[str, ...] | None = None,
    schema_hints: dict[str, ViewSchemaHint | dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Return metadata actions needed before data query execution.

    RepairPlan actions are authoritative when present. Otherwise, required
    Type-B schema discovery can synthesize first-run metadata actions.
    """
    actions = filter_schema_discovery_actions(repair_plan)
    if actions:
        schema = schema_discovery if isinstance(schema_discovery, dict) else {}
        if schema.get("required") and not schema_list_captured(schema):
            has_list = any(
                str(a.get("action_type") or "") == "discover_ads_views"
                for a in actions
            )
            if not has_list:
                actions = [
                    {
                        "action_type": "discover_ads_views",
                        "reason": "当前 run 缺少 MCP 接口/视图列表发现证据",
                    },
                    *actions,
                ]
        return actions
    schema = schema_discovery if isinstance(schema_discovery, dict) else {}
    if not schema.get("required"):
        return []

    described = described_schema_views(schema, schema_hints=schema_hints)
    missing_views = [
        str(view).strip()
        for view in dict.fromkeys(planned_views or [])
        if str(view).strip() and str(view).strip() not in described
    ]
    out: list[dict[str, Any]] = []
    if not schema_list_captured(schema):
        out.append({
            "action_type": "discover_ads_views",
            "reason": "首轮 Type-B schema-first：自动获取 MCP 接口/视图列表",
        })
    if missing_views:
        out.append({
            "action_type": "describe_ads_views",
            "views": missing_views,
            "reason": "首轮 Type-B schema-first：自动获取计划 view 字段/表备注",
        })
    return out


def describe_views_from_schema_actions(
    actions: list[dict[str, Any]] | tuple[dict[str, Any], ...] | None,
    *,
    fallback_views: list[str] | tuple[str, ...] | None = None,
) -> list[str]:
    views: list[str] = []
    for action in actions or []:
        if not isinstance(action, dict):
            continue
        if str(action.get("action_type") or "") != "describe_ads_views":
            continue
        views.extend(str(v).strip() for v in (action.get("views") or []) if str(v).strip())
    if not views:
        views.extend(str(v).strip() for v in (fallback_views or []) if str(v).strip())
    return [v for v in dict.fromkeys(views) if v]
