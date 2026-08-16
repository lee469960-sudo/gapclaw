"""Compile verifier gaps into a structured export repair plan."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class RepairAction:
    action_type: str
    priority: int
    reason: str
    role: str = ""
    column: str = ""
    views: list[str] = field(default_factory=list)
    node_keys: list[str] = field(default_factory=list)
    blocking: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RepairPlan:
    status: str = "none"  # none | repairable | blocked
    source_status: str = ""
    actions: list[RepairAction] = field(default_factory=list)
    preserve_done_node_keys: list[str] = field(default_factory=list)
    skip_failed_node_keys: list[str] = field(default_factory=list)
    repair_hints: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "source_status": self.source_status,
            "actions": [a.to_dict() for a in self.actions],
            "preserve_done_node_keys": list(self.preserve_done_node_keys),
            "skip_failed_node_keys": list(self.skip_failed_node_keys),
            "repair_hints": list(self.repair_hints),
            "summary": {
                "action_count": len(self.actions),
                "blocking_count": sum(1 for a in self.actions if a.blocking),
            },
        }


def _dedupe(items: list[str] | tuple[str, ...] | set[str]) -> list[str]:
    return [x for x in dict.fromkeys(str(i).strip() for i in items if str(i).strip())]


def _query_nodes(trace: dict[str, Any]) -> list[dict[str, Any]]:
    return [n for n in (trace.get("query_graph") or []) if isinstance(n, dict)]


def _node_keys_for_role(trace: dict[str, Any], role: str) -> tuple[list[str], list[str]]:
    r = (role or "").strip().lower()
    keys: list[str] = []
    views: list[str] = []
    for node in _query_nodes(trace):
        if str(node.get("role") or "").strip().lower() != r:
            continue
        keys.append(str(node.get("key") or "").strip())
        views.append(str(node.get("view") or "").strip())
    return _dedupe(keys), _dedupe(views)


def _column_sources(trace: dict[str, Any], column: str) -> tuple[list[str], list[str], list[str]]:
    col = (column or "").strip()
    roles: list[str] = []
    views: list[str] = []
    keys: list[str] = []
    for item in trace.get("column_plan") or []:
        if not isinstance(item, dict) or str(item.get("header") or "").strip() != col:
            continue
        for src in item.get("sources") or []:
            if not isinstance(src, dict):
                continue
            roles.append(str(src.get("role") or "").strip())
            views.append(str(src.get("view") or "").strip())
        agg = item.get("agg_spec") if isinstance(item.get("agg_spec"), dict) else {}
        roles.append(str(agg.get("role") or "").strip())
        views.append(str(agg.get("view") or "").strip())
    role_set = _dedupe(roles)
    view_set = _dedupe(views)
    for node in _query_nodes(trace):
        headers = [str(h).strip() for h in (node.get("headers") or []) if str(h).strip()]
        if col in headers:
            keys.append(str(node.get("key") or "").strip())
            views.append(str(node.get("view") or "").strip())
        elif role_set and str(node.get("role") or "").strip().lower() in {r.lower() for r in role_set}:
            keys.append(str(node.get("key") or "").strip())
    return role_set, _dedupe(views), _dedupe(keys)


def _failed_node_keys(trace: dict[str, Any]) -> list[str]:
    failed = [str(x).strip() for x in (trace.get("failed_views") or []) if str(x).strip()]
    out: list[str] = []
    for item in failed:
        if item.startswith("node:"):
            out.append(item.split("node:", 1)[1])
    return _dedupe(out)


def _schema_discovery_from(trace: dict[str, Any]) -> dict[str, Any]:
    schema = trace.get("schema_discovery")
    if isinstance(schema, dict):
        return schema
    contract = trace.get("export_contract")
    if isinstance(contract, dict) and isinstance(contract.get("schema_discovery"), dict):
        return dict(contract["schema_discovery"])
    return {}


def _schema_repair_actions(
    verifier_result: dict[str, Any],
    trace: dict[str, Any],
) -> list[RepairAction]:
    summary = (
        verifier_result.get("summary")
        if isinstance(verifier_result.get("summary"), dict)
        else {}
    )
    schema = (
        summary.get("schema_discovery")
        if isinstance(summary.get("schema_discovery"), dict)
        else {}
    )
    if not schema:
        schema = _schema_discovery_from(trace)
    if not isinstance(schema, dict) or not schema.get("required"):
        return []

    listed = schema.get("list_ads_views")
    listed_ok = bool(listed is True or (isinstance(listed, dict) and listed.get("captured")))
    described = schema.get("described_views")
    described_views = set((described or {}).keys()) if isinstance(described, dict) else set()
    missing = _dedupe(schema.get("missing_describe_views") or [])
    if not missing:
        planned_views = [
            str(node.get("view") or "").strip()
            for node in _query_nodes(trace)
            if str(node.get("view") or "").strip()
        ]
        missing = [v for v in _dedupe(planned_views) if v not in described_views]

    actions: list[RepairAction] = []
    if not listed_ok:
        actions.append(RepairAction(
            action_type="discover_ads_views",
            priority=-2,
            reason="缺少 MCP 接口列表发现证据：先调用 list_ads_views",
            blocking=False,
        ))
    if missing:
        actions.append(RepairAction(
            action_type="describe_ads_views",
            priority=-1,
            views=missing,
            reason="缺少 MCP 表备注/字段发现证据：对计划 view 调用 describe_ads_view",
            blocking=False,
        ))
    return actions


def build_repair_plan(
    verifier_result: dict[str, Any] | None,
    trace: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a deterministic repair plan from verifier output and trace.

    The plan is intentionally declarative; execution remains in the engine.
    """
    vr = verifier_result if isinstance(verifier_result, dict) else {}
    tr = trace if isinstance(trace, dict) else {}
    source_status = str(vr.get("status") or "").strip() or ("pass" if vr.get("ok") else "failed")
    done_keys = _dedupe(tr.get("done_node_keys") or [])
    failed_node_keys = _failed_node_keys(tr)
    actions: list[RepairAction] = []
    actions.extend(_schema_repair_actions(vr, tr))

    if source_status == "pass" and actions:
        actions.sort(key=lambda a: (a.priority, a.action_type, a.role, a.column))
        return RepairPlan(
            status="repairable",
            source_status=source_status,
            actions=actions,
            preserve_done_node_keys=done_keys,
            skip_failed_node_keys=failed_node_keys,
            repair_hints=_dedupe(vr.get("repair_hints") or []),
        ).to_dict()

    if source_status == "pass":
        return RepairPlan(
            status="none",
            source_status=source_status,
            preserve_done_node_keys=done_keys,
            skip_failed_node_keys=failed_node_keys,
            repair_hints=_dedupe(vr.get("repair_hints") or []),
        ).to_dict()

    for role in _dedupe(vr.get("missing_core") or []):
        keys, views = _node_keys_for_role(tr, role)
        actions.append(RepairAction(
            action_type="fetch_core_role",
            priority=0,
            role=role,
            views=views,
            node_keys=[k for k in keys if k not in done_keys],
            reason=f"核心 role 缺失或失败：{role}",
            blocking=True,
        ))

    for col in _dedupe(vr.get("missing_columns") or []):
        roles, views, keys = _column_sources(tr, col)
        actions.append(RepairAction(
            action_type="rewrite_artifact",
            priority=1,
            column=col,
            role=",".join(roles),
            views=views,
            node_keys=[k for k in keys if k not in done_keys],
            reason=f"交付缺少输出列：{col}",
            blocking=source_status == "failed",
        ))

    for col in _dedupe(vr.get("incomplete_non_core") or []):
        roles, views, keys = _column_sources(tr, col)
        actions.append(RepairAction(
            action_type="fetch_noncore_or_keep_incomplete",
            priority=2,
            column=col,
            role=",".join(roles),
            views=views,
            node_keys=[k for k in keys if k not in done_keys and k not in failed_node_keys],
            reason=f"非核心列未完整：{col}",
            blocking=False,
        ))

    errors = [str(e) for e in (vr.get("errors") or [])]
    if any("交付文件" in e or "数据 sheet" in e or "数据行为 0" in e for e in errors):
        actions.append(RepairAction(
            action_type="rematerialize_artifact",
            priority=1,
            reason="交付文件缺失、空表或 sheet 不完整",
            blocking=source_status == "failed",
        ))

    if not actions and source_status != "pass":
        actions.append(RepairAction(
            action_type="inspect_verifier_warnings",
            priority=3,
            reason="存在 verifier 警告，需要人工或模型解释后决定是否重写",
            blocking=False,
        ))

    actions.sort(key=lambda a: (a.priority, a.action_type, a.role, a.column))
    status = "blocked" if any(a.blocking for a in actions) else "repairable"
    return RepairPlan(
        status=status,
        source_status=source_status,
        actions=actions,
        preserve_done_node_keys=done_keys,
        skip_failed_node_keys=failed_node_keys,
        repair_hints=_dedupe(vr.get("repair_hints") or []),
    ).to_dict()


def activate_repair_plan_from_trace(prior_trace: dict[str, Any] | None) -> dict[str, Any]:
    """Merge prior trace and repair_plan into resume-safe key sets."""
    tr = prior_trace if isinstance(prior_trace, dict) else {}
    repair = tr.get("repair_plan") if isinstance(tr.get("repair_plan"), dict) else {}
    keep_keys = _dedupe(repair.get("preserve_done_node_keys") or [])
    skip_keys = _dedupe(repair.get("skip_failed_node_keys") or [])
    done = _dedupe([
        *[str(k) for k in (tr.get("done_node_keys") or []) if str(k)],
        *keep_keys,
    ])
    failed = _dedupe([
        *[str(v) for v in (tr.get("failed_views") or []) if str(v)],
        *[f"node:{k}" for k in skip_keys],
    ])
    return {
        "done_node_keys": done,
        "failed_views": failed,
        "repair_plan": dict(repair or {}),
    }


def activate_repair_plan_from_prior(
    prior_trace: dict[str, Any] | None,
    prior_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Resume from export_trace first, with _run_state repair_plan as fallback."""
    trace_activated = activate_repair_plan_from_trace(prior_trace)
    state_activated = activate_repair_plan_from_trace(prior_state)
    trace_repair = trace_activated.get("repair_plan")
    state_repair = state_activated.get("repair_plan")
    repair = trace_repair if isinstance(trace_repair, dict) and trace_repair else state_repair
    return {
        "done_node_keys": _dedupe([
            *list(trace_activated.get("done_node_keys") or []),
            *list(state_activated.get("done_node_keys") or []),
        ]),
        "failed_views": _dedupe([
            *list(trace_activated.get("failed_views") or []),
            *list(state_activated.get("failed_views") or []),
        ]),
        "repair_plan": dict(repair or {}),
    }


def prioritize_query_nodes_for_repair(
    nodes: list[dict[str, Any]] | None,
    repair_plan: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """Stable-order query nodes so repair actions run first.

    This intentionally prioritizes rather than filters; dependencies can still
    run if a repair action did not name every needed node.
    """
    graph = [n for n in (nodes or []) if isinstance(n, dict)]
    repair = repair_plan if isinstance(repair_plan, dict) else {}
    actions = [a for a in (repair.get("actions") or []) if isinstance(a, dict)]
    if not graph or not actions:
        return graph

    key_rank: dict[str, int] = {}
    role_rank: dict[str, int] = {}
    view_rank: dict[str, int] = {}
    for idx, action in enumerate(actions):
        base = int(action.get("priority") or idx)
        for key in _dedupe(action.get("node_keys") or []):
            key_rank.setdefault(key, base)
        for role in _dedupe(str(action.get("role") or "").split(",")):
            role_rank.setdefault(role.lower(), base + 10)
        for view in _dedupe(action.get("views") or []):
            view_rank.setdefault(view, base + 10)

    def _rank(item: tuple[int, dict[str, Any]]) -> tuple[int, int]:
        idx, node = item
        key = str(node.get("key") or "").strip()
        role = str(node.get("role") or "").strip().lower()
        view = str(node.get("view") or "").strip()
        rank = min(
            key_rank.get(key, 1000),
            role_rank.get(role, 1000),
            view_rank.get(view, 1000),
        )
        return rank, idx

    return [node for _, node in sorted(enumerate(graph), key=_rank)]


def limit_query_nodes_for_repair(
    nodes: list[dict[str, Any]] | None,
    repair_plan: dict[str, Any] | None,
) -> tuple[list[dict[str, Any]], bool]:
    """Limit query graph to explicit repair node keys when safely possible.

    Returns (nodes, scoped). If the repair plan does not name node_keys, keep
    the original graph so dependency nodes can still run.
    """
    graph = [n for n in (nodes or []) if isinstance(n, dict)]
    repair = repair_plan if isinstance(repair_plan, dict) else {}
    actions = [a for a in (repair.get("actions") or []) if isinstance(a, dict)]
    wanted = {
        key
        for action in actions
        for key in _dedupe(action.get("node_keys") or [])
    }
    if not graph or not wanted:
        return graph, False
    scoped = [
        n for n in graph
        if str(n.get("key") or "").strip() in wanted
    ]
    if not scoped:
        return graph, False
    return scoped, True


def repair_plan_should_reverify_after_progress(
    repair_plan: dict[str, Any] | None,
    *,
    landed_count: int,
    scoped: bool = False,
) -> bool:
    """True when repair progress should force rematerialize + verifier.

    A scoped repair run with landed rows is the strongest signal. For unscoped
    runs, only data/materialization actions trigger this; pure inspection does
    not.
    """
    if int(landed_count or 0) <= 0:
        return False
    repair = repair_plan if isinstance(repair_plan, dict) else {}
    actions = [a for a in (repair.get("actions") or []) if isinstance(a, dict)]
    if not actions:
        return False
    if scoped:
        return True
    material_actions = {
        "fetch_core_role",
        "fetch_noncore_or_keep_incomplete",
        "rewrite_artifact",
        "rematerialize_artifact",
    }
    return any(str(a.get("action_type") or "") in material_actions for a in actions)


def format_repair_plan_block(repair_plan: dict[str, Any] | None) -> str:
    """Markdown block for user-facing next repair actions."""
    repair = repair_plan if isinstance(repair_plan, dict) else {}
    actions = [a for a in (repair.get("actions") or []) if isinstance(a, dict)]
    if not actions:
        return ""
    lines = ["### 修复计划"]
    for action in actions[:6]:
        kind = str(action.get("action_type") or "").strip() or "repair"
        reason = str(action.get("reason") or "").strip()
        role = str(action.get("role") or "").strip()
        column = str(action.get("column") or "").strip()
        views = [
            str(v).strip()
            for v in (action.get("views") or [])
            if str(v).strip()
        ]
        parts = [kind]
        if role:
            parts.append(f"role={role}")
        if column:
            parts.append(f"column={column}")
        if views:
            parts.append("views=" + "、".join(views[:4]))
        if reason:
            parts.append(reason)
        lines.append("- " + "；".join(parts))
    return "\n".join(lines)
