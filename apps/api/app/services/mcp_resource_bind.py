"""Bind MCP-agnostic MetricIntent to live MCP resources (list → describe → LLM).

Hardcoded ADS views in metric_registry are soft seed only when catalog/bind fails.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from app.services.intent_router import MetricIntentItem, TurnIntent, _extract_json_object

logger = logging.getLogger(__name__)
_VIEW_NAME_RE = re.compile(r"\b(view_result_[a-zA-Z0-9_]+)\b", re.I)

_BIND_SYSTEM = """你是 MCP 资源绑定器。只输出一个 JSON 对象，不要 Markdown。

输入：用户指标意图（与具体库无关）+ 当前 MCP 工具目录 + 可选资源摘要/字段。
任务：为每个指标选出 query 工具与真实 resource 名；字段/过滤只能来自 describe 摘要。

输出：
{
  "bindings": [
    {
      "goal": "充值卡数量",
      "tool": "<query工具名>",
      "resource": "<目录中的真实资源名>",
      "select_hint": "用哪些字段做 distinct/sum（来自 schema）",
      "filter_hints": ["过滤条件提示"],
      "confidence": 0.0
    }
  ]
}

规则：
1. resource 必须出现在资源目录中；没有把握则 confidence<=0.4 且 resource 可空。
2. 依据资源描述、view_comment 和字段备注判断语义，不使用历史默认视图。
3. 不要编造 view_result_*；目录给什么用什么。
4. tool 必须是目录里的 query 类工具名。
"""


@dataclass
class ResourceBinding:
    goal: str
    tool: str = ""
    resource: str = ""
    select_hint: str = ""
    filter_hints: list[str] = field(default_factory=list)
    confidence: float = 0.0
    source: str = "llm"  # llm | seed

    def to_dict(self) -> dict[str, Any]:
        return {
            "goal": self.goal,
            "tool": self.tool,
            "resource": self.resource,
            "select_hint": self.select_hint,
            "filter_hints": list(self.filter_hints),
            "confidence": self.confidence,
            "source": self.source,
        }


@dataclass
class CatalogTools:
    list_tool: str = ""
    describe_tool: str = ""
    query_tool: str = ""
    all_names: list[str] = field(default_factory=list)

    def has_discovery(self) -> bool:
        return bool(self.list_tool or self.describe_tool)


@dataclass
class BindResult:
    bindings: list[ResourceBinding] = field(default_factory=list)
    catalog: CatalogTools = field(default_factory=CatalogTools)
    resources: list[dict[str, str]] = field(default_factory=list)
    used_seed: bool = False
    error: str = ""


def _similarity_terms(text: str) -> set[str]:
    raw = str(text or "").strip().lower()
    if not raw:
        return set()
    out: set[str] = set()
    for token in re.findall(r"[a-z0-9_]+", raw):
        if len(token) >= 2:
            out.add(token)
    cjk = re.sub(r"[^\u4e00-\u9fff]", "", raw)
    if cjk:
        for n in (2, 3):
            if len(cjk) >= n:
                out.update(cjk[i : i + n] for i in range(len(cjk) - n + 1))
    return out


def _text_similarity_score(left: str, right: str) -> float:
    lset = _similarity_terms(left)
    rset = _similarity_terms(right)
    if not lset or not rset:
        return 0.0
    inter = len(lset & rset)
    if inter <= 0:
        return 0.0
    denom = max(1, min(len(lset), len(rset)))
    return inter / denom


def _intent_binding_text(mi: MetricIntentItem) -> str:
    return " ".join(
        x
        for x in [
            str(mi.goal or "").strip(),
            str(mi.entity_hint or "").strip(),
            str(mi.resource_hint or "").strip(),
        ]
        if x
    )


def _resource_evidence_text(
    resource: str,
    *,
    resources_by_name: dict[str, dict[str, str]] | None = None,
    resource_schemas: dict[str, str] | None = None,
) -> str:
    row = (
        (resources_by_name or {}).get(str(resource or "").strip())
        if isinstance(resources_by_name, dict)
        else None
    ) or {}
    desc = str(row.get("description") or row.get("comment") or "").strip()
    schema = str((resource_schemas or {}).get(str(resource or "").strip()) or "").strip()
    return " ".join(x for x in [str(resource or "").strip(), desc, schema] if x)


def _binding_candidate_score(
    binding: ResourceBinding,
    *,
    intent: MetricIntentItem | None = None,
    resources_by_name: dict[str, dict[str, str]] | None = None,
    resource_schemas: dict[str, str] | None = None,
) -> float:
    score = float(binding.confidence or 0.0)
    resource = str(binding.resource or "").strip()
    if not resource:
        return score - 1.0
    mi = intent if isinstance(intent, MetricIntentItem) else None
    intent_text = _intent_binding_text(mi) if mi else str(binding.goal or "")
    evidence = _resource_evidence_text(
        resource,
        resources_by_name=resources_by_name,
        resource_schemas=resource_schemas,
    )
    score += _text_similarity_score(intent_text, evidence) * 1.4
    if mi and str(mi.resource_hint or "").strip():
        if resource.lower() == str(mi.resource_hint).strip().lower():
            score += 1.5
        else:
            score -= 0.25
    if binding.source == "llm":
        score += 0.05
    if binding.select_hint:
        score += 0.03
    if binding.filter_hints:
        score += 0.02
    return score


def _filter_seed_bindings_to_catalog(
    seeds: list[ResourceBinding],
    *,
    allowed_resources: set[str] | None = None,
) -> list[ResourceBinding]:
    if allowed_resources is None:
        return list(seeds or [])
    out: list[ResourceBinding] = []
    for row in seeds or []:
        if not isinstance(row, ResourceBinding):
            continue
        if str(row.resource or "").strip() in allowed_resources:
            out.append(row)
    return out


def _catalog_soft_bindings(
    intents: list[MetricIntentItem],
    *,
    catalog_resources: list[dict[str, str]] | None = None,
    resource_schemas: dict[str, str] | None = None,
    query_tool: str = "query_ads_view",
) -> list[ResourceBinding]:
    rows = [dict(r) for r in (catalog_resources or []) if isinstance(r, dict)]
    if not rows or not intents:
        return []
    out: list[ResourceBinding] = []
    for mi in intents:
        scored: list[tuple[float, dict[str, str]]] = []
        intent_text = _intent_binding_text(mi)
        for row in rows:
            name = str(row.get("name") or "").strip()
            if not name:
                continue
            evidence = _resource_evidence_text(
                name,
                resources_by_name={name: row},
                resource_schemas=resource_schemas,
            )
            score = _text_similarity_score(intent_text, evidence)
            if str(mi.resource_hint or "").strip() and name.lower() == str(mi.resource_hint).strip().lower():
                score += 1.0
            if score > 0:
                scored.append((score, row))
        scored.sort(key=lambda x: x[0], reverse=True)
        chosen = 0
        for score, row in scored:
            if chosen >= 2:
                break
            if score < 0.16 and not (
                str(mi.resource_hint or "").strip()
                and str(row.get("name") or "").strip().lower() == str(mi.resource_hint).strip().lower()
            ):
                continue
            out.append(
                ResourceBinding(
                    goal=mi.goal,
                    tool=query_tool or "query_ads_view",
                    resource=str(row.get("name") or "").strip(),
                    select_hint="",
                    filter_hints=[],
                    confidence=min(0.58, 0.18 + score),
                    source="seed",
                )
            )
            chosen += 1
    return out


def _entity_hint_for_export_column(
    header: str,
    col: dict[str, Any] | None = None,
) -> str:
    text = str(header or "").strip()
    item = col if isinstance(col, dict) else {}
    role = str(
        (
            (item.get("agg_spec") or {}).get("role")
            if isinstance(item.get("agg_spec"), dict)
            else ""
        )
        or ""
    ).strip().lower()
    mode = str(item.get("fetch_mode") or "").strip().lower()
    if "封禁" in text:
        return "ban_status"
    if "注册时间" in text:
        return "user_register"
    if text in ("用户ID", "uid") or "用户id" in text.lower():
        return "user_identity"
    if "提现" in text:
        if "卡" in text:
            return "payout_card"
        return "payout"
    if "退款" in text:
        return "refund"
    if "充值" in text:
        if "卡" in text:
            return "payment_card"
        return "payment"
    if role == "cash":
        return "payout"
    if role == "user" and mode == "field":
        return "user_identity"
    if role == "pay":
        return "payment"
    return ""


def _resource_hint_for_export_column(
    header: str,
    *,
    index: int | None = None,
    context_text: str = "",
) -> str:
    """Soft user-stated resource preference from current export brief/context."""
    ctx = str(context_text or "")
    if not ctx.strip():
        return ""
    header_text = str(header or "").strip()
    header_key = re.sub(r"[（(].*?[）)]", "", header_text).strip()
    ordinal = int(index or 0) + 1 if index is not None else None
    candidates: list[str] = []
    seen: set[str] = set()

    def _add(view: str) -> None:
        name = str(view or "").strip().lower()
        if name and name not in seen:
            seen.add(name)
            candidates.append(name)

    # Prefer the most local clause that mentions either the header text or the
    # ordinal column reference plus a concrete resource name.
    for raw_line in re.split(r"[\n。；;]", ctx):
        line = str(raw_line or "").strip()
        if not line:
            continue
        line_hit = False
        if header_key and header_key in line:
            line_hit = True
        if ordinal is not None and re.search(rf"第\s*{ordinal}\s*列|^{ordinal}\s*[.、]", line):
            line_hit = True
        if not line_hit:
            continue
        for m in _VIEW_NAME_RE.finditer(line):
            _add(m.group(1))
    if candidates:
        return candidates[0]

    # Fallback: one explicit view mentioned in a ban-related clause.
    if "封禁" in header_key:
        for raw_line in re.split(r"[\n。；;]", ctx):
            line = str(raw_line or "").strip()
            if "封禁" not in line:
                continue
            for m in _VIEW_NAME_RE.finditer(line):
                _add(m.group(1))
        if len(candidates) == 1:
            return candidates[0]
    return ""


def metric_intents_from_turn(turn: TurnIntent | None) -> list[MetricIntentItem]:
    if turn is None:
        return []
    if turn.metric_intents:
        return list(turn.metric_intents)
    out: list[MetricIntentItem] = []
    for g in turn.metrics or []:
        s = str(g or "").strip()
        if s:
            out.append(MetricIntentItem(goal=s))
    if not out and (turn.query_goal or "").strip():
        out.append(MetricIntentItem(goal=turn.query_goal.strip()[:80]))
    return out


def classify_catalog_tools(tools: list[dict] | None) -> CatalogTools:
    """Map tools/list entries to list/describe/query roles (ADS names are aliases only)."""
    cat = CatalogTools()
    if not tools:
        return cat
    names: list[str] = []
    scored: dict[str, list[tuple[int, str]]] = {
        "list": [],
        "describe": [],
        "query": [],
    }
    for t in tools:
        if not isinstance(t, dict):
            continue
        name = str(t.get("name") or "").strip()
        if not name:
            continue
        names.append(name)
        desc = str(t.get("description") or "").lower()
        blob = f"{name} {desc}".lower()
        # Known ADS aliases
        if name in ("list_ads_views", "list_views", "list_resources", "list_tables"):
            scored["list"].append((100, name))
        elif name in ("describe_ads_view", "describe_view", "describe_resource", "describe_table"):
            scored["describe"].append((100, name))
        elif name in ("query_ads_view", "query_view", "query_resource", "run_query"):
            scored["query"].append((100, name))
        else:
            if re.search(r"\b(list|enumerate).*(view|table|resource|schema)", blob) or (
                "list_" in name and any(k in name for k in ("view", "table", "resource"))
            ):
                scored["list"].append((60, name))
            if re.search(r"\b(describe|schema|columns?).*(view|table|resource)", blob) or (
                "describe_" in name
            ):
                scored["describe"].append((60, name))
            if re.search(r"\b(query|select|run_sql|execute).*(view|table|sql)", blob) or (
                name.startswith("query_")
            ):
                scored["query"].append((60, name))
    cat.all_names = names
    for role, key in (("list", "list_tool"), ("describe", "describe_tool"), ("query", "query_tool")):
        opts = scored[role]
        if opts:
            opts.sort(key=lambda x: -x[0])
            setattr(cat, key, opts[0][1])
    return cat


def soft_seed_bindings(
    intents: list[MetricIntentItem],
    *,
    query_tool: str = "query_ads_view",
) -> list[ResourceBinding]:
    """Keep only user-explicit resource preferences as soft candidates."""
    out: list[ResourceBinding] = []
    for mi in intents:
        resource = str(mi.resource_hint or "").strip()
        if not resource:
            continue
        out.append(
            ResourceBinding(
                goal=mi.goal,
                tool=query_tool or "query_ads_view",
                resource=resource,
                select_hint="",
                filter_hints=[],
                confidence=0.42,
                source="seed",
            )
        )
    return out


def parse_resource_catalog(text: str) -> list[dict[str, str]]:
    """Best-effort parse of list_* tool output into {name, description}."""
    raw = (text or "").strip()
    if not raw:
        return []
    resources: list[dict[str, str]] = []
    seen: set[str] = set()

    def _append(name: str, description: str = "") -> None:
        key = str(name or "").strip()
        if not key or key in seen:
            return
        seen.add(key)
        resources.append({
            "name": key,
            "description": str(description or "")[:200],
        })

    def _walk_rows(obj: Any) -> None:
        if isinstance(obj, list):
            for row in obj:
                _walk_rows(row)
            return
        if not isinstance(obj, dict):
            return
        name = str(
            obj.get("name")
            or obj.get("view")
            or obj.get("view_name")
            or obj.get("resource")
            or obj.get("resource_name")
            or obj.get("table")
            or obj.get("table_name")
            or ""
        ).strip()
        if name:
            _append(
                name,
                str(
                    obj.get("description")
                    or obj.get("comment")
                    or obj.get("table_comment")
                    or obj.get("view_comment")
                    or obj.get("remark")
                    or ""
                ),
            )
        for val in obj.values():
            if isinstance(val, (list, dict)):
                _walk_rows(val)

    # JSON array / object with views
    try:
        data = json.loads(raw)
        if isinstance(data, list):
            for row in data:
                if isinstance(row, str):
                    _append(row, "")
                else:
                    _walk_rows(row)
        elif isinstance(data, dict):
            _walk_rows(data)
        if resources:
            return resources[:80]
    except Exception:
        pass
    # Line / bullet names
    for line in raw.splitlines():
        s = line.strip().lstrip("-*•").strip()
        if not s or len(s) > 120:
            continue
        m = re.match(r"`?([a-zA-Z_][\w.]*)`?", s)
        if m and ("view" in m.group(1).lower() or "table" in m.group(1).lower() or "_" in m.group(1)):
            _append(m.group(1), s[:200])
        if len(resources) >= 80:
            break
    return resources


def parse_bindings_payload(
    data: dict[str, Any] | None,
    *,
    allowed_resources: set[str] | None = None,
    default_tool: str = "",
    source: str = "llm",
) -> list[ResourceBinding]:
    if not isinstance(data, dict):
        return []
    rows = data.get("bindings")
    if not isinstance(rows, list):
        return []
    out: list[ResourceBinding] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        goal = str(row.get("goal") or "").strip()[:80]
        resource = str(row.get("resource") or row.get("view") or "").strip()[:120]
        tool = str(row.get("tool") or default_tool or "").strip()[:80]
        if not goal:
            continue
        if allowed_resources is not None and resource and resource not in allowed_resources:
            # Keep empty resource rather than hallucinated name
            resource = ""
        try:
            conf = float(row.get("confidence") if row.get("confidence") is not None else 0.5)
        except (TypeError, ValueError):
            conf = 0.5
        hints = row.get("filter_hints") or []
        if not isinstance(hints, list):
            hints = [str(hints)]
        out.append(
            ResourceBinding(
                goal=goal,
                tool=tool,
                resource=resource,
                select_hint=str(row.get("select_hint") or "")[:200],
                filter_hints=[str(h)[:120] for h in hints if str(h).strip()][:8],
                confidence=max(0.0, min(1.0, conf)),
                source=source,
            )
        )
    return out


def merge_bindings(
    primary: list[ResourceBinding],
    seed: list[ResourceBinding],
    *,
    intents: list[MetricIntentItem] | None = None,
    resources_by_name: dict[str, dict[str, str]] | None = None,
    resource_schemas: dict[str, str] | None = None,
) -> list[ResourceBinding]:
    """Pick the best candidate per goal from LLM + soft seed evidence."""
    candidates_by_goal: dict[str, list[ResourceBinding]] = {}
    for row in list(primary or []) + list(seed or []):
        if not isinstance(row, ResourceBinding):
            continue
        goal = str(row.goal or "").strip()
        if not goal:
            continue
        candidates_by_goal.setdefault(goal, []).append(row)

    intents_by_goal = {
        str(mi.goal or "").strip(): mi
        for mi in (intents or [])
        if str(mi.goal or "").strip()
    }
    out: list[ResourceBinding] = []
    for goal, rows in candidates_by_goal.items():
        best = max(
            rows,
            key=lambda row: _binding_candidate_score(
                row,
                intent=intents_by_goal.get(goal),
                resources_by_name=resources_by_name,
                resource_schemas=resource_schemas,
            ),
        )
        out.append(best)
    return out


async def bind_metrics_to_mcp(
    llm,
    intents: list[MetricIntentItem],
    *,
    tools: list[dict] | None = None,
    catalog_resources: list[dict[str, str]] | None = None,
    resource_schemas: dict[str, str] | None = None,
    db=None,
    timeout: int = 40,
    allow_seed: bool = True,
) -> BindResult:
    """LLM-bind intents to MCP resources; soft-seed only on failure / low confidence."""
    result = BindResult()
    if not intents:
        return result
    cat = classify_catalog_tools(tools or [])
    result.catalog = cat
    # Explicit empty list ⇒ reject LLM-invented names (preflight); None ⇒ no filter
    if catalog_resources is None:
        resources: list[dict[str, str]] = []
        allowed: set[str] | None = None
    else:
        resources = list(catalog_resources)
        allowed = {str(r.get("name") or "") for r in resources if r.get("name")}
    result.resources = resources
    resources_by_name = {
        str(r.get("name") or "").strip(): dict(r)
        for r in resources
        if str(r.get("name") or "").strip()
    }

    seed_all = soft_seed_bindings(intents, query_tool=cat.query_tool or "query_ads_view")
    seed_all.extend(
        _catalog_soft_bindings(
            intents,
            catalog_resources=resources,
            resource_schemas=resource_schemas,
            query_tool=cat.query_tool or "query_ads_view",
        )
    )
    catalog_seed = _filter_seed_bindings_to_catalog(seed_all, allowed_resources=allowed)
    seed = seed_all if allow_seed else []

    if llm is None:
        fallback = catalog_seed or seed
        result.bindings = fallback
        result.used_seed = bool(fallback)
        result.error = "no_llm"
        return result

    from app.services.llm_client import chat_completion

    catalog_txt = json.dumps(
        {
            "tools": cat.all_names[:40],
            "list_tool": cat.list_tool,
            "describe_tool": cat.describe_tool,
            "query_tool": cat.query_tool,
            "resources": resources[:60],
            "schemas": {
                k: str(v)[:400] for k, v in (resource_schemas or {}).items()
            },
        },
        ensure_ascii=False,
    )
    intent_txt = json.dumps([m.to_dict() for m in intents], ensure_ascii=False)
    messages = [
        {"role": "system", "content": _BIND_SYSTEM},
        {
            "role": "user",
            "content": f"指标意图：\n{intent_txt}\n\nMCP目录：\n{catalog_txt}",
        },
    ]
    try:
        raw = await chat_completion(
            llm, messages, max_tokens=800, db=db, timeout=timeout,
        )
        data = _extract_json_object(raw)
        llm_bindings = parse_bindings_payload(
            data,
            allowed_resources=allowed,
            default_tool=cat.query_tool,
            source="llm",
        )
        merged = merge_bindings(
            llm_bindings,
            catalog_seed or seed,
            intents=intents,
            resources_by_name=resources_by_name,
            resource_schemas=resource_schemas,
        )
        cleaned: list[ResourceBinding] = []
        used_seed = False
        for b in merged:
            if b.source == "seed":
                used_seed = True
            cleaned.append(b)
        if catalog_seed:
            bound_goals = {
                str(b.goal or "").strip()
                for b in cleaned
                if str(b.goal or "").strip() and str(b.resource or "").strip()
            }
            for s in catalog_seed:
                goal = str(s.goal or "").strip()
                if not goal or goal in bound_goals:
                    continue
                cleaned.append(s)
                used_seed = True
                bound_goals.add(goal)
        result.bindings = cleaned
        result.used_seed = used_seed or (not llm_bindings and bool(seed))
        return result
    except Exception as e:
        logger.exception("mcp_resource_bind LLM failed")
        fallback = catalog_seed or seed
        result.bindings = fallback
        result.used_seed = bool(fallback)
        result.error = str(e)[:200]
        return result


def binding_resource_whitelist(result: BindResult | None) -> list[str]:
    """Resources allowed for MCP query on this turn (need-based, no hexad)."""
    if not result:
        return []
    out: list[str] = []
    for b in result.bindings:
        r = (b.resource or "").strip()
        if r and r not in out:
            out.append(r)
    return out


def metric_intents_from_export_columns(
    column_plan: list[dict] | None = None,
    column_headers: list[str] | None = None,
    context_text: str = "",
) -> list[MetricIntentItem]:
    """Column headers / plan → MetricIntent (export path, no hexad)."""
    out: list[MetricIntentItem] = []
    seen: set[str] = set()
    ctx = str(context_text or "")
    for idx, col in enumerate(column_plan or []):
        if not isinstance(col, dict):
            continue
        header = str(col.get("header") or "").strip()
        if not header or header in seen:
            continue
        seen.add(header)
        entity = _entity_hint_for_export_column(header, col)
        resource_hint = _resource_hint_for_export_column(
            header,
            index=idx,
            context_text=ctx,
        )
        if "封禁" in header and re.search(r"封禁.{0,12}(视图|表)|视图.{0,12}封禁", ctx):
            entity = "ban_status"
        out.append(
            MetricIntentItem(
                goal=header,
                kind=str(col.get("fetch_mode") or "other") or "other",
                entity_hint=entity,
                resource_hint=resource_hint,
            )
        )
    base_idx = len(seen)
    for offset, header in enumerate(column_headers or []):
        h = str(header or "").strip()
        if not h or h in seen:
            continue
        seen.add(h)
        entity = _entity_hint_for_export_column(h)
        resource_hint = _resource_hint_for_export_column(
            h,
            index=base_idx + offset,
            context_text=ctx,
        )
        if "封禁" in h and re.search(r"封禁.{0,12}(视图|表)|视图.{0,12}封禁", ctx):
            entity = "ban_status"
        out.append(
            MetricIntentItem(
                goal=h,
                entity_hint=entity,
                resource_hint=resource_hint,
            )
        )
    return out


def resolve_export_resource_whitelist(
    column_plan: list[dict] | None = None,
    column_headers: list[str] | None = None,
    *,
    bind_result: BindResult | None = None,
    allow_seed: bool = False,
    gap_seed: bool = False,
    include_column_plan_views: bool = False,
) -> list[str]:
    """Export whitelist from runtime bindings, never from a static view template.

    ``column_plan`` is metric semantics.  Its historical ``view`` values may be
    used by migrations/tests only when ``include_column_plan_views`` is explicit;
    normal execution must wait for the MCP catalog and LLM binding.
    """
    from app.services.export_column_plan import views_from_column_plan

    out: list[str] = []
    for r in binding_resource_whitelist(bind_result):
        if r not in out:
            out.append(r)
    if include_column_plan_views:
        for v in views_from_column_plan(column_plan):
            if v and v not in out:
                out.append(v)

    intents = metric_intents_from_export_columns(column_plan, column_headers)
    bound_goals = {
        str(b.goal or "").strip()
        for b in ((bind_result.bindings if bind_result else None) or [])
        if str(b.resource or "").strip() and str(b.goal or "").strip()
    }

    need_seed: list[MetricIntentItem] = []
    if not out and allow_seed:
        need_seed = list(intents)
    elif gap_seed and intents:
        # Per-goal: seed only goals with no bind resource whose seeded view is missing
        for mi in intents:
            if mi.goal in bound_goals:
                continue
            need_seed.append(mi)
    if need_seed:
        for b in soft_seed_bindings(need_seed):
            r = (b.resource or "").strip()
            if r and r not in out:
                out.append(r)
    return out[:32]


def format_resource_binding_hint(result: BindResult | None) -> str:
    if not result or not (result.bindings or result.catalog.all_names):
        return ""
    lines = [
        "【MCP资源绑定·动态】先 list→describe→再 query；字段名必须来自 describe，"
        "资源选择必须依据本轮目录、资源备注与字段证据。",
    ]
    cat = result.catalog
    if cat.list_tool or cat.describe_tool or cat.query_tool:
        lines.append(
            f"工具映射：list={cat.list_tool or '—'}；"
            f"describe={cat.describe_tool or '—'}；"
            f"query={cat.query_tool or '—'}。"
        )
    wl = binding_resource_whitelist(result)
    if wl:
        lines.append(
            "query 白名单（仅这些 resource，禁止无白名单的全量明细）："
            + ", ".join(f"`{x}`" for x in wl)
        )
    for b in result.bindings[:10]:
        src = "seed提示" if b.source == "seed" else "LLM"
        res = b.resource or "(待 list/describe 选定)"
        lines.append(
            f"- {b.goal} → tool={b.tool or '?'} resource=`{res}` "
            f"({src}, conf={b.confidence:.2f})"
        )
        if b.select_hint:
            lines.append(f"  select_hint: {b.select_hint[:160]}")
        if b.filter_hints:
            lines.append("  filters: " + "; ".join(b.filter_hints[:4]))
    if result.used_seed:
        lines.append(
            "注：soft seed 仅作文案提示，禁止因此静默全量拉取未绑定视图。"
        )
    return "\n".join(lines)


_GAP_LABELS = {
    "metrics": "指标口径不清（可用 FINAL 追问要统计什么）",
    "time_window": "时间窗未定（可用 FINAL 追问日期，或按用户话补日历窗后再查）",
    "resource": "资源未绑定（请根据本轮 list→describe 证据选定）",
}


def format_need_based_query_plan(
    turn: TurnIntent | None,
    *,
    gaps: list[str] | None = None,
    bind_result: BindResult | None = None,
    time_window: dict[str, Any] | None = None,
) -> str:
    """Single coach block: 意图 / 已绑定 / 仍缺 — soft, never hard-stop tools."""
    if turn is None:
        return ""
    gap_list = list(gaps or [])
    intents = metric_intents_from_turn(turn)
    goal = (turn.query_goal or "").strip()
    mets = [m.goal for m in intents] or list(turn.metrics or [])
    tw = time_window if isinstance(time_window, dict) else turn.time_window
    lines: list[str] = [
        "【查数计划·按需】软教练：缺什么补什么；允许 MCP list/describe/最小 query；"
        "禁止默认拉取所有视图全量。",
    ]

    intent_bits: list[str] = []
    if goal:
        intent_bits.append(f"目标={goal}")
    if mets:
        intent_bits.append("指标=" + "、".join(mets[:8]))
    if intents:
        kinds = [f"{m.goal}({m.kind})" for m in intents[:6] if m.kind and m.kind != "other"]
        if kinds:
            intent_bits.append("形态=" + "、".join(kinds))
    if turn.result_shape and turn.result_shape != "unspecified":
        shape = turn.result_shape
        if turn.temporal_grain and turn.temporal_grain != "unspecified":
            shape += f"/{turn.temporal_grain}"
        intent_bits.append("结果形态=" + shape)
    lines.append("当前意图：" + ("；".join(intent_bits) if intent_bits else "（待澄清）") + "。")

    if isinstance(tw, dict) and tw.get("start_ms") is not None and tw.get("end_ms") is not None:
        cal = ""
        if tw.get("start_date") or tw.get("end_date"):
            cal = f"日历 {tw.get('start_date') or '?'}～{tw.get('end_date') or tw.get('start_date') or '?'}；"
        lines.append(
            f"时间窗：{tw.get('label') or ''}；{cal}"
            f"tz={tw.get('tz') or ''}；"
            f"start_ms={tw.get('start_ms')} end_ms={tw.get('end_ms')}（半开）。"
        )

    wl = binding_resource_whitelist(bind_result)
    bound_bits: list[str] = []
    if bind_result and bind_result.bindings:
        for b in bind_result.bindings[:8]:
            res = b.resource or "待 discover"
            bound_bits.append(f"{b.goal}→`{res}`")
    if bound_bits:
        lines.append("预绑定提示：" + "；".join(bound_bits) + "。")
    if wl:
        lines.append(
            "query 白名单（有绑定才限制）：" + ", ".join(f"`{x}`" for x in wl)
            + "。未点名资源勿分页全表。"
        )
    else:
        lines.append(
            "尚无 query 白名单：请先 list→describe 再最小 query；"
            "勿默认全量拉取（不是禁止使用 MCP）。"
        )
        if bind_result and bind_result.catalog.all_names:
            cat = bind_result.catalog
            lines.append(
                f"工具映射：list={cat.list_tool or '—'}；"
                f"describe={cat.describe_tool or '—'}；"
                f"query={cat.query_tool or '—'}。"
            )

    if gap_list:
        miss = [_GAP_LABELS.get(g, g) for g in gap_list]
        lines.append("仍缺：" + "；".join(miss) + "。")
        lines.append(
            "建议：FINAL 一句向用户澄清，或继续 MCP discover；"
            "不要空转叙述；不要因缺口停止工具环。"
        )
    else:
        lines.append(
            "缺口已清：对白名单（若有）做最小 query；"
            "字段名来自 describe；FINAL 用 Markdown「### 查询结果」表格。"
        )
    if bind_result and bind_result.used_seed:
        lines.append("seed 仅文案提示，discover 结果优先于 seed。")
    return "\n".join(lines)


def ensure_sql_resource_aligned(resource: str, sql: str, *, schema_prefix: str = "ads") -> str:
    """Force SQL FROM <prefix>.<resource> to match tool resource/view param."""
    raw = (sql or "").strip()
    view = (resource or "").strip()
    if not raw or not view:
        return raw
    # Already FROM prefix.view — rewrite if different table
    m = re.search(
        rf"\bFROM\s+((?:{re.escape(schema_prefix)}\.)?)(`?)([A-Za-z_][\w]*)(`?)",
        raw,
        re.I,
    )
    if m:
        current = m.group(3)
        if current == view:
            return raw
        return raw[: m.start(3)] + view + raw[m.end(3) :]
    # Bare FROM without prefix
    m2 = re.search(r"\bFROM\s+(`?)([A-Za-z_][\w]*)(`?)", raw, re.I)
    if m2:
        return (
            raw[: m2.start()]
            + f"FROM {schema_prefix}.{view}"
            + raw[m2.end() :]
        )
    # WHERE-only / SELECT * WHERE
    where = raw
    m3 = re.match(r"(?is)^SELECT\s+\*\s+WHERE\s+(.+)$", raw)
    if m3:
        where = m3.group(1).strip()
    elif re.match(r"(?is)^SELECT\b", raw):
        wm = re.search(r"(?is)\bWHERE\b\s*(.+)$", raw)
        where = wm.group(1).strip() if wm else "1"
    elif re.match(r"(?is)^WHERE\s+", raw):
        where = re.sub(r"(?is)^WHERE\s+", "", raw).strip()
    else:
        return raw
    return f"SELECT * FROM {schema_prefix}.{view} WHERE {where}"
