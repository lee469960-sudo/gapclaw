"""Column-driven export planning: map output headers → MCP sources + query graph.

Type-B default path: plan card → aggregate/specialized MCP → SHELL write.
"""

from __future__ import annotations

from copy import deepcopy
import re
from typing import Any

from app.services.metric_registry import match_metric, metric_to_column_fragment

# fetch_mode values
FETCH_AGG = "agg"
FETCH_FIELD = "field"
FETCH_LOOKUP = "lookup"
FETCH_SEQUENCE = "sequence"
FETCH_TOP_N = "top_n"
FETCH_FLAG = "flag"
FETCH_DERIVED = "derived"
FETCH_UNKNOWN = "unknown"

def _normalize_header(header: str) -> str:
    t = (header or "").strip()
    t = re.sub(r"[（(].*?[）)]", "", t)
    return t.strip().lower()


def _enrich_sources(
    sources: list[dict],
    *,
    preferred_view_by_role: dict[str, str] | None = None,
) -> list[dict]:
    pref = {
        str(k).strip(): str(v).strip()
        for k, v in (preferred_view_by_role or {}).items()
        if str(k).strip() and str(v).strip()
    }
    out: list[dict] = []
    for s in sources or []:
        if not isinstance(s, dict):
            continue
        existing = str(s.get("view") or "").strip()
        role = str(s.get("role") or "").strip()
        if not existing:
            existing = pref.get(role, "")
        if not existing:
            continue
        item = dict(s)
        if role:
            item["role"] = role
        item["view"] = existing
        fields = s.get("fields") or []
        item["fields"] = [str(f).strip() for f in fields if str(f).strip()]
        out.append(item)
    return out


def _default_fetch_mode(kind: str) -> str:
    return {
        "aggregate": FETCH_AGG,
        "field": FETCH_FIELD,
        "lookup": FETCH_LOOKUP,
        "compute": FETCH_DERIVED,
        "sequence": FETCH_SEQUENCE,
        "flag": FETCH_FLAG,
        "unknown": FETCH_UNKNOWN,
    }.get(kind or "", FETCH_UNKNOWN)


def _column_preferred_role(col: dict | None) -> str:
    item = col if isinstance(col, dict) else {}
    agg = item.get("agg_spec") if isinstance(item.get("agg_spec"), dict) else {}
    role = str(agg.get("role") or "").strip().lower()
    if role:
        return role
    for src in item.get("sources") or []:
        if not isinstance(src, dict):
            continue
        role = str(src.get("role") or "").strip().lower()
        if role:
            return role
    return ""


def match_column_rule(header: str) -> dict[str, Any] | None:
    """Return the MetricSpec-derived contract fragment for a header."""
    h = _normalize_header(header)
    if not h:
        return None
    m = match_metric(header) or match_metric(h)
    if m is not None:
        return metric_to_column_fragment(m)
    return None


def _column_plan_views(frag: dict | None) -> list[str]:
    item = frag if isinstance(frag, dict) else {}
    views: list[str] = []
    seen: set[str] = set()

    def _add(view: str) -> None:
        name = str(view or "").strip()
        if not name or name in seen:
            return
        seen.add(name)
        views.append(name)

    spec = item.get("agg_spec") if isinstance(item.get("agg_spec"), dict) else {}
    _add(str(spec.get("view") or ""))
    for src in item.get("sources") or []:
        if not isinstance(src, dict):
            continue
        _add(str(src.get("view") or ""))
    return views


def column_plan_entry_is_bound(col: dict | None) -> bool:
    """True when runtime MCP evidence has resolved this column to concrete resources."""
    item = col if isinstance(col, dict) else {}
    status = str(item.get("binding_status") or "").strip().lower()
    if status == "bound":
        return True
    views = _column_plan_views(item)
    if not views:
        return False
    schema_source = str(item.get("schema_source") or "").strip().lower()
    if schema_source in {"binding", "describe_ads_view"}:
        return True
    return False


def _derive_contract_source_method(frag: dict | None) -> tuple[str, str]:
    """Return semantic source/method from the contract fragment itself."""
    frag = frag if isinstance(frag, dict) else {}
    src = str(frag.get("数据来源") or "").strip()
    method = str(frag.get("统计方法") or "").strip()
    if src and method:
        return src, method
    spec = frag.get("agg_spec") if isinstance(frag.get("agg_spec"), dict) else {}
    view = str(spec.get("view") or "").strip()
    if not view:
        views: list[str] = []
        for s in frag.get("sources") or []:
            if isinstance(s, dict):
                v = str(s.get("view") or "").strip()
                if v and v not in views:
                    views.append(v)
        for r in spec.get("roles") if isinstance(spec.get("roles"), list) else []:
            v = str(r).strip()
            if v and v not in views:
                views.append(v)
        src = " + ".join(f"`{v}`" for v in views) if views else "—"
    else:
        src = f"`{view}`"
        if spec.get("needs_game_dim"):
            src += " + 游戏维表（待 MCP 绑定）"
    if not method:
        filt = str(spec.get("filter_sql") or "").strip()
        mode = str(spec.get("mode") or frag.get("fetch_mode") or frag.get("kind") or "")
        compute = str(frag.get("compute") or frag.get("口径") or "").strip()
        if filt and compute:
            method = f"{filt.replace(' = ', '=')} → {compute}"
        elif filt:
            method = filt.replace(" = ", "=")
        elif compute:
            method = compute
        elif mode:
            method = mode
        else:
            method = "—"
    return src or "—", method or "—"


def derive_column_source_method(frag: dict | None) -> tuple[str, str]:
    """Return (数据来源, 统计方法) for plan card / FINAL field docs."""
    frag = frag if isinstance(frag, dict) else {}
    # Registry fragments describe metric semantics only.  They are not runtime
    # authorization to query their historical ADS view; that comes from the
    # LLM binding against the live MCP catalog.
    if not column_plan_entry_is_bound(frag):
        method = "待按表备注确认资源与字段"
        if "未完整" in str(frag.get("统计方法") or frag.get("口径") or ""):
            method += "（未完整）"
        return "待 MCP list/describe + LLM 绑定", method
    return _derive_contract_source_method(frag)


def column_source_method(col: dict | None) -> tuple[str, str]:
    """Public helper: (数据来源, 统计方法) from a column_plan entry."""
    return derive_column_source_method(col if isinstance(col, dict) else {})


def plan_one_column(header: str) -> dict[str, Any]:
    """Build one column_plan entry from rules (unknown → kind=unknown)."""
    text = str(header or "").strip()
    frag = match_column_rule(text)
    if not frag:
        return {
            "header": text,
            "kind": "unknown",
            "fetch_mode": FETCH_UNKNOWN,
            "sources": [],
            "join_on": "uid",
            "filter": {},
            "compute": "",
            "口径": "",
            "time_field": "",
            "agg_spec": {},
            "数据来源": "—",
            "统计方法": "未识别列（交付时诚实留空）",
            "rule": "none",
        }
    agg = dict(frag.get("agg_spec") or {})
    agg_role = str(agg.get("role") or "").strip()
    agg_view = str(agg.get("view") or "").strip()
    pref = {agg_role: agg_view} if agg_role and agg_view else None
    sources = _enrich_sources(list(frag.get("sources") or []), preferred_view_by_role=pref)
    # Align sources.view with agg_spec contract when role matches (bet daily vs archive)
    if agg_role and agg_view:
        synced = False
        for s in sources:
            if str(s.get("role") or "").strip() == agg_role:
                s["view"] = agg_view
                synced = True
        if not synced:
            sources.append({"role": agg_role, "view": agg_view, "fields": []})
    kind = str(frag.get("kind") or "field")
    src, method = _derive_contract_source_method(frag)
    return {
        "header": text,
        "kind": kind,
        "fetch_mode": str(frag.get("fetch_mode") or _default_fetch_mode(kind)),
        "sources": sources,
        "join_on": str(frag.get("join_on") or "uid"),
        "filter": dict(frag.get("filter") or {}),
        "compute": str(frag.get("compute") or ""),
        "口径": str(frag.get("口径") or frag.get("compute") or ""),
        "time_field": str(frag.get("time_field") or ""),
        "agg_spec": agg,
        "depends_on": list(frag.get("depends_on") or []),
        "数据来源": src,
        "统计方法": method,
        "rule": "dict",
        "metric_id": str(frag.get("metric_id") or ""),
        "cost_level": str(frag.get("cost_level") or ""),
        "failure_policy": str(frag.get("failure_policy") or ""),
        "validation_rules": list(frag.get("validation_rules") or []),
        "binding_status": "pending",
    }


def build_column_plan(headers: list[str] | None) -> list[dict[str, Any]]:
    """Map ordered output headers → column_plan list."""
    plan: list[dict[str, Any]] = []
    seen: set[str] = set()
    for h in headers or []:
        text = str(h or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        plan.append(plan_one_column(text))
    return plan


def carry_forward_bound_columns(
    plan: list[dict] | None,
    *,
    prior_plan: list[dict] | None = None,
) -> list[dict[str, Any]]:
    """Carry same-header runtime bindings from a prior plan into a rebuilt plan.

    This preserves user-confirmed / schema-confirmed column-resource bindings
    across follow-up export turns where we rebuild the plan from headers again.
    """
    current = [dict(c) for c in (plan or []) if isinstance(c, dict)]
    if not current or not prior_plan:
        return current

    prior_by_header: dict[str, dict[str, Any]] = {}
    for row in prior_plan or []:
        if not isinstance(row, dict):
            continue
        header = _normalize_header(str(row.get("header") or ""))
        if header and column_plan_entry_is_bound(row):
            prior_by_header[header] = row

    if not prior_by_header:
        return current

    carry_keys = (
        "kind",
        "fetch_mode",
        "sources",
        "join_on",
        "filter",
        "compute",
        "口径",
        "time_field",
        "agg_spec",
        "depends_on",
        "数据来源",
        "统计方法",
        "rule",
        "metric_id",
        "cost_level",
        "failure_policy",
        "validation_rules",
        "binding_status",
        "schema_source",
        "schema_adjustments",
        "schema_score",
    )

    out: list[dict[str, Any]] = []
    for col in current:
        header = _normalize_header(str(col.get("header") or ""))
        prior = prior_by_header.get(header)
        if not prior:
            out.append(col)
            continue
        merged = dict(col)
        for key in carry_keys:
            if key in prior:
                merged[key] = deepcopy(prior.get(key))
        notes = [
            str(x).strip()
            for x in (merged.get("schema_adjustments") or [])
            if str(x).strip()
        ]
        carry_note = "沿用上轮同列已绑定资源"
        if carry_note not in notes:
            notes.append(carry_note)
        merged["schema_adjustments"] = notes
        out.append(merged)
    return out


def apply_binding_hints_to_column_plan(
    plan: list[dict] | None,
    *,
    bindings: list[dict] | None = None,
) -> list[dict[str, Any]]:
    """Soft-apply runtime MCP bindings back onto known column sources."""
    goal_to_resource: dict[str, str] = {}
    for row in bindings or []:
        if not isinstance(row, dict):
            continue
        goal = _normalize_header(str(row.get("goal") or ""))
        resource = str(row.get("resource") or row.get("view") or "").strip()
        if goal and resource:
            goal_to_resource[goal] = resource
    if not goal_to_resource:
        return [dict(c) for c in (plan or []) if isinstance(c, dict)]

    role_to_resource: dict[str, str] = {}
    for col in plan or []:
        if not isinstance(col, dict):
            continue
        role = _column_preferred_role(col)
        resource = goal_to_resource.get(_normalize_header(str(col.get("header") or "")), "")
        if role and resource:
            role_to_resource[role] = resource

    out: list[dict[str, Any]] = []
    for col in plan or []:
        if not isinstance(col, dict):
            continue
        c = dict(col)
        role = _column_preferred_role(c)
        candidate_views = {
            str(v).strip()
            for v in _column_plan_views(c)
            if str(v).strip()
        }
        explicit_resource = goal_to_resource.get(
            _normalize_header(str(c.get("header") or "")),
            "",
        )
        role_resource = role_to_resource.get(role, "")
        if role_resource and candidate_views and role_resource not in candidate_views:
            role_resource = ""
        resource = explicit_resource or (
            "" if column_plan_entry_is_bound(c) else role_resource
        )
        if not resource:
            out.append(c)
            continue

        changed = False
        original_agg_view = str((c.get("agg_spec") or {}).get("view") or "").strip()
        source_rows = [s for s in (c.get("sources") or []) if isinstance(s, dict)]
        sources = []
        for src in source_rows:
            item = dict(src)
            is_primary = (
                len(source_rows) == 1
                or (
                    original_agg_view
                    and str(item.get("view") or "").strip() == original_agg_view
                )
            )
            if is_primary and str(item.get("view") or "").strip() != resource:
                item["view"] = resource
                changed = True
            sources.append(item)
        if sources:
            c["sources"] = sources

        agg = dict(c.get("agg_spec") or {})
        if agg:
            if str(agg.get("view") or "").strip() != resource:
                agg["view"] = resource
                c["agg_spec"] = agg
                changed = True

        if changed or resource:
            c["schema_source"] = "binding"
            c["binding_status"] = "bound"
            notes = [
                str(x).strip()
                for x in (c.get("schema_adjustments") or [])
                if str(x).strip()
            ]
            note = f"resource 绑定改写 source -> `{resource}`"
            if note not in notes:
                notes.append(note)
            c["schema_adjustments"] = notes
            c["数据来源"] = f"`{resource}`"
        out.append(c)
    return out


_SQL_KEYWORDS = {
    "SELECT", "FROM", "WHERE", "AND", "OR", "AS", "GROUP", "BY", "ORDER", "LIMIT",
    "OFFSET", "IN", "NOT", "NULL", "DISTINCT", "COUNT", "SUM", "MAX", "MIN", "AVG",
    "TOSTRING", "UPPER", "JSONEXTRACTSTRING", "TODATE", "FROMUNIXTIMESTAMP64MILLI",
    # ClickHouse: uniqExact.upper() == UNIQEXACT (not UNIQUE…)
    "COUNTIF", "UNIQEXACT", "UNIQEXACTIF", "UNIQUEXACT", "UNIQUEXACTIF",
    "UNIQ", "UNIQTHETA", "UNIQCOMBINED", "UNIQHLL12", "GROUPUNIQARRAY", "ARRAYJOIN",
    "COALESCE", "CASE", "WHEN", "THEN", "ELSE", "END", "LEFT", "RIGHT", "INNER",
    "OUTER", "JOIN", "ON", "HAVING", "IF", "IFNULL", "ISNULL", "ARGMAX", "ARGMIN",
    "BETWEEN", "LIKE", "IS", "TRUE", "FALSE", "UNION", "ALL", "WITH", "OVER",
    "PARTITION", "DESC", "ASC",
}
_NON_MEASURE_FIELDS = {
    "uid", "user_id", "id", "status", "type", "goods_type", "channel_id", "game_id",
    "create_time", "finish_time", "update_time", "register_time", "stat_date",
}
_HEADER_KEYWORDS = {
    "充值": ("充值", "recharge", "deposit", "pay", "price", "amount"),
    "提现": ("提现", "withdraw", "cash", "amount"),
    "下注": ("下注", "投注", "bet", "bet_value", "bet_sc"),
    "投注": ("下注", "投注", "bet", "bet_value", "bet_sc"),
    "返奖": ("返奖", "派彩", "win", "result", "result_value"),
    "余额": ("余额", "balance", "sc"),
    "退款": ("退款", "refund"),
    "银行卡": ("银行卡", "card", "account"),
    "渠道": ("渠道", "channel"),
    "游戏": ("游戏", "game"),
    "注册": ("注册", "register"),
    "次数": ("次数", "count", "cnt", "nums", "times"),
    "金额": ("金额", "amount", "price", "value", "sum"),
    "手续费": ("手续费", "fee"),
}


def _schema_hint_map(schema_hints: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for view, raw in (schema_hints or {}).items():
        if hasattr(raw, "to_dict"):
            raw = raw.to_dict()
        if not isinstance(raw, dict):
            continue
        fields = [str(f).strip() for f in (raw.get("fields") or []) if str(f).strip()]
        comments = {
            str(k).strip(): str(v).strip()
            for k, v in (raw.get("comments") or {}).items()
            if str(k).strip() and str(v).strip()
        }
        view_comment = str(raw.get("view_comment") or "").strip()
        if fields:
            out[str(view)] = {
                "fields": fields,
                "comments": comments,
                "view_comment": view_comment,
            }
    return out


def _view_schema(schema: dict[str, dict[str, Any]], view: str) -> dict[str, Any]:
    return schema.get(str(view or "")) or {}


def _schema_fields(hint: dict[str, Any]) -> list[str]:
    return [str(f).strip() for f in (hint.get("fields") or []) if str(f).strip()]


def _schema_field_set(hint: dict[str, Any]) -> set[str]:
    return {f.lower() for f in _schema_fields(hint)}


def _role_from_view(view: str) -> str:
    v = (view or "").lower()
    if "user_info" in v:
        return "user"
    if "pay" in v or "recharge" in v:
        return "pay"
    if "cash" in v or "withdraw" in v:
        return "cash"
    if "bet" in v or "gameuser" in v:
        return "bet"
    if "channel" in v:
        return "channel"
    if "game" in v:
        return "game"
    return ""


def _view_score_for_header(header: str, view: str, hint: dict[str, Any]) -> int:
    h = str(header or "")
    hay = (str(view or "") + " " + str(hint.get("view_comment") or "")).lower()
    score = 0
    role = _role_from_view(view)
    role_cn = {
        "pay": ("充值", "支付"),
        "cash": ("提现", "出款"),
        "bet": ("下注", "投注", "流水"),
        "user": ("用户", "注册", "余额"),
        "channel": ("渠道",),
        "game": ("游戏",),
    }
    for cn in role_cn.get(role, ()):
        if cn in h:
            score += 6
    for cn, aliases in _HEADER_KEYWORDS.items():
        if cn not in h:
            continue
        for alias in aliases:
            if str(alias).lower() in hay:
                score += 4
    return score


def _field_score_for_header(header: str, field: str, comment: str = "") -> int:
    h = str(header or "")
    hay = (str(field or "") + " " + str(comment or "")).lower()
    score = 0
    if h and h in comment:
        score += 8
    for cn, aliases in _HEADER_KEYWORDS.items():
        if cn not in h:
            continue
        for alias in aliases:
            if str(alias).lower() in hay:
                score += 3
    if "总" in h and any(x in hay for x in ("sum", "amount", "price", "value")):
        score += 1
    return score


def _pick_schema_field_with_score(
    header: str,
    hint: dict[str, Any],
    *,
    exclude: set[str] | None = None,
) -> tuple[str, int]:
    comments = hint.get("comments") if isinstance(hint.get("comments"), dict) else {}
    excluded = {x.lower() for x in (exclude or set())}
    best = ""
    best_score = 0
    for field in _schema_fields(hint):
        if field.lower() in excluded:
            continue
        if field.lower() in _NON_MEASURE_FIELDS and not any(
            k in str(header or "") for k in ("用户", "注册", "渠道", "游戏")
        ):
            continue
        score = _field_score_for_header(header, field, str(comments.get(field) or ""))
        if score > best_score:
            best = field
            best_score = score
    return best, best_score


def _pick_schema_field(
    header: str,
    hint: dict[str, Any],
    *,
    exclude: set[str] | None = None,
) -> str:
    field, score = _pick_schema_field_with_score(header, hint, exclude=exclude)
    return field if score > 0 else ""


def _pick_time_field(role: str, hint: dict[str, Any]) -> str:
    fields = _schema_field_set(hint)
    preferred = {
        "pay": ("finish_time", "create_time", "update_time"),
        "cash": ("update_time", "finish_time", "create_time"),
        "user": ("register_time", "create_time"),
        "bet": ("stat_date", "create_time", "update_time"),
    }.get((role or "").lower(), ("create_time", "update_time", "stat_date"))
    for field in preferred:
        if field.lower() in fields:
            return field
    for field in _schema_fields(hint):
        if field.endswith("_time") or field.endswith("_date"):
            return field
    return ""


def _sql_identifiers(sql: str) -> set[str]:
    out = set()
    for ident in re.findall(r"\b([A-Za-z_][A-Za-z0-9_]*)\b", sql or ""):
        if ident.upper() in _SQL_KEYWORDS:
            continue
        out.add(ident)
    return out


def _sql_as_aliases(sql: str) -> set[str]:
    return {
        m.group(1)
        for m in re.finditer(r"\bAS\s+([A-Za-z_][A-Za-z0-9_]*)", sql or "", re.I)
    }


def _sql_relation_names(sql: str) -> set[str]:
    """Table/view names after FROM/JOIN (plus schema prefix ads)."""
    names = {"ads"}
    for m in re.finditer(
        r"\b(?:FROM|JOIN)\s+(?:([A-Za-z_][A-Za-z0-9_]*)\.)?([A-Za-z_][A-Za-z0-9_]*)",
        sql or "",
        re.I,
    ):
        if m.group(1):
            names.add(m.group(1))
        names.add(m.group(2))
    return names


def sql_unknown_identifiers(sql: str, fields: list[str] | set[str] | None) -> list[str]:
    """Identifiers in SQL not present in describe fields (aliases/relations excluded)."""
    available = {str(f).strip().lower() for f in (fields or []) if str(f).strip()}
    if not available:
        return []
    skip = {s.lower() for s in (_sql_as_aliases(sql) | _sql_relation_names(sql))}
    unknown: list[str] = []
    for ident in sorted(_sql_identifiers(sql), key=str.lower):
        low = ident.lower()
        if low in available or low in skip:
            continue
        unknown.append(ident)
    return unknown


def _split_top_level_and(expr: str) -> list[str]:
    """Split SQL boolean expression on top-level AND (paren-aware)."""
    parts: list[str] = []
    buf: list[str] = []
    depth = 0
    i = 0
    s = expr or ""
    while i < len(s):
        c = s[i]
        if c == "(":
            depth += 1
            buf.append(c)
            i += 1
            continue
        if c == ")":
            depth = max(0, depth - 1)
            buf.append(c)
            i += 1
            continue
        if (
            depth == 0
            and s[i : i + 3].upper() == "AND"
            and (i == 0 or not (s[i - 1].isalnum() or s[i - 1] == "_"))
            and (i + 3 >= len(s) or not (s[i + 3].isalnum() or s[i + 3] == "_"))
        ):
            part = "".join(buf).strip()
            if part:
                parts.append(part)
            buf = []
            i += 3
            continue
        buf.append(c)
        i += 1
    tail = "".join(buf).strip()
    if tail:
        parts.append(tail)
    return parts


def soft_align_filter_sql_to_schema(
    filter_sql: str,
    fields: list[str] | set[str] | None,
) -> tuple[str, list[str]]:
    """Drop AND-predicates that reference describe-unknown identifiers.

    Subquery clauses are kept as-is (outer-view fields must not strip cohort IN).
    Returns (aligned_filter_sql, notes). No invent / no hard gate.
    """
    available = {str(f).strip().lower() for f in (fields or []) if str(f).strip()}
    text = (filter_sql or "").strip()
    notes: list[str] = []
    if not text or not available:
        return text, notes

    clauses = _split_top_level_and(text)
    if len(clauses) == 1:
        stripped = text
        if stripped.startswith("(") and stripped.endswith(")"):
            inner = stripped[1:-1].strip()
            inner_clauses = _split_top_level_and(inner)
            if len(inner_clauses) > 1:
                clauses = inner_clauses

    kept: list[str] = []
    for clause in clauses:
        raw = clause.strip()
        if not raw:
            continue
        # Keep subquery predicates (cohort uid IN (...)); only prune simple filters.
        if re.search(r"\bSELECT\b", raw, re.I):
            kept.append(raw)
            continue
        unknown = sorted(
            i for i in _sql_identifiers(raw) if i.lower() not in available
        )
        if unknown:
            note = f"去掉 filter 未知列谓词 `{raw}`（未知: {', '.join(unknown)}）"
            if note not in notes:
                notes.append(note)
            continue
        kept.append(raw)
    return " AND ".join(kept), notes


def soft_align_sql_to_schema(
    sql: str,
    fields: list[str] | set[str] | None,
) -> tuple[str, list[str], list[str]]:
    """Soft-strip unknown columns using describe field set only (no invent).

    Returns (aligned_sql, notes, unknown_left).
    """
    available = {
        str(f).strip().lower(): str(f).strip()
        for f in (fields or [])
        if str(f).strip()
    }
    notes: list[str] = []
    out = (sql or "").strip()
    if not out or not available:
        return out, notes, sql_unknown_identifiers(out, available.keys())

    bin_re = re.compile(
        r"([A-Za-z_][A-Za-z0-9_]*)\s*([+\-])\s*([A-Za-z_][A-Za-z0-9_]*)"
    )

    def _bin_repl(m: re.Match[str]) -> str:
        a, _op, b = m.group(1), m.group(2), m.group(3)
        a_ok = a.lower() in available
        b_ok = b.lower() in available
        if a_ok and b_ok:
            return m.group(0)
        if a_ok and not b_ok:
            note = f"去掉未知列 `{b}`（保留 `{a}`）"
            if note not in notes:
                notes.append(note)
            return a
        if b_ok and not a_ok:
            note = f"去掉未知列 `{a}`（保留 `{b}`）"
            if note not in notes:
                notes.append(note)
            return b
        return m.group(0)

    for _ in range(12):
        nxt = bin_re.sub(_bin_repl, out)
        if nxt == out:
            break
        out = nxt

    # Conservative: drop bare unknown identifiers from SELECT lists
    # e.g. "SELECT uid, win_lsc, bet_sc" → "SELECT uid, bet_sc"
    select_m = re.match(
        r"(?is)^(\s*SELECT\s+)(.+?)(\s+FROM\s+.+)$",
        out,
    )
    if select_m:
        head, select_list, tail = select_m.group(1), select_m.group(2), select_m.group(3)
        parts = [p.strip() for p in select_list.split(",")]
        kept: list[str] = []
        for part in parts:
            # bare ident or "ident AS alias"
            bare = re.fullmatch(
                r"([A-Za-z_][A-Za-z0-9_]*)(?:\s+AS\s+[A-Za-z_][A-Za-z0-9_]*)?",
                part,
                re.I,
            )
            if bare:
                col = bare.group(1)
                if (
                    col.lower() not in available
                    and col.upper() not in _SQL_KEYWORDS
                    and col.lower() not in {s.lower() for s in _sql_as_aliases(part)}
                ):
                    # alias-only line still needs the expression; bare unknown col drop
                    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", part, re.I):
                        note = f"去掉 SELECT 未知列 `{col}`"
                        if note not in notes:
                            notes.append(note)
                        continue
            kept.append(part)
        if kept and len(kept) != len(parts):
            out = f"{head}{', '.join(kept)}{tail}"

    # Soft-strip unknown predicates from WHERE (top-level AND only).
    where_m = re.search(
        r"(?is)\bWHERE\b(\s+)(.+?)(?=\s+\bGROUP\s+BY\b|\s+\bORDER\s+BY\b|\s+\bHAVING\b|\s+\bLIMIT\b|$)",
        out,
    )
    if where_m:
        where_body = where_m.group(2).strip()
        aligned_where, where_notes = soft_align_filter_sql_to_schema(
            where_body, available.keys()
        )
        for note in where_notes:
            if note not in notes:
                notes.append(note)
        if aligned_where != where_body:
            if aligned_where:
                out = (
                    out[: where_m.start(2)]
                    + aligned_where
                    + out[where_m.end(2) :]
                )
            else:
                # Drop empty WHERE; keep trailing GROUP BY / ORDER BY / LIMIT.
                out = (out[: where_m.start()] + out[where_m.end() :]).strip()
                out = re.sub(r"\s{2,}", " ", out)

    # tidy empty SUM()/SUM( )
    out = re.sub(r"\bSUM\s*\(\s*\)", "SUM(0)", out, flags=re.I)
    out = re.sub(r"[ \t]{2,}", " ", out)
    unknown_left = sql_unknown_identifiers(out, available.keys())
    return out, notes, unknown_left


def soft_align_query_graph_to_schema(
    graph: list[dict] | None,
    schema_fields_by_view: dict[str, list[str]] | None,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Soft-align each query_graph node SQL against describe fields.

    Returns (aligned_nodes, sql_schema_notes). Diagnostic only — never blocks.
    """
    schema_map = (
        schema_fields_by_view if isinstance(schema_fields_by_view, dict) else {}
    )
    notes_all: list[str] = []
    out: list[dict[str, Any]] = []
    for node in graph or []:
        if not isinstance(node, dict):
            continue
        n = dict(node)
        view = str(n.get("view") or "").strip()
        fields = [
            str(f).strip()
            for f in (schema_map.get(view) or [])
            if str(f).strip()
        ]
        sql = str(n.get("sql") or "").strip()
        if sql and fields:
            aligned, notes, _unknown = soft_align_sql_to_schema(sql, fields)
            if notes or aligned != sql:
                n["sql"] = aligned
            if notes:
                prev = [
                    str(x).strip()
                    for x in (n.get("schema_soft_notes") or [])
                    if str(x).strip()
                ]
                for note in notes:
                    if note not in prev:
                        prev.append(note)
                    tagged = f"{view}: {note}"
                    if tagged not in notes_all:
                        notes_all.append(tagged)
                n["schema_soft_notes"] = prev
        out.append(n)
    return out, notes_all


def _filter_is_schema_safe(filter_sql: str, available: set[str]) -> bool:
    if not filter_sql:
        return True
    for ident in _sql_identifiers(filter_sql):
        if ident.lower() not in available:
            return False
    return True


def _scale_from_schema(field: str, comment: str = "") -> int:
    text = f"{field} {comment}".lower()
    if "分" in comment or "cent" in text or "cents" in text:
        return 100
    return 1


def _schema_derived_fragment(header: str, view: str, hint: dict[str, Any]) -> dict[str, Any] | None:
    field = _pick_schema_field(header, hint)
    if not field:
        return None
    role = _role_from_view(view)
    comments = hint.get("comments") if isinstance(hint.get("comments"), dict) else {}
    comment = str(comments.get(field) or "")
    uid_field = "uid" if "uid" in _schema_field_set(hint) else ""
    time_field = _pick_time_field(role, hint)
    is_agg = bool(uid_field and any(k in header for k in ("总", "金额", "次数", "数量")))
    if is_agg and role not in ("user", "channel", "game"):
        scale = _scale_from_schema(field, comment)
        select_expr = f"uid, sum({field})"
        compute = f"sum({field})"
        if scale != 1:
            select_expr += f" / {scale}"
            compute += f"/{scale}"
        select_expr += f" AS schema_{field}_sum"
        return {
            "kind": "aggregate",
            "fetch_mode": FETCH_AGG,
            "sources": [{"role": role or "fact", "view": view, "fields": [uid_field, field]}],
            "join_on": uid_field,
            "filter": {},
            "compute": compute,
            "口径": comment or compute,
            "time_field": time_field,
            "agg_spec": {
                "role": role,
                "view": view,
                "mode": "agg",
                "select": select_expr,
                "group_by": uid_field,
                "filter_sql": "",
                "time_field": time_field,
                "scale": scale,
            },
            "数据来源": f"`{view}`",
            "统计方法": f"describe 备注匹配 `{field}`" + (f"（{comment}）" if comment else ""),
            "schema_source": "describe_ads_view",
        }
    return {
        "kind": "field",
        "fetch_mode": FETCH_FIELD,
        "sources": [{"role": role or "user", "view": view, "fields": [uid_field or field, field]}],
        "join_on": uid_field or field,
        "filter": {},
        "compute": field,
        "口径": comment or field,
        "time_field": time_field,
        "agg_spec": {
            "role": role,
            "view": view,
            "mode": "field",
            "select": ", ".join([x for x in (uid_field, field) if x]),
            "group_by": "",
            "filter_sql": "",
            "time_field": time_field,
        },
        "数据来源": f"`{view}`",
        "统计方法": f"describe 备注匹配 `{field}`" + (f"（{comment}）" if comment else ""),
        "schema_source": "describe_ads_view",
    }


def apply_schema_hints_to_column_plan(
    plan: list[dict] | None,
    *,
    schema_hints: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Adjust seed column_plan with runtime describe schema.

    This is conservative: it only replaces fields missing from the described
    view, drops filters that reference unavailable fields, and upgrades unknown
    columns when field/comment evidence is explicit.
    """
    schema = _schema_hint_map(schema_hints)
    if not schema or not plan:
        return [dict(c) for c in (plan or []) if isinstance(c, dict)]
    out: list[dict[str, Any]] = []
    for col in plan:
        if not isinstance(col, dict):
            continue
        header = str(col.get("header") or "")
        c = dict(col)
        adjustments: list[str] = list(c.get("schema_adjustments") or [])
        if str(c.get("kind") or "") == "unknown":
            best_frag = None
            best_score = 0
            for view, hint in schema.items():
                field, field_score = _pick_schema_field_with_score(header, hint)
                if not field:
                    continue
                score = field_score + _view_score_for_header(header, view, hint)
                if score > best_score:
                    best_score = score
                    best_frag = _schema_derived_fragment(header, view, hint)
            if best_frag:
                derived = plan_one_column(header)
                derived.update(best_frag)
                derived["rule"] = "schema"
                derived["schema_score"] = best_score
                derived["binding_status"] = "bound"
                derived["schema_adjustments"] = [
                    f"schema 多候选评分选择 `{best_frag.get('agg_spec', {}).get('view') or best_frag.get('数据来源')}` score={best_score}"
                ]
                out.append(derived)
                continue

        replacements_by_view: dict[str, dict[str, str]] = {}
        new_sources: list[dict] = []
        for source in c.get("sources") or []:
            if not isinstance(source, dict):
                continue
            s = dict(source)
            view = str(s.get("view") or "").strip()
            hint = _view_schema(schema, view)
            available = _schema_field_set(hint)
            if hint and s.get("fields"):
                new_fields: list[str] = []
                for field in s.get("fields") or []:
                    f = str(field).strip()
                    if not f:
                        continue
                    if f.lower() in available:
                        new_fields.append(f)
                        continue
                    repl = _pick_schema_field(header, hint, exclude=set(new_fields))
                    if repl:
                        replacements_by_view.setdefault(view, {})[f] = repl
                        new_fields.append(repl)
                        adjustments.append(f"字段 `{f}` -> `{repl}`（describe）")
                if new_fields:
                    s["fields"] = list(dict.fromkeys(new_fields))
            new_sources.append(s)
        if new_sources:
            c["sources"] = new_sources

        spec = dict(c.get("agg_spec") or {})
        view = str(spec.get("view") or "").strip()
        hint = _view_schema(schema, view)
        available = _schema_field_set(hint)
        if hint and available:
            for old, new in replacements_by_view.get(view, {}).items():
                for key in ("select", "group_by"):
                    if spec.get(key):
                        spec[key] = re.sub(
                            rf"\b{re.escape(old)}\b",
                            new,
                            str(spec.get(key) or ""),
                        )
            time_field = str(spec.get("time_field") or c.get("time_field") or "").strip()
            if time_field and time_field.lower() not in available:
                picked = _pick_time_field(str(spec.get("role") or ""), hint)
                if picked:
                    spec["time_field"] = picked
                    c["time_field"] = picked
                    adjustments.append(f"时间字段 `{time_field}` -> `{picked}`（describe）")
            filt = str(spec.get("filter_sql") or "").strip()
            if filt:
                new_filt, filt_notes = soft_align_filter_sql_to_schema(filt, available)
                if filt_notes or new_filt != filt:
                    for note in filt_notes:
                        adjustments.append(note)
                    if not filt_notes and new_filt != filt:
                        adjustments.append("filter_sql 已按 describe 软对齐")
                    spec["filter_sql"] = new_filt
            sel = str(spec.get("select") or "").strip()
            if sel and "{time_pred}" not in sel and view:
                probe = f"SELECT {sel} FROM ads.{view}"
                aligned_probe, sel_notes, _unk = soft_align_sql_to_schema(probe, available)
                if sel_notes:
                    m = re.match(
                        r"(?is)^\s*SELECT\s+(.+?)\s+FROM\s+",
                        aligned_probe,
                    )
                    if m:
                        spec["select"] = m.group(1).strip()
                    for note in sel_notes:
                        adjustments.append(note)
            c["agg_spec"] = spec
        if adjustments:
            c["schema_adjustments"] = list(dict.fromkeys(adjustments))
            c["schema_source"] = "describe_ads_view"
        if hint and available and _column_plan_views(c):
            c["binding_status"] = "bound"
            if not str(c.get("数据来源") or "").strip() or str(c.get("数据来源") or "").strip() == "—":
                c["数据来源"] = f"`{view}`"
        out.append(c)
    return out


def roles_from_column_plan(plan: list[dict] | None) -> list[str]:
    """Deduped semantic roles from column plan sources, in stable order."""
    seen: set[str] = set()
    result: list[str] = []

    def _add(role: str, view: str = "") -> None:
        r = str(role or "").strip().lower() or _role_from_view(view)
        if r in ("user", "pay", "cash", "bet", "channel", "game") and r not in seen:
            seen.add(r)
            result.append(r)

    for col in plan or []:
        if not isinstance(col, dict):
            continue
        for s in col.get("sources") or []:
            if not isinstance(s, dict):
                continue
            _add(str(s.get("role") or ""), str(s.get("view") or ""))
        agg = col.get("agg_spec") if isinstance(col.get("agg_spec"), dict) else {}
        _add(str(agg.get("role") or ""), str(agg.get("view") or ""))
        for role in agg.get("roles") if isinstance(agg.get("roles"), list) else []:
            _add(str(role or ""))
        if agg.get("needs_game_dim"):
            _add("game")
    return result


def views_from_column_plan(plan: list[dict] | None) -> list[str]:
    """Union of sources.view and agg_spec.view (contract), de-duped in order."""
    out: list[str] = []
    seen: set[str] = set()

    def _add(v: str) -> None:
        name = str(v or "").strip()
        if name and name not in seen:
            seen.add(name)
            out.append(name)

    for col in plan or []:
        if not isinstance(col, dict):
            continue
        agg = col.get("agg_spec") if isinstance(col.get("agg_spec"), dict) else {}
        _add(str(agg.get("view") or ""))
        for s in col.get("sources") or []:
            if not isinstance(s, dict):
                continue
            _add(str(s.get("view") or ""))
    return out


def unknown_columns(plan: list[dict] | None) -> list[str]:
    return [
        str(c.get("header") or "")
        for c in (plan or [])
        if isinstance(c, dict) and str(c.get("kind") or "") == "unknown"
        and str(c.get("header") or "").strip()
    ]


def format_column_plan_summary(plan: list[dict] | None) -> str:
    """Human-readable step content for 执行过程."""
    lines = ["【列规划】按输出列推导 MCP 数据源（未点名 role 不拉）："]
    if not plan:
        lines.append("（无输出列）")
        return "\n".join(lines)
    for i, col in enumerate(plan, 1):
        if not isinstance(col, dict):
            continue
        header = str(col.get("header") or "")
        kind = str(col.get("kind") or "")
        mode = str(col.get("fetch_mode") or "")
        roles = [
            str(s.get("role") or "")
            for s in (col.get("sources") or [])
            if isinstance(s, dict) and s.get("role")
        ]
        role_s = "+".join(roles) if roles else "—"
        compute = str(col.get("compute") or "")
        bit = f"{i}. {header} → [{kind}/{mode}] {role_s}"
        if compute:
            bit += f" ({compute})"
        lines.append(bit)
    roles_all = roles_from_column_plan(plan)
    if roles_all:
        lines.append("本轮目标 role：" + "、".join(roles_all))
    unk = unknown_columns(plan)
    if unk:
        lines.append("未识别列（不盲目全量拉取）：" + "、".join(unk))
    return "\n".join(lines)


def format_column_plan_card(plan: list[dict] | None) -> str:
    """Plan card isomorphic to FINAL field docs（列名 | 数据来源 | 统计方法）."""
    lines = [
        "【列计划卡】确认口径后按查询图 MCP（禁止无落盘编造数字）：",
        "| # | 列名 | 数据来源 | 统计方法 |",
        "| --- | --- | --- | --- |",
    ]
    if not plan:
        lines.append("| — | （无输出列） | — | — |")
        return "\n".join(lines)
    for i, col in enumerate(plan, 1):
        if not isinstance(col, dict):
            continue
        header = str(col.get("header") or "").replace("|", "/")
        src, method = column_source_method(col)
        src = src.replace("|", "/")
        method = method.replace("|", "/")
        lines.append(f"| {i} | {header} | {src} | {method} |")
    roles_all = roles_from_column_plan(plan)
    if roles_all:
        lines.append("")
        lines.append("查询图 roles：" + "、".join(roles_all))
    unk = unknown_columns(plan)
    if unk:
        lines.append("未识别（诚实留空）：" + "、".join(unk))
    return "\n".join(lines)


def _time_predicate(
    role: str,
    time_window: dict | None,
    *,
    time_field: str = "",
) -> str:
    tw = time_window if isinstance(time_window, dict) else {}
    start, end = tw.get("start_ms"), tw.get("end_ms")
    if start is None or end is None:
        return "1=1"
    field = (time_field or "").strip()
    if not field:
        r = (role or "").strip().lower()
        if r == "user":
            field = "register_time"
        else:
            field = "create_time"
    # Date columns (e.g. everyday betstat.stat_date) cannot compare to ms UInt64
    if field == "stat_date" or field.endswith("_date"):
        return (
            f"{field} >= toDate(fromUnixTimestamp64Milli({start})) "
            f"AND {field} < toDate(fromUnixTimestamp64Milli({end}))"
        )
    return f"{field} >= {start} AND {field} < {end}"


def _pay_cohort_uid_sql(
    time_window: dict | None,
    *,
    view: str,
    time_field: str = "",
    filter_sql: str = "",
) -> str:
    """Build a cohort anchor from the already-bound payment resource."""
    tw = time_window if isinstance(time_window, dict) else {}
    resource = str(view or "").strip()
    if str(tw.get("cohort") or "").strip() != "pay" or not resource:
        return ""
    where = _time_predicate("pay", tw, time_field=time_field)
    filt = str(filter_sql or "").strip()
    if filt:
        where += f" AND ({filt})"
    return f"SELECT uid, 1 AS cohort_uid FROM ads.{resource} WHERE {where} GROUP BY uid"


def node_cost_for_mode(mode: str) -> str:
    """light = one-page agg/dim; heavy = top_n/sequence (split segment)."""
    m = (mode or "").strip().lower()
    if m in (FETCH_TOP_N, FETCH_SEQUENCE, "top_n", "sequence"):
        return "heavy"
    return "light"


def node_segment_for(mode: str, role: str, view: str) -> str:
    """Generic segment label for merge (not hardcoded recharge pipeline)."""
    m = (mode or "").strip().lower()
    v = (view or "").lower()
    r = (role or "").strip().lower()
    if m in (FETCH_SEQUENCE, "sequence"):
        return "sequence"
    if m in (FETCH_TOP_N, "top_n"):
        return "top_n"
    if "betstat_everyday" in v or "everyday_bygame" in v:
        return "bet_daily"
    if m in (FETCH_LOOKUP, "lookup") or r in ("channel", "game"):
        return "dim"
    if m in (FETCH_FIELD, "field") or r == "user":
        return "identity"
    if r in ("pay", "cash"):
        return "fact_agg"
    if r == "bet":
        return "bet_agg"
    return "fact_agg"


def sql_from_agg_spec(
    agg_spec: dict | None,
    time_window: dict | None,
    *,
    limit: int = 10000,
) -> tuple[str, str]:
    """Return (view, sql) for an agg_spec node. Empty sql if not buildable."""
    spec = agg_spec if isinstance(agg_spec, dict) else {}
    mode = str(spec.get("mode") or "").strip()
    if mode in ("derived", "lookup", ""):
        return "", ""
    view = str(spec.get("view") or "").strip()
    role = str(spec.get("role") or "").strip()
    if not view:
        return "", ""
    time_field = str(spec.get("time_field") or "").strip()
    if mode == "field":
        cols = str(spec.get("select") or "uid").strip()
        # Pay cohort: never emit unfiltered full-table user_info SQL (engine pulls by uid IN).
        if role == "user" and isinstance(time_window, dict) and time_window.get("cohort") == "pay":
            return view, ""
        filter_sql = str(spec.get("filter_sql") or "").strip()
        pred = _time_predicate(role or "user", time_window, time_field=time_field)
        sql = f"SELECT {cols} FROM ads.{view} WHERE {pred}"
        if filter_sql:
            sql += f" AND ({filter_sql})"
        return view, sql

    select = str(spec.get("select") or "uid").strip()
    group_by = str(spec.get("group_by") or "").strip()
    filter_sql = str(spec.get("filter_sql") or "").strip()
    pred = _time_predicate(role, time_window, time_field=time_field)
    if "{time_pred}" in select:
        sql = select.replace("{time_pred}", pred)
        if not sql.upper().lstrip().startswith("SELECT"):
            sql = f"SELECT {sql}"
        return view, sql

    where_bits = [pred]
    if filter_sql:
        where_bits.append(f"({filter_sql})")
    where = " AND ".join(where_bits)
    sql = f"SELECT {select} FROM ads.{view} WHERE {where}"
    if group_by:
        sql += f" GROUP BY {group_by}"
    return view, sql


_TIME_FIELD_CANDIDATES = (
    "stat_date",
    "create_time",
    "finish_time",
    "update_time",
    "register_time",
    "event_time",
)


def resolve_time_field_from_evidence(
    *,
    plan_time_field: str = "",
    schema_fields: list[str] | None = None,
    fallback: str = "create_time",
) -> str:
    """Pick a time column from plan + describe evidence (no hard view→column ban)."""
    fields = [str(f).strip() for f in (schema_fields or []) if str(f).strip()]
    by_lower = {f.lower(): f for f in fields}
    pt = (plan_time_field or "").strip()
    if pt and (not fields or pt.lower() in by_lower):
        return by_lower.get(pt.lower(), pt) if fields else pt
    if fields:
        for cand in _TIME_FIELD_CANDIDATES:
            if cand in by_lower:
                return by_lower[cand]
    return (pt or fallback).strip() or fallback


def build_query_graph(
    plan: list[dict] | None,
    time_window: dict | None = None,
    *,
    dim_views: dict[str, str] | None = None,
    page_limit: int = 10000,
    schema_fields_by_view: dict[str, list[str]] | None = None,
    bound_only: bool = False,
) -> list[dict[str, Any]]:
    """Dedup MCP query nodes from column_plan (agg/field/flag/top_n/sequence).

    Derived columns produce no node. Lookup channel/game dims get separate nodes.
    schema_fields_by_view: optional describe evidence (view → field names).
    """
    nodes: list[dict[str, Any]] = []
    seen_keys: set[str] = set()
    dims = dim_views if isinstance(dim_views, dict) else {}
    schema_map = schema_fields_by_view if isinstance(schema_fields_by_view, dict) else {}

    def _add(node: dict) -> None:
        key = str(node.get("key") or "")
        if not key or key in seen_keys:
            return
        seen_keys.add(key)
        nodes.append(node)

    need_channel = False
    need_game = False
    need_bet_sequence = False
    field_selects_by_view: dict[str, set[str]] = {}
    lookup_views: dict[str, str] = {}
    pay_cohort = (
        isinstance(time_window, dict)
        and str(time_window.get("cohort") or "").strip() == "pay"
    )

    for col in plan or []:
        if not isinstance(col, dict):
            continue
        if bound_only and not column_plan_entry_is_bound(col):
            # A registry view is a semantic hint, not execution authority.
            # Query nodes appear only after live MCP discovery + LLM binding.
            continue
        mode = str(col.get("fetch_mode") or "")
        header = str(col.get("header") or "")
        spec = dict(col.get("agg_spec") or {})
        if mode == FETCH_DERIVED:
            continue
        if mode == FETCH_LOOKUP:
            lookup_roles = spec.get("roles") if isinstance(spec.get("roles"), list) else []
            need_channel = need_channel or any(
                str(s.get("role")) == "channel" for s in (col.get("sources") or [])
                if isinstance(s, dict)
            ) or "channel" in [str(r) for r in lookup_roles]
            for source in col.get("sources") or []:
                if not isinstance(source, dict):
                    continue
                source_view = str(source.get("view") or "").strip()
                source_role = str(source.get("role") or "").strip().lower()
                if source_view and source_role == "user":
                    field_selects_by_view.setdefault(source_view, set()).update(
                        str(field).strip()
                        for field in (source.get("fields") or ["uid", "channel_id"])
                        if str(field).strip()
                    )
                if source_view and source_role in ("channel", "game"):
                    lookup_views[source_role] = source_view
            continue
        if mode == FETCH_FIELD:
            added = False
            for s in col.get("sources") or []:
                if not isinstance(s, dict):
                    continue
                if str(s.get("role") or "").strip().lower() != "user":
                    continue
                view = str(s.get("view") or "").strip()
                if not view:
                    continue
                bucket = field_selects_by_view.setdefault(view, set())
                bucket.add("uid")
                bucket.update(
                    str(f).strip()
                    for f in (s.get("fields") or [])
                    if str(f).strip()
                )
                added = True
            if not added and not bound_only:
                view = str(spec.get("view") or "").strip()
                if not view:
                    continue
                bucket = field_selects_by_view.setdefault(view, set())
                bucket.add("uid")
                for part in str(spec.get("select") or "uid").split(","):
                    p = part.strip()
                    if p and re.match(r"^[a-zA-Z_][a-zA-Z0-9_]*$", p):
                        bucket.add(p)
            continue
        if mode in (FETCH_AGG, FETCH_FLAG, FETCH_TOP_N, FETCH_SEQUENCE):
            if mode == FETCH_TOP_N and spec.get("needs_game_dim"):
                need_game = True
            if mode == FETCH_SEQUENCE and str(spec.get("role") or "") == "pay":
                need_bet_sequence = True
            view_hint = str(
                spec.get("view") or ""
            ).strip()
            schema_fields = [
                str(f).strip()
                for f in (schema_map.get(view_hint) or [])
                if str(f).strip()
            ]
            soft_notes: list[str] = []
            if schema_fields:
                spec = dict(spec)
                filt = str(spec.get("filter_sql") or "").strip()
                if filt:
                    new_filt, filt_notes = soft_align_filter_sql_to_schema(
                        filt, schema_fields
                    )
                    soft_notes.extend(filt_notes)
                    spec["filter_sql"] = new_filt
                sel = str(spec.get("select") or "").strip()
                if sel and "{time_pred}" not in sel:
                    probe = f"SELECT {sel} FROM ads.{view_hint}"
                    aligned_probe, sel_notes, _unk = soft_align_sql_to_schema(
                        probe, schema_fields
                    )
                    soft_notes.extend(sel_notes)
                    m = re.match(
                        r"(?is)^\s*SELECT\s+(.+?)\s+FROM\s+",
                        aligned_probe,
                    )
                    if m and sel_notes:
                        spec["select"] = m.group(1).strip()
            view, sql = sql_from_agg_spec(spec, time_window, limit=page_limit)
            role = str(spec.get("role") or "")
            if not view:
                # Gap for LLM bind — do not invent archive role→view (esp. user_bet_log)
                continue
            if schema_fields and sql:
                sql, align_notes, _unk = soft_align_sql_to_schema(sql, schema_fields)
                for note in align_notes:
                    if note not in soft_notes:
                        soft_notes.append(note)
            key = f"{mode}:{view}:{sql[:120]}"
            existing = next((n for n in nodes if n.get("key") == key), None)
            if existing:
                existing.setdefault("headers", []).append(header)
                if soft_notes:
                    prev = list(existing.get("schema_soft_notes") or [])
                    for note in soft_notes:
                        if note not in prev:
                            prev.append(note)
                    existing["schema_soft_notes"] = prev
                continue
            node = {
                "key": key,
                "role": role,
                "view": view,
                "mode": mode,
                "headers": [header],
                "sql": sql,
                "limit": page_limit if mode != FETCH_SEQUENCE else min(page_limit, 5000),
                "incomplete_ok": mode == FETCH_SEQUENCE,
                "cost": node_cost_for_mode(mode),
                "segment": node_segment_for(mode, role, view),
            }
            if soft_notes:
                node["schema_soft_notes"] = soft_notes
            _add(node)

    if field_selects_by_view:
        pay_specs = [
            dict(c.get("agg_spec") or {})
            for c in plan or []
            if isinstance(c, dict)
            and (not bound_only or column_plan_entry_is_bound(c))
            and str((c.get("agg_spec") or {}).get("role") or "").strip().lower() == "pay"
            and str((c.get("agg_spec") or {}).get("view") or "").strip()
        ]
        if pay_cohort and pay_specs:
            pay_spec = pay_specs[0]
            pay_view = str(pay_spec.get("view") or "").strip()
            cohort_sql = _pay_cohort_uid_sql(
                time_window,
                view=pay_view,
                time_field=str(pay_spec.get("time_field") or ""),
                filter_sql=str(pay_spec.get("filter_sql") or ""),
            )
            _add({
                "key": f"cohort:pay_uid:{pay_view}",
                "role": "pay",
                "view": pay_view,
                "mode": FETCH_AGG,
                "headers": ["付费用户UID"],
                "sql": cohort_sql,
                "limit": page_limit,
                "cost": "light",
                "segment": "cohort",
            })
        for view_name, field_set in field_selects_by_view.items():
            cols = sorted(field_set) or ["uid", "register_time", "channel_id", "sc"]
            if "uid" not in cols:
                cols = ["uid"] + cols
            select = ", ".join(cols)
            spec = {
                "view": view_name,
                "role": "user",
                "mode": "field",
                "select": select,
            }
            view, sql = sql_from_agg_spec(spec, time_window, limit=page_limit)
            if not view:
                continue
            if pay_cohort:
                sql = ""
            user_limit = min(int(page_limit or 10000), 2000)
            _add({
                "key": f"field:{view}:{select}",
                "role": "user",
                "view": view,
                "mode": FETCH_FIELD,
                "headers": ["用户字段"],
                "sql": sql,
                "limit": user_limit,
                "cost": "light",
                "segment": "identity",
                "deferred": bool(pay_cohort),
                "defer_until": "pay" if pay_cohort else "",
                "select_cols": select,
            })

    if need_channel and (v := lookup_views.get("channel") or dims.get("channel")):
        _add({
            "key": f"dim:channel:{v}",
            "role": "channel",
            "view": v,
            "mode": FETCH_LOOKUP,
            "headers": ["注册渠道"],
            "sql": "",
            "limit": 2000,
            "cost": "light",
            "segment": "dim",
        })
    if need_game and (v := lookup_views.get("game") or dims.get("game")):
        _add({
            "key": f"dim:game:{v}",
            "role": "game",
            "view": v,
            "mode": FETCH_LOOKUP,
            "headers": ["游戏维表"],
            "sql": "",
            "limit": 2000,
            "cost": "light",
            "segment": "dim",
        })

    if need_bet_sequence:
        # Only emit when a bet view is already declared on the plan — never invent user_bet_log
        planned_bet_view = ""
        planned_time_field = ""
        for col in plan or []:
            if not isinstance(col, dict):
                continue
            if bound_only and not column_plan_entry_is_bound(col):
                continue
            agg = col.get("agg_spec") if isinstance(col.get("agg_spec"), dict) else {}
            if str(agg.get("role") or "") == "bet" and str(agg.get("view") or "").strip():
                planned_bet_view = str(agg.get("view") or "").strip()
                planned_time_field = str(
                    agg.get("time_field") or col.get("time_field") or ""
                ).strip()
                break
            for s in col.get("sources") or []:
                if (
                    isinstance(s, dict)
                    and str(s.get("role") or "") == "bet"
                    and str(s.get("view") or "").strip()
                ):
                    planned_bet_view = str(s.get("view") or "").strip()
                    planned_time_field = str(
                        col.get("time_field") or s.get("time_field") or ""
                    ).strip()
                    break
            if planned_bet_view:
                break
        if planned_bet_view:
            schema_fields = [
                str(f).strip()
                for f in (schema_map.get(planned_bet_view) or [])
                if str(f).strip()
            ]
            time_field = resolve_time_field_from_evidence(
                plan_time_field=planned_time_field,
                schema_fields=schema_fields,
                fallback="create_time",
            )
            # type=1 only when describe evidence has `type`, or view looks like bet_log
            filter_sql = ""
            if schema_fields:
                if any(f.lower() == "type" for f in schema_fields):
                    filter_sql = "type = 1"
            elif "bet_log" in planned_bet_view.lower():
                filter_sql = "type = 1"
            spec = {
                "view": planned_bet_view,
                "mode": FETCH_SEQUENCE,
                "select": f"uid, {time_field}",
                "filter_sql": filter_sql,
                "time_field": time_field,
            }
            view, sql = sql_from_agg_spec(spec, time_window, limit=page_limit)
            if view and sql:
                _add({
                    "key": f"sequence:bet_events:{view}:{sql[:100]}",
                    "role": "bet",
                    "view": view,
                    "mode": FETCH_SEQUENCE,
                    "headers": ["连续充值次数"],
                    "sql": sql,
                    "limit": min(int(page_limit or 10000), 5000),
                    "incomplete_ok": True,
                    "cost": node_cost_for_mode(FETCH_SEQUENCE),
                    "segment": node_segment_for(FETCH_SEQUENCE, "bet", view),
                })

    # Light nodes first, heavy (top_n/sequence) last. Pay-cohort exports need
    # pay facts early so deferred user uid-batches have an anchor; pull user
    # before secondary facts so row volume is not starved by cash/bet pagination.
    pay_cohort_sort = pay_cohort
    role_order = (
        {"user": 2, "pay": 3, "cash": 4, "bet": 5, "channel": 6, "game": 7}
        if pay_cohort_sort
        else {"user": 0, "pay": 1, "cash": 2, "bet": 3, "channel": 4, "game": 5}
    )

    def _sort_role_rank(node: dict[str, Any]) -> int:
        role = str(node.get("role") or "").strip().lower()
        mode = str(node.get("mode") or "").strip().lower()
        headers = " ".join(str(h) for h in (node.get("headers") or [])).lower()
        if pay_cohort_sort and (
            str(node.get("segment") or "") == "cohort"
            or str(node.get("key") or "").startswith("cohort:pay_uid:")
        ):
            return 0
        if pay_cohort_sort and mode == FETCH_SEQUENCE and "连续充值" in headers:
            if role == "bet":
                return 8
            if role == "pay":
                return 9
        if pay_cohort_sort and role == "pay":
            sql = str(node.get("sql") or "").lower()
            if "pay_sum" in sql or "sum(price" in sql or "总充值" in headers:
                return 1
        return role_order.get(role, 50)

    nodes.sort(key=lambda n: (
        0 if n.get("cost") == "light" else 1,
        _sort_role_rank(n),
        str(n.get("segment") or ""),
    ))
    # Final soft pass: sequence/user/cohort nodes + any residual unknown preds.
    if schema_map:
        nodes, _graph_notes = soft_align_query_graph_to_schema(nodes, schema_map)
    return nodes


def sql_with_probe_limit(sql: str, *, limit: int = 1) -> str:
    """Append/replace trailing LIMIT for a soft remote SQL probe."""
    text = (sql or "").strip()
    if not text:
        return text
    lim = max(1, int(limit or 1))
    if re.search(r"(?is)\bLIMIT\s+\d+\s*$", text):
        return re.sub(r"(?is)\bLIMIT\s+\d+\s*$", f"LIMIT {lim}", text)
    return f"{text} LIMIT {lim}"


def should_soft_probe_query_node(node: dict | None) -> bool:
    """True for bet/fact query-graph nodes that should LIMIT-1 probe before full fetch."""
    n = node if isinstance(node, dict) else {}
    if bool(n.get("deferred")):
        return False
    sql = str(n.get("sql") or "").strip()
    if not sql:
        return False
    mode = str(n.get("mode") or "").strip().lower()
    role = str(n.get("role") or "").strip().lower()
    if mode in ("lookup", "dim"):
        return False
    if mode in ("agg", "flag", "top_n", "sequence", "field"):
        return True
    return role in ("bet", "pay", "cash", "user")


def mcp_line_from_query_node(node: dict | None) -> str:
    """Build MCP: query_ads_view … for one query-graph node."""
    import json

    n = node if isinstance(node, dict) else {}
    if bool(n.get("deferred")):
        return ""
    view = str(n.get("view") or "").strip()
    if not view:
        return ""
    lim = int(n.get("limit") or 10000)
    sql = str(n.get("sql") or "").strip()
    # Never emit bare user_info limit-only (full table) when sql is empty
    if not sql and str(n.get("role") or "").strip().lower() == "user":
        return ""
    args: dict[str, Any] = {"view": view, "limit": lim}
    if sql:
        args["sql"] = sql
    return "MCP: query_ads_view " + json.dumps(args, ensure_ascii=False)


def query_graph_complete(
    nodes: list[dict] | None,
    fetched_view_pages: dict[str, int] | None,
) -> bool:
    """True when every non-derived node has ≥1 landed page for its view."""
    pages = fetched_view_pages or {}
    for n in nodes or []:
        if not isinstance(n, dict):
            continue
        view = str(n.get("view") or "")
        if not view:
            continue
        if int(pages.get(view, 0) or 0) < 1:
            return False
    return bool(nodes)


def _node_view_handled(
    view: str,
    fetched_view_pages: dict[str, int] | None,
    failed_views: set[str] | frozenset | None,
) -> bool:
    """Landed (≥1 page) or permanently failed for this run."""
    v = (view or "").strip()
    if not v:
        return True
    if int((fetched_view_pages or {}).get(v, 0) or 0) >= 1:
        return True
    return bool(failed_views) and v in failed_views


def query_graph_done_enough(
    nodes: list[dict] | None,
    fetched_view_pages: dict[str, int] | None,
    failed_views: set[str] | frozenset | None = None,
    done_node_keys: set[str] | frozenset | None = None,
) -> bool:
    """True when every node is handled (landed or failed) and core user/pay is writable.

    Heavy / bet_daily / sequence failures do not block: they count as handled.
    Core = segment in (identity, fact_agg) with role in (user, pay) must have
    at least one successful landed page each. If the graph has no such core
    nodes, any landed page is enough.
    """
    graph = [n for n in (nodes or []) if isinstance(n, dict) and str(n.get("view") or "").strip()]
    if not graph:
        return False
    pages = fetched_view_pages or {}
    failed = failed_views or set()
    done_keys = done_node_keys or set()

    def _node_handled(n: dict) -> bool:
        key = str(n.get("key") or "").strip()
        if key and key in done_keys:
            return True
        if key and f"node:{key}" in failed:
            return True
        mode = str(n.get("mode") or "").strip().lower()
        role = str(n.get("role") or "").strip().lower()
        if mode in (FETCH_AGG, FETCH_FLAG, FETCH_TOP_N, FETCH_SEQUENCE, "agg", "flag", "top_n", "sequence"):
            if role not in ("user", "pay"):
                return _node_view_handled(str(n.get("view") or ""), pages, failed)
            return False
        return _node_view_handled(str(n.get("view") or ""), pages, failed)

    if not all(_node_handled(n) for n in graph):
        return False
    core = [
        n
        for n in graph
        if str(n.get("segment") or "") in ("identity", "fact_agg")
        and str(n.get("role") or "").strip().lower() in ("user", "pay")
    ]
    if not core:
        return any(int(pages.get(str(n.get("view") or ""), 0) or 0) >= 1 for n in graph)
    for role in {str(n.get("role") or "").strip().lower() for n in core}:
        role_views = [
            str(n.get("view") or "")
            for n in core
            if str(n.get("role") or "").strip().lower() == role
        ]
        if not any(int(pages.get(v, 0) or 0) >= 1 for v in role_views):
            return False
    return True


def needs_export_clarify(
    *,
    time_window: dict | None,
    column_headers: list[str] | None,
    user_message: str = "",
) -> str:
    """Return one short clarification question, or empty if ready to run."""
    tw = time_window if isinstance(time_window, dict) else {}
    cols = [str(c).strip() for c in (column_headers or []) if str(c).strip()]
    text = (user_message or "").strip()
    if not tw.get("start_ms") or not tw.get("end_ms"):
        if not re.search(r"20\d{2}|至|到|至今|[-/年]", text):
            return (
                "【对齐需求】请补充导出时间范围"
                "（例如：美国东部时间 2026-07-01 至 2026-08-05），以及人群"
                "（充值用户 / 新注册用户）。确认后我将按列计划卡拉数写表。"
            )
    if len(cols) < 2 and not re.search(r"导出|报表|xlsx|列", text, re.I):
        return (
            "【对齐需求】请说明要导出的列（或贴列清单），"
            "以及时间窗与人群；我会先出列计划卡再查询。"
        )
    return ""


# Fabricated overview numbers without task landing — strip / block
_FABRICATED_STAT_RE = re.compile(
    r"(?:"
    r"总用户数\s*[|：:]*\s*\d{2,}"
    r"|有\s*SC\s*下注[^\d]{0,12}\d{2,}"
    r"|被封禁用户\s*[|：:]*\s*\d{2,}"
    r"|有退款[^\d]{0,12}\d{2,}"
    r"|约?\s*\d{3,5}\s*名?(?:用户|人)"
    r"|\|\s*\d{3,5}\s*\|"  # markdown table cells with large ints in overview
    r")",
    re.I,
)


def text_has_export_stat_claims(text: str) -> bool:
    """True if text looks like an export overview with concrete headcounts."""
    t = text or ""
    if not t.strip():
        return False
    if re.search(r"(总用户|封禁用户|有退款|有\s*SC\s*下注|银行卡).{0,20}\d{2,}", t):
        return True
    if re.search(r"\d{3,5}\s*(?:名)?(?:充值)?用户", t):
        return True
    return bool(_FABRICATED_STAT_RE.search(t))


def sanitize_final_without_landing(
    text: str,
    *,
    has_task_data: bool,
    plan_card: str = "",
) -> str:
    """Hard ban: no concrete overview numbers when nothing landed in task/."""
    if has_task_data:
        return text or ""
    body = (text or "").strip()
    if not text_has_export_stat_claims(body) and "2650" not in body and "295" not in body:
        # Still strip obvious fake counts if present
        if not re.search(r"\d{3,}", body):
            return body
    lines = [
        "### 未能交付可信概况",
        "",
        "本轮**未落盘可核验的 MCP 数据页**（`task/page_*.json`），"
        "因此**禁止输出**总用户数/封禁数/退款数等具体统计数字。",
        "",
        "请回复「按计划重跑」或补充时间窗后重新导出；我会按列计划卡真实查询。",
    ]
    if plan_card:
        lines += ["", plan_card]
    return "\n".join(lines)


def apply_describe_field_hints(
    plan: list[dict] | None,
    *,
    view_fields: dict[str, list[str]] | None = None,
) -> list[dict]:
    """Optionally drop fields not present in describe cache (soft correction)."""
    cache = view_fields or {}
    if not cache or not plan:
        return list(plan or [])
    out: list[dict] = []
    for col in plan:
        if not isinstance(col, dict):
            continue
        c = dict(col)
        new_sources: list[dict] = []
        for s in c.get("sources") or []:
            if not isinstance(s, dict):
                continue
            s2 = dict(s)
            view = str(s2.get("view") or "")
            allowed = {f.lower() for f in (cache.get(view) or []) if f}
            if allowed and s2.get("fields"):
                kept = [f for f in s2["fields"] if str(f).lower() in allowed]
                if kept:
                    s2["fields"] = kept
            new_sources.append(s2)
        c["sources"] = new_sources
        out.append(c)
    return out


async def polish_unknown_columns_with_llm(
    llm,
    db,
    plan: list[dict],
    *,
    timeout: int = 12,
) -> list[dict]:
    """Best-effort LLM fill for kind=unknown columns; never expands to full hexad."""
    unk_idx = [
        i
        for i, c in enumerate(plan or [])
        if isinstance(c, dict) and c.get("kind") == "unknown"
    ]
    if not unk_idx or not llm:
        return list(plan or [])
    try:
        from app.services.llm_client import chat_completion
    except Exception:
        return list(plan or [])
    headers = [str(plan[i].get("header") or "") for i in unk_idx]
    prompt = (
        "你是 ADS 导出列规划器。对下列未识别的中文表头，各给出一个 JSON 对象，"
        "字段：header,kind,role(user|pay|cash|bet|channel|game 之一或多个逗号分隔),"
        "compute(短说明)。只输出 JSON 数组，不要前言。\n"
        f"表头：{headers}\n"
        "若无法判断，kind 填 unknown、role 留空。"
    )
    try:
        raw = await chat_completion(
            llm,
            [{"role": "user", "content": prompt}],
            max_tokens=600,
            db=db,
            timeout=timeout,
        )
    except Exception:
        return list(plan or [])
    text = (raw or "").strip()
    m = re.search(r"\[[\s\S]*\]", text)
    if not m:
        return list(plan or [])
    import json

    try:
        arr = json.loads(m.group(0))
    except Exception:
        return list(plan or [])
    if not isinstance(arr, list):
        return list(plan or [])
    by_header = {
        str(x.get("header") or "").strip(): x
        for x in arr
        if isinstance(x, dict) and str(x.get("header") or "").strip()
    }
    out = [dict(c) if isinstance(c, dict) else c for c in plan]
    for i in unk_idx:
        h = str(out[i].get("header") or "")
        tip = by_header.get(h)
        if not tip:
            continue
        roles_raw = str(tip.get("role") or "")
        roles = [
            r.strip()
            for r in re.split(r"[,/+|，、\s]+", roles_raw)
            if r.strip()
        ]
        if not roles:
            continue
        kind = str(tip.get("kind") or "field")
        out[i] = {
            "header": h,
            "kind": kind,
            "fetch_mode": _default_fetch_mode(kind),
            "sources": _enrich_sources(
                [{"role": r, "fields": ["uid"]} for r in roles]
            ),
            "join_on": "uid",
            "filter": {},
            "compute": str(tip.get("compute") or "")[:80],
            "口径": str(tip.get("compute") or "")[:80],
            "time_field": "",
            "agg_spec": {},
            "rule": "llm",
        }
    return out
