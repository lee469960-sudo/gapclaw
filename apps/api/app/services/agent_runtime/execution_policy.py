"""Execution-mode policy for the Agent Runtime.

The policy is deliberately deterministic: ordinary conversation must not pay
an additional LLM call just to decide whether it is ordinary conversation.
Explicit operational intent wins over the chat fast path.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import re
from typing import Any


class ExecutionMode(StrEnum):
    CHAT = "chat"
    TASK = "task"
    TOOL = "tool"
    HUMAN_WAIT = "human_wait"


@dataclass(frozen=True)
class ExecutionPolicy:
    """Bounded budgets applied independently to each execution mode."""

    chat_max_turns: int = 1
    chat_max_retries: int = 1
    task_max_turns: int = 150
    tool_max_turns: int = 150
    no_progress_limit: int = 2
    human_loop_on_uncertainty: bool = True

    @classmethod
    def from_mapping(cls, values: dict[str, Any] | None = None) -> "ExecutionPolicy":
        """Build a safe policy from optional runtime configuration."""
        values = values or {}
        defaults = cls()
        ints = {
            "chat_max_turns": defaults.chat_max_turns,
            "chat_max_retries": defaults.chat_max_retries,
            "task_max_turns": defaults.task_max_turns,
            "tool_max_turns": defaults.tool_max_turns,
            "no_progress_limit": defaults.no_progress_limit,
        }
        normalized: dict[str, int | bool] = {}
        for name, fallback in ints.items():
            try:
                normalized[name] = max(0, int(values.get(name, fallback)))
            except (TypeError, ValueError):
                normalized[name] = fallback
        normalized["chat_max_turns"] = max(1, int(normalized["chat_max_turns"]))
        normalized["no_progress_limit"] = max(1, int(normalized["no_progress_limit"]))
        normalized["human_loop_on_uncertainty"] = bool(
            values.get("human_loop_on_uncertainty", defaults.human_loop_on_uncertainty)
        )
        return cls(**normalized)

    def max_turns(self, mode: ExecutionMode) -> int:
        if mode is ExecutionMode.CHAT:
            return self.chat_max_turns
        if mode is ExecutionMode.TOOL:
            return self.tool_max_turns
        return self.task_max_turns


# Explicit operations take precedence over the chat fallback. Keep this list
# intentionally small and user-oriented; it is not an MCP/tool allowlist.
_OPERATION_RE = re.compile(
    r"(?:\b(?:read|write|edit|patch|search|query|export|run|execute|deploy|rollback|inspect|call|invoke|use)\b"
    r"|读取|写入|修改|编辑|补丁|搜索|查询|导出|执行|运行|部署|回滚|检查|调用|使用|打开文件|查看文件|数据库(?:中|里|查询)|SQL|命令|脚本|代码)",
    re.IGNORECASE,
)
_MULTI_STEP_RE = re.compile(
    r"(?:\b(?:first|then|after that|step\s*\d+|implement|build|fix|create)\b"
    r"|首先|然后|接着|步骤|实现|构建|修复|创建|完成任务|多步骤)",
    re.IGNORECASE,
)
_HIGH_RISK_RE = re.compile(
    r"(?:\b(?:production|permission|delete|remove|release|publish)\b|生产|权限|删除|发布|上线)",
    re.IGNORECASE,
)
_HUMAN_CONFIRM_RE = re.compile(
    r"(?:"
    r"(?:delete|remove|drop|reset|grant|revoke|deploy|rollback|release|publish|上线|部署|发布|回滚|删除|移除|清空|重置).*(?:production|prod|线上|生产|数据库|数据|权限|密钥|密码)"
    r"|(?:production|prod|线上|生产).*(?:deploy|rollback|release|publish|上线|部署|发布|回滚)"
    r"|(?:权限|密钥|密码).*(?:修改|变更|授予|撤销|grant|revoke|change)"
    r")",
    re.IGNORECASE,
)


def classify_request(user_message: str, *, explicit_mode: str | None = None) -> ExecutionMode:
    """Classify one user request without making an LLM call.

    Explicit mode metadata is honored first. Otherwise operational intent maps
    to task/tool mode and all remaining requests use the chat fast path.
    """
    requested = str(explicit_mode or "").strip().lower()
    if requested in {mode.value for mode in ExecutionMode}:
        return ExecutionMode(requested)
    text = str(user_message or "").strip()
    if not text:
        return ExecutionMode.CHAT
    if _HUMAN_CONFIRM_RE.search(text):
        return ExecutionMode.HUMAN_WAIT
    if _OPERATION_RE.search(text):
        return ExecutionMode.TASK
    if _MULTI_STEP_RE.search(text):
        return ExecutionMode.TASK
    return ExecutionMode.CHAT


def requires_human_wait(user_message: str, metadata: dict[str, Any] | None = None) -> bool:
    """Return whether a request has an explicit uncertainty/confirmation signal."""
    metadata = metadata or {}
    if bool(metadata.get("requires_human_confirmation") or metadata.get("uncertain")):
        return True
    return bool(_HUMAN_CONFIRM_RE.search(str(user_message or "")))


def policy_for_context(ctx: Any) -> ExecutionPolicy:
    """Resolve policy overrides from optional context metadata."""
    meta = getattr(ctx, "message_meta", None) or {}
    values = meta.get("execution_policy") if isinstance(meta, dict) else None
    return ExecutionPolicy.from_mapping(values if isinstance(values, dict) else None)
