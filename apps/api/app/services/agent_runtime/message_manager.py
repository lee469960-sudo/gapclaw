"""MessageManager — message trimming, progress maintenance, and path extraction.

Manages the LLM conversation messages array during long agent runs:
trimming oversized tool results, maintaining the progress block, and
extracting file paths from agent output.
"""

from __future__ import annotations

import re

from app.services.agent_runtime.loop_state import AgentLoopState
from app.services.agent_runtime.utils import (
    _IMPORTANT_LINE_RE,
    _KEEP_RECENT_TOOL_MSGS,
    _KEEP_RECENT_TOOL_MSGS_EXPORT,
    _OLD_TOOL_MSG_CAP,
    _OLD_TOOL_MSG_CAP_EXPORT,
    _PATH_IN_TEXT_RE,
    _PROGRESS_MAX_LINES,
    _TRIM_TOOL_MSG_EVERY,
)


class MessageManager:
    """Manages the messages array during agent loop execution.

    All methods are static or take explicit parameters — no hidden state.
    """

    # ---- Tool message trimming ----

    @staticmethod
    def shrink_tool_message(content: str, cap: int) -> str:
        """Truncate tool result but keep lines with checkpoint / file paths."""
        _content = content or ""
        if len(_content) <= cap:
            return _content
        lines = _content.splitlines()
        keep = [ln for ln in lines if _IMPORTANT_LINE_RE.search(ln)]
        head = _content[: max(80, cap // 3)].rstrip()
        important = "\n".join(keep[:20])
        merged = head
        if important and important not in merged:
            merged = f"{merged}\n…\n{important}"
        if len(merged) > cap:
            if important:
                budget = max(40, cap - len(important) - 20)
                merged = f"{head[:budget]}\n…\n{important}"[:cap]
            else:
                merged = _content[: max(0, cap - 12)] + "\n…(已截断)"
        elif not merged.endswith("…(已截断)") and len(_content) > len(merged):
            merged = merged.rstrip() + "\n…(已截断)"
        return merged

    @staticmethod
    def trim_old_tool_messages(
        messages: list[dict],
        *,
        keep_recent: int = _KEEP_RECENT_TOOL_MSGS,
        cap: int = _OLD_TOOL_MSG_CAP,
    ) -> None:
        """In-place shrink older tool-result user messages during long runs."""
        tool_idxs = [
            i
            for i, m in enumerate(messages)
            if m.get("role") == "user"
            and str(m.get("content") or "").startswith("工具结果:")
        ]
        if len(tool_idxs) <= keep_recent:
            return
        for i in tool_idxs[:-keep_recent]:
            content = messages[i].get("content") or ""
            if len(content) > cap:
                messages[i]["content"] = MessageManager.shrink_tool_message(
                    content, cap
                )

    @staticmethod
    def trim_for_export(messages: list[dict]) -> None:
        """Trim tool messages with export-specific limits."""
        MessageManager.trim_old_tool_messages(
            messages,
            keep_recent=_KEEP_RECENT_TOOL_MSGS_EXPORT,
            cap=_OLD_TOOL_MSG_CAP_EXPORT,
        )

    @staticmethod
    def trim_for_generic(messages: list[dict]) -> None:
        """Trim tool messages with generic ReAct limits."""
        MessageManager.trim_old_tool_messages(
            messages,
            keep_recent=_KEEP_RECENT_TOOL_MSGS,
            cap=_OLD_TOOL_MSG_CAP,
        )

    # ---- Progress messages ----

    @staticmethod
    def progress_block_text(lines: list[str]) -> str:
        """Build the progress block text from accumulated lines."""
        body = "\n".join(lines[-_PROGRESS_MAX_LINES:]) if lines else "(暂无)"
        return (
            "【本轮进度】以下路径在长跑中保留，勿丢失："
            "中间产物在 task/，仅最终交付文件写到当前目录。\n"
            f"{body}"
        )

    @staticmethod
    def upsert_progress_message(
        messages: list[dict], progress_lines: list[str]
    ) -> None:
        """Insert or update the single progress message in the messages array."""
        text = MessageManager.progress_block_text(progress_lines)
        for m in messages:
            if m.get("role") == "system" and str(m.get("content") or "").startswith(
                "【本轮进度】"
            ):
                m["content"] = text
                return
        insert_at = 0
        for i, m in enumerate(messages):
            if m.get("role") == "system":
                insert_at = i + 1
            else:
                break
        messages.insert(insert_at, {"role": "system", "content": text})

    @staticmethod
    def append_progress(progress_lines: list[str], line: str) -> None:
        """Append a line to the progress log, avoiding dupes."""
        line = (line or "").strip()
        if not line:
            return
        if progress_lines and progress_lines[-1] == line:
            return
        progress_lines.append(line)
        if len(progress_lines) > _PROGRESS_MAX_LINES:
            del progress_lines[: -_PROGRESS_MAX_LINES]

    @staticmethod
    def append_progress_to_state(state: AgentLoopState, line: str) -> None:
        """Append a progress line using AgentLoopState."""
        MessageManager.append_progress(state.progress_lines, line)

    @staticmethod
    def sync_progress_to_messages(
        state: AgentLoopState, messages: list[dict]
    ) -> None:
        """Sync AgentLoopState progress lines into the messages array."""
        MessageManager.upsert_progress_message(messages, state.progress_lines)

    # ---- Path extraction ----

    @staticmethod
    def extract_named_paths(*texts: str) -> list[str]:
        """Extract file/dir paths from agent output texts."""
        found: list[str] = []
        seen: set[str] = set()
        for text in texts:
            for m in _PATH_IN_TEXT_RE.finditer(text or ""):
                p = m.group(0).strip().strip("`").lstrip("/")
                if p.startswith("workplace/"):
                    p = p[len("workplace/"):]
                if not p or p in seen:
                    continue
                seen.add(p)
                found.append(p)
        return found

    @staticmethod
    def extract_paths_from_state(state: AgentLoopState, *extra_texts: str) -> list[str]:
        """Extract paths from the last reply and any extra texts."""
        texts = [state.last_reply, *extra_texts]
        return MessageManager.extract_named_paths(*texts)
