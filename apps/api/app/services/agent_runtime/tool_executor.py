"""ToolExecutor — wraps agent_tools.execute_action with MCP validation and tracking.

Provides pre-execution validation, post-execution result recording, error
classification, and failure enrichment. All methods take explicit parameters
— no hidden state.
"""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.agent.models import Agent
    from app.models.sandbox import Sandbox
    from sqlmodel import Session


class ToolExecutor:
    """Wraps tool execution with MCP validation, tracking, and enrichment.

    Stateless — all state (mcp_results, error counters, etc.) is passed
    explicitly by the caller (typically the DecisionEngine or main loop).
    """

    # ---- Core execution ----

    @staticmethod
    async def execute(
        action: str,
        normalized: str,
        db: Session,
        agent: Agent,
        sandbox: Sandbox | None,
        skill_ids: list[str],
        mcp_ids: list[str],
        httpmcp_ids: list[str],
        rag_ids: list[str],
    ) -> str | None:
        """Execute a tool action via agent_tools.execute_action.

        Thin async wrapper that delegates to the existing action dispatcher.
        """
        from app.services.agent_tools import execute_action

        return await execute_action(
            action,
            normalized,
            db,
            agent,
            sandbox,
            skill_ids,
            mcp_ids,
            httpmcp_ids,
            rag_ids,
        )

    # ---- Engine-level retry ----

    @staticmethod
    def classify_retryable(error_text: str) -> bool:
        """True if the MCP error is transient and safe to retry automatically.

        Retryable: timeout, connection, mcp_remote, empty (transport-level)
        Non-retryable: unknown_column, type_mismatch, permission, syntax (needs LLM fix)
        """
        from app.services.agent_runtime.utils import (
            _MCP_RETRYABLE_CLASSES,
            _classify_mcp_error,
        )
        err_cls = _classify_mcp_error(error_text or "")
        return err_cls in _MCP_RETRYABLE_CLASSES

    @staticmethod
    async def execute_with_retry(
        action: str,
        normalized: str,
        db: Session,
        agent: Agent,
        sandbox: Sandbox | None,
        skill_ids: list[str],
        mcp_ids: list[str],
        httpmcp_ids: list[str],
        rag_ids: list[str],
        *,
        on_retry: callable | None = None,
    ) -> tuple[str | None, int]:
        """Execute with exponential backoff retry for transient MCP failures.

        Retries up to 3 times for timeout/connection/transport errors.
        Non-retryable errors (schema, syntax, permission) are returned immediately.
        Returns (result, retry_count).
        """
        import asyncio
        from app.services.agent_runtime.utils import _MCP_MAX_RETRIES, _MCP_RETRY_BACKOFF_BASE

        last_result: str | None = None
        for attempt in range(_MCP_MAX_RETRIES + 1):
            try:
                result = await ToolExecutor.execute(
                    action, normalized, db, agent, sandbox,
                    skill_ids, mcp_ids, httpmcp_ids, rag_ids,
                )
            except Exception as exc:
                result = f"MCP 调用异常: {exc}"

            last_result = result
            result_text = result or ""

            # Check if this is a failure worth retrying
            if not ToolExecutor.is_tool_failure(result_text):
                return result, attempt

            if not ToolExecutor.classify_retryable(result_text):
                return result, attempt

            if attempt < _MCP_MAX_RETRIES:
                delay = _MCP_RETRY_BACKOFF_BASE * (2 ** attempt)
                if on_retry:
                    try:
                        on_retry(attempt + 1, delay, result_text)
                    except Exception:
                        pass
                await asyncio.sleep(delay)

        return last_result, _MCP_MAX_RETRIES

    # ---- Schema alignment ----

    @staticmethod
    def align_sql_to_schema(
        sql: str,
        fields: list[str],
        *,
        view: str = "",
    ) -> tuple[str, str, list[str]]:
        """Soft-align SQL columns against describe schema fields.

        Returns (aligned_sql, alignment_level, notes):
        - "ok": all columns matched
        - "soft": some unknown but core columns aligned
        - "hard": view not in schema at all — recommend SELECT *
        """
        from app.services.agent_runtime.result_types import SchemaAlignment
        from app.services.export_column_plan import soft_align_sql_to_schema as _align

        if not fields:
            return sql, SchemaAlignment.HARD.value, [f"view `{view}` 无 schema 字段，使用原始 SQL"]

        aligned, align_notes, unknown = _align(sql, fields)
        notes = list(align_notes or [])

        if not unknown:
            return aligned, SchemaAlignment.OK.value, notes

        # Has unknown columns — determine severity
        notes.append(f"SQL 含 describe 未返回的列: {', '.join(unknown[:20])}")
        notes.append("已软对齐，继续发起查询（远端真实错误比本地猜测更有价值）")

        if aligned and aligned != sql:
            return aligned, SchemaAlignment.SOFT.value, notes
        return sql, SchemaAlignment.SOFT.value, notes

    # ---- Result recording ----

    @staticmethod
    def record_mcp_result(
        mcp_results: list[dict],
        normalized: str,
        tool_result: str,
        *,
        export_like: bool = False,
    ) -> None:
        """Parse and record MCP query result metadata into mcp_results list.

        For export: metadata only. For generic query: keeps a short Markdown preview.
        """
        from app.services.agent_runtime.utils import (
            _extract_mcp_view_name,
            _mcp_tool_name,
            _parse_mcp_rows,
            _rows_to_markdown_table,
        )

        rows = _parse_mcp_rows(tool_result)
        if not rows:
            return
        tool = _mcp_tool_name(normalized)
        view = _extract_mcp_view_name(normalized)
        item: dict = {
            "tool": tool,
            "view": view,
            "row_count": len(rows),
        }
        if not export_like and re.search(r"query", tool or "", re.I):
            preview = _rows_to_markdown_table(rows, max_rows=15)
            if preview:
                item["preview_md"] = preview
        mcp_results.append(item)

    # ---- MCP validation ----

    @staticmethod
    def validate_mcp_call(
        normalized: str,
        *,
        time_window: dict | None = None,
        target_roles: list[str] | None = None,
    ) -> str | None:
        """Soft-coach unrecoverable ads args — do not hit remote MCP.

        Returns an error hint string if validation fails, or None if OK.
        """
        from app.services.agent_runtime.utils import (
            _MCP_WHERE_SQL_HINT,
            _mcp_tool_name,
            _parse_mcp_args,
        )
        from app.services.react_engine import (
            _executable_query_example,
            _normalize_ads_mcp_args,
        )

        tool = _mcp_tool_name(normalized)
        raw_args = _parse_mcp_args(normalized)
        args = _normalize_ads_mcp_args(tool, raw_args)

        if tool == "describe_ads_view":
            if not str(args.get("view_name") or "").strip():
                ex = _executable_query_example(
                    time_window=time_window, target_roles=target_roles,
                )
                desc_ex = re.sub(r"query_ads_view", "describe_ads_view", ex, count=1)
                desc_ex = desc_ex.replace('"view":', '"view_name":')
                return ToolExecutor.enrich_failure(
                    tool,
                    "MCP 参数软提示: describe_ads_view 缺少 view_name（勿空参数 {}）。\n"
                    "请先 list_ads_views，或使用：\n"
                    f"{desc_ex}",
                    time_window=time_window,
                )
        elif tool == "query_ads_view":
            sql_norm = str(args.get("sql") or "")
            if re.search(r"\bFORMAT\b", sql_norm, re.I) or sql_norm.rstrip().endswith(";"):
                return ToolExecutor.enrich_failure(
                    tool,
                    "MCP 参数软提示: sql 勿含 FORMAT / 结尾分号（平台自动 FORMAT）。\n"
                    "请去掉 FORMAT 后用同一 SELECT + limit 分页重试。",
                    time_window=time_window,
                )
            if not str(args.get("view") or "").strip():
                ex = _executable_query_example(
                    time_window=time_window, target_roles=target_roles,
                )
                return ToolExecutor.enrich_failure(
                    tool,
                    "MCP 参数软提示: query_ads_view 缺少 view（勿空参数 {}；"
                    "字段名是 view 不是 view_name）。\n"
                    "正确 SOP：list_ads_views → describe_ads_view(view_name) → "
                    "query_ads_view(view)。\n"
                    f"可执行示例：{ex}",
                    time_window=time_window,
                )
            where = args.get("where")
            if isinstance(where, list):
                return ToolExecutor.enrich_failure(
                    tool,
                    "MCP 参数软提示: query_ads_view 的 where 不能是数组，须为对象。"
                    f"\n{_MCP_WHERE_SQL_HINT}",
                    time_window=time_window,
                )
            if isinstance(where, str) and where.strip():
                return ToolExecutor.enrich_failure(
                    tool,
                    "MCP 参数软提示: where 必须是 JSON 对象，不能是 SQL/字符串片段。"
                    f"\n{_MCP_WHERE_SQL_HINT}\n"
                    "若要写 SQL，请用字段 `sql`（完整 SELECT），不要把 SQL 放进 where。",
                    time_window=time_window,
                )
        return None

    # ---- Error enrichment ----

    @staticmethod
    def enrich_failure(
        tool: str,
        tool_result: str,
        *,
        time_window: dict | None = None,
        view: str = "",
        schema_hints: dict | None = None,
        tool_schema_summary: str = "",
    ) -> str:
        """Append soft coach hints on MCP failures; never hard-blocks export/FINAL."""
        from app.services.agent_runtime.utils import (
            _MCP_QUERY_UNION_HINT,
            _MCP_SELECT_ONLY_HINT,
            _MCP_WHERE_SQL_HINT,
        )

        text = tool_result or ""
        tool = (tool or "").strip()
        lower = text.lower()
        parts: list[str] = [text]

        transportish = (
            text.strip() in ("", "MCP 无响应", "MCP 调用失败:")
            or text.strip().endswith("MCP 调用失败:")
            or "timeoutexception" in lower
            or "connecterror" in lower
            or "remoteprotocolerror" in lower
            or "readerror" in lower
            or "all connection attempts failed" in lower
        )
        if transportish and "传输层软提示" not in text:
            parts.append(
                "【传输层软提示】疑似超时/断连（非业务硬门禁）。"
                "可缩小 limit、分页重试，或先 describe 再 query；勿因空失败散文收工。"
            )

        if tool == "query_ads_view" and "invalid_union" in lower:
            if "invalid_union 纠偏" not in text:
                parts.append(_MCP_QUERY_UNION_HINT)

        if tool == "query_ads_view" and any(
            k in lower for k in (
                "cannot convert string", "类型不匹配", "where 期望 object",
                "收到 string", "undefined",
            )
        ):
            if "where 纠偏" not in text:
                parts.append(_MCP_WHERE_SQL_HINT)

        if tool == "query_ads_view" and (
            "only select" in lower or "only select queries are allowed" in lower
        ):
            if "sql 纠偏" not in text:
                parts.append(_MCP_SELECT_ONLY_HINT)
                if time_window and time_window.get("mcp_example"):
                    parts.append(f"推荐：`{time_window.get('mcp_example')}`")

        if tool == "query_ads_view" and (
            "from ads" in lower or "whitelist_view" in lower
        ):
            if "视图白名单 纠偏" not in text:
                parts.append(
                    "【视图白名单 纠偏】使用 `from ads.view_xxx` 格式。"
                    "未知视图请先 list_ads_views 再 describe。"
                )

        if tool == "query_ads_view" and ("db::exception" in lower or "missing columns" in lower):
            if view and schema_hints:
                hint = schema_hints.get(view)
                if hint:
                    fields = getattr(hint, "fields", None) or []
                    if fields:
                        parts.append(f"已知字段: {', '.join(fields[:50])}")

        return "\n".join(parts)

    # ---- Error classification & tracking ----

    @staticmethod
    def classify_error(result: str) -> str:
        """Classify an MCP error string into a stable class key."""
        from app.services.agent_runtime.utils import _classify_mcp_error
        return _classify_mcp_error(result)

    @staticmethod
    def error_signature(tool: str, result: str) -> str:
        """Stable error signature from tool name + result content."""
        from app.services.agent_runtime.utils import _mcp_error_signature
        return _mcp_error_signature(tool, result)

    @staticmethod
    def is_tool_failure(tool_result: str) -> bool:
        """True if the tool result text indicates a failure."""
        from app.services.agent_runtime.utils import _is_mcp_tool_failure
        return _is_mcp_tool_failure(tool_result)

    @staticmethod
    def is_data_query(tool: str) -> bool:
        """True if the tool name is a data query (not list/describe)."""
        from app.services.agent_runtime.utils import _is_mcp_data_query
        return _is_mcp_data_query(tool)

    @staticmethod
    def track_mcp_failure(
        tool: str,
        normalized: str,
        error_text: str,
        *,
        mcp_tool_calls: dict[str, int],
        mcp_tool_fails: dict[str, int],
        mcp_identical_counts: dict[str, int],
        mcp_class_fails: dict[str, int],
        mcp_class_samples: dict[str, str],
        mcp_class_tools: dict[str, str],
        soft_fail_limit: int,
        class_fail_limit: int,
        export_like: bool = False,
        in_export_finalize: bool = False,
    ) -> str:
        """Track MCP failure counters and return soft coach hints.

        Updates the counter dicts in-place. Returns a hint string (may be empty)
        with corrective suggestions. Never hard-blocks tools.
        """
        from app.services.agent_runtime.utils import (
            _MCP_FORMAT_STRIP_HINT,
            _MCP_NON_TOOL_FUSE_CLASSES,
            _classify_mcp_error,
            _mcp_error_signature,
            _parse_mcp_args,
        )

        if not tool:
            return ""

        mcp_tool_calls[tool] = mcp_tool_calls.get(tool, 0) + 1

        mcp_tool_fails[tool] = mcp_tool_fails.get(tool, 0) + 1
        args = _parse_mcp_args(normalized)
        ident = _mcp_error_signature(
            tool, json.dumps(args, ensure_ascii=False, sort_keys=True)
        )
        mcp_identical_counts[ident] = mcp_identical_counts.get(ident, 0) + 1

        err_cls = _classify_mcp_error(error_text or "")
        mcp_class_fails[err_cls] = mcp_class_fails.get(err_cls, 0) + 1
        mcp_class_samples[err_cls] = (error_text or "")[:200]
        mcp_class_tools[err_cls] = tool
        class_n = mcp_class_fails[err_cls]

        fails = mcp_tool_fails.get(tool, 0)
        identical = mcp_identical_counts.get(ident, 0)
        notices: list[str] = []

        if class_n >= class_fail_limit:
            if err_cls in _MCP_NON_TOOL_FUSE_CLASSES or "format" in err_cls:
                notices.append(
                    f"\n\n【纠偏】`{tool}` `{err_cls}` 已失败 {class_n} 次；"
                    + _MCP_FORMAT_STRIP_HINT
                    + " 去掉 FORMAT 后继续分页 query。"
                )
            else:
                notices.append(
                    f"\n\n【纠偏提示】`{tool}` 同类错误 `{err_cls}` 已失败 {class_n} 次。"
                    "请改 where/sql/视图后继续；勿同参空转。"
                    "可查阅 Skill「近期 MCP 反例」。"
                )

        if export_like and identical >= soft_fail_limit:
            notices.append(
                f"\n\n【纠偏提示】`{tool}` 相同参数已失败 {identical} 次。"
                "请更换参数/OFFSET 后继续，或 SHELL 基于已落盘数据写表。"
            )
        elif in_export_finalize or fails >= soft_fail_limit:
            notices.append(
                f"\n\n【纠偏提示】`{tool}` 已多次失败。"
                "建议改参续查，或 SHELL 分析后 FINAL。"
            )

        return "".join(notices)

    # ---- Utility ----

    @staticmethod
    def primary_mcp_name(db: Session, mcp_ids: list[str]) -> str:
        """Return the primary MCP name for display."""
        from app.services.agent_runtime.utils import _bound_mcp_names
        names = _bound_mcp_names(db, mcp_ids)
        return names[0] if names else "mcp"

    @staticmethod
    def ensure_sandbox_running(db: Session, sandbox: Sandbox | None) -> bool:
        """Ensure the sandbox is running; start if needed."""
        if not sandbox:
            return False
        try:
            from app.services.sandbox_util import ensure_sandbox_running as _ensure
            return _ensure(db, sandbox)
        except Exception:
            return False
