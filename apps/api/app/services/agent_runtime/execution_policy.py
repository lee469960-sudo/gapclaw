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


class ResponseStyle(StrEnum):
    """User-facing style for the tool-free conversational path."""

    ADAPTIVE = "adaptive"
    CONCISE = "concise"
    STRUCTURED = "structured"
    ANALYTICAL = "analytical"


VALID_RESPONSE_STYLES = frozenset(item.value for item in ResponseStyle)


def normalize_response_style(value: Any) -> str:
    """Normalize a persisted/API style without ever blocking a conversation."""
    normalized = str(value or "").strip().lower()
    return normalized if normalized in VALID_RESPONSE_STYLES else ResponseStyle.ADAPTIVE.value


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
    r"|读取|写入|修改|编辑|补丁|搜索|查询|导出|执行|运行|部署|回滚|检查|调用|使用|选股|筛选|过滤|打开文件|查看文件|数据库(?:中|里|查询)|SQL|命令|脚本|代码|SKILL_MD|RUN_SKILL|SKILL包)",
    re.IGNORECASE,
)
_MULTI_STEP_RE = re.compile(
    r"(?:\b(?:first|then|after that|step\s*\d+|implement|build|fix|create)\b"
    r"|首先|然后|接着|步骤|实现|构建|修复|创建|生成|制作|打包|产出|编写|完成任务|多步骤)",
    re.IGNORECASE,
)
_COMPLEX_CHAT_RE = re.compile(
    r"(?:总结|归纳|拆解|分析|梳理|对比|提取|整理|分组|概括|评估|复盘|规划|列出要点"
    r"|summari[sz]e|break\s*down|analy[sz]e|compare|extract|organize|outline|evaluate)"
    r"|(?:来源|日期|主题|笔记|多个|多项|分别|至少|条件如下|要求如下|并且|同时|以及)"
    r"|(?:\n\s*\d+[.、)]|\b(?:and|with|including)\b)",
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


def _routing_instruction_text(value: str) -> str:
    """Remove fenced payloads before safety classification.

    Skill/document generation requests often include policy examples mentioning
    production, permissions, or deletion. Those examples are content to write,
    not the operation the user is asking the agent to perform.
    """
    text = re.sub(r"```[\s\S]*?```|~~~[\s\S]*?~~~", " ", str(value or ""))
    # Some clients send Markdown payloads without their fences. Only separate
    # such a body when the surrounding request explicitly generates a Skill.
    # Keep a mixed request such as "create a Skill then deploy to production"
    # intact so its real operation is still checked.
    if _SKILL_ARTIFACT_RE.search(text):
        heading = re.search(r"(?m)^\s*#{1,6}\s+", text)
        if heading:
            text = text[:heading.start()]
    return text


_SKILL_ARTIFACT_RE = re.compile(
    r"(?:创建|制作|生成|打包|编写).{0,40}(?:skill|技能)(?:包|文件|目录)?",
    re.IGNORECASE | re.DOTALL,
)

_RESOURCE_AFTER_VERB_RE = re.compile(
    r"(?:使用|调用|通过|use|call|via)\s*[`\"']?([A-Za-z][A-Za-z0-9._-]{2,})",
    re.IGNORECASE,
)
_RESOURCE_SUFFIX_RE = re.compile(
    r"\b([A-Za-z][A-Za-z0-9]*(?:-[A-Za-z0-9]+)+)\b",
    re.IGNORECASE,
)
_RESOURCE_SUFFIXES = ("mcp", "trader", "hub", "docs", "api")
_NON_RESOURCE_MENTIONS = frozenset({
    "read", "write", "shell", "skill_md", "run_skill", "skill包",
    "dev", "development", "test", "testing", "stage", "staging", "prod", "production",
})
_CAPABILITY_STOPWORDS = frozenset({
    "当前", "现在", "信息", "情况", "内容", "数据", "查询", "查看", "看看", "帮我",
    "请问", "一下", "这个", "那个", "the", "current", "please", "query", "show",
})
_CAPABILITY_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9_-]{2,}|[\u4e00-\u9fff]{2,}")
_CAPABILITY_OPERATION_RE = re.compile(
    r"(?:帮我|请|想要|需要|看看|查看|查询|获取|列出|显示|检查|读取|使用|调用|通过|选股|筛选|筛选出|过滤|选出|查一下|看一下|show|check|fetch|list|use|call)",
    re.IGNORECASE,
)
_STRUCTURED_OPERATION_RE = re.compile(
    r"(?:条件(?:如下|是|为)?\s*[:：]|基准日|至少|不选|筛选条件|criteria|where\b|\n\s*\d+[.、)])",
    re.IGNORECASE,
)
_CAPABILITY_KNOWLEDGE_RE = re.compile(
    r"(?:什么是|是什么|解释|介绍|定义|含义|原理|区别|为什么)",
    re.IGNORECASE,
)


def extract_named_resource_mentions(user_message: str) -> list[str]:
    """Extract explicit, tool-like resource names without a resource allowlist."""
    text = _routing_instruction_text(user_message)
    names: list[str] = []
    for match in _RESOURCE_AFTER_VERB_RE.finditer(text):
        value = match.group(1).strip("`\"'.,;:!?，。；：！？")
        if value and value.casefold() not in _NON_RESOURCE_MENTIONS and value.lower() not in {item.lower() for item in names}:
            names.append(value)
    for match in _RESOURCE_SUFFIX_RE.finditer(text):
        value = match.group(1)
        if any(value.lower().endswith(suffix) for suffix in _RESOURCE_SUFFIXES):
            if value.lower() not in {item.lower() for item in names}:
                names.append(value)
    return names


def _capability_tokens(value: str) -> set[str]:
    tokens: set[str] = set()
    for token in _CAPABILITY_TOKEN_RE.findall(str(value or "")):
        normalized = token.strip().lower()
        if len(normalized) >= 2 and normalized not in _CAPABILITY_STOPWORDS:
            tokens.add(normalized)
    return tokens


def _matches_bound_capability(user_message: str, capability_hints: list[dict[str, Any]] | None) -> bool:
    request_tokens = _capability_tokens(user_message)
    if not request_tokens:
        return False
    is_explicit_operation = bool(_CAPABILITY_OPERATION_RE.search(str(user_message or "")))
    is_structured_operation = bool(_STRUCTURED_OPERATION_RE.search(str(user_message or "")))
    is_knowledge_question = bool(_CAPABILITY_KNOWLEDGE_RE.search(str(user_message or "")))
    for hint in capability_hints or []:
        if not isinstance(hint, dict):
            continue
        name = str(hint.get("name") or "").strip()
        if name and name.lower() in str(user_message or "").lower():
            return True
        capability_tokens = _capability_tokens(
            " ".join(str(hint.get(key) or "") for key in ("name", "tags", "description"))
        )
        overlaps = {
            (request_token, capability_token)
            for request_token in request_tokens
            for capability_token in capability_tokens
            if request_token in capability_token or capability_token in request_token
        }
        # Explicit operational language needs one capability match; without it
        # require two independent metadata matches. This lets semantic requests
        # such as “沪深300 中最近涨停的股票” route dynamically while keeping a
        # single-word knowledge question on the chat fast path.
        if is_knowledge_question:
            continue
        if overlaps and (is_explicit_operation or len(overlaps) >= 2):
            return True
        # A structured external request can use domain wording absent from a
        # short MCP description. Keep it out of the chat fast path when it has
        # an operation verb; the LLM router still selects the actual MCP.
        if is_structured_operation and is_explicit_operation:
            return True
    return False


def classify_request(
    user_message: str,
    *,
    explicit_mode: str | None = None,
    capability_hints: list[dict[str, Any]] | None = None,
    skill_names: list[str] | None = None,
) -> ExecutionMode:
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
    if requires_human_wait(text):
        return ExecutionMode.HUMAN_WAIT
    if _OPERATION_RE.search(text):
        return ExecutionMode.TASK
    if _MULTI_STEP_RE.search(text):
        return ExecutionMode.TASK
    # A bound Skill named by the user is an explicit capability request. This
    # must not fall through to the conversation fast path, otherwise the Skill
    # markdown/tools are never injected into the model context.
    normalized_text = text.casefold()
    if any(name.strip() and name.strip().casefold() in normalized_text for name in (skill_names or [])):
        return ExecutionMode.TASK
    if extract_named_resource_mentions(text) or _matches_bound_capability(text, capability_hints):
        return ExecutionMode.TASK
    return ExecutionMode.CHAT


def is_complex_conversation(user_message: str, *, response_style: str = "adaptive") -> bool:
    """Return whether a chat request deserves the bounded quality gate.

    This is deliberately deterministic and does not inspect or connect tools.
    Structured/analytical styles opt complex-looking requests into the gate,
    while concise style never forces a long response for simple chat.
    """
    text = str(user_message or "").strip()
    if not text:
        return False
    style = normalize_response_style(response_style)
    if _COMPLEX_CHAT_RE.search(text):
        return True
    # Multiple independent clauses/requirements are complex even without a
    # particular keyword; avoid treating short greetings as such.
    clauses = len(re.findall(r"[，,；;。]|\band\b|\bwith\b", text, re.IGNORECASE))
    return len(text) >= 120 or clauses >= 3 or style in {
        ResponseStyle.STRUCTURED.value,
        ResponseStyle.ANALYTICAL.value,
    } and len(text) >= 40


def requires_human_wait(user_message: str, metadata: dict[str, Any] | None = None) -> bool:
    """Return whether a request has an explicit uncertainty/confirmation signal."""
    metadata = metadata or {}
    if bool(metadata.get("requires_human_confirmation") or metadata.get("uncertain")):
        return True
    instruction = _routing_instruction_text(user_message)
    return bool(_HUMAN_CONFIRM_RE.search(instruction))


def policy_for_context(ctx: Any) -> ExecutionPolicy:
    """Resolve policy overrides from optional context metadata."""
    meta = getattr(ctx, "message_meta", None) or {}
    values = meta.get("execution_policy") if isinstance(meta, dict) else None
    return ExecutionPolicy.from_mapping(values if isinstance(values, dict) else None)
