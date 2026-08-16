"""Validation for structured ADS export TaskSpec contracts."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from app.services.export_task_spec import TaskSpec
_TASK_TYPES = {"light_identity", "multi_fact", "single_view"}


@dataclass(frozen=True)
class TaskSpecValidationIssue:
    code: str
    severity: str  # info | warning | clarification | error
    message: str
    field: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class TaskSpecValidationResult:
    status: str = "pass"  # pass | clarification | error
    issues: list[TaskSpecValidationIssue] = field(default_factory=list)
    repair_hints: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.status == "pass"

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "ok": self.ok,
            "issues": [i.to_dict() for i in self.issues],
            "repair_hints": list(self.repair_hints),
        }


def _severity_status(issues: list[TaskSpecValidationIssue]) -> str:
    if any(i.severity == "error" for i in issues):
        return "error"
    if any(i.severity == "clarification" for i in issues):
        return "clarification"
    return "pass"


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def validate_task_spec(spec: TaskSpec | dict[str, Any] | None) -> TaskSpecValidationResult:
    """Validate the contract before execution planning.

    The validator checks platform invariants only. Business semantics are
    resolved by the model from live MCP catalog and describe evidence.
    """

    issues: list[TaskSpecValidationIssue] = []
    hints: list[str] = []

    if spec is None:
        issues.append(TaskSpecValidationIssue(
            code="missing_task_spec",
            severity="error",
            message="缺少 TaskSpec，不能进入契约化执行。",
            field="task_spec",
        ))
        return TaskSpecValidationResult(status="error", issues=issues, repair_hints=[
            "先将用户需求解析为 TaskSpec。",
        ])

    data = spec.to_dict() if isinstance(spec, TaskSpec) else dict(spec or {})

    task_type = str(data.get("task_type") or "").strip()
    if task_type not in _TASK_TYPES:
        issues.append(TaskSpecValidationIssue(
            code="invalid_task_type",
            severity="error",
            message=f"未知 task_type：{task_type or '<empty>'}。",
            field="task_type",
        ))
        hints.append("task_type 必须是 light_identity、multi_fact 或 single_view。")

    tw = data.get("time_window") if isinstance(data.get("time_window"), dict) else {}
    start_ms = _as_int(tw.get("start_ms"))
    end_ms = _as_int(tw.get("end_ms"))
    if task_type != "single_view":
        if start_ms is None or end_ms is None:
            issues.append(TaskSpecValidationIssue(
                code="missing_time_window",
                severity="clarification",
                message="缺少可执行的毫秒时间窗。",
                field="time_window",
            ))
            hints.append("向用户确认时间范围，或从上一轮 export_trace 继承时间窗。")
        elif start_ms >= end_ms:
            issues.append(TaskSpecValidationIssue(
                code="invalid_time_window",
                severity="error",
                message="time_window.start_ms 必须小于 end_ms。",
                field="time_window",
            ))

    pinned_views = [str(v).strip() for v in data.get("pinned_views") or [] if str(v).strip()]
    if task_type == "single_view":
        if not pinned_views:
            issues.append(TaskSpecValidationIssue(
                code="missing_pinned_view",
                severity="clarification",
                message="single_view 任务必须指定 pinned view。",
                field="pinned_views",
            ))
            hints.append("让模型或用户明确要查询哪一个目录资源。")

    resources = [
        str(resource).strip()
        for resource in data.get("required_resources") or []
        if str(resource).strip()
    ]
    if task_type == "multi_fact" and not resources:
        issues.append(TaskSpecValidationIssue(
            code="unbound_resources",
            severity="info",
            message="尚未形成 resource 绑定；执行阶段会先通过 MCP list/describe 让模型完成绑定。",
            field="required_resources",
        ))

    columns = [str(c).strip() for c in data.get("requested_columns") or [] if str(c).strip()]
    if task_type != "single_view" and not columns:
        issues.append(TaskSpecValidationIssue(
            code="missing_requested_columns",
            severity="clarification",
            message="缺少 requested_columns，无法稳定生成列计划。",
            field="requested_columns",
        ))
        hints.append("先解析用户需要的输出列；若只是身份列表，至少包含用户ID/注册时间等基础列。")

    criteria = [str(c).strip() for c in data.get("acceptance_criteria") or [] if str(c).strip()]
    if not criteria:
        issues.append(TaskSpecValidationIssue(
            code="missing_acceptance_criteria",
            severity="clarification",
            message="缺少 acceptance_criteria，无法由 verifier 判断完成。",
            field="acceptance_criteria",
        ))
        hints.append("补充文件存在、sheet、核心 role、概况数字来源等验收标准。")

    return TaskSpecValidationResult(
        status=_severity_status(issues),
        issues=issues,
        repair_hints=hints,
    )


def format_task_spec_validation_reply(result: TaskSpecValidationResult) -> str:
    """User-facing soft-gate reply; do not expose prompts or internal traces."""
    if result.status == "pass":
        return ""
    title = (
        "我需要先确认几个信息，暂时不会开始拉数。"
        if result.status == "clarification"
        else "任务契约校验未通过，暂时不会执行导出。"
    )
    lines = [title]
    shown = result.issues[:4]
    if shown:
        lines.append("")
        lines.append("需要处理：")
        for issue in shown:
            lines.append(f"- {issue.message}")
    if result.repair_hints:
        lines.append("")
        lines.append("下一步：")
        for hint in result.repair_hints[:3]:
            lines.append(f"- {hint}")
    if result.status == "clarification":
        lines.append("")
        lines.append("请补充上述信息后，我再继续生成执行计划。")
    else:
        lines.append("")
        lines.append("我不会在契约不合法时宣称任务已完成。")
    return "\n".join(lines)
