"""ContextManager — structured context layer management for LLM conversations.

Replaces the simple messages.append() pattern with typed layer operations.
Each layer has a known position range, so updates (like replacing coach hints
or observation blocks) are O(1) — no scanning the full messages array.

Layers (ordered from most stable to most volatile):
  1. IMMUTABLE_BASE   — System prompt, agent identity, long-term memory
  2. TOOLS_CATALOG    — Available tools (injected by ToolRouter)
  3. PROGRESS_BLOCK   — Running progress (upserted, not appended)
  4. COACH_HINT       — Dynamic coaching hints (replaced each iteration)
  5. HISTORY          — User/assistant message history (appended)
  6. TOOL_RESULT      — Tool execution results (appended, periodically trimmed)
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Default limits
_DEFAULT_TOOL_RESULT_CLIP = 6000  # Max chars for a single tool result in context
_DEFAULT_KEEP_RECENT_TOOL_MSGS = 12  # Keep this many recent tool messages un-trimmed
_DEFAULT_OLD_TOOL_MSG_CAP = 500  # Cap older tool messages to this many chars


@dataclass
class _Layer:
    """Internal bookkeeping for a context layer's position in the messages array."""

    name: str
    start: int = -1  # Index of first message in this layer, -1 = not present
    end: int = -1  # Index after last message, -1 = not present

    @property
    def present(self) -> bool:
        return self.start >= 0 and self.end > self.start

    @property
    def count(self) -> int:
        return self.end - self.start if self.present else 0


class ContextManager:
    """Manages structured context layers for the agent's LLM conversation.

    Usage::

        ctx = ContextManager()
        ctx.set_base(system_prompt=system_prompt)
        ctx.set_tools_catalog(tools_block)
        ctx.push_user_message(user_message)
        # ... in loop:
        ctx.push_coach_hint("try using offset=100")
        ctx.push_tool_result("工具结果:\\n...")
        ctx.trim_tool_results()
    """

    def __init__(self, messages: list[dict] | None = None):
        self._messages: list[dict] = messages or []
        self._layers: dict[str, _Layer] = {
            "base": _Layer("base"),
            "tools_catalog": _Layer("tools_catalog"),
            "progress_block": _Layer("progress_block"),
            "coach_hint": _Layer("coach_hint"),
            "history": _Layer("history"),
            "tool_result": _Layer("tool_result"),
        }
        self._tool_result_indices: list[int] = []  # Fast lookup for trimming
        self._version: int = 0

    # -- Read access --

    @property
    def messages(self) -> list[dict]:
        """Return the current messages array for LLM chat_completion()."""
        return self._messages

    @property
    def version(self) -> int:
        """Monotonic version counter, bumped on every structural change."""
        return self._version

    # -- Layer insertion helpers --

    def _append_system(self, content: str, layer_name: str) -> int:
        """Append a system message and track it under `layer_name`. Update range."""
        idx = len(self._messages)
        self._messages.append({"role": "system", "content": content})
        layer = self._layers[layer_name]
        if not layer.present:
            layer.start = idx
        layer.end = idx + 1
        self._version += 1
        return idx

    def _replace_layer(self, layer_name: str, content: str) -> None:
        """Replace an existing layer's message(s) with a single new message.

        If the layer is not present, appends. If present with multiple messages,
        removes all but keeps the slot at the original start position.
        """
        layer = self._layers[layer_name]
        if not layer.present:
            self._append_system(content, layer_name)
            return
        # Replace first message with new content, remove rest
        self._messages[layer.start] = {"role": "system", "content": content}
        count = layer.count
        if count > 1:
            del self._messages[layer.start + 1 : layer.end]
            # Adjust all layers that come after
            shift = count - 1
            for name, l in self._layers.items():
                if l.present and l.start >= layer.end:
                    l.start -= shift
                    l.end -= shift
        layer.end = layer.start + 1
        self._version += 1

    def _append_user(self, content: str) -> int:
        """Append a user message and track it as a tool result."""
        idx = len(self._messages)
        self._messages.append({"role": "user", "content": content})
        layer = self._layers["tool_result"]
        if not layer.present:
            layer.start = idx
        layer.end = idx + 1
        self._tool_result_indices.append(idx)
        self._version += 1
        return idx

    def _append_assistant(self, content: str) -> int:
        """Append an assistant message to the history layer."""
        idx = len(self._messages)
        self._messages.append({"role": "assistant", "content": content})
        layer = self._layers["history"]
        if not layer.present:
            layer.start = idx
        layer.end = idx + 1
        self._version += 1
        return idx

    # -- Layer 1: Immutable base --

    def set_base(self, *, system_prompt: str, memory_text: str = "", summary_text: str = "") -> None:
        """Set the immutable base layer (system prompt + memory + summary).

        Must be called once before the main loop. All three components are
        folded into a single system message to save context slots.
        """
        parts = [system_prompt.strip()]
        if memory_text and memory_text.strip():
            parts.append(f"\n---\n## 长期记忆\n{memory_text.strip()}")
        if summary_text and summary_text.strip():
            parts.append(f"\n---\n## 会话滚动总结\n{summary_text.strip()}")
        content = "\n".join(parts)
        self._replace_layer("base", content)

    # -- Layer 4: Tools catalog --

    def set_tools_catalog(self, tools_block: str) -> None:
        """Set the tools catalog layer. Call once before the main loop.

        The tools_block should be a compact description built by ToolRouter.
        """
        if not tools_block.strip():
            return
        self._append_system(f"【可用工具】\n{tools_block.strip()}", "tools_catalog")

    # -- Layer 5: Progress block --

    def set_progress_block(self, lines: list[str]) -> None:
        """Upsert the progress block with accumulated progress lines."""
        body = "\n".join(lines[-80:]) if lines else "(暂无)"
        content = (
            "【本轮进度】以下路径在长跑中保留，勿丢失："
            "中间产物在 task/，仅最终交付文件写到当前目录。\n"
            f"{body}"
        )
        self._replace_layer("progress_block", content)

    # -- Layer 6: Coach hint (dynamic, replaced each iteration) --

    def push_coach_hint(self, hint: str) -> None:
        """Replace the coach hint with a new hint for the current iteration.

        Coach hints are short-lived guidance injected into the system context.
        They are replaced (not appended) each time this is called.
        """
        if not hint.strip():
            return
        self._replace_layer("coach_hint", hint.strip())

    # -- Layer 7: History --

    def push_user_message(self, content: str) -> None:
        """Append the current user message to history."""
        idx = len(self._messages)
        self._messages.append({"role": "user", "content": content})
        layer = self._layers["history"]
        if not layer.present:
            layer.start = idx
        layer.end = idx + 1
        self._version += 1

    def push_assistant_reply(self, content: str) -> None:
        """Append an assistant reply to history (after LLM call)."""
        self._append_assistant(content)

    # -- Layer 8: Tool results --

    def push_tool_result(
        self,
        tool_output: str,
        *,
        clip: int = _DEFAULT_TOOL_RESULT_CLIP,
        action: str = "",
    ) -> int:
        """Append a tool result observation.

        Large results are clipped to `clip` chars. Paths and key lines are
        preserved. Returns the message index.
        """
        content = tool_output or ""
        label_map = {
            "mcp_tool_call": "MCP 工具结果",
            "shell": "Shell 结果",
            "file_write": "文件写入结果",
            "file_read": "文件读取结果",
        }
        label = label_map.get(action, "工具结果")
        if len(content) > clip:
            # Keep head + important lines
            lines = content.splitlines()
            important_pattern = re.compile(
                r"(checkpoint|saved|written|created|error|Error|失败|成功|task/)"
            )
            keep_lines = [ln for ln in lines if important_pattern.search(ln)]
            head = content[: max(80, clip // 3)].rstrip()
            important = "\n".join(keep_lines[:20])
            if important and important not in head:
                summary = f"{head}\n…\n{important}"
            else:
                summary = head
            if len(summary) > clip:
                summary = summary[: clip - 20] + "\n…(已截断)"
            content = summary + f"\n…(原始输出 {len(tool_output)} 字符，已截断)"

        wrapped = f"{label}:\n{content.strip()}"
        return self._append_user(wrapped)

    # -- Trimming --

    def trim_tool_results(
        self,
        *,
        keep_recent: int = _DEFAULT_KEEP_RECENT_TOOL_MSGS,
        cap: int = _DEFAULT_OLD_TOOL_MSG_CAP,
    ) -> None:
        """Shrink older tool-result messages to keep context small.

        The most recent `keep_recent` tool messages are kept intact;
        older ones are capped to `cap` characters.
        """
        indices = self._tool_result_indices
        if len(indices) <= keep_recent:
            return
        for i in indices[:-keep_recent]:
            content = self._messages[i].get("content") or ""
            if len(content) > cap:
                self._messages[i]["content"] = _shrink_content(content, cap)
        self._version += 1


def _shrink_content(content: str, cap: int) -> str:
    """Shrink a tool result message to fit within `cap` chars."""
    if len(content) <= cap:
        return content
    lines = content.splitlines()
    important_pattern = re.compile(r"(checkpoint|saved|written|created|error|Error|失败|成功|task/)")
    keep = [ln for ln in lines if important_pattern.search(ln)]
    head = content[: max(80, cap // 3)].rstrip()
    important = "\n".join(keep[:20])
    merged = head
    if important and important not in merged:
        merged = f"{merged}\n…\n{important}"
    if len(merged) > cap:
        budget = max(40, cap - len(important) - 20) if important else cap - 12
        merged = f"{content[:budget]}\n…(已截断)"
        return merged[:cap]
    if not merged.endswith("…(已截断)") and len(content) > len(merged):
        merged = merged.rstrip() + "\n…(已截断)"
    return merged
