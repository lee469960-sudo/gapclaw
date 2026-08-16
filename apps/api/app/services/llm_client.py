import asyncio
import json
import logging
import re
from collections.abc import Callable

import httpx
from sqlalchemy.orm import Session

from app.models import LLMResource
from app.security import decrypt_secret, is_masked_secret
from app.services.agent_runtime.hub import ChatStopped


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


def normalize_chat_messages(messages: list[dict]) -> list[dict]:
    """Prepare messages for strict OpenAI-compatible providers (e.g. MiniMax).

    - Map developer -> system
    - Coalesce consecutive system messages into one
    - Ensure content is a string
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
        out.append({"role": role, "content": content})
    return out


def extract_chat_response_text(data: dict) -> str:
    """Return text or normalize OpenAI-compatible tool_calls to engine protocol."""
    try:
        message = data["choices"][0]["message"]
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError("LLM 响应缺少 choices[0].message") from exc

    content = message.get("content") if isinstance(message, dict) else None
    if isinstance(content, str) and content.strip():
        return content
    if isinstance(content, list):
        parts = [
            str(item.get("text") or "")
            for item in content
            if isinstance(item, dict) and item.get("text")
        ]
        if parts:
            return "\n".join(parts)

    tool_calls = message.get("tool_calls") if isinstance(message, dict) else None
    lines: list[str] = []
    for call in tool_calls or []:
        fn = call.get("function") if isinstance(call, dict) else None
        if not isinstance(fn, dict):
            continue
        name = str(fn.get("name") or "").strip()
        raw_args = fn.get("arguments")
        if not name:
            continue
        if isinstance(raw_args, str):
            args_text = raw_args.strip() or "{}"
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
        if lower_name in ("mcp", "mcp_tool_call") and isinstance(args_obj, dict):
            tool_name = str(
                args_obj.get("tool_name")
                or args_obj.get("tool")
                or args_obj.get("name")
                or ""
            ).strip()
            tool_args = (
                args_obj.get("arguments")
                if "arguments" in args_obj
                else args_obj.get("args", args_obj.get("input", {}))
            )
            if tool_name:
                if isinstance(tool_args, str):
                    normalized_args = tool_args.strip() or "{}"
                else:
                    normalized_args = json.dumps(tool_args or {}, ensure_ascii=False)
                lines.append(f"MCP: {tool_name} {normalized_args}")
            continue
        if lower_name == "shell":
            command = args_obj.get("cmd") if isinstance(args_obj, dict) else args_text
            if command:
                lines.append(f"SHELL: {command}")
            continue
        if lower_name in ("done", "final"):
            answer = args_obj.get("answer") if isinstance(args_obj, dict) else args_text
            if answer:
                lines.append(f"FINAL: {answer}")
            continue
        lines.append(f"MCP: {name} {args_text}")

    if lines:
        return "\n".join(lines)
    raise RuntimeError("LLM 响应缺少 content，且未返回可执行 tool_calls")


def estimate_tokens(text: str) -> int:
    """Conservative token estimate for mixed CJK/Latin (≈2 chars/token)."""
    if not text:
        return 0
    return max(1, (len(text) + 1) // 2)


def _clip_text(text: str, max_chars: int) -> str:
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    keep = max(0, max_chars - 12)
    return text[:keep] + "\n…(已截断)"


def fit_messages_to_context(
    messages: list[dict],
    *,
    max_context_tokens: int,
    max_output_tokens: int,
) -> tuple[list[dict], int]:
    """Trim/drop messages so input + output fit the model context window.

    Returns (messages, recommended_max_tokens).
    """
    ctx = max(2048, int(max_context_tokens or 128000))
    out_cap = max(256, int(max_output_tokens or 4096))
    # Leave headroom for tokenizer variance and provider overhead
    reserve = max(out_cap + 512, min(8192, ctx // 8))
    budget = max(1024, ctx - reserve)

    msgs = normalize_chat_messages(messages)
    if not msgs:
        return msgs, min(out_cap, ctx // 4)

    # Cap individual message size (system / history / tool dumps)
    per_cap_chars = max(2000, min(24000, budget * 2 // 3))
    capped: list[dict] = []
    for i, m in enumerate(msgs):
        content = m.get("content") or ""
        # Keep the latest user turn a bit larger
        limit = per_cap_chars if i < len(msgs) - 1 else min(per_cap_chars * 2, 48000)
        if m.get("role") == "system":
            limit = min(limit, 20000)
        capped.append({"role": m["role"], "content": _clip_text(content, limit)})
    msgs = capped

    def _total(ms: list[dict]) -> int:
        return sum(estimate_tokens(m.get("content") or "") + 4 for m in ms)

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
        msgs.pop(drop_idx)

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
    # Ensure max_tokens won't push past context (MiniMax 2013 often = input+output > window)
    allowed_out = max(256, min(out_cap, ctx - used - 256))
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


def format_llm_http_error(exc: httpx.HTTPStatusError) -> str:
    """Build a readable error for LLM HTTP failures (esp. MiniMax)."""
    status = exc.response.status_code if exc.response is not None else "?"
    body = _extract_api_error_text(exc.response)
    lower = body.lower()

    if status == 401:
        return (
            "API Key 认证失败（401），请在 LLM 管理中检查密钥是否正确、是否与 Base URL 区域匹配"
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


async def test_llm_chat(llm: LLMResource, message: str, db: Session | None = None) -> str:
    if llm.type == "group":
        members = json.loads(llm.members or "[]")
        if not members:
            return "模型组无成员"
        if db:
            for mid in members:
                child = db.query(LLMResource).filter(LLMResource.id == mid).first()
                if child:
                    result = await test_llm_chat(child, message, db)
                    if not result.startswith("测试失败"):
                        return result
            return "模型组全部成员不可用"
        return "需要 db 会话解析模型组"

    api_key = decrypt_secret(llm.api_key_enc)
    endpoint = openai_chat_completions_url(llm.base_url, llm.provider)
    if not endpoint:
        return "未配置 base_url"

    payload_messages = normalize_chat_messages([{"role": "user", "content": message}])
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                endpoint,
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={
                    "model": llm.model,
                    "messages": payload_messages,
                    "max_tokens": min(llm.max_output_tokens or 4096, 1024),
                },
            )
            resp.raise_for_status()
            data = resp.json()
            return data["choices"][0]["message"]["content"]
    except httpx.HTTPStatusError as e:
        return f"测试失败: {format_llm_http_error(e)}"
    except Exception as e:
        return f"测试失败: {e}"


async def chat_completion(
    llm: LLMResource,
    messages: list[dict],
    max_tokens: int | None = None,
    db: Session | None = None,
    timeout: int | None = None,
    cancel_check: Callable[[], bool] | None = None,
) -> str:
    if llm.type == "group":
        members = json.loads(llm.members or "[]")
        if not members:
            raise RuntimeError("模型组无成员")
        if not db:
            raise RuntimeError("模型组需要 db 会话")
        last_err = None
        for mid in members:
            child = db.query(LLMResource).filter(LLMResource.id == mid).first()
            if not child:
                continue
            try:
                return await chat_completion(
                    child, messages, max_tokens, db,
                    timeout=timeout, cancel_check=cancel_check,
                )
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

    async def _post(payload_messages: list[dict], out_tokens: int) -> str:
        _raise_if_stopped()
        async with httpx.AsyncClient(timeout=resolved_timeout) as client:
            send_task = asyncio.create_task(
                client.post(
                    endpoint,
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": llm.model,
                        "messages": payload_messages,
                        "max_tokens": out_tokens,
                    },
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
                return extract_chat_response_text(resp.json())
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
        attempts: int = 3,
    ) -> str:
        last_error: httpx.TransportError | None = None
        for attempt in range(max(1, attempts)):
            _raise_if_stopped()
            try:
                return await _post(payload_messages, out_tokens)
            except ChatStopped:
                raise
            except httpx.TransportError as exc:
                last_error = exc
                logger.warning(
                    "LLM transport failed model=%s attempt=%d/%d type=%s error=%r",
                    llm.model,
                    attempt + 1,
                    attempts,
                    type(exc).__name__,
                    exc,
                )
                if attempt + 1 < attempts:
                    await asyncio.sleep(0.5 * (2 ** attempt))
        assert last_error is not None
        raise RuntimeError(
            f"LLM 传输失败 ({type(last_error).__name__}): {last_error!r}"
        ) from last_error

    payload_messages, allowed_out = fit_messages_to_context(
        messages,
        max_context_tokens=ctx_tokens,
        max_output_tokens=int(requested_out),
    )
    try:
        return await _post_with_transport_retry(payload_messages, allowed_out)
    except ChatStopped:
        raise
    except httpx.HTTPStatusError as e:
        if _is_sensitive_input_error(e):
            slim = slim_messages_for_sensitive_retry(messages)
            slim, slim_out = fit_messages_to_context(
                slim,
                max_context_tokens=ctx_tokens,
                max_output_tokens=int(requested_out),
            )
            try:
                return await _post_with_transport_retry(slim, slim_out)
            except ChatStopped:
                raise
            except httpx.HTTPStatusError as e2:
                raise RuntimeError(format_llm_http_error(e2)) from e2
        raise RuntimeError(format_llm_http_error(e)) from e
