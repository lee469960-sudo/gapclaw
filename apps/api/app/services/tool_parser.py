"""Parse LLM tool invocations (plain WRITE:/SHELL: and MiniMax XML tool_call)."""

import json
import re
from dataclasses import dataclass


@dataclass
class ToolStep:
    action: str
    reply: str
    is_final: bool = False
    tool_call_id: str = ""
    batch: dict | None = None


def _norm_path(path: str) -> str:
    p = (path or "").strip().strip('"').strip("'")
    # Drop trailing shell redirections glued onto paths by the model
    p = re.split(r"(?:2>&1|2>|1>|>>|[|;&<>]|&&|\|\|)", p, maxsplit=1)[0].strip()
    p = p.replace("\\", "/")
    if p.startswith("/workplace/"):
        p = p[len("/workplace/") :]
    elif p.startswith("workplace/"):
        p = p[len("workplace/") :]
    return p.strip("/").strip("[]").strip()


# Canonical protocol marker list — single source of truth for parsing boundaries,
# history-trimming, and display cleanup. Import this instead of re-listing markers.
PROTOCOL_MARKERS = (
    r"BATCH:|SHELL:|WRITE:|READ:|PATCH:|FINAL:|THINK:|MCP_ROUTE:|MCP:|SKILL_MD:|RAG:|PLAN:|RECALL:|RUN_SKILL:|HTTPMCP:|SEARCH:"
)

# Leaked native tool-call special tokens that pollute text and function names.
# MiniMax emits ]<]minimax[> / ]:<]minimax[ in addition to ChatML <|...|> and
# the older ]<tool_call>[ wrapper.
LEAKED_TOOL_TOKEN_RE = re.compile(
    r"<\|[^|>\n]*\|>"
    r"|\]:?<\]?minimax\[>?"
    r"|<\]minimax\[>?"
    r"|\]<[^>\n]*>\["
)

_PROTOCOL_NAME_ALIASES = {
    "shell": "shell",
    "write": "file_write",
    "file_write": "file_write",
    "read": "file_read",
    "file_read": "file_read",
    "patch": "file_search_replace",
    "file_search_replace": "file_search_replace",
    "search": "file_search",
    "file_search": "file_search",
    "mcp": "mcp_tool_call",
    "mcp_tool_call": "mcp_tool_call",
    "mcp_route": "mcp_route_request",
    "mcp_route_request": "mcp_route_request",
    "httpmcp": "httpmcp_call",
    "httpmcp_call": "httpmcp_call",
    "skill_md": "skill_read_md",
    "skill_read_md": "skill_read_md",
    "run_skill": "skill_run_script",
    "skill_run_script": "skill_run_script",
    "rag": "rag_query",
    "rag_query": "rag_query",
    "recall": "recall",
    "final": "done",
    "done": "done",
}
_MCP_TOOL_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,127}$")


_LEAK_GLUE_RE = re.compile(
    r"\[<+(?=(?:FINAL|SHELL|WRITE|READ|PATCH|MCP|PLAN|THINK|BATCH|SEARCH|RAG|RECALL)\b)",
    re.IGNORECASE,
)


def strip_leaked_tool_tokens(text: str) -> str:
    """Drop leaked native tool-call special tokens (e.g. MiniMax <|tool_call|>)."""
    cleaned = LEAKED_TOOL_TOKEN_RE.sub("", text or "")
    return _LEAK_GLUE_RE.sub("", cleaned)


def sanitize_native_tool_name(name: str) -> str:
    """Strip MiniMax leaks and map SHELL:/READ: leftovers to native tool names."""
    cleaned = strip_leaked_tool_tokens(name or "").strip().strip("`\"'")
    cleaned = cleaned.strip("[]<>|")
    if not cleaned:
        return ""
    prefix = cleaned.split(":", 1)[0].strip().lower().replace("-", "_")
    alias = _PROTOCOL_NAME_ALIASES.get(prefix)
    if alias:
        return alias
    lower = cleaned.lower().replace("-", "_")
    return _PROTOCOL_NAME_ALIASES.get(lower, cleaned)


def looks_like_mcp_tool_name(name: str) -> bool:
    return bool(_MCP_TOOL_NAME_RE.fullmatch(str(name or "").strip()))


_TOOL_BOUNDARY = PROTOCOL_MARKERS

# Protocol completion marker. Only treated as "done" when at line start,
# followed by ':'/'：' and a non-empty payload. Keep this narrow: natural-language
# phrases (答案/Answer/最终回答) previously caused false-positive early exits.
_FINAL_MARKER = r"(?:FINAL|Final Answer)"


def _normalize_mcp_step(tool_name: str, args_raw: str = "") -> ToolStep | None:
    name = (tool_name or "").strip().strip('"').strip("'")
    args = (args_raw or "").strip()
    if not name:
        return None
    return ToolStep("mcp_tool_call", f"MCP: {name} {args}".strip())


def _parse_mcp_command(text: str) -> ToolStep | None:
    match = re.match(r"(?is)^\s*MCP\s*[:：]\s*(\S+)\s*(.*?)\s*$", text or "")
    if not match:
        return None
    return _normalize_mcp_step(match.group(1), match.group(2))


def _json_dumps(obj) -> str:
    return json.dumps(obj or {}, ensure_ascii=False)


def _json_tool_objects(text: str) -> list[dict]:
    raw = (text or "").strip()
    fenced = re.match(r"(?is)^```(?:json)?\s*([\s\S]*?)\s*```$", raw)
    if fenced:
        raw = fenced.group(1).strip()
    if not raw.startswith("{"):
        return []

    decoder = json.JSONDecoder()
    pos = 0
    out: list[dict] = []
    while pos < len(raw):
        while pos < len(raw) and raw[pos].isspace():
            pos += 1
        if pos >= len(raw):
            break
        if raw[pos] != "{":
            return []
        try:
            obj, pos = decoder.raw_decode(raw, pos)
        except (TypeError, ValueError):
            return []
        if not isinstance(obj, dict):
            return []
        out.append(obj)
    return out


def _tool_steps_from_json_object(obj: dict) -> list[ToolStep]:
    if not isinstance(obj, dict):
        return []
    name = str(obj.get("name") or obj.get("tool") or "").strip()
    args = obj.get("arguments", obj.get("args", obj.get("input", {})))
    if isinstance(args, str):
        try:
            args_obj = json.loads(args)
        except (TypeError, ValueError):
            args_obj = None
    elif isinstance(args, dict):
        args_obj = args
    else:
        args_obj = None

    lower_name = name.lower()
    if lower_name in ("batch", "tool_batch"):
        payload = args_obj if args_obj is not None else args
        return [_parse_batch_payload(payload)]

    if lower_name in ("mcp", "mcp_tool_call"):
        if not isinstance(args_obj, dict):
            return []
        tool_name = str(
            args_obj.get("tool_name") or args_obj.get("tool") or args_obj.get("name") or ""
        ).strip()
        if not tool_name:
            return []
        if "arguments" in args_obj:
            payload = args_obj.get("arguments")
        elif "args" in args_obj:
            payload = args_obj.get("args")
        elif "input" in args_obj:
            payload = args_obj.get("input")
        else:
            payload = {
                k: v for k, v in args_obj.items()
                if k not in {"tool_name", "tool", "name"}
            }
        payload_text = payload.strip() if isinstance(payload, str) else _json_dumps(payload)
        return [_normalize_mcp_step(tool_name, payload_text)] if tool_name else []

    if lower_name == "httpmcp_call":
        if not isinstance(args_obj, dict):
            return []
        tool_name = str(
            args_obj.get("tool_name") or args_obj.get("tool") or args_obj.get("name") or ""
        ).strip()
        if not tool_name:
            return []
        payload = args_obj.get("arguments", args_obj.get("args", args_obj.get("input", {})))
        payload_text = payload.strip() if isinstance(payload, str) else _json_dumps(payload)
        return [ToolStep("httpmcp_call", f"HTTPMCP: {tool_name} {payload_text}")]

    if lower_name in ("done", "final"):
        if isinstance(args_obj, dict):
            answer = str(args_obj.get("answer") or "").strip()
        else:
            answer = args.strip() if isinstance(args, str) else ""
        return [ToolStep("done", f"FINAL: {answer}", True)] if answer else []

    # Unknown JSON tool name → assume it is a real bound MCP tool name, matching
    # native tool_call normalization in llm_client._tool_call_to_step().
    if name:
        payload_text = args.strip() if isinstance(args, str) else _json_dumps(args_obj or args)
        return [_normalize_mcp_step(name, payload_text)]

    return []


def _parse_json_tool_object(text: str) -> list[ToolStep]:
    """Parse a whole-reply JSON tool object.

    Some non-native providers emit the OpenAI tool-call shape as assistant text,
    for example ``{"name":"MCP","arguments":{"tool_name":"x","id":"1"}}``.
    Only parse when the *entire* cleaned reply is a JSON object; embedded JSON in
    prose/docs must remain inert.
    """
    steps: list[ToolStep] = []
    for obj in _json_tool_objects(text):
        steps.extend(_tool_steps_from_json_object(obj))
    return steps


_BATCH_MODES = {"parallel", "sequence", "transaction"}
_BATCH_CHILD_REPLY_PREFIXES = (
    "SHELL:",
    "WRITE:",
    "READ:",
    "PATCH:",
    "MCP_ROUTE:",
    "MCP:",
    "SKILL_MD:",
    "RAG:",
    "RECALL:",
    "RUN_SKILL:",
    "HTTPMCP:",
    "SEARCH:",
)


def _batch_invalid(reason: str) -> ToolStep:
    payload = {"ok": False, "reason": reason}
    return ToolStep("tool_batch_invalid", f"BATCH_INVALID: {_json_dumps(payload)}", batch=payload)


def _batch_child_to_step(child: dict) -> tuple[ToolStep | None, str]:
    if not isinstance(child, dict):
        return None, "child_not_object"
    child_id = str(child.get("id") or "").strip()
    if not child_id:
        return None, "child_missing_id"

    reply = str(child.get("reply") or "").strip()
    if reply:
        if not reply.upper().startswith(_BATCH_CHILD_REPLY_PREFIXES):
            return None, f"child_invalid_reply:{child_id}"
        parsed = _parse_plain_steps(reply)
        executable = [s for s in parsed if not s.is_final and s.action != "plan"]
        if len(executable) != 1:
            return None, f"child_malformed_reply:{child_id}"
        return executable[0], ""

    action = str(child.get("action") or child.get("tool") or child.get("name") or "").strip()
    if not action:
        return None, f"child_missing_action:{child_id}"
    args = child.get("arguments", child.get("args", child.get("input", {})))
    if isinstance(args, str):
        try:
            args_obj = json.loads(args)
        except (TypeError, ValueError):
            args_obj = args
    else:
        args_obj = args
    steps = _tool_steps_from_json_object({"name": action, "arguments": args_obj})
    executable = [s for s in steps if not s.is_final and s.action != "plan"]
    if len(executable) != 1:
        return None, f"child_malformed_action:{child_id}"
    return executable[0], ""


def _parse_batch_payload(payload) -> ToolStep:
    if isinstance(payload, str):
        raw = payload.strip()
        fenced = re.match(r"(?is)^```(?:json)?\s*([\s\S]*?)\s*```$", raw)
        if fenced:
            raw = fenced.group(1).strip()
        try:
            payload = json.loads(raw)
        except (TypeError, ValueError):
            return _batch_invalid("invalid_json")
    if not isinstance(payload, dict):
        return _batch_invalid("payload_not_object")

    mode = str(payload.get("mode") or "").strip().lower()
    if mode not in _BATCH_MODES:
        return _batch_invalid("missing_or_invalid_mode")
    children = payload.get("children")
    if not isinstance(children, list) or not children:
        return _batch_invalid("missing_children")

    seen_ids: set[str] = set()
    normalized_children: list[dict] = []
    for child in children:
        if not isinstance(child, dict):
            return _batch_invalid("child_not_object")
        child_id = str(child.get("id") or "").strip()
        if not child_id:
            return _batch_invalid("child_missing_id")
        if child_id in seen_ids:
            return _batch_invalid(f"duplicate_child_id:{child_id}")
        seen_ids.add(child_id)
        step, err = _batch_child_to_step(child)
        if err or step is None:
            return _batch_invalid(err or f"child_malformed:{child_id}")
        normalized_children.append({
            "id": child_id,
            "action": step.action,
            "reply": step.reply,
        })

    batch = {"mode": mode, "children": normalized_children}
    batch_id = str(payload.get("batch_id") or payload.get("id") or "").strip()
    if batch_id:
        batch["id"] = batch_id[:80]
    return ToolStep("tool_batch", f"BATCH: {_json_dumps(batch)}", batch=batch)


_SHELL_META_RE = re.compile(
    r"</?\s*(?:think|tool_call|action|parameter)\b|"
    r"^(?:actually|wait[, ]|looking at|let me|i (?:think|notice|need)|"
    r"so i need|the previous turn)\b",
    re.IGNORECASE,
)


def _is_executable_shell_payload(value: str) -> bool:
    """Reject model self-talk/protocol leakage before it reaches /bin/sh."""
    cmd = str(value or "").strip()
    if not cmd or _SHELL_META_RE.search(cmd):
        return False
    first_line = cmd.splitlines()[0].strip()
    if not first_line or re.match(r"^[\u3400-\u9fff]", first_line):
        return False
    return bool(re.match(r"^(?:[A-Za-z0-9_./~$-]+|['\"])", first_line))


def _parse_plain_steps(text: str) -> list[ToolStep]:
    steps: list[ToolStep] = []
    patterns = [
        (rf"^\s*BATCH\s*[:：]\s*([\s\S]+?)(?=\n\s*(?:{_TOOL_BOUNDARY})|\Z)", "tool_batch", False),
        (rf"^\s*MCP_ROUTE\s*[:：]\s*(.+?)(?=\n\s*(?:{_TOOL_BOUNDARY})|\Z)", "mcp_route_request", False),
        (rf"^\s*MCP\s*[:：]\s*(\S+)\s*([\s\S]*?)(?=\n\s*(?:{_TOOL_BOUNDARY})|\Z)", "mcp_tool_call", False),
        (r"^\s*SKILL_MD\s*[:：]\s*(.+?)(?=\n|$)", "skill_read_md", False),
        (r"^\s*RUN_SKILL\s*[:：]\s*(.+?)(?=\n|$)", "skill_run_script", False),
        (
            rf"^\s*RAG\s*[:：]\s*(.+?)(?=\n\s*(?:{_TOOL_BOUNDARY})|\Z)",
            "rag_query",
            False,
        ),
        (
            rf"^\s*HTTPMCP\s*[:：]\s*(\S+)\s*([\s\S]*?)(?=\n\s*(?:{_TOOL_BOUNDARY})|\Z)",
            "httpmcp_call",
            False,
        ),
        (r"^\s*SEARCH\s*[:：]\s*(.+?)(?=\n|$)", "file_search", False),
        (
            rf"^\s*PLAN\s*[:：]\s*([\s\S]+?)(?=\n\s*(?:{_TOOL_BOUNDARY})|\Z)",
            "plan",
            False,
        ),
        (r"^\s*RECALL\s*[:：]\s*(.+?)(?=\n|$)", "recall", False),
        (
            rf"^\s*SHELL\s*[:：]\s*(.+?)(?=\n\s*(?:{_TOOL_BOUNDARY})|\Z)",
            "shell",
            False,
        ),
        (
            rf"^\s*WRITE\s*[:：]\s*(.+?)\n([\s\S]+?)(?=\n\s*(?:{_TOOL_BOUNDARY})|\Z)",
            "file_write",
            False,
        ),
        (r"^\s*READ\s*[:：]\s*(.+?)(?=\n|$)", "file_read", False),
        (
            rf"^\s*PATCH\s*[:：]\s*(.+?)\n([\s\S]+?)(?=\n\s*(?:{_TOOL_BOUNDARY})|\Z)",
            "file_search_replace",
            False,
        ),
        (
            rf"^\s*(?:[*#>-]+\s*|\d+[.)、]\s*)?{_FINAL_MARKER}\s*[:：]\s*([\s\S]+)",
            "done",
            True,
        ),
    ]
    for pat, action, is_final in patterns:
        for m in re.finditer(pat, text, re.MULTILINE | re.IGNORECASE):
            if action == "tool_batch":
                steps.append(_parse_batch_payload(m.group(1).strip()))
            elif action == "file_write":
                path = _norm_path(m.group(1))
                content = m.group(2).strip()
                if path and content:
                    steps.append(ToolStep(action, f"WRITE: {path}\n{content}", is_final))
            elif action == "file_search_replace":
                parts = m.group(0).split("\n", 2)
                if len(parts) >= 3:
                    path = _norm_path(parts[0].replace("PATCH:", "").strip())
                    steps.append(ToolStep(action, f"PATCH: {path}\n{parts[1]}\n{parts[2]}", is_final))
            elif action == "done":
                payload = m.group(1).strip().strip("*_`>").strip()
                if payload:
                    steps.append(ToolStep("done", f"FINAL: {payload}", True))
            elif action == "file_read":
                path = _norm_path(m.group(1))
                if path:
                    steps.append(ToolStep(action, f"READ: {path}", is_final))
            elif action == "shell":
                cmd = re.sub(r"\[[0-9A-Za-z~^]+(?:\[|$)", "", m.group(1)).strip()
                if _is_executable_shell_payload(cmd):
                    steps.append(ToolStep(action, f"SHELL: {cmd}", is_final))
            elif action == "mcp_tool_call":
                tool_name = m.group(1).strip()
                args_raw = (m.group(2) or "").strip()
                if tool_name:
                    steps.append(ToolStep(action, f"MCP: {tool_name} {args_raw}".strip(), is_final))
            elif action == "mcp_route_request":
                need = m.group(1).strip()
                if need:
                    steps.append(ToolStep(action, f"MCP_ROUTE: {need}", is_final))
            elif action == "skill_read_md":
                sid = m.group(1).strip()
                if sid:
                    steps.append(ToolStep(action, f"SKILL_MD: {sid}", is_final))
            elif action == "skill_run_script":
                name = m.group(1).strip()
                if name:
                    steps.append(ToolStep(action, f"RUN_SKILL: {name}", is_final))
            elif action == "rag_query":
                query = m.group(1).strip()
                if query:
                    steps.append(ToolStep(action, f"RAG: {query}", is_final))
            elif action == "httpmcp_call":
                tool_name = m.group(1).strip()
                args_raw = (m.group(2) or "").strip()
                if tool_name:
                    steps.append(ToolStep(action, f"HTTPMCP: {tool_name} {args_raw}".strip(), is_final))
            elif action == "file_search":
                query = m.group(1).strip()
                if query:
                    steps.append(ToolStep(action, f"SEARCH: {query}", is_final))
            elif action == "plan":
                content = m.group(1).strip()
                if content:
                    steps.append(ToolStep(action, f"PLAN: {content}", is_final))
            elif action == "recall":
                query = m.group(1).strip()
                if query:
                    steps.append(ToolStep(action, f"RECALL: {query}", is_final))
    return steps


def _parse_minimax_xml(text: str) -> list[ToolStep]:
    steps: list[ToolStep] = []
    chunks = re.split(r"\]<[^>]*>\[|<tool_call>", text)
    for chunk in chunks:
        chunk = chunk.strip()
        if not chunk:
            continue

        for m in re.finditer(
            r'<action\s+tool="(?:mcp|mcp_tool_call)"[^>]*>([\s\S]*?)(?:</action>|$)',
            chunk,
            re.IGNORECASE,
        ):
            block = m.group(1)
            name_m = re.search(
                r'<parameter\s+name="(?:name|tool|tool_name)"[^>]*>([\s\S]*?)(?:</parameter>|$)',
                block,
                re.IGNORECASE,
            )
            args_m = re.search(
                r'<parameter\s+name="(?:arguments|args|input)"[^>]*>([\s\S]*?)(?:</parameter>|$)',
                block,
                re.IGNORECASE,
            )
            step = _normalize_mcp_step(
                name_m.group(1) if name_m else "",
                args_m.group(1) if args_m else "",
            )
            if step:
                steps.append(step)

        for m in re.finditer(
            r'<action\s+tool="(shell|file_write|file_read|done)"[^>]*>\s*'
            r'(?:<parameter\s+name="(cmd|path|answer|content)"[^>]*>([\s\S]*?)(?:</parameter>|$))?',
            chunk,
            re.IGNORECASE,
        ):
            tool = m.group(1).lower()
            param = (m.group(2) or "").lower()
            value = (m.group(3) or "").strip().rstrip("]").rstrip(">")

            if tool == "shell" and value:
                mcp_step = _parse_mcp_command(value)
                if mcp_step:
                    steps.append(mcp_step)
                elif _is_executable_shell_payload(value):
                    steps.append(ToolStep("shell", f"SHELL: {value}"))
            elif tool == "file_read" and value:
                steps.append(ToolStep("file_read", f"READ: {_norm_path(value)}"))
            elif tool == "file_write" and param == "path" and value:
                content_m = re.search(
                    r'<parameter\s+name="content"[^>]*>([\s\S]*?)(?:</parameter>|$)',
                    chunk[m.end() : m.end() + 2000],
                    re.IGNORECASE,
                )
                content = content_m.group(1).strip() if content_m else ""
                path = _norm_path(value)
                if path and content:
                    steps.append(ToolStep("file_write", f"WRITE: {path}\n{content}"))
            elif tool == "done":
                answer_m = re.search(
                    r'<parameter\s+name="answer"[^>]*>([\s\S]*?)(?:</parameter>|$)',
                    chunk,
                    re.IGNORECASE,
                )
                ans = (answer_m.group(1) if answer_m else value).strip()
                if ans:
                    steps.append(ToolStep("done", f"FINAL: {ans}", True))

        for m in re.finditer(
            r'<action\s+tool="file_write"[^>]*>[\s\S]*?'
            r'<parameter\s+name="path"[^>]*>([^<]+)</parameter>[\s\S]*?'
            r'<parameter\s+name="content"[^>]*>([\s\S]*?)</parameter>',
            chunk,
            re.IGNORECASE,
        ):
            path = _norm_path(m.group(1))
            content = m.group(2).strip()
            if path and content:
                steps.append(ToolStep("file_write", f"WRITE: {path}\n{content}"))

    return steps


def _strip_reasoning_blocks(text: str) -> str:
    """Strip only reasoning blocks (<think>/<tool_call>) — the parse-safety concern.

    Token artifacts are left intact so executable content (WRITE body, rescued bare
    code) is never corrupted by display-only cleanup.
    """
    text = re.sub(r"<\s*think\b[^>]*>[\s\S]*?(?:<\s*/\s*think\s*>|$)", "", text, flags=re.I)
    text = re.sub(r"<\s*think\b[^>]*$", "", text, flags=re.I)
    text = re.sub(r"<tool_call>[\s\S]*?(?:</tool_call>|$)", "", text, flags=re.I)
    return text


def _strip_llm_artifacts(text: str) -> str:
    """Full artifact cleanup for user-facing display (reasoning blocks + leaked tokens)."""
    text = _strip_reasoning_blocks(text)
    # Leaked native tool-call special tokens (e.g. MiniMax <|tool_call|>) pollute
    # text-only replies; drop them so they neither mis-parse nor leak to the UI.
    text = strip_leaked_tool_tokens(text)
    text = re.sub(r"\]<[^>]*>\[?", "", text)
    text = re.sub(r"\x1b\[[0-9;?]*[ -/]*[@-~]", "", text)
    return text.strip()


def extract_final_payload(text: str) -> str:
    """Return only the payload after the last line-level FINAL marker."""
    source = text or ""
    matches = list(re.finditer(rf"(?im)^\s*{_FINAL_MARKER}\s*[:：]\s*", source))
    if not matches:
        return source
    return source[matches[-1].end() :].strip()


def is_final_reply(reply: str) -> bool:
    """True if the reply is a FINAL (task complete)."""
    return bool(re.search(
        rf"(?im)^\s*(?:[*#>-]+\s*|\d+[.)、]\s*)?{_FINAL_MARKER}\s*[:：]",
        reply or "",
    ))


def clean_display_text(text: str) -> str:
    """Strip protocol markers from display text for user-facing output."""
    t = _strip_llm_artifacts(text or "")
    t = re.sub(r"(?<![A-Za-z/])(SHELL:|READ:|PATCH:|THINK:|MCP:|PLAN:|RECALL:|HTTPMCP:|SEARCH:)\s*[^\n]+", "", t)
    t = re.sub(
        r"(?<![A-Za-z/])WRITE:\s*\S[^\n]*(?:\n(?!(?:SHELL:|WRITE:|READ:|PATCH:|FINAL:|THINK:|MCP:|HTTPMCP:|SEARCH:))[^\n]*)*",
        "",
        t,
    )
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()


def clean_final_answer(text: str) -> str:
    """User-facing assistant content: strip protocol markers and leaked model monologue."""
    t = clean_display_text(extract_final_payload(text or ""))
    t = re.sub(rf"(?im)^\s*{_FINAL_MARKER}\s*[:：]\s*", "", t).strip()
    t = re.sub(r"</?think>", "", t, flags=re.I)
    t = re.sub(
        r"(?is)(?:^|\n)\s*(?:"
        r"Let me write(?:\s+it)?(?:\s+now)?\.?"
        r"|I haven'?t yet[^\n]*"
        r"|the platform seems to think[^\n]*"
        r"|tool budget[^\n]*"
        r"|I should now[^\n]*"
        r")",
        "",
        t,
    )
    return t.strip()


_SUBTASK_LINE_RE = re.compile(
    r"^\s*(?:\[(?P<box>[ xX✓✅])\]\s*|[-*]\s+\[(?P<mbox>[ xX✓✅])\]\s*|"
    r"(?:\d+)[.)、]\s*|[-*]\s+)(?P<text>.+?)\s*$"
)
_SUBTASK_DONE = set("xX✓✅")


def parse_subtasks(plan_text: str) -> list[dict]:
    """Extract an ordered subtask list from a PLAN body.

    Supports `[x]`/`[ ]` checkboxes (with or without a leading `-`/`*`), numbered
    lines (`1.`/`2)`/`1、`), and plain `-`/`*` bullets. A checked box marks
    ``status == "done"``; everything else is ``"pending"``. Returns ``[]`` when no
    structured subtask line is found (free-text PLAN).
    """
    out: list[dict] = []
    for ln in (plan_text or "").splitlines():
        m = _SUBTASK_LINE_RE.match(ln)
        if not m:
            continue
        text = (m.group("text") or "").strip()
        if not text:
            continue
        box = (m.group("box") or m.group("mbox") or "").strip()
        status = "done" if box in _SUBTASK_DONE else "pending"
        out.append({"text": text, "status": status})
    return out


def extract_tool_steps(reply: str) -> list[ToolStep]:
    if not reply or not reply.strip():
        return []
    cleaned = strip_leaked_tool_tokens(_strip_reasoning_blocks(reply))
    merged: list[ToolStep] = []
    seen: set[tuple[str, str]] = set()
    # XML is parsed from the original envelope. Plain protocol is parsed only
    # after removing think/tool_call blocks so examples and hidden reasoning
    # cannot become executable actions.
    for step in (
        _parse_minimax_xml(reply)
        + _parse_json_tool_object(cleaned)
        + _parse_plain_steps(cleaned)
    ):
        key = (step.action, step.reply[:120])
        if key in seen:
            continue
        seen.add(key)
        merged.append(step)
    # 模型把 FINAL 埋在 <think>/<tool_call> 里被 _strip_reasoning_blocks 剥掉 →
    # 回扫原始 reply，找回行首 FINAL 标记，避免被判成纯文本死循环。
    if not any(s.is_final for s in merged):
        fm = re.search(rf"(?im)^\s*{_FINAL_MARKER}\s*[:：]\s*", reply)
        if fm:
            payload = re.sub(
                r"</?\s*(?:think|tool_call)\s*>", "", reply[fm.end():], flags=re.I,
            )
            payload = _strip_llm_artifacts(payload).strip().strip("*_`>").strip()
            if payload:
                merged.append(ToolStep("done", f"FINAL: {payload}", True))
    return merged
