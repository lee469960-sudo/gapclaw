"""Parse LLM tool invocations (plain WRITE:/SHELL: and MiniMax XML tool_call)."""

import re
from dataclasses import dataclass


@dataclass
class ToolStep:
    action: str
    reply: str
    is_final: bool = False


def _norm_path(path: str) -> str:
    p = (path or "").strip().strip('"').strip("'")
    # Drop trailing shell redirections glued onto paths by the model
    p = re.split(r"(?:2>&1|2>|1>|>>|[|;&<>]|&&|\|\|)", p, maxsplit=1)[0].strip()
    p = p.replace("\\", "/")
    if p.startswith("/workplace/"):
        p = p[len("/workplace/") :]
    elif p.startswith("workplace/"):
        p = p[len("workplace/") :]
    return p.strip("/")


_TOOL_BOUNDARY = (
    r"SHELL:|WRITE:|READ:|PATCH:|FINAL:|THINK:|MCP:|HTTPMCP:|SKILL_MD:|RAG:"
)


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
        (r"^\s*MCP\s*[:：]\s*(\S+)\s*(.*)$", "mcp_tool_call", False),
        (r"^\s*HTTPMCP\s*[:：]\s*(\S+)\s*(.*)$", "httpmcp_call", False),
        (r"^\s*SKILL_MD\s*[:：]\s*(.+?)(?=\n|$)", "skill_read_md", False),
        (
            rf"^\s*RAG\s*[:：]\s*(.+?)(?=\n\s*(?:{_TOOL_BOUNDARY})|\Z)",
            "rag_query",
            False,
        ),
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
        (r"^\s*FINAL\s*[:：]\s*([\s\S]+)", "done", True),
    ]
    for pat, action, is_final in patterns:
        for m in re.finditer(pat, text, re.MULTILINE | re.IGNORECASE):
            if action == "file_write":
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
                steps.append(ToolStep("done", f"FINAL: {m.group(1).strip()}", True))
            elif action == "file_read":
                path = _norm_path(m.group(1))
                if path:
                    steps.append(ToolStep(action, f"READ: {path}", is_final))
            elif action == "shell":
                cmd = re.sub(r"\[[0-9A-Za-z~^]+(?:\[|$)", "", m.group(1)).strip()
                if _is_executable_shell_payload(cmd):
                    steps.append(ToolStep(action, f"SHELL: {cmd}", is_final))
            elif action in ("mcp_tool_call", "httpmcp_call"):
                tool_name = m.group(1).strip()
                args_raw = (m.group(2) or "").strip()
                if tool_name:
                    prefix = "MCP:" if action == "mcp_tool_call" else "HTTPMCP:"
                    steps.append(ToolStep(action, f"{prefix} {tool_name} {args_raw}".strip(), is_final))
            elif action == "skill_read_md":
                sid = m.group(1).strip()
                if sid:
                    steps.append(ToolStep(action, f"SKILL_MD: {sid}", is_final))
            elif action == "rag_query":
                query = m.group(1).strip()
                if query:
                    steps.append(ToolStep(action, f"RAG: {query}", is_final))
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


def _strip_llm_artifacts(text: str) -> str:
    text = re.sub(r"<think>[\s\S]*?</think>", "", text, flags=re.I)
    text = re.sub(r"<\s*think\s*>[\s\S]*?<\s*/\s*think\s*>", "", text, flags=re.I)
    text = re.sub(r"<tool_call>[\s\S]*?(?:</tool_call>|$)", "", text, flags=re.I)
    text = re.sub(r"\]<[^>]*>\[?", "", text)
    text = re.sub(r"\[[0-9A-Za-z~^]+(?:\[|$)", "", text)
    text = re.sub(r"\x1b\[[0-9;?]*[ -/]*[@-~]", "", text)
    return text.strip()


def extract_final_payload(text: str) -> str:
    """Return only the payload after the last line-level FINAL marker."""
    source = text or ""
    matches = list(re.finditer(r"(?im)^\s*FINAL\s*[:：]\s*", source))
    if not matches:
        return source
    return source[matches[-1].end() :].strip()


def extract_tool_steps(reply: str) -> list[ToolStep]:
    if not reply or not reply.strip():
        return []
    cleaned = _strip_llm_artifacts(reply)
    merged: list[ToolStep] = []
    seen: set[tuple[str, str]] = set()
    # XML is parsed from the original envelope. Plain protocol is parsed only
    # after removing think/tool_call blocks so examples and hidden reasoning
    # cannot become executable actions.
    for step in _parse_minimax_xml(reply) + _parse_plain_steps(cleaned):
        key = (step.action, step.reply[:120])
        if key in seen:
            continue
        seen.add(key)
        merged.append(step)
    return merged


def detect_action_from_reply(reply: str, allowed: list[str]) -> tuple[str | None, str]:
    """Return (action, normalized_reply) for first executable step."""
    action_map = {
        "shell": "shell",
        "file_read": "file_read",
        "file_write": "file_write",
        "file_search_replace": "file_search_replace",
        "mcp_tool_call": "mcp_tool_call",
        "httpmcp_call": "httpmcp_call",
        "skill_read_md": "skill_read_md",
        "rag_query": "rag_query",
        "done": "done",
    }
    for step in extract_tool_steps(reply):
        act = action_map.get(step.action)
        if step.is_final:
            return "done", step.reply
        if act and act in allowed:
            return act, step.reply
    reply = reply.strip()
    for prefix, action in [
        ("MCP:", "mcp_tool_call"),
        ("HTTPMCP:", "httpmcp_call"),
        ("SKILL_MD:", "skill_read_md"),
        ("RAG:", "rag_query"),
        ("SHELL:", "shell"),
        ("READ:", "file_read"),
        ("WRITE:", "file_write"),
        ("PATCH:", "file_search_replace"),
        ("FINAL:", "done"),
    ]:
        if reply.startswith(prefix) and action in allowed:
            return action, reply
        m = re.search(rf"(?<![A-Za-z/]){re.escape(prefix)}\s*", reply)
        if m and action in allowed:
            return action, reply[m.start():].strip()
    return None, reply
