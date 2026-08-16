"""Structured TaskSpec for ADS export (engine-driven, not prose PLAN)."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

@dataclass
class CohortSpec:
    """Who is in the export population."""

    type: str = "custom"
    view: str = ""
    time_field: str = ""
    filters: dict[str, Any] = field(default_factory=dict)
    # Explicit: crowd vs metric time must not be mixed in docs/SQL
    note: str = ""


@dataclass
class TimeWindowSpec:
    timezone: str = ""
    start_ms: int | None = None
    end_ms: int | None = None
    label: str = ""
    cohort: str = ""


@dataclass
class TaskSpec:
    task_type: str = "multi_fact"  # light_identity | multi_fact | single_view
    cohort: CohortSpec = field(default_factory=CohortSpec)
    time_window: TimeWindowSpec = field(default_factory=TimeWindowSpec)
    requested_columns: list[str] = field(default_factory=list)
    required_resources: list[str] = field(default_factory=list)
    pinned_views: list[str] = field(default_factory=list)
    filters: dict[str, Any] = field(default_factory=dict)
    output_format: str = "xlsx"
    acceptance_criteria: list[str] = field(default_factory=list)
    ambiguities: list[str] = field(default_factory=list)
    clarification_needed: bool = False
    source_brief: str = ""

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        return d


def build_task_spec(
    *,
    task_type: str = "multi_fact",
    headers: list[str] | None = None,
    time_window: dict | None = None,
    resources: list[str] | None = None,
    pinned_views: list[str] | None = None,
    source_brief: str = "",
) -> TaskSpec:
    """Build TaskSpec from resolved export context (post policy / column parse)."""
    tw = time_window if isinstance(time_window, dict) else {}
    cols = [str(h).strip() for h in (headers or []) if str(h).strip()]
    cohort_kind = str(tw.get("cohort") or "").strip().lower()
    cohort = CohortSpec(
        type=cohort_kind or "custom",
        view=(pinned_views or [""])[0] if task_type == "single_view" and pinned_views else "",
        time_field="",
        note="资源与时间字段由本轮 MCP describe 证据和 QueryContract 确定",
    )

    req_resources = list(dict.fromkeys(
        str(resource).strip()
        for resource in (resources or [])
        if str(resource).strip()
    ))

    acceptance = [
        "交付 xlsx 存在且非空",
        "含数据 sheet 与口径说明 sheet（或兼容命名）",
        "每个已绑定 resource 的查询结果均可追溯",
        "FINAL 概况数字来自 verifier / 文件复算",
    ]
    if task_type == "multi_fact":
        acceptance.append("每个请求列均有绑定资源或明确标记未完整")

    return TaskSpec(
        task_type=task_type or "multi_fact",
        cohort=cohort,
        time_window=TimeWindowSpec(
            timezone=str(tw.get("tz_label") or tw.get("timezone") or ""),
            start_ms=tw.get("start_ms"),
            end_ms=tw.get("end_ms"),
            label=str(tw.get("label") or ""),
            cohort=str(tw.get("cohort") or cohort.type),
        ),
        requested_columns=cols,
        required_resources=req_resources,
        pinned_views=[str(v).strip() for v in (pinned_views or []) if str(v).strip()],
        acceptance_criteria=acceptance,
        source_brief=(source_brief or "")[:2000],
    )
