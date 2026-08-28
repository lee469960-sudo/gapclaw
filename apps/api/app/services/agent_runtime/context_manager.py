"""ContextManager — structured context layer management for LLM conversations.

Replaces the simple messages.append() pattern with typed layer operations.
Each layer has a known position range, so updates (like replacing coach hints
or observation blocks) are O(1) — no scanning the full messages array.

Layers (ordered from most stable to most volatile):
  1. IMMUTABLE_BASE   — System prompt, agent identity, long-term memory
  2. TOOLS_CATALOG    — Available tools (built by SystemPromptBuilder)
  3. PROGRESS_BLOCK   — Running progress (upserted, not appended)
  4. COACH_HINT       — Dynamic coaching hints (replaced each iteration)
  5. HISTORY          — User/assistant message history (appended)
  6. TOOL_RESULT      — Tool execution results (appended, periodically trimmed)
"""

from __future__ import annotations

import re
import time as _time
from dataclasses import dataclass

# Default limits
_DEFAULT_TOOL_RESULT_CLIP = 6000  # Max chars for a single tool result in context
_DEFAULT_KEEP_RECENT_TOOL_MSGS = 24  # Keep this many recent tool messages in context
_DEFAULT_RECALL_TOP_K = 5  # Top-k archived entries returned by recall()
_DEFAULT_ARCHIVE_MAX = 500  # Session archive size cap (oldest dropped beyond this)


@dataclass
class _ArchiveEntry:
    """A fully-preserved historical item kept out of the inference context."""

    type: str  # "tool_result" | "log" | "code"
    content: str
    ts: float


def _tokenize(q: str) -> list[str]:
    """Split a recall query into keyword tokens (ascii words + CJK runs)."""
    q = (q or "").lower()
    return [t for t in re.findall(r"[a-z0-9_\.\-/]+|[一-鿿]+", q) if t]


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


_IMPORTANT_LINE_RE = re.compile(r"(checkpoint|saved|written|created|error|Error|失败|成功|task/)")


def _clip_tool_content(content: str, clip: int, original_len: int | None = None) -> str:
    """Clip a tool result to `clip` chars, keeping the head + important lines."""
    n = original_len if original_len is not None else len(content)
    lines = content.splitlines()
    keep_lines = [ln for ln in lines if _IMPORTANT_LINE_RE.search(ln)]
    head = content[: max(80, clip // 3)].rstrip()
    important = "\n".join(keep_lines[:20])
    if important and important not in head:
        summary = f"{head}\n…\n{important}"
    else:
        summary = head
    if len(summary) > clip:
        summary = summary[: clip - 20] + "\n…(已截断)"
    return summary + f"\n…(原始输出 {n} 字符，已截断)"


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
            "task_context": _Layer("task_context"),
            "tools_catalog": _Layer("tools_catalog"),
            "progress_block": _Layer("progress_block"),
            "coach_hint": _Layer("coach_hint"),
            "history": _Layer("history"),
            "tool_result": _Layer("tool_result"),
        }
        self._tool_result_indices: list[int] = []  # Fast lookup for trimming
        self._native_group_starts: list[int] = []  # Indices of assistant(tool_calls) messages (atomic-group trimming)
        self._archive: list[_ArchiveEntry] = []  # Full-preservation archive (not in context)
        self._coach_hint_buffer: list[str] = []  # Hints accumulated per-iteration (flushed before each LLM call)
        self._checklist: str = ""  # Completed-work checklist (react-engine-v16 R3), stable in task_context

    # -- Read access --

    @property
    def messages(self) -> list[dict]:
        """Return the current messages array for LLM chat_completion()."""
        return self._messages

    # -- Layer insertion helpers --

    def _append_system(self, content: str, layer_name: str, *, volatile: bool = False) -> int:
        """Append a system message and track it under `layer_name`. Update range.

        ``volatile`` marks a dynamic layer (progress_block / coach_hint) so the LLM
        client can protect it from head-truncation when the system block overflows.
        """
        idx = len(self._messages)
        msg = {"role": "system", "content": content}
        if volatile:
            msg["volatile"] = True
        self._messages.append(msg)
        layer = self._layers[layer_name]
        if not layer.present:
            layer.start = idx
        layer.end = idx + 1
        return idx

    def _replace_layer(self, layer_name: str, content: str, *, volatile: bool = False) -> None:
        """Replace an existing layer's message(s) with a single new message.

        If the layer is not present, appends. If present with multiple messages,
        removes all but keeps the slot at the original start position.
        """
        layer = self._layers[layer_name]
        if not layer.present:
            self._append_system(content, layer_name, volatile=volatile)
            return
        # Replace first message with new content, remove rest
        msg = {"role": "system", "content": content}
        if volatile:
            msg["volatile"] = True
        self._messages[layer.start] = msg
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

    def _append_user(self, content: str) -> int:
        """Append a user message and track it as a tool result."""
        idx = len(self._messages)
        self._messages.append({"role": "user", "content": content})
        layer = self._layers["tool_result"]
        if not layer.present:
            layer.start = idx
        layer.end = idx + 1
        self._tool_result_indices.append(idx)
        return idx

    def _append_assistant(self, content: str) -> int:
        """Append an assistant message to the history layer."""
        idx = len(self._messages)
        self._messages.append({"role": "assistant", "content": content})
        layer = self._layers["history"]
        if not layer.present:
            layer.start = idx
        layer.end = idx + 1
        return idx

    # -- Layer 1: Immutable base --

    def set_base(self, *, system_prompt: str, memory_text: str = "", summary_text: str = "") -> None:
        """Set the immutable base layer (system prompt + memory + summary).

        Must be called once before the main loop. All three components are
        folded into a single system message to save context slots.
        """
        parts = [system_prompt.strip()]
        if memory_text and memory_text.strip():
            parts.append(f"\n---\n## 长期规则（必须遵守，优先级最高）\n{memory_text.strip()}")
        if summary_text and summary_text.strip():
            parts.append(f"\n---\n## 会话滚动总结\n{summary_text.strip()}")
        content = "\n".join(parts)
        self._replace_layer("base", content)

    # -- Layer 2: Task context (goal + current plan, echoed every inference) --

    def set_task_context(
        self,
        goal: str,
        plan: str = "",
        view_catalog: str = "",
        checklist: str | None = None,
    ) -> None:
        """Set the persistent task goal + current plan layer.

        The goal is stable for the whole run; the plan is replaced whenever the
        LLM emits a new PLAN: line. Both stay in context every round. An optional
        ``view_catalog`` (cached ADS view names + view→field mapping, react-engine-v14
        R3) is injected as a third block so PLAN-stage semantic matching sees it.
        ``checklist`` (react-engine-v16 R3) is the completed-work summary; when None
        the previous checklist is preserved (so a re-issued PLAN keeps it).
        """
        self._goal = (goal or "").strip()
        self._plan = (plan or "").strip()
        self._view_catalog = (view_catalog or "").strip()
        if checklist is not None:
            self._checklist = (checklist or "").strip()
        self._render_task_context()

    def set_completed_checklist(self, checklist: str) -> None:
        """Update only the completed-work checklist block (react-engine-v16 R3).

        Keeps goal/plan/view_catalog intact; re-renders the task_context layer in
        place (O(1) replace) so the checklist stays fresh every round and is never
        trimmed (``trim_tool_results`` only touches tool_result + native history).
        """
        self._checklist = (checklist or "").strip()
        self._render_task_context()

    def _render_task_context(self) -> None:
        parts = [f"【任务目标】\n{self._goal}"]
        if self._plan:
            parts.append(f"【当前计划】\n{self._plan}")
        if self._view_catalog:
            parts.append(f"【数据视图目录】\n{self._view_catalog}")
        if self._checklist:
            parts.append(self._checklist)
        self._replace_layer("task_context", "\n\n".join(parts))

    # -- Archive + recall (out-of-context full preservation) --

    def push_archive(self, type: str, content: str) -> None:
        """Fully archive one historical item (kept out of the inference context)."""
        if not content or not content.strip():
            return
        self._archive.append(_ArchiveEntry(type=type, content=content.strip(), ts=_time.time()))
        if len(self._archive) > _DEFAULT_ARCHIVE_MAX:
            del self._archive[: len(self._archive) - _DEFAULT_ARCHIVE_MAX]

    def recall(self, query: str, top_k: int = _DEFAULT_RECALL_TOP_K) -> str:
        """Lightweight keyword+recency retrieval from the archive."""
        tokens = _tokenize(query)
        if not tokens or not self._archive:
            return "（归档中未找到与查询相关的内容）"
        scored = []
        for e in self._archive:
            hay = e.content.lower()
            score = sum(1 for t in tokens if t in hay or t in e.type)
            if score > 0:
                scored.append((score, e.ts, e))
        scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
        if not scored:
            return "（归档中未找到与查询相关的内容）"
        parts = [f"[{e.type}] {e.content[:800]}" for _, _, e in scored[:top_k]]
        return "【归档检索结果】\n" + "\n---\n".join(parts)

    # -- Layer 4: Tools catalog --

    def set_tools_catalog(self, tools_block: str) -> None:
        """Set the tools catalog layer. Call once before the main loop.

        The tools_block should be a compact description built by SystemPromptBuilder.
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
        self._replace_layer("progress_block", content, volatile=True)

    # -- Layer 6: Coach hint (dynamic, replaced each iteration) --

    def push_coach_hint(self, hint: str) -> None:
        """Replace the coach hint with a new hint immediately.

        Coach hints are short-lived guidance injected into the system context.
        They are replaced (not appended) each time this is called. The layer is
        established (possibly empty) on first call so it stays in the leading
        system block instead of being appended mid-conversation later.

        For in-loop hints, prefer :meth:`add_coach_hint` + :meth:`flush_coach_hints`
        so multiple hints in one iteration are merged instead of overwriting each other.
        """
        self._replace_layer("coach_hint", (hint or "").strip(), volatile=True)

    def add_coach_hint(self, hint: str) -> None:
        """Accumulate a coach hint for the current iteration.

        Hints are flushed once (joined into a single message) before the next
        LLM call via :meth:`flush_coach_hints`. This lets several hints produced
        within one iteration co-exist instead of the last one silently winning.
        Hints are expected to self-label (e.g. 「卡死检测」「完成度反思」).
        """
        h = (hint or "").strip()
        if not h:
            return
        if any(h == existing for existing in self._coach_hint_buffer):
            return
        self._coach_hint_buffer.append(h)

    def flush_coach_hints(self) -> None:
        """Join accumulated hints into one coach_hint message and clear the buffer.

        No-op when the buffer is empty (leaves any previously-established layer intact).
        """
        if not self._coach_hint_buffer:
            return
        self._replace_layer("coach_hint", "\n\n".join(self._coach_hint_buffer), volatile=True)
        self._coach_hint_buffer.clear()

    # -- Layer 7: History --

    def push_user_message(self, content: str) -> None:
        """Append the current user message to history."""
        idx = len(self._messages)
        self._messages.append({"role": "user", "content": content})
        layer = self._layers["history"]
        if not layer.present:
            layer.start = idx
        layer.end = idx + 1

    def push_assistant_reply(self, content: str) -> None:
        """Append an assistant reply to history (after LLM call)."""
        self._append_assistant(content)

    def push_history_message(self, role: str, content: str) -> None:
        """Append a historical user/assistant message to the history layer."""
        if role not in ("user", "assistant"):
            return
        idx = len(self._messages)
        self._messages.append({"role": role, "content": content})
        layer = self._layers["history"]
        if not layer.present:
            layer.start = idx
        layer.end = idx + 1

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
            "httpmcp_call": "HTTP 请求代理结果",
            "shell": "Shell 结果",
            "file_write": "文件写入结果",
            "file_read": "文件读取结果",
            "file_search": "文件搜索结果",
        }
        label = label_map.get(action, "工具结果")
        if len(content) > clip:
            content = _clip_tool_content(content, clip, len(tool_output))

        wrapped = f"{label}:\n{content.strip()}"
        return self._append_user(wrapped)

    def push_assistant_native(self, content: str, tool_calls: list[dict]) -> int:
        """Append an assistant message carrying native tool_calls (history layer)."""
        idx = len(self._messages)
        self._messages.append({
            "role": "assistant",
            "content": content or "",
            "tool_calls": list(tool_calls or []),
        })
        layer = self._layers["history"]
        if not layer.present:
            layer.start = idx
        layer.end = idx + 1
        self._native_group_starts.append(idx)
        return idx

    def push_native_tool_result(
        self,
        tool_call_id: str,
        tool_output: str,
        *,
        clip: int = _DEFAULT_TOOL_RESULT_CLIP,
    ) -> int:
        """Append a role:tool result paired with a native assistant tool_call.

        Kept in the history layer (not the tool_result layer) so
        ``trim_tool_results`` never orphans the assistant's tool_calls; atomic
        trimming happens in ``fit_messages_to_context``.
        """
        content = tool_output or ""
        if len(content) > clip:
            content = _clip_tool_content(content, clip, len(tool_output))
        idx = len(self._messages)
        self._messages.append({
            "role": "tool",
            "tool_call_id": tool_call_id or "",
            "content": content.strip(),
        })
        layer = self._layers["history"]
        if not layer.present:
            layer.start = idx
        layer.end = idx + 1
        return idx

    # -- Trimming --

    def trim_tool_results(self, *, keep_recent: int = _DEFAULT_KEEP_RECENT_TOOL_MSGS) -> None:
        """Drop old tool-result messages from context.

        Full content is already preserved in the archive, so the in-context
        ``tool_result`` layer only keeps the most recent `keep_recent` observations.
        Also trims native ``assistant(tool_calls) + role:tool`` atomic groups in the
        history layer (D7) — each group is dropped as one unit so no tool message is
        orphaned (mirrors ``fit_messages_to_context``'s atomic-group deletion).
        """
        # Text tool-result messages to drop (oldest first).
        text_drop = self._tool_result_indices[: max(0, len(self._tool_result_indices) - keep_recent)]

        # Native atomic groups to drop (assistant(tool_calls) + following role:tool).
        native_drop: list[int] = []
        if len(self._native_group_starts) > keep_recent:
            for s in self._native_group_starts[: len(self._native_group_starts) - keep_recent]:
                if s >= len(self._messages) or not self._messages[s].get("tool_calls"):
                    continue
                j = s + 1
                while j < len(self._messages) and self._messages[j].get("role") == "tool":
                    j += 1
                native_drop.extend(range(s, j))

        to_delete = sorted(set(text_drop) | set(native_drop))
        if not to_delete:
            return
        deleted = set(to_delete)
        for i in reversed(to_delete):
            del self._messages[i]

        self._tool_result_indices = [
            i - sum(1 for d in to_delete if d < i)
            for i in self._tool_result_indices
            if i not in deleted
        ]
        self._native_group_starts = [
            i - sum(1 for d in to_delete if d < i)
            for i in self._native_group_starts
            if i not in deleted
        ]

        for name, layer in self._layers.items():
            if name == "tool_result":
                continue
            if not layer.present:
                continue
            layer.start -= sum(1 for d in to_delete if d < layer.start)
            layer.end -= sum(1 for d in to_delete if d < layer.end)
        tr = self._layers["tool_result"]
        if self._tool_result_indices:
            tr.start = self._tool_result_indices[0]
            tr.end = self._tool_result_indices[-1] + 1
        else:
            tr.start = tr.end = -1
