"""Normalize a finished agent run into the six-field output envelope.

The model keeps its tool protocol and FINAL text. This module runs at the
write boundary and decides status, type, the user-facing Markdown, and the
small structured payload.
"""

from __future__ import annotations

import json
import re

OUTPUT_VERSION = "1.0"
OUTPUT_KEYS = ("version", "status", "type", "message", "data", "actions")

_FENCE = re.compile(r"```[\s\S]*?(?:```|$)", re.M)
_TOOL_LINE = re.compile(
    r"^(?:MCP:|TOOL:|ACTION:|PLAN:|tool_call\b|function_call\b|Observation:|观察：)",
    re.I,
)
_CODE_LINE = re.compile(
    r"^(?:def |class |import |from |return |yield |async def |await |"
    r"if |elif |else:|for |while |try:|except |with |"
    r"const |let |var |function |public |private |protected |package |#include |"
    r"SELECT |INSERT |UPDATE |DELETE |CREATE |"
    r"\{|\}|\[|\]|</?[A-Za-z])"
)
_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_\-]{0,40}$")
_BARE_STOP = {"[已停止]", "任务已取消", "已停止", "（已停止）"}
_MAX_LABEL = 40
_MAX_PATH = 120
_INTERNAL_ARTIFACT = re.compile(
    r"已缓存\s*MCP\s*结果|mcp_result_\d+\.json|shell_result_\d+\.|read_result_\d+\.",
    re.I,
)
_INTERMEDIATE_HEADER = re.compile(
    r"^(?:[-*]\s*)?(?:已产生的中间产物|工作区文件（非最终交付）)\s*[:：]?\s*$"
)
_RAW_OUTPUT_HEADER = re.compile(r"^(?:[-*]\s*)?本轮最后的原始输出\s*[:：]?\s*$")


def build_unified_output(reason: str, message: str, structured: dict | None = None) -> dict:
    """Return an envelope with exactly the six contract fields."""
    data = sanitize_data(structured)
    kind = (reason or "complete").strip() or "complete"
    text = (message or "").strip()
    if kind == "complete":
        status = "ok"
        output_type = "data" if data else "answer"
    elif kind in {"budget", "cancel", "incomplete"}:
        status = "partial"
        output_type = "answer"
        text = _partial_message(text, structured)
    elif kind == "need_input":
        status = "need_input"
        output_type = "clarify"
    elif kind == "error":
        status = "error"
        output_type = "error"
    else:
        status = "error"
        output_type = "error"
        text = text or "运行失败"
    if not text:
        text = "（本轮未产生文字回复）"
    return {
        "version": OUTPUT_VERSION,
        "status": status,
        "type": output_type,
        "message": text,
        "data": data,
        "actions": [],
    }


def sanitize_data(structured: dict | None) -> dict:
    """Keep counts, short field names, paths, and statuses. Drop dumps."""
    if not isinstance(structured, dict):
        return {}
    out: dict = {}
    for key, value in structured.items():
        if not isinstance(key, str) or not _KEY.match(key):
            continue
        cleaned = _sanitize_value(value)
        if cleaned is None:
            continue
        if cleaned == {} or cleaned == []:
            continue
        out[key] = cleaned
    return out


def _is_envelope(output) -> bool:
    return (
        isinstance(output, dict)
        and set(output.keys()) == set(OUTPUT_KEYS)
        and isinstance(output.get("message"), str)
        and output.get("message").strip()
    )


def group_assistant_record(reply: str, output: dict | None = None) -> tuple[str, dict]:
    """Group transcript uses the same envelope as the agent session."""
    envelope = output if _is_envelope(output) else build_unified_output("complete", reply or "")
    return envelope["message"], {"output": envelope}


def visible_saved_text(content: str, meta_raw: str | None) -> str:
    """Prefer the stored envelope message when a row has one."""
    try:
        meta = json.loads(meta_raw or "{}")
    except json.JSONDecodeError:
        meta = {}
    output = meta.get("output") if isinstance(meta, dict) else None
    if _is_envelope(output):
        return output["message"]
    return content or ""


def visible_reply(text: str) -> str:
    """Channel text is the Markdown message, even if a caller passed the envelope."""
    raw = (text or "").strip()
    if raw.startswith("{") and raw.endswith("}"):
        try:
            obj = json.loads(raw)
        except Exception:
            return text or ""
        if (
            isinstance(obj, dict)
            and set(obj.keys()) == set(OUTPUT_KEYS)
            and isinstance(obj.get("message"), str)
        ):
            return obj["message"]
    return text or ""


def _is_internal_artifact(text: str) -> bool:
    return bool(_INTERNAL_ARTIFACT.search(text or ""))


def _visible_partial_line(line: str) -> str:
    """Drop cached tool dumps from a user-facing partial line."""
    stripped = (line or "").strip()
    if not stripped or _INTERMEDIATE_HEADER.match(stripped) or _RAW_OUTPUT_HEADER.match(stripped):
        return ""
    if not _is_internal_artifact(stripped):
        return stripped
    parts = re.split(r"[；、]", stripped)
    if len(parts) == 1:
        return ""
    kept = [part.strip(" -*`") for part in parts if part.strip() and not _is_internal_artifact(part)]
    if not kept:
        return ""
    prefix = "- " if stripped.startswith("- ") else ""
    return prefix + "；".join(kept)


def _keep_short_value(text: str) -> bool:
    if not text or "\n" in text or "```" in text or " " in text:
        return False
    if _is_internal_artifact(text):
        return False
    if text.startswith("{") or text.startswith("def ") or text.startswith("import "):
        return False
    if "/" in text or "." in text:
        return len(text) <= _MAX_PATH
    return len(text) <= _MAX_LABEL


def _sanitize_value(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return value
    if isinstance(value, str):
        text = value.strip()
        if not _keep_short_value(text):
            return None
        return text
    if isinstance(value, list):
        items = []
        for item in value[:20]:
            cleaned = _sanitize_value(item)
            if cleaned is None or isinstance(cleaned, (dict, list)):
                continue
            items.append(cleaned)
        return items or None
    if isinstance(value, dict):
        nested = sanitize_data(value)
        return nested or None
    return None


def _partial_message(text: str, structured: dict | None) -> str:
    cleaned = _clean_partial_text(text)
    if cleaned:
        return cleaned
    return _partial_fallback(structured)


def _clean_partial_text(text: str) -> str:
    without_fences = _FENCE.sub("\n", text or "")
    kept: list[str] = []
    skipping_raw = False
    for line in without_fences.splitlines():
        stripped = line.strip()
        if _RAW_OUTPUT_HEADER.match(stripped):
            skipping_raw = True
            continue
        if skipping_raw:
            continue
        if not stripped:
            if kept and kept[-1] != "":
                kept.append("")
            continue
        visible = _visible_partial_line(stripped)
        if not visible:
            continue
        if visible in _BARE_STOP or _TOOL_LINE.match(visible) or _CODE_LINE.match(visible):
            continue
        if len(visible) > 400:
            continue
        kept.append(visible)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(kept)).strip()


def _partial_fallback(structured: dict | None) -> str:
    data = structured if isinstance(structured, dict) else {}
    lines = ["已停止，当前结果不完整。"]
    progress = [
        _visible_partial_line(str(item))
        for item in (data.get("progress") or [])
        if _visible_partial_line(str(item))
        and not _CODE_LINE.match(_visible_partial_line(str(item)))
        and "```" not in str(item)
    ]
    paths = [
        str(item).strip()
        for item in (data.get("saved_paths") or [])
        if str(item).strip() and not _is_internal_artifact(str(item))
    ]
    pending = [str(item).strip() for item in (data.get("pending") or []) if str(item).strip()]
    if progress:
        lines.append("已做到：")
        lines.extend(f"- {item[:200]}" for item in progress[:8])
    if paths:
        lines.append("已保存：")
        lines.extend(f"- `{item[:200]}`" for item in paths[:8])
    if pending:
        lines.append("尚未完成：")
        lines.extend(f"- {item[:200]}" for item in pending[:8])
    if len(lines) == 1:
        lines.append("本轮没有可展示的完成项。")
    return "\n".join(lines)
