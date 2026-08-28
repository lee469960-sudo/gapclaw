"""Stable CodeAgent terminal result presentation shared by API and UI."""

from __future__ import annotations

import json

from app.services.code_agent.failures import external_failure
from app.services.code_agent.output_security import redact_code_output


TERMINAL_PRESENTATION = {
    "patch_ready": ("Patch 已验证", "success"),
    "stale": ("基线已过期", "warning"),
    "no_change_justified": ("无需代码变更", "success"),
    "verification_inconclusive": ("验证不充分", "warning"),
    "baseline_broken": ("基线测试已失败", "warning"),
    "verification_failed": ("验证失败", "danger"),
    "budget_exhausted": ("预算已耗尽", "warning"),
    "no_progress": ("未取得有效进展", "warning"),
    "policy_rejected": ("策略拒绝", "danger"),
    "runtime_unavailable": ("Claude Code Runtime 不可用", "danger"),
    "model_unavailable": ("Claude Code 模型不可用", "danger"),
    "mcp_config_failed": ("Claude Code MCP 配置失败", "danger"),
    "skill_load_failed": ("Claude Code Skill 加载失败", "danger"),
    "coding_failed": ("Claude Code 编码失败", "danger"),
    "coding_timeout": ("Claude Code 编码超时", "warning"),
    "target_not_found": ("目标未找到", "warning"),
    "needs_user_decision": ("需要用户决策", "warning"),
    "workspace_integrity_error": ("Workspace 完整性失败", "danger"),
    "infrastructure_error": ("基础设施失败", "danger"),
    "cancelled": ("已取消", "info"),
    "timed_out": ("已超时", "warning"),
    "failed": ("执行失败", "danger"),
}


def _redact_runtime_value(value):
    if isinstance(value, dict):
        return {str(key): _redact_runtime_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact_runtime_value(item) for item in value]
    if isinstance(value, tuple):
        return [_redact_runtime_value(item) for item in value]
    if isinstance(value, str):
        return redact_code_output(value).text
    return value


def serialize_code_result(run, artifact=None) -> dict:
    try:
        report = json.loads(run.verifier_report or "{}")
    except json.JSONDecodeError:
        report = {}
    try:
        budget_usage = json.loads(getattr(run, "budget_usage", "") or "{}")
    except json.JSONDecodeError:
        budget_usage = {}
    try:
        runner_facts = json.loads(getattr(run, "runner_facts", "") or "{}")
    except json.JSONDecodeError:
        runner_facts = {}
    try:
        task_contract = json.loads(getattr(run, "task_contract", "") or "{}")
    except json.JSONDecodeError:
        task_contract = {}
    try:
        effective_policy = json.loads(getattr(run, "effective_policy", "") or "{}")
    except json.JSONDecodeError:
        effective_policy = {}
    if not isinstance(runner_facts, dict):
        runner_facts = {}
    if not isinstance(task_contract, dict):
        task_contract = {}
    if not isinstance(effective_policy, dict):
        effective_policy = {}
    coding_runtime = (
        task_contract.get("coding_runtime")
        or effective_policy.get("coding_runtime")
        or "legacy"
    )
    runtime_summary = _redact_runtime_value({
        "coding_runtime": str(coding_runtime),
        "preflight": runner_facts.get("claude_code_preflight") or {},
        "skills": (runner_facts.get("claude_code_skills") or {}).get("skills", []),
        "mcp_servers": (runner_facts.get("claude_code_mcp") or {}).get("servers", []),
        "runtime_result": runner_facts.get("claude_code_runtime") or {},
    })
    verified = report.get("passed") is True
    sealed = bool(artifact and artifact.status == "sealed")
    status = run.status
    if status == "patch_ready" and not (verified and sealed):
        status = "infrastructure_error"
    label, severity = TERMINAL_PRESENTATION.get(
        status,
        ("运行中" if status in {"pending", "running"} else "未知结果", "info"),
    )
    directly_adoptable = status == "patch_ready" and verified and sealed
    terminal = status not in {"pending", "running"}
    warning = ""
    if terminal and not directly_adoptable:
        warning = "该结果未形成完整的已验证 patch，不可直接采用。"
    failure = external_failure(run.failure_reason)
    return {
        "run_id": run.id,
        "project_id": run.project_id,
        "status": status,
        "label": label,
        "severity": severity,
        "terminal": terminal,
        "verified": verified,
        "sealed": sealed,
        "directly_adoptable": directly_adoptable,
        "warning": warning,
        "failure_reason": failure.get("reason", ""),
        "failure": failure,
        "manifest_id": getattr(run, "manifest_id", ""),
        "manifest_version": getattr(run, "manifest_version", 0),
        "verifier_report": report,
        "budget_usage": budget_usage,
        "runtime": runtime_summary,
        "artifact": ({
            "id": artifact.id,
            "base_commit": artifact.base_commit,
            "diff_hash": artifact.diff_hash,
            "policy_hash": artifact.policy_hash,
            "verifier_report_hash": artifact.verifier_report_hash,
            "image": artifact.image,
            "image_id": artifact.image_id,
        } if artifact else None),
    }
