"""Export run trace: structured MCP / materialize / verify record for resume."""

from __future__ import annotations

import json
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from app.services.workplace import ensure_workplace


def _safe_run(run_id: str) -> str:
    return re.sub(r"[^\w\-]+", "_", (run_id or "run").strip())[:80] or "run"


@dataclass
class QueryNodeTrace:
    key: str = ""
    role: str = ""
    view: str = ""
    sql: str = ""
    params: dict[str, Any] = field(default_factory=dict)
    started_at: float | None = None
    ended_at: float | None = None
    duration_ms: float | None = None
    row_count: int | None = None
    truncated: bool = False
    error: str = ""
    error_class: str = ""
    retry_count: int = 0
    fallback_used: bool = False
    status: str = "pending"  # pending|ok|error|soft_fail|skipped


@dataclass
class ExportTrace:
    run_id: str = ""
    source_brief: str = ""
    export_contract: dict[str, Any] = field(default_factory=dict)
    task_spec: dict[str, Any] = field(default_factory=dict)
    task_spec_validation: dict[str, Any] = field(default_factory=dict)
    column_plan: list[dict[str, Any]] = field(default_factory=list)
    query_contract: dict[str, Any] = field(default_factory=dict)
    query_graph: list[dict[str, Any]] = field(default_factory=list)
    schema_discovery: dict[str, Any] = field(default_factory=dict)
    query_nodes: list[dict[str, Any]] = field(default_factory=list)
    materialization: dict[str, Any] = field(default_factory=dict)
    verification: dict[str, Any] = field(default_factory=dict)
    repair_plan: dict[str, Any] = field(default_factory=dict)
    failed_views: list[str] = field(default_factory=list)
    abandoned_roles: list[str] = field(default_factory=list)
    done_node_keys: list[str] = field(default_factory=list)
    fetched_view_pages: dict[str, int] = field(default_factory=dict)
    cte_attempts_used: int = 0
    cte_attempt_limit: int = 0
    cte_attempt_errors: list[dict[str, Any]] = field(default_factory=list)
    updated_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def record_query(
        self,
        *,
        key: str,
        role: str = "",
        view: str = "",
        sql: str = "",
        params: dict | None = None,
        row_count: int | None = None,
        error: str = "",
        error_class: str = "",
        retry_count: int = 0,
        fallback_used: bool = False,
        truncated: bool = False,
        status: str = "",
        duration_ms: float | None = None,
    ) -> None:
        st = status or ("error" if error else "ok")
        self.query_nodes.append(
            {
                "key": key,
                "role": role,
                "view": view,
                "sql": (sql or "")[:4000],
                "params": dict(params or {}),
                "row_count": row_count,
                "truncated": truncated,
                "error": (error or "")[:800],
                "error_class": error_class or classify_mcp_error(error),
                "retry_count": retry_count,
                "fallback_used": fallback_used,
                "status": st,
                "duration_ms": duration_ms,
                "ended_at": time.time(),
            }
        )
        self.updated_at = time.time()

    def set_materialization(self, **kwargs: Any) -> None:
        self.materialization.update(kwargs)
        self.updated_at = time.time()

    def set_verification(self, result: dict[str, Any] | None) -> None:
        self.verification = dict(result or {})
        self.updated_at = time.time()

    def set_repair_plan(self, plan: dict[str, Any] | None) -> None:
        self.repair_plan = dict(plan or {})
        self.updated_at = time.time()

    def set_schema_discovery(self, discovery: dict[str, Any] | None) -> None:
        self.schema_discovery = dict(discovery or {})
        if isinstance(self.export_contract, dict):
            self.export_contract["schema_discovery"] = dict(self.schema_discovery)
        self.updated_at = time.time()

    def record_ads_views_list(self, text: str) -> None:
        discovery = dict(self.schema_discovery or {})
        discovery["list_ads_views"] = {
            "captured": True,
            "excerpt": (text or "")[:2000],
            "updated_at": time.time(),
        }
        self.set_schema_discovery(discovery)

    def record_ads_view_schema(self, view: str, schema: dict[str, Any] | None) -> None:
        v = str(view or "").strip()
        if not v:
            return
        discovery = dict(self.schema_discovery or {})
        described = discovery.get("described_views")
        if not isinstance(described, dict):
            described = {}
        payload = dict(schema or {})
        payload.setdefault("view", v)
        payload["source"] = "describe_ads_view"
        payload["updated_at"] = time.time()
        described[v] = payload
        discovery["described_views"] = described
        self.set_schema_discovery(discovery)


def classify_mcp_error(text: str) -> str:
    t = (text or "").lower()
    if not t.strip():
        return ""
    if "timeout" in t or "timed out" in t:
        return "timeout"
    if "unknown expression" in t or "unknown identifier" in t:
        return "unknown_column"
    if "illegal types" in t or "type mismatch" in t:
        return "type_mismatch"
    if "permission" in t or "denied" in t or "auth" in t:
        return "permission"
    if "syntax" in t:
        return "syntax"
    if "format" in t and "clause" in t:
        return "format_clause"
    if "远程失败" in (text or "") or "mcp" in t:
        return "mcp_remote"
    return "other"


def export_trace_rel(run_id: str) -> str:
    return f"task/{_safe_run(run_id)}/export_trace.json"


def write_export_trace(sandbox_id: str, run_id: str, trace: ExportTrace | dict) -> str:
    """Persist export_trace.json under workplace/task/<run_id>/."""
    root = ensure_workplace(sandbox_id)
    rel = export_trace_rel(run_id)
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = trace.to_dict() if isinstance(trace, ExportTrace) else dict(trace)
    payload["updated_at"] = time.time()
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return rel


def read_export_trace(sandbox_id: str, run_id: str) -> dict[str, Any] | None:
    root = ensure_workplace(sandbox_id)
    path = root / export_trace_rel(run_id)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def load_latest_export_trace(
    sandbox_id: str,
    *,
    session_id: str = "",
) -> tuple[str, dict[str, Any]] | None:
    """Find the newest eligible export trace for resume.

    A supplied session id is an identity boundary: traces without a matching
    sibling run state are not eligible.
    """
    root = ensure_workplace(sandbox_id)
    task = root / "task"
    if not task.is_dir():
        return None
    best: tuple[float, str, dict] | None = None
    wanted_session = str(session_id or "").strip()
    for p in task.glob("*/export_trace.json"):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                continue
            if wanted_session:
                state_path = p.parent / "_run_state.json"
                if not state_path.is_file():
                    continue
                state = json.loads(state_path.read_text(encoding="utf-8"))
                if (
                    not isinstance(state, dict)
                    or str(state.get("session_id") or "").strip() != wanted_session
                ):
                    continue
            mtime = p.stat().st_mtime
            run = p.parent.name
            if best is None or mtime > best[0]:
                best = (mtime, run, data)
        except Exception:
            continue
    if not best:
        return None
    return best[1], best[2]


def merge_run_state_into_trace(trace: ExportTrace, run_state: dict | None) -> None:
    rs = run_state if isinstance(run_state, dict) else {}
    if rs.get("fetched_view_pages"):
        trace.fetched_view_pages = {
            str(k): int(v or 0) for k, v in dict(rs["fetched_view_pages"]).items()
        }
    if rs.get("export_failed_views") or rs.get("failed_views"):
        failed = rs.get("export_failed_views") or rs.get("failed_views") or []
        trace.failed_views = [str(x) for x in failed]
    if rs.get("abandoned_roles"):
        trace.abandoned_roles = [str(x) for x in rs["abandoned_roles"]]
    if rs.get("done_node_keys"):
        trace.done_node_keys = [str(x) for x in rs["done_node_keys"]]
