import asyncio
import json
import logging
import random
import re
from collections.abc import Callable
from dataclasses import dataclass, field

import httpx
from sqlalchemy.orm import Session

from app.models import LLMResource
from app.security import decrypt_secret, is_masked_secret
from app.services.agent_runtime.hub import ChatStopped
from app.services.tool_parser import ToolStep, strip_leaked_tool_tokens


logger = logging.getLogger(__name__)


def normalize_openai_base_url(base_url: str, provider: str = "openai") -> str:
    """Normalize base URL for OpenAI-compatible /chat/completions."""
    url = (base_url or "").strip().rstrip("/")
    if not url:
        return url
    if url.endswith("/chat/completions"):
        url = url[: -len("/chat/completions")].rstrip("/")

    prov = (provider or "openai").lower()
    if prov not in ("openai", "minimax"):
        return url

    # MiniMax: must use /v1, not /anthropic
    lower = url.lower()
    if "minimax" in lower:
        url = re.sub(r"/anthropic/?$", "/v1", url, flags=re.IGNORECASE)
        url = re.sub(r"/anthropic/", "/v1/", url, flags=re.IGNORECASE)
        if not re.search(r"/v\d+$", url):
            url = f"{url}/v1"
    return url


def openai_chat_completions_url(base_url: str, provider: str = "openai") -> str:
    base = normalize_openai_base_url(base_url, provider)
    if not base:
        return ""
    return f"{base}/chat/completions"


def is_anthropic_provider(provider: str = "") -> bool:
    return (provider or "").strip().lower() in {"anthropic", "cloud_claude"}


def anthropic_base_url(base_url: str) -> str:
    """Normalize an Anthropic resource URL to the SDK-compatible base URL."""
    url = (base_url or "").strip().rstrip("/")
    if not url:
        return ""
    for suffix in ("/chat/completions", "/v1/messages", "/messages", "/v1"):
        if url.lower().endswith(suffix):
            url = url[: -len(suffix)].rstrip("/")
            break
    return url


def anthropic_messages_url(base_url: str) -> str:
    """Build the native Anthropic Messages endpoint from a resource base URL."""
    url = anthropic_base_url(base_url)
    if not url:
        return ""
    return f"{url}/v1/messages"


def _anthropic_content_text(data: dict) -> str:
    content = data.get("content") if isinstance(data, dict) else None
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            str(item.get("text") or "")
            for item in content
            if isinstance(item, dict) and item.get("type") == "text" and item.get("text")
        )
    return ""


def is_minimax_llm(llm) -> bool:
    """True when the LLM resource points at a MiniMax endpoint (M3 reasoning format)."""
    return "minimax" in (getattr(llm, "base_url", "") or "").lower()


def normalize_chat_messages(messages: list[dict]) -> list[dict]:
    """Prepare messages for strict OpenAI-compatible providers (e.g. MiniMax).

    - Map developer -> system
    - Coalesce consecutive system messages into one
    - Ensure content is a string
    - Pass through native pairing: assistant ``tool_calls`` and tool ``tool_call_id``
    """
    out: list[dict] = []
    for raw in messages or []:
        if not isinstance(raw, dict):
            continue
        role = str(raw.get("role") or "user").strip().lower()
        if role == "developer":
            role = "system"
        content = raw.get("content")
        if content is None:
            content = ""
        elif not isinstance(content, str):
            content = json.dumps(content, ensure_ascii=False)
        if out and role == "system" and out[-1].get("role") == "system":
            prev = out[-1]["content"]
            merged = f"{prev}\n\n{content}" if prev and content else (prev or content)
            out[-1] = {"role": "system", "content": merged}
            continue
        item = {"role": role, "content": content}
        if role == "assistant" and raw.get("tool_calls"):
            item["tool_calls"] = raw["tool_calls"]
        elif role == "tool" and raw.get("tool_call_id"):
            item["tool_call_id"] = raw["tool_call_id"]
        out.append(item)
    return out


def _tool_arg(args_obj, *keys, default=""):
    """Return the first non-empty value among keys in a tool_call arguments object."""
    if isinstance(args_obj, dict):
        for k in keys:
            v = args_obj.get(k)
            if v not in (None, ""):
                return v
    return default


def _extract_content_text(message: dict) -> str:
    """Return the assistant message content as a string (stripped of leaked tokens)."""
    content = message.get("content")
    content_text = ""
    if isinstance(content, str) and content.strip():
        content_text = content
    elif isinstance(content, list):
        parts = [
            str(item.get("text") or "")
            for item in content
            if isinstance(item, dict) and item.get("text")
        ]
        if parts:
            content_text = "\n".join(parts)
    return strip_leaked_tool_tokens(content_text)


class LLMTransportError(RuntimeError):
    """Transport-level LLM failure (environmental / self-healing: DNS jitter,
    proxy restart, transient unreachable). Distinguished from HTTP 4xx so the run
    loop can apply a separate exit threshold (react-engine-v6 R3).
    """


class LLMHTTPError(RuntimeError):
    """HTTP-status LLM failure (e.g. 400 — parameter/request error, needs fixing)."""


class LLMGroupCycleError(RuntimeError):
    """模型组成员解析成环或嵌套过深（react-engine-v11 R2）。

    RuntimeError 子类，仅用于让 group 递归循环区分「环 / 超深」与「成员真实失败」：
    命中时直接上抛、不被 `except Exception` 吞掉再叠加「模型组全部失败」前缀。
    """


# group 成员解析的最大嵌套层数（react-engine-v11 R2）；8 已远超实际用途，
# 又挡住无限递归。成员为叶子（R1 禁止组套组）时恒为 1 层。
_GROUP_MAX_DEPTH = 8


# Output-budget floor when retrying empty / length-truncated responses (v17).
_MIN_RETRY_ALLOWED_OUT = 2048
# Max continuation rounds inside a single chat_completion when finish_reason=length.
_MAX_LENGTH_CONTINUATIONS = 2
_LENGTH_FINISH_REASONS = frozenset({"length", "max_tokens"})


@dataclass
class ChatResult:
    """Structured chat_completion return for the native tool-calls path.

    ``text`` is the normalized protocol text (FINAL:/SHELL: …) for the text path;
    ``content`` + ``tool_calls`` carry the raw native pairing for role:tool backfill.
    ``output_truncated`` is True when finish_reason=length persisted after bounded
    continuations — the loop must not accept FINAL from this reply (v17 R3).
    """

    text: str = ""
    content: str = ""
    tool_calls: list[dict] = field(default_factory=list)
    output_truncated: bool = False

    @property
    def has_tool_calls(self) -> bool:
        return bool(self.tool_calls)


def _tool_call_to_step(call: dict) -> ToolStep | None:
    """Map one native function tool_call to a ToolStep (single source of the
    name→action mapping). Carries the provider ``id`` so results can be backfilled
    as role:tool with the matching tool_call_id.
    """
    fn = call.get("function") if isinstance(call, dict) else None
    if not isinstance(fn, dict):
        return None
    name = str(fn.get("name") or "").strip()
    raw_args = fn.get("arguments")
    call_id = str(call.get("id") or "").strip()
    if not name:
        return None
    if isinstance(raw_args, str):
        args_text = strip_leaked_tool_tokens(raw_args).strip() or "{}"
        try:
            args_obj = json.loads(args_text)
        except (TypeError, ValueError):
            args_obj = None
    elif isinstance(raw_args, dict):
        args_obj = raw_args
        args_text = json.dumps(raw_args, ensure_ascii=False)
    else:
        args_obj = None
        args_text = "{}"

    lower_name = name.lower()
    step: ToolStep | None = None
    if lower_name in ("done", "final"):
        answer = _tool_arg(args_obj, "answer") or (args_text if args_obj is None else "")
        if answer:
            step = ToolStep("done", f"FINAL: {answer}", True)
    elif lower_name == "shell":
        cmd = _tool_arg(args_obj, "cmd")
        if cmd:
            step = ToolStep("shell", f"SHELL: {cmd}")
    elif lower_name == "file_write":
        path = _tool_arg(args_obj, "path")
        if path:
            step = ToolStep("file_write", f"WRITE: {path}\n{_tool_arg(args_obj, 'content')}")
    elif lower_name == "file_read":
        path = _tool_arg(args_obj, "path")
        if path:
            step = ToolStep("file_read", f"READ: {path}")
    elif lower_name == "file_search_replace":
        path = _tool_arg(args_obj, "path")
        if path:
            step = ToolStep("file_search_replace", f"PATCH: {path}\n{_tool_arg(args_obj, 'old')}\n{_tool_arg(args_obj, 'new')}")
    elif lower_name in ("mcp", "mcp_tool_call"):
        tool_name = _tool_arg(args_obj, "tool_name", "tool", "name")
        if tool_name:
            tool_args = (
                args_obj.get("arguments")
                if isinstance(args_obj, dict) and "arguments" in args_obj
                else args_obj.get("args", args_obj.get("input", {}))
                if isinstance(args_obj, dict)
                else {}
            )
            norm = tool_args.strip() if isinstance(tool_args, str) else json.dumps(tool_args or {}, ensure_ascii=False)
            step = ToolStep("mcp_tool_call", f"MCP: {tool_name} {norm}")
    elif lower_name == "httpmcp_call":
        tool_name = _tool_arg(args_obj, "tool_name", "tool", "name")
        if tool_name:
            tool_args = (
                args_obj.get("arguments")
                if isinstance(args_obj, dict) and "arguments" in args_obj
                else args_obj.get("args", args_obj.get("input", {}))
                if isinstance(args_obj, dict)
                else {}
            )
            norm = tool_args.strip() if isinstance(tool_args, str) else json.dumps(tool_args or {}, ensure_ascii=False)
            step = ToolStep("httpmcp_call", f"HTTPMCP: {tool_name} {norm}")
    elif lower_name == "file_search":
        q = _tool_arg(args_obj, "query")
        if q:
            step = ToolStep("file_search", f"SEARCH: {q}")
    elif lower_name in ("code_read", "code_search", "code_edit", "code_test", "code_shell", "code_git"):
        step = ToolStep(lower_name, f"{lower_name.upper()}: {json.dumps(args_obj or {}, ensure_ascii=False)}")
    elif lower_name == "rag_query":
        q = _tool_arg(args_obj, "query")
        if q:
            step = ToolStep("rag_query", f"RAG: {q}")
    elif lower_name == "skill_read_md":
        sid = _tool_arg(args_obj, "skill_id", "id")
        if sid:
            step = ToolStep("skill_read_md", f"SKILL_MD: {sid}")
    elif lower_name == "skill_run_script":
        n = _tool_arg(args_obj, "name", "skill_id")
        if n:
            step = ToolStep("skill_run_script", f"RUN_SKILL: {n}")
    elif lower_name == "recall":
        q = _tool_arg(args_obj, "query")
        if q:
            step = ToolStep("recall", f"RECALL: {q}")
    else:
        # Unknown tool name → assume an MCP tool invoked by its real name.
        step = ToolStep("mcp_tool_call", f"MCP: {name} {args_text}")
    if step is not None:
        step.tool_call_id = call_id
    return step


def tool_steps_from_tool_calls(tool_calls: list[dict]) -> list[ToolStep]:
    """Generate ToolSteps directly from native tool_calls (no text re-parse).

    This is the single source for the native path: each call maps to exactly one
    step and keeps its ``tool_call_id`` for role:tool backfill.
    """
    steps: list[ToolStep] = []
    for call in tool_calls or []:
        step = _tool_call_to_step(call)
        if step is not None:
            steps.append(step)
    return steps


def extract_chat_response_text(data: dict) -> str:
    """Return text or normalize OpenAI-compatible tool_calls to engine protocol.

    Native function-calling is normalized into the same text protocol the loop
    already parses (SHELL:/WRITE:/READ:/PATCH:/MCP:/RAG:/SKILL_MD:/
    RUN_SKILL:/RECALL:/FINAL:), so the ReAct loop stays format-agnostic.
    tool_calls take priority over content when both are present (the content is
    usually a prose preamble in that case).
    """
    try:
        message = data["choices"][0]["message"]
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError("LLM 响应缺少 choices[0].message") from exc
    if not isinstance(message, dict):
        raise RuntimeError("LLM 响应 message 不是对象")

    content_text = _extract_content_text(message)

    lines: list[str] = []
    for call in message.get("tool_calls") or []:
        step = _tool_call_to_step(call)
        if step is not None:
            lines.append(step.reply)

    if lines:
        return "\n".join(lines)
    if content_text:
        return content_text
    # Empty / reasoning-only / unexecutable tool_calls → soft empty string.
    # Raising here used to wrap as「模型组全部失败」and burn llm_failures (v17 R1).
    return ""


def estimate_tokens(text: str) -> int:
    """Conservative token estimate for mixed CJK/Latin (≈2 chars/token)."""
    if not text:
        return 0
    return max(1, (len(text) + 1) // 2)


def estimate_messages_tokens(messages: list[dict]) -> int:
    """Sum estimate_tokens over message contents plus 4 tokens/message overhead.

    Single token-accounting口径 shared by ``fit_messages_to_context`` and the
    context-availability percentage (react-engine-v15 R4) so the two never drift.
    """
    return sum(estimate_tokens(m.get("content") or "") + 4 for m in messages or [])


def _clip_text(text: str, max_chars: int) -> str:
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    keep = max(0, max_chars - 12)
    return text[:keep] + "\n…(已截断)"


def _split_system_layers(messages: list[dict]) -> tuple[list[str], list[str]]:
    """Split raw system/developer messages into (static, volatile) content parts.

    Volatile layers (coach_hint / progress_block, flagged ``volatile`` by
    ContextManager) must survive head-truncation; static layers (base, task
    context, tools/skills catalog) are trimmed first (D3).
    """
    static: list[str] = []
    volatile: list[str] = []
    for raw in messages or []:
        if not isinstance(raw, dict):
            continue
        role = str(raw.get("role") or "").strip().lower()
        if role not in ("system", "developer"):
            continue
        content = raw.get("content")
        if content is None:
            content = ""
        elif not isinstance(content, str):
            content = json.dumps(content, ensure_ascii=False)
        if raw.get("volatile"):
            volatile.append(content)
        else:
            static.append(content)
    return static, volatile


def fit_messages_to_context(
    messages: list[dict],
    *,
    max_context_tokens: int,
    max_output_tokens: int,
    min_allowed_out: int | None = None,
) -> tuple[list[dict], int]:
    """Trim/drop messages so input + output fit the model context window.

    Returns (messages, recommended_max_tokens).

    When ``min_allowed_out`` is set (v17 empty/length retry), trim more aggressively
    so the returned ``allowed_out`` is at least that floor when the window allows.
    """
    ctx = max(2048, int(max_context_tokens or 128000))
    out_cap = max(256, int(max_output_tokens or 4096))
    out_floor = max(256, int(min_allowed_out or 256))
    # Leave headroom for tokenizer variance and provider overhead
    reserve = max(out_cap + 512, min(8192, ctx // 8))
    if min_allowed_out is not None:
        reserve = max(reserve, out_floor + 512)
    budget = max(1024, ctx - reserve)

    msgs = normalize_chat_messages(messages)
    if not msgs:
        return msgs, min(out_cap, ctx // 4)

    # Cap individual message size (system / history / tool dumps)
    per_cap_chars = max(2000, min(24000, budget * 2 // 3))
    system_limit = min(per_cap_chars, 20000)

    # Protect dynamic system layers (coach_hint / progress_block) from head
    # truncation: trim static layers first, keep volatile layers whole (D3).
    static_parts, volatile_parts = _split_system_layers(messages)
    static_text = "\n\n".join(static_parts)
    volatile_text = "\n\n".join(volatile_parts)
    if volatile_text:
        static_budget = max(0, system_limit - len(volatile_text) - 2)
        protected_system = (
            (_clip_text(static_text, static_budget) + "\n\n" + volatile_text)
            if static_text
            else volatile_text
        )
    else:
        protected_system = _clip_text(static_text, system_limit)

    capped: list[dict] = []
    for i, m in enumerate(msgs):
        content = m.get("content") or ""
        if m.get("role") == "system":
            item = {"role": "system", "content": protected_system}
        else:
            # Keep the latest user turn a bit larger
            limit = per_cap_chars if i < len(msgs) - 1 else min(per_cap_chars * 2, 48000)
            item = {"role": m["role"], "content": _clip_text(content, limit)}
            # Preserve native pairing fields through the per-message cap.
            if m.get("tool_calls"):
                item["tool_calls"] = m["tool_calls"]
            if m.get("tool_call_id"):
                item["tool_call_id"] = m["tool_call_id"]
        capped.append(item)
    msgs = capped

    def _total(ms: list[dict]) -> int:
        return estimate_messages_tokens(ms)

    # Drop oldest non-system messages until under budget (keep first system + tail)
    while len(msgs) > 2 and _total(msgs) > budget:
        # Prefer dropping early user/assistant after the leading system block
        drop_idx = None
        for i, m in enumerate(msgs):
            if i == 0 and m.get("role") == "system":
                continue
            if i >= len(msgs) - 1:
                continue
            drop_idx = i
            break
        if drop_idx is None:
            break
        # Atomic group trim: an assistant(tool_calls) and its following consecutive
        # role:tool messages are dropped as one unit so no tool message is orphaned.
        drop_count = 1
        if msgs[drop_idx].get("tool_calls"):
            j = drop_idx + 1
            while j < len(msgs) and msgs[j].get("role") == "tool":
                drop_count += 1
                j += 1
        del msgs[drop_idx : drop_idx + drop_count]

    # If still over, aggressively shrink system then oldest remaining
    while msgs and _total(msgs) > budget:
        # Shrink the longest message that isn't the last user turn
        longest_i = 0
        longest_n = -1
        for i, m in enumerate(msgs[:-1] if len(msgs) > 1 else msgs):
            n = len(m.get("content") or "")
            if n > longest_n:
                longest_n = n
                longest_i = i
        if longest_n <= 200:
            break
        msgs[longest_i]["content"] = _clip_text(msgs[longest_i]["content"], longest_n // 2)

    used = _total(msgs)
    # Safety margin: estimate_tokens (≈ len/2) undercounts symbol-dense shell/code
    # output, so shave ~10% of the estimated input off allowed_out on top of the
    # fixed 256 guard to keep input+output inside the window (MiniMax 2013).
    def _allowed(ms: list[dict]) -> int:
        u = _total(ms)
        safety = max(512, u // 10)
        return max(256, min(out_cap, ctx - u - 256 - safety))

    # v17: when a higher output floor is requested, keep trimming until we can
    # meet it (or messages can shrink no further).
    allowed_out = _allowed(msgs)
    while min_allowed_out is not None and allowed_out < out_floor and len(msgs) > 2:
        drop_idx = None
        for i, m in enumerate(msgs):
            if i == 0 and m.get("role") == "system":
                continue
            if i >= len(msgs) - 1:
                continue
            drop_idx = i
            break
        if drop_idx is None:
            break
        drop_count = 1
        if msgs[drop_idx].get("tool_calls"):
            j = drop_idx + 1
            while j < len(msgs) and msgs[j].get("role") == "tool":
                drop_count += 1
                j += 1
        del msgs[drop_idx : drop_idx + drop_count]
        allowed_out = _allowed(msgs)

    while min_allowed_out is not None and allowed_out < out_floor and msgs:
        longest_i = 0
        longest_n = -1
        for i, m in enumerate(msgs[:-1] if len(msgs) > 1 else msgs):
            n = len(m.get("content") or "")
            if n > longest_n:
                longest_n = n
                longest_i = i
        if longest_n <= 200:
            break
        msgs[longest_i]["content"] = _clip_text(msgs[longest_i]["content"], longest_n // 2)
        allowed_out = _allowed(msgs)

    allowed_out = _allowed(msgs)
    if min_allowed_out is not None:
        # Prefer the requested floor when the window still allows it.
        room = max(256, ctx - _total(msgs) - 256 - max(512, _total(msgs) // 10))
        allowed_out = max(allowed_out, min(out_floor, out_cap, room))
    return msgs, allowed_out


def _extract_api_error_text(response: httpx.Response | None) -> str:
    if response is None:
        return ""
    try:
        data = response.json()
    except Exception:
        text = (response.text or "").strip()
        return text[:800] if text else ""

    if isinstance(data, dict):
        err = data.get("error")
        if isinstance(err, dict):
            msg = err.get("message") or err.get("msg") or err.get("type")
            if msg:
                return str(msg)[:800]
            return json.dumps(err, ensure_ascii=False)[:800]
        if isinstance(err, str) and err.strip():
            return err.strip()[:800]
        base = data.get("base_resp")
        if isinstance(base, dict):
            msg = base.get("status_msg") or base.get("message")
            if msg:
                return str(msg)[:800]
        for key in ("message", "msg", "detail"):
            if data.get(key):
                return str(data[key])[:800]
        return json.dumps(data, ensure_ascii=False)[:800]
    return str(data)[:800]


def _retryable_status_attempts(status: int) -> int:
    """Retry budget for transient HTTP statuses; 0 means not retryable.

    react-engine-v10 R5: 529 (overload) / 429 (rate-limit) → 3 attempts;
    502/503/504 (transient 5xx) → 5 attempts. Deterministic failures (400/401/
    2013/1026/1027) return 0 so they re-raise straight to the caller.
    """
    if status in (529, 429):
        return 3
    if status in (502, 503, 504):
        return 5
    return 0


def format_llm_http_error(exc: httpx.HTTPStatusError) -> str:
    """Build a readable error for LLM HTTP failures (esp. MiniMax)."""
    status = exc.response.status_code if exc.response is not None else "?"
    body = _extract_api_error_text(exc.response)
    lower = body.lower()

    if status == 401:
        return (
            "API Key 认证失败（401），请在 LLM 管理中检查密钥是否正确、是否与 Base URL 区域匹配"
        )

    if status == 529:
        return (
            "LLM 集群过载（529）：服务端暂时无法处理请求，平台已自动退避重试；"
            "若仍失败，请稍后重试或切换其它模型 / 区域。"
        )
    if status == 429:
        return (
            "LLM 请求被限流（429）：平台已自动退避重试；"
            "若仍失败，请稍后重试或降低请求频率。"
        )
    if status in (502, 503, 504):
        return (
            f"LLM 服务端瞬时故障（{status}）：平台已自动退避重试；"
            "若仍失败，请稍后重试或切换其它模型。"
        )

    hint = ""
    if "1027" in lower or ("output" in lower and "sensitive" in lower):
        hint = (
            "（MiniMax 输出内容审核未通过；请改写任务表述，或新开会话去掉历史报表/工具结果后重试）"
        )
    elif (
        "1026" in lower
        or "new_sensitive" in lower
        or ("input" in lower and "sensitive" in lower)
    ):
        hint = (
            "（MiniMax 输入内容审核未通过 1026：常为历史对话/工具结果误触敏感词。"
            "可新开会话、缩短历史，或换更中性的表述；平台会尝试精简上下文重试一次）"
        )
    elif status == 400 and ("context window" in lower or ("2013" in lower and "context" in lower)):
        hint = "（输入过长或 max_tokens 挤占窗口；平台会自动截断历史/工具结果，请重试或缩短会话）"
    elif status == 400 and any(
        k in lower
        for k in (
            "invalid chat setting",
            "multiple system",
            "invalid role",
            "developer",
        )
    ):
        hint = "（平台已自动合并连续 system 消息；若仍失败请检查模型名与 API Key）"
    elif status == 400 and "2013" in lower:
        hint = "（MiniMax 2013：请检查上下文长度、max_tokens，或缩短会话后重试）"
    elif status == 400 and not body:
        hint = "（常见原因：多条 system、模型名无效、或请求体不合法；请查看 LLM 配置）"
    elif "/anthropic/" in str(exc.request.url if exc.request else ""):
        hint = "（MiniMax 请使用 OpenAI 兼容地址：https://api.minimaxi.com/v1）"

    if body:
        return f"LLM 请求被拒绝 ({status}): {body}{hint}"
    return f"LLM 请求被拒绝 ({status}): {exc}{hint}"


def _is_sensitive_input_error(exc: httpx.HTTPStatusError) -> bool:
    body = _extract_api_error_text(exc.response).lower()
    status = exc.response.status_code if exc.response is not None else 0
    if "1026" in body or ("new_sensitive" in body and "output" not in body):
        return True
    if status in (400, 422) and "sensitive" in body and "1027" not in body:
        return True
    return False


def slim_messages_for_sensitive_retry(messages: list[dict]) -> list[dict]:
    """Keep a short system + latest user turn to avoid history false-positives."""
    msgs = normalize_chat_messages(messages)
    system_parts: list[str] = []
    last_user = ""
    for m in msgs:
        role = m.get("role")
        content = (m.get("content") or "").strip()
        if role == "system" and content:
            system_parts.append(content)
        if role == "user" and content:
            last_user = content
    system = "\n\n".join(system_parts)
    # Drop bulky workplace dumps / tool catalogs that often trip filters when combined
    if len(system) > 2500:
        system = system[:2200] + "\n…(系统提示已精简以避开内容审核误伤)"
    if len(last_user) > 4000:
        last_user = last_user[:3800] + "\n…(用户消息已截断)"
    if not last_user:
        last_user = "请继续完成上一任务，用 FINAL: 给出结论。"
    out = []
    if system:
        out.append({
            "role": "system",
            "content": (
                system
                + "\n\n【注意】因内容审核，本轮仅保留精简上下文；请基于用户最新指令直接完成，"
                "勿要求回顾被省略的历史细节。"
            ),
        })
    out.append({"role": "user", "content": last_user})
    return out


async def test_llm_chat(
    llm: LLMResource,
    message: str,
    db: Session | None = None,
    _visited: set | None = None,
    _depth: int = 0,
) -> str:
    if llm.type == "group":
        members = json.loads(llm.members or "[]")
        if not members:
            return "模型组无成员"
        visited = _visited or set()
        if llm.id in visited or _depth >= _GROUP_MAX_DEPTH:
            return "模型组存在循环引用或嵌套过深"
        visited = visited | {llm.id}
        if db:
            for mid in members:
                child = db.query(LLMResource).filter(LLMResource.id == mid).first()
                if child:
                    result = await test_llm_chat(
                        child, message, db, _visited=visited, _depth=_depth + 1,
                    )
                    if not result.startswith("测试失败"):
                        return result
            return "模型组全部成员不可用"
        return "需要 db 会话解析模型组"

    api_key = decrypt_secret(llm.api_key_enc)
    anthropic = is_anthropic_provider(llm.provider)
    endpoint = (
        anthropic_messages_url(llm.base_url)
        if anthropic
        else openai_chat_completions_url(llm.base_url, llm.provider)
    )
    if not endpoint:
        return "未配置 base_url"

    payload_messages = normalize_chat_messages([{"role": "user", "content": message}])
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            headers = (
                {
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                    "Content-Type": "application/json",
                }
                if anthropic
                else {
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                }
            )
            resp = await client.post(
                endpoint,
                headers=headers,
                json={
                    "model": llm.model,
                    "messages": payload_messages,
                    "max_tokens": min(llm.max_output_tokens or 4096, 1024),
                },
            )
            resp.raise_for_status()
            data = resp.json()
            if anthropic:
                return _anthropic_content_text(data)
            return data["choices"][0]["message"]["content"]
    except httpx.HTTPStatusError as e:
        return f"测试失败: {format_llm_http_error(e)}"
    except Exception as e:
        return f"测试失败: {e}"


def _choice0(data: dict) -> dict:
    try:
        choice = data["choices"][0]
    except (KeyError, IndexError, TypeError):
        return {}
    return choice if isinstance(choice, dict) else {}


def _response_message(data: dict) -> dict:
    message = _choice0(data).get("message")
    return message if isinstance(message, dict) else {}


def _finish_reason(data: dict) -> str:
    return str(_choice0(data).get("finish_reason") or "").strip().lower()


def _is_length_finish(data: dict) -> bool:
    return _finish_reason(data) in _LENGTH_FINISH_REASONS


def _has_reasoning_only(data: dict) -> bool:
    message = _response_message(data)
    if _extract_content_text(message):
        return False
    if any(_tool_call_to_step(c) is not None for c in (message.get("tool_calls") or [])):
        return False
    return bool(message.get("reasoning_details") or message.get("reasoning_content"))


def _executable_tool_calls(data: dict) -> list[dict]:
    message = _response_message(data)
    return [c for c in (message.get("tool_calls") or []) if _tool_call_to_step(c) is not None]


def _response_content_text(data: dict) -> str:
    return _extract_content_text(_response_message(data))


def _is_produceless(data: dict) -> bool:
    """True when there is no user-visible text and no executable tool_calls.

    Reasoning-only MiniMax rounds are produceless for the loop (soft empty) but
    do not need an empty-retry bump — they already map to "".
    Truncated / unmappable tool_calls count as produceless (v17 R4).
    """
    if _response_content_text(data):
        return False
    if _executable_tool_calls(data):
        return False
    return True


def _build_chat_result(data: dict, *, output_truncated: bool = False) -> ChatResult:
    """Split a raw LLM response into a ChatResult for the native tool-calls path.

    When executable tool_calls are present the normalized text is empty (the loop
    generates ToolSteps directly from ``tool_calls``); otherwise ``text`` carries
    the normalized protocol reply. Unexecutable / truncated tool_calls are dropped
    so the loop never executes half-parsed calls (v17 R4).
    """
    content_text = _response_content_text(data)
    executable = _executable_tool_calls(data)
    if executable and not output_truncated and not _is_length_finish(data):
        return ChatResult(
            text="",
            content=content_text,
            tool_calls=executable,
            output_truncated=False,
        )
    # Length-truncated tool_calls: never execute — surface as empty/truncated text path.
    if executable and (_is_length_finish(data) or output_truncated):
        return ChatResult(
            text=content_text or "",
            content=content_text,
            tool_calls=[],
            output_truncated=True,
        )
    return ChatResult(
        text=extract_chat_response_text(data),
        content=content_text,
        tool_calls=[],
        output_truncated=output_truncated,
    )


async def chat_completion(
    llm: LLMResource,
    messages: list[dict],
    max_tokens: int | None = None,
    db: Session | None = None,
    timeout: int | None = None,
    cancel_check: Callable[[], bool] | None = None,
    tools: list[dict] | None = None,
    collect_native: bool = False,
    _visited: set | None = None,
    _depth: int = 0,
) -> str | ChatResult:
    if llm.type == "group":
        members = json.loads(llm.members or "[]")
        if not members:
            raise RuntimeError("模型组无成员")
        if not db:
            raise RuntimeError("模型组需要 db 会话")
        visited = _visited or set()
        if llm.id in visited or _depth >= _GROUP_MAX_DEPTH:
            raise LLMGroupCycleError("模型组存在循环引用或嵌套过深")
        visited = visited | {llm.id}
        last_err = None
        for mid in members:
            child = db.query(LLMResource).filter(LLMResource.id == mid).first()
            if not child:
                continue
            try:
                return await chat_completion(
                    child, messages, max_tokens, db,
                    timeout=timeout, cancel_check=cancel_check, tools=tools,
                    collect_native=collect_native,
                    _visited=visited, _depth=_depth + 1,
                )
            except LLMGroupCycleError:
                raise
            except Exception as e:
                last_err = e
                continue
        raise RuntimeError(f"模型组全部失败: {last_err}")

    api_key = decrypt_secret(llm.api_key_enc)
    if not api_key:
        raise RuntimeError("未配置 API Key")
    if is_masked_secret(api_key):
        raise RuntimeError("API Key 无效（保存了脱敏占位符），请在 LLM 管理中重新填写完整密钥")
    endpoint = openai_chat_completions_url(llm.base_url, llm.provider)
    # Prefer explicit timeout (e.g. Agent.llm_timeout) over LLM resource default
    resolved_timeout = timeout if timeout is not None else getattr(llm, "llm_timeout", None)
    resolved_timeout = int(resolved_timeout) if resolved_timeout else 120
    if resolved_timeout < 1:
        resolved_timeout = 120
    requested_out = max_tokens or llm.max_output_tokens or 4096
    ctx_tokens = int(getattr(llm, "max_context_tokens", None) or 128000)

    def _raise_if_stopped() -> None:
        if cancel_check and cancel_check():
            raise ChatStopped("已停止")

    async def _post(payload_messages: list[dict], out_tokens: int) -> dict:
        _raise_if_stopped()
        body = {
            "model": llm.model,
            "messages": payload_messages,
            "max_tokens": out_tokens,
        }
        if tools:
            body["tools"] = tools
        if is_minimax_llm(llm):
            # Keep M3 thinking out of `content` (separate reasoning_details field),
            # so an "only thinking" round no longer parses as a corrupted reply.
            body["reasoning_split"] = True
        async with httpx.AsyncClient(timeout=resolved_timeout) as client:
            send_task = asyncio.create_task(
                client.post(
                    endpoint,
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                    json=body,
                )
            )
            try:
                while True:
                    _raise_if_stopped()
                    done, _pending = await asyncio.wait({send_task}, timeout=0.35)
                    if done:
                        break
                if send_task.cancelled():
                    raise ChatStopped("已停止")
                resp = send_task.result()
                resp.raise_for_status()
                return resp.json()
            except asyncio.CancelledError as exc:
                raise ChatStopped("已停止") from exc
            finally:
                if not send_task.done():
                    send_task.cancel()
                    try:
                        await send_task
                    except (asyncio.CancelledError, Exception):
                        pass

    async def _post_with_transport_retry(
        payload_messages: list[dict],
        out_tokens: int,
        *,
        attempts: int = 5,
    ) -> dict:
        """Retry transport errors and transient HTTP statuses with backoff + jitter.

        Transport errors use ``attempts`` (5). Retryable HTTP statuses use a fixed
        budget (529/429→3, 502/503/504→5). Deterministic failures (400/401/2013/
        1026/1027) re-raise immediately so the caller's sensitive-input / error path
        is unchanged (react-engine-v10 R5).
        """
        last_error: Exception | None = None
        budget: int | None = None
        attempt = 0
        while attempt < max(1, budget if budget is not None else attempts):
            _raise_if_stopped()
            try:
                return await _post(payload_messages, out_tokens)
            except ChatStopped:
                raise
            except httpx.TransportError as exc:
                last_error = exc
                if budget is None:
                    budget = attempts
                logger.warning(
                    "LLM transport failed model=%s attempt=%d/%d type=%s error=%r",
                    llm.model, attempt + 1, budget, type(exc).__name__, exc,
                )
            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code if exc.response is not None else 0
                n = _retryable_status_attempts(status)
                if n <= 0:
                    raise  # deterministic → caller's sensitive-input / error path
                last_error = exc
                if budget is None:
                    budget = n
                logger.warning(
                    "LLM HTTP retryable failed model=%s status=%d attempt=%d/%d",
                    llm.model, status, attempt + 1, budget,
                )
            if attempt + 1 < (budget if budget is not None else attempts):
                # Exponential backoff 2/4/8/16s + random jitter (up to +25%).
                base = 2.0 * (2 ** attempt)
                jitter = random.uniform(0.0, 0.25) * base
                await asyncio.sleep(base + jitter)
            attempt += 1
        assert last_error is not None
        if isinstance(last_error, httpx.HTTPStatusError):
            raise last_error  # caller's HTTPStatusError handler builds the message
        raise LLMTransportError(
            f"LLM 传输失败 ({type(last_error).__name__}): {last_error!r}"
            f"（端点 {llm.base_url}）"
        ) from last_error

    def _fit(msgs: list[dict], *, min_out: int | None = None) -> tuple[list[dict], int]:
        return fit_messages_to_context(
            msgs,
            max_context_tokens=ctx_tokens,
            max_output_tokens=int(requested_out),
            min_allowed_out=min_out,
        )

    async def _pipeline(data: dict, payload_messages: list[dict]) -> str | ChatResult:
        """v17: empty soft-retry + length continuation inside one chat_completion."""
        # R1/R4: produceless (empty / unexecutable tools) — bump output budget once.
        # Reasoning-only MiniMax is soft-empty without a retry bump.
        if _is_produceless(data) and not _has_reasoning_only(data):
            retry_msgs, retry_out = _fit(messages, min_out=_MIN_RETRY_ALLOWED_OUT)
            logger.info(
                "LLM empty/unexecutable response; retry once with allowed_out=%s model=%s",
                retry_out, llm.model,
            )
            data = await _post_with_transport_retry(retry_msgs, retry_out)
            payload_messages = retry_msgs
            if _is_produceless(data):
                # Soft empty — never raise (group must not failover on empty 200).
                empty = ChatResult(text="", content="", tool_calls=[], output_truncated=False)
                return empty if collect_native else ""

        # Length-truncated tool_calls: do not execute; treat as truncated empty-ish.
        if _executable_tool_calls(data) and _is_length_finish(data):
            logger.info(
                "LLM length-truncated tool_calls; refusing partial execution model=%s",
                llm.model,
            )
            # One empty-style retry with more output budget (same as R1).
            retry_msgs, retry_out = _fit(messages, min_out=_MIN_RETRY_ALLOWED_OUT)
            data = await _post_with_transport_retry(retry_msgs, retry_out)
            payload_messages = retry_msgs
            if _executable_tool_calls(data) and _is_length_finish(data):
                result = _build_chat_result(data, output_truncated=True)
                return result if collect_native else (result.text or result.content or "")
            if _is_produceless(data):
                empty = ChatResult(text="", content="", tool_calls=[], output_truncated=False)
                return empty if collect_native else ""

        # R2: finish_reason=length with visible text → continue up to 2 times.
        if _is_length_finish(data) and _response_content_text(data):
            parts = [_response_content_text(data)]
            cont_messages = list(payload_messages) + [
                {"role": "assistant", "content": parts[0]},
                {"role": "user", "content": "请从断点处继续输出，不要重复已写内容。"},
            ]
            still_truncated = True
            for _ in range(_MAX_LENGTH_CONTINUATIONS):
                cont_msgs, cont_out = _fit(cont_messages, min_out=_MIN_RETRY_ALLOWED_OUT)
                logger.info(
                    "LLM finish_reason=length; continuation allowed_out=%s model=%s",
                    cont_out, llm.model,
                )
                cont_data = await _post_with_transport_retry(cont_msgs, cont_out)
                piece = _response_content_text(cont_data)
                if piece:
                    parts.append(piece)
                    cont_messages = list(cont_msgs) + [
                        {"role": "assistant", "content": piece},
                        {"role": "user", "content": "请从断点处继续输出，不要重复已写内容。"},
                    ]
                if not _is_length_finish(cont_data):
                    still_truncated = False
                    break
                if not piece:
                    break
            joined = "".join(parts)
            result = ChatResult(
                text=joined,
                content=joined,
                tool_calls=[],
                output_truncated=still_truncated,
            )
            return result if collect_native else joined

        if collect_native:
            return _build_chat_result(data)
        return extract_chat_response_text(data)

    payload_messages, allowed_out = _fit(messages)
    try:
        data = await _post_with_transport_retry(payload_messages, allowed_out)
    except ChatStopped:
        raise
    except httpx.HTTPStatusError as e:
        if _is_sensitive_input_error(e):
            slim = slim_messages_for_sensitive_retry(messages)
            slim, slim_out = _fit(slim)
            try:
                data = await _post_with_transport_retry(slim, slim_out)
            except ChatStopped:
                raise
            except httpx.HTTPStatusError as e2:
                raise LLMHTTPError(f"{format_llm_http_error(e2)}（端点 {llm.base_url}）") from e2
            return await _pipeline(data, slim)
        else:
            raise LLMHTTPError(f"{format_llm_http_error(e)}（端点 {llm.base_url}）") from e

    return await _pipeline(data, payload_messages)
