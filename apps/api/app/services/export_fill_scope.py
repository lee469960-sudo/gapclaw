"""LLM-resolved column fill scope for export repair (no regex column extraction)."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from app.services.intent_router import _extract_json_object

logger = logging.getLogger(__name__)

_FILL_SCOPE_SYSTEM = """你是导出「按列补齐」范围解析器。只输出一个 JSON 对象，不要 Markdown。

输入：用户本轮话术 + 上一轮报表已有表头列表（prior_headers）+ 可选缺失提示。
任务：判断用户是否只要补其中若干列，并选出要补的表头。

输出：
{
  "is_column_fill": true,
  "focus_columns": ["总下注金额", "充值银行卡数量"],
  "include_identity": true,
  "reason": "一句话中文理由"
}

规则：
1. focus_columns 必须从 prior_headers 中选择（可用近义对齐到最接近的表头；禁止臆造新列名）。
2. 用户明确点名要补/重算/仍缺的列 → is_column_fill=true，focus_columns=这些列。
3. 用户说「全部重导 / 整表重来 / 全部列重新导出」→ is_column_fill=false，focus_columns=[]。
4. 用户只抱怨「不全/有问题」但未点任何列 → is_column_fill=false，focus_columns=[]。
5. 禁止输出 view_result_* / role=pay|cash|bet 等资源名。
6. include_identity：补事实列时通常 true（需要 uid 关联）；仅补身份字段时可 false。
"""


@dataclass
class ExportFillScope:
    is_column_fill: bool = False
    focus_columns: list[str] = field(default_factory=list)
    include_identity: bool = True
    reason: str = ""
    source: str = "fallback"  # llm | fallback

    def to_dict(self) -> dict[str, Any]:
        return {
            "is_column_fill": self.is_column_fill,
            "focus_columns": list(self.focus_columns),
            "include_identity": self.include_identity,
            "reason": self.reason,
            "source": self.source,
        }


def prior_headers_from_state(state: dict[str, Any] | None) -> list[str]:
    """Collect analyze column headers from prior run_state / column_plan."""
    st = state if isinstance(state, dict) else {}
    out: list[str] = []
    seen: set[str] = set()
    for c in st.get("analyze_columns") or []:
        h = str(c or "").strip()
        if h and h not in seen:
            seen.add(h)
            out.append(h)
    for col in st.get("column_plan") or []:
        if not isinstance(col, dict):
            continue
        h = str(col.get("header") or "").strip()
        if h and h not in seen:
            seen.add(h)
            out.append(h)
    for t in st.get("todos") or []:
        if not isinstance(t, dict) or t.get("phase") != "analyze":
            continue
        h = str(t.get("text") or "").strip()
        if h and h not in seen and not h.startswith(("请优先补齐", "上轮已覆盖")):
            seen.add(h)
            out.append(h)
    return out[:32]


def _norm_header(s: str) -> str:
    return "".join(ch for ch in (s or "").strip().lower() if not ch.isspace())


def align_focus_to_headers(
    focus: list[str] | None,
    prior_headers: list[str],
) -> list[str]:
    """Map LLM focus names onto prior_headers (exact / normalized / containment)."""
    headers = [str(h).strip() for h in (prior_headers or []) if str(h).strip()]
    if not headers:
        return []
    by_norm = {_norm_header(h): h for h in headers}
    out: list[str] = []
    seen: set[str] = set()
    for raw in focus or []:
        name = str(raw or "").strip()
        if not name:
            continue
        picked = ""
        if name in headers:
            picked = name
        else:
            n = _norm_header(name)
            if n in by_norm:
                picked = by_norm[n]
            else:
                # containment either way
                for h in headers:
                    hn = _norm_header(h)
                    if n and (n in hn or hn in n):
                        picked = h
                        break
        if picked and picked not in seen:
            seen.add(picked)
            out.append(picked)
    return out


def pick_identity_headers(prior_headers: list[str]) -> list[str]:
    """Identity columns already present in prior headers (for join)."""
    out: list[str] = []
    for h in prior_headers or []:
        n = _norm_header(h)
        if n in ("uid", "userid", "user_id") or "用户id" in n or n.endswith("用户编号"):
            if h not in out:
                out.append(h)
    return out


def merge_fill_headers(
    focus_columns: list[str],
    prior_headers: list[str],
    *,
    include_identity: bool = True,
) -> list[str]:
    """Final headers for this fill turn: focus (+ identity if requested)."""
    aligned = align_focus_to_headers(focus_columns, prior_headers)
    if not aligned:
        return []
    out = list(aligned)
    if include_identity:
        for h in pick_identity_headers(prior_headers):
            if h not in out:
                out.insert(0, h)
    return out


def resolve_export_fill_target_rel(
    sandbox_id: str,
    prior_state: dict[str, Any] | None,
) -> str:
    """Soft-resolve prior deliverable path to overwrite; empty if none (never hard-fail)."""
    from app.services.workplace import download_path, list_recent_data_files

    sid = str(sandbox_id or "").strip()
    if not sid:
        return ""
    st = prior_state if isinstance(prior_state, dict) else {}
    candidates: list[str] = []
    raw = str(st.get("deliverable") or "").strip().replace("\\", "/")
    if raw:
        candidates.append(raw)
        base = raw.rsplit("/", 1)[-1]
        if base and base != raw:
            candidates.append(base)

    def _ok(rel: str) -> bool:
        r = (rel or "").strip().replace("\\", "/")
        if not r or r.startswith("task/") or r.startswith("workplace/"):
            return False
        if "/" in r:
            return False  # root deliverables only
        name = r.rsplit("/", 1)[-1]
        low = name.lower()
        if low.startswith("export_") or low.startswith("_fill_tmp"):
            return False
        p = download_path(sid, r)
        return bool(p and p.is_file())

    for rel in candidates:
        if _ok(rel):
            return rel.strip().replace("\\", "/")
    for rel in list_recent_data_files(sid, limit=24):
        if _ok(rel):
            return rel.strip().replace("\\", "/")
    return ""


def parse_fill_scope_payload(
    data: dict[str, Any] | None,
    *,
    prior_headers: list[str],
    source: str = "llm",
) -> ExportFillScope:
    if not isinstance(data, dict):
        return ExportFillScope(source="fallback")
    is_fill = bool(data.get("is_column_fill"))
    raw_focus = data.get("focus_columns") or []
    if not isinstance(raw_focus, list):
        raw_focus = []
    focus = align_focus_to_headers([str(x) for x in raw_focus], prior_headers)
    include_identity = data.get("include_identity")
    if include_identity is None:
        include_identity = True
    reason = str(data.get("reason") or "").strip()[:200]
    if is_fill and not focus:
        # Model claimed fill but none aligned → treat as non-scoped
        return ExportFillScope(
            is_column_fill=False,
            focus_columns=[],
            include_identity=bool(include_identity),
            reason=reason or "focus_columns 未能对齐表头",
            source=source,
        )
    return ExportFillScope(
        is_column_fill=bool(is_fill and focus),
        focus_columns=focus,
        include_identity=bool(include_identity),
        reason=reason,
        source=source,
    )


async def resolve_export_fill_scope(
    llm,
    *,
    user_message: str,
    prior_headers: list[str],
    missing_hint: list[str] | None = None,
    db=None,
    timeout: int = 20,
) -> ExportFillScope:
    """LLM: which prior columns to refill. Never raises; empty scope on failure."""
    headers = [str(h).strip() for h in (prior_headers or []) if str(h).strip()]
    if not headers or not (user_message or "").strip():
        return ExportFillScope(source="fallback")
    if llm is None:
        return ExportFillScope(source="fallback")

    from app.services.llm_client import chat_completion

    miss = [str(x).strip() for x in (missing_hint or []) if str(x).strip()][:16]
    user_block = (
        f"用户消息：\n{(user_message or '')[:1500]}\n\n"
        f"prior_headers（必须从中选）：\n"
        + "\n".join(f"- {h}" for h in headers)
    )
    if miss:
        user_block += "\n\n缺失提示（仅供参考，勿强制全选）：\n" + "\n".join(
            f"- {m}" for m in miss
        )
    messages = [
        {"role": "system", "content": _FILL_SCOPE_SYSTEM},
        {"role": "user", "content": user_block},
    ]
    try:
        raw = await chat_completion(
            llm,
            messages,
            max_tokens=500,
            db=db,
            timeout=timeout,
        )
        data = _extract_json_object(raw)
        if not data:
            logger.warning("export_fill_scope: non-json reply")
            return ExportFillScope(source="fallback")
        return parse_fill_scope_payload(data, prior_headers=headers, source="llm")
    except Exception:
        logger.exception("export_fill_scope LLM failed")
        return ExportFillScope(source="fallback")


def build_column_fill_repair_plan(
    *,
    focus_columns: list[str],
    prior_trace: dict[str, Any] | None = None,
    prior_state: dict[str, Any] | None = None,
    column_plan: list[dict[str, Any]] | None = None,
    time_window: dict[str, Any] | None = None,
    dim_views: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Declarative RepairPlan scoped to focus columns (for limit_query_nodes_for_repair)."""
    from app.services.export_column_plan import build_query_graph
    from app.services.export_repair_plan import RepairAction, RepairPlan, _column_sources, _dedupe

    tr = dict(prior_trace or {})
    if not tr.get("column_plan") and isinstance(prior_state, dict):
        if prior_state.get("column_plan"):
            tr["column_plan"] = prior_state.get("column_plan")
        if prior_state.get("query_graph"):
            tr["query_graph"] = prior_state.get("query_graph")
        if prior_state.get("done_node_keys"):
            tr["done_node_keys"] = prior_state.get("done_node_keys")
        if prior_state.get("failed_views"):
            tr["failed_views"] = prior_state.get("failed_views")

    done_keys = _dedupe(tr.get("done_node_keys") or [])
    actions: list[RepairAction] = []
    all_keys: list[str] = []

    # Prefer fresh query graph from narrowed column_plan (reliable node_keys)
    plan = column_plan if isinstance(column_plan, list) and column_plan else None
    if plan:
        nodes = build_query_graph(
            plan,
            time_window,
            dim_views=dim_views or {},
            bound_only=True,
        )
        for node in nodes:
            if not isinstance(node, dict):
                continue
            key = str(node.get("key") or "").strip()
            view = str(node.get("view") or "").strip()
            role = str(node.get("role") or "").strip()
            headers = [
                str(h).strip()
                for h in (node.get("headers") or [])
                if str(h).strip()
            ]
            if key:
                all_keys.append(key)
            actions.append(
                RepairAction(
                    action_type="fetch_noncore_or_keep_incomplete",
                    priority=1,
                    column=",".join(headers[:3]) or (focus_columns[0] if focus_columns else ""),
                    role=role,
                    views=[view] if view else [],
                    node_keys=[key] if key else [],
                    reason="用户点名列补齐·查询图节点",
                    blocking=False,
                )
            )

    if not actions:
        for col in focus_columns or []:
            roles, views, keys = _column_sources(tr, col)
            node_keys = _dedupe(keys)
            all_keys.extend(node_keys)
            actions.append(
                RepairAction(
                    action_type="fetch_noncore_or_keep_incomplete",
                    priority=1,
                    column=col,
                    role=",".join(roles),
                    views=views,
                    node_keys=node_keys,
                    reason=f"用户点名补齐列：{col}",
                    blocking=False,
                )
            )

    fill_key_set = set(all_keys)
    preserve = [k for k in done_keys if k not in fill_key_set]
    status = "repairable" if actions else "none"
    return RepairPlan(
        status=status,
        source_status="column_fill",
        actions=actions,
        preserve_done_node_keys=preserve,
        skip_failed_node_keys=[],
        repair_hints=[f"LLM列补齐：{', '.join(focus_columns[:8])}"] if focus_columns else [],
    ).to_dict()
