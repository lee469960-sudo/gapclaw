"""Stable CodeAgent failure taxonomy, external mapping and sanitized audit events."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from app.models import CodeControlAudit
from app.security import new_id, now_str
from app.services.code_agent.output_security import redact_code_output


class FailureStage(StrEnum):
    AUTHORIZATION = "authorization"
    SOURCE = "source"
    AUTH = "auth"
    REF = "ref"
    SNAPSHOT = "snapshot"
    IMAGE = "image"
    MOUNT = "mount"
    SCAN = "scan"
    RUNNER = "runner"
    RUNTIME = "runtime"
    MODEL = "model"
    MCP = "mcp"
    SKILL = "skill"
    RESOURCE = "resource"
    CODING = "coding"
    VERIFIER = "verifier"
    CLEANUP = "cleanup"
    INTERNAL = "internal"


class FailureReason(StrEnum):
    AUTHORIZATION_DENIED = "authorization_denied"
    SOURCE_NOT_ALLOWED = "source_not_allowed"
    REPOSITORY_NETWORK_POLICY_DENIED = "repository_network_policy_denied"
    SOURCE_UNREACHABLE = "source_unreachable"
    SOURCE_LIMIT_EXCEEDED = "source_limit_exceeded"
    REPOSITORY_FEATURE_UNSUPPORTED = "repository_feature_unsupported"
    REPOSITORY_AUTH_FAILED = "repository_auth_failed"
    REPOSITORY_REF_INVALID = "repository_ref_invalid"
    SNAPSHOT_INVALID = "snapshot_invalid"
    IMAGE_DIGEST_INVALID = "image_digest_invalid"
    WORKSPACE_MOUNT_INVALID = "workspace_mount_invalid"
    WORKSPACE_INTEGRITY_ERROR = "workspace_integrity_error"
    SOURCE_SCAN_FAILED = "source_scan_failed"
    PATCH_SCAN_FAILED = "patch_scan_failed"
    SECRET_DETECTED = "secret_detected"
    RUNNER_POLICY_INVALID = "runner_policy_invalid"
    RUNNER_UNAVAILABLE = "runner_unavailable"
    RUNTIME_UNAVAILABLE = "runtime_unavailable"
    MODEL_UNAVAILABLE = "model_unavailable"
    MCP_CONFIG_FAILED = "mcp_config_failed"
    SKILL_LOAD_FAILED = "skill_load_failed"
    CODING_FAILED = "coding_failed"
    CODING_TIMEOUT = "coding_timeout"
    TARGET_NOT_FOUND = "target_not_found"
    NEEDS_USER_DECISION = "needs_user_decision"
    RESOURCE_LIMIT_EXCEEDED = "resource_limit_exceeded"
    BUDGET_EXHAUSTED = "budget_exhausted"
    VERIFIER_FAILED = "verifier_failed"
    SANDBOX_CLEANUP_FAILED = "sandbox_cleanup_failed"
    INFRASTRUCTURE_ERROR = "infrastructure_error"


@dataclass(frozen=True)
class FailureDescriptor:
    reason: FailureReason
    stage: FailureStage
    detail: str

    def to_external_dict(self) -> dict[str, str]:
        return {
            "reason": self.reason.value,
            "stage": self.stage.value,
            "detail": self.detail,
        }


_FAILURES = {
    FailureReason.AUTHORIZATION_DENIED: (
        FailureStage.AUTHORIZATION,
        "当前主体无权执行该 CodeAgent 操作。",
    ),
    FailureReason.SOURCE_NOT_ALLOWED: (
        FailureStage.SOURCE,
        "仓库来源未被平台策略批准，请检查来源配置。",
    ),
    FailureReason.REPOSITORY_NETWORK_POLICY_DENIED: (
        FailureStage.SOURCE,
        "仓库连接目标未通过网络安全策略，请联系管理员检查来源配置。",
    ),
    FailureReason.SOURCE_UNREACHABLE: (
        FailureStage.SOURCE,
        "仓库来源当前不可达，请稍后重试或联系管理员。",
    ),
    FailureReason.SOURCE_LIMIT_EXCEEDED: (
        FailureStage.SOURCE,
        "仓库超过导入限制，请缩小仓库或调整批准的限制。",
    ),
    FailureReason.REPOSITORY_FEATURE_UNSUPPORTED: (
        FailureStage.SOURCE,
        "仓库使用了当前安全导入流程不支持的特性，请移除后重试。",
    ),
    FailureReason.REPOSITORY_AUTH_FAILED: (
        FailureStage.AUTH,
        "仓库认证失败，请检查凭据引用是否有效且已授权。",
    ),
    FailureReason.REPOSITORY_REF_INVALID: (
        FailureStage.REF,
        "仓库 ref 无法解析，请检查 branch、tag 或 commit。",
    ),
    FailureReason.SNAPSHOT_INVALID: (
        FailureStage.SNAPSHOT,
        "源码快照不可用，请重新导入并发布 Manifest。",
    ),
    FailureReason.IMAGE_DIGEST_INVALID: (
        FailureStage.IMAGE,
        "Runner 镜像 digest 未获批准或已发生漂移。",
    ),
    FailureReason.WORKSPACE_MOUNT_INVALID: (
        FailureStage.MOUNT,
        "Workspace 挂载未就绪，请检查部署路径映射。",
    ),
    FailureReason.WORKSPACE_INTEGRITY_ERROR: (
        FailureStage.MOUNT,
        "Workspace 完整性校验失败，运行已停止。",
    ),
    FailureReason.SOURCE_SCAN_FAILED: (
        FailureStage.SCAN,
        "源码安全扫描未完整通过，不能启动 Runner。",
    ),
    FailureReason.PATCH_SCAN_FAILED: (
        FailureStage.SCAN,
        "变更安全扫描未完整通过，不能交付 Patch。",
    ),
    FailureReason.SECRET_DETECTED: (
        FailureStage.SCAN,
        "检测到疑似秘密内容，运行结果已被阻止。",
    ),
    FailureReason.RUNNER_POLICY_INVALID: (
        FailureStage.RUNNER,
        "Runner 安全策略校验失败，容器未启动。",
    ),
    FailureReason.RUNNER_UNAVAILABLE: (
        FailureStage.RUNNER,
        "Runner 当前不可用，请检查运行环境。",
    ),
    FailureReason.RUNTIME_UNAVAILABLE: (
        FailureStage.RUNTIME,
        "Claude Code runtime 不可用，请检查 runner 镜像和 CLI 预检。",
    ),
    FailureReason.MODEL_UNAVAILABLE: (
        FailureStage.MODEL,
        "Claude Code 模型不可用，请检查 Cloud Claude 模型配置和凭证。",
    ),
    FailureReason.MCP_CONFIG_FAILED: (
        FailureStage.MCP,
        "Claude Code MCP 配置无效，请检查已授权 MCP 配置。",
    ),
    FailureReason.SKILL_LOAD_FAILED: (
        FailureStage.SKILL,
        "Claude Code Skill 加载失败，请检查 Agent 绑定和 Skill 资源。",
    ),
    FailureReason.CODING_FAILED: (
        FailureStage.CODING,
        "Claude Code 编码阶段失败，请查看脱敏 runtime 事件和摘要。",
    ),
    FailureReason.CODING_TIMEOUT: (
        FailureStage.CODING,
        "Claude Code 编码阶段超时，运行已停止。",
    ),
    FailureReason.TARGET_NOT_FOUND: (
        FailureStage.CODING,
        "任务目标在允许范围内未找到，未生成 Patch。",
    ),
    FailureReason.NEEDS_USER_DECISION: (
        FailureStage.CODING,
        "存在多个候选或范围不清，需要用户明确选择后再执行。",
    ),
    FailureReason.RESOURCE_LIMIT_EXCEEDED: (
        FailureStage.RESOURCE,
        "运行达到资源限制，请缩小任务或调整批准预算。",
    ),
    FailureReason.BUDGET_EXHAUSTED: (
        FailureStage.RESOURCE,
        "任务预算已耗尽，运行已停止。",
    ),
    FailureReason.VERIFIER_FAILED: (
        FailureStage.VERIFIER,
        "验证未通过，请检查测试与 Verifier 结果。",
    ),
    FailureReason.SANDBOX_CLEANUP_FAILED: (
        FailureStage.CLEANUP,
        "Sandbox 清理未完成，系统将重试并告警。",
    ),
    FailureReason.INFRASTRUCTURE_ERROR: (
        FailureStage.INTERNAL,
        "CodeAgent 基础设施异常，请稍后重试或联系管理员。",
    ),
}


_ALIASES = {
    "repository_source_not_allowed": FailureReason.SOURCE_NOT_ALLOWED,
    "repository_unreachable": FailureReason.SOURCE_UNREACHABLE,
    "repository_limit_exceeded": FailureReason.SOURCE_LIMIT_EXCEEDED,
    "ref_unavailable": FailureReason.REPOSITORY_REF_INVALID,
    "snapshot_hash_mismatch": FailureReason.SNAPSHOT_INVALID,
    "image_digest_mismatch": FailureReason.IMAGE_DIGEST_INVALID,
    "workspace_integrity_error": FailureReason.WORKSPACE_INTEGRITY_ERROR,
    "secret_scan_failed": FailureReason.PATCH_SCAN_FAILED,
    "secret_scan_truncated": FailureReason.PATCH_SCAN_FAILED,
    "runner_start_failed": FailureReason.RUNNER_UNAVAILABLE,
    "startup_failed": FailureReason.RUNNER_UNAVAILABLE,
    "runner_claude_code_unavailable": FailureReason.RUNTIME_UNAVAILABLE,
    "claude_code_cli_unavailable": FailureReason.RUNTIME_UNAVAILABLE,
    "claude_code_config_dir_unwritable": FailureReason.RUNTIME_UNAVAILABLE,
    "claude_code_repository_cwd_unwritable": FailureReason.RUNTIME_UNAVAILABLE,
    "claude_code_model_unavailable": FailureReason.MODEL_UNAVAILABLE,
    "llm_not_configured": FailureReason.MODEL_UNAVAILABLE,
    "llm_not_found": FailureReason.MODEL_UNAVAILABLE,
    "llm_provider_not_supported": FailureReason.MODEL_UNAVAILABLE,
    "llm_group_not_supported": FailureReason.MODEL_UNAVAILABLE,
    "llm_api_key_missing": FailureReason.MODEL_UNAVAILABLE,
    "llm_model_missing": FailureReason.MODEL_UNAVAILABLE,
    "claude_code_mcp_config_invalid": FailureReason.MCP_CONFIG_FAILED,
    "claude_code_skill_load_failed": FailureReason.SKILL_LOAD_FAILED,
    "claude_code_coding_failed": FailureReason.CODING_FAILED,
    "claude_code_coding_timeout": FailureReason.CODING_TIMEOUT,
    "claude_code_budget_watchdog_unavailable": FailureReason.INFRASTRUCTURE_ERROR,
    "claude_code_budget_exhausted": FailureReason.BUDGET_EXHAUSTED,
    "runner_command_timed_out": FailureReason.RESOURCE_LIMIT_EXCEEDED,
    "runner_oom_killed": FailureReason.RESOURCE_LIMIT_EXCEEDED,
    "runner_resource_limit": FailureReason.RESOURCE_LIMIT_EXCEEDED,
    "project_concurrency_limit": FailureReason.RESOURCE_LIMIT_EXCEEDED,
    "tool_call_budget_exhausted": FailureReason.BUDGET_EXHAUSTED,
    "iteration_budget_exhausted": FailureReason.BUDGET_EXHAUSTED,
    "verification_failed": FailureReason.VERIFIER_FAILED,
    "verification_baseline_unavailable": FailureReason.VERIFIER_FAILED,
    "baseline_unavailable": FailureReason.VERIFIER_FAILED,
    "cleanup_failed": FailureReason.SANDBOX_CLEANUP_FAILED,
}

_ALIAS_DETAILS = {
    "llm_not_configured": "Claude Code 需要 Agent 绑定单个可用 LLMResource。",
    "llm_not_found": "Agent 绑定的 Claude Code LLMResource 不存在，请重新选择模型。",
    "llm_group_not_supported": "Claude Code 不支持 LLM Group，请为 CodeAgent 绑定单个可用 LLMResource。",
    "llm_api_key_missing": "Claude Code LLMResource 缺少可用 API key，请检查模型凭证。",
    "llm_model_missing": "Claude Code LLMResource 缺少 model，请检查模型配置。",
}


def failure_descriptor(reason: FailureReason | str) -> FailureDescriptor:
    try:
        normalized = reason if isinstance(reason, FailureReason) else FailureReason(reason)
    except ValueError:
        normalized = _ALIASES.get(str(reason), FailureReason.INFRASTRUCTURE_ERROR)
    stage, detail = _FAILURES[normalized]
    return FailureDescriptor(normalized, stage, detail)


def external_failure(reason: FailureReason | str | None) -> dict[str, str]:
    if not reason:
        return {}
    payload = failure_descriptor(reason).to_external_dict()
    alias_detail = _ALIAS_DETAILS.get(str(reason))
    if alias_detail:
        payload["detail"] = alias_detail
        payload["internal_reason"] = str(reason)
    return payload


_SAFE_DETAIL_KEYS = frozenset({
    "operation",
    "resource_type",
    "scope",
    "status",
    "outcome",
    "reference_id",
    "source_id",
    "snapshot_id",
    "manifest_id",
    "manifest_version",
    "image_digest",
    "image",
    "error_type",
    "error_summary",
    "tool",
    "file_count",
    "byte_count",
    "finding_count",
    "retry_count",
    "cleanup_state",
})
_SAFE_ID = re.compile(r"^[A-Za-z0-9_.:@/+-]{1,256}$")
_UNSAFE_SUMMARY_CHAR = re.compile(r"[^A-Za-z0-9_.,:@/+=()\[\] \-]")
_HASH = re.compile(r"^[a-fA-F0-9]{64}$")


def _safe_text(value: Any, *, fallback: str = "") -> str:
    text = str(value or "")[:256]
    redacted = redact_code_output(text).text
    if "[REDACTED:SECRET]" in redacted or not _SAFE_ID.fullmatch(redacted):
        return fallback
    return redacted


def _safe_summary(value: Any) -> str:
    text = str(value or "")[:300]
    redacted = redact_code_output(text)
    summary = redacted.text.replace("[REDACTED:SECRET]", "[redacted]")
    summary = "".join(ch for ch in summary if ch.isprintable())
    summary = _UNSAFE_SUMMARY_CHAR.sub(" ", summary)
    summary = re.sub(r"\s+", " ", summary).strip()
    return summary[:300]


def sanitized_audit_details(details: dict[str, Any] | None) -> dict[str, Any]:
    sanitized: dict[str, Any] = {}
    for key, value in (details or {}).items():
        if key not in _SAFE_DETAIL_KEYS:
            continue
        if isinstance(value, bool):
            sanitized[key] = value
        elif isinstance(value, int) and value >= 0:
            sanitized[key] = value
        elif isinstance(value, str):
            safe = _safe_summary(value) if key == "error_summary" else _safe_text(value)
            if safe:
                sanitized[key] = safe
    return sanitized


def record_code_failure(
    db,
    *,
    actor: str,
    reason: FailureReason | str,
    project_id: str = "",
    run_id: str = "",
    policy_hash: str = "",
    trace_id: str = "",
    details: dict[str, Any] | None = None,
    commit: bool = True,
) -> CodeControlAudit:
    descriptor = failure_descriptor(reason)
    safe_trace_id = _safe_text(trace_id) or new_id()
    payload = {
        "stage": descriptor.stage.value,
        "reason": descriptor.reason.value,
        "trace_id": safe_trace_id,
        "run_id": _safe_text(run_id),
        "policy_hash": policy_hash.lower() if _HASH.fullmatch(policy_hash or "") else "",
        "facts": sanitized_audit_details(details),
    }
    event = CodeControlAudit(
        id=new_id(),
        actor=_safe_text(actor, fallback="anonymous"),
        action="security_failure",
        project_id=_safe_text(project_id),
        details=json.dumps(payload, sort_keys=True),
        created_at=now_str(),
    )
    db.add(event)
    if commit:
        db.commit()
    return event
