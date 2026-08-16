import json
import asyncio
import hashlib
import logging
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from sqlalchemy.orm import Session

from app.models import Agent, LLMResource, Sandbox, Skill, MCP, ChatMessage, ChatSummary, ChatNote, ImChannel, ImSession
from app.security import now_str
from app.services.llm_client import chat_completion, estimate_tokens, fit_messages_to_context
from app.services.agent_tools import (
    execute_action,
    _write_workplace,
    _read_workplace,
    _exec_shell,
)
from app.services import docker_service
from app.services.workplace import (
    cleanup_workplace_temp_dirs,
    collect_export_uids_from_task,
    count_data_rows,
    download_path,
    ensure_workplace,
    find_best_task_data_file,
    find_export_build_scripts,
    format_dir_listing,
    is_valid_deliverable_file,
    list_recent_data_files,
    list_task_page_view_map,
    materialize_analyzed_export,
    materialize_export_deliverable,
    promote_named_files,
    read_deliverable_headers,
    task_has_exportable_data,
    write_task_json_page,
)  # is_valid_deliverable_file used by channel file resolve
from app.services.export_column_plan import (
    apply_binding_hints_to_column_plan,
    build_column_plan,
    build_query_graph,
    carry_forward_bound_columns,
    column_source_method,
    format_column_plan_card,
    format_column_plan_summary,
    mcp_line_from_query_node,
    needs_export_clarify,
    plan_one_column,
    sanitize_final_without_landing,
    should_soft_probe_query_node,
    soft_align_sql_to_schema,
    sql_with_probe_limit,
    text_has_export_stat_claims,
    views_from_column_plan,
)
from app.services.export_build_report import (
    export_filename_for_title,
    write_export_deliverable,
    write_query_result_deliverable,
)
from app.services.export_contract import build_export_contract
from app.services.export_cte_orchestrator import run_cte_export
from app.services.export_contract_updater import (
    record_schema_hint,
    record_schema_list,
    sync_export_contract_state,
)
from app.services.export_finalizer import (
    outcome_from_verification,
    verify_export_finalizer,
)
from app.services.export_materializer import (
    mark_incomplete_columns,
    materialize_platform_export,
    merge_focus_into_prior_deliverable,
)
from app.services.export_query_executor import (
    build_query_node_failure_decision,
    build_query_node_rows_decision,
    build_query_execution_plan,
    query_execution_ready,
    query_node_should_skip,
    should_reverify_after_query_progress,
)
from app.services.export_query_contract import (
    format_query_contract_sql,
    format_query_contract_methods,
    is_executable_query_sql,
    normalize_sql,
    query_contract_column_plan,
)
from app.services.export_run_state import (
    build_export_run_state_payload,
    build_run_state_digest as export_run_state_digest,
    build_schema_discovery_repair_plan as export_build_schema_discovery_repair_plan,
    cohort_rows_incomplete as export_cohort_rows_incomplete,
    derive_export_completeness as export_derive_completeness,
    hydrate_time_window_from_state as export_hydrate_time_window_from_state,
    serialize_time_window_for_state as export_serialize_time_window_for_state,
    strip_completed_schema_repair_actions as export_strip_completed_schema_repair_actions,
    summarize_schema_discovery as export_summarize_schema_discovery,
)
from app.services.export_schema_discovery import (
    ViewSchemaHint,
    build_schema_discovery_actions,
    parse_describe_schema_hint,
)
from app.services.export_schema_executor import execute_schema_discovery_actions
from app.services.export_fill_scope import (
    ExportFillScope,
    build_column_fill_repair_plan,
    prior_headers_from_state,
    resolve_export_fill_target_rel,
)
from app.services.export_repair_plan import (
    activate_repair_plan_from_prior,
    activate_repair_plan_from_trace,
    format_repair_plan_block,
)
from app.services.export_task_validator import (
    format_task_spec_validation_reply,
)
from app.services.export_trace import (
    ExportTrace,
    classify_mcp_error,
    load_latest_export_trace,
    read_export_trace,
    write_export_trace,
)
from app.services.export_verifier import (
    build_final_summary_from_verifier,
    diagnose_query_export_change,
    format_verifier_block,
    format_query_export_verification,
    inspect_query_deliverable,
    verify_query_export_delivery,
)
from app.services.intent_router import (
    TurnIntent,
    analyze_turn_intent,
    build_task_relation_context,
    compute_data_query_gaps,
    format_time_window_system_hint,
    looks_like_task_message,
    should_hard_stop_task_spec,
    task_policy_from_intent,
)
from app.services.mcp_resource_bind import (
    BindResult,
    bind_metrics_to_mcp,
    binding_resource_whitelist,
    ensure_sql_resource_aligned,
    format_need_based_query_plan,
    format_resource_binding_hint,
    metric_intents_from_export_columns,
    metric_intents_from_turn,
    parse_resource_catalog,
    resolve_export_resource_whitelist,
)
from app.services.task_policy import (
    TaskPolicy,
    ViewIntentRoute,
    build_generic_todos,
    classify_view_intent_llm,
    detect_task_policy,
    load_skill_mds,
    mark_act_todos_from_evidence,
    parse_generic_plan,
    route_export_view_intent,
    skill_snapshot_for_prompt,
    unfinished_act_todos,
)
from app.services.skill_lesson import (
    find_latest_run_state,
    find_repair_base_run_state,
    gap_tags_for_rolling,
    infer_repair_gaps,
    load_recent_fetch_gap_hints,
    load_recent_mcp_lesson_hints,
    materialize_export_skill_lesson,
)
from app.services.tool_parser import (
    extract_final_payload,
    extract_tool_steps,
    detect_action_from_reply,
    _strip_llm_artifacts,
)
from app.services.mcp_client import call_mcp_tool, connect_mcp_detail
from app.services.channels.base import create_adapter
from app.services.agent_runtime.hub import (
    ChatStreamHub,
    ChatStopped,
    hub,
    _running,
    is_running,
    stop_chat,
)

logger = logging.getLogger(__name__)

# mcp_id -> (monotonic_ts, tools)
_mcp_tools_cache: dict[str, tuple[float, list[dict]]] = {}
_MCP_TOOLS_TTL_SEC = 600.0
_MCP_TOOLS_PROMPT_LIMIT = 30

_ROLLING_MAX_ENTRIES = 40
_DATA_FILE_SUFFIXES = (".xlsx", ".xls", ".csv")
_MCP_SOFT_FAIL_HINT_DEFAULT = 5  # soft hint after N failures (do not abort turn)
# Class hard-fuse disabled (OpenClaw-style — soft 纠偏 only)
_MCP_CLASS_HARD_LIMIT = 999  # effectively disabled; kept for compat
_EXPORT_MCP_HARD_BLOCK_LIMIT = 999  # disable class/tool hard abort for export
_TOOL_RESULT_MAX_CHARS = 6000
_WS_TOOL_CLIP_CHARS = 2000
_SHELL_OBS_MAX_CHARS = 2000
_SHELL_CHECKPOINT_MAX_CHARS = 64 * 1024
_TRIM_TOOL_MSG_EVERY = 10
_KEEP_RECENT_TOOL_MSGS = 12
_KEEP_RECENT_TOOL_MSGS_EXPORT = 8
_OLD_TOOL_MSG_CAP = 500
_OLD_TOOL_MSG_CAP_EXPORT = 800
_PROGRESS_MAX_LINES = 80
_FINALIZE_WINDOW = 5
_EXPORT_FINALIZE_RATIO = 0.18  # last ~18% of budget: hard stop querying
_EXPORT_QUERY_BUDGET_DEFAULT = 14
_EXPORT_QUERY_BUDGET_TYPE_B = 18  # Type-B needs user deep + dims + facts
_EXPORT_QUERY_BUDGET_TYPE_B_MAX = 22  # adaptive bump cap after truncated prior run
_EXPORT_QUERY_BUDGET_TYPE_B_PAY_MAX = 30  # pay cohort + prior fact trunc / row gap
_EXPORT_PLAN_MAX_ATTEMPTS = 2
_EXPORT_ANALYZE_MAX_ROUNDS = 12  # base; also max(12, col_todos//2)
_EXPORT_ANALYZE_WRITE_GRACE = 6  # extra rounds to urge LLM SHELL before raw fallback
_EXPORT_MAX_PAGES_PER_VIEW = 3  # pay/cash/bet fact views (register cohort)
_EXPORT_MAX_PAGES_PER_VIEW_PAY_COHORT = 8  # 充值用户：允许更深翻至短页
_EXPORT_FACT_PAGE_CAP_MAX = 20  # dynamic cap ceiling from COUNT estimate
_EXPORT_MAX_PAGES_DIM = 1  # channel/game config dims
_EXPORT_MAX_PAGES_USER = 6  # user_info may paginate within time window
_EXPORT_MAX_PAGES_USER_MAX = 12  # dynamic ceiling from cohort estimate
_EXPORT_AUTO_OFFSET_MAX = 10  # engine auto OFFSET pages per LLM MCP hit
_EXPORT_DIM_RESERVE = 2  # reserve query slots for channel+game before analyze
_EXPORT_DIM_FORCE_MAX_ROUNDS = 3  # soft nudge budget; never hard-stall analyze for dims


def _should_hard_defer_analyze_for_missing_dims(
    dim_miss: list[str] | None,
    *,
    budget_left: int = 0,
    force_rounds: int = 0,
    max_rounds: int = _EXPORT_DIM_FORCE_MAX_ROUNDS,
) -> bool:
    """Business hard-gate removed: missing dims never hard-block analyze."""
    del dim_miss, budget_left, force_rounds, max_rounds
    return False
_EXPORT_FETCH_IDLE_ESCAPE = 4  # fetch turns without MCP data → try analyze/finalize
# ADS archive labels only — never assign as default export_target_roles
_VIEW_NAME_TOKEN_RE = re.compile(r"\b(view_result_[a-zA-Z0-9_]+)\b")
_EXPORT_FULL_PAGE_ROWS = 900  # MCP effective page cap is 1000; ≥90% must continue.
_EXPORT_PAGE_LIMIT = 1000  # default MCP limit per page (facts)
_EXPORT_USER_PAGE_LIMIT = 2000  # user_info wider pages (once-pull-full)
_EXPORT_TINY_PAGE_LIMIT = 100  # LLM/sample limits below this get soft-raised on export fetch
_REMOTE_TINY_PAGE_LO = 25  # suspicious remote default page (often ~30)
_REMOTE_TINY_PAGE_HI = 35
_EXPORT_USER_UID_BATCH = 500  # pay-cohort: uid IN (...) batch size (bounded vs timeout)
_EXPORT_QUERY_BUDGET_FULL_FETCH_MAX = 45  # one-shot full fetch ceiling
_COHORT_ROW_COMPLETE_RATIO = 0.85  # deliverable_rows / estimate (for honest FINAL gap)
# ClickHouse FORMAT must not appear in query_ads_view sql (MCP appends it)
_SQL_FORMAT_CLAUSE_RE = re.compile(
    r"(?is)\s*;?\s*FORMAT\s+\w+\s*$|\s+FORMAT\s+\w+\b",
)
_MCP_NON_TOOL_FUSE_CLASSES = frozenset({"format_clause"})
_COHORT_UID_ESTIMATE_RE = re.compile(
    r"(?:约|达到|≈|~|=)\s*(\d{3,5})\s*(?:量级|个|人|uid)?"
    r"|(?:充值用户|distinct\s+uid|uid\s*数)[^\d]{0,24}(\d{3,5})"
    r"|(\d{3,5})\s*(?:量级|个充值用户)",
    re.I,
)
_SHELL_WRITE_XLSX_RE = re.compile(
    r"openpyxl|to_excel|\.xlsx|Workbook\s*\(|\.save\s*\(",
    re.I,
)
# python path/to/script.py (not python -c …) often writes deliverable inside the script
_SHELL_PYTHON_SCRIPT_RE = re.compile(
    r"\bpython3?\b(?!\s+-c\b)(?:\s+-[^\s]+)*\s+\S+\.py\b",
    re.I,
)
_SHELL_EXPLORE_RE = re.compile(
    r"(?:^|[\n;&|])\s*(?:ls|head|wc|cat|find|file|stat)\b",
    re.I,
)
_EXPORT_TODO_JUNK_RE = re.compile(
    r"[=÷]|SUM|聚合|view_|→|->|ASC|DESC|GROUP\s*BY|WHERE|"
    r"READ\s*:|WRITE\s*:|SHELL\s*:|MCP\s*:|HTTPMCP\s*:|FINAL\s*:|"
    r"需要资源|query_ads|pandas|openpyxl|to_excel|"
    r"page_\*|task/|ID\.txt|无需其他|仅\s*user",
    re.I,
)
_EXPORT_DATE_RANGE_RE = re.compile(
    r"(?P<y1>20\d{2})[-/.年](?P<m1>\d{1,2})[-/.月](?P<d1>\d{1,2})日?"
    r".{0,20}?"
    r"(?P<y2>20\d{2})[-/.年](?P<m2>\d{1,2})[-/.月](?P<d2>\d{1,2})日?",
    re.I | re.S,
)
# Flex: M-D / M月D日 / 缺少年份 / 末端仅月 + 至今（如「美国时间6-1至8至今」）
_EXPORT_DATE_RANGE_FLEX_RE = re.compile(
    r"(?:(?P<y1>20\d{2})\s*[-/.年])?\s*(?P<m1>\d{1,2})\s*[-/.月]\s*(?P<d1>\d{1,2})\s*日?"
    r".{0,24}?"
    r"(?:(?P<y2>20\d{2})\s*[-/.年])?\s*(?P<m2>\d{1,2})"
    r"(?:\s*[-/.月]\s*(?P<d2>\d{1,2})\s*日?)?"
    r"(?:\s*(?P<till_now>至今|到今|到现在|迄今))?",
    re.I | re.S,
)
_EXPORT_DATE_TO_NOW_RE = re.compile(
    r"(?:(?P<y1>20\d{2})\s*[-/.年])?\s*"
    r"(?P<m1>\d{1,2})\s*[-/.月]\s*(?P<d1>\d{1,2})\s*日?"
    r"\s*(?:至|到|-|~|—|--)?\s*(?P<till_now>至今|到今|到现在|迄今)",
    re.I | re.S,
)
_EXPORT_DISCOVER_RATIO = 0.15
_EXPORT_PLAN_HINT = (
    "【导出·规划闸门】必须基于 MCP schema 发现结果规划："
    "list_ads_views 的接口/视图列表 + describe_ads_view 的字段/表备注。"
    "静态 metric_registry 仅 ADS 档案，不能覆盖真实字段、不能默认全量拉取所有视图。\n"
    "本轮只输出 PLAN（不要调用工具），格式：\n"
    "PLAN:\n"
    "- 目标:\n"
    "- 任务类型: A-轻量身份 | B-多事实 | C-点名单视图\n"
    "- 数据源/视图:\n"
    "- 字段与筛选:（必须含用户消息时间窗，user_info 用 sql 过滤 register_time）\n"
    "- 输出列:（逐条抄写用户编号列的完整表述，保留括号说明）\n"
    "- 需要资源:（按输出列推导的 MCP resource/视图；类型 C 点名 view 时写「无」；"
    "仅用户字段可只写身份源；禁止默认写满 user/pay/cash/bet/channel/game）\n"
    "- 计算与关联:\n"
    "- 分页预算: 最多 N 次 query（默认约 14；Type-B/full_fetch 可达约 45；"
    "页长 fact=1000 / user=2000，勿按 30 行/页或 generic 8 次工具估算）\n"
    "- SHELL 分析步骤:（读 task/page_*.json → 关联计算 → 写当前目录 xlsx）\n"
    "- 完成标准:\n"
    "平台会限量拉取并自动写入 task/page_N.json；禁止把原始 MCP 页当最终报表；"
    "拉数后必须用 SHELL（pandas/openpyxl）分析落盘；勿 WRITE 二进制 xlsx。\n"
    "用户已点名 view_result_* 时属类型 C：只需 query 该视图，勿默认拉取全量明细。"
)
_EXPORT_FETCH_HINT_TAIL_SINGLE = (
    "类型 C：只 query 用户点名的视图（平台已白名单拦截其它 view）；"
    "describe 仅限白名单视图；拉数后 SHELL 写当前目录 xlsx，再 FINAL。"
)
_EXPORT_ANALYZE_HINT = (
    "【导出·分析阶段】白名单资源齐套后禁止再 MCP query；若仍缺声明资源可先补拉再写表。\n"
    "前提：task 页须已覆盖 PLAN「需要资源」/列计划白名单；"
    "**禁止在缺声明明细页时用宽表字段凑数**。\n"
    "本阶段必须用 SHELL:（pandas/openpyxl）读取 `task/<run_id>/page_*.json`，"
    "按用户完整列名输出表头到**工作区当前目录**（非 task/）。\n"
    "脚本请写到 `/tmp/build_report.py` 再 `python3 /tmp/build_report.py`；"
    "**禁止**往 `task/` 写 `.py`（会触发服务热重载中断）。\n"
    "计算口径以用户列/Skill 为准；缺维表时对应列可暂空但仍须完整表头；完成后 FINAL:。"
)
_EXPORT_FETCH_HINT_TAIL = (
    "仅拉列计划/白名单内 resource；user_info 用 sql（完整 SELECT * FROM ads.<view> "
    "WHERE register_time…）带平台毫秒窗并分页至短页。"
    "where 须为 JSON 对象；时间范围请放 sql 字段；禁止默认全量拉取。"
)


def _adapt_export_prompt_for_capabilities(text: str, *, shell_enabled: bool) -> str:
    """Keep export instructions consistent with the agent's enabled tools."""
    if shell_enabled:
        return text
    replacements = (
        (
            "- SHELL 分析步骤:（读 task/page_*.json → 关联计算 → 写当前目录 xlsx）",
            "- 引擎交付步骤:（数据齐套后由平台关联计算、写 xlsx 并校验）",
        ),
        ("SHELL 分析步骤", "平台交付步骤"),
        ("SHELL 步骤", "平台交付步骤"),
        (
            "拉数后必须用 SHELL（pandas/openpyxl）分析落盘；勿 WRITE 二进制 xlsx。",
            "拉数后由平台关联计算并落盘；勿 WRITE 二进制 xlsx。",
        ),
        ("拉数后必须用 SHELL 分析落盘", "拉数后由平台分析、落盘并校验"),
        ("SHELL 写当前目录交付物", "由平台生成并校验当前目录交付物"),
        ("拉数后 SHELL 写当前目录 xlsx，再 FINAL。", "拉数后由平台写 xlsx 并校验，再 FINAL。"),
        ("随后 SHELL 分析再 FINAL。", "随后由平台分析、写表并校验。"),
        ("拉数结束后必须用 SHELL 做关联/计算并写当前目录 xlsx，再 FINAL。", "拉数结束后由平台做关联/计算、写 xlsx 并校验。"),
    )
    adapted = text
    for old, new in replacements:
        adapted = adapted.replace(old, new)
    return adapted


def _export_write_grace_done(
    *,
    shell_enabled: bool,
    analyze_rounds_used: int,
    analyze_max_rounds: int,
) -> bool:
    """No-shell exports must never wait for LLM-authored build scripts."""
    return (not shell_enabled) or analyze_rounds_used >= (
        analyze_max_rounds + _EXPORT_ANALYZE_WRITE_GRACE
    )
_EXPORT_QUERY_BUDGET_FLOOR = 10  # PLAN 预算下限（含 channel/game 各 1 页预留）
_MCP_WHERE_SQL_HINT = (
    "【where 纠偏】query_ads_view 的 where 必须是对象（标量），例如 {\"uid\":123}；"
    "时间范围请使用 sql 字段，且必须是带 FROM ads.<view> 的 SELECT，例如 "
    "\"sql\":\"SELECT * FROM ads.<已绑定资源> WHERE <已确认时间字段> >= START_MS "
    "AND <已确认时间字段> < END_MS\"；"
    "禁止把 SQL 片段塞进 where 字符串；禁止因 sql 报错就改用 where 过滤时间窗。"
)
_MCP_SELECT_ONLY_HINT = (
    "【sql 纠偏】远程要求 sql 必须是 SELECT，且必须含 FROM ads.<白名单视图>。"
    "不要只传 WHERE 片段，也不要改用 where JSON 绕过时间窗。"
    "请改用："
    '"sql":"SELECT * FROM ads.<已绑定资源> WHERE <已确认时间字段> >= START_MS '
    'AND <已确认时间字段> < END_MS"'
    "（START/END 用平台给出的毫秒），然后继续分页；禁止因此放弃拉取或散文收工。"
)
_MCP_FROM_ADS_HINT = (
    "【FROM 纠偏】sql 必须包含 FROM ads.<whitelist_view>，例如 "
    '"sql":"SELECT * FROM ads.<已绑定资源> WHERE <已确认时间字段> >= START_MS '
    'AND <已确认时间字段> < END_MS"。'
    "平台会尽量自动补 FROM；请勿改用 where 绕过时间窗。"
)
_NUMBERED_COL_RE = re.compile(
    r"(?m)^\s*(?:\d+[\.\)、]|[-*•])\s*(.+?)\s*$",
)
_PATH_IN_TEXT_RE = re.compile(
    r"(?:task/[\w.\-/=]+|(?:[\w.\-]+/)*[\w.\-]+\.(?:xlsx|xls|xlsm|csv|pdf|zip|md|json))",
    re.I,
)
_IMPORTANT_LINE_RE = re.compile(
    r"(全文已落盘:|可用 READ:|已写入|\.xlsx|\.xls|\.xlsm|\.csv|\.pdf|\.zip|task/)",
    re.I,
)
_GFM_TABLE_BLOCK_RE = re.compile(
    r"(?:^|\n)(?:\|[^\n]*\|(?:\n|$)){2,}",
    re.M,
)


def _clip_tool_result(text: str, limit: int = _TOOL_RESULT_MAX_CHARS) -> str:
    text = text or ""
    if len(text) <= limit:
        return text
    return text[: limit - 12] + "\n…(已截断)"


def _is_export_like_task(user_message: str, skill_blob: str = "") -> bool:
    """True only for analytical export-report tasks (not every 导出/明细/用户数据)."""
    return detect_task_policy(user_message, skill_blob=skill_blob).export_like



_TOOL_INTENT_ITER_FLOOR = 16


def _effective_max_iterations(
    agent: Agent,
    user_message: str = "",
    *,
    needs_tools: bool = False,
) -> int:
    """Honor Agent.max_iterations; floor tool-intent turns so PLAN/MCP can run."""
    del user_message  # kept for call-site compatibility
    configured = max(1, int(getattr(agent, "max_iterations", None) or 100))
    if needs_tools:
        return max(configured, _TOOL_INTENT_ITER_FLOOR)
    return configured


def _count_rows_in_payload(obj) -> int | None:
    if isinstance(obj, list):
        return len(obj)
    if not isinstance(obj, dict):
        return None
    for key in ("rows", "data", "records", "result", "items"):
        val = obj.get(key)
        if isinstance(val, list):
            return len(val)
    nested = obj.get("content")
    if isinstance(nested, list):
        return len(nested)
    if isinstance(nested, dict):
        return _count_rows_in_payload(nested)
    return None


def _prepare_tool_observation(
    text: str,
    *,
    action: str,
    sandbox: Sandbox | None,
    session_id: str,
) -> str:
    """Keep LLM context short: large tool blobs → workplace checkpoint + row summary."""
    raw = text or ""
    act = (action or "").strip().lower()
    is_shell = act in ("shell",) or act.startswith("shell")
    soft_limit = _SHELL_OBS_MAX_CHARS if is_shell else _TOOL_RESULT_MAX_CHARS
    if len(raw) <= soft_limit:
        return raw

    row_count = None
    parsed = None
    stripped = raw.strip()
    if not is_shell and (stripped.startswith("{") or stripped.startswith("[")):
        try:
            parsed = json.loads(stripped)
            row_count = _count_rows_in_payload(parsed)
        except Exception:
            parsed = None

    checkpoint = ""
    if sandbox is not None:
        safe_sid = re.sub(r"[^\w\-]+", "_", session_id or "sess")[:32]
        rel = f"task/_engine_checkpoints/{safe_sid}_{int(time.time() * 1000)}_{act or 'tool'}.txt"
        dump = raw
        if is_shell and len(dump) > _SHELL_CHECKPOINT_MAX_CHARS:
            dump = (
                dump[: 48 * 1024]
                + f"\n…(checkpoint 已截断，原文约 {len(raw)} 字)…\n"
                + dump[-16 * 1024 :]
            )
        elif not is_shell and len(dump) > _SHELL_CHECKPOINT_MAX_CHARS * 4:
            # Non-shell: still avoid multi-MB checkpoint files
            dump = _clip_tool_result(dump, _SHELL_CHECKPOINT_MAX_CHARS * 4)
        try:
            written = _write_workplace(sandbox, rel, dump)
            if written.startswith("已写入"):
                checkpoint = rel
        except Exception:
            logger.exception("checkpoint write failed action=%s", action)

    parts = [f"[工具结果已压缩 action={action} 原文 {len(raw)} 字]"]
    if row_count is not None:
        parts.append(f"约 {row_count} 行/条")
    if checkpoint:
        parts.append(
            f"全文已落盘: {checkpoint}（过程文件，无需 READ；继续合并写入 task/ 或 FINAL）"
        )
    elif row_count is not None:
        parts.append("全文未落盘（无沙箱）；请缩小单次查询或确保沙箱可用。")
    elif not is_shell:
        parts.append(_clip_tool_result(raw, 2000))
    if isinstance(parsed, dict):
        keys = list(parsed.keys())[:12]
        if keys:
            parts.append("顶层键: " + ", ".join(keys))
    if is_shell:
        parts.append("输出预览:\n" + _clip_tool_result(raw, soft_limit))
    return "\n".join(parts)
_RE_SEND_CHANNEL_VERB = re.compile(
    r"(?:发送到|发送给|发到|推送到|推送给|推到|推给|发给|推送至)",
    re.I,
)
# 「发送 … 到 tg」中间可夹选中/文件等词
_RE_SEND_LOOSE_TO_CHANNEL = re.compile(
    r"(?:发送|推送|发给|发)\s*.{0,40}?\s*到\s*(?:tg|telegram|电报|飞书|feishu|"
    r"钉钉|dingtalk|企微|wecom|qq|渠道)",
    re.I,
)
_RE_SELECTED_FILE_TO_CHANNEL = re.compile(
    r"(?:选中的?|勾选的?).{0,24}(?:excel|xlsx|csv|文件|报表).{0,24}"
    r"(?:到|给)\s*(?:tg|telegram|电报|飞书|钉钉|企微|渠道)",
    re.I,
)
_RE_CHANNEL_TARGET = re.compile(
    r"(?:tg|telegram|电报|飞书|feishu|lark|钉钉|dingtalk|"
    r"企微|企业微信|wecom|qq|渠道|消息渠道|绑定渠道)",
    re.I,
)
# Legacy TG-oriented pattern (kept as extra hit surface)
_RE_SEND_TG = re.compile(
    r"(?:将|把)?(?:数据|报表|文件|excel|xlsx|csv)?[^。\n]{0,80}?"
    r"(?:发送到|发送给|发给|推送给|推送到|推到|发到)\s*(?:tg|telegram|电报)"
    r"|(?:tg|telegram)\s*(?:发送|推送)",
    re.I,
)
_RE_EXCEL_OR_SELECTED = re.compile(
    r"(?:excel|xlsx|csv|选中|勾选|报表|文件)",
    re.I,
)
_RE_DATA_FILENAME = re.compile(
    r"([^\s`\"'<>|/\\]+\.(?:xlsx|xlsm|xls|csv))",
    re.I,
)
_RE_REQUERY = re.compile(
    r"(重新查|再查|重查|重新统计|重新拉取|再统计|再导出|重新导出|重新生成)",
    re.I,
)
_PROVIDER_MESSAGE_ALIASES: dict[str, tuple[str, ...]] = {
    "telegram": ("tg", "telegram", "电报"),
    "feishu": ("飞书", "feishu", "lark"),
    "dingtalk": ("钉钉", "dingtalk"),
    "wecom": ("企微", "企业微信", "wecom"),
    "qq": ("qq",),
}


_INTERNAL_TOOL_ACTIONS = frozenset({"shell", "mcp_tool_call", "httpmcp_call"})


def _parse_mcp_rows(tool_result: str) -> list[dict] | None:
    text = (tool_result or "").strip()
    if not text:
        return None
    try:
        data = json.loads(text)
        if isinstance(data, list) and data and isinstance(data[0], dict):
            return data
        if isinstance(data, dict):
            for key in ("data", "items", "records", "result", "rows"):
                val = data.get(key)
                if isinstance(val, list) and val and isinstance(val[0], dict):
                    return val
    except Exception:
        pass
    match = re.search(r"\[[\s\S]*\]", text)
    if match:
        try:
            data = json.loads(match.group(0))
            if isinstance(data, list) and data and isinstance(data[0], dict):
                return data
        except Exception:
            pass
    return None


def _parse_resource_timestamp_ms(value: Any) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        raw = float(value)
        return int(raw if abs(raw) >= 100_000_000_000 else raw * 1000)
    text = str(value).strip()
    if re.fullmatch(r"-?\d+(?:\.\d+)?", text):
        raw = float(text)
        return int(raw if abs(raw) >= 100_000_000_000 else raw * 1000)
    try:
        normalized = text.replace("Z", "+00:00")
        dt = datetime.fromisoformat(normalized)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp() * 1000)
    except (TypeError, ValueError):
        return None


def _temporal_list_contract_annotation(
    tool: str,
    tool_result: str,
    time_window: dict[str, Any] | None,
) -> str:
    """Annotate list results with engine-validated resource creation time matches."""
    if not re.match(r"(?i)^list(?:_|$)", str(tool or "").strip()):
        return ""
    if not isinstance(time_window, dict):
        return ""
    try:
        start_ms = int(time_window.get("start_ms"))
        end_ms = int(time_window.get("end_ms"))
    except (TypeError, ValueError):
        return ""
    rows = _parse_mcp_rows(tool_result) or []
    if not rows:
        return ""
    in_window: list[dict] = []
    checked = 0
    for row in rows:
        if not isinstance(row, dict):
            continue
        ts = None
        for key in ("created_at", "create_time", "createdAt", "created_time"):
            if key in row:
                ts = _parse_resource_timestamp_ms(row.get(key))
                if ts is not None:
                    break
        if ts is None:
            continue
        checked += 1
        if start_ms <= ts < end_ms:
            in_window.append(row)
    if not checked:
        return (
            "\n\n【时间窗校验·引擎】当前列表缺少可解析的 created_at/create_time，"
            "不能按内容日期替代；请查询详情或使用带资源创建时间的接口。"
        )
    refs = []
    for row in in_window[:50]:
        rid = row.get("id") or row.get("note_id") or row.get("uuid") or "?"
        title = str(row.get("title") or row.get("name") or "").strip()[:80]
        refs.append(f"{rid}" + (f"（{title}）" if title else ""))
    selected = "、".join(refs) if refs else "（本页无）"
    return (
        "\n\n【时间窗校验·引擎】"
        f"按资源创建时间半开区间 [{start_ms},{end_ms}) 校验："
        f"当前页 {checked} 条可校验，命中 {len(in_window)} 条。"
        f"仅这些记录可计入本日期：{selected}。"
        "若 has_more=true 且尚未越过起点，必须带原 cursor 继续翻页；"
        "标题、正文或活动日期不得改变归属日。"
    )


def _md_table_cell(val) -> str:
    s = "" if val is None else str(val)
    return s.replace("|", "\\|").replace("\n", " ").strip()


def _rows_to_markdown_table(rows: list[dict], max_rows: int = 30) -> str:
    if not rows:
        return ""
    cols = list(rows[0].keys())
    if len(cols) > 10:
        cols = cols[:10]
    header = "| " + " | ".join(_md_table_cell(c) for c in cols) + " |"
    sep = "| " + " | ".join("---" for _ in cols) + " |"
    body = [
        "| " + " | ".join(_md_table_cell(row.get(c, "")) for c in cols) + " |"
        for row in rows[:max_rows]
    ]
    md = "\n".join([header, sep, *body])
    if len(rows) > max_rows:
        md += f"\n\n*共 {len(rows)} 条，展示前 {max_rows} 条*"
    return md


def _format_generic_query_observation(tool_result: str, rows: list[dict]) -> str:
    """Prefer Markdown table preview for non-export query tool results."""
    table = _rows_to_markdown_table(rows, max_rows=30)
    n = len(rows)
    parts = ["### 查询结果", ""]
    if table:
        parts.append(table)
    else:
        parts.append(f"（{n} 行，无可展示列）")
    parts.extend(["", f"共 {n} 行。"])
    clipped = _clip_tool_result(tool_result or "", 1200)
    if clipped.strip():
        parts.extend(["", "原始数据（截断，供核对）:", clipped])
    return "\n".join(parts)


def _mcp_tool_name(normalized: str) -> str:
    match = re.match(r"(?:MCP|HTTPMCP):\s*(\S+)", normalized or "")
    raw = match.group(1) if match else "mcp"
    return _clean_mcp_tool_name(raw) or "mcp"


def _clean_mcp_tool_name(name: str) -> str:
    """Strip markdown/quote wrappers LLM often wraps around tool names."""
    return (name or "").strip().strip("`'\"")


_ADS_MCP_TOOLS = frozenset({
    "list_ads_views",
    "describe_ads_view",
    "query_ads_view",
    "query_ads_metric",
})


def _rewrite_mcp_clean_tool_name(normalized: str) -> str:
    """Rewrite MCP: <tool> … so tool has no backticks/quotes."""
    m = re.match(r"(MCP:\s*)(\S+)(\s*)(.*)$", normalized or "", re.DOTALL)
    if not m:
        return normalized or ""
    cleaned = _clean_mcp_tool_name(m.group(2))
    if not cleaned or cleaned == m.group(2):
        return normalized or ""
    return f"{m.group(1)}{cleaned}{m.group(3)}{m.group(4)}"


def _tool_schema_property_keys(tool: dict) -> set[str] | None:
    """Return allowed top-level arg keys, or None if stripping is unsafe/unavailable."""
    schema = _tool_input_schema(tool)
    if schema.get("additionalProperties") is True:
        return None
    props = schema.get("properties")
    if not isinstance(props, dict) or not props:
        return None
    return {str(k) for k in props.keys()}


def _find_mcp_tool_def(tools: list[dict] | None, name: str) -> dict | None:
    want = _clean_mcp_tool_name(name).lower()
    if not want:
        return None
    for t in tools or []:
        if not isinstance(t, dict):
            continue
        if _clean_mcp_tool_name(str(t.get("name") or "")).lower() == want:
            return t
    return None


def _soft_strip_unknown_mcp_args(
    args: dict | None,
    tool_def: dict | None,
) -> tuple[dict, list[str]]:
    """Drop top-level keys not declared in tool inputSchema.properties (soft)."""
    notes: list[str] = []
    src = dict(args) if isinstance(args, dict) else {}
    if not tool_def:
        return src, notes
    allowed = _tool_schema_property_keys(tool_def)
    if allowed is None:
        return src, notes
    out: dict = {}
    stripped: list[str] = []
    for k, v in src.items():
        key = str(k)
        if key in allowed:
            out[key] = v
        else:
            stripped.append(key)
    if stripped:
        notes.append("已剥离未知参数: " + ", ".join(stripped))
    missing = [
        r
        for r in _tool_required_fields(tool_def)
        if r not in out or out.get(r) in (None, "")
    ]
    if missing:
        notes.append(
            "软提示缺 required: " + ", ".join(missing) + "（不阻止调用/FINAL）"
        )
    return out, notes


def _format_tool_schema_summary(tool_def: dict | None) -> str:
    if not isinstance(tool_def, dict):
        return ""
    name = str(tool_def.get("name") or "").strip()
    schema = _tool_input_schema(tool_def)
    props = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
    keys = [str(k) for k in props.keys()]
    req = _tool_required_fields(tool_def)
    bits: list[str] = []
    if keys:
        bits.append("参数: " + ", ".join(keys[:16]))
    if req:
        bits.append("required: " + ", ".join(req[:8]))
    if not bits:
        return ""
    prefix = f"{name} " if name else ""
    return prefix + "；".join(bits)


def soft_align_mcp_line_to_tool_schema(
    normalized: str,
    tools: list[dict] | None,
) -> tuple[str, list[str]]:
    """Clean tool name + soft-strip schema-unknown top-level args. Never hard-blocks."""
    notes: list[str] = []
    line = _rewrite_mcp_clean_tool_name(normalized or "")
    if line != (normalized or ""):
        notes.append(f"已规范化工具名→`{_mcp_tool_name(line)}`")
    tool = _mcp_tool_name(line)
    args = _parse_mcp_args(line)
    tool_def = _find_mcp_tool_def(tools, tool)
    if not tool_def:
        return line, notes
    fixed, strip_notes = _soft_strip_unknown_mcp_args(args, tool_def)
    notes.extend(strip_notes)
    if fixed != args or line != (normalized or ""):
        line = f"MCP: {tool} {json.dumps(fixed, ensure_ascii=False)}"
    return line, notes


def _shell_command(normalized: str) -> str:
    text = (normalized or "").strip()
    if text.startswith("SHELL:"):
        return text[6:].strip()
    return text


def _short_text(text: str, limit: int = 120) -> str:
    compact = " ".join((text or "").split())
    if len(compact) <= limit:
        return compact
    return compact[: max(0, limit - 1)] + "…"


def _bound_mcp_names(db: Session, mcp_ids: list[str]) -> list[str]:
    names: list[str] = []
    for mid in mcp_ids or []:
        mcp = db.query(MCP).filter(MCP.id == mid).first()
        if mcp:
            names.append(mcp.name or mid)
    return names


def _primary_mcp_name(db: Session, mcp_ids: list[str]) -> str:
    names = _bound_mcp_names(db, mcp_ids)
    return names[0] if names else "mcp"


_PREFLIGHT_BIND_LLM_TIMEOUT = 15


async def _bind_mcp_resources_for_turn(
    *,
    db: Session,
    llm,
    agent: Agent,
    mcp_ids: list[str],
    turn_intent: TurnIntent,
    allow_seed: bool = True,
) -> BindResult:
    """Soft preflight bind: cached tools/list + short LLM; no remote list/describe.

    Discover (list_ads_views / describe) stays in the tool loop so preflight
    cannot stall or kill the main ReAct LLM turn.
    """
    del agent  # reserved for future per-agent bind policy
    intents = metric_intents_from_turn(turn_intent)
    if not intents:
        return BindResult()
    tools: list[dict] = []
    for mid in mcp_ids or []:
        mcp = db.query(MCP).filter(MCP.id == mid).first()
        if not mcp:
            continue
        try:
            tools.extend(await _get_mcp_tools_cached(mcp))
        except Exception:
            continue
    # Empty catalog → bind rejects invented resource names; seed may fill
    try:
        return await asyncio.wait_for(
            bind_metrics_to_mcp(
                llm,
                intents,
                tools=tools,
                catalog_resources=[],
                resource_schemas=None,
                db=db,
                timeout=_PREFLIGHT_BIND_LLM_TIMEOUT,
                allow_seed=allow_seed,
            ),
            timeout=float(_PREFLIGHT_BIND_LLM_TIMEOUT + 2),
        )
    except Exception as e:
        logger.warning("preflight mcp bind skipped: %s", e)
        try:
            return await bind_metrics_to_mcp(
                None,
                intents,
                tools=tools,
                catalog_resources=[],
                db=db,
                allow_seed=allow_seed,
            )
        except Exception:
            return BindResult(error=str(e)[:200])


async def _inject_mcp_resource_bind_hint(
    *,
    db: Session,
    llm,
    agent: Agent,
    mcp_ids: list[str],
    turn_intent: TurnIntent,
) -> str:
    """Backward-compatible string hint (prefer format_need_based_query_plan)."""
    result = await _bind_mcp_resources_for_turn(
        db=db,
        llm=llm,
        agent=agent,
        mcp_ids=mcp_ids,
        turn_intent=turn_intent,
    )
    return format_resource_binding_hint(result)


def _record_mcp_result(
    mcp_results: list[dict],
    normalized: str,
    tool_result: str,
    *,
    export_like: bool = False,
    result_shape: str = "unspecified",
) -> None:
    rows = _parse_mcp_rows(tool_result)
    if not rows:
        return
    tool = _mcp_tool_name(normalized)
    view = _extract_mcp_view_name(normalized)
    # Export: metadata only. Generic data turns retain a renderable row preview so
    # FINAL does not depend on the model repeating a prior tool observation.
    item: dict = {
        "tool": tool,
        "view": view,
        "row_count": len(rows),
    }
    if not export_like:
        max_rows = len(rows) if result_shape == "time_series" else 30
        preview = _rows_to_markdown_table(rows, max_rows=max_rows)
        if preview:
            item["preview_md"] = preview
    mcp_results.append(item)


def _has_markdown_table(text: str) -> bool:
    """Structural Markdown-table check without business keywords or tool names."""
    lines = [line.strip() for line in str(text or "").splitlines() if line.strip()]
    for index in range(len(lines) - 1):
        header = lines[index]
        separator = lines[index + 1]
        if not (header.startswith("|") and header.endswith("|")):
            continue
        if not (separator.startswith("|") and separator.endswith("|")):
            continue
        cells = [cell.strip() for cell in separator.strip("|").split("|")]
        if cells and all(cell and set(cell.replace(":", "")) <= {"-"} for cell in cells):
            return True
    return False


def _merge_structured_presentation(
    final: str,
    candidate: str,
    *,
    result_shape: str = "unspecified",
) -> str:
    """Keep an earlier complete presentation when a later FINAL drops its rows."""
    if result_shape not in {"time_series", "detail_list", "mixed"}:
        return final
    clean_candidate = _clean_display_text(candidate or "")
    clean_final = _clean_final_answer(final or "")
    if not _has_markdown_table(clean_candidate) or _has_markdown_table(clean_final):
        return clean_final or clean_candidate
    if not clean_final or clean_final in clean_candidate:
        return clean_candidate
    return f"{clean_candidate.rstrip()}\n\n{clean_final.lstrip()}"


def _shrink_tool_message_keeping_paths(content: str, cap: int) -> str:
    """Truncate but keep lines with checkpoint / file paths."""
    if len(content) <= cap:
        return content
    lines = content.splitlines()
    keep = [ln for ln in lines if _IMPORTANT_LINE_RE.search(ln)]
    head = content[: max(80, cap // 3)].rstrip()
    important = "\n".join(keep[:20])
    merged = head
    if important and important not in merged:
        merged = f"{merged}\n…\n{important}"
    if len(merged) > cap:
        # Prefer keeping important paths at the end
        if important:
            budget = max(40, cap - len(important) - 20)
            merged = f"{head[:budget]}\n…\n{important}"[:cap]
        else:
            merged = content[: max(0, cap - 12)] + "\n…(已截断)"
    elif not merged.endswith("…(已截断)") and len(content) > len(merged):
        merged = merged.rstrip() + "\n…(已截断)"
    return merged


def _trim_old_tool_messages(
    messages: list[dict],
    *,
    keep_recent: int = _KEEP_RECENT_TOOL_MSGS,
    cap: int = _OLD_TOOL_MSG_CAP,
) -> None:
    """In-place shrink older tool-result user messages during long runs."""
    tool_idxs = [
        i for i, m in enumerate(messages)
        if m.get("role") == "user" and str(m.get("content") or "").startswith("工具结果:")
    ]
    if len(tool_idxs) <= keep_recent:
        return
    for i in tool_idxs[:-keep_recent]:
        content = messages[i].get("content") or ""
        if len(content) > cap:
            messages[i]["content"] = _shrink_tool_message_keeping_paths(content, cap)


def _progress_block_text(lines: list[str]) -> str:
    body = "\n".join(lines[-_PROGRESS_MAX_LINES:]) if lines else "(暂无)"
    return (
        "【本轮进度】以下路径在长跑中保留，勿丢失："
        "中间产物在 task/，仅最终交付文件写到当前目录。\n"
        f"{body}"
    )


def _upsert_progress_message(messages: list[dict], progress_lines: list[str]) -> None:
    text = _progress_block_text(progress_lines)
    for m in messages:
        if m.get("role") == "system" and str(m.get("content") or "").startswith("【本轮进度】"):
            m["content"] = text
            return
    # Insert after leading system messages
    insert_at = 0
    for i, m in enumerate(messages):
        if m.get("role") == "system":
            insert_at = i + 1
        else:
            break
    messages.insert(insert_at, {"role": "system", "content": text})


def _append_progress(progress_lines: list[str], line: str) -> None:
    line = (line or "").strip()
    if not line:
        return
    if progress_lines and progress_lines[-1] == line:
        return
    progress_lines.append(line)
    if len(progress_lines) > _PROGRESS_MAX_LINES:
        del progress_lines[:-_PROGRESS_MAX_LINES]


def _extract_named_paths(*texts: str) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()
    for text in texts:
        for m in _PATH_IN_TEXT_RE.finditer(text or ""):
            p = m.group(0).strip().strip("`").lstrip("/")
            if p.startswith("workplace/"):
                p = p[len("workplace/") :]
            if not p or p in seen:
                continue
            seen.add(p)
            found.append(p)
    return found


def _finalize_hint(
    save_dir: str = "",
    *,
    export_like: bool = False,
    shell_enabled: bool = True,
) -> str:
    dest = f"`{save_dir}/`" if save_dir else "当前目录（工作区根）"
    if export_like:
        if not shell_enabled:
            return (
                "【收尾提示·导出】迭代已接近上限：\n"
                "1) 建议停止新增 MCP query，优先收束已拉到的 task 数据；\n"
                f"2) 若当前目录尚无分析后的 xlsx，由平台按列计划从 task/ 物化并写入 {dest}；"
                "不要等待或请求 SHELL；\n"
                "3) 请立即单独输出一行 FINAL（精简：文件说明 / 统计 / 筛选 / 缺口若有）；\n"
                "4) 勿粘贴 markdown 数据表或样例行。"
            )
        return (
            "【收尾提示·导出】迭代已接近上限：\n"
            "1) 建议停止新 MCP query，优先 SHELL 写表；\n"
            f"2) 若当前目录尚无分析后的 xlsx，立即用 SHELL 从 task/ 生成并写入 {dest}；"
            "禁止 WRITE 二进制；SHELL 失败则平台将回退合并原始 JSON；\n"
            "3) 请立即单独输出一行 FINAL（精简：文件说明 / 统计 / 筛选 / 缺口若有）；\n"
            "4) 勿粘贴 markdown 数据表或样例行。"
        )
    return (
        "【收尾提示】迭代接近上限：请尽快给出最终回答。"
        f"若有交付文件，仅把最终文件写到 {dest}；过程产物留在 task/。"
        "输出 FINAL: <简洁结论>，不要罗列中间文件。"
    )


def _export_finalize_start(max_iters: int) -> int:
    """Iteration index (0-based) when export hard-finalize begins."""
    window = max(_FINALIZE_WINDOW, int(max_iters * _EXPORT_FINALIZE_RATIO))
    return max(0, max_iters - window)


def _is_mcp_list_or_describe(tool: str) -> bool:
    t = (tool or "").lower()
    return "describe" in t or t.startswith("list_") or t.startswith("list")


def _is_mcp_data_query(tool: str) -> bool:
    """True for query/search style tools (not list/describe)."""
    t = (tool or "").lower()
    if not t or _is_mcp_list_or_describe(t):
        return False
    return "query" in t or "search" in t or "fetch" in t


def _parse_export_plan_budget(
    reply: str,
    *,
    max_budget: int | None = None,
) -> int | None:
    """If reply has a PLAN: block, return clamped query budget; else None."""
    text = reply or ""
    if not re.search(r"(?im)^\s*PLAN\s*:", text):
        return None
    cap = int(max_budget) if max_budget is not None else _EXPORT_QUERY_BUDGET_DEFAULT
    n = cap
    m = re.search(
        r"(?:分页预算|预算|最多)[^\d]{0,24}(\d+)|(\d+)\s*次\s*(?:query|查询|分页)",
        text[:1500],
        re.I,
    )
    if m:
        try:
            n = int(m.group(1) or m.group(2))
        except (TypeError, ValueError):
            n = cap
    # Floor includes dim reserve; cap at Type-B / default max for this run
    return max(_EXPORT_QUERY_BUDGET_FLOOR, min(n, cap))


def _should_auto_accept_export_plan(
    *,
    export_phase: str,
    plan_done: bool,
    export_view_mode: str,
    column_plan: list[dict] | None,
    task_validation_status: str,
    schema_summary: dict | None,
) -> bool:
    """True when structured export contract can replace a prose PLAN turn."""
    del schema_summary
    if (export_phase or "") != "plan" or plan_done:
        return False
    if (export_view_mode or "") == "single_view":
        return False
    if task_validation_status and task_validation_status != "pass":
        return False
    if not any(isinstance(c, dict) and c.get("header") for c in (column_plan or [])):
        return False
    # Schema discovery is executed as the first fetch step; it must not block
    # entry into fetch, otherwise schema-first runs can stall in PLAN forever.
    return True


def _export_roles_ready_for_analyze(
    *,
    missing_roles: list[str] | None,
    has_data: bool,
    user_fetch_complete: bool,
    user_pages: int,
    fact_need_continue: list[str] | None = None,
    max_user_pages: int = _EXPORT_MAX_PAGES_USER,
    target_roles: list[str] | None = None,
) -> bool:
    """True when plan roles covered; user short-page only if user is a target."""
    if not has_data:
        return False
    if [r for r in (missing_roles or []) if str(r).strip()]:
        return False
    targets = {str(r).strip() for r in (target_roles or []) if str(r).strip()}
    # Column-driven: skip user short-page gate when user not in plan
    if not targets or "user" in targets:
        if not (user_fetch_complete or int(user_pages or 0) >= max_user_pages):
            return False
    if fact_need_continue:
        return False
    return True


def _fact_roles_needing_continue(
    target_roles: list[str] | None,
    fetched_view_pages: dict[str, int] | None,
    last_page_rows_by_view: dict[str, int] | None,
    *,
    budget_left: int,
    max_fact_pages: int | None = None,
    pay_cohort: bool = False,
    full_fetch: bool = False,
) -> list[str]:
    """pay/cash/bet with a full last page and room under per-view cap."""
    if budget_left <= 0:
        return []
    cap = int(max_fact_pages) if max_fact_pages is not None else _EXPORT_MAX_PAGES_PER_VIEW
    targets = {str(r).strip() for r in (target_roles or []) if str(r).strip()}
    pages = fetched_view_pages or {}
    last_rows = last_page_rows_by_view or {}
    # Pay cohort / full_fetch: never continue fact OFFSET while user still missing
    # (pay cohort: still allow pay continue — user comes from uid batches after pay)
    user_pages = sum(int(n or 0) for v, n in pages.items() if _is_user_info_view(v))
    block_non_pay_facts = bool(
        (pay_cohort or full_fetch) and "user" in targets and user_pages < 1
    )
    need: list[str] = []
    for role in ("pay", "cash", "bet"):
        if role not in targets:
            continue
        # Pay cohort: allow pay OFFSET before user; still block cash/bet
        if block_non_pay_facts and not (pay_cohort and role == "pay"):
            continue
        role_views = [v for v in pages if _view_category(v) == role]
        if not role_views:
            continue
        total_pages = sum(int(pages.get(v, 0) or 0) for v in role_views)
        if total_pages >= cap:
            continue
        if any(_is_full_page_rows(last_rows.get(v, 0), view=v) for v in role_views):
            need.append(role)
    return need


def _export_roles_needing_page_continue(
    target_roles: list[str] | None,
    fetched_view_pages: dict[str, int] | None,
    last_page_rows_by_view: dict[str, int] | None,
    *,
    budget_left: int,
    fact_page_cap: int | None = None,
    user_page_cap: int | None = None,
    pay_cohort: bool = False,
    full_fetch: bool = False,
) -> list[str]:
    """user + pay/cash/bet still full-page with room under soft page/budget caps."""
    if int(budget_left or 0) < 2:
        return []
    need = list(
        _fact_roles_needing_continue(
            target_roles,
            fetched_view_pages,
            last_page_rows_by_view,
            budget_left=budget_left,
            max_fact_pages=fact_page_cap,
            pay_cohort=pay_cohort,
            full_fetch=full_fetch,
        )
    )
    pages = fetched_view_pages or {}
    last_rows = last_page_rows_by_view or {}
    targets = {str(r).strip() for r in (target_roles or []) if str(r).strip()}
    if targets and "user" not in targets:
        return need
    user_views = [v for v in pages if _is_user_info_view(v)]
    if not user_views:
        return need
    u_cap = (
        int(user_page_cap)
        if user_page_cap is not None and int(user_page_cap) > 0
        else _EXPORT_MAX_PAGES_USER
    )
    u_pages = sum(int(pages.get(v, 0) or 0) for v in user_views)
    if u_pages >= u_cap:
        return need
    if any(_is_full_page_rows(last_rows.get(v, 0), view=v) for v in user_views):
        if "user" not in need:
            need = ["user"] + need
    return need


def _parse_cohort_uid_estimate(*texts: str) -> int | None:
    """Best-effort parse of target cohort size (e.g. ~3747 充值用户)."""
    best: int | None = None
    for raw in texts:
        text = raw or ""
        for m in _COHORT_UID_ESTIMATE_RE.finditer(text):
            for g in m.groups():
                if not g:
                    continue
                try:
                    n = int(g)
                except ValueError:
                    continue
                if 100 <= n <= 500_000:
                    if best is None or n > best:
                        best = n
    return best


def _cohort_rows_incomplete(
    *,
    deliverable_rows: int | None,
    cohort_uid_estimate: int | None,
    ratio: float = _COHORT_ROW_COMPLETE_RATIO,
) -> bool:
    return export_cohort_rows_incomplete(
        deliverable_rows=deliverable_rows,
        cohort_uid_estimate=cohort_uid_estimate,
        ratio=ratio,
    )


def _compute_adaptive_export_budget(
    *,
    type_b: bool,
    prior_state: dict | None,
    current_tw_label: str = "",
    current_tw: dict | None = None,
    pay_cohort: bool = False,
    allow_prior_mcp_examples: bool = True,
) -> tuple[int, str]:
    """Return (budget, coach_hint) from prior truncation / row-gap signals."""
    base = _EXPORT_QUERY_BUDGET_TYPE_B if type_b else _EXPORT_QUERY_BUDGET_DEFAULT
    cap = _EXPORT_QUERY_BUDGET_TYPE_B_MAX if type_b else _EXPORT_QUERY_BUDGET_DEFAULT
    if pay_cohort and type_b:
        cap = max(cap, _EXPORT_QUERY_BUDGET_TYPE_B_PAY_MAX)
    if not isinstance(prior_state, dict):
        if pay_cohort and type_b:
            return min(cap, max(base, base + 4)), (
                "【充值用户】事实表可深翻至短页；勿浅拉 1 页即收工。"
            )
        return base, ""
    prior_tw = prior_state.get("time_window") or {}
    if not isinstance(prior_tw, dict):
        prior_tw = {}
    cur_tw = current_tw if isinstance(current_tw, dict) else None
    prior_label = str(prior_tw.get("label") or "").strip()
    cur_label = ""
    if cur_tw:
        cur_label = str(cur_tw.get("label") or "").strip()
    if not cur_label:
        cur_label = (current_tw_label or "").strip()
    sa, ea = _tw_ms_tuple(cur_tw)
    sb, eb = _tw_ms_tuple(prior_tw)
    if sa is not None and ea is not None and sb is not None and eb is not None:
        same_tw = sa == sb and ea == eb
    elif cur_label and prior_label:
        # Legacy prior without ms: label substring match; empty current ≠ same
        same_tw = prior_label in cur_label or cur_label in prior_label
    else:
        same_tw = False
    try:
        user_complete = prior_state.get("user_fetch_complete")
    except Exception:
        user_complete = None
    truncated = bool(prior_state.get("user_truncated")) or bool(
        prior_state.get("fact_truncated_roles")
    )
    user_incomplete = user_complete is False
    prior_est = prior_state.get("cohort_uid_estimate")
    try:
        prior_est_i = int(prior_est) if prior_est is not None else None
    except (TypeError, ValueError):
        prior_est_i = None
    try:
        prior_rows = prior_state.get("deliverable_rows")
        prior_rows_i = int(prior_rows) if prior_rows is not None else None
    except (TypeError, ValueError):
        prior_rows_i = None
    row_gap = _cohort_rows_incomplete(
        deliverable_rows=prior_rows_i,
        cohort_uid_estimate=prior_est_i,
    )
    completeness = str(prior_state.get("completeness") or "").strip()
    need_cont = [
        str(r).strip()
        for r in (prior_state.get("need_continue_roles") or [])
        if str(r).strip()
    ]
    incomplete_comp = completeness in (
        "truncated", "fact_starved", "iters_exhausted", "no_data", "fallback",
    )
    if not same_tw or not (
        truncated or user_incomplete or row_gap or pay_cohort or incomplete_comp or need_cont
    ):
        # Still allow digest-only learning hint without budget bump (same window only)
        learn = _prior_state_learning_hint(prior_state)
        if learn and same_tw:
            return base, learn
        return base, ""
    try:
        prior_budget = int(prior_state.get("budget") or base)
    except (TypeError, ValueError):
        prior_budget = base
    bump = 6 if (pay_cohort and (truncated or row_gap or incomplete_comp)) else 4
    if completeness in ("iters_exhausted", "fact_starved"):
        bump = max(bump, 6)
    budget = min(cap, max(base, prior_budget + bump))
    try:
        up = int(prior_state.get("user_pages") or 0)
    except (TypeError, ValueError):
        up = 0
    facts = [
        str(r).strip()
        for r in (prior_state.get("fact_truncated_roles") or [])
        if str(r).strip()
    ]
    if need_cont:
        for r in need_cont:
            if r not in facts:
                facts.append(r)
    hint = (
        f"【上轮拉取缺口】completeness={completeness or '未知'}；"
        f"user_pages={up}，短页={'是' if user_complete else '否'}；"
        "本轮须续翻至短页或达帽后再写表。"
    )
    digest = str(prior_state.get("digest") or "").strip()
    if digest:
        hint += f" 上轮摘要：{digest}"
    if facts:
        hint += " 明细满页截断/须续翻：" + "、".join(facts) + "（同窗 OFFSET 续页）。"
    if row_gap and prior_est_i and prior_rows_i is not None:
        hint += (
            f" 交付行数={prior_rows_i} << 目标≈{prior_est_i}；"
            "先拉全 pay cohort 再 join。"
        )
    # Prefer concrete next MCP from prior state only when same window
    if allow_prior_mcp_examples and same_tw:
        shown = 0
        for a in (prior_state.get("next_actions") or [])[:2]:
            if not isinstance(a, dict):
                continue
            mcp = str(a.get("mcp_example") or "").strip()
            if mcp:
                hint += f" 建议：{mcp}"
                shown += 1
            if shown >= 2:
                break
    if pay_cohort:
        hint += f" 充值用户事实表页帽≤{_EXPORT_MAX_PAGES_PER_VIEW_PAY_COHORT}。"
    hint += f" 本轮预算已调至 {budget}。"
    return budget, hint


def _ensure_ads_sql_from(view: str, sql: str) -> str:
    """Ensure sql FROM target matches tool view/resource (generic alignment)."""
    raw = (sql or "").strip()
    view = (view or "").strip()
    if not raw or not view:
        return raw
    # Strip client-side FORMAT / trailing ; before FROM rewrite
    raw, _ = _strip_clickhouse_format_clause(raw)
    return ensure_sql_resource_aligned(view, raw, schema_prefix="ads")


def _strip_clickhouse_format_clause(sql: str) -> tuple[str, bool]:
    """Remove FORMAT … / trailing semicolons from agent sql. MCP adds FORMAT itself."""
    raw = (sql or "").strip()
    if not raw:
        return raw, False
    had = bool(re.search(r"\bFORMAT\b", raw, re.I))
    cleaned = _SQL_FORMAT_CLAUSE_RE.sub(" ", raw)
    cleaned = cleaned.strip().rstrip(";").strip()
    cleaned = re.sub(r"[ \t]+\n", "\n", cleaned)
    cleaned = re.sub(r" {2,}", " ", cleaned).strip()
    return cleaned, had


_MCP_FORMAT_STRIP_HINT = (
    "【FORMAT 纠偏】禁止在 query_ads_view 的 sql 中写 `FORMAT JSONEachRow` 或结尾分号；"
    "平台会自动 FORMAT。大结果请用 limit + 同窗 OFFSET/分页落 task/page_*.json，"
    "勿 toJSONString(groupArray(...))。"
)


def _extract_numbered_column_specs(text: str, *, allow_formula: bool = False) -> list[dict]:
    """Parse numbered lines into {header, spec} keeping parentheses in header."""
    out: list[dict] = []
    seen: set[str] = set()
    for m in _NUMBERED_COL_RE.finditer(text or ""):
        raw = re.sub(r"\s+", " ", (m.group(1) or "").strip())
        if not raw or len(raw) > 80:
            continue
        if raw.startswith((
            "目标", "数据源", "字段", "输出列", "计算", "分页", "SHELL", "完成标准",
            "SOP", "步骤", "需要资源", "依赖", "任务类型", "工具预算",
        )):
            continue
        # Tool / PLAN step lines must never become analyze-column todos
        if re.match(
            r"^(?:READ|WRITE|SHELL|MCP|HTTPMCP|FINAL|PATCH|THINK)\s*:",
            raw,
            re.I,
        ):
            continue
        if _EXPORT_TODO_JUNK_RE.search(raw) and "流水" not in raw:
            # Tool/role/prep prose must never become analyze-column todos
            continue
        header = raw
        spec = ""
        pm = re.search(r"[（(]([^（）()]+)[）)]\s*$", raw)
        if pm:
            spec = (pm.group(1) or "").strip()
        key = header.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append({"header": header, "spec": spec, "text": header})
    return out


def _extract_numbered_labels(text: str, *, allow_formula: bool = False) -> list[str]:
    return [c["header"] for c in _extract_numbered_column_specs(text, allow_formula=allow_formula)]


def _plan_output_columns_section(plan_text: str) -> str:
    """Slice PLAN「输出列」only; stop before steps/resources to avoid todo pollution."""
    if not plan_text:
        return ""
    m = re.search(r"输出列\s*[:：]?\s*", plan_text)
    if not m:
        return ""
    rest = plan_text[m.end():]
    stop = re.search(
        r"\n\s*[-*•]?\s*(?:"
        r"需要资源|依赖|步骤|计算与关联|计算|分页预算|分页|"
        r"SHELL|完成标准|数据源|字段与筛选|字段|目标|任务类型|工具预算"
        r")\s*[:：]",
        rest,
        re.I,
    )
    if stop:
        return rest[: stop.start()]
    return rest[:800]


def _parse_export_todos(user_message: str, plan_text: str = "") -> list[dict]:
    """Build checklist primarily from user numbered columns; PLAN only fills gaps."""
    specs = _extract_numbered_column_specs(user_message or "")
    if plan_text and len(specs) < 4:
        sec = _plan_output_columns_section(plan_text) or ""
        if not sec:
            # Fallback: only lines under an explicit 输出列 block; never whole PLAN
            sec = ""
        for c in _extract_numbered_column_specs(sec):
            if c["header"] not in {x["header"] for x in specs}:
                specs.append(c)
    # No invent of default business columns — model/clarify must name columns
    specs = specs[:16]
    todos: list[dict] = [
        {"id": "phase_discover", "text": "schema已确认", "done": False, "phase": "discover"},
        {"id": "phase_fetch", "text": "数据已落盘 task/page_*", "done": False, "phase": "fetch"},
    ]
    for i, col in enumerate(specs):
        todos.append({
            "id": f"col_{i}",
            "text": col["header"],
            "spec": col.get("spec") or "",
            "done": False,
            "phase": "analyze",
        })
    todos.append({"id": "phase_final", "text": "FINAL已输出", "done": False, "phase": "finalize"})
    return todos


def _soft_reconcile_export_column_todos(
    brief: str,
    todos: list[dict],
    *,
    plan_text: str = "",
) -> tuple[list[dict], str]:
    """If brief has N numbered cols and todos have fewer, re-parse from brief (soft).

    Never blocks FINAL / write — returns nudge text when expanded.
    """
    brief_n = _count_numbered_cols(brief or "")
    if brief_n < 4:
        return todos, ""
    cur_n = sum(1 for t in (todos or []) if isinstance(t, dict) and t.get("phase") == "analyze")
    if cur_n >= brief_n:
        return todos, ""
    richer = _parse_export_todos(brief or "", plan_text or "")
    richer_n = sum(
        1 for t in richer if isinstance(t, dict) and t.get("phase") == "analyze"
    )
    if richer_n <= cur_n:
        return todos, ""
    nudge = (
        f"列规划({cur_n})少于用户清单({brief_n})，已按原编号软对齐至 {richer_n} 列"
        "（含投注/返奖等；不拦截写表）"
    )
    return richer, nudge


def _is_pay_cohort_brief(text: str) -> bool:
    """True when brief asks for 充值用户 cohort (pay create_time), not new registers."""
    return bool(re.search(r"充值用户", text or ""))


def _export_tz_from_text(text: str) -> tuple[int, str]:
    """Resolve UTC offset hours + label; default ET when 美国时间/美东/美国."""
    if re.search(r"太平洋|PT\b|PDT|PST", text or "", re.I):
        return -7, "美国太平洋时间(UTC-7)"
    if re.search(r"美国时间|美东|东部|ET\b|EDT|EST", text or "", re.I):
        return -4, "美国东部时间(UTC-4)"
    if re.search(r"美国", text or ""):
        return -4, "美国东部时间(UTC-4)"
    return -4, "美国东部时间(UTC-4)"


def _parse_export_time_window(user_message: str) -> dict | None:
    """Parse date range from user message into ms bounds [start, end).

    Supports full ``YYYY-M-D … YYYY-M-D``, flex ``M-D至M至今`` / ``M-D至M-D``
    (missing year → current year in TZ), and 「美国时间」→ ET.
    """
    text = user_message or ""
    offset, tz_label = _export_tz_from_text(text)
    tz = timezone(timedelta(hours=offset))
    now_local = datetime.now(tz)

    y1 = mo1 = d1 = y2 = mo2 = d2 = None
    till_now = False
    m = _EXPORT_DATE_RANGE_RE.search(text)
    if m:
        try:
            y1, mo1, d1 = int(m.group("y1")), int(m.group("m1")), int(m.group("d1"))
            y2, mo2, d2 = int(m.group("y2")), int(m.group("m2")), int(m.group("d2"))
        except (TypeError, ValueError):
            m = None
    if not m:
        mt = _EXPORT_DATE_TO_NOW_RE.search(text)
        if mt:
            try:
                mo1, d1 = int(mt.group("m1")), int(mt.group("d1"))
            except (TypeError, ValueError):
                return None
            y1_raw = mt.group("y1")
            y1 = int(y1_raw) if y1_raw else now_local.year
            y2, mo2, d2 = y1, mo1, None
            till_now = True
        else:
            mf = _EXPORT_DATE_RANGE_FLEX_RE.search(text)
            if not mf:
                return None
            try:
                mo1, d1 = int(mf.group("m1")), int(mf.group("d1"))
                mo2 = int(mf.group("m2"))
            except (TypeError, ValueError):
                return None
            y1_raw, y2_raw = mf.group("y1"), mf.group("y2")
            d2_raw = mf.group("d2")
            till_now = bool(mf.group("till_now"))
            y1 = int(y1_raw) if y1_raw else now_local.year
            y2 = int(y2_raw) if y2_raw else y1
            if d2_raw:
                d2 = int(d2_raw)
            elif till_now:
                d2 = None  # resolved below from "today"
            else:
                # End month only → last calendar day of that month
                if mo2 == 12:
                    d2 = 31
                else:
                    d2 = (datetime(y2, mo2 + 1, 1, tzinfo=tz) - timedelta(days=1)).day
            if y2 < y1 or (y2 == y1 and mo2 < mo1):
                y2 = y1 + 1

    try:
        start = datetime(y1, mo1, d1, 0, 0, 0, tzinfo=tz)
        if till_now or d2 is None:
            today = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
            end = today + timedelta(days=1)
            y2, mo2, d2 = today.year, today.month, today.day
        else:
            end = datetime(y2, mo2, d2, 0, 0, 0, tzinfo=tz) + timedelta(days=1)
    except (TypeError, ValueError):
        return None
    start_ms = int(start.timestamp() * 1000)
    end_ms = int(end.timestamp() * 1000)
    label = f"{tz_label} {y1:04d}-{mo1:02d}-{d1:02d} 至 {y2:04d}-{mo2:02d}-{d2:02d}"
    cohort = "pay" if _is_pay_cohort_brief(text) else "register"
    return {
        "tz_label": tz_label,
        "utc_offset_hours": offset,
        "start_ms": start_ms,
        "end_ms": end_ms,
        "label": label,
        "cohort": cohort,
    }


def _query_covers_time_window(args: dict, tw: dict | None) -> bool:
    if not tw:
        return True
    start_s, end_s = str(tw.get("start_ms")), str(tw.get("end_ms"))
    if not start_s or not end_s or start_s == "None" or end_s == "None":
        return True
    sql = str((args or {}).get("sql") or "")
    if start_s in sql and end_s in sql:
        return True
    # also accept hint substring pieces
    if tw.get("sql_hint") and str(tw["sql_hint"]) in sql:
        return True
    where = (args or {}).get("where")
    if isinstance(where, dict) and where:
        blob = json.dumps(where, ensure_ascii=False, default=str)
        if start_s in blob and end_s in blob:
            return True
        for key in ("create_time", "register_time"):
            val = where.get(key)
            if val is None:
                continue
            val_s = (
                json.dumps(val, ensure_ascii=False, default=str)
                if isinstance(val, (dict, list))
                else str(val)
            )
            if start_s in val_s and end_s in val_s:
                return True
    return False


def _is_time_windowed_export_role(role: str) -> bool:
    return (role or "").strip() in ("user", "pay", "cash", "bet")


def _is_user_info_view(view: str) -> bool:
    return _VIEW_IS_USER(view)


def _max_pages_for_view(
    view: str,
    *,
    pay_cohort: bool = False,
    fact_page_cap: int | None = None,
    user_page_cap: int | None = None,
) -> int:
    if _is_user_info_view(view):
        if user_page_cap is not None and int(user_page_cap) > 0:
            return int(user_page_cap)
        return _EXPORT_MAX_PAGES_USER
    role = _view_category(view)
    if role in ("channel", "game"):
        return _EXPORT_MAX_PAGES_DIM
    if role in ("pay", "cash", "bet"):
        if fact_page_cap is not None and int(fact_page_cap) > 0:
            return int(fact_page_cap)
        if pay_cohort:
            return _EXPORT_MAX_PAGES_PER_VIEW_PAY_COHORT
        return _EXPORT_MAX_PAGES_PER_VIEW
    return _EXPORT_MAX_PAGES_PER_VIEW


def _needed_pages_for_estimate(
    estimate: int | None,
    *,
    page_limit: int = _EXPORT_PAGE_LIMIT,
) -> int:
    """Pages needed for estimate with +2 slack."""
    if not estimate or int(estimate) <= 0:
        return 0
    lim = max(1, int(page_limit))
    return (int(estimate) + lim - 1) // lim + 2


def _compute_user_page_cap(cohort_uid_estimate: int | None = None) -> int:
    """Dynamic user_info page cap from cohort estimate (once-pull-full)."""
    needed = _needed_pages_for_estimate(
        cohort_uid_estimate, page_limit=_EXPORT_USER_PAGE_LIMIT,
    )
    if needed <= 0:
        return _EXPORT_MAX_PAGES_USER
    # Soft: estimate known → climb toward USER_MAX faster (+1 headroom)
    return min(
        _EXPORT_MAX_PAGES_USER_MAX,
        max(_EXPORT_MAX_PAGES_USER, needed + 1),
    )


def _compute_fact_page_cap(
    *,
    pay_cohort: bool = False,
    cohort_uid_estimate: int | None = None,
) -> int:
    """Dynamic per-fact-view page cap from COUNT estimate (once-pull-full)."""
    base = (
        _EXPORT_MAX_PAGES_PER_VIEW_PAY_COHORT
        if pay_cohort
        else _EXPORT_MAX_PAGES_PER_VIEW
    )
    needed = _needed_pages_for_estimate(cohort_uid_estimate)
    if needed <= 0:
        return base
    # Soft: estimate known → climb toward FACT_PAGE_CAP_MAX faster
    return min(_EXPORT_FACT_PAGE_CAP_MAX, max(base, needed + 2))


def _compute_full_fetch_budget(
    *,
    type_b: bool,
    pay_cohort: bool,
    fact_page_cap: int,
    current: int = 0,
    user_page_cap: int | None = None,
) -> int:
    """Raise query budget so user + 3 fact roles can each reach short-page under cap."""
    if not type_b:
        return current or _EXPORT_QUERY_BUDGET_DEFAULT
    user_cap = (
        int(user_page_cap)
        if user_page_cap is not None and int(user_page_cap) > 0
        else _EXPORT_MAX_PAGES_USER
    )
    need = (
        _EXPORT_MAX_PAGES_DIM * 2
        + max(1, user_cap)
        + 3 * max(1, int(fact_page_cap))
        + 2  # COUNT + slack
        + max(2, min(6, _EXPORT_AUTO_OFFSET_MAX // 2))  # auto-OFFSET headroom
    )
    base = current or (
        _EXPORT_QUERY_BUDGET_TYPE_B_PAY_MAX if pay_cohort else _EXPORT_QUERY_BUDGET_TYPE_B
    )
    return min(
        _EXPORT_QUERY_BUDGET_FULL_FETCH_MAX,
        max(base, need, int(fact_page_cap) * 3),
    )


def _normalize_export_fetch_budget(
    *,
    requested_budget: int,
    current_cap: int,
    type_b: bool,
    pay_cohort: bool,
    full_fetch: bool,
    fact_page_cap: int,
    user_page_cap: int,
) -> tuple[int, int]:
    """Clamp PLAN budget, but keep Type-B/pay-cohort above engine minimums."""
    floor = _EXPORT_QUERY_BUDGET_FLOOR
    cap = max(int(current_cap or 0), floor)
    desired = max(int(requested_budget or 0), floor)
    if type_b:
        cap = max(cap, _EXPORT_QUERY_BUDGET_TYPE_B_MAX)
        desired = max(desired, _EXPORT_QUERY_BUDGET_TYPE_B)
    if type_b and pay_cohort:
        cap = max(cap, _EXPORT_QUERY_BUDGET_TYPE_B_PAY_MAX)
        desired = max(desired, _EXPORT_QUERY_BUDGET_TYPE_B_PAY_MAX)
    if type_b and (full_fetch or pay_cohort):
        desired = _compute_full_fetch_budget(
            type_b=True,
            pay_cohort=pay_cohort,
            fact_page_cap=fact_page_cap,
            current=desired,
            user_page_cap=user_page_cap,
        )
        cap = max(cap, desired)
    cap = min(_EXPORT_QUERY_BUDGET_FULL_FETCH_MAX, cap)
    return max(floor, min(desired, cap)), cap


def _sql_base_for_pagination(
    sql_now: str,
    role: str,
    time_window: dict | None,
) -> str:
    """Strip LIMIT/OFFSET from the already-executed bound-resource SQL."""
    base = (sql_now or "").strip().rstrip(";")
    if base and not _is_count_sql(base):
        base = re.sub(r"(?is)\s+LIMIT\s+\d+(\s+OFFSET\s+\d+)?\s*$", "", base).strip()
        base = re.sub(r"(?is)\s+OFFSET\s+\d+\s*$", "", base).strip()
        if base:
            return base
    del role, time_window
    return ""


def _should_auto_offset_continue(
    *,
    phase: str,
    last_page_rows: int,
    pages_done: int,
    page_cap: int,
    budget_left: int,
    auto_done: int,
    auto_max: int = _EXPORT_AUTO_OFFSET_MAX,
    full_page_rows: int | None = None,
    page_limit: int | None = None,
    view: str | None = None,
) -> bool:
    """True when engine should fire another same-view OFFSET page."""
    if (phase or "") != "fetch":
        return False
    if auto_done >= max(0, int(auto_max)):
        return False
    if budget_left < 2:
        return False
    if pages_done >= max(1, int(page_cap)):
        return False
    eff_limit = (
        int(page_limit)
        if page_limit is not None and int(page_limit) > 0
        else (_page_limit_for_view(view) if view else None)
    )
    # Soft: ~30-row remote default under a large limit — do not OFFSET (wrong stride)
    if eff_limit and _looks_like_remote_tiny_page_cap(
        last_page_rows, requested_limit=eff_limit,
    ):
        return False
    if full_page_rows is not None and int(full_page_rows) > 0:
        thresh = int(full_page_rows)
    elif eff_limit:
        thresh = _full_page_row_threshold(eff_limit)
    elif view:
        thresh = _full_page_row_threshold(_page_limit_for_view(view))
    else:
        thresh = _EXPORT_FULL_PAGE_ROWS
    if int(last_page_rows or 0) < thresh:
        return False
    return True


def _full_page_row_threshold(limit: int | None = None) -> int:
    """Treat ≥90% of page limit as a full page (must OFFSET continue)."""
    lim = int(limit) if limit is not None and int(limit) > 0 else _EXPORT_PAGE_LIMIT
    return max(1, int(lim * 0.9))


def _page_limit_for_view(view: str | None = None) -> int:
    """Per-view MCP page size: user_info uses wider pages; facts default 1000."""
    if view and _is_user_info_view(str(view)):
        return _EXPORT_USER_PAGE_LIMIT
    return _EXPORT_PAGE_LIMIT


def _is_full_page_rows(
    row_count: int | None,
    *,
    view: str | None = None,
    limit: int | None = None,
) -> bool:
    """True when row_count looks like a full MCP page for this view/limit."""
    if limit is not None and int(limit) > 0:
        thresh = _full_page_row_threshold(int(limit))
    elif view:
        thresh = _full_page_row_threshold(_page_limit_for_view(view))
    else:
        thresh = _EXPORT_FULL_PAGE_ROWS
    return int(row_count or 0) >= thresh


def _looks_like_remote_tiny_page_cap(
    row_count: int | None,
    *,
    requested_limit: int | None = None,
) -> bool:
    """True when MCP likely returned ~30-row server default despite a large request."""
    try:
        lim = int(requested_limit) if requested_limit is not None else 0
    except (TypeError, ValueError):
        lim = 0
    if lim < 500:
        return False
    n = int(row_count or 0)
    if n < _REMOTE_TINY_PAGE_LO or n > _REMOTE_TINY_PAGE_HI:
        return False
    return n < _full_page_row_threshold(lim)


def _is_short_page_complete(
    row_count: int | None,
    *,
    view: str | None = None,
    limit: int | None = None,
) -> bool:
    """True when this page is a real short page (fetch complete for the view).

    Soft: ~30 rows under a large requested limit is NOT complete — remote default
    must not mark user_fetch_complete / stop once-pull-full.
    """
    lim = (
        int(limit)
        if limit is not None and int(limit) > 0
        else _page_limit_for_view(view)
    )
    if _looks_like_remote_tiny_page_cap(row_count, requested_limit=lim):
        return False
    return not _is_full_page_rows(row_count, view=view, limit=lim)


def _offset_for_pages_done(
    pages_done: int,
    *,
    view: str | None = None,
    limit: int | None = None,
) -> int:
    """OFFSET stride aligned to the page limit actually used for this view."""
    lim = (
        int(limit)
        if limit is not None and int(limit) > 0
        else _page_limit_for_view(view)
    )
    return max(0, int(pages_done or 0)) * max(1, lim)


def _soft_align_export_query_limit(args: dict | None) -> tuple[dict, str | None]:
    """Fill/raise query_ads_view limit for export fetch once-pull-full.

    Skips COUNT and probe (limit=1 / SQL LIMIT 1). Soft only — never blocks the call.
    """
    out = dict(args or {})
    sql = str(out.get("sql") or "")
    if _is_count_sql(sql):
        return out, None
    if re.search(r"(?is)\bLIMIT\s+1\s*$", (sql or "").strip()):
        return out, None
    try:
        raw_lim = out.get("limit")
        lim_i = (
            int(raw_lim)
            if raw_lim is not None and str(raw_lim).strip() != ""
            else None
        )
    except (TypeError, ValueError):
        lim_i = None
    if lim_i == 1:
        return out, None
    view = str(out.get("view") or "").strip()
    target = _page_limit_for_view(view)
    if lim_i is None:
        out["limit"] = target
        return out, f"已补全 limit={target}（导出分页默认）"
    half = max(1, target // 2)
    if lim_i < _EXPORT_TINY_PAGE_LIMIT or lim_i < half:
        out["limit"] = target
        return out, f"已将过小 limit={lim_i} 抬至 {target}（避免远端短页假完成）"
    return out, None


def _reply_looks_like_underfetch_myth(text: str) -> bool:
    """True when model invents 30-row / 8-tool / must-async-dbt platform limits."""
    t = text or ""
    if not t.strip():
        return False
    myths = (
        r"30\s*行",
        r"单次\s*query[^\n]{0,24}30",
        r"工具预算\s*8",
        r"8\s*次\s*工具",
        r"326\s*轮",
        r"143\s*页",
        r"(?:须|必须|只能).{0,12}(?:dbt|异步|宿主机).{0,16}(?:导出|拉全)",
    )
    return any(re.search(p, t, re.I) for p in myths)


def _soft_rebut_underfetch_myth() -> str:
    return (
        "【取数纠偏】平台导出分页默认 fact limit=1000、user limit=2000；"
        f"query 预算 Type-B/full_fetch 可达约 {_EXPORT_QUERY_BUDGET_FULL_FETCH_MAX}，"
        "不是 30 行/页或 generic 8 次工具。"
        "请按默认页长 MCP + OFFSET 续翻至短页拉全；勿改推 dbt/异步放弃一次拉全。"
    )


def _parse_count_from_mcp_rows(rows: list | None) -> int | None:
    """Extract COUNT / count(DISTINCT uid) result from a 1-row MCP payload."""
    if not rows or not isinstance(rows, list):
        return None
    row0 = rows[0] if rows else None
    if not isinstance(row0, dict):
        return None
    prefer = (
        "cnt", "count", "c", "total", "n", "uid_cnt", "cnt_uid",
        "COUNT", "Cnt", "total_count",
    )
    for key in prefer:
        if key in row0:
            try:
                n = int(float(row0[key]))
                if 1 <= n <= 5_000_000:
                    return n
            except (TypeError, ValueError):
                continue
    # Only accept anonymous single-column COUNT rows (not data rows with uid/amount)
    if len(rows) == 1 and len(row0) == 1:
        for k, v in row0.items():
            if any(x in str(k).lower() for x in ("uid", "id", "amount", "time", "status")):
                return None
            try:
                n = int(float(v))
                if 10 <= n <= 5_000_000:  # skip tiny ids mistaken as counts
                    return n
            except (TypeError, ValueError):
                continue
    return None


def _is_count_sql(sql: str) -> bool:
    return bool(re.search(r"(?is)\bcount\s*\(", sql or ""))


def _sql_with_offset(sql: str, offset: int, *, limit: int = _EXPORT_PAGE_LIMIT) -> str:
    """Append/replace LIMIT+OFFSET on a SELECT (no FORMAT)."""
    base = (sql or "").strip().rstrip(";")
    base = re.sub(r"(?is)\s+LIMIT\s+\d+(\s+OFFSET\s+\d+)?\s*$", "", base).strip()
    base = re.sub(r"(?is)\s+OFFSET\s+\d+\s*$", "", base).strip()
    if not re.search(r"(?is)\bORDER\s+BY\b", base):
        # Stable pagination hint; ClickHouse accepts without ORDER BY but OFFSET is safer with it
        if re.search(r"(?is)\bGROUP\s+BY\s+uid\b", base):
            base = f"{base} ORDER BY uid"
        elif "create_time" in base.lower():
            base = f"{base} ORDER BY create_time, uid"
        elif "register_time" in base.lower():
            base = f"{base} ORDER BY register_time, uid"
    return f"{base} LIMIT {int(limit)} OFFSET {max(0, int(offset))}"


def _auto_offset_for_node_page(auto_done: int, *, limit: int = _EXPORT_PAGE_LIMIT) -> int:
    """Next OFFSET for a single query node after its initial offset-0 page."""
    return (max(0, int(auto_done)) + 1) * max(1, int(limit))


def _format_offset_mcp_example(
    view: str,
    sql_base: str,
    pages_done: int,
    *,
    limit: int = _EXPORT_PAGE_LIMIT,
) -> str:
    offset = max(0, int(pages_done)) * int(limit)
    sql = _sql_with_offset(sql_base, offset, limit=limit)
    return (
        f'MCP: query_ads_view {{"view":"{view}",'
        f'"sql":"{sql}","limit":{int(limit)}}}'
    )


_UID_BATCH_NARROW_COLS = "uid"


def _user_select_cols_from_plan(column_plan: list | None) -> str:
    """Select only identity fields already requested by the bound column plan."""
    cols: set[str] = set()
    for col in column_plan or []:
        if not isinstance(col, dict):
            continue
        for s in col.get("sources") or []:
            if isinstance(s, dict) and str(s.get("role") or "") == "user":
                cols.update(str(f).strip() for f in (s.get("fields") or []) if str(f).strip())
        spec = col.get("agg_spec") if isinstance(col.get("agg_spec"), dict) else {}
        if str(spec.get("role") or "") == "user" and spec.get("select"):
            for part in str(spec.get("select") or "").split(","):
                p = part.strip()
                if p and re.match(r"^[a-zA-Z_][a-zA-Z0-9_]*$", p):
                    cols.add(p)
    if not cols:
        return "uid"
    ordered = ["uid"] + sorted(c for c in cols if c != "uid")
    return ", ".join(ordered)


def _format_uid_in_list(uids: list[str]) -> str:
    """ClickHouse-safe uid list for IN (...)."""
    parts: list[str] = []
    for u in uids:
        s = str(u).strip()
        if not s:
            continue
        if re.fullmatch(r"-?\d+", s):
            parts.append(s)
        else:
            parts.append("'" + s.replace("'", "\\'") + "'")
    return ", ".join(parts)


def _uid_batch_sql(
    uids: list[str],
    *,
    view: str,
    select_cols: str = "",
) -> str:
    """SELECT requested cols from a bound identity resource by cohort uid."""
    resource = str(view or "").strip()
    if not resource:
        return ""
    cols = (select_cols or "").strip() or "uid"
    if "uid" not in {c.strip() for c in cols.split(",")}:
        cols = f"uid, {cols}"
    in_list = _format_uid_in_list(uids)
    if not in_list:
        return ""
    return f"SELECT {cols} FROM ads.{resource} WHERE uid IN ({in_list})"


def _uid_batch_probe_sql(uid: str, *, view: str) -> str:
    """Liveness probe for one cohort uid on a bound identity resource."""
    resource = str(view or "").strip()
    if not resource:
        return ""
    in_list = _format_uid_in_list([uid])
    if not in_list:
        return ""
    return (
        f"SELECT uid FROM ads.{resource} "
        f"WHERE uid IN ({in_list}) LIMIT 1"
    )


def _clip_mcp_error_text(text: str, *, max_chars: int = 400) -> str:
    """Truncate MCP/CH error for step/progress (keep actionable head)."""
    s = re.sub(r"\s+", " ", (text or "").strip())
    if not s:
        return ""
    n = max(40, int(max_chars))
    if len(s) <= n:
        return s
    return s[: n - 1] + "…"


def _format_mcp_exc_for_step(exc: BaseException) -> str:
    """Non-empty exception text for query-graph step messages."""
    name = type(exc).__name__
    detail = str(exc).strip() or repr(exc)
    return f"{name}: {detail}"


def _uid_batch_fail_message(
    view: str,
    *,
    mcp_error: str = "",
    detail: str = "",
) -> str:
    """Human-readable uid-batch failure; includes MCP snippet when present."""
    bits = [
        f"【uid批次】远程失败 view={view}",
        "时间窗已用于 pay.create_time；user_info 按 uid IN 补拉（不用 register_time）",
    ]
    if detail:
        bits.append(detail)
    err = _clip_mcp_error_text(mcp_error)
    if err:
        bits.append(f"MCP: {err}")
    return "；".join(bits)


def _uid_batch_step_hint(*, batch_no: int, uid_count: int) -> str:
    return (
        f"【uid批次】user_info batch={batch_no} uids={uid_count}；"
        "时间窗已用于 pay.create_time；user_info 按 uid IN 补拉（不用 register_time）"
    )


def _view_for_export_role(
    role: str,
    *,
    dim_views: dict[str, str] | None = None,
) -> str:
    """Return an explicitly discovered dimension resource, never a role default."""
    r = (role or "").strip().lower()
    dims = dim_views if isinstance(dim_views, dict) else {}
    if r in ("channel", "game") and dims.get(r):
        return str(dims[r]).strip()
    return ""


def _views_from_column_plan_for_role(
    column_plan: list | None,
    role: str,
) -> list[str]:
    """Collect view names for a role from column_plan sources / agg_spec."""
    r = (role or "").strip().lower()
    if not r:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for col in column_plan or []:
        if not isinstance(col, dict):
            continue
        if str(col.get("binding_status") or "").strip().lower() != "bound":
            continue
        for s in col.get("sources") or []:
            if not isinstance(s, dict):
                continue
            if str(s.get("role") or "").strip().lower() != r:
                continue
            v = str(s.get("view") or "").strip()
            if v and v not in seen:
                seen.add(v)
                out.append(v)
        agg = col.get("agg_spec") if isinstance(col.get("agg_spec"), dict) else {}
        if str(agg.get("role") or "").strip().lower() == r:
            v = str(agg.get("view") or "").strip()
            if v and v not in seen:
                seen.add(v)
                out.append(v)
        # Role inferred from view name when plan omits role on agg
        if not agg.get("role") and agg.get("view"):
            v = str(agg.get("view") or "").strip()
            if v and _view_category(v) == r and v not in seen:
                seen.add(v)
                out.append(v)
    return out


def _pick_preferred_export_view(views: list[str]) -> str:
    """Prefer aggregate/stat views over raw *_log when multiple candidates."""
    scored: list[tuple[int, str]] = []
    for v in views:
        low = (v or "").lower()
        if not low:
            continue
        score = 0
        if "everyday" in low or "betstat" in low:
            score += 20
        elif "stat" in low:
            score += 10
        if "user_bet_log" in low:
            score -= 20
        elif low.endswith("_log"):
            score -= 5
        scored.append((score, v))
    if not scored:
        return ""
    scored.sort(key=lambda x: (-x[0], x[1]))
    return scored[0][1]


def _view_for_export_pull(
    role: str,
    *,
    column_plan: list | None = None,
    whitelist: list[str] | None = None,
    dim_views: dict[str, str] | None = None,
) -> str:
    """Resolve engine-pull view from column_plan / whitelist / dims — no bet→user_bet_log default."""
    r = (role or "").strip().lower()
    if not r:
        return ""
    dims = dim_views if isinstance(dim_views, dict) else {}
    if r in ("channel", "game") and dims.get(r):
        return str(dims[r]).strip()

    plan_views = _views_from_column_plan_for_role(column_plan, r)
    if plan_views:
        return _pick_preferred_export_view(plan_views)

    wl = [str(x).strip() for x in (whitelist or []) if str(x).strip()]
    if wl:
        # Prefer aggregate betstat over raw bet log when both present
        role_hits = [v for v in wl if _view_category(v) == r]
        if r == "bet":
            preferred = _pick_preferred_export_view(role_hits or [
                v for v in wl
                if "betstat" in v.lower()
                or "everyday" in v.lower()
                or ("bet" in v.lower() and "user_bet_log" not in v.lower())
            ])
            if preferred:
                return preferred
            # LLM explicitly bound raw bet log — allow only via whitelist hit
            for v in wl:
                if "user_bet_log" in v.lower() or _VIEW_IS_BET(v):
                    return v
            return ""
        if role_hits:
            return _pick_preferred_export_view(role_hits)
        default = ""
        if default and default in wl:
            return default
        for v in wl:
            if r and r in v.lower():
                return v

    return ""


def _build_engine_fetch_mcp(
    role: str,
    time_window: dict | None,
    *,
    view: str = "",
    column_plan: list | None = None,
    whitelist: list[str] | None = None,
    dim_views: dict[str, str] | None = None,
    pages_done: int = 0,
    limit: int = _EXPORT_PAGE_LIMIT,
) -> str:
    """Build MCP: query_ads_view line for engine-driven pull of one planned role."""
    r = (role or "").strip().lower()
    view = (view or "").strip() or _view_for_export_pull(
        r,
        column_plan=column_plan,
        whitelist=whitelist,
        dim_views=dim_views,
    )
    if not view:
        return ""
    tw = time_window if isinstance(time_window, dict) else {}
    if r == "user" or _is_user_info_view(view):
        lim = _EXPORT_USER_PAGE_LIMIT
    else:
        lim = int(limit) if limit else _EXPORT_PAGE_LIMIT
    if r in ("channel", "game"):
        args: dict = {"view": view, "limit": min(2000, lim)}
        return "MCP: query_ads_view " + json.dumps(args, ensure_ascii=False)
    # Pay cohort user: no unfiltered SQL — caller must use uid-batch pull
    if r == "user" and str(tw.get("cohort") or "").strip() == "pay":
        return ""
    args = {"view": view, "limit": lim}
    return "MCP: query_ads_view " + json.dumps(args, ensure_ascii=False)


def _should_soft_block_export_read(
    *,
    phase: str,
    missing_roles: list[str] | None,
    has_root_xlsx: bool = False,
) -> bool:
    """Block READ idle loops during fetch/analyze until roles ready or xlsx exists."""
    if (phase or "") not in ("fetch", "analyze"):
        return False
    if has_root_xlsx:
        return False
    miss = [str(r).strip() for r in (missing_roles or []) if str(r).strip()]
    return bool(miss) or (phase or "") == "fetch"


def _should_soft_block_metric_tool(tool: str, *, phase: str) -> bool:
    """Soft-block query_ads_metric during export fetch (column plan uses views)."""
    if (phase or "") != "fetch":
        return False
    t = (tool or "").lower()
    return "metric" in t and "query" in t


def _format_llm_mcp_progress(
    *,
    llm_iter: int,
    max_iters: int,
    mcp_query_count: int,
    mcp_budget: int,
) -> str:
    return (
        f"LLM {max(0, int(llm_iter))}/{max(1, int(max_iters))} · "
        f"MCP {max(0, int(mcp_query_count))}/{max(0, int(mcp_budget))}"
    )


def _export_analyze_max_rounds(todos: list[dict]) -> int:
    col_n = sum(1 for t in todos if t.get("phase") == "analyze")
    return max(
        _EXPORT_ANALYZE_MAX_ROUNDS,
        col_n // 2 if col_n else _EXPORT_ANALYZE_MAX_ROUNDS,
    )


def _is_type_b_export(
    view_mode: str,
    column_plan: list[dict] | None,
) -> bool:
    """True for column-driven multi-role export (not single_view).

    Opens full_fetch / Type-B budgets. Distinct from
    `_type_b_prefilled_plan_ready`, which never skips schema discovery.
    """
    if (view_mode or "").strip().lower() == "single_view":
        return False
    return any(
        isinstance(c, dict) and str(c.get("header") or "").strip()
        for c in (column_plan or [])
    )


def _type_b_prefilled_plan_ready(
    todos: list[dict],
    target_roles: list[str],
    view_mode: str,
    *,
    column_plan: list[dict] | None = None,
) -> bool:
    """Always False: never skip discover/PLAN via static column seed.

    Static column plans are only a seed/fallback. Runtime MCP schema
    (list_ads_views + describe_ads_view) must shape PLAN/SQL.
    Auto-accept of structured PLAN uses `_should_auto_accept_export_plan`.
    """
    del todos, target_roles, view_mode, column_plan
    return False


def _shell_looks_like_xlsx_write(normalized: str) -> bool:
    cmd = normalized or ""
    if _SHELL_WRITE_XLSX_RE.search(cmd):
        return True
    if not _SHELL_PYTHON_SCRIPT_RE.search(cmd):
        return False
    # Scripts under task/ are usually prep helpers — not root deliverable writes
    if re.search(r"(?:^|[\s\"'=])/?(?:\.?/?)*task/", cmd) or re.search(
        r"\btask/[^\s\"']+\.py\b", cmd
    ):
        return False
    return True


def _shell_looks_like_prep_only(normalized: str) -> bool:
    """True when SHELL only writes/runs helpers under task/ without xlsx save."""
    cmd = normalized or ""
    if _SHELL_WRITE_XLSX_RE.search(cmd):
        return False
    return bool(
        re.search(r"\btask/[^\s\"']+\.py\b", cmd)
        or re.search(r"(?:tee|cat\s*>)\s+[^\s]*task/", cmd)
        or (re.search(r"\bpython3?\b", cmd, re.I) and "task/" in cmd)
    )


def _shell_looks_like_explore(normalized: str) -> bool:
    """True when SHELL is inspection-only (ls/head or python without write-xlsx)."""
    cmd = _shell_command(normalized or "")
    if not cmd.strip():
        return True
    if _shell_looks_like_prep_only(cmd):
        return True
    if _shell_looks_like_xlsx_write(cmd):
        return False
    if _SHELL_EXPLORE_RE.search(cmd):
        return True
    # python / pandas probing without writing deliverable
    if re.search(r"\b(?:python3?|pandas|json\.load|DataFrame)\b", cmd, re.I):
        return True
    return False


def _shell_counts_as_progress(normalized: str) -> bool:
    """Explore/prep SHELL is not effective progress for no_progress reset."""
    if _shell_looks_like_xlsx_write(normalized or ""):
        return True
    if _shell_looks_like_explore(normalized or ""):
        return False
    if _shell_looks_like_prep_only(_shell_command(normalized or "")):
        return False
    return True


def _reply_is_empty_idle(reply: str) -> bool:
    """True when reply has no tool/FINAL lines and little/no visible text (idle LLM)."""
    from app.services.intent_router import _strip_think_noise

    text = _strip_think_noise(reply or "")
    if re.search(
        r"(?im)^\s*(?:MCP|SHELL|WRITE|READ|FINAL|HTTPMCP|PATCH|THINK)\s*:",
        text,
    ):
        return False
    # Drop leftover protocol noise
    compact = re.sub(r"\s+", "", text)
    return len(compact) < 12


def _export_idle_early_finish_allowed(
    *,
    export_like: bool,
    export_phase: str,
    empty_llm_streak: int,
    has_root_deliverable: bool,
) -> bool:
    """Empty-LLM early FINAL only in analyze/finalize with a claimed root xlsx."""
    return (
        bool(export_like)
        and export_phase in ("analyze", "finalize")
        and int(empty_llm_streak or 0) >= 2
        and bool(has_root_deliverable)
    )


def _is_game_stat_or_bet_view(view: str) -> bool:
    v = (view or "").lower()
    return bool(
        "bet" in v
        or "gameuser" in v
        or "game_user" in v
        or ("stat" in v and "game" in v)
    )


_VIEW_IS_USER = lambda v: "user_info" in (v or "").lower() or "user_register" in (v or "").lower()
_VIEW_IS_PAY = lambda v: "pay" in (v or "").lower()
_VIEW_IS_CASH = lambda v: "cash" in (v or "").lower()
_VIEW_IS_BET = lambda v: "bet" in (v or "").lower()
_VIEW_IS_CHANNEL = lambda v: "channel" in (v or "").lower() and "user" not in (v or "").lower()
_VIEW_IS_GAME_DIM = lambda v: (lambda x: "game" in x and ("config" in x or "dict" in x or "dim" in x or "name" in x) and not _is_game_stat_or_bet_view(x))((v or "").lower())
def _VIEW_IS_FACT(view: str) -> str:
    """Return a semantic fact role, not a boolean predicate."""
    if _VIEW_IS_PAY(view):
        return "pay"
    if _VIEW_IS_CASH(view):
        return "cash"
    if _VIEW_IS_BET(view):
        return "bet"
    return ""
_VIEW_IS_DIM = lambda v: _VIEW_IS_CHANNEL(v) or _VIEW_IS_GAME_DIM(v)

def _view_category(view: str) -> str:
    """View name → category for routing (pattern matching, not a role registry)."""
    v = (view or "").lower()
    if not v:
        return ""
    if "user_info" in v or "user_register" in v:
        return "user"
    if "channel" in v and "user" not in v:
        return "channel"
    if "pay" in v:
        return "pay"
    if "cash" in v:
        return "cash"
    if "bet" in v:
        return "bet"
    if _is_game_stat_or_bet_view(v):
        return ""
    if "game" in v and ("config" in v or "dict" in v or "dim" in v or "name" in v):
        return "game"
    return ""

_VIEW_FROM_SQL_RE = re.compile(
    r"(?is)\bFROM\s+(?:ads\.)?(view_result_[a-zA-Z0-9_]+)\b",
)
def _export_analyze_column_texts(todos: list[dict] | None) -> list[str]:
    """Full analyze-phase column headers from todos (no truncation)."""
    out: list[str] = []
    for t in todos or []:
        if not isinstance(t, dict) or t.get("phase") != "analyze":
            continue
        text = str(t.get("text") or "").strip()
        if text:
            out.append(text)
    return out


def _find_recent_assistant_query_contract(history: list | None) -> dict[str, Any]:
    """Recover the last persisted model query contract from assistant metadata."""
    for item in reversed(list(history or [])):
        if str(getattr(item, "role", "") or "") != "assistant":
            continue
        raw_meta = getattr(item, "meta", None)
        if isinstance(raw_meta, str):
            try:
                meta = json.loads(raw_meta)
            except Exception:
                meta = None
        else:
            meta = raw_meta
        if not isinstance(meta, dict):
            continue
        contract = meta.get("query_contract")
        if not isinstance(contract, dict):
            export_contract = meta.get("export_contract")
            contract = (
                export_contract.get("query_contract")
                if isinstance(export_contract, dict)
                else None
            )
        if (
            isinstance(contract, dict)
            and is_executable_query_sql(str(contract.get("sql") or ""))
        ):
            return dict(contract)
    return {}


def _resolve_authoritative_query_contract(
    sandbox: Sandbox | None,
    run_id: str,
    state: dict[str, Any] | None,
) -> tuple[dict[str, Any], str]:
    """Load the persisted contract, preferring the run's latest SQL artifact."""
    source = state if isinstance(state, dict) else {}
    export_contract = (
        source.get("export_contract")
        if isinstance(source.get("export_contract"), dict)
        else {}
    )
    persisted = (
        export_contract.get("query_contract")
        if isinstance(export_contract.get("query_contract"), dict)
        else {}
    )
    contract = dict(persisted)
    sql_rel = ""
    if sandbox and run_id:
        root = ensure_workplace(sandbox.id)
        for filename in ("final_sql.sql", "draft_sql.sql"):
            candidate_rel = f"task/{run_id}/{filename}"
            candidate = root / candidate_rel
            if not candidate.is_file():
                continue
            try:
                artifact_sql = normalize_sql(candidate.read_text(encoding="utf-8"))
            except OSError:
                continue
            if is_executable_query_sql(artifact_sql):
                contract["sql"] = artifact_sql
                sql_rel = candidate_rel
                break
    if not is_executable_query_sql(str(contract.get("sql") or "")):
        return {}, ""
    if not contract.get("output_columns"):
        contract["output_columns"] = prior_headers_from_state(source)
    return contract, sql_rel


def _export_task_title(
    *,
    query_goal: str = "",
    source_brief: str = "",
    prior_state: dict[str, Any] | None = None,
) -> str:
    """Resolve a stable task title; revisions inherit the original title."""
    state = prior_state if isinstance(prior_state, dict) else {}
    prior_title = str(state.get("task_title") or "").strip()
    if prior_title:
        return prior_title[:80]
    raw = str(query_goal or "").strip()
    if not raw or state:
        raw = str(state.get("source_brief") or source_brief or raw).strip()
    raw = re.split(r"(?:输出列(?:如下)?|列(?:如下)?)[：:]", raw, maxsplit=1)[0]
    raw = raw.split("\n", 1)[0].strip()
    raw = re.sub(r"(?i)\b(?:xlsx|excel)\b", "", raw)
    raw = re.sub(r"\s+", " ", raw).strip(" ，,。；;:_-")
    return (raw[:80] or "导出任务")


def _prior_column_plan_from_state(prior_state: dict | None) -> list[dict]:
    """Best-effort prior column plan from resume state / persisted trace."""
    state = prior_state if isinstance(prior_state, dict) else {}
    rows = state.get("column_plan")
    if isinstance(rows, list):
        return [dict(x) for x in rows if isinstance(x, dict)]
    tr = state.get("trace") if isinstance(state.get("trace"), dict) else {}
    rows = tr.get("column_plan")
    if isinstance(rows, list):
        return [dict(x) for x in rows if isinstance(x, dict)]
    return []


def _format_binding_resource_schemas(
    schema_hints: dict[str, Any] | None,
) -> dict[str, str]:
    out: dict[str, str] = {}
    for view, hint in (schema_hints or {}).items():
        v = str(view or "").strip()
        if not v:
            continue
        fields = [str(x).strip() for x in (getattr(hint, "fields", None) or []) if str(x).strip()]
        comments = getattr(hint, "comments", None) or {}
        comment_bits = [
            f"{str(k).strip()}:{str(val).strip()}"
            for k, val in dict(comments).items()
            if str(k).strip() and str(val).strip()
        ]
        out[v] = (
            f"view={v}; comment={getattr(hint, 'view_comment', '')}; "
            f"fields={','.join(fields)}; field_comments={' | '.join(comment_bits[:24])}"
        )
    return out


def _format_export_task_anchor(
    *,
    source_brief: str = "",
    time_window: dict | None = None,
    column_headers: list[str] | None = None,
    target_roles: list[str] | None = None,
) -> str:
    """Compact always-on context so the model keeps the original export brief."""
    lines = ["【任务锚点】请始终按下列原始需求推进（勿丢列、勿改时间窗）。"]
    tw = time_window if isinstance(time_window, dict) else None
    pay_cohort = bool(tw and tw.get("cohort") == "pay") or _is_pay_cohort_brief(
        source_brief or ""
    )
    if tw and tw.get("label"):
        lines.append(f"时间窗：{tw.get('label')}")
        if tw.get("start_ms") is not None and tw.get("end_ms") is not None:
            lines.append(
                f"毫秒区间：[{tw.get('start_ms')}, {tw.get('end_ms')})"
            )
    if pay_cohort:
        lines.append("人群：充值用户 cohort（由已绑定资源的成功订单 uid 构造）。")
    cols = [str(c).strip() for c in (column_headers or []) if str(c).strip()]
    if cols:
        lines.append("输出列（须完整作表头，保留括号说明）：")
        for i, c in enumerate(cols, 1):
            lines.append(f"{i}.{c}")
    del target_roles
    brief = (source_brief or "").strip()
    if brief:
        # Keep full brief when short enough; otherwise columns+window already carry the contract
        if len(brief) <= 1800:
            lines.append("原始需求：")
            lines.append(brief)
        else:
            lines.append("原始需求（摘要）：" + re.sub(r"\s+", " ", brief)[:400] + "…")
            lines.append("（完整列清单见上方编号；时间窗见毫秒区间。）")
    lines.append(
        f"分页：默认 fact limit={_EXPORT_PAGE_LIMIT}、user limit={_EXPORT_USER_PAGE_LIMIT}；"
        "满页须同窗 OFFSET 至短页。"
        "勿按 30 行/页或 generic 8 次工具估算，也勿改推 dbt/异步放弃一次拉全。"
    )
    if pay_cohort:
        lines.append(
            "建议顺序：COUNT → pay（create_time 窗）续翻至短页建 uid cohort → "
            "按 uid 补 user → 仅列计划内维表/明细至短页 → "
            "SHELL 写当前目录 xlsx → FINAL。"
            "列 15/16 APTPAY 见 Skill export-report。"
            "一次拉全：满页必须 OFFSET 续翻；勿默认拉未声明的 bet。"
        )
    else:
        lines.append(
            "建议顺序：仅拉列计划/白名单内 role 至短页 → SHELL 写当前目录 xlsx → FINAL；"
            "勿默认 pay+cash+bet 齐拉。"
        )
    return "\n".join(lines)


def _format_shell_task_card(
    *,
    column_headers: list[str] | None = None,
    page_map: str = "",
    run_id: str = "",
    column_plan: list[dict] | None = None,
    fill_target_rel: str = "",
    focus_columns: list[str] | None = None,
) -> str:
    """Short strong card: LLM must SHELL-write Chinese headers (OpenClaw agent-led)."""
    cols = [str(c).strip() for c in (column_headers or []) if str(c).strip()]
    fill_target = str(fill_target_rel or "").strip()
    focus = [str(c).strip() for c in (focus_columns or []) if str(c).strip()]
    lines = [
        "【SHELL 写表任务卡】本阶段由你完成分析交付（引擎不代写中文分析表）。",
        "1) 脚本写到 `/tmp/build_report.py`，再 `SHELL: python3 /tmp/build_report.py`",
        "2) 用 pandas/openpyxl 读 `task/"
        + (run_id or "<run_id>")
        + "/page_*.json`，按【列规划】join/聚合后写到**当前目录**（非 task/）",
    ]
    if fill_target:
        lines.append(
            f"3) 建议覆盖已有交付 `{fill_target}`："
            + (
                ("仅更新列 " + "、".join(focus[:8]) + "；")
                if focus
                else ""
            )
            + "保留原表全部列与行，按用户ID合并后 `to_excel`/`save` 覆盖同名文件"
            "（勿另存新的用户分析_*.xlsx）。"
        )
        lines.append("4) 表头应为下列完整中文列名（与原接口一致），再单独一行 FINAL:")
    else:
        lines.append(
            "3) 表头必须为下列完整中文列名（保留括号说明），再单独一行 FINAL:"
        )
    if cols:
        for i, c in enumerate(cols[:16], 1):
            lines.append(f"   {i}.{c}")
    else:
        lines.append("   （见任务锚点输出列）")
    if column_plan:
        lines.append(format_column_plan_summary(column_plan))
    if page_map:
        lines.append("【task 页清单】\n" + page_map.strip())
    lines.append(
        "禁止：只 ls/读 json 不写表；禁止把英文原始宽表当分析交付；"
        "只使用列规划中的数据源，勿假设未拉的 role；"
        "口径见 Skill `export-report`（可用 SKILL_MD 再读）。"
    )
    return "\n".join(lines)


def _export_next_action_coach(
    *,
    phase: str = "",
    fetched_view_pages: dict[str, int] | None = None,
    target_roles: list[str] | None = None,
    covered_roles: list[str] | None = None,
    missing_roles: list[str] | None = None,
    time_window: dict | None = None,
    column_headers: list[str] | None = None,
    budget_left: int | None = None,
    dim_views: dict[str, str] | None = None,
    user_fetch_complete: bool = False,
    fact_need_continue: list[str] | None = None,
    cohort_uid_estimate: int | None = None,
    fact_page_cap: int | None = None,
) -> str:
    """Soft next-step guidance — COUNT / 短页齐套 / 可复制 OFFSET."""
    del dim_views  # view names live in Skill export-report §3
    pages = dict(fetched_view_pages or {})
    target = [str(r).strip() for r in (target_roles or []) if str(r).strip()]
    # Empty target = no invent (not even user)
    covered = {str(r).strip() for r in (covered_roles or []) if str(r).strip()}
    if not covered:
        for v, n in pages.items():
            if int(n or 0) <= 0:
                continue
            role = _view_category(v)
            if role:
                covered.add(role)
    miss = [str(r).strip() for r in (missing_roles or []) if str(r).strip()]
    if not miss:
        miss = [r for r in target if r not in covered]
    user_pages = sum(
        int(n or 0) for v, n in pages.items() if _is_user_info_view(v)
    )
    dim_miss = [r for r in miss if r in ("channel", "game")]
    fact_miss = [r for r in miss if r in ("pay", "cash", "bet")]
    fact_cont = [str(r).strip() for r in (fact_need_continue or []) if str(r).strip()]
    cols = [str(c).strip() for c in (column_headers or []) if str(c).strip()]
    tw = time_window if isinstance(time_window, dict) else None
    pay_cohort = bool(tw and tw.get("cohort") == "pay")
    pay_pages = sum(
        int(n or 0) for v, n in pages.items() if _VIEW_IS_PAY(v)
    )
    has_any_data_page = any(int(n or 0) > 0 for n in pages.values())

    lines = ["【下一步建议】（列驱动=只拉列规划内 role 至短页；SOP 见 Skill `export-report`）"]
    if phase:
        lines.append(f"当前阶段：{phase}")
    if budget_left is not None:
        lines.append(f"剩余 query 预算：{max(0, int(budget_left))}")
    if fact_page_cap:
        lines.append(f"事实表页帽：{int(fact_page_cap)}（动态）")
    if tw and tw.get("label"):
        lines.append(f"时间窗：{tw.get('label')}")
    if cohort_uid_estimate:
        est = int(cohort_uid_estimate)
        user_pages_est = _needed_pages_for_estimate(
            est, page_limit=_EXPORT_USER_PAGE_LIMIT,
        )
        fact_pages_est = _needed_pages_for_estimate(
            est, page_limit=_EXPORT_PAGE_LIMIT,
        )
        lines.append(
            f"cohort 目标≈{est}；按页长估算 user≈{user_pages_est} 页"
            f"（limit={_EXPORT_USER_PAGE_LIMIT}）、事实≈{fact_pages_est} 页"
            f"（limit={_EXPORT_PAGE_LIMIT}）——勿按 30 行/页推算成上百页"
        )
    else:
        lines.append(
            f"分页默认 fact limit={_EXPORT_PAGE_LIMIT}、"
            f"user limit={_EXPORT_USER_PAGE_LIMIT}（满页 OFFSET 至短页）"
        )
    if pay_cohort:
        lines.append("人群模式：充值用户（COUNT → pay create_time 至短页 → 补 user）")
    if cols:
        lines.append("输出列数：" + str(len(cols)) + "（按列规划取数，勿默认全量拉取）")
    if miss:
        lines.append("仍缺 role（列规划内）：" + "、".join(miss))
    elif fact_cont:
        lines.append("角色页已有但明细仍满页，须续翻：" + "、".join(fact_cont))
    elif covered:
        lines.append("列规划 role 已齐且短页（" + "、".join(sorted(covered)) + "）")

    step = 1
    # Resource discovery/binding precedes any count; do not invent a count source.
    if (
        phase in ("", "init", "plan", "fetch", "discover")
        and not cohort_uid_estimate
        and not has_any_data_page
    ):
        lines.append(f"{step}) 对已绑定资源执行 COUNT 或样例查询，核对时间窗与人群。")
        step += 1

    if pay_cohort and pay_pages <= 0 and "pay" in target:
        lines.append(
            f"{step}) 先 MCP 拉 pay（create_time 窗），满页则 OFFSET 续翻至短页"
        )
        step += 1
    if user_pages <= 0 and "user" in target:
        if pay_cohort:
            lines.append(
                f"{step}) 按 pay cohort uid 补拉 user_info（勿用 register_time 当人群；"
                "勿跨 task/旧run 拼 user）"
            )
        else:
            lines.append(
                f"{step}) 先 MCP 拉 user_info（时间窗 sql 见任务锚点；满页 OFFSET 续翻）"
            )
        step += 1
    elif (
        not user_fetch_complete
        and user_pages > 0
        and "user" in target
        and user_pages < _EXPORT_MAX_PAGES_USER
    ):
        lines.append(f"{step}) 已绑定身份资源未短页：复用该节点已执行 SQL 继续分页。")
        step += 1
    if dim_miss:
        lines.append(
            f"{step}) 拉维表各 1 页：{'、'.join(dim_miss)}"
            "（视图名见 export-report / list_ads_views）"
        )
        step += 1
    if fact_miss:
        remain_facts = [
            r for r in fact_miss if not (pay_cohort and r == "pay" and pay_pages <= 0)
        ]
        if remain_facts:
            lines.append(
                f"{step}) 拉明细各至少 1 页后续翻至短页：{'、'.join(remain_facts)}"
            )
            step += 1
    if fact_cont:
        lines.append(f"{step}) 【一次拉全】下列明细末页仍满页，请 OFFSET 续翻：")
        for role in fact_cont:
            view = ""
            for v, n in pages.items():
                if _view_category(v) == role and int(n or 0) > 0:
                    view = v
                    break
            pages_done = sum(
                int(n or 0) for v, n in pages.items() if _view_category(v) == role
            )
            if view:
                lines.append(
                    f"   {role}: MCP: query_ads_view "
                    f'{{"view":"{view}","limit":{_EXPORT_PAGE_LIMIT}}}'
                )
            else:
                lines.append(
                    f"   {role}: 按已绑定/已拉 view 续翻（勿默认 user_bet_log）"
                )
        step += 1

    ready_to_write = not miss and not fact_cont
    if ready_to_write and cols:
        lines.append(
            f"{step}) 短页齐套：SHELL join 写**当前目录**中文表头 xlsx，再 FINAL。"
            "表头须含："
        )
        for i, c in enumerate(cols[:16], 1):
            lines.append(f"   {i}.{c}")
    elif ready_to_write:
        lines.append(
            f"{step}) 短页齐套：SHELL 写当前目录中文表头 xlsx，再 FINAL。"
        )
    elif phase in ("analyze", "finalize") and fact_cont:
        lines.append(
            f"{step}) 建议改参续查满页明细后再写表；若预算已尽须在 FINAL 标明「未标完整」。"
        )
    elif phase in ("analyze", "finalize"):
        lines.append(
            f"{step}) 优先 SHELL 按任务锚点写完整表头；缺页列可暂空。"
            "勿把英文原始宽表当作分析交付。"
        )

    return "\n".join(lines)


def _prefer_richer_export_brief(*candidates: str) -> str:
    """Pick the brief with more numbered columns / longer export signal."""
    best = ""
    best_score = -1
    for raw in candidates:
        text = (raw or "").strip()
        if not text:
            continue
        cols = _count_numbered_cols(text)
        score = cols * 100 + min(len(text), 2000) // 10
        if _RE_NEW_EXPORT_SIGNAL.search(text):
            score += 50
        if score > best_score:
            best_score = score
            best = text
    return best


def _fact_role_page_count(fetched_view_pages: dict[str, int], role: str) -> int:
    """Sum pages already fetched for a fact role (pay/cash/bet)."""
    total = 0
    for view, n in (fetched_view_pages or {}).items():
        if _VIEW_IS_FACT(view) == role:
            total += int(n or 0)
    return total


def _query_node_oneshot_modes() -> frozenset[str]:
    """Heavy nodes that must be attempted at most once per node key (no re-burst)."""
    return frozenset({"sequence", "top_n"})


def _query_node_auto_offset_modes() -> frozenset[str]:
    """Node modes whose full pages should continue with same-SQL OFFSET."""
    return frozenset({"field", "raw", "agg", "flag", "sequence"})


def _query_node_already_pulled(
    node: dict | None,
    *,
    done_keys: set[str] | frozenset | None,
    failed_views: set[str] | frozenset | None,
) -> bool:
    """True when this sequence/top_n node was already succeeded or abandoned this run."""
    if not isinstance(node, dict):
        return False
    mode = str(node.get("mode") or "").strip().lower()
    if mode not in _query_node_oneshot_modes():
        return False
    key = str(node.get("key") or node.get("view") or "").strip()
    if not key:
        return False
    if done_keys and key in done_keys:
        return True
    if failed_views and f"node:{key}" in failed_views:
        return True
    return False


def _full_fetch_short_pages_ready(
    *,
    target_roles: list[str] | None,
    user_fetch_complete: bool,
    fact_truncated_roles: list[str] | None,
    missing_roles: list[str] | None,
    abandoned_roles: set[str] | frozenset | None = None,
) -> bool:
    """True when every non-abandoned user/pay/cash/bet target is short-page complete."""
    abandoned = {str(r).strip() for r in (abandoned_roles or []) if str(r).strip()}
    miss = {str(r).strip() for r in (missing_roles or []) if str(r).strip()}
    trunc = {str(r).strip() for r in (fact_truncated_roles or []) if str(r).strip()}
    for raw in target_roles or []:
        role = str(raw).strip()
        if role not in ("user", "pay", "cash", "bet"):
            continue
        if role in abandoned:
            continue
        if role in miss:
            return False
        if role == "user" and not user_fetch_complete:
            return False
        if role in ("pay", "cash", "bet") and role in trunc:
            return False
    return True


def _full_fetch_page_caps_exhausted(
    *,
    target_roles: list[str] | None,
    fetched_view_pages: dict[str, int] | None,
    fact_truncated_roles: list[str] | None,
    abandoned_roles: set[str] | frozenset | None,
    user_fetch_complete: bool,
    user_page_cap: int,
    fact_page_cap: int,
) -> bool:
    """True when every still-incomplete role is stuck at its page cap (or abandoned)."""
    abandoned = {str(r).strip() for r in (abandoned_roles or []) if str(r).strip()}
    trunc = {str(r).strip() for r in (fact_truncated_roles or []) if str(r).strip()}
    pages = fetched_view_pages or {}
    for raw in target_roles or []:
        role = str(raw).strip()
        if role not in ("user", "pay", "cash", "bet"):
            continue
        if role in abandoned:
            continue
        if role == "user":
            if user_fetch_complete:
                continue
            user_pages = sum(
                int(n or 0) for v, n in pages.items() if _is_user_info_view(v)
            )
            if user_pages < max(1, int(user_page_cap or 1)):
                return False
            continue
        role_views = [v for v in pages if _view_category(v) == role]
        if not role_views:
            return False
        if role not in trunc:
            continue
        role_pages = max(int(pages.get(v, 0) or 0) for v in role_views)
        if role_pages < max(1, int(fact_page_cap or 1)):
            return False
    return True


def _full_fetch_core_unavailable(
    *,
    target_roles: list[str] | None,
    abandoned_roles: set[str] | frozenset | None,
    pay_cohort: bool = False,
) -> bool:
    """Core identity/pay view abandoned → cannot honestly full-fetch."""
    abandoned = {str(r).strip() for r in (abandoned_roles or []) if str(r).strip()}
    targets = {str(r).strip() for r in (target_roles or []) if str(r).strip()}
    if "user" in targets and "user" in abandoned:
        return True
    if pay_cohort and "pay" in targets and "pay" in abandoned:
        return True
    return False


def _full_fetch_allow_force_write(
    *,
    full_fetch: bool,
    prefer_fallback: bool,
    short_pages_ready: bool,
    core_unavailable: bool,
    budget_exhausted: bool,
    page_caps_exhausted: bool,
) -> bool:
    """Gate force/FINAL write under full_fetch (strict short-page or honest cap exhaust)."""
    if not full_fetch:
        return True
    if core_unavailable:
        return False
    if short_pages_ready:
        return True
    if prefer_fallback and budget_exhausted and page_caps_exhausted:
        return True
    return False


def _query_graph_fail_message(
    view: str,
    *,
    hint: str = "",
    mcp_error: str = "",
    exc: str = "",
) -> str:
    """Query-graph remote failure copy with clipped MCP/exception text."""
    bits = [f"【查询图】远程失败 view={view}"]
    if hint:
        bits.append(hint)
    detail = _clip_mcp_error_text(mcp_error or exc)
    if detail:
        prefix = "异常" if exc and not mcp_error else "MCP"
        bits.append(f"{prefix}: {detail}")
    return "；".join(bits)


def _noncore_abandon_progress(
    *,
    mode: str = "",
    role: str = "",
    view: str = "",
    soft: bool = False,
) -> str:
    """Progress line: bet/top_n abandoned; export may continue with 未完整."""
    kind = (mode or role or "节点").strip() or "节点"
    v = (view or "").strip()
    if soft:
        return (
            f"查询图放弃重节点 {kind}/{v}（非核心，可继续写表并标未完整）"
            if v
            else f"查询图放弃重节点 {kind}（非核心，可继续写表并标未完整）"
        )
    return (
        f"查询图放弃 {kind}/{v}（非核心失败快停，可继续写表并标未完整）"
        if v
        else f"查询图放弃 {kind}（非核心失败快停，可继续写表并标未完整）"
    )


def _full_fetch_core_abort_message(role: str = "", view: str = "") -> str:
    """Describe a bound-resource failure without suggesting a replacement."""
    del role
    v = (view or "").strip() or "已绑定资源"
    return (
        f"已绑定资源 {v} 不可用，无法完成其契约列；"
        f"请先 MCP 探活（describe_ads_view + SELECT uid FROM ads.{v} LIMIT 1）"
    )


def _facts_ready_for_analyzed_delivery(
    *,
    export_target_roles: list[str],
    missing_roles: list[str],
    fact_truncated_roles: list[str] | None = None,
    budget_left: int | None = None,
    pay_cohort: bool = False,
    deliverable_rows: int | None = None,
    cohort_uid_estimate: int | None = None,
) -> bool:
    """Compatibility adapter: only explicit contract gaps may delay delivery."""
    del export_target_roles, fact_truncated_roles, budget_left, pay_cohort
    del deliverable_rows, cohort_uid_estimate
    return not any(str(item or "").strip() for item in (missing_roles or []))


def _blocking_roles_for_analyzed_delivery(
    *,
    export_target_roles: list[str],
    missing_roles: list[str],
) -> list[str]:
    """Compatibility adapter returning explicit contract gaps only."""
    del export_target_roles
    return list(dict.fromkeys(
        str(item).strip() for item in (missing_roles or []) if str(item).strip()
    ))


def _format_resource_binding_gap(resources: list[str] | None = None) -> str:
    """Explain an execution gap without inventing a business resource or SQL."""
    names = [str(item).strip() for item in (resources or []) if str(item).strip()]
    suffix = f"：{', '.join(names)}" if names else ""
    return (
        "【资源绑定缺口】当前列计划缺少可执行的已绑定查询节点"
        + suffix
        + "。请先 MCP list/describe 获取资源与备注，再由模型重新绑定列意图；"
        "引擎不会替换为默认视图或生成猜测 SQL。"
    )


def _apply_view_intent_route(
    route: ViewIntentRoute,
) -> tuple[str, list[str]]:
    """Return intent mode and explicitly pinned resources."""
    mode = (route.mode or "multi_fact").strip().lower()
    if mode == "single_view":
        return "single_view", list(route.pinned_views or [])
    if mode == "light_identity":
        return "light_identity", []
    return "multi_fact", []


def _export_resource_allowed(view: str, whitelist: list[str] | None) -> bool:
    """Empty whitelist = no filter; else view must be listed (case-insensitive)."""
    wl = [str(x).strip() for x in (whitelist or []) if str(x).strip()]
    if not wl:
        return True
    v = str(view or "").strip().lower()
    if not v:
        return False
    return v in {x.lower() for x in wl}


async def _export_bind_column_resources(
    *,
    db: Session,
    llm,
    mcp_ids: list[str],
    column_plan: list[dict],
    column_headers: list[str] | None = None,
    catalog_text: str = "",
    resource_schemas: dict[str, str] | None = None,
    context_text: str = "",
) -> tuple[list[str], BindResult | None]:
    """Column intent → soft bind → export_resource_whitelist (no hexad default)."""
    intents = metric_intents_from_export_columns(
        column_plan,
        column_headers,
        context_text=context_text,
    )
    bind_result: BindResult | None = None
    if llm and intents and mcp_ids:
        tools: list[dict] = []
        for mid in mcp_ids:
            mcp = db.query(MCP).filter(MCP.id == mid).first()
            if not mcp:
                continue
            try:
                tools.extend(await _get_mcp_tools_cached(mcp))
            except Exception:
                continue
        try:
            catalog_resources = parse_resource_catalog(catalog_text or "")
            bind_result = await asyncio.wait_for(
                bind_metrics_to_mcp(
                    llm,
                    intents,
                    tools=tools,
                    catalog_resources=catalog_resources,
                    resource_schemas=resource_schemas,
                    db=db,
                    timeout=12,
                    allow_seed=False,
                ),
                timeout=14.0,
            )
        except Exception as e:
            logger.warning("export column bind skipped: %s", e)
            bind_result = None
    if bind_result and bind_result.bindings:
        try:
            column_plan[:] = apply_binding_hints_to_column_plan(
                column_plan,
                bindings=[b.to_dict() for b in bind_result.bindings],
            )
        except Exception:
            logger.exception("apply binding hints to column plan failed")
    wl = resolve_export_resource_whitelist(
        column_plan,
        column_headers,
        bind_result=bind_result,
        allow_seed=False,
        gap_seed=False,
        include_column_plan_views=False,
    )
    return wl, bind_result


def _list_run_page_metas(sandbox_id: str, run_id: str) -> list[dict]:
    root = ensure_workplace(sandbox_id)
    safe_run = "".join(c if c.isalnum() or c in "-_" else "_" for c in str(run_id or ""))[:48]
    d = root / "task" / safe_run if safe_run else root / "task"
    if not d.is_dir():
        return []
    out: list[dict] = []
    for mpath in sorted(d.glob("page_*.meta.json")):
        try:
            meta = json.loads(mpath.read_text(encoding="utf-8", errors="replace"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(meta, dict):
            out.append(meta)
    return out


def _covered_export_roles(sandbox_id: str, run_id: str) -> set[str]:
    covered: set[str] = set()
    for meta in _list_run_page_metas(sandbox_id, run_id):
        view = str(meta.get("view") or "")
        role = _VIEW_IS_FACT(view)
        if not role:
            # fall back to sidecar role labels / keys
            r = str(meta.get("role") or "").lower()
            keys = {str(k).lower() for k in (meta.get("keys") or [])}
            if r == "user":
                role = "user"
            elif r == "bet":
                role = "bet"
            elif r == "dim_game":
                role = "game"
            elif r == "dim_channel":
                role = "channel"
            elif r == "order":
                role = "pay" if "pay" in view.lower() else ("cash" if "cash" in view.lower() else "pay")
            elif "game_id" in keys and "game_name" in keys and "uid" not in keys:
                role = "game"
            elif (
                ("channel_id" in keys or "channelid" in keys)
                and (keys & {"channel_name", "channelname", "name", "title"})
                and "uid" not in keys
            ):
                role = "channel"
        if role:
            covered.add(role)
    return covered


def _missing_export_roles(
    sandbox_id: str | None,
    run_id: str,
    target_roles: list[str],
    *,
    abandoned_roles: set[str] | frozenset | None = None,
) -> list[str]:
    if not sandbox_id:
        base = list(target_roles)
    else:
        covered = _covered_export_roles(sandbox_id, run_id)
        base = [r for r in target_roles if r not in covered]
    if not abandoned_roles:
        return base
    skip = {str(r).strip() for r in abandoned_roles if str(r).strip()}
    return [r for r in base if r not in skip]


def _summarize_run_page_state(
    sandbox_id: str | None,
    run_id: str,
) -> tuple[dict[str, int], dict[str, int], int]:
    """Summarize already-landed task/page_*.meta.json for engine resume/state."""
    if not sandbox_id or not run_id:
        return {}, {}, 0
    pages: dict[str, int] = {}
    last_rows: dict[str, int] = {}
    max_page = 0
    for meta in _list_run_page_metas(sandbox_id, run_id):
        view = str(meta.get("view") or "").strip()
        if not view:
            continue
        try:
            page_n = int(meta.get("page") or 0)
        except (TypeError, ValueError):
            page_n = 0
        try:
            rows_n = int(meta.get("rows") or 0)
        except (TypeError, ValueError):
            rows_n = 0
        pages[view] = int(pages.get(view, 0) or 0) + 1
        if page_n >= max_page:
            max_page = page_n
        last_rows[view] = rows_n
    return pages, last_rows, max_page


def _should_import_prior_node_state(
    *,
    prior_run_id: str,
    current_run_id: str,
    current_fetched_pages: dict[str, int] | None = None,
) -> bool:
    """Only reuse done/failed node state when it refers to this run's pages."""
    del current_fetched_pages
    if not prior_run_id or not current_run_id:
        return False
    return str(prior_run_id) == str(current_run_id)


def _strip_repair_plan_node_state(repair_plan: dict | None) -> dict:
    repair = dict(repair_plan or {}) if isinstance(repair_plan, dict) else {}
    if not repair:
        return {}
    repair["preserve_done_node_keys"] = []
    repair["skip_failed_node_keys"] = []
    return repair


def _record_export_view_failure(
    failed_views: set[str],
    view: str,
    *,
    role: str = "",
    abandoned_roles: set[str] | None = None,
    abandon_role: bool = False,
) -> None:
    """Blacklist a view for this run; optionally abandon the whole role."""
    v = (view or "").strip()
    if v:
        failed_views.add(v)
    r = (role or "").strip().lower()
    if abandon_role and r and abandoned_roles is not None:
        abandoned_roles.add(r)


def _export_role_pull_blocked(
    role: str,
    *,
    failed_views: set[str] | frozenset | None,
    abandoned_roles: set[str] | frozenset | None,
    dim_views: dict[str, str] | None = None,
) -> bool:
    """True when engine must not attempt another pull for this role."""
    r = (role or "").strip().lower()
    if not r:
        return True
    if abandoned_roles and r in abandoned_roles:
        return True
    return False


def _format_missing_roles_note(
    missing: list[str],
    *,
    budget_left: int,
    full_views: list[str] | None = None,
    dim_views: dict[str, str] | None = None,
) -> str:
    if not missing:
        return ""
    parts = [
        f"\n\n【缺表】仍缺: {', '.join(missing)}。剩余预算 {max(0, budget_left)}。"
        "请改查其他 view，勿重复已满页的视图。"
    ]
    if dim_views:
        for role in ("channel", "game"):
            if role in missing and dim_views.get(role):
                parts.append(
                    f" 建议 `query_ads_view {{\"view\":\"{dim_views[role]}\",\"limit\":2000}}`（{role}）。"
                )
    if full_views:
        parts.append(f"已满页: {', '.join(full_views)}。")
    return "".join(parts)


def _sanitize_where_dict(where: dict) -> dict:
    out: dict = {}
    for k, v in (where or {}).items():
        if v is None:
            continue
        if isinstance(v, str) and v.strip().lower() in ("", "undefined", "null", "none"):
            continue
        out[str(k)] = v
    return out


def _format_todo_progress_lines(todos: list[dict]) -> list[str]:
    if not todos:
        return []
    lines = ["TODO:"]
    for t in todos:
        mark = "x" if t.get("done") else " "
        lines.append(f"  [{mark}] {t.get('text') or t.get('id')}")
    done = sum(1 for t in todos if t.get("done"))
    lines.append(f"  进度 {done}/{len(todos)}")
    return lines


def _todo_token(text: str) -> str:
    t = re.sub(r"\s+", "", (text or "").lower())
    return t


def _headers_have_chinese(headers: list[str], *, min_count: int = 3) -> bool:
    """True if enough headers contain CJK — analyzed sheet, not raw English dump."""
    n = 0
    for h in headers or []:
        if re.search(r"[\u4e00-\u9fff]", str(h or "")):
            n += 1
            if n >= min_count:
                return True
    return False


def _header_matches_todo(headers: list[str], todo_text: str) -> bool:
    if not headers or not todo_text:
        return False
    want = _todo_token(todo_text)
    if not want:
        return False
    # Prefer exact / containment match on full header (keeps (SC) etc.)
    for h in headers:
        hs = _todo_token(str(h))
        if hs == want or (want in hs) or (hs in want and len(hs) >= 2):
            return True
    aliases = [want]
    if "用户" in todo_text and "id" in todo_text.lower():
        aliases.append("用户id")
    if "流水" in todo_text:
        aliases.append("流水倍数")
    # English aliases only when sheet already looks analyzed — never for 注册渠道
    if _headers_have_chinese(headers, min_count=3):
        if "用户" in todo_text and "id" in todo_text.lower():
            aliases.extend(["userid", "uid"])
        if "注册时间" in todo_text:
            aliases.append("register_time")
    joined = " ".join(str(h) for h in headers).lower().replace(" ", "")
    for a in aliases:
        a2 = a.lower().replace(" ", "")
        if a2 and a2 in joined:
            return True
    return False


def _find_header_index(headers: list[str], *names: str) -> int:
    norms = [_todo_token(h) for h in headers]
    for name in names:
        want = _todo_token(name)
        for i, n in enumerate(norms):
            if n == want or want in n or n in want:
                return i
    return -1


def _channel_column_mostly_numeric(path: Path, headers: list[str]) -> bool:
    """True if 注册渠道 column looks like raw ids (≥80% digit-only)."""
    idx = _find_header_index(headers, "注册渠道", "渠道")
    if idx < 0 or not path or not path.is_file():
        return False
    try:
        import openpyxl
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        try:
            ws = wb.active
            vals = []
            for row in ws.iter_rows(min_row=2, max_row=81, values_only=True):
                if not row or idx >= len(row):
                    continue
                v = row[idx]
                if v is None or str(v).strip() == "":
                    continue
                vals.append(str(v).strip())
                if len(vals) >= 40:
                    break
        finally:
            wb.close()
    except Exception:
        return False
    if len(vals) < 5:
        return False
    digitish = sum(1 for v in vals if re.fullmatch(r"\d+(\.0+)?", v))
    return (digitish / len(vals)) >= 0.8


def _sample_column_all_empty(path: Path, headers: list[str], *names: str) -> bool:
    idx = _find_header_index(headers, *names)
    if idx < 0 or not path or not path.is_file():
        return False
    try:
        import openpyxl
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        try:
            ws = wb.active
            seen = 0
            nonempty = 0
            for row in ws.iter_rows(min_row=2, max_row=120, values_only=True):
                if not row or idx >= len(row):
                    continue
                seen += 1
                v = row[idx]
                if v is not None and str(v).strip() not in ("", "None", "nan"):
                    nonempty += 1
                if seen >= 80:
                    break
        finally:
            wb.close()
    except Exception:
        return False
    return seen >= 5 and nonempty == 0


def _column_empty_ratio(path: Path, headers: list[str], *names: str) -> float | None:
    """Fraction of empty cells in first matching column (full scan, capped)."""
    idx = _find_header_index(headers, *names)
    if idx < 0 or not path or not path.is_file():
        return None
    try:
        import openpyxl
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        try:
            ws = wb.active
            seen = 0
            empty = 0
            for row in ws.iter_rows(min_row=2, max_row=5001, values_only=True):
                if not row or idx >= len(row):
                    continue
                seen += 1
                v = row[idx]
                if v is None or str(v).strip() in ("", "None", "nan"):
                    empty += 1
        finally:
            wb.close()
    except Exception:
        return None
    if seen < 1:
        return None
    return empty / seen


def _count_metric_users(path: Path, headers: list[str], *names: str) -> int | None:
    idx = _find_header_index(headers, *names)
    if idx < 0 or not path or not path.is_file():
        return None
    try:
        import openpyxl
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        try:
            ws = wb.active
            n = 0
            for row in ws.iter_rows(min_row=2, values_only=True):
                if not row or idx >= len(row):
                    continue
                v = row[idx]
                if v is None or str(v).strip() in ("", "0", "0.0", "None", "nan"):
                    continue
                # 是否有退款: 有
                if isinstance(v, str) and v.strip() in ("无", "否", "N", "n"):
                    continue
                n += 1
        finally:
            wb.close()
        return n
    except Exception:
        return None


def _sum_metric_column(path: Path, headers: list[str], *names: str) -> float | None:
    """Deterministic sum of a numeric deliverable column (e.g. 总充值金额)."""
    idx = _find_header_index(headers, *names)
    if idx < 0 or not path or not path.is_file():
        return None
    try:
        import openpyxl
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        try:
            ws = wb.active
            total = 0.0
            any_num = False
            for row in ws.iter_rows(min_row=2, values_only=True):
                if not row or idx >= len(row):
                    continue
                v = row[idx]
                if v is None or str(v).strip() in ("", "None", "nan", "—", "-"):
                    continue
                try:
                    if isinstance(v, str):
                        v = v.replace(",", "").replace("$", "").strip()
                    total += float(v)
                    any_num = True
                except (TypeError, ValueError):
                    continue
        finally:
            wb.close()
        return total if any_num else 0.0
    except Exception:
        return None


_EXPORT_EMPTY_CHECK_COLS = (
    ("流水倍数", "流水倍数"),
    ("连续充值次数", "连续充值次数"),
    ("SC投注金额最多的游戏", "SC投注金额最多的游戏"),
    ("是否有退款", "是否有退款"),
)


def _field_docs_from_plan_or_todos(
    column_plan: list[dict] | None,
    todos: list[dict] | None,
    headers: list[str] | None = None,
) -> list[tuple[str, str, str]]:
    """Return [(列名, 数据来源, 统计方法), ...] for FINAL 字段说明."""
    plan = [c for c in (column_plan or []) if isinstance(c, dict) and c.get("header")]
    if not plan and headers:
        plan = [plan_one_column(h) for h in headers if str(h or "").strip()]
    if not plan:
        for t in todos or []:
            if t.get("phase") == "analyze" and t.get("text"):
                plan.append(plan_one_column(str(t.get("text") or "")))
    out: list[tuple[str, str, str]] = []
    for col in plan:
        name = str(col.get("header") or "").strip()
        if not name:
            continue
        src, method = column_source_method(col)
        out.append((name, src, method))
    return out


def _build_export_analysis_appendix(
    *,
    path: Path | None,
    headers: list[str],
    todos: list[dict],
    time_window: dict | None,
    row_hint: int | None,
    detail_truncated: bool = False,
    truncated_roles: list[str] | None = None,
    missing_roles: list[str] | None = None,
    user_fetch_complete: bool | None = None,
    dim_budget_exhausted: bool = False,
    cohort_uid_estimate: int | None = None,
    column_plan: list[dict] | None = None,
    file_size: int | None = None,
) -> str:
    """Screenshot FINAL blocks: 导出概况 + 字段说明(来源/方法) + 备注."""
    tw_label = (time_window or {}).get("label") or "见用户需求"
    rows_n = row_hint if row_hint is not None else 0
    pay_sum = (
        _sum_metric_column(path, headers, "总充值金额", "总充值", "充值金额")
        if path
        else None
    )
    bet_n = (
        _count_metric_users(path, headers, "总下注金额", "总下注", "下注金额", "下注次数")
        if path
        else None
    )
    pay_card_n = (
        _count_metric_users(path, headers, "充值银行卡", "充值银行卡数量")
        if path
        else None
    )
    cash_card_n = (
        _count_metric_users(path, headers, "提现银行卡", "提现银行卡数量")
        if path
        else None
    )
    ban_n = _count_metric_users(path, headers, "是否被封禁", "封禁") if path else None
    ref_n = _count_metric_users(path, headers, "是否有退款", "退款") if path else None
    try:
        est_n = int(cohort_uid_estimate) if cohort_uid_estimate is not None else None
    except (TypeError, ValueError):
        est_n = None
    row_gap = _cohort_rows_incomplete(
        deliverable_rows=rows_n if row_hint is not None else None,
        cohort_uid_estimate=est_n,
    )

    def _cell(v) -> str:
        if v is None:
            return "—"
        if isinstance(v, float):
            if abs(v - round(v)) < 1e-9:
                return f"{int(round(v)):,}"
            return f"{v:,.2f}"
        if isinstance(v, int):
            return f"{v:,}"
        return str(v)

    def _money(v) -> str:
        if v is None:
            return "—"
        try:
            return f"${float(v):,.2f}"
        except (TypeError, ValueError):
            return str(v)

    empty_risks: list[str] = []
    if path and headers:
        for label, *aliases in _EXPORT_EMPTY_CHECK_COLS:
            ratio = _column_empty_ratio(path, headers, label, *aliases)
            if ratio is not None and ratio >= 0.8:
                empty_risks.append(f"{label}空值约 {ratio:.0%}")

    overview = [
        "| 指标 | 数值 |",
        "| --- | --- |",
        f"| 时间范围 | {tw_label} |",
        f"| 总用户数 | {_cell(rows_n)} |",
        f"| 总充值金额 | {_money(pay_sum)} |",
        f"| 有 SC 下注 | {_cell(bet_n)} |",
        f"| 有充值卡 | {_cell(pay_card_n)} |",
        f"| 有提现卡 | {_cell(cash_card_n)} |",
        f"| 被封禁 | {_cell(ban_n)} |",
        f"| 有退款 | {_cell(ref_n)} |",
    ]
    if file_size is not None:
        overview.append(f"| 文件大小 | {_human_file_size(file_size)} |")
    # Bullet dual-insurance (survives accidental table strip)
    overview_bullets = [
        f"- 时间范围：{tw_label}",
        f"- 总用户数：{_cell(rows_n)}",
        f"- 总充值金额：{_money(pay_sum)}",
        f"- 有 SC 下注：{_cell(bet_n)}",
        f"- 有充值卡：{_cell(pay_card_n)}",
        f"- 有提现卡：{_cell(cash_card_n)}",
        f"- 被封禁：{_cell(ban_n)}",
        f"- 有退款：{_cell(ref_n)}",
    ]
    if file_size is not None:
        overview_bullets.append(f"- 文件大小：{_human_file_size(file_size)}")

    field_rows = _field_docs_from_plan_or_todos(column_plan, todos, headers)
    n_cols = len(field_rows)
    col_lines = [
        "| # | 列名 | 数据来源 | 统计方法 |",
        "| --- | --- | --- | --- |",
    ]
    for i, (name, src, method) in enumerate(field_rows, 1):
        col_lines.append(
            f"| {i} | {name.replace('|', '/')} | "
            f"{src.replace('|', '/')} | {method.replace('|', '/')} |"
        )

    notes = [
        "备注:",
        "- 金额字段已从「分」转换为「美元」显示（÷100），保留 2 位小数。",
        "- 用户行按总充值金额降序排列（若 SHELL 已按此处理）。",
        f"- 时间已按用户描述时区转换（{(time_window or {}).get('tz_label') or '见需求'}）。",
    ]
    if user_fetch_complete is False:
        notes.append("- 用户主表分页未见短页，时间窗内用户可能未拉全，行数可能偏低。")
    if path and _sample_column_all_empty(path, headers, "连续充值次数"):
        notes.append(
            "- 连续充值次数需要游戏行为时序分析；当前若全空则标未完整，可继续补充。"
        )
    for r in empty_risks:
        notes.append(f"- 空列抽检：{r}，对应指标可能低估或未映射。")
    if detail_truncated or truncated_roles:
        role_txt = "、".join(truncated_roles) if truncated_roles else "充值/提现/下注"
        notes.append(
            f"- 关联明细（{role_txt}）可能因 query 预算/同 view 页上限截断，金额与次数存在低估风险。"
        )
    if row_gap and est_n is not None:
        notes.append(
            f"- 行数缺口：交付 {rows_n:,}，目标 cohort uid≈{est_n:,}；"
            "本表**未标完整**，可同窗 OFFSET 续翻后再导出。"
        )
    if missing_roles:
        notes.append(
            "- 拉取阶段仍缺 role："
            + "、".join(missing_roles)
            + "，渠道名/游戏名等映射可能不完整。"
        )
        if dim_budget_exhausted:
            notes.append(
                "- 预算耗尽未拉维表：query 预算在事实表分页中用尽，channel/game 配置维表未能落盘。"
            )

    return (
        "### 导出概况\n"
        + "\n".join(overview)
        + "\n\n"
        + "\n".join(overview_bullets)
        + f"\n\n### 字段说明（共 {n_cols} 列）\n"
        + "\n".join(col_lines)
        + "\n\n"
        + "\n".join(notes)
    )


def _build_export_fallback_appendix(
    *,
    missing: list[str] | None = None,
    missing_roles: list[str] | None = None,
    row_hint: int | None = None,
    time_window: dict | None = None,
    analyze_incomplete: bool = False,
    shell_enabled: bool = True,
) -> str:
    """Short gap notes for 原始回退 FINAL."""
    tw_label = (time_window or {}).get("label") or "见用户需求"
    lines = [
        "### 完整性/缺口说明",
        f"- 时间范围：{tw_label}",
        f"- 回退行数：{row_hint:,}" if row_hint is not None else "- 回退行数：未知",
        "- 交付类型：原始回退（英文/原始宽表合并，**不是**用户要求的中文分析表）。",
        (
            "- 说明：请继续 SHELL 按任务锚点写分析表，或直接说明要补哪些列。"
            if shell_enabled
            else "- 说明：平台未能完成契约化分析；请按缺口补数后由引擎重新写表。"
        ),
    ]
    if analyze_incomplete and not missing_roles:
        lines.append(
            (
                "- 回退原因：拉取角色已齐套，但 LLM 未用 SHELL 写出当前目录中文表头 xlsx。"
                if shell_enabled
                else "- 回退原因：拉取角色已齐套，但平台分析或交付校验未通过。"
            )
        )
    if missing:
        lines.append("- 未命中分析列：" + "、".join(missing[:14]) + ("…" if len(missing) > 14 else ""))
    if missing_roles:
        facts = [r for r in missing_roles if r in ("pay", "cash", "bet")]
        lines.append("- 拉取仍缺 role：" + "、".join(missing_roles))
        if facts:
            lines.append(
                "- 缺口类型：fact_starved（目标明细从未落盘或未齐套，常见于预算被其它 view 深翻占满）。"
            )
            next_step = (
                "然后 SHELL 写当前目录中文表。"
                if shell_enabled
                else "齐套后由引擎自动关联、写表并校验。"
            )
            lines.append(
                "- 建议：下一轮按列计划/白名单 resource 对各缺明细各 MCP 1 页"
                "（list/describe 后绑定；勿硬拉 user_bet_log），"
                "勿再深翻已有明细；" + next_step
            )
        else:
            lines.append(
                (
                    "- 建议：确认 MCP sql（含 FROM ads.<view>）与时间窗后重试，并完成 SHELL 写当前目录中文表。"
                    if shell_enabled
                    else "- 建议：确认 MCP sql（含 FROM ads.<view>）与时间窗后重试，齐套后由引擎自动写表。"
                )
            )
    else:
        lines.append(
            (
                "- 建议：基于 task/<run_id>/page_*.json 用 pandas/openpyxl 写出当前目录中文表头 xlsx 后重试（勿只写 prep 脚本）。"
                if shell_enabled
                else "- 建议：保留 task/<run_id>/page_*.json，修正列计划或校验缺口后由引擎重新生成 xlsx。"
            )
        )
    return "\n".join(lines)


def _extract_mcp_view_name(normalized: str) -> str:
    """Best-effort view name from MCP tool invocation text."""
    text = normalized or ""
    for pat in (
        r'"view"\s*:\s*"([^"]+)"',
        r"'view'\s*:\s*'([^']+)'",
        r'"view_name"\s*:\s*"([^"]+)"',
        r"view[=:]\s*([a-zA-Z0-9_]+)",
    ):
        m = re.search(pat, text)
        if m:
            return (m.group(1) or "").strip()[:120]
    return ""


def _shell_output_tail(tool_result: str, limit: int = 400) -> str:
    text = (tool_result or "").strip()
    if not text:
        return ""
    if len(text) <= limit:
        return text
    return text[-limit:]


def _todos_satisfied(headers: list[str], todos: list[dict]) -> tuple[bool, list[str]]:
    """Analyze success: ≥60% of analyze-phase column todos matched, and ≥3 matches."""
    col_todos = [t for t in todos if t.get("phase") == "analyze"]
    if not col_todos:
        return (bool(headers), [])
    missing: list[str] = []
    hit = 0
    for t in col_todos:
        text = str(t.get("text") or "")
        if _header_matches_todo(headers, text):
            hit += 1
        else:
            missing.append(text)
    need = max(3, int(len(col_todos) * 0.6 + 0.999))
    return (hit >= need, missing)


def _mark_todos_phase(todos: list[dict], phase: str, done: bool = True) -> None:
    for t in todos:
        if t.get("phase") == phase:
            t["done"] = done


def _mark_column_todos_from_headers(todos: list[dict], headers: list[str]) -> None:
    for t in todos:
        if t.get("phase") != "analyze":
            continue
        if _header_matches_todo(headers, str(t.get("text") or "")):
            t["done"] = True


def _serialize_time_window_for_state(time_window: dict | None) -> dict | None:
    return export_serialize_time_window_for_state(time_window)


def _hydrate_time_window_from_state(state: dict | None) -> dict | None:
    return export_hydrate_time_window_from_state(state, page_limit=_EXPORT_PAGE_LIMIT)


def _tw_ms_tuple(tw: dict | None) -> tuple[int | None, int | None]:
    if not isinstance(tw, dict):
        return None, None
    try:
        start = int(tw["start_ms"]) if tw.get("start_ms") is not None else None
    except (TypeError, ValueError):
        start = None
    try:
        end = int(tw["end_ms"]) if tw.get("end_ms") is not None else None
    except (TypeError, ValueError):
        end = None
    return start, end


def _time_windows_equal(a: dict | None, b: dict | None) -> bool:
    sa, ea = _tw_ms_tuple(a)
    sb, eb = _tw_ms_tuple(b)
    if sa is None or ea is None or sb is None or eb is None:
        return False
    return sa == sb and ea == eb


def _resolve_export_time_window(
    user_message: str = "",
    source_brief: str = "",
    prior_state: dict | None = None,
) -> tuple[dict | None, bool]:
    """Resolve time window: current message > source_brief > prior hydrate.

    Returns (window, from_prior). If user/brief parses a window whose ms differs
    from prior, prior SQL templates are not used (fresh parse only).
    """
    from_user = _parse_export_time_window(user_message or "")
    from_brief = _parse_export_time_window(source_brief or "") if source_brief else None
    prior_tw = _hydrate_time_window_from_state(prior_state)

    chosen = from_user or from_brief
    if chosen:
        if prior_tw and not _time_windows_equal(chosen, prior_tw):
            return chosen, False
        return chosen, False
    if prior_tw:
        return prior_tw, True
    return None, False


def _export_turn_state_from_intent(
    turn_intent: TurnIntent,
    *,
    resolved_tw: dict | None = None,
    prior_state: dict | None = None,
) -> str:
    """Project LLM task relation into run state using only contract facts."""
    if turn_intent.verification_required:
        return "repair"
    relation = str(turn_intent.task_relation or "unknown").strip().lower()
    if relation == "new":
        return "new_export"
    prior_tw = _hydrate_time_window_from_state(prior_state)
    if (
        relation == "revise"
        and resolved_tw
        and prior_tw
        and not _time_windows_equal(resolved_tw, prior_tw)
    ):
        return "window_change"
    if relation in ("continue", "revise"):
        return "repair"
    return "unknown"


def _build_turn_digest(
    user_message: str,
    *,
    intent: str = "",
    time_window: dict | None = None,
) -> str:
    bits: list[str] = []
    intent = (intent or "").strip() or "unknown"
    label = ""
    if isinstance(time_window, dict):
        label = str(time_window.get("label") or "").strip()
    if intent == "window_change":
        bits.append("本轮变更时间窗" + (f"：{label}" if label else "。"))
    elif intent == "repair":
        bits.append("本轮按列意图/缺口补齐导出" + (f"（沿用窗 {label}）" if label else "。"))
    elif intent == "feedback":
        bits.append("本轮为交付完整性反馈，不重新拉数。")
    elif intent == "new_export":
        bits.append("本轮新导出任务" + (f"；时间窗 {label}" if label else "。"))
    else:
        bits.append("本轮导出相关请求。")
    um = re.sub(r"\s+", " ", (user_message or "").strip())
    if um and len(um) <= 80:
        bits.append(f"用户：{um}")
    elif um:
        bits.append(f"用户：{um[:77]}…")
    return "".join(bits)[:300]


def _build_export_constraints(
    *,
    time_window: dict | None = None,
    target_roles: list[str] | None = None,
    source_brief: str = "",
) -> dict:
    tw = time_window if isinstance(time_window, dict) else {}
    start_ms, end_ms = _tw_ms_tuple(tw)
    cohort = str(tw.get("cohort") or "").strip()
    if not cohort and _is_pay_cohort_brief(source_brief or ""):
        cohort = "pay"
    return {
        "time_window_label": str(tw.get("label") or "").strip(),
        "start_ms": start_ms,
        "end_ms": end_ms,
        "cohort": cohort,
        "roles": [str(r).strip() for r in (target_roles or []) if str(r).strip()][:12],
    }


def _derive_export_completeness(
    *,
    missing_roles: list[str] | None = None,
    fact_truncated_roles: list[str] | None = None,
    user_truncated: bool = False,
    user_fetch_complete: bool | None = None,
    deliverable: str = "",
    fallback: bool = False,
    deliverable_rows: int | None = None,
    cohort_uid_estimate: int | None = None,
    fetched_view_pages: dict[str, int] | None = None,
    has_task_data: bool | None = None,
    force: str = "",
) -> str:
    return export_derive_completeness(
        missing_roles=missing_roles,
        fact_truncated_roles=fact_truncated_roles,
        user_truncated=user_truncated,
        user_fetch_complete=user_fetch_complete,
        deliverable=deliverable,
        fallback=fallback,
        deliverable_rows=deliverable_rows,
        cohort_uid_estimate=cohort_uid_estimate,
        fetched_view_pages=fetched_view_pages,
        has_task_data=has_task_data,
        force=force,
    )


def _short_page_roles_from_state(
    *,
    target_roles: list[str] | None,
    fetched_view_pages: dict[str, int] | None,
    fetched_view_last_rows: dict[str, int] | None,
) -> list[str]:
    pages = fetched_view_pages or {}
    last = fetched_view_last_rows or {}
    targets = {str(r).strip() for r in (target_roles or []) if str(r).strip()}
    out: list[str] = []
    for role in ("user", "pay", "cash", "bet", "channel", "game"):
        if targets and role not in targets:
            continue
        role_views = [v for v in pages if _view_category(v) == role]
        if not role_views:
            continue
        if all(_is_short_page_complete(last.get(v, 0), view=v) for v in role_views):
            if any(int(pages.get(v, 0) or 0) > 0 for v in role_views):
                out.append(role)
    return out


def _build_run_state_next_actions(
    *,
    completeness: str,
    need_continue_roles: list[str] | None = None,
    missing_roles: list[str] | None = None,
    fetched_view_pages: dict[str, int] | None = None,
    time_window: dict | None = None,
    has_task_data: bool = False,
) -> list[dict]:
    """Machine-readable next steps for coach / next-run injection."""
    actions: list[dict] = []
    pages = fetched_view_pages or {}
    need = [str(r).strip() for r in (need_continue_roles or []) if str(r).strip()]
    miss = [str(r).strip() for r in (missing_roles or []) if str(r).strip()]

    if completeness == "no_data":
        actions.append({
            "role": "check",
            "view": "",
            "offset": 0,
            "mcp_example": "",
            "hint": "核对视图权限与时间窗毫秒区间；必要时 list/describe 后再 query",
        })
        return actions[:4]

    if completeness == "iters_exhausted" and has_task_data:
        actions.append({
            "role": "shell",
            "view": "",
            "offset": 0,
            "mcp_example": "",
            "hint": "过程数据已在 task/page_*.json：SHELL 写当前目录中文表头 xlsx，或说明要补哪些列",
        })

    for role in need:
        view = ""
        for v, n in pages.items():
            if _view_category(v) == role and int(n or 0) > 0:
                view = v
                break
        pages_done = sum(
            int(n or 0) for v, n in pages.items() if _view_category(v) == role
        )
        mcp_ex = ""
        if view:
            mcp_ex = (
                f'MCP: query_ads_view {{"view":"{view}",'
                f'"limit":{_EXPORT_PAGE_LIMIT}}}'
            )
        actions.append({
            "role": role,
            "view": view,
            "offset": _offset_for_pages_done(pages_done, view=view),
            "mcp_example": mcp_ex,
            "hint": (
                f"{role} 末页仍满页，同窗 OFFSET 续翻至短页"
                if view
                else f"{role} 续翻：按已绑定/已拉 view，勿默认归档源表"
            ),
        })

    for role in miss:
        if role in need:
            continue
        view = ""
        for v, n in pages.items():
            if _view_category(v) == role and int(n or 0) > 0:
                view = v
                break
        mcp_ex = ""
        if view:
            mcp_ex = (
                f'MCP: query_ads_view {{"view":"{view}",'
                f'"limit":{_EXPORT_PAGE_LIMIT}}}'
            )
        actions.append({
            "role": role,
            "view": view,
            "offset": 0,
            "mcp_example": mcp_ex,
            "hint": (
                f"仍缺 role={role}，先拉至少 1 页"
                if view
                else f"仍缺 role={role}：list/describe 后按列计划/白名单绑定再查"
            ),
        })

    return actions[:6]


def _build_run_state_digest(
    *,
    completeness: str,
    missing_roles: list[str] | None = None,
    fact_truncated_roles: list[str] | None = None,
    need_continue_roles: list[str] | None = None,
    deliverable_rows: int | None = None,
    cohort_uid_estimate: int | None = None,
    has_task_data: bool = False,
    deliverable: str = "",
    mcp_query_count: int | None = None,
    mcp_budget: int | None = None,
    schema_discovery: dict | None = None,
) -> str:
    return export_run_state_digest(
        completeness=completeness,
        missing_roles=missing_roles,
        fact_truncated_roles=fact_truncated_roles,
        need_continue_roles=need_continue_roles,
        deliverable_rows=deliverable_rows,
        cohort_uid_estimate=cohort_uid_estimate,
        has_task_data=has_task_data,
        deliverable=deliverable,
        mcp_query_count=mcp_query_count,
        mcp_budget=mcp_budget,
        schema_discovery=schema_discovery,
    )


def _format_smart_empty_export_final(
    *,
    completeness: str,
    digest: str = "",
    next_actions: list[dict] | None = None,
) -> str:
    """Replace blunt 'MCP 未返回' with actionable digest + next_actions."""
    lines: list[str] = []
    if completeness == "iters_exhausted":
        lines.append(
            "未能在当前目录产出最终 Excel/CSV：过程数据可能已在左侧 `task/`，"
            "本轮未完成 SHELL 写表或轮次已用尽。"
        )
    elif completeness == "no_data":
        lines.append(
            "未能产出最终 Excel/CSV：本轮未落盘可合并的 MCP 数据页"
            "（筛选可能无结果、时间窗或视图权限需核对）。"
        )
    else:
        lines.append("未能产出最终 Excel/CSV：请查看下列缺口与下一步。")
    if digest:
        lines.append("")
        lines.append("### 本轮结论")
        lines.append(digest)
    actions = [a for a in (next_actions or []) if isinstance(a, dict)][:3]
    if actions:
        lines.append("")
        lines.append("### 建议下一步")
        for i, a in enumerate(actions, 1):
            hint = str(a.get("hint") or "").strip()
            mcp = str(a.get("mcp_example") or "").strip()
            if hint:
                lines.append(f"{i}. {hint}")
            if mcp:
                lines.append(f"   `{mcp}`")
    lines.append("")
    lines.append("过程文件请查看左侧 `task/`；下一轮可直接说明要补哪些列。")
    return "\n".join(lines)


def _prior_state_learning_hint(prior_state: dict | None) -> str:
    """Inject prior digest + next_actions into system prompt."""
    if not isinstance(prior_state, dict):
        return ""
    digest = str(prior_state.get("digest") or "").strip()
    actions = prior_state.get("next_actions") or []
    bits: list[str] = []
    if digest:
        bits.append(f"上轮摘要：{digest}")
    n = 0
    for a in actions:
        if not isinstance(a, dict):
            continue
        mcp = str(a.get("mcp_example") or "").strip()
        hint = str(a.get("hint") or "").strip()
        if mcp:
            bits.append(f"上轮建议续查：{mcp}")
            n += 1
        elif hint:
            bits.append(f"上轮建议：{hint}")
            n += 1
        if n >= 2:
            break
    if not bits:
        return ""
    return "【上轮 run_state 记忆】\n" + "\n".join(bits)


def _write_export_run_state(
    sandbox: Sandbox | None,
    run_id: str,
    *,
    phase: str,
    mcp_query_count: int,
    budget: int,
    todos: list[dict],
    deliverable: str = "",
    fallback: bool = False,
    covered_roles: list[str] | None = None,
    missing_roles: list[str] | None = None,
    fetched_view_pages: dict[str, int] | None = None,
    fetched_view_last_rows: dict[str, int] | None = None,
    mcp_failures: list[dict] | None = None,
    source_brief: str = "",
    task_title: str = "",
    time_window: dict | None = None,
    target_roles: list[str] | None = None,
    analyze_columns: list[str] | None = None,
    user_fetch_complete: bool | None = None,
    user_pages: int | None = None,
    user_truncated: bool | None = None,
    fact_truncated_roles: list[str] | None = None,
    deliverable_rows: int | None = None,
    cohort_uid_estimate: int | None = None,
    fact_page_cap: int | None = None,
    cte_attempts_used: int | None = None,
    cte_attempt_limit: int | None = None,
    cte_attempt_errors: list[dict] | None = None,
    need_continue_roles: list[str] | None = None,
    completeness: str = "",
    short_page_roles: list[str] | None = None,
    next_actions: list[dict] | None = None,
    digest: str = "",
    has_task_data: bool | None = None,
    session_id: str = "",
    user_intent: str = "",
    turn_digest: str = "",
    constraints: dict | None = None,
    export_contract: dict | None = None,
    column_plan: list[dict] | None = None,
    repair_plan: dict | None = None,
    schema_discovery: dict | None = None,
    done_node_keys: list[str] | None = None,
    failed_views: list[str] | None = None,
) -> None:
    if not sandbox:
        return
    cols = [
        str(c).strip()
        for c in (analyze_columns or [])
        if str(c).strip()
    ]
    if not cols:
        cols = [
            str(t.get("text") or "").strip()
            for t in (todos or [])
            if t.get("phase") == "analyze" and str(t.get("text") or "").strip()
        ]
    pages = dict(fetched_view_pages or {})
    last_rows = dict(fetched_view_last_rows or {})
    fact_trunc = list(fact_truncated_roles or [])
    need_cont = list(need_continue_roles or [])
    if not need_cont:
        need_cont = list(fact_trunc)
    miss = list(missing_roles or [])
    comp = completeness or _derive_export_completeness(
        missing_roles=miss,
        fact_truncated_roles=fact_trunc,
        user_truncated=bool(user_truncated),
        user_fetch_complete=user_fetch_complete,
        deliverable=deliverable,
        fallback=fallback,
        deliverable_rows=deliverable_rows,
        cohort_uid_estimate=cohort_uid_estimate,
        fetched_view_pages=pages,
        has_task_data=has_task_data,
    )
    schema_summary = export_summarize_schema_discovery(
        schema_discovery=schema_discovery,
        export_contract=export_contract,
    )
    short_roles = short_page_roles
    if short_roles is None:
        short_roles = _short_page_roles_from_state(
            target_roles=target_roles,
            fetched_view_pages=pages,
            fetched_view_last_rows=last_rows,
        )
    actions = next_actions
    if actions is None:
        actions = _build_run_state_next_actions(
            completeness=comp,
            need_continue_roles=need_cont,
            missing_roles=miss,
            fetched_view_pages=pages,
            time_window=time_window,
            has_task_data=bool(
                has_task_data
                if has_task_data is not None
                else sum(int(n or 0) for n in pages.values()) > 0
            ),
        )
    dig = digest or _build_run_state_digest(
        completeness=comp,
        missing_roles=miss,
        fact_truncated_roles=fact_trunc,
        need_continue_roles=need_cont,
        deliverable_rows=deliverable_rows,
        cohort_uid_estimate=cohort_uid_estimate,
        has_task_data=bool(
            has_task_data
            if has_task_data is not None
            else sum(int(n or 0) for n in pages.values()) > 0
        ),
        deliverable=deliverable,
        mcp_query_count=mcp_query_count,
        mcp_budget=budget,
        schema_discovery=schema_summary,
    )
    payload = build_export_run_state_payload(
        phase=phase,
        mcp_query_count=mcp_query_count,
        budget=budget,
        todos=todos,
        deliverable=deliverable,
        fallback=fallback,
        covered_roles=covered_roles,
        missing_roles=miss,
        fetched_view_pages=pages,
        fetched_view_last_rows=last_rows,
        mcp_failures=mcp_failures,
        source_brief=source_brief,
        task_title=task_title,
        time_window=time_window,
        target_roles=target_roles,
        analyze_columns=cols,
        column_plan=column_plan,
        completeness=comp,
        short_page_roles=short_roles,
        need_continue_roles=need_cont,
        next_actions=actions,
        digest=dig,
        updated_at=now_str(),
        session_id=session_id,
        user_intent=user_intent,
        turn_digest=turn_digest,
        constraints=constraints,
        export_contract=export_contract,
        repair_plan=repair_plan,
        schema_discovery=schema_summary,
        done_node_keys=done_node_keys,
        failed_views=failed_views,
        fact_page_cap=fact_page_cap,
        user_fetch_complete=user_fetch_complete,
        user_pages=user_pages,
        user_truncated=user_truncated,
        fact_truncated_roles=fact_truncated_roles,
        deliverable_rows=deliverable_rows,
        cohort_uid_estimate=cohort_uid_estimate,
        cte_attempts_used=cte_attempts_used,
        cte_attempt_limit=cte_attempt_limit,
        cte_attempt_errors=cte_attempt_errors,
    )
    rel = f"task/{run_id}/_run_state.json"
    try:
        _write_workplace(sandbox, rel, json.dumps(payload, ensure_ascii=False, indent=2))
    except Exception:
        logger.exception("write export run state failed run=%s", run_id)


def _export_time_window_label(time_window: dict | None) -> str:
    if not isinstance(time_window, dict):
        return ""
    label = str(time_window.get("label") or "").strip()
    if label:
        return label
    start = str(time_window.get("start") or time_window.get("from") or "").strip()
    end = str(time_window.get("end") or time_window.get("to") or "").strip()
    if start and end:
        return f"{start} ~ {end}"
    return start or end or ""


def _append_export_skill_lesson_section(
    sandbox: Sandbox | None,
    run_id: str,
    *,
    user_message: str,
    mode: str,
    deliverable: str,
    covered_roles: list[str],
    missing_roles: list[str],
    fetched_view_pages: dict[str, int],
    export_todos: list[dict],
    time_window: dict | None,
    analysis_md: str,
    mcp_failures: list[dict] | None = None,
    fact_truncated_roles: list[str] | None = None,
    deliverable_rows: int | None = None,
    cohort_uid_estimate: int | None = None,
    skill_ids: list[str] | None = None,
    shell_enabled: bool = True,
    session_id: str = "",
) -> str:
    """Write lesson drafts and append FINAL「建议写入 Skill」section."""
    if not sandbox or not run_id:
        return analysis_md
    try:
        col_todos = [
            str(t.get("text") or "").strip()
            for t in (export_todos or [])
            if t.get("phase") == "analyze" and str(t.get("text") or "").strip()
        ]
        # Prefer explicit args; else hydrate from persisted run_state
        st = None
        try:
            from app.services.skill_lesson import load_run_state

            st = load_run_state(sandbox.id, run_id)
        except Exception:
            st = None
        fact_trunc = list(fact_truncated_roles or [])
        rows_n = deliverable_rows
        est_n = cohort_uid_estimate
        completeness_now = ""
        digest_now = ""
        repair_plan_now: dict | None = None
        if isinstance(st, dict):
            if not fact_trunc:
                fact_trunc = [
                    str(r).strip()
                    for r in (st.get("fact_truncated_roles") or [])
                    if str(r).strip()
                ]
            if rows_n is None and st.get("deliverable_rows") is not None:
                try:
                    rows_n = int(st.get("deliverable_rows"))
                except (TypeError, ValueError):
                    rows_n = None
            if est_n is None and st.get("cohort_uid_estimate") is not None:
                try:
                    est_n = int(st.get("cohort_uid_estimate"))
                except (TypeError, ValueError):
                    est_n = None
            completeness_now = str(st.get("completeness") or "").strip()
            digest_now = str(st.get("digest") or "").strip()
            if isinstance(st.get("repair_plan"), dict):
                repair_plan_now = st.get("repair_plan")
        skill_dirs: list[Path] = []
        if skill_ids:
            try:
                from app.config import get_settings

                data_skills = Path(get_settings().data_dir) / "skills"
                for sid in skill_ids:
                    base = data_skills / str(sid)
                    if base.is_dir():
                        skill_dirs.append(base)
            except Exception:
                skill_dirs = []
        _md, _paths, section = materialize_export_skill_lesson(
            sandbox.id,
            run_id,
            user_message=user_message,
            mode=mode,
            deliverable=deliverable,
            covered_roles=covered_roles,
            missing_roles=missing_roles,
            fetched_view_pages=fetched_view_pages,
            column_todos=col_todos,
            time_window_label=_export_time_window_label(time_window),
            fallback=(mode == "fallback"),
            mcp_failures=mcp_failures,
            fact_truncated_roles=fact_trunc,
            deliverable_rows=rows_n,
            cohort_uid_estimate=est_n,
            completeness=completeness_now,
            digest=digest_now,
            repair_plan=repair_plan_now,
            shell_enabled=shell_enabled,
            skill_dirs=skill_dirs,
            session_id=session_id,
        )
    except Exception:
        logger.exception("export skill lesson failed run=%s", run_id)
        return analysis_md
    if not section:
        return analysis_md
    if "### 建议写入 Skill" in (analysis_md or ""):
        return analysis_md
    return ((analysis_md or "").rstrip() + "\n\n" + section).strip()


def _human_file_size(n: int) -> str:
    if n < 1024:
        return f"{n}B"
    if n < 1024 * 1024:
        return f"{n / 1024:.0f}KB"
    return f"{n / (1024 * 1024):.1f}MB"


def _is_valid_saved_deliverable(sandbox: Sandbox | None, rel: str) -> bool:
    if not rel or not _is_data_export_path(rel):
        return False
    if rel.startswith("task/") or rel.startswith("workplace/") or "_engine_checkpoints" in rel:
        return False
    if not sandbox:
        return True
    p = download_path(sandbox.id, rel)
    return bool(p and is_valid_deliverable_file(p))


def _current_dir_deliverables(paths: list[str], sandbox: Sandbox | None = None) -> list[str]:
    out: list[str] = []
    for p in paths or []:
        if not p or not _is_data_export_path(p):
            continue
        if p.startswith("task/") or p.startswith("workplace/") or "_engine_checkpoints" in p:
            continue
        if sandbox and not _is_valid_saved_deliverable(sandbox, p):
            continue
        if p not in out:
            out.append(p)
    return out[:3]


_RUN_ID_IN_NAME_RE = re.compile(r"(?<![0-9])(\d{10,16})(?![0-9])")


def _run_ids_in_filename(name: str) -> list[str]:
    """Extract millisecond-style run ids embedded in a deliverable filename."""
    return _RUN_ID_IN_NAME_RE.findall(Path(name or "").name)


def _is_run_owned_deliverable(
    rel: str,
    *,
    sandbox: Sandbox | None,
    run_id: str = "",
    started_at: float | None = None,
    saved_paths: list[str] | None = None,
) -> bool:
    """Whether a root deliverable belongs to this export run (concurrent-safe)."""
    if not rel:
        return False
    if rel.startswith("task/") or rel.startswith("workplace/"):
        return False
    if Path(rel).suffix.lower() not in {".xlsx", ".xlsm", ".xls", ".csv"}:
        return False
    paths = saved_paths or []
    if rel in paths:
        return True
    name = Path(rel).name
    rid = str(run_id or "").strip()
    if rid and rid in name:
        return True
    embedded = _run_ids_in_filename(name)
    if embedded and (not rid or rid not in embedded):
        # Tagged with another run id — do not claim
        return False
    if started_at is None or not sandbox:
        return False
    p = download_path(sandbox.id, rel)
    if not p or not p.is_file():
        return False
    try:
        # No negative skew: untagged files from just before start must not be claimed
        return p.stat().st_mtime >= float(started_at)
    except OSError:
        return False


def _find_export_root_deliverables(
    sandbox: Sandbox | None,
    save_dir: str,
    saved_paths: list[str] | None = None,
    *,
    run_id: str = "",
    started_at: float | None = None,
) -> list[str]:
    """Valid xlsx/csv in current dir (save_dir or workplace root), not under task/.

    When run_id/started_at provided, only return files owned by this run
    (saved_paths / filename run_id / recent untagged mtime).
    """
    found = list(_current_dir_deliverables(saved_paths or [], sandbox))
    if not sandbox:
        return found[:5]
    sd = (save_dir or "").strip().strip("/")
    for rel in list_recent_data_files(sandbox.id, limit=30):
        if not rel or rel.startswith("task/") or rel.startswith("workplace/"):
            continue
        if "_engine_checkpoints" in rel:
            continue
        if sd:
            if rel != sd and not rel.startswith(sd + "/"):
                continue
            rest = rel[len(sd) + 1 :] if rel.startswith(sd + "/") else ""
            if rest and "/" in rest:
                continue
        elif "/" in rel:
            continue
        if _is_valid_saved_deliverable(sandbox, rel) and rel not in found:
            found.append(rel)
    if run_id or started_at is not None:
        found = [
            rel
            for rel in found
            if _is_run_owned_deliverable(
                rel,
                sandbox=sandbox,
                run_id=run_id,
                started_at=started_at,
                saved_paths=saved_paths,
            )
        ]
    return found[:5]


def _strip_gfm_tables(text: str) -> str:
    if not text:
        return ""
    cleaned = _GFM_TABLE_BLOCK_RE.sub("\n", text)
    return re.sub(r"\n{3,}", "\n\n", cleaned).strip()


_EXPORT_APPENDIX_MARKERS = (
    "### 导出概况",
    "### 字段说明",
    "### 完整性/缺口说明",
    # legacy headings (still preserve if old FINAL present)
    "### 分析摘要",
    "### 报表统计",
    "### 输出列说明",
    "### 可靠性校验",
)

# Platform FINAL sections (tables kept; only strip junk before first marker)
_EXPORT_PRESERVE_FROM_MARKERS = (
    "### 导出概况",
    "### 字段说明",
    "### 下载文件",
    "### 统计结果",  # legacy
    *_EXPORT_APPENDIX_MARKERS,
    "### 建议写入 Skill",
    "### 数据来源",
)


def _strip_gfm_tables_preserving_appendix(text: str) -> str:
    """Strip model sample tables, but keep platform export sections intact."""
    if not text:
        return ""
    cut = -1
    for marker in _EXPORT_PRESERVE_FROM_MARKERS:
        i = text.find(marker)
        if i >= 0 and (cut < 0 or i < cut):
            cut = i
    if cut < 0:
        return _strip_gfm_tables(text)
    head = _strip_gfm_tables(text[:cut])
    tail = text[cut:].strip()
    if head and tail:
        return head.rstrip() + "\n\n" + tail
    return tail or head


def _export_section_body(text: str, heading: str, *, stops: tuple[str, ...]) -> str:
    """Body after a ### heading until the next stop marker (exclusive)."""
    i = text.find(heading)
    if i < 0:
        return ""
    start = i + len(heading)
    while start < len(text) and text[start] in "\r\n \t":
        start += 1
    end = len(text)
    for stop in stops:
        j = text.find(stop, start)
        if j >= 0:
            end = min(end, j)
    return text[start:end].strip()


def _export_appendix_incomplete(text: str) -> bool:
    """True when 导出概况/字段说明 headings exist but table/list body is missing."""
    t = text or ""
    # Prefer new screenshot shape
    if "### 导出概况" in t:
        def _has_body(body: str) -> bool:
            return bool(
                body
                and (
                    re.search(r"^\|.+\|", body, re.M)
                    or re.search(r"^\s*[-*]\s+", body, re.M)
                )
            )

        body = _export_section_body(
            t,
            "### 导出概况",
            stops=("### 字段说明", "### 下载文件", "### 完整性/缺口说明", "备注:"),
        )
        if not _has_body(body):
            return True
        # Match both "### 字段说明" and "### 字段说明（共 N 列）"
        field_heading = "### 字段说明"
        body2 = _export_section_body(
            t,
            field_heading,
            stops=("### 下载文件", "### 完整性/缺口说明", "备注:", "### "),
        )
        if not _has_body(body2):
            return True
        return False

    # Legacy shape
    if "### 报表统计" not in t:
        return "### 分析摘要" in t and "### 报表统计" not in t

    def _has_body_legacy(body: str) -> bool:
        return bool(
            body
            and (
                re.search(r"^\|.+\|", body, re.M)
                or re.search(r"^\s*[-*]\s+", body, re.M)
            )
        )

    body = _export_section_body(
        t,
        "### 报表统计",
        stops=("### 输出列说明", "### 可靠性校验", "### 完整性/缺口说明", "备注:"),
    )
    if not _has_body_legacy(body):
        return True
    body2 = _export_section_body(
        t,
        "### 输出列说明",
        stops=("### 可靠性校验", "### 完整性/缺口说明", "备注:", "### "),
    )
    if not _has_body_legacy(body2):
        return True
    return False


def _critical_empty_column_failures(path: Path, headers: list[str]) -> list[str]:
    """Columns that must not be mostly empty for analyzed delivery."""
    checks = (
        ("流水倍数", "流水倍数"),
        ("SC投注金额最多的游戏", "SC投注金额最多的游戏"),
        ("是否有退款", "是否有退款"),
    )
    bad: list[str] = []
    for label, *aliases in checks:
        ratio = _column_empty_ratio(path, headers, label, *aliases)
        if ratio is not None and ratio >= 0.8:
            bad.append(f"{label}(空值{ratio:.0%})")
    return bad


def _export_final_task_title(user_message: str, file_rel: str) -> str:
    """Short title for FINAL opening line."""
    name = Path(file_rel or "").stem.strip()
    if name and name not in ("export", "data", "report", "out"):
        return name
    raw = re.sub(r"\s+", " ", (user_message or "").strip())
    if not raw:
        return "导出任务"
    # Prefer first clause / first line
    raw = raw.split("\n", 1)[0].strip()
    for sep in ("。", "；", ";", "，", ","):
        if sep in raw:
            raw = raw.split(sep, 1)[0].strip()
            break
    if len(raw) > 48:
        raw = raw[:45] + "…"
    return raw or "导出任务"


def _format_export_final(
    *,
    user_message: str,
    file_rel: str,
    file_size: int | None = None,
    row_hint: int | None = None,
    preview_md: str = "",
    mode: str = "analyzed",
    todo_done: int = 0,
    todo_total: int = 0,
    missing: list[str] | None = None,
    analysis_md: str = "",
    filter_condition: str = "",
    platform_assisted: bool = False,
    engine_executed: bool = False,
    verifier_status: str = "",
    shell_enabled: bool = True,
) -> str:
    """Screenshot-shaped FINAL: opening + 导出概况/字段说明 + 下载（无工程交付类型表）."""
    del preview_md, todo_done, todo_total  # engineering progress stays out of FINAL
    raw_cond = (filter_condition or user_message or "").strip()
    cond = re.sub(r"\s+", " ", raw_cond)
    if len(cond) > 160:
        cond = cond[:157] + "…"

    is_fallback = mode == "fallback"
    title = _export_final_task_title(user_message, file_rel)
    fname = Path(file_rel).name if file_rel else "deliverable.xlsx"

    vstatus = (verifier_status or "").strip().lower()
    if is_fallback:
        opening = (
            f"**{title}（原始回退）**\n\n"
            "未完成中文分析表，已合并原始宽表（非用户要求的分析列）。"
            "下列缺口说明供续跑；概况数字不作为分析结论。"
        )
    elif vstatus == "repairable":
        opening = (
            f"**{title}已生成，但存在可修复缺口。** "
            "文件已落盘；概况数字由交付 xlsx 确定性复算，"
            "缺口请以校验状态和修复建议为准。"
        )
    else:
        trust = (
            "所有数据均基于真实 ClickHouse / MCP 查询与多表关联统计，"
            "概况数字由交付 xlsx 确定性复算。"
        )
        opening = f"**{title}已完成！** {trust}"
        if engine_executed:
            opening += "\n\n> 说明：本文件由**引擎代执行**已有分析脚本生成。"
        elif platform_assisted:
            opening += "\n\n> 说明：本文件由**平台辅助 join**生成"
            if shell_enabled:
                opening += "（LLM SHELL 未产出合格中文分析表）。"
            else:
                opening += "（当前 Agent 未启用 SHELL，由引擎直接完成物化与校验）。"

    parts: list[str] = [opening.strip()]
    appendix = (analysis_md or "").strip()
    # Drop duplicate assist/engine notes if callers already prefixed analysis_md
    if platform_assisted or engine_executed:
        appendix = re.sub(
            r"^(?:>\s*)?说明：本文件由\*\*(?:平台辅助 join|引擎代执行)[^\n]*\n+",
            "",
            appendix,
            count=2,
            flags=re.M,
        ).strip()
    if is_fallback:
        if appendix:
            parts.append(appendix)
    elif "### 导出概况" in appendix:
        parts.append(appendix)
    else:
        # Minimal overview when appendix missing / incomplete
        tw = cond or "见用户需求"
        mini = [
            "### 导出概况",
            "| 指标 | 数值 |",
            "| --- | --- |",
            f"| 时间范围 | {tw} |",
        ]
        if row_hint is not None:
            mini.append(f"| 总用户数 | {row_hint:,} |")
        if file_size is not None:
            mini.append(f"| 文件大小 | {_human_file_size(file_size)} |")
        parts.append("\n".join(mini))
        if appendix and (
            appendix.startswith("###")
            or appendix.startswith(">")
            or "字段说明" in appendix
        ):
            parts.append(appendix)

    if is_fallback and missing:
        miss_txt = "、".join(missing[:12]) + ("…" if len(missing) > 12 else "")
        if "未命中" not in (appendix or ""):
            parts.append(f"- 未命中分析列：{miss_txt}")

    # Download section (always for user-facing report)
    size_bit = f"（{_human_file_size(file_size)}）" if file_size is not None else ""
    row_bit = f"，约 {row_hint:,} 行" if row_hint is not None else ""
    parts.append(
        "### 下载文件\n"
        f"- [`{fname}`]({file_rel}){size_bit}{row_bit}"
    )
    return "\n\n".join(p for p in parts if p).strip()


def _mcp_total_row_hint(mcp_results: list[dict]) -> int | None:
    """Prefer summed page sizes when multiple MCP pages; else last page if large."""
    if not mcp_results:
        return None
    total = 0
    any_count = False
    for item in mcp_results:
        try:
            n = int(item.get("row_count") or 0)
        except (TypeError, ValueError):
            n = 0
        if n > 0:
            total += n
            any_count = True
    if not any_count:
        return None
    return total if total > 0 else None


def _build_exhaust_fallback(
    *,
    sandbox: Sandbox | None,
    save_dir: str,
    saved_paths: list[str],
    progress_lines: list[str],
    last_reply: str,
    final: str,
    mcp_results: list[dict],
    export_like: bool,
    user_message: str = "",
    filter_condition: str = "",
    run_state: dict | None = None,
    max_iters: int | None = None,
    shell_enabled: bool = True,
) -> tuple[str, list[str]]:
    """Exhaust without FINAL: promote/materialize best task data; concise export template."""
    del progress_lines  # progress is for coach UI, not exhaust trigger
    # Non-export: never wipe an already-useful conclusion (clarify / LLM error / last reply)
    if not export_like:
        kept = (final or "").strip()
        if kept and not _looks_like_tool_call(kept):
            return kept, list(saved_paths)
        cleaned = _clean_display_text(last_reply or "")
        if cleaned and not _looks_like_tool_call(cleaned):
            return cleaned, list(saved_paths)
        iters_bit = (
            f"（当前最大轮次 {int(max_iters)}）"
            if max_iters is not None
            else ""
        )
        return (
            "任务未完成"
            + iters_bit
            + "。请重试，或在 Agent 高级设置提高最大轮次；"
            "查数请直接 MCP list→describe→最小 query，勿空转叙述。",
            list(saved_paths),
        )
    paths = list(saved_paths)
    root_files = _current_dir_deliverables(paths, sandbox)

    preferred = ""
    for p in root_files or paths:
        if p and _is_data_export_path(p) and not p.startswith("task/"):
            preferred = Path(p).name
            break

    if sandbox and export_like:
        made = materialize_export_deliverable(
            sandbox.id,
            preferred_name=preferred or "export.xlsx",
            target_dir=save_dir,
            ignore_existing=True,
        )
        if made:
            if made not in paths:
                paths.append(made)
            root_files = [made]
        else:
            root_files = _current_dir_deliverables(paths, sandbox)

    if sandbox and not root_files:
        for rel in list_recent_data_files(sandbox.id, limit=20):
            if rel.startswith("task/") or rel.startswith("workplace/"):
                continue
            if save_dir:
                sd = save_dir.strip("/")
                if rel != sd and not rel.startswith(sd + "/") and "/" in rel:
                    continue
            if _is_data_export_path(rel) and _is_valid_saved_deliverable(sandbox, rel):
                root_files.append(rel)
                if rel not in paths:
                    paths.append(rel)
                break

    if sandbox and not root_files:
        best = find_best_task_data_file(sandbox.id)
        if best:
            promoted = promote_named_files(sandbox.id, [best], target_dir=save_dir)
            for p in promoted:
                if p not in paths:
                    paths.append(p)
                if p not in root_files and _is_valid_saved_deliverable(sandbox, p):
                    root_files.append(p)

    row_hint = _mcp_total_row_hint(mcp_results)

    if root_files:
        file_rel = root_files[0]
        file_size = None
        if sandbox:
            p = download_path(sandbox.id, file_rel)
            if p and p.is_file():
                try:
                    file_size = p.stat().st_size
                except OSError:
                    file_size = None
                file_rows = count_data_rows(p)
                if file_rows is not None:
                    row_hint = file_rows
        text = _format_export_final(
            user_message=user_message,
            file_rel=file_rel,
            file_size=file_size,
            row_hint=row_hint,
            preview_md="",
            mode="fallback",
            analysis_md=_build_export_fallback_appendix(
                row_hint=row_hint,
                time_window=None,
                shell_enabled=shell_enabled,
            ),
            filter_condition=filter_condition,
            shell_enabled=shell_enabled,
        )
        return text, paths

    # Nothing to deliver — smart empty FINAL from run_state / task presence
    if export_like:
        has_task = bool(sandbox and task_has_exportable_data(sandbox.id))
        st = run_state if isinstance(run_state, dict) else {}
        comp = str(st.get("completeness") or "").strip()
        if not comp:
            comp = "iters_exhausted" if has_task else "no_data"
        digest = str(st.get("digest") or "").strip() or _build_run_state_digest(
            completeness=comp,
            missing_roles=list(st.get("missing_roles") or []),
            fact_truncated_roles=list(st.get("fact_truncated_roles") or []),
            need_continue_roles=list(st.get("need_continue_roles") or []),
            deliverable_rows=st.get("deliverable_rows"),
            cohort_uid_estimate=st.get("cohort_uid_estimate"),
            has_task_data=has_task,
        )
        actions = st.get("next_actions")
        if not isinstance(actions, list) or not actions:
            actions = _build_run_state_next_actions(
                completeness=comp,
                need_continue_roles=list(st.get("need_continue_roles") or []),
                missing_roles=list(st.get("missing_roles") or []),
                fetched_view_pages=st.get("fetched_view_pages")
                if isinstance(st.get("fetched_view_pages"), dict)
                else {},
                time_window=st.get("time_window")
                if isinstance(st.get("time_window"), dict)
                else None,
                has_task_data=has_task,
            )
        text = _format_smart_empty_export_final(
            completeness=comp,
            digest=digest,
            next_actions=actions,
        )
        return text, paths
    return (
        "任务未完成。请重试，或在 Agent 高级设置提高最大轮次。",
        paths,
    )


def _is_mcp_tool_failure(tool_result: str) -> bool:
    text = (tool_result or "").strip()
    if not text:
        return True
    lower = text.lower()
    return (
        text.startswith("MCP 错误")
        or text.startswith("MCP 调用失败")
        or text.startswith("MCP 本地拦截")
        or text.startswith("MCP 参数软提示")
        or text.startswith("MCP URL")
        or text.startswith("MCP 无响应")
        or text.startswith("no mcp configured")
        or text.startswith("no httpmcp configured")
        or "unknown tool" in lower
        or "stdio mcp 失败" in lower
        or "【硬熔断】" in text
    )


def _is_mcp_preflight_feedback(tool_result: str) -> bool:
    """True for local parameter/schema coaching before any remote MCP call happened."""
    text = (tool_result or "").strip()
    if not text:
        return False
    return (
        text.startswith("MCP 参数软提示")
        or text.startswith("MCP 本地拦截")
        or text.startswith("【schema 预检】")
    )


def _classify_mcp_error(result: str) -> str:
    """Map enriched MCP error text to a stable class (for circuit + lessons)."""
    text = result or ""
    lower = text.lower()
    if (
        "format jsoneachrow" in lower
        or "禁止含 format" in lower
        or "format 纠偏" in lower
        or ("('format')" in lower and "syntax error" in lower)
        or ("failed at position" in lower and "format" in lower)
        or ("expected one of: settings" in lower and "format" in lower)
    ):
        return "format_clause"
    if "invalid_union" in lower:
        return "invalid_union"
    if "only select" in lower:
        return "select_only"
    if "from ads" in lower or "whitelist_view" in lower:
        return "from_ads"
    if any(
        k in lower
        for k in (
            "where 必须",
            "where 期望",
            "where 不能",
            "cannot convert string",
            "收到 string",
            "where 纠偏",
            "类型不匹配",
        )
    ):
        return "where_sql"
    if "unknown tool" in lower:
        return "unknown_tool"
    if (
        text.startswith("MCP 本地拦截")
        or text.startswith("MCP 参数软提示")
        or "本地拦截" in text
        or "参数软提示" in text
    ):
        return "local_validate"
    if any(
        k in text or k in lower
        for k in (
            "MCP 无响应",
            "MCP 调用失败",
            "MCP URL",
            "timeout",
            "timed out",
            "连接",
        )
    ):
        return "transport"
    return "other"


def _summarize_mcp_failures(
    class_fails: dict[str, int],
    class_samples: dict[str, str],
    class_tools: dict[str, str],
    *,
    limit: int = 8,
) -> list[dict]:
    items = sorted(class_fails.items(), key=lambda x: (-x[1], x[0]))[:limit]
    out: list[dict] = []
    for cls, count in items:
        out.append({
            "tool": class_tools.get(cls, ""),
            "class": cls,
            "count": int(count),
            "sample": (class_samples.get(cls) or "")[:200],
        })
    return out


def _step_results_are_mcp_failures_only(step_results: list[str]) -> bool:
    """True when every multi-step result is an MCP/HTTPMCP failure (or hard block)."""
    if not step_results:
        return False
    for r in step_results:
        m = re.match(r"^\[([^\]]+)\]\s*(.*)$", r or "", re.DOTALL)
        if not m:
            return False
        act, body = m.group(1), m.group(2)
        if act not in ("mcp_tool_call", "httpmcp_call"):
            return False
        if _is_mcp_preflight_feedback(body):
            return False
        if not _is_mcp_tool_failure(body):
            return False
    return True


async def _get_mcp_tools_cached(mcp: MCP) -> list[dict]:
    mid = mcp.id or ""
    now = time.monotonic()
    hit = _mcp_tools_cache.get(mid)
    if hit and now - hit[0] < _MCP_TOOLS_TTL_SEC and hit[1]:
        return hit[1]
    try:
        detail = await connect_mcp_detail(mcp)
    except Exception:
        return hit[1] if hit else []
    tools = detail.get("tools") or []
    if tools:
        _mcp_tools_cache[mid] = (now, tools)
    return tools


def _tool_input_schema(tool: dict) -> dict:
    schema = tool.get("inputSchema") or tool.get("input_schema") or {}
    return schema if isinstance(schema, dict) else {}


def _tool_required_fields(tool: dict) -> list[str]:
    schema = _tool_input_schema(tool)
    req = schema.get("required")
    if isinstance(req, list):
        return [str(x) for x in req if x]
    return []


# Known MCP tool call examples (ads-sync-hub / getnote). Used in prompts and error hints.
_MCP_TOOL_EXAMPLES: dict[str, str] = {
    "list_ads_views": "MCP: list_ads_views {}",
    "describe_ads_view": 'MCP: describe_ads_view {"view_name":"<待确认资源>"}',
    "query_ads_view": (
        'MCP: query_ads_view {"view":"<已绑定资源>",'
        '"where":{"<已确认字段>":"<条件值>"}}'
    ),
    "query_ads_metric": 'MCP: query_ads_metric {"metric_id":"daily_stat_by_date","params":{"stat_date":"2025-01-01"}}',
    "list_notes": 'MCP: list_notes {"since_id":0}',
    "recall": 'MCP: recall {"query":"关键词"}',
}

_MCP_TOOL_HINTS: dict[str, str] = {
    "describe_ads_view": (
        '期望参数示例: MCP: describe_ads_view {"view_name":"<视图名>"}\n'
        "请先 MCP: list_ads_views {} 再选 view_name；禁止空参数 {}。"
    ),
    "query_ads_view": (
        '期望参数示例: MCP: query_ads_view {"view":"<视图名>","where":{}}\n'
        "注意字段名是 view（不是 view_name）；可用 sql/limit；未知视图时先 list_ads_views；禁止空参数。"
    ),
    "query_ads_metric": (
        '期望参数示例: MCP: query_ads_metric {"metric_id":"<指标id>","params":{}}'
    ),
}

_MCP_QUERY_UNION_HINT = (
    "【invalid_union 纠偏】query_ads_view 的 where 必须是对象，例如 "
    '{"stat_date":"2025-01-01"}，不能是字符串或数组；'
    "过滤字段名须来自 describe_ads_view 返回的真实列名。\n"
    '可执行示例: MCP: query_ads_view {"view":"<视图名>","where":{"stat_date":"YYYY-MM-DD"}}'
)


def _mcp_arg_hint(tool: str) -> str:
    return _MCP_TOOL_HINTS.get((tool or "").strip(), "")


def _format_describe_schema_soft_hint(
    view: str,
    schema_hints: dict | None,
) -> str:
    """Soft coach from cached describe fields — never hard-blocks write/FINAL."""
    v = (view or "").strip()
    if not v or not isinstance(schema_hints, dict):
        return ""
    hint = schema_hints.get(v)
    fields: list[str] = []
    if hasattr(hint, "fields"):
        fields = [str(x) for x in (getattr(hint, "fields", None) or []) if x]
    elif isinstance(hint, dict):
        fields = [str(x) for x in (hint.get("fields") or []) if x]
    if not fields:
        return ""
    shown = ", ".join(fields[:24])
    more = f" …(+{len(fields) - 24})" if len(fields) > 24 else ""
    return (
        f"【schema 软提示】`{v}` 已 describe 字段（须按真实列名改参，禁止猜列）："
        f"{shown}{more}。请 MCP: describe_ads_view 复核后重写 query。"
    )


def _enrich_mcp_failure(
    tool: str,
    tool_result: str,
    *,
    time_window: dict | None = None,
    view: str = "",
    schema_hints: dict | None = None,
    tool_schema_summary: str = "",
) -> str:
    """Append soft coach on failures; never hard-blocks export/FINAL."""
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
        k in lower for k in ("cannot convert string", "类型不匹配", "where 期望 object", "收到 string", "undefined")
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
        if "FROM 纠偏" not in text:
            parts.append(_MCP_FROM_ADS_HINT)
            if time_window and time_window.get("mcp_example"):
                parts.append(f"推荐：`{time_window.get('mcp_example')}`")

    businessish = any(
        k in lower
        for k in (
            "unknown expression",
            "unknown identifier",
            "invalid_type",
            "invalid_union",
            "undefined",
            "required",
            "view_name",
            "validation",
            "missing",
            "expected",
            "received",
            "空参",
            "本地拦截",
            "参数软提示",
            "syntax error",
            "no such column",
            "doesn't exist",
            "does not exist",
        )
    )
    hint = _mcp_arg_hint(tool)
    needs_hint = businessish or transportish
    if hint and needs_hint and "期望参数示例" not in text:
        parts.append(hint)
    if text.strip() in ("", "MCP 无响应") or text.strip().endswith("MCP 调用失败:"):
        if "空失败续拉" not in text:
            parts.append(
                "【空失败续拉】本次无有效错误正文。请改 limit/offset 或换缺 role 的 view 重试；"
                "勿因此散文收工；有数据时可 SHELL 写表后再 FINAL；user_info 时间窗仍须用 sql SELECT。"
            )

    # LLM-perceive path: soft-feed describe / tool schema (no hard gate)
    if businessish or transportish:
        v = (view or "").strip()
        if not v and tool in ("query_ads_view", "describe_ads_view"):
            # best-effort: leave empty; caller should pass view
            pass
        schema_line = _format_describe_schema_soft_hint(v, schema_hints)
        if schema_line and schema_line not in text:
            parts.append(schema_line)
        elif (
            businessish
            and tool in _ADS_MCP_TOOLS
            and "list_ads_views" not in text
            and "describe_ads_view" not in "".join(parts)
        ):
            # ADS-only coach — never inject list_ads_views into OKX/other MCP failures
            parts.append(
                "【接口感知】字段/参数须来自 MCP 真实接口："
                "先 list_ads_views → describe_ads_view(view_name) → 再按返回列名改 query；"
                "禁止臆造列名。此为软教练，不阻止后续写表/FINAL。"
            )
        summary = (tool_schema_summary or "").strip()
        if summary and summary not in text:
            parts.append(f"【tool schema】{summary[:400]}")
        elif (
            businessish
            and tool
            and tool not in _ADS_MCP_TOOLS
            and "tool schema" not in text
            and "【tool schema】" not in "".join(parts)
        ):
            # Non-ADS: nudge toward real tool params without ADS SOP
            hint = _mcp_arg_hint(tool)
            if not hint:
                parts.append(
                    f"【tool schema】`{tool}` 参数须来自该工具真实 inputSchema；"
                    "禁止臆造未声明字段（如 after/before）。此为软教练，不阻止后续写表/FINAL。"
                )

    if len(parts) == 1:
        return text
    return "\n\n".join(parts)


def _parse_mcp_args(normalized: str) -> dict:
    m = re.match(r"MCP:\s*\S+\s*(.*)", normalized or "", re.DOTALL)
    raw = (m.group(1) if m else "").strip()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def _extract_view_from_sql(sql: str) -> str:
    m = _VIEW_FROM_SQL_RE.search(sql or "")
    return (m.group(1) or "").strip() if m else ""


def _extract_view_from_mcp_example(example: str) -> str:
    text = example or ""
    m = re.search(r'"view"\s*:\s*"(view_result_[^"]+)"', text)
    if m:
        return (m.group(1) or "").strip()
    return _extract_view_from_sql(text)


def _looks_like_sql_fragment(text: str) -> bool:
    t = (text or "").strip()
    if not t:
        return False
    if re.search(r"(?is)\b(SELECT|WHERE|FROM|LIMIT|OFFSET)\b", t):
        return True
    if re.search(r"(?i)(register_time|create_time|uid)\s*[><=]", t):
        return True
    return False


def _executable_query_example(
    *,
    time_window: dict | None = None,
    target_roles: list[str] | None = None,
) -> str:
    del time_window, target_roles
    return (
        "请先 list_ads_views / describe_ads_view 绑定 resource，再 "
        f'MCP: query_ads_view {{"view":"<白名单或列计划 view>","limit":{_EXPORT_PAGE_LIMIT}}}'
    )


def _autofill_ads_query_args(
    args: dict,
    *,
    time_window: dict | None = None,
    target_roles: list[str] | None = None,
    missing_roles: list[str] | None = None,
    fetched_view_pages: dict[str, int] | None = None,
    dim_views: dict[str, str] | None = None,
) -> tuple[dict, list[str]]:
    """Fill missing view / repair where-as-SQL before local validate.

    Returns (args, human notes of what was auto-filled).
    """
    out = dict(args or {})
    notes: list[str] = []
    del time_window
    # target/missing roles intentionally unused — never invent archive views from them
    _ = (target_roles, missing_roles)

    # view_name → view (also done in normalize; keep here for autofill-only callers)
    view = str(out.get("view") or "").strip()
    view_name = str(out.get("view_name") or "").strip()
    if not view and view_name:
        out["view"] = view_name
        out.pop("view_name", None)
        view = view_name
        notes.append(f"view_name→view=`{view}`")

    # where as SQL string → move to sql
    where = out.get("where")
    if isinstance(where, str) and where.strip():
        try:
            parsed = json.loads(where)
            if isinstance(parsed, dict):
                out["where"] = parsed
            elif _looks_like_sql_fragment(where):
                if not str(out.get("sql") or "").strip():
                    out["sql"] = where.strip()
                    notes.append("where(字符串SQL)→sql")
                out.pop("where", None)
        except Exception:
            if _looks_like_sql_fragment(where):
                if not str(out.get("sql") or "").strip():
                    out["sql"] = where.strip()
                    notes.append("where(字符串SQL)→sql")
                out.pop("where", None)

    sql = str(out.get("sql") or "").strip()
    view = str(out.get("view") or "").strip()

    if not view and sql:
        from_sql = _extract_view_from_sql(sql)
        if from_sql:
            out["view"] = from_sql
            view = from_sql
            notes.append(f"从 sql FROM 补全 view=`{from_sql}`")

    del dim_views, fetched_view_pages

    # If we moved a WHERE fragment into sql without SELECT, wrap with view
    sql_now = str(out.get("sql") or "").strip()
    view_now = str(out.get("view") or "").strip()
    if (
        view_now
        and sql_now
        and not re.match(r"(?is)^SELECT\b", sql_now)
        and _looks_like_sql_fragment(sql_now)
    ):
        out["sql"] = _ensure_ads_sql_from(view_now, sql_now)
        notes.append("将过滤片段包装为 SELECT … FROM ads.<view>")
        sql_now = str(out.get("sql") or "").strip()

    return out, notes


def _normalize_ads_mcp_args(
    tool: str,
    args: dict,
    *,
    soft_export_page_limit: bool = False,
) -> dict:
    """Fix common model mistakes before hitting remote MCP."""
    out = dict(args or {})
    tool = (tool or "").strip()
    if tool == "query_ads_view":
        view = str(out.get("view") or "").strip()
        view_name = str(out.get("view_name") or "").strip()
        if not view and view_name:
            out["view"] = view_name
            out.pop("view_name", None)
        elif view and "view_name" in out:
            out.pop("view_name", None)
        where = out.get("where")
        if isinstance(where, str) and where.strip():
            try:
                parsed = json.loads(where)
                if isinstance(parsed, dict):
                    out["where"] = _sanitize_where_dict(parsed)
                # non-dict JSON left as-is for local validate / autofill
            except Exception:
                # SQL / free-text — autofill may move to sql; leave for now
                pass
        elif isinstance(where, dict):
            out["where"] = _sanitize_where_dict(where)
        # Ensure sql has FROM ads.<view> when present; strip FORMAT / trailing ;
        view_final = str(out.get("view") or "").strip()
        sql_raw = out.get("sql")
        if isinstance(sql_raw, str) and sql_raw.strip():
            cleaned, _had_fmt = _strip_clickhouse_format_clause(sql_raw)
            if view_final:
                out["sql"] = _ensure_ads_sql_from(view_final, cleaned)
            else:
                out["sql"] = cleaned
        # Export fetch: fill/raise tiny limits so remote default (~30) cannot fake short-page.
        # Non-export / discover keep omitted limit (avoid forcing huge sample pages).
        if soft_export_page_limit:
            out, _note = _soft_align_export_query_limit(out)
    elif tool == "describe_ads_view":
        view_name = str(out.get("view_name") or "").strip()
        view = str(out.get("view") or "").strip()
        if not view_name and view:
            out["view_name"] = view
            out.pop("view", None)
        elif view_name and "view" in out:
            out.pop("view", None)
    return out


def _rewrite_mcp_with_normalized_args(
    normalized: str,
    *,
    soft_export_page_limit: bool = False,
) -> str:
    tool = _mcp_tool_name(normalized)
    if tool not in ("query_ads_view", "describe_ads_view"):
        return normalized
    args = _parse_mcp_args(normalized)
    fixed = _normalize_ads_mcp_args(
        tool, args, soft_export_page_limit=soft_export_page_limit,
    )
    if fixed == args:
        return normalized
    return f"MCP: {tool} {json.dumps(fixed, ensure_ascii=False)}"


def _rewrite_mcp_with_autofill(
    normalized: str,
    *,
    time_window: dict | None = None,
    target_roles: list[str] | None = None,
    missing_roles: list[str] | None = None,
    fetched_view_pages: dict[str, int] | None = None,
    dim_views: dict[str, str] | None = None,
    soft_export_page_limit: bool = False,
) -> tuple[str, list[str]]:
    """normalize + context autofill. Returns (rewritten MCP line, autofill notes)."""
    tool = _mcp_tool_name(normalized)
    if tool not in ("query_ads_view", "describe_ads_view"):
        return normalized, []
    args = _parse_mcp_args(normalized)
    notes: list[str] = []
    if tool == "query_ads_view":
        args, notes = _autofill_ads_query_args(
            args,
            time_window=time_window,
            target_roles=target_roles,
            missing_roles=missing_roles,
            fetched_view_pages=fetched_view_pages,
            dim_views=dim_views,
        )
    elif tool == "describe_ads_view":
        # Mirror query autofill lightly for empty describe
        view_name = str(args.get("view_name") or args.get("view") or "").strip()
        if not view_name:
            filled, qnotes = _autofill_ads_query_args(
                {"view": "", "sql": str(args.get("sql") or "")},
                time_window=time_window,
                target_roles=target_roles,
                missing_roles=missing_roles,
                fetched_view_pages=fetched_view_pages,
                dim_views=dim_views,
            )
            v = str(filled.get("view") or "").strip()
            if v:
                args["view_name"] = v
                args.pop("view", None)
                notes = [n.replace("view=", "view_name=") for n in qnotes] or [
                    f"补全 view_name=`{v}`"
                ]
    before_lim = args.get("limit") if tool == "query_ads_view" else None
    fixed = _normalize_ads_mcp_args(
        tool, args, soft_export_page_limit=soft_export_page_limit,
    )
    if (
        soft_export_page_limit
        and tool == "query_ads_view"
        and fixed.get("limit") != before_lim
    ):
        notes.append(f"已对齐 limit→{fixed.get('limit')}（导出分页）")
    return f"MCP: {tool} {json.dumps(fixed, ensure_ascii=False)}", notes


def _mcp_local_validate(
    normalized: str,
    *,
    time_window: dict | None = None,
    target_roles: list[str] | None = None,
) -> str | None:
    """Soft-coach unrecoverable ads args — do not hit remote MCP.

    FORMAT/semicolon should already be stripped by normalize; only soft-prompt
    if still present. Missing view after autofill → soft example (not a hard gate).
    """
    tool = _mcp_tool_name(normalized)
    raw_args = _parse_mcp_args(normalized)
    args = _normalize_ads_mcp_args(tool, raw_args)
    if tool == "describe_ads_view":
        if not str(args.get("view_name") or "").strip():
            ex = _executable_query_example(
                time_window=time_window, target_roles=target_roles,
            )
            # describe uses view_name
            desc_ex = re.sub(
                r"query_ads_view",
                "describe_ads_view",
                ex,
                count=1,
            )
            desc_ex = desc_ex.replace('"view":', '"view_name":')
            return _enrich_mcp_failure(
                tool,
                "MCP 参数软提示: describe_ads_view 缺少 view_name（勿空参数 {}）。\n"
                "请先 list_ads_views，或使用：\n"
                f"{desc_ex}",
                time_window=time_window,
            )
    elif tool == "query_ads_view":
        sql_norm = str(args.get("sql") or "")
        # Normalize should strip FORMAT; only soft-prompt if residue remains
        if re.search(r"\bFORMAT\b", sql_norm, re.I) or sql_norm.rstrip().endswith(";"):
            return _enrich_mcp_failure(
                tool,
                "MCP 参数软提示: sql 勿含 FORMAT / 结尾分号（平台自动 FORMAT）。\n"
                + _MCP_FORMAT_STRIP_HINT
                + "\n请去掉 FORMAT 后用同一 SELECT + limit 分页重试。",
                time_window=time_window,
            )
        if not str(args.get("view") or "").strip():
            ex = _executable_query_example(
                time_window=time_window, target_roles=target_roles,
            )
            return _enrich_mcp_failure(
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
            return _enrich_mcp_failure(
                tool,
                "MCP 参数软提示: query_ads_view 的 where 不能是数组，须为对象。"
                f"\n{_MCP_WHERE_SQL_HINT}",
                time_window=time_window,
            )
        if isinstance(where, str) and where.strip():
            # Still a string after autofill/normalize → not valid JSON object
            return _enrich_mcp_failure(
                tool,
                "MCP 参数软提示: where 必须是 JSON 对象，不能是 SQL/字符串片段。"
                f"\n{_MCP_WHERE_SQL_HINT}\n"
                "若要写 SQL，请用字段 `sql`（完整 SELECT），不要把 SQL 放进 where。",
                time_window=time_window,
            )
    return None


def _mcp_error_signature(tool: str, result: str) -> str:
    key = re.sub(r"\s+", " ", (result or "")[:200].lower()).strip()
    return f"{(tool or '').strip()}|{key}"


def _is_data_export_path(path: str) -> bool:
    lower = (path or "").lower()
    return any(lower.endswith(suf) for suf in _DATA_FILE_SUFFIXES)


def _is_send_existing_to_channel_intent(user_message: str) -> bool:
    """True when user wants to push an existing deliverable to a bound IM channel."""
    text = (user_message or "").strip()
    if not text or len(text) > 400:
        return False
    # Do not treat「分析」inside filenames as re-query intent
    stripped = _RE_DATA_FILENAME.sub(" ", text)
    if _RE_REQUERY.search(stripped):
        return False
    if _RE_SEND_LOOSE_TO_CHANNEL.search(text) and _RE_EXCEL_OR_SELECTED.search(text):
        return True
    if _RE_SELECTED_FILE_TO_CHANNEL.search(text):
        return True
    has_send = bool(_RE_SEND_CHANNEL_VERB.search(text))
    has_target = bool(_RE_CHANNEL_TARGET.search(text))
    has_file = bool(_RE_DATA_FILENAME.search(text))
    if has_send and (has_target or has_file):
        return True
    return bool(_RE_SEND_TG.search(text))


def _normalize_workplace_files(
    sandbox_id: str | None,
    paths: list[str] | None,
) -> list[str]:
    """Keep only existing deliverable paths under workplace (relative)."""
    if not sandbox_id or not paths:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for raw in paths:
        rel = (raw or "").strip().lstrip("/")
        if not rel or rel in seen or ".." in rel.split("/"):
            continue
        if not _is_data_export_path(rel):
            continue
        p = download_path(sandbox_id, rel)
        if not p or not is_valid_deliverable_file(p):
            continue
        seen.add(rel)
        out.append(rel)
    return out


# Backward-compatible alias
_is_send_existing_to_tg_intent = _is_send_existing_to_channel_intent


_RE_NEW_EXPORT_SIGNAL = re.compile(
    r"(?:导出|报表|xlsx|excel|落盘|新增注册|"
    r"\d{4}[-/年]\d{1,2}[-/月]\d{1,2})",
    re.I,
)


def _count_numbered_cols(text: str) -> int:
    n = 0
    for m in re.finditer(
        r"(?:^|\n)\s*(?:\d+[.)、]|[-*•])\s*(.+?)(?=\n|$)",
        text or "",
        re.M,
    ):
        if len((m.group(1) or "").strip()) >= 2:
            n += 1
    return n


def _resolve_repair_source_brief(
    *,
    prior_user: str,
    prior_state: dict | None,
) -> str:
    state = prior_state or {}
    base = str(state.get("source_brief") or "").strip()
    if not base:
        base = (prior_user or "").strip()
    if not base:
        base = "按上一轮导出需求补齐缺口并重新导出分析表"
    return base


def _build_export_repair_system_hint(
    *,
    user_message: str,
    prior_state: dict | None,
    prior_run_id: str = "",
    resolved_tw: dict | None = None,
    window_changed: bool = False,
) -> str:
    """Gap / coverage hint for system prompt — must NOT enter column todo parsing."""
    state = prior_state or {}
    gaps = infer_repair_gaps(state)
    covered = [str(r) for r in (state.get("covered_roles") or []) if r]
    pages = state.get("fetched_view_pages") or {}
    page_bits = [
        f"{v}×{int(n or 0)}"
        for v, n in sorted(pages.items())[:12]
        if int(n or 0) > 0
    ]
    tw = resolved_tw if isinstance(resolved_tw, dict) else None
    if not tw:
        tw = _hydrate_time_window_from_state(state)
    tw_label = (tw or {}).get("label") or _export_time_window_label(
        state.get("time_window") if isinstance(state.get("time_window"), dict) else None
    )
    if window_changed:
        lines = [
            "【本轮时间窗已更新】以当前用户消息解析的时间窗为准；"
            "勿沿用上轮 MCP SQL / OFFSET 续页模板。",
            f"用户原话：{(user_message or '').strip() or '补齐相关列'}",
            "列与角色仍可参考 source_brief / 上轮缺口；禁止把「数据不全」当作筛选条件。",
        ]
    else:
        lines = [
            "【本轮补齐导出】按用户点名列或上轮缺口重新拉数并写表。",
            f"用户原话：{(user_message or '').strip() or '补齐相关列'}",
            "时间窗以上轮为准；输出列以本轮列意图 / source_brief 为准；"
            "禁止把「数据不全」当作筛选条件。",
        ]
    if prior_run_id:
        lines.append(f"基线 run：`{prior_run_id}`")
    if tw_label:
        lines.append(f"时间窗：{tw_label}")
    completeness = str(state.get("completeness") or "").strip()
    digest = str(state.get("digest") or "").strip()
    if completeness:
        lines.append(f"上轮完整度：{completeness}")
    if digest and not window_changed:
        lines.append(f"上轮摘要：{digest}")
    if completeness == "iters_exhausted" and not window_changed:
        lines.append(
            "优先：过程数据可能已在 task/ — 先 SHELL 写当前目录中文表头 xlsx；"
            "缺 role 再 OFFSET 续查。"
        )
    lines.append(
        "仍缺 role：" + ("、".join(gaps) if gaps else "见上轮未完成列 / 继续拉齐目标角色")
    )
    lines.append("上轮已覆盖：" + ("、".join(covered) if covered else "无/未知"))
    if page_bits and not window_changed:
        lines.append("上轮视图页数：" + "，".join(page_bits))
    repair_plan = state.get("repair_plan") if isinstance(state.get("repair_plan"), dict) else {}
    actions = [a for a in (repair_plan.get("actions") or []) if isinstance(a, dict)]
    if actions and not window_changed:
        lines.append("RepairPlan：")
        for a in actions[:5]:
            kind = str(a.get("action_type") or "").strip()
            role = str(a.get("role") or "").strip()
            col = str(a.get("column") or "").strip()
            reason = str(a.get("reason") or "").strip()
            views = [str(v) for v in (a.get("views") or []) if str(v)]
            bits = [kind]
            if role:
                bits.append(f"role={role}")
            if col:
                bits.append(f"column={col}")
            if views:
                bits.append("views=" + ",".join(views[:4]))
            if reason:
                bits.append(reason)
            lines.append("- " + "；".join(bits))
    # Old OFFSET SQL only valid for same window
    if not window_changed:
        for a in (state.get("next_actions") or [])[:2]:
            if not isinstance(a, dict):
                continue
            mcp = str(a.get("mcp_example") or "").strip()
            hint = str(a.get("hint") or "").strip()
            if mcp:
                lines.append(f"建议续查：{mcp}")
            elif hint:
                lines.append(f"建议：{hint}")
    if completeness != "iters_exhausted" or window_changed:
        lines.append(
            "请仅对声明要补的列/资源各查至少 1 页，再 join 写当前目录中文表头 xlsx；"
            "勿默认全量拉取。"
        )
    return "\n".join(lines)


def _build_export_repair_message(
    user_message: str,
    *,
    prior_user: str,
    prior_state: dict | None,
    fill_columns: list[str] | None = None,
    fill_target_rel: str = "",
) -> tuple[str, str]:
    """Return (clean_export_brief, filter_condition_placeholder).

    Brief must stay free of `-` meta bullets so `_parse_export_todos` keeps real columns.
    Gap instructions go to `_build_export_repair_system_hint`.
    Filter label is resolved later via `_resolve_export_time_window` (do not lock prior TW).
    """
    state = prior_state or {}
    cols = [str(c).strip() for c in (fill_columns or []) if str(c).strip()]
    target = str(fill_target_rel or "").strip()
    if cols:
        # Narrow brief: only focus columns (numbered) so fetch column_plan stays scoped
        lines = [f"{i}. {c}" for i, c in enumerate(cols, 1)]
        brief = (
            "【本轮补齐·列意图】仅拉取下列列（勿拉未点名列）；"
            + (
                f"建议覆盖已有交付 `{target}`，保留原表全部列：\n"
                if target
                else "写回时保留原接口全部列（勿另存窄表）：\n"
            )
            + "\n".join(lines)
        )
        del user_message, prior_user, state
        return brief, ""
    base = _resolve_repair_source_brief(prior_user=prior_user, prior_state=state)
    # One non-bullet marker line only — never `- 请优先补齐…`
    brief = (
        f"{base.rstrip()}\n\n"
        + (
            f"【本轮补齐】建议覆盖 `{target}` 按缺口补列（勿另存新文件）"
            if target
            else "【本轮补齐】按缺口重导；也可说明只要补哪些列"
        )
    )
    del user_message  # kept for API compat; gaps live in system hint
    return brief, ""


def _todos_from_analyze_columns(columns: list[str]) -> list[dict]:
    """Rebuild export todos from persisted analyze column headers."""
    specs = [
        {"header": str(c).strip(), "spec": "", "text": str(c).strip()}
        for c in (columns or [])
        if str(c).strip()
    ][:16]
    if not specs:
        return _parse_export_todos("")
    todos: list[dict] = [
        {"id": "phase_discover", "text": "schema已确认", "done": False, "phase": "discover"},
        {"id": "phase_fetch", "text": "数据已落盘 task/page_*", "done": False, "phase": "fetch"},
    ]
    for i, col in enumerate(specs):
        todos.append({
            "id": f"col_{i}",
            "text": col["header"],
            "spec": "",
            "done": False,
            "phase": "analyze",
        })
    todos.append({"id": "phase_final", "text": "FINAL已输出", "done": False, "phase": "finalize"})
    return todos


def _extract_deliverable_filenames(user_message: str) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for m in _RE_DATA_FILENAME.finditer(user_message or ""):
        name = (m.group(1) or "").strip().strip("`\"'")
        base = Path(name).name
        if not base or base.lower() in seen:
            continue
        seen.add(base.lower())
        out.append(base)
    return out


# Allow「将v6的」(no Latin word-boundary before v); reject digits inside 2026
_RE_VERSION_HINT = re.compile(r"(?i)(?<![0-9a-z_])v(\d+)(?![0-9])")
_RE_LATEST_HINT = re.compile(r"(?:最新|latest)", re.I)


def _rel_mtime(sandbox_id: str, rel: str) -> float:
    p = download_path(sandbox_id, rel) if sandbox_id else None
    if not p or not p.exists():
        return 0.0
    try:
        return float(p.stat().st_mtime)
    except OSError:
        return 0.0


def _pick_newest_rel(sandbox_id: str, rels: list[str]) -> str | None:
    clean = [r for r in (rels or []) if (r or "").strip()]
    if not clean:
        return None
    if not sandbox_id:
        return clean[0]
    return max(clean, key=lambda r: _rel_mtime(sandbox_id, r))


def _is_root_deliverable_rel(rel: str) -> bool:
    rel = (rel or "").strip().lstrip("/")
    if not rel or not _is_data_export_path(rel):
        return False
    if rel.startswith("task/") or "_engine_checkpoints" in rel:
        return False
    return True


def _find_deliverable_by_hint(
    sandbox_id: str,
    user_message: str,
    *,
    target_dir: str = "",
) -> str | None:
    """Resolve 「v6」/「最新」 to a single root deliverable (mtime newest)."""
    if not sandbox_id:
        return None
    text = user_message or ""
    td = (target_dir or "").strip().strip("/")
    root = ensure_workplace(sandbox_id)
    candidates: list[str] = []

    def _consider(rel: str):
        rel = (rel or "").strip().lstrip("/")
        if not _is_root_deliverable_rel(rel):
            return
        if td:
            if rel != td and not rel.startswith(td + "/"):
                return
            rest = rel[len(td) + 1 :] if rel.startswith(td + "/") else ""
            if rest and "/" in rest:
                return
        elif "/" in rel:
            # allow one-level subdir only when not using save_dir
            if rel.count("/") > 1:
                return
        p = download_path(sandbox_id, rel)
        if p and is_valid_deliverable_file(p):
            candidates.append(rel)

    for rel in list_recent_data_files(sandbox_id, limit=50):
        _consider(rel)
    # Also scan save_dir / root directly
    scan_bases = []
    if td:
        scan_bases.append(root / td)
    scan_bases.append(root)
    for base in scan_bases:
        if not base.is_dir():
            continue
        try:
            for p in base.iterdir():
                if p.is_file() and is_valid_deliverable_file(p):
                    _consider(str(p.relative_to(root)).replace("\\", "/"))
        except OSError:
            pass

    if not candidates:
        return None

    ver_m = _RE_VERSION_HINT.search(text)
    if ver_m:
        ver = ver_m.group(1)
        # Prefer *_v{n}.ext exact version token, not substring of v60
        pat = re.compile(rf"(?i)(?:^|[^0-9])v{re.escape(ver)}(?:[^0-9]|$)")
        matched = [r for r in candidates if pat.search(Path(r).name)]
        if matched:
            return _pick_newest_rel(sandbox_id, matched)
        return None

    if _RE_LATEST_HINT.search(text):
        return _pick_newest_rel(sandbox_id, candidates)
    return None


def _resolve_one_send_file(
    db: Session,
    agent: Agent,
    session_id: str,
    user_message: str,
    *,
    save_dir: str = "",
    workplace_files: list[str] | None = None,
) -> tuple[str | None, str]:
    """Pick exactly one file to push. Returns (rel_or_none, err_if_selected_empty)."""
    selected = _normalize_workplace_files(agent.sandbox_id, workplace_files)
    if selected:
        one = _pick_newest_rel(agent.sandbox_id or "", selected)
        return one, ""

    for name in _extract_deliverable_filenames(user_message):
        if not agent.sandbox_id:
            break
        found = _find_deliverable_by_name(
            agent.sandbox_id, name, target_dir=save_dir,
        )
        if found:
            return found, ""

    ver_m = _RE_VERSION_HINT.search(user_message or "")
    if agent.sandbox_id:
        hinted = _find_deliverable_by_hint(
            agent.sandbox_id, user_message, target_dir=save_dir,
        )
        if hinted:
            return hinted, ""
        if ver_m:
            v = ver_m.group(1)
            return None, (
                f"未找到 v{v} 的 Excel/CSV（文件名需含 `_v{v}` 或 `v{v}`）。"
                "请写出完整文件名，或在左侧勾选后发送。"
                "本次不会重新查询广告数据。"
            )

    if re.search(r"选中|勾选", user_message or ""):
        return None, (
            "未收到左侧勾选的 Excel/CSV。"
            "请先在工作目录勾选要发送的文件，再说「发送选中的到 tg」。"
            "本次不会重新查询广告数据。"
        )

    recent = _collect_recent_export_rels(db, agent, session_id, limit=8)
    root_first = [r for r in recent if _is_root_deliverable_rel(r)]
    pool = root_first or recent
    one = _pick_newest_rel(agent.sandbox_id or "", pool) if pool else None
    return one, ""


def format_im_completion_reply(
    reply: str,
    *,
    saved_paths: list[str] | None = None,
    sandbox_id: str = "",
) -> tuple[str, list[str]]:
    """Return the Agent's actual reply plus its newest valid channel attachment."""
    text = (reply or "").strip()
    paths = [
        p for p in (saved_paths or [])
        if _is_root_deliverable_rel(p)
    ]
    if sandbox_id and paths:
        kept: list[str] = []
        for p in paths:
            fp = download_path(sandbox_id, p)
            if fp and is_valid_deliverable_file(fp):
                kept.append(p)
        paths = kept
    one = _pick_newest_rel(sandbox_id, paths) if paths else None
    return text, [one] if one else []


def _infer_provider_from_message(user_message: str) -> str | None:
    text = (user_message or "").lower()
    for provider, aliases in _PROVIDER_MESSAGE_ALIASES.items():
        for a in aliases:
            if a.lower() in text:
                return provider
    return None


def _find_deliverable_by_name(
    sandbox_id: str,
    filename: str,
    *,
    target_dir: str = "",
) -> str | None:
    """Locate deliverable by basename under root/save_dir/task/_stale_*."""
    if not sandbox_id or not filename:
        return None
    root = ensure_workplace(sandbox_id)
    want = Path(filename).name
    want_l = want.lower()
    candidates: list[Path] = []
    td = (target_dir or "").strip().strip("/")
    search_roots = []
    if td:
        search_roots.append(root / td)
    search_roots.append(root)
    for base in search_roots:
        if not base.is_dir():
            continue
        direct = base / want
        if direct.is_file():
            return str(direct.relative_to(root)).replace("\\", "/")
    # Prefer newest match under workplace (includes task/_stale_*)
    try:
        for p in root.rglob(want):
            if p.is_file() and p.name.lower() == want_l and is_valid_deliverable_file(p):
                candidates.append(p)
    except OSError:
        pass
    if not candidates:
        # case-insensitive scan of recent files
        for rel in list_recent_data_files(sandbox_id, limit=40):
            if Path(rel).name.lower() == want_l:
                return rel
        return None
    candidates.sort(key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True)
    return str(candidates[0].relative_to(root)).replace("\\", "/")


def _collect_recent_export_rels(
    db: Session,
    agent: Agent,
    session_id: str,
    *,
    limit: int = 8,
) -> list[str]:
    """Prefer recent assistant saved_paths, then workplace newest data files."""
    ordered: list[str] = []
    seen: set[str] = set()

    def _add(rel: str):
        rel = (rel or "").strip().lstrip("/")
        if not rel or rel in seen or not _is_data_export_path(rel):
            return
        if not agent.sandbox_id or not download_path(agent.sandbox_id, rel):
            return
        seen.add(rel)
        ordered.append(rel)

    msgs = (
        db.query(ChatMessage)
        .filter(
            ChatMessage.agent_id == agent.id,
            ChatMessage.session_id == session_id,
            ChatMessage.role == "assistant",
        )
        .order_by(ChatMessage.id.desc())
        .limit(12)
        .all()
    )
    for m in msgs:
        if not m.meta:
            continue
        try:
            paths = list(json.loads(m.meta).get("saved_paths") or [])
        except Exception:
            paths = []
        for p in paths:
            _add(p)
            if len(ordered) >= limit:
                return ordered

    if agent.sandbox_id:
        for rel in list_recent_data_files(agent.sandbox_id, limit=limit):
            _add(rel)
            if len(ordered) >= limit:
                break
    return ordered


def _pick_channel_session(
    db: Session,
    agent: Agent,
    channels: list[ImChannel],
) -> tuple[ImChannel | None, str, str]:
    """Newest ImSession among channels → (channel, chat_id, err)."""
    best: tuple[ImChannel, ImSession] | None = None
    for ch in channels:
        sess = (
            db.query(ImSession)
            .filter(ImSession.channel_id == ch.id, ImSession.agent_id == agent.id)
            .order_by(ImSession.updated_at.desc(), ImSession.id.desc())
            .first()
        )
        if not sess or not sess.external_chat_id:
            continue
        if best is None or (sess.updated_at or "") > (best[1].updated_at or ""):
            best = (ch, sess)
    if not best:
        if not channels:
            return None, "", "未找到已启用且绑定本 Agent 的消息渠道。"
        return (
            channels[0],
            "",
            f"已配置 {channels[0].provider} 渠道，但还没有会话。"
            "请先在该渠道与机器人私聊一次后再试。",
        )
    return best[0], best[1].external_chat_id, ""


def _resolve_im_push_target(
    db: Session,
    agent: Agent,
    user_meta: dict | None = None,
    user_message: str = "",
) -> tuple[ImChannel | None, str, str]:
    """Return (channel, chat_id, error) for Agent-bound IM channel push."""
    meta = user_meta or {}
    src = str(meta.get("source") or "")
    if src.startswith("im:") and meta.get("chat_id"):
        ch_id = str(meta.get("channel_id") or "")
        channel = db.query(ImChannel).filter(ImChannel.id == ch_id).first() if ch_id else None
        if not channel:
            provider = src.split(":", 1)[-1] or ""
            q = db.query(ImChannel).filter(
                ImChannel.agent_id == agent.id,
                ImChannel.enabled.is_(True),
            )
            if provider:
                q = q.filter(ImChannel.provider == provider)
            channel = q.first()
        if channel:
            return channel, str(meta["chat_id"]), ""

    preferred = _infer_provider_from_message(user_message)
    q = db.query(ImChannel).filter(
        ImChannel.agent_id == agent.id,
        ImChannel.enabled.is_(True),
    )
    channels = q.all()
    if preferred:
        matched = [c for c in channels if c.provider == preferred]
        if matched:
            return _pick_channel_session(db, agent, matched)
        return (
            None,
            "",
            f"未找到已启用的 {preferred} 渠道（Agent 未绑定或未启用）。",
        )
    if not channels:
        return None, "", "未找到已启用且绑定本 Agent 的消息渠道。"
    # Prefer telegram when multiple (only provider with send_document today)
    tg = [c for c in channels if c.provider == "telegram"]
    return _pick_channel_session(db, agent, tg or channels)


def _resolve_telegram_target(
    db: Session,
    agent: Agent,
    user_meta: dict | None = None,
) -> tuple[ImChannel | None, str, str]:
    """Backward-compatible wrapper → telegram-preferred channel resolve."""
    return _resolve_im_push_target(db, agent, user_meta, "telegram")


async def _push_exports_to_channel(
    db: Session,
    channel: ImChannel,
    chat_id: str,
    sandbox_id: str,
    paths: list[str],
) -> tuple[list[str], list[str]]:
    """Push files via ChannelAdapter.send_document. Returns (sent_rels, errors)."""
    del db  # reserved for future event logging
    adapter = create_adapter(channel.provider, channel.id, channel.get_config())
    sent: list[str] = []
    errors: list[str] = []
    for rel in paths:
        fp = download_path(sandbox_id, rel)
        if not fp:
            errors.append(f"{rel}: 文件不存在")
            continue
        try:
            await adapter.send_document(chat_id, str(fp), caption=fp.name)
            sent.append(rel)
        except NotImplementedError:
            errors.append(
                f"{rel}: 渠道 `{channel.provider}` 暂不支持发送文件"
                "（当前仅 Telegram 完整支持；请绑定 Telegram 或后续扩展该渠道）"
            )
        except Exception as e:
            logger.warning(
                "push export to channel failed provider=%s path=%s: %s",
                channel.provider, rel, e,
            )
            errors.append(f"{rel}: {e}")
    return sent, errors


_push_exports_to_telegram = _push_exports_to_channel


async def _handle_send_existing_to_channel(
    db: Session,
    agent: Agent,
    session_id: str,
    user_meta: dict,
    user_message: str = "",
    *,
    save_dir: str = "",
    workplace_files: list[str] | None = None,
) -> str:
    """Fast-path: push exactly one xlsx/csv to Agent-bound IM channel."""
    one, select_err = _resolve_one_send_file(
        db,
        agent,
        session_id,
        user_message,
        save_dir=save_dir,
        workplace_files=workplace_files,
    )
    if select_err:
        return select_err

    channel, chat_id, err = _resolve_im_push_target(
        db, agent, user_meta, user_message,
    )
    if not one:
        return (
            "未找到可发送的 xlsx/csv。请先在左侧勾选文件、写出文件名/版本（如 v6），"
            "或完成报表导出（若刚被隔离，可查看 `task/_stale_*/`）。"
            "本次不会重新查询广告数据。"
        )
    if not channel or not chat_id:
        return err or "无法定位消息渠道会话，无法推送文件。"
    if not agent.sandbox_id:
        return "Agent 未绑定沙箱，无法读取工作目录文件。"

    provider = channel.provider or "channel"
    sent, errors = await _push_exports_to_channel(
        db, channel, chat_id, agent.sandbox_id, [one],
    )
    if sent:
        name = Path(sent[0]).name
        return f"已推送到 `{provider}`：`{name}`"
    if errors:
        return "推送失败：\n" + "\n".join(f"- {e}" for e in errors)
    return "未推送任何文件。"


async def _handle_send_existing_to_tg(
    db: Session,
    agent: Agent,
    session_id: str,
    user_meta: dict,
) -> str:
    """Backward-compatible alias."""
    return await _handle_send_existing_to_channel(
        db, agent, session_id, user_meta, "telegram",
    )


def _ensure_download_paths(final: str, saved_paths: list[str], sandbox: Sandbox | None = None) -> str:
    """Append download list for current-dir deliverables only (never task/ checkpoints)."""
    data_files = _current_dir_deliverables(saved_paths or [], sandbox)
    if not data_files:
        return final or ""
    text = final or ""
    missing = []
    for p in data_files:
        base = p.split("/")[-1]
        if base not in text and p not in text:
            missing.append(p)
    if not missing:
        return text
    lines = "\n".join(f"- `{p}`" for p in missing[:3])
    if "### 文件说明" in text or "### 下载文件" in text or "下载文件" in text:
        return f"{text.rstrip()}\n{lines}"
    return f"{text.rstrip()}\n\n### 文件说明\n{lines}"


_FINAL_ARTIFACT_REF_RE = re.compile(
    r"`(?P<code>[^`\n]+\.(?:xlsx|xlsm?|csv|sql|json|md))`|"
    r"\[[^\]\n]+\]\((?P<link>[^)\n]+\.(?:xlsx|xlsm?|csv|sql|json|md))\)",
    re.I,
)


def _reconcile_final_artifacts(
    final: str,
    saved_paths: list[str],
    sandbox: Sandbox | None,
    *,
    export_like: bool = False,
) -> tuple[str, list[str]]:
    """Make final artifact claims match files that the workplace can serve.

    This is an execution contract check only: it does not infer business
    meaning or choose resources. A path is publishable only when it exists in
    the current sandbox; export completion additionally requires a verified
    root data file.
    """
    if sandbox is None:
        normalized = [
            rel
            for rel in (_canonicalize_artifact_rel(p) for p in (saved_paths or []))
            if rel
        ]
        return final or "", list(dict.fromkeys(normalized))

    verified: list[str] = []
    for raw in saved_paths or []:
        rel = _canonicalize_artifact_rel(raw)
        if rel and download_path(sandbox.id, rel) and rel not in verified:
            verified.append(rel)

    text = final or ""
    missing_claims: list[str] = []
    for match in _FINAL_ARTIFACT_REF_RE.finditer(text):
        raw_ref = str(match.group("code") or match.group("link") or "").strip()
        if not raw_ref or raw_ref.startswith(("http://", "https://")):
            continue
        rel = _canonicalize_artifact_rel(raw_ref)
        if raw_ref != rel:
            text = text.replace(raw_ref, rel)
        if download_path(sandbox.id, rel):
            if rel not in verified:
                verified.append(rel)
        elif raw_ref not in missing_claims:
            missing_claims.append(raw_ref)

    if missing_claims:
        missing_set = set(missing_claims)
        kept_lines = [
            line for line in text.splitlines()
            if not any(path in line for path in missing_set)
        ]
        text = "\n".join(kept_lines).strip()

    root_data = _current_dir_deliverables(verified, sandbox)
    if export_like and not root_data:
        existing = [path for path in verified if not _is_data_export_path(path)]
        details = []
        if missing_claims:
            details.append(
                "工作目录中未找到模型声明的文件："
                + "、".join(f"`{path}`" for path in missing_claims[:5])
            )
        else:
            details.append("工作目录中没有通过格式与存在性校验的 xlsx/csv 文件")
        if existing:
            details.append(
                "已保留中间产物：" + "、".join(f"`{path}`" for path in existing[:5])
            )
        prefix = (
            "本轮未生成可下载的数据文件，不能视为导出完成。\n\n"
            "原因：" + "；".join(details) + "。"
        )
        text = prefix + (f"\n\n{text}" if text else "")
    elif missing_claims:
        text = (
            (text.rstrip() + "\n\n" if text else "")
            + "### 交付校验\n"
            + "- 未找到并已移除无效文件引用："
            + "、".join(f"`{path}`" for path in missing_claims[:5])
        )

    return text, verified


def _collect_mcp_view_names(
    mcp_results: list[dict] | None = None,
    fetched_view_pages: dict[str, int] | None = None,
) -> list[str]:
    """Unique MCP view names for FINAL footer (no source rows)."""
    views: list[str] = []
    seen: set[str] = set()
    for item in mcp_results or []:
        v = str(item.get("view") or "").strip()
        if v and v not in seen:
            seen.add(v)
            views.append(v)
    for v in sorted((fetched_view_pages or {}).keys()):
        v = str(v or "").strip()
        if v and v not in seen:
            seen.add(v)
            views.append(v)
    return views


def _append_mcp_markdown(
    final: str,
    mcp_results: list[dict],
    *,
    export_like: bool = False,
    fetched_view_pages: dict[str, int] | None = None,
) -> str:
    """Attach MCP footer; for generic queries also ensure a Markdown result table."""
    summary = (final or "").strip()
    # Non-export: if FINAL has no GFM table, inject latest query preview
    if (
        not export_like
        and summary
        and not _looks_like_tool_call(summary)
        and not re.search(r"\|\s*-{3,}\s*\|", summary)
        and "### 查询结果" not in summary
    ):
        for item in reversed(mcp_results or []):
            preview = str(item.get("preview_md") or "").strip()
            if preview:
                summary = f"{summary}\n\n### 查询结果\n\n{preview}"
                break
    if "### 数据来源" in summary:
        return summary
    views = _collect_mcp_view_names(mcp_results, fetched_view_pages)
    if not views and not mcp_results:
        return summary
    lines = ["### 数据来源（MCP 视图）", "| 视图名 |", "| --- |"]
    if views:
        for v in views:
            lines.append(f"| `{v}` |")
    else:
        # query without parseable view: list tool names only
        tools: list[str] = []
        seen_t: set[str] = set()
        for item in mcp_results or []:
            t = str(item.get("tool") or "").strip()
            if t and t not in seen_t:
                seen_t.add(t)
                tools.append(t)
        lines = ["### 数据来源（MCP）", "| 工具 |", "| --- |"]
        for t in tools:
            lines.append(f"| `{t}` |")
    footer = "\n".join(lines)
    if summary and not _looks_like_tool_call(summary):
        return f"{summary}\n\n{footer}"
    return footer


def _heuristic_rolling_line(
    stamp: str,
    final: str,
    saved_paths: list[str],
    gap_tag: str = "",
) -> str:
    cleaned = re.sub(r"\s+", " ", _clean_display_text(final or "")).strip()
    first = cleaned[:120] if cleaned else "完成一轮任务"
    if "。" in first:
        first = first.split("。")[0] + "。"
    files = ""
    if saved_paths:
        names = ", ".join(p.split("/")[-1] for p in saved_paths[:3])
        files = f" 文件：{names}。"
    return f"- [{stamp}] {first}{files}{gap_tag or ''}".strip()


async def _make_rolling_line(
    llm,
    db: Session,
    final: str,
    saved_paths: list[str],
    timeout: int | None = None,
    gap_tag: str = "",
) -> str:
    stamp = now_str()[:16]  # YYYY-MM-DD HH:MM
    if not llm:
        return _heuristic_rolling_line(stamp, final, saved_paths, gap_tag=gap_tag)
    paths_txt = ", ".join(saved_paths[:5]) if saved_paths else "无"
    prompt = (
        f"请根据本轮助手最终回复，写【恰好一行】滚动总结，格式严格为：\n"
        f"[{stamp}] 完成……（含关键数字若有）。核心表/要点……\n"
        f"不要换行，不超过 180 字，不要加前缀说明。\n\n"
        f"已保存文件：{paths_txt}\n\n助手回复：\n{(final or '')[:1500]}"
    )
    try:
        line = await chat_completion(
            llm,
            [{"role": "user", "content": prompt}],
            max_tokens=256,
            db=db,
            timeout=timeout,
        )
        line = re.sub(r"\s+", " ", (line or "").strip())
        if not line:
            return _heuristic_rolling_line(stamp, final, saved_paths, gap_tag=gap_tag)
        if not line.startswith("["):
            line = f"[{stamp}] {line}"
        if not line.startswith("- "):
            line = f"- {line}"
        if gap_tag and gap_tag.strip() not in line:
            line = f"{line.rstrip()}{gap_tag}"
        return line[:280]
    except Exception:
        logger.exception("rolling summary llm failed")
        return _heuristic_rolling_line(stamp, final, saved_paths, gap_tag=gap_tag)


def _summary_max_chars(agent: Agent) -> int:
    """Agent.summary_max_words is treated as a character budget (字 ≈ chars)."""
    try:
        n = int(getattr(agent, "summary_max_words", None) or 2000)
    except (TypeError, ValueError):
        n = 2000
    return max(50, min(n, 50000))


def _clamp_summary_text(text: str, max_chars: int) -> str:
    """Keep newest rolling lines; drop from head until within max_chars."""
    text = (text or "").strip()
    if not text or len(text) <= max_chars:
        return text
    entries = [ln.strip() for ln in text.splitlines() if ln.strip()]
    while entries and len("\n".join(entries)) > max_chars:
        entries.pop(0)
    joined = "\n".join(entries)
    if len(joined) > max_chars:
        # Single oversized line: keep the tail (newest content)
        joined = joined[-max_chars:]
    return joined


async def _append_rolling_summary(
    db: Session,
    agent: Agent,
    session_id: str,
    llm,
    final: str,
    saved_paths: list[str] | None = None,
    gap_tag: str = "",
) -> str:
    """Append one rolling summary entry to ChatSummary.content."""
    if not (final or "").strip() or final.strip() in ("[已停止]", "未配置 LLM"):
        s = db.query(ChatSummary).filter(
            ChatSummary.agent_id == agent.id, ChatSummary.session_id == session_id
        ).first()
        return (s.content if s else "") or ""
    if final.startswith("LLM 错误:"):
        s = db.query(ChatSummary).filter(
            ChatSummary.agent_id == agent.id, ChatSummary.session_id == session_id
        ).first()
        return (s.content if s else "") or ""

    llm_timeout = getattr(agent, "llm_timeout", None)
    entry = await _make_rolling_line(
        llm, db, final, saved_paths or [], timeout=llm_timeout, gap_tag=gap_tag or "",
    )
    s = db.query(ChatSummary).filter(
        ChatSummary.agent_id == agent.id, ChatSummary.session_id == session_id
    ).first()
    if not s:
        s = ChatSummary(agent_id=agent.id, session_id=session_id, content="")
        db.add(s)
    entries = [ln.strip() for ln in (s.content or "").splitlines() if ln.strip()]
    entries.append(entry)
    if len(entries) > _ROLLING_MAX_ENTRIES:
        entries = entries[-_ROLLING_MAX_ENTRIES:]
    s.content = _clamp_summary_text("\n".join(entries), _summary_max_chars(agent))
    db.commit()
    return s.content


def _format_mcp_tools_for_prompt(mcp_name: str, tools: list[dict]) -> list[str]:
    lines = [
        f"- mcp {mcp_name}: 必须使用下列真实工具名（禁止编造 search_notes 等），格式 MCP: <工具名> {{json args}}",
    ]
    names = {str(t.get("name")) for t in tools if isinstance(t, dict) and t.get("name")}
    if "describe_ads_view" in names or "query_ads_view" in names:
        lines.append(
            "  【硬规则】describe_ads_view / query_ads_view 禁止空参数 {}；"
            "未知视图名时先 list_ads_views；describe 用 view_name，query 用 view。"
            " SOP：list_ads_views → describe_ads_view(view_name) → query_ads_view(view)。"
        )
    for t in tools[:_MCP_TOOLS_PROMPT_LIMIT]:
        if not isinstance(t, dict) or not t.get("name"):
            continue
        name = str(t["name"])
        desc = re.sub(r"\s+", " ", str(t.get("description") or "")).strip()[:80]
        req = _tool_required_fields(t)
        req_bit = f" required=[{', '.join(req)}]" if req else ""
        example = _MCP_TOOL_EXAMPLES.get(name)
        if example:
            lines.append(f"  - {example}{('  # ' + desc) if desc else ''}{req_bit}")
        elif desc:
            lines.append(f"  - MCP: {name} {{...}}  # {desc}{req_bit}")
        else:
            lines.append(f"  - MCP: {name} {{...}}{req_bit}")
    if len(tools) > _MCP_TOOLS_PROMPT_LIMIT:
        lines.append(f"  - …共 {len(tools)} 个工具，其余名称以服务端 tools/list 为准")
    return lines


def _clean_display_text(text: str) -> str:
    text = _strip_llm_artifacts(text or "")
    text = re.sub(r"(?<![A-Za-z/])(SHELL:|READ:|PATCH:|THINK:|MCP:|HTTPMCP:)\s*[^\n]+", "", text)
    text = re.sub(r"(?<![A-Za-z/])WRITE:\s*\S[^\n]*(?:\n(?!(?:SHELL:|WRITE:|READ:|PATCH:|FINAL:|THINK:|MCP:|HTTPMCP:))[^\n]*)*", "", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _clean_final_answer(text: str) -> str:
    """User-facing assistant content: strip protocol markers and leaked model monologue."""
    cleaned = _clean_display_text(extract_final_payload(text or ""))
    cleaned = re.sub(r"(?im)^\s*FINAL\s*[:：]\s*", "", cleaned).strip()
    # Strip leaked English/tool-budget thinking that should never reach the user
    cleaned = re.sub(r"</?think>", "", cleaned, flags=re.I)
    cleaned = re.sub(
        r"(?is)(?:^|\n)\s*(?:"
        r"Let me write(?:\s+it)?(?:\s+now)?\.?"
        r"|I haven'?t yet[^\n]*"
        r"|the platform seems to think[^\n]*"
        r"|tool budget[^\n]*"
        r"|But the platform[^\n]*"
        r"|Progress check table[^\n]*"
        r"|Key finding:[^\n]*"
        r"|Validation evidence[^\n]*"
        r"|Let me write a single-line FINAL[^\n]*"
        r")[^\n]*",
        "",
        cleaned,
    )
    cleaned = re.sub(r"(?is)<结论>\.?\s*", "", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def _clean_step_display_text(text: str) -> str:
    """Execution-step safe text: strip protocol/meta reasoning before UI preview."""
    raw = str(text or "").strip()
    if not raw:
        return ""
    if re.search(r"(?im)^\s*FINAL\s*[:：]\s*", raw):
        cleaned = _clean_final_answer(raw)
    else:
        cleaned = _clean_display_text(raw)
        cleaned = re.sub(
            r"(?im)^\s*(?:actually|wait|looking at|let me|so i need|"
            r"the previous turn|i think|i notice)\b.*$",
            "",
            cleaned,
        )
        if re.search(r"(?im)^\s*FINAL\s*[:：]\s*", cleaned):
            cleaned = _clean_final_answer(cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def _looks_like_tool_call(text: str) -> bool:
    return bool(re.search(
        r"(?<![A-Za-z/])(SHELL:|WRITE:|READ:|PATCH:|THINK:|MCP:|HTTPMCP:|SKILL_MD:)\s*\S",
        text or "",
    ))


def _narrates_tool_without_call(text: str) -> bool:
    """Model talks about using tools but did not emit executable READ:/SHELL:/… syntax."""
    if _looks_like_tool_call(text):
        return False
    return bool(re.search(
        r"(读取|查看|打开).{0,12}(文件|配置|目录)|用\s*READ|使用\s*READ|READ\s*工具|"
        r"(先|再)?(用)?\s*(SHELL|WRITE|FINAL)\s*(工具|命令|指令)?|"
        r"让我先(读|看|查)",
        text or "",
        re.I,
    ))


# Consecutive ReAct turns with no tool progress before aborting
_NO_PROGRESS_LIMIT = 5


def _is_saveable_content(content: str, user_message: str) -> bool:
    if not content or _looks_like_tool_call(content):
        return False
    cleaned = content.strip()
    if len(cleaned) < 80:
        return False
    if _wants_workplace_save(user_message):
        if re.search(r"^#+\s|^\s*[-*]\s|^\s*\d+\.\s", cleaned, re.MULTILINE):
            return True
        return len(cleaned) >= 300
    return True


def _load_saved_content(sandbox: Sandbox | None, paths: list[str]) -> str:
    if not sandbox or not paths:
        return ""
    for rel in reversed(paths):
        text = _read_workplace(sandbox, rel.lstrip("/"))
        if text and not text.startswith("文件不存在"):
            return text
    return ""


_CLARIFY_ASK_RE = re.compile(
    r"(?:"
    r"[？?]|吗[？?]?|哪一[天日]|哪天|哪个日期|什么时候|何时|"
    r"时间窗|日期范围|请补充|请确认|请问|需要确认|"
    r"要查哪|想查哪|查哪一天|查哪段"
    r")",
    re.I,
)


def _is_clarify_progress_reply(reply: str) -> bool:
    """True when the model is asking the user a clarifying question (counts as progress)."""
    text = (reply or "").strip()
    if not text:
        return False
    # Prefer FINAL body if present
    m = re.search(r"(?im)^\s*FINAL:\s*(.+)$", text)
    body = (m.group(1).strip() if m else text)
    body = re.sub(r"(?im)^\s*(PLAN|MCP|SHELL|READ|WRITE|HTTPMCP):.*$", "", body).strip()
    if not body or len(body) > 400:
        return False
    if _CLARIFY_ASK_RE.search(body):
        return True
    # Short question-only lines
    if body.endswith(("?", "？")) and len(body) <= 120:
        return True
    return False


def _step_preview(reply: str) -> str:
    steps = extract_tool_steps(reply)
    if steps:
        parts: list[str] = []
        for step in steps:
            if step.is_final or step.action == "done":
                ans = step.reply.replace("FINAL:", "", 1).strip()
                if ans:
                    cleaned_ans = _clean_step_display_text(ans)
                    if cleaned_ans:
                        parts.append(cleaned_ans[:200])
            elif step.action == "shell":
                continue
            elif step.action in ("mcp_tool_call", "httpmcp_call"):
                continue
            elif step.action == "file_write":
                path = step.reply[6:].split("\n", 1)[0].strip() if step.reply.startswith("WRITE:") else ""
                display = path.split("/")[-1] if path else ""
                parts.append(f"写入文件: {display}" if display else "写入文件")
            elif step.action == "file_read":
                path = step.reply[5:].strip() if step.reply.startswith("READ:") else ""
                display = path.split("/")[-1] if path else ""
                parts.append(f"读取文件: {display}" if display else "读取文件")
        if parts:
            return "\n".join(parts)
    cleaned = _clean_step_display_text(reply)
    return cleaned[:300] if cleaned else "推理中..."


def _ensure_sandbox_running(db: Session, sandbox: Sandbox | None) -> bool:
    if not sandbox:
        return False
    if sandbox.container_id:
        status = docker_service.sync_container_status(sandbox.container_id)
        if status == "running":
            return True
    cid, status, err = docker_service.start_sandbox(sandbox)
    if cid:
        sandbox.container_id = cid
        sandbox.status = status or "running"
        db.commit()
        return True
    return False


def _wants_workplace_save(user_message: str) -> bool:
    return any(k in user_message for k in ("保存", "写入", "工作目录", "存到", "存储", "梳理", "清单"))


def _autosave_path(user_message: str, save_dir: str = "") -> str:
    if any(k in user_message for k in ("清单", "规范", "checklist")):
        name = "dba_daily_checklist.md"
    else:
        name = "output.md"
    if save_dir:
        return f"{save_dir.strip('/')}/{name}"
    return name


def _user_wants_task_dir(user_message: str) -> bool:
    return "task/" in user_message or "task目录" in user_message


def _is_temp_setup_preview(preview: str) -> bool:
    if not preview:
        return False
    if re.search(r"写入文件|#+\s|^\s*[-*]\s", preview, re.MULTILINE):
        return False
    return bool(re.search(r"task|mkdir|工作目录|创建.*(文件夹|目录)", preview, re.I))


def _ensure_visible_run_steps(
    steps: list[dict] | None,
    *,
    export_like: bool = False,
    has_mcp: bool = False,
) -> list[dict]:
    """Guarantee ≥1 visible step for UI exec-card (never hard-blocks FINAL)."""
    visible = list(steps or [])
    if visible:
        return visible
    if export_like and has_mcp:
        title = "本轮未调用 MCP（请用 list_ads_views / query_ads_view 拉数，勿仅写 clickhouse SQL）"
        action = "no_tools_export"
    elif export_like:
        title = "本轮未调用工具（导出未绑定 MCP 时无法远程拉数）"
        action = "no_tools_export"
    else:
        title = "本轮未调用 MCP/Shell"
        action = "no_tools"
    return [{
        "type": "info",
        "action": action,
        "title": title,
        "status": "done",
    }]


def _sanitize_steps(steps: list[dict]) -> list[dict]:
    visible: list[dict] = []
    for step in steps:
        if step.get("hidden"):
            continue
        cleaned = {
            "type": step.get("type"),
            "action": step.get("action"),
            "title": step.get("title"),
            "status": step.get("status"),
            "iteration": step.get("iteration"),
        }
        if step.get("hidden"):
            cleaned["hidden"] = True
        if cleaned.get("type") == "llm" and step.get("preview"):
            preview = re.sub(r"(?<![A-Za-z/])SHELL:\s*[^\n]+", "", step["preview"]).strip()
            preview = re.sub(r"(?<![A-Za-z/])MCP:\s*[^\n]+", "", preview).strip()
            preview = re.sub(r"(?<![A-Za-z/])HTTPMCP:\s*[^\n]+", "", preview).strip()
            preview = re.sub(r"执行命令:\s*[^\n]*", "", preview).strip()
            if _is_temp_setup_preview(preview):
                cleaned["preview"] = None
            else:
                cleaned["preview"] = (preview[:300] if preview else None)
        if cleaned.get("action") in _INTERNAL_TOOL_ACTIONS and cleaned.get("status") == "done":
            cleaned.pop("content", None)
            cleaned.pop("preview", None)
        if cleaned.get("type") == "tool" and cleaned.get("action") == "file_write":
            content = step.get("content") or ""
            m = re.search(r"已写入\s+(\S+)", content)
            if m:
                cleaned["content"] = f"已写入 {m.group(1).split('/')[-1]}"
        elif cleaned.get("type") == "tool" and cleaned.get("status") == "error":
            content = step.get("content") or ""
            if content:
                cleaned["content"] = str(content)[:400]
        if (
            cleaned.get("type") == "llm"
            and not cleaned.get("preview")
            and visible
            and visible[-1].get("type") == "llm"
            and not visible[-1].get("preview")
        ):
            visible[-1]["iteration"] = cleaned.get("iteration")
            visible[-1]["title"] = cleaned.get("title")
            visible[-1]["status"] = cleaned.get("status")
            continue
        visible.append(cleaned)
    return visible


def _normalize_final_paths(text: str, saved_paths: list[str]) -> str:
    if not text or not saved_paths:
        return text
    for p in saved_paths:
        base = p.split("/")[-1]
        if not p.startswith("task/"):
            text = text.replace(p, base)
    return text


def _canonicalize_artifact_rel(path: str) -> str:
    rel = str(path or "").strip().lstrip("/")
    if rel.startswith("workplace/"):
        rel = rel[len("workplace/") :]
    return rel.strip()


_LIGHT_AGENT_INTERACTION_RE = re.compile(
    r"^\s*(?:"
    r"你好|您好|hello|hi|在吗|"
    r"你是谁|你是干嘛的|介绍一下(?:你自己)?|你的身份|"
    r"你能做什么|你会做什么|你可以做什么|你都可以做什么|"
    r"有什么能力|有什么用|怎么用你|"
    r"帮我做什么|"
    r"(?:你)?可以帮我做(?:哪些|什么)(?:事情|事)?|"
    r"能帮我做(?:什么|哪些)(?:事情|事)?"
    r")[。！？!?,.，\s]*$",
    re.I,
)

_GENERIC_PLAN_GATE_INTENT_RE = re.compile(
    r"(?:"
    # Export / deliverable
    r"导出|报表|xlsx|excel|明细|落盘|"
    # MCP / ads tooling
    r"list_\w+|describe_\w+|query_ads|"
    r"\bmcp\b|SHELL:|WRITE:|READ:|PATCH:|"
    # Workplace / files
    r"读(?:一下|取)?\s*(?:文件|目录|workplace|配置)|"
    r"查看(?:一下)?\s*(?:文件|目录|workplace|配置)|"
    r"写(?:入|一个)?\s*(?:文件|脚本)|"
    r"\bworkplace\b|"
    r"运行|执行脚本|调用工具|"
    r"分析(?:一下)?(?:这份|这个)?文件|"
    # SQL / query
    r"\bsql\b|写\s*sql|sql\s*语句|查询|查一下|查数|"
    r"\bselect\b|\bclickhouse\b|"
    # dbt / modeling
    r"\bdbt\b|建模|\bmodels?\b|\bmart\b|"
    r"\bstg_|\bdim_|\bfct_|"
    # Generic execution / codegen
    r"写一份|生成脚本|改造|跑一下|执行|创建|修改代码|"
    # Ops / logs
    r"系统日志|日志分析|分析.*日志|查看.*日志|Traceback|"
    r"优化建议|报错分析|运维诊断|"
    # Skill triggers
    r"按\s*skill|按技能|用技能|用\s*skill"
    r")",
    re.I,
)


_TASK_FOLLOWUP_RE = re.compile(
    r"(?:"
    r"真跑|重试|再跑|再查|继续|接着|再试|"
    r"跑一次|查一次|执行一次|按刚才|同上|"
    r"再来一次|再执行|再查询|再试一次|"
    r"重新处理|再处理|继续处理|继续工作|排查|跟进|查原因"
    r")",
    re.I,
)

# Smalltalk that must stay conversational even after a prior tool turn
_CASUAL_CHAT_RE = re.compile(
    r"^\s*(?:"
    r"今天怎么样|怎么样啊?|在忙吗|忙什么|"
    r"谢谢|多谢|感谢|"
    r"你最擅长(?:做什么)?|擅长什么|"
    r"(?:你的|dba的)?职责是什么"
    r")[。！？!?,.，\s]*$",
    re.I,
)

def _is_light_agent_interaction(user_message: str) -> bool:
    """Greeting / identity turns should stay conversational, not enter PLAN/tool flow."""
    text = (user_message or "").strip()
    if not text or len(text) > 80:
        return False
    if "\n" in text:
        return False
    return bool(_LIGHT_AGENT_INTERACTION_RE.search(text))


def _is_casual_chat_message(user_message: str) -> bool:
    """Pure smalltalk / capability Q&A — never inherit tool ring from session context."""
    text = (user_message or "").strip()
    if not text or len(text) > 40:
        return False
    if "\n" in text:
        return False
    if _is_light_agent_interaction(text):
        return True
    return bool(_CASUAL_CHAT_RE.search(text))


def _session_recently_used_tools(
    db: Session | None,
    agent_id: str,
    session_id: str,
    *,
    lookback: int = 2,
) -> bool:
    """True if a recent assistant turn in this session ran tools (not conversational)."""
    if db is None or not (agent_id or "").strip() or not (session_id or "").strip():
        return False
    try:
        rows = (
            db.query(ChatMessage)
            .filter(
                ChatMessage.agent_id == agent_id,
                ChatMessage.session_id == session_id,
                ChatMessage.role == "assistant",
            )
            .order_by(ChatMessage.id.desc())
            .limit(max(1, int(lookback)))
            .all()
        )
    except Exception:
        return False
    tool_actions = {
        "mcp_tool_call",
        "mcp_loaded",
        "skill_loaded",
        "shell",
        "file_write",
        "file_read",
        "httpmcp_call",
        "rag_query",
    }
    for row in rows:
        try:
            meta = json.loads(row.meta or "{}")
        except Exception:
            continue
        if not isinstance(meta, dict):
            continue
        if meta.get("conversational_reply"):
            continue
        for step in meta.get("steps") or []:
            if not isinstance(step, dict):
                continue
            if str(step.get("action") or "") in tool_actions:
                return True
    return False


def _is_task_followup_message(user_message: str) -> bool:
    text = (user_message or "").strip()
    if not text or len(text) > 80:
        return False
    if "\n" in text:
        return False
    if _is_light_agent_interaction(text):
        return False
    return bool(_TASK_FOLLOWUP_RE.search(text))


def _needs_generic_plan_gate(
    user_message: str,
    allowed_actions: list | None = None,
    *,
    db: Session | None = None,
    agent_id: str = "",
    session_id: str = "",
) -> bool:
    """True when non-export turn likely needs PLAN + tools (not pure chat/Q&A)."""
    del allowed_actions  # reserved for future action-aware routing
    text = (user_message or "").strip()
    if not text:
        return False
    if _is_casual_chat_message(text) or _is_light_agent_interaction(text):
        return False
    if _GENERIC_PLAN_GATE_INTENT_RE.search(text):
        return True
    # Follow-ups like「真跑一次 / 重试一次」must keep the tool ring
    if _is_task_followup_message(text):
        return True
    # Short follow-up after a toolful turn in this session (not new smalltalk)
    if (
        len(text) <= 40
        and "\n" not in text
        and _session_recently_used_tools(db, agent_id, session_id)
    ):
        return True
    # Numbered multi-step asks or explicit tool/view names
    if re.search(r"(?:^|\n)\s*\d+[\.、]\s*\S", text) and len(text) > 40:
        return True
    if re.search(r"view_result_\w+", text, re.I):
        return True
    return False


def _agent_identity_label(agent: Agent | None) -> str:
    if not agent:
        return "当前 Agent"
    name = str(getattr(agent, "name", "") or "").strip()
    return f"Agent「{name}」" if name else "当前 Agent"


def _build_light_agent_reply(agent: Agent | None, user_message: str) -> str:
    """Short user-facing reply; never exposes the raw base prompt."""
    label = _agent_identity_label(agent)
    desc = str(getattr(agent, "description", "") or "").strip()
    text = (user_message or "").strip().lower()
    if desc:
        identity = f"我是 {label}，{desc.rstrip('。.!！') }。"
    else:
        identity = f"我是 {label}。"
    if re.search(
        r"你是谁|身份|介绍|做什么|能力|干嘛|有什么用|怎么用|帮我做",
        text,
        re.I,
    ):
        return (
            f"{identity}\n"
            "我可以帮你理解需求、回答问题，并在需要时协调已绑定的技能与工具完成任务。"
            "直接告诉我你想做什么就行。"
        )
    return (
        f"你好，{identity}\n"
        "直接告诉我你的问题或想完成的事就可以。"
    )


def _build_model_understanding_system(
    agent: Agent | None,
    task_policy: TaskPolicy,
) -> str:
    """Stable model-side contract: understand first, engine executes contracts."""
    label = _agent_identity_label(agent)
    desc = str(getattr(agent, "description", "") or "").strip()
    lines = [
        "【Agent 身份与需求理解协议】",
        f"- 当前身份：{label}" + (f"；简介：{desc[:300]}" if desc else "。"),
        "- Agent 基础提示词已作为最前 system 生效；回复要体现身份和职责，但禁止复述或引用基础提示词原文。",
        "- 每轮先判断用户真实意图：寒暄/问答/澄清/文件处理/工具操作/数据导出/导出修复。",
        "- 不要把所有对话都套成数据导出；非导出意图应自然回应、必要时提出一个澄清问题，只有需要执行时才进入通用 PLAN。",
        "- 对导出任务，模型只负责理解需求并形成契约化输入：任务类型、时间窗、人群口径、输出列、歧义和验收标准；SQL、分页、落盘、验证和修复由引擎执行。",
        "- 若用户需求不完整，优先询问缺失约束；不要用散文 PLAN 替代 TaskSpec/ColumnPlan/Verifier。",
    ]
    if task_policy.export_like:
        lines.append("- 当前路由：导出候选；请特别保留用户原始列名、时间描述和口径差异。")
    else:
        lines.append("- 当前路由：通用交互；除非用户明确要求导出报表，不要提导出状态机或查询图。")
    return "\n".join(lines)


_CONVERSATIONAL_HISTORY_MAX = 6


def _build_conversational_system(agent: Agent | None) -> str:
    """System prompt for tool-free single-turn chat (no PLAN/MCP/FINAL protocol)."""
    label = _agent_identity_label(agent)
    desc = str(getattr(agent, "description", "") or "").strip()
    base = str(getattr(agent, "prompt", None) or "You are a helpful assistant.").strip()
    return "\n".join([
        base,
        "",
        "【对话模式】",
        f"- 当前身份：{label}" + (f"；简介：{desc[:300]}" if desc else "。"),
        "- 用自然语言直接回答；体现身份与职责，禁止复述或引用基础提示词原文。",
        "- 禁止输出 PLAN: / MCP: / SHELL: / WRITE: / READ: / FINAL: 等工具协议行。",
        "- 不要提导出状态机、查询图、列计划或完成标准；用户未要求执行任务时不要调用或描述工具。",
        "- 不要编造已导出文件、查询结果或任务进度。",
    ])


def _trim_conversational_history(
    history: list,
    *,
    max_msgs: int = _CONVERSATIONAL_HISTORY_MAX,
) -> list[dict]:
    """Recent short turns only; blunt large export/tool dumps that derail chat."""
    out: list[dict] = []
    for h in list(history or [])[-max_msgs:]:
        if isinstance(h, dict):
            role = str(h.get("role") or "")
            content = str(h.get("content") or "")
        else:
            role = str(getattr(h, "role", "") or "")
            content = str(getattr(h, "content", "") or "")
        if role not in ("user", "assistant"):
            continue
        if role == "assistant" and len(content) > 200 and (
            "FINAL:" in content
            or "【导出" in content
            or "list_ads_views" in content
            or "query_ads_view" in content
            or "已落盘" in content
            or "原始回退" in content
        ):
            content = "（上一轮为工具/导出执行，细节已省略）"
        elif len(content) > 800:
            content = content[:700] + "\n…(已截断)"
        out.append({"role": role, "content": content})
    return out


def _build_conversational_messages(
    agent: Agent | None,
    history: list,
    effective_message: str,
    note_content: str = "",
) -> list[dict]:
    """Messages for conversational path: no tools / skills / export coaches."""
    msgs: list[dict] = [{"role": "system", "content": _build_conversational_system(agent)}]
    if (note_content or "").strip():
        msgs.append({
            "role": "system",
            "content": (
                "【会话备注·约束】以下备注用于补充默认口径，不是本轮用户消息；"
                "用户显式要求优先：\n" + note_content.strip()[:3000]
            ),
        })
    trimmed = _trim_conversational_history(history)
    # Drop trailing user (just committed); we append effective_message once
    if trimmed and trimmed[-1].get("role") == "user":
        trimmed = trimmed[:-1]
    msgs.extend(trimmed)
    msgs.append({"role": "user", "content": (effective_message or "").strip() or "你好"})
    return msgs


def _summary_history_messages(history: list, user_message: str = "") -> list[dict]:
    records: list[dict] = []
    for row in history or []:
        if isinstance(row, dict):
            role = str(row.get("role") or "")
            content = str(row.get("content") or "")
            created_at = str(row.get("created_at") or "").strip()
            meta_raw = row.get("meta")
        else:
            role = str(getattr(row, "role", "") or "")
            content = str(getattr(row, "content", "") or "")
            created_at = str(getattr(row, "created_at", "") or "").strip()
            meta_raw = getattr(row, "meta", None)
        if role in ("user", "assistant") and content.strip():
            records.append({
                "role": role,
                "content": content.strip(),
                "created_at": created_at,
                "meta": meta_raw,
            })
    if (
        records
        and records[-1].get("role") == "user"
        and records[-1].get("content", "").strip() == (user_message or "").strip()
    ):
        records.pop()

    messages: list[dict] = []
    for record in records:
        try:
            meta = (
                json.loads(record.get("meta") or "{}")
                if isinstance(record.get("meta"), str)
                else (record.get("meta") or {})
            )
        except Exception:
            meta = {}
        if not isinstance(meta, dict):
            meta = {}
        provenance: list[str] = []
        if record.get("created_at"):
            provenance.append(f"时间={record['created_at']}")
        if meta.get("source"):
            provenance.append(f"来源={meta['source']}")
        sender = (
            meta.get("sender_username")
            or meta.get("sender_display_name")
            or meta.get("sender_name")
            or meta.get("username")
            or meta.get("user_id")
        )
        if sender:
            username = str(meta.get("sender_username") or "").strip().lstrip("@")
            display_name = str(meta.get("sender_display_name") or "").strip()
            identity = f"@{username}" if username else str(sender)
            if display_name and display_name != identity:
                identity += f"（{display_name}）"
            provenance.append(f"发送者={identity}")
        evidence: list[str] = []
        if meta.get("task_title"):
            evidence.append(f"任务标题={meta['task_title']}")
        saved_paths = [str(path) for path in (meta.get("saved_paths") or []) if str(path)]
        if saved_paths:
            evidence.append("交付文件=" + "、".join(saved_paths[:12]))
        verification = meta.get("verification")
        if isinstance(verification, dict) and verification.get("status"):
            evidence.append(f"验证状态={verification['status']}")
        prefix = f"【{'；'.join(provenance)}】\n" if provenance else ""
        suffix = f"\n【运行证据：{'；'.join(evidence)}】" if evidence else ""
        messages.append({
            "role": record["role"],
            "content": prefix + record["content"] + suffix,
        })
    return messages


def _collect_history_saved_paths(history: list) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for row in history or []:
        meta_raw = row.get("meta") if isinstance(row, dict) else getattr(row, "meta", None)
        if not meta_raw:
            continue
        try:
            meta = json.loads(meta_raw or "{}")
        except Exception:
            continue
        for p in meta.get("saved_paths") or []:
            rel = str(p or "").strip()
            if rel and rel not in seen:
                seen.add(rel)
                out.append(rel)
    return out


def _load_current_session_history(
    db: Session,
    agent_id: str,
    session_id: str,
    fallback: list | None = None,
) -> list:
    """Load authoritative messages for exactly one Agent session."""
    try:
        return (
            db.query(ChatMessage)
            .filter(
                ChatMessage.agent_id == agent_id,
                ChatMessage.session_id == session_id,
            )
            .order_by(ChatMessage.id.asc())
            .all()
        )
    except Exception:
        return list(fallback or [])


def _build_session_summary_fallback(
    *,
    history: list,
    user_message: str,
    note_content: str = "",
) -> str:
    msgs = _summary_history_messages(history, user_message)
    prior_users = [
        str(m.get("content") or "").strip()
        for m in msgs
        if m.get("role") == "user" and str(m.get("content") or "").strip()
    ]
    prior_assistants = [
        str(m.get("content") or "").strip()
        for m in msgs
        if m.get("role") == "assistant" and str(m.get("content") or "").strip()
    ]
    if not prior_users and not prior_assistants:
        return (
            "### 会话概览\n"
            "- 当前会话暂无可总结的业务内容。\n"
            "- 目前只看到了这条“总结会话内容”的请求，前面还没有形成任务过程、结论或交付文件。"
        )

    saved_paths = _collect_history_saved_paths(history)
    files_line = "、".join(f"`{p}`" for p in saved_paths[:5]) if saved_paths else "暂无明确交付文件记录"
    user_lines = "\n".join(
        f"{index}. {content[:240]}{'…' if len(content) > 240 else ''}"
        for index, content in enumerate(prior_users, start=1)
    )
    note_line = note_content.strip()[:160] if note_content.strip() else "无"
    return (
        "## 聊天记录总结\n\n"
        "### 会话概览\n"
        f"- 已记录用户消息：{len(prior_users)} 条\n"
        f"- 已记录 Agent 回复：{len(prior_assistants)} 条\n"
        f"- 交付产物：{files_line}\n"
        f"- 会话备注：{note_line}\n\n"
        "### 用户请求时间线\n"
        f"{user_lines}\n\n"
        "### 生成状态\n"
        "- LLM 总结调用失败，以上仅列出当前会话中的真实消息与产物，未推断任务结论。"
    )


def _build_session_summary_messages(
    *,
    agent: Agent | None,
    history: list,
    user_message: str,
    note_content: str = "",
) -> list[dict]:
    msgs: list[dict] = [{
        "role": "system",
        "content": (
            _build_conversational_system(agent)
            + "\n\n你现在的唯一任务是总结当前会话中用户与 Agent 的真实交互。"
            "不要自我介绍，不要索要新需求，不要转成工具协议。"
            "证据范围仅限随后提供的同一 session 消息和会话备注；禁止引用滚动摘要、其他 session、"
            "模型常识或未出现在证据中的表名、SQL、数字、人员、执行结果。\n"
            "总结应像项目复盘而不是普通问答，并根据证据丰富度自适应组织以下内容：\n"
            "1. 标题与截至日期；若消息元数据能识别参与人员，列出参与人员。\n"
            "2. 按时间顺序列出任务：需求条件、输出字段、结果数字、统计口径、交付文件和当前状态。\n"
            "3. 对发生返工的任务，比较各版本的逻辑/数字/问题/修正结果，明确根因和最终采用口径。\n"
            "4. 汇总经过验证的技术口径与经验沉淀。\n"
            "5. 单列待办、未验证项和仍有争议的内容。\n"
            "6. 用户质疑后若 Agent 只承诺但没有执行证据，必须写成‘尚未完成’，不得写成已完成。\n"
            "用 Markdown 标题、表格和清单清晰呈现；相邻消息属于同一任务时合并，"
            "但不得把错误版本与修正版混成一个结论。"
            "如果当前会话除本句外没有实质内容，就明确写“当前会话暂无可总结内容”，并说明原因。"
        ),
    }]
    if note_content.strip():
        msgs.append({
            "role": "system",
            "content": "【会话备注】\n" + note_content.strip()[:3000],
        })
    msgs.extend(_summary_history_messages(history, user_message))
    msgs.append({
        "role": "user",
        "content": "请基于以上真实会话内容，输出一版结构化会话总结。",
    })
    return msgs


async def _run_conversational_turn(
    *,
    db: Session,
    agent: Agent,
    session_id: str,
    user_message: str,
    effective_message: str,
    note_content: str = "",
    llm: LLMResource | None,
    history: list,
    key: str,
    user_meta: dict,
    turn_intent: TurnIntent | None = None,
) -> str:
    """Single-turn LLM reply without tools; light canned reply on LLM failure."""
    used_fallback = False
    is_summary_request = bool(
        turn_intent is not None and turn_intent.wants_session_summary
    )
    summary_history = (
        _load_current_session_history(db, agent.id, session_id, history)
        if is_summary_request
        else history
    )
    final = ""
    try:
        if not llm:
            raise RuntimeError("未配置 LLM")
        if is_summary_request:
            messages = _build_session_summary_messages(
                agent=agent,
                history=summary_history,
                user_message=user_message,
                note_content=note_content,
            )
        else:
            messages = _build_conversational_messages(
                agent, history, effective_message, note_content,
            )
        # Guard: conversational path must never include tool protocol coaches
        for m in messages:
            c = str(m.get("content") or "")
            if m.get("role") == "system" and (
                "可用工具:" in c or "【通用·规划闸门】" in c or "【导出策略" in c
            ):
                raise RuntimeError("conversational messages leaked tool protocol")
        reply = await chat_completion(
            llm,
            messages,
            max_tokens=4096 if is_summary_request else 1024,
            db=db,
            timeout=getattr(agent, "llm_timeout", None),
        )
        final = _clean_final_answer(reply or "")
        if not final.strip():
            raise RuntimeError("empty conversational reply")
        if _looks_like_tool_call(final) or re.search(
            r"(?im)^\s*(PLAN|FINAL)\s*[:：]",
            final,
        ):
            final = _clean_display_text(
                re.sub(r"(?im)^\s*(PLAN|FINAL)\s*[:：]\s*", "", final)
            )
        if not final.strip():
            raise RuntimeError("protocol-only conversational reply")
    except Exception:
        logger.exception(
            "conversational turn failed agent=%s session=%s; using light fallback",
            agent.id,
            session_id,
        )
        if is_summary_request:
            final = _build_session_summary_fallback(
                history=summary_history,
                user_message=user_message,
                note_content=note_content,
            )
        else:
            final = _build_light_agent_reply(agent, user_message)
        used_fallback = True

    step = {
        "type": "info",
        "action": (
            "session_summary_reply"
            if is_summary_request
            else ("conversational_reply" if not used_fallback else "agent_identity_interaction")
        ),
        "title": (
            "会话总结"
            if is_summary_request
            else ("对话回复" if not used_fallback else "Agent 身份回应")
        ),
        "status": "done",
        "content": (final or "")[:500],
    }
    await hub.publish(key, {"type": "step", "op": "append", "index": 0, "step": step})
    meta = json.dumps({
        "steps": [step],
        "step_count": 1,
        "saved_paths": [],
        "conversational_reply": not used_fallback,
        "light_agent_interaction": (
            False if is_summary_request else (used_fallback or _is_light_agent_interaction(user_message))
        ),
        "session_summary_reply": is_summary_request,
        **{
            key: user_meta[key]
            for key in (
                "source", "channel_id", "chat_id", "chat_type", "user_id",
                "sender_username", "sender_display_name",
            )
            if user_meta.get(key)
        },
    }, ensure_ascii=False)
    db.add(ChatMessage(
        agent_id=agent.id,
        session_id=session_id,
        role="assistant",
        content=final,
        meta=meta,
        created_at=now_str(),
    ))
    db.commit()
    if not is_summary_request:
        try:
            await _append_rolling_summary(db, agent, session_id, llm, final, [])
        except Exception:
            logger.exception("rolling summary failed agent=%s session=%s", agent.id, session_id)
    await hub.publish(key, {
        "type": "done",
        "content": (final or "")[:500],
        "content_truncated": len(final or "") > 500,
        "workplace_changed": False,
    })
    _running[key] = False
    return final


async def run_react_loop(
    db: Session,
    agent: Agent,
    session_id: str,
    user_message: str,
    username: str,
    workplace_dir: str = "",
    workplace_files: list[str] | None = None,
    message_meta: dict | None = None,
    *,
    persist_user_message: bool = True,
    precomputed_turn_intent: TurnIntent | None = None,
) -> str:
    key = f"{agent.id}:{session_id}"
    _running[key] = True
    save_dir = workplace_dir.strip().strip("/")
    selected_files = list(workplace_files or [])

    llm = db.query(LLMResource).filter(LLMResource.id == agent.llm_id).first()
    sandbox = db.query(Sandbox).filter(Sandbox.id == agent.sandbox_id).first()
    allowed = json.loads(agent.allowed_actions or "[]")
    shell_enabled = "shell" in allowed
    skill_ids = json.loads(agent.skills or "[]")
    mcp_ids = json.loads(agent.mcps or "[]")
    rag_ids = json.loads(getattr(agent, "rags", None) or "[]")
    httpmcp_ids = []
    files_written = 0

    user_meta = dict(message_meta or {})
    if persist_user_message:
        db.add(ChatMessage(
            agent_id=agent.id,
            session_id=session_id,
            role="user",
            content=user_message,
            meta=json.dumps(user_meta, ensure_ascii=False) if user_meta else "{}",
            created_at=now_str(),
        ))
        db.commit()

    note = db.query(ChatNote).filter(ChatNote.agent_id == agent.id, ChatNote.session_id == session_id).first()
    effective_message = user_message
    note_content = ""
    if note and note.content and note.content.strip():
        note_content = note.content.strip()

    # Repair / feedback context: never treat「数据不全」as a standalone export brief
    export_filter_condition = ""
    export_repair_system_hint = ""
    export_repair_prior_state: dict | None = None
    export_repair_prior_run_id = ""
    export_prior_same_session = True
    export_turn_intent = "unknown"
    export_turn_digest = ""
    export_tw_from_prior = False
    export_fill_scope = ExportFillScope()
    export_fill_columns: list[str] = []
    export_fill_target_rel = ""
    export_deliverable_headers: list[str] = []
    export_repair_like = False
    export_repair_prefetched: tuple[str, dict] | None = None
    task_brief = user_message
    export_source_brief = (user_message or "").strip()
    # Prefetch prior export state whenever sandbox exists (LLM may enter column fill)
    if agent.sandbox_id:
        export_repair_prefetched = find_repair_base_run_state(
            agent.sandbox_id, session_id=session_id or "",
        )
    await hub.publish(key, {"type": "status", "content": "思考中..."})

    # Fast path: push existing deliverable to Agent-bound IM channel (skip MCP/LLM)
    send_channel_intent = _is_send_existing_to_channel_intent(user_message)
    if send_channel_intent:
        pref = _infer_provider_from_message(user_message) or "消息渠道"
        step = {
            "type": "info",
            "action": "channel_push_existing",
            "title": f"推送已有文件到 {pref}",
            "status": "running",
        }
        await hub.publish(key, {"type": "step", "op": "append", "index": 0, "step": step})
        try:
            final = await _handle_send_existing_to_channel(
                db,
                agent,
                session_id,
                user_meta,
                user_message,
                save_dir=save_dir,
                workplace_files=selected_files,
            )
            step["status"] = "done"
            step["content"] = (final or "")[:500]
        except Exception as e:
            logger.exception("channel push existing failed")
            final = f"推送到消息渠道失败: {e}"
            step["status"] = "error"
            step["content"] = str(e)[:500]
        await hub.publish(key, {"type": "step", "op": "patch", "index": 0, "step": step})
        meta = json.dumps({
            "steps": [step],
            "step_count": 1,
            "saved_paths": [],
            "channel_push_fastpath": True,
            "tg_push_fastpath": True,  # legacy meta key for older clients
            **{
                key: user_meta[key]
                for key in (
                    "source", "channel_id", "chat_id", "chat_type", "user_id",
                    "sender_username", "sender_display_name",
                )
                if user_meta.get(key)
            },
        }, ensure_ascii=False)
        db.add(ChatMessage(
            agent_id=agent.id,
            session_id=session_id,
            role="assistant",
            content=final,
            meta=meta,
            created_at=now_str(),
        ))
        db.commit()
        try:
            await _append_rolling_summary(db, agent, session_id, llm, final, [])
        except Exception:
            logger.exception("rolling summary failed agent=%s session=%s", agent.id, session_id)
        await hub.publish(key, {
            "type": "done",
            "content": (final or "")[:500],
            "content_truncated": len(final or "") > 500,
            "workplace_changed": False,
        })
        _running[key] = False
        return final

    system = agent.prompt or "You are a helpful assistant."
    if note_content:
        system += (
            "\n\n【会话备注·约束】以下备注用于补充默认口径、时区和文件规则，"
            "不是本轮用户任务；用户显式要求优先：\n" + note_content[:3000]
        )
    if agent.memory:
        system += f"\n\n长期记忆:\n{agent.memory}"
    summary = db.query(ChatSummary).filter(ChatSummary.agent_id == agent.id, ChatSummary.session_id == session_id).first()
    if summary and summary.content:
        roll_txt = _clamp_summary_text(summary.content, _summary_max_chars(agent))
        system += f"\n\n会话滚动总结:\n{roll_txt}"

    history = db.query(ChatMessage).filter(
        ChatMessage.agent_id == agent.id,
        ChatMessage.session_id == session_id,
    ).order_by(ChatMessage.id.desc()).limit(max(1, min(int(agent.history_length or 30), 200))).all()
    history = list(reversed(history))
    relation_history = list(history)
    if relation_history:
        latest = relation_history[-1]
        if (
            str(getattr(latest, "role", "") or "") == "user"
            and str(getattr(latest, "content", "") or "").strip()
            == str(user_message or "").strip()
        ):
            relation_history = relation_history[:-1]
    followup_sql = ""
    followup_query_contract: dict[str, Any] = {}
    followup_output_columns: list[str] = []
    authoritative_sql_rel = ""
    authoritative_query_contract: dict[str, Any] = {}
    if export_repair_prefetched:
        authoritative_query_contract, authoritative_sql_rel = (
            _resolve_authoritative_query_contract(
                sandbox,
                str(export_repair_prefetched[0] or ""),
                export_repair_prefetched[1],
            )
        )

    messages = [{"role": "system", "content": system}]
    for h in history:
        content = h.content or ""
        if len(content) > 6000:
            content = content[:5500] + "\n…(历史消息已截断)"
        messages.append({"role": h.role, "content": content})
    if messages and messages[-1]["role"] == "user":
        messages[-1]["content"] = task_brief or user_message

    # Policy: LLM turn intent (primary) — regex detect_task_policy only for repair force
    skill_mds = load_skill_mds(db, skill_ids)
    skill_blob = "\n".join(f"{n}\n{m}" for n, m in skill_mds)
    has_export_skill = bool(
        re.search(r"export.?report|用户报表|注册用户.*导出|分析列", skill_blob or "", re.I)
    )
    turn_intent = precomputed_turn_intent
    if turn_intent is None:
        turn_intent = await analyze_turn_intent(
            llm,
            user_message,
            has_export_skill=has_export_skill,
            skill_blob=skill_blob,
            db=db,
            is_light_chat=_is_casual_chat_message(user_message)
            or _is_light_agent_interaction(user_message),
            timeout=min(45, int(getattr(agent, "llm_timeout", None) or 45) or 45),
            agent_id=getattr(agent, "id", "") or "",
            session_id=session_id or "",
            context_note=note_content,
            conversation_context=build_task_relation_context(
                relation_history,
                export_repair_prefetched[1] if export_repair_prefetched else None,
            ),
            require_decided_relation=bool(export_repair_prefetched),
        )
    if (
        (
            turn_intent.task_relation in ("continue", "revise")
            or turn_intent.verification_required
        )
        and export_repair_prefetched
        and not export_repair_like
    ):
        export_repair_like = True
        export_repair_prior_run_id = str(export_repair_prefetched[0] or "")
        prior = export_repair_prefetched[1]
        export_repair_prior_state = prior if isinstance(prior, dict) else None
        prior_source = str(
            (export_repair_prior_state or {}).get("source_brief") or ""
        ).strip()
        if export_repair_prior_state is not None:
            prior_sess = str(export_repair_prior_state.get("session_id") or "").strip()
            export_prior_same_session = (
                not prior_sess or prior_sess == (session_id or "").strip()
            )
        export_fill_target_rel = resolve_export_fill_target_rel(
            agent.sandbox_id,
            export_repair_prior_state,
        )
        if export_fill_target_rel:
            prior_path = download_path(agent.sandbox_id, export_fill_target_rel)
            if prior_path:
                export_deliverable_headers = read_deliverable_headers(prior_path) or []
        if not export_deliverable_headers:
            export_deliverable_headers = prior_headers_from_state(
                export_repair_prior_state,
            )
        task_brief, export_filter_condition = _build_export_repair_message(
            user_message,
            prior_user=prior_source,
            prior_state=export_repair_prior_state,
        )
        export_source_brief = _resolve_repair_source_brief(
            prior_user=prior_source,
            prior_state=export_repair_prior_state,
        )
        effective_message = task_brief
        if messages and messages[-1]["role"] == "user":
            messages[-1]["content"] = task_brief
        followup_query_contract = (
            authoritative_query_contract
            or _find_recent_assistant_query_contract(history)
        )
        followup_sql = str(followup_query_contract.get("sql") or "").strip()
        followup_output_columns = [
            str(name).strip()
            for name in (followup_query_contract.get("output_columns") or [])
            if str(name).strip()
        ]
    # Never take tool-free chat for task / rework / session follow-ups
    if turn_intent.is_chat and not turn_intent.wants_session_summary and (
        turn_intent.task_relation in ("continue", "revise")
        or turn_intent.verification_required
        or looks_like_task_message(user_message)
        or _needs_generic_plan_gate(
            user_message,
            db=db,
            agent_id=getattr(agent, "id", "") or "",
            session_id=session_id,
        )
    ):
        turn_intent = TurnIntent(
            intent="other_tools",
            reason=f"override:tools_not_chat:{turn_intent.reason}",
            wants_deliverable=False,
            query_goal=turn_intent.query_goal or (user_message or "").strip()[:200],
            conversation_action=turn_intent.conversation_action,
            metrics=list(turn_intent.metrics or []),
            metric_intents=list(turn_intent.metric_intents or []),
            time_window=turn_intent.time_window,
            task_relation=turn_intent.task_relation,
            verification_required=turn_intent.verification_required,
            verification_targets=list(turn_intent.verification_targets),
            requested_artifacts=list(turn_intent.requested_artifacts),
            result_shape=turn_intent.result_shape,
            temporal_grain=turn_intent.temporal_grain,
            raw=turn_intent.raw,
            source=turn_intent.source,
        )
    requested_artifacts = set(turn_intent.requested_artifacts or [])
    if requested_artifacts and not followup_query_contract:
        followup_query_contract = (
            authoritative_query_contract
            or _find_recent_assistant_query_contract(history)
        )
        followup_sql = str(followup_query_contract.get("sql") or "").strip()
        followup_output_columns = [
            str(name).strip()
            for name in (followup_query_contract.get("output_columns") or [])
            if str(name).strip()
        ]
    task_policy: TaskPolicy = task_policy_from_intent(turn_intent)
    intent_time_window = turn_intent.time_window
    if "sql" in requested_artifacts and followup_sql:
        sql_path_line = (
            f"### SQL 文件\n\n- `{authoritative_sql_rel}`\n\n"
            if authoritative_sql_rel
            else ""
        )
        final = (
            sql_path_line
            + "### 最新 SQL\n\n```sql\n"
            + normalize_sql(followup_sql)
            + "\n```"
        ).strip()
        step = {
            "type": "info",
            "action": "artifact_read",
            "title": "已读取上一任务的最新 SQL 产物",
            "status": "done",
            "path": authoritative_sql_rel,
        }
        saved = [authoritative_sql_rel] if authoritative_sql_rel else []
        meta = json.dumps({
            "steps": [step],
            "step_count": 1,
            "saved_paths": saved,
            "query_contract": followup_query_contract,
            "export_run_id": str((export_repair_prefetched or ("", {}))[0] or ""),
            "task_relation": turn_intent.task_relation,
            "context_continuity": True,
            "task_title": str(
                ((export_repair_prefetched or ("", {}))[1] or {}).get("task_title")
                or turn_intent.query_goal
                or ""
            ).strip(),
        }, ensure_ascii=False)
        db.add(ChatMessage(
            agent_id=agent.id,
            session_id=session_id,
            role="assistant",
            content=final,
            meta=meta,
            created_at=now_str(),
        ))
        db.commit()
        try:
            await _append_rolling_summary(db, agent, session_id, llm, final, saved)
        except Exception:
            logger.exception(
                "rolling summary failed agent=%s session=%s",
                agent.id,
                session_id,
            )
        await hub.publish(key, {
            "type": "done",
            "content": final[:500],
            "content_truncated": len(final) > 500,
            "workplace_changed": False,
        })
        _running[key] = False
        return final
    if followup_sql and turn_intent.wants_deliverable and not task_policy.export_like:
        from app.services.task_policy import EXPORT_PLAN_HINT

        task_policy = TaskPolicy(
            policy_id="export_report",
            export_like=True,
            plan_hint=EXPORT_PLAN_HINT,
            require_plan_gate=True,
            reason="followup_sql_export",
        )
    if followup_sql and task_policy.export_like:
        export_source_brief = (
            (export_source_brief or user_message or "").strip()
            + "\n\n【沿用上一轮已确认 SQL】\n```sql\n"
            + followup_sql.strip()[:12000]
            + "\n```"
        )
    if (export_repair_like or export_fill_columns) and not task_policy.export_like:
        # Force export path for gap repair / LLM column-fill
        task_policy = detect_task_policy(
            (task_brief or user_message) + "\n导出分析报表 xlsx",
            skill_blob=skill_blob,
        )
        if not task_policy.export_like:
            from app.services.task_policy import EXPORT_PLAN_HINT

            task_policy = TaskPolicy(
                policy_id="export_report",
                export_like=True,
                plan_hint=EXPORT_PLAN_HINT,
                require_plan_gate=True,
                reason="column_fill_or_repair",
            )
    # Tool-free chat: only pure chat (not task-like / follow-up)
    if not task_policy.export_like and turn_intent.is_chat:
        return await _run_conversational_turn(
            db=db,
            agent=agent,
            session_id=session_id,
            user_message=user_message,
            effective_message=effective_message,
            note_content=note_content,
            llm=llm,
            history=history,
            key=key,
            user_meta=user_meta,
            turn_intent=turn_intent,
        )
    skill_snap = skill_snapshot_for_prompt(skill_mds)
    if skill_snap:
        messages.append({"role": "system", "content": skill_snap})
    messages.append({
        "role": "system",
        "content": _build_model_understanding_system(agent, task_policy),
    })
    # Need-based data_query: one merged coach (gaps + bind whitelist); never hexad fetch
    query_resource_whitelist: list[str] = []
    if turn_intent.needs_tools and not task_policy.export_like:
        gaps = compute_data_query_gaps(turn_intent, user_message=user_message)
        bind_result: BindResult | None = None
        # Soft seed only when no critical intent gaps (avoid silent full pulls from seed)
        allow_seed = "metrics" not in gaps
        if mcp_ids and metric_intents_from_turn(turn_intent):
            try:
                bind_result = await _bind_mcp_resources_for_turn(
                    db=db,
                    llm=llm,
                    agent=agent,
                    mcp_ids=mcp_ids,
                    turn_intent=turn_intent,
                    allow_seed=allow_seed,
                )
                gaps = compute_data_query_gaps(
                    turn_intent,
                    user_message=user_message,
                    bindings=bind_result.bindings if bind_result else None,
                )
                query_resource_whitelist = binding_resource_whitelist(bind_result)
            except Exception:
                logger.exception("mcp resource bind failed")
                bind_result = None
        plan_hint = format_need_based_query_plan(
            turn_intent,
            gaps=gaps,
            bind_result=bind_result,
            time_window=intent_time_window,
        )
        if plan_hint:
            messages.append({"role": "system", "content": plan_hint})
        if intent_time_window or turn_intent.query_goal or turn_intent.metrics:
            tw_hint = format_time_window_system_hint(
                intent_time_window,
                query_goal=turn_intent.query_goal,
                metrics=turn_intent.metrics,
                metric_intents=turn_intent.metric_intents,
                result_shape=turn_intent.result_shape,
                temporal_grain=turn_intent.temporal_grain,
            )
            if tw_hint:
                messages.append({"role": "system", "content": tw_hint})
        if query_resource_whitelist:
            messages.append({
                "role": "system",
                "content": (
                    "【按需拉取白名单】优先 query view/resource ∈ {"
                    + ", ".join(query_resource_whitelist)
                    + "}；勿默认全量拉取/分页全表。"
                    "若 list/describe 发现更合适的资源名，以目录为准。"
                ),
            })
    else:
        tw_hint = format_time_window_system_hint(
            intent_time_window,
            query_goal=turn_intent.query_goal,
            metrics=turn_intent.metrics,
            metric_intents=turn_intent.metric_intents,
            result_shape=turn_intent.result_shape,
            temporal_grain=turn_intent.temporal_grain,
        )
        if tw_hint:
            messages.append({"role": "system", "content": tw_hint})
    # export_repair_system_hint appended after time-window resolve (may mark window_change)
    if sandbox and (task_policy.export_like or mcp_ids):
        mcp_lesson_hint = load_recent_mcp_lesson_hints(sandbox.id, max_chars=800)
        if mcp_lesson_hint:
            messages.append({"role": "system", "content": mcp_lesson_hint})
        if task_policy.export_like:
            fetch_gap_hint = load_recent_fetch_gap_hints(sandbox.id, max_chars=700)
            if fetch_gap_hint:
                messages.append({"role": "system", "content": fetch_gap_hint})

    tools_desc = await _build_tools_desc(
        db, agent, allowed, skill_ids, mcp_ids, rag_ids, save_dir,
        im_source=str(user_meta.get("source") or ""),
        export_like=task_policy.export_like,
        strategy_blurb=task_policy.tools_strategy_blurb(),
    )
    if tools_desc:
        if len(tools_desc) > 12000:
            tools_desc = tools_desc[:11000] + "\n…(工具说明已截断)"
        messages.append({"role": "system", "content": f"可用工具:\n{tools_desc}"})
    if save_dir:
        messages.append({
            "role": "system",
            "content": (
                f"【保存目录】用户已选中 `{save_dir}` 作为当前目录："
                f"仅最终交付文件写入 `{save_dir}/`；过程产物写入 `task/<毫秒时间戳>/`。"
            ),
        })

    if sandbox:
        wp_listing = format_dir_listing(sandbox.id, save_dir)
        if len(wp_listing) > 4000:
            wp_listing = "\n".join(wp_listing.splitlines()[:80]) + "\n…(目录列表已截断)"
        messages.append({
            "role": "system",
            "content": (
                "【当前工作目录】以下列表与左侧文件面板一致（API 持久化目录）。"
                "查看文件请优先用 READ: <相对路径>，不要用 SHELL: ls /workplace 判断文件是否存在。\n"
                f"{wp_listing}"
            ),
        })

    prior_exports = _collect_recent_export_rels(db, agent, session_id, limit=5)
    if prior_exports:
        messages.append({
            "role": "system",
            "content": (
                "【近期已导出文件】"
                + "、".join(f"`{p}`" for p in prior_exports)
                + "。若用户只要把已有报表发到 Telegram/TG，禁止再调用 query_ads_view / describe_ads_view；"
                "直接 FINAL 说明由平台推送，或提示用户说「发给tg」。"
            ),
        })

    final = ""
    last_reply = ""
    structured_reply_candidate = ""
    run_steps: list[dict] = []
    saved_paths: list[str] = []
    mcp_results: list[dict] = []
    mcp_tool_calls: dict[str, int] = {}
    mcp_tool_fails: dict[str, int] = {}
    mcp_identical_counts: dict[str, int] = {}
    mcp_class_fails: dict[str, int] = {}
    mcp_class_samples: dict[str, str] = {}
    mcp_class_tools: dict[str, str] = {}
    progress_lines: list[str] = []
    finalize_hint_injected = False
    had_explicit_final = False
    no_progress = 0
    empty_llm_streak = 0
    tools_effective_this_turn = False
    soft_finish_requested = False
    mcp_excuse_soft_rejects = 0
    mcp_excuse_claim_hit = False
    execution_context_continuity = bool(
        export_repair_prefetched
        and turn_intent.task_relation in ("continue", "revise")
    )
    execution_context_title = str(
        ((export_repair_prefetched or ("", {}))[1] or {}).get("task_title")
        or turn_intent.query_goal
        or ""
    ).strip()
    context_available_percent = 100

    def _current_context_available_percent() -> int:
        max_context = max(
            2048,
            int(getattr(llm, "max_context_tokens", None) or 128000),
        )
        fitted, _ = fit_messages_to_context(
            messages,
            max_context_tokens=max_context,
            max_output_tokens=4096,
        )
        used = sum(
            estimate_tokens(str(message.get("content") or "")) + 4
            for message in fitted
        )
        return max(0, min(100, round((max_context - used) * 100 / max_context)))
    soft_fail_limit = max(
        1,
        min(int(getattr(agent, "mcp_soft_circuit", None) or _MCP_SOFT_FAIL_HINT_DEFAULT), 50),
    )
    class_fail_limit = min(soft_fail_limit, _MCP_CLASS_HARD_LIMIT)

    async def _handle_mcp_excuse_on_final(final_text: str) -> tuple[str, bool]:
        """LLM-intent soft-reject for bound-MCP 'no tools' excuse. Returns (text, continue_loop)."""
        nonlocal mcp_excuse_soft_rejects, mcp_excuse_claim_hit, had_explicit_final, final
        from app.services.intent_router import (
            UNAVAILABLE_DATA_TOOLS_COACH,
            append_unavailable_tools_soft_nudge,
            maybe_soft_reject_unavailable_tools_finish,
        )
        action, mcp_excuse_soft_rejects, hit = await maybe_soft_reject_unavailable_tools_finish(
            llm=llm,
            assistant_text=final_text or "",
            has_mcp=bool(mcp_ids),
            ran_any_tool=ran_any_tool,
            soft_reject_count=mcp_excuse_soft_rejects,
            db=db,
            timeout=min(30, int(getattr(agent, "llm_timeout", None) or 30)),
        )
        if hit:
            mcp_excuse_claim_hit = True
        if action == "reject":
            had_explicit_final = False
            final = ""
            messages.append({"role": "assistant", "content": reply})
            messages.append({"role": "user", "content": UNAVAILABLE_DATA_TOOLS_COACH})
            return "", True
        if action == "append":
            return append_unavailable_tools_soft_nudge(final_text or ""), False
        return final_text or "", False

    async def _push_step(step: dict):
        nonlocal context_available_percent
        context_available_percent = _current_context_available_percent()
        step.setdefault("task_relation", turn_intent.task_relation)
        step.setdefault("context_continuity", execution_context_continuity)
        step.setdefault("context_available_percent", context_available_percent)
        if execution_context_title:
            step.setdefault("task_title", execution_context_title)
        run_steps.append(step)
        idx = len(run_steps) - 1
        await hub.publish(key, {
            "type": "step",
            "op": "append",
            "index": idx,
            "step": step,
        })

    async def _patch_last_step(**updates):
        nonlocal context_available_percent
        if not run_steps:
            return
        context_available_percent = _current_context_available_percent()
        updates.setdefault("context_available_percent", context_available_percent)
        idx = len(run_steps) - 1
        run_steps[idx].update(updates)
        await hub.publish(key, {
            "type": "step",
            "op": "patch",
            "index": idx,
            "step": updates,
        })

    async def _publish_tool_ws(content: str, action: str, *, hidden: bool = False):
        # Frontend only consumes step/done; skip unused tool WS noise.
        del content, action, hidden
        return

    mcp_names = _bound_mcp_names(db, mcp_ids)
    skill_names = [n for n, _md in (skill_mds or []) if n]
    if skill_names:
        await _push_step({
            "type": "info",
            "action": "skill_loaded",
            "title": f"已加载 Skills: {', '.join(skill_names)}",
            "status": "done",
        })
    elif skill_ids:
        await _push_step({
            "type": "info",
            "action": "skill_loaded",
            "title": "Skill 绑定异常：未能读取已配置 Skill（检查 Skill 是否仍存在或文件是否可读）",
            "status": "error",
            "content": "skill_ids=" + ",".join(str(s) for s in skill_ids[:8]),
        })
    if mcp_names:
        await _push_step({
            "type": "info",
            "action": "mcp_loaded",
            "title": f"已加载 MCPs: {', '.join(mcp_names)}",
            "status": "done",
        })

    def _bump_no_progress(reply: str) -> bool:
        """Return True if loop should abort due to stuck narration / no tools."""
        nonlocal no_progress, final
        # Clarify / ask-user → finish turn successfully (not empty-spin abort)
        if (
            not export_like
            and not turn_intent.needs_tools
            and _is_clarify_progress_reply(reply)
        ):
            cleaned = _clean_final_answer(_clean_display_text(reply) or reply)
            final = cleaned or (reply or "").strip()
            return True
        no_progress += 1
        if no_progress < _NO_PROGRESS_LIMIT:
            # Soft coach before abort — keeps agent oriented to original brief
            if export_like:
                try:
                    messages.append({
                        "role": "user",
                        "content": (
                            "请立即输出可执行工具行（MCP:/SHELL:/FINAL:），勿只叙述。\n"
                            + _coach_nudge()
                        ),
                    })
                except NameError:
                    pass
            return False
        hint = (
            "Agent 连续多轮未执行有效工具（疑似只用自然语言描述，未输出 READ:/WRITE:/SHELL:/FINAL: 格式），已中止，避免空转耗尽迭代。"
            "请重试，并确保模型按行首工具指令调用。"
        )
        cleaned = _clean_display_text(reply)
        final = f"{cleaned}\n\n---\n{hint}" if cleaned else hint
        return True

    max_iters = _effective_max_iterations(
        agent,
        user_message,
        needs_tools=bool(turn_intent.needs_tools),
    )
    export_like = task_policy.export_like
    export_task_title = (
        _export_task_title(
            query_goal=turn_intent.query_goal,
            source_brief=export_source_brief or task_brief,
            prior_state=export_repair_prior_state,
        )
        if export_like
        else ""
    )
    trim_keep = _KEEP_RECENT_TOOL_MSGS_EXPORT if export_like else _KEEP_RECENT_TOOL_MSGS
    trim_cap = _OLD_TOOL_MSG_CAP_EXPORT if export_like else _OLD_TOOL_MSG_CAP
    export_finalize_from = _export_finalize_start(max_iters) if export_like else max(0, max_iters - _FINALIZE_WINDOW)
    export_phase = "discover" if export_like else ""
    # data_query already has need-based plan — skip generic PLAN (avoids burning 2 LLM rounds)
    skip_generic_plan = (turn_intent.intent or "").strip() == "data_query"
    # Generic policy: PLAN gate for other tool intents (not data_query / not export)
    generic_phase = (
        "plan"
        if (
            not export_like
            and not skip_generic_plan
            and task_policy.require_plan_gate
            and turn_intent.needs_tools
        )
        else ""
    )
    generic_plan_done = False
    generic_plan_attempts = 0
    generic_plan_hint_injected = False
    generic_todos: list[dict] = []
    generic_tool_budget = 8
    generic_tool_count = 0
    export_discover_ready = False
    export_discover_queries = 0
    plan_done = False
    plan_attempts = 0
    plan_hint_injected = False
    mcp_query_count = 0
    mcp_query_budget = _EXPORT_QUERY_BUDGET_DEFAULT
    export_budget_cap = _EXPORT_QUERY_BUDGET_DEFAULT
    export_force_finalize = False
    analyze_rounds_used = 0
    analyze_hint_injected = False
    analyze_empty_reject_count = 0
    export_run_id = str(int(time.time() * 1000))
    # Parse columns from clean source brief (strip 补齐 marker line if present)
    _todo_brief = export_source_brief or task_brief
    if "【本轮补齐】" in (_todo_brief or ""):
        _todo_brief = re.split(r"\n\s*【本轮补齐】", _todo_brief, maxsplit=1)[0].rstrip()
    export_todos: list[dict] = _parse_export_todos(_todo_brief) if export_like else []
    if export_like and followup_output_columns:
        export_todos = _todos_from_analyze_columns(followup_output_columns)
    if export_like and export_repair_prior_state:
        prior_cols = [
            str(c).strip()
            for c in (export_repair_prior_state.get("analyze_columns") or [])
            if str(c).strip()
        ]
        if not prior_cols:
            prior_cols = [
                str(t.get("text") or "").strip()
                for t in (export_repair_prior_state.get("todos") or [])
                if isinstance(t, dict)
                and t.get("phase") == "analyze"
                and str(t.get("text") or "").strip()
                and not str(t.get("text") or "").startswith(("请优先补齐", "上轮已覆盖", "时间窗与输出列"))
            ]
        parsed_n = sum(1 for t in export_todos if t.get("phase") == "analyze")
        if prior_cols and parsed_n < 4:
            export_todos = _todos_from_analyze_columns(prior_cols)
    if export_like and export_todos:
        _recon_todos, _recon_nudge = _soft_reconcile_export_column_todos(
            _todo_brief,
            export_todos,
        )
        if _recon_nudge:
            export_todos = _recon_todos
            _append_progress(progress_lines, _recon_nudge)
    export_fallback = False
    export_missing_cols: list[str] = []
    ran_any_tool = False
    analyze_write_shell_ok = False
    analyze_explore_shells = 0
    engine_script_ran = False
    engine_script_executed = False
    fetched_view_pages: dict[str, int] = {}
    fetched_view_last_rows: dict[str, int] = {}
    export_failed_views: set[str] = set()
    export_abandoned_roles: set[str] = set()
    export_done_node_keys: set[str] = set()  # sequence/top_n: at most one pull per node key
    export_full_fetch = False
    export_uid_batch_index = 0  # uid offset into pay distinct-uid list
    user_fetch_complete = False
    export_prior_fetch_hint = ""
    export_view_mode = "multi_fact"
    export_pinned_views: list[str] = []
    export_target_roles: list[str] = []
    export_prior_column_plan: list[dict] = _prior_column_plan_from_state(export_repair_prior_state)
    if export_like:
        view_route = route_export_view_intent(
            _todo_brief if export_repair_prior_state else task_brief,
        )
        if view_route.mode == "ambiguous":
            view_route = await classify_view_intent_llm(
                llm,
                db,
                _todo_brief if export_repair_prior_state else task_brief,
                view_route,
                timeout=min(20, int(getattr(agent, "llm_timeout", None) or 20)),
            )
        export_view_mode, export_pinned_views = _apply_view_intent_route(
            view_route,
        )
        # Intent routing only controls the interaction mode. Live MCP bindings
        # are the sole authorization for export execution.
        export_target_roles = []
        export_pinned_views = list(export_pinned_views)
    # Column-driven plan: user intent becomes resource bindings, never a role set.
    export_column_plan: list[dict] = []
    export_resource_whitelist: list[str] = []
    if export_like and export_view_mode != "single_view":
        # LLM column-fill: fetch from focus; deliverable schema keeps prior wide headers
        if export_fill_columns:
            if not export_deliverable_headers:
                export_deliverable_headers = prior_headers_from_state(
                    export_repair_prior_state,
                )
            # Gate/FINAL todos = prior interface (wide); fetch plan = focus only
            if export_deliverable_headers:
                export_todos = _todos_from_analyze_columns(export_deliverable_headers)
            _append_progress(
                progress_lines,
                "fill_columns="
                + ",".join(export_fill_columns[:12])
                + (
                    f"; fill_target={export_fill_target_rel}"
                    if export_fill_target_rel
                    else ""
                ),
            )
            export_column_plan = [
                {"header": str(header).strip(), "rule": "output_contract"}
                for header in export_fill_columns
                if str(header).strip()
            ]
        else:
            col_headers = _export_analyze_column_texts(export_todos)
            # Soft seed from numbered brief when todos empty (intent JSON failed earlier)
            if not col_headers:
                col_headers = _extract_numbered_labels(
                    export_source_brief or task_brief or user_message or ""
                )
                if col_headers:
                    export_todos = _todos_from_analyze_columns(col_headers) or export_todos
                    _append_progress(
                        progress_lines,
                        f"列计划软种子={len(col_headers)}（编号列清单）",
                    )
            export_column_plan = [
                {"header": str(header).strip(), "rule": "output_contract"}
                for header in col_headers
                if str(header).strip()
            ]
        # Runtime target selection happens only after live MCP list/describe.
        export_target_roles = []
        _append_progress(
            progress_lines,
            f"output_contract columns={len(export_column_plan)}；资源待 MCP list/describe + LLM CTE",
        )
        if export_fill_columns:
            messages.append({
                "role": "system",
                "content": (
                    "【导出·列补齐】本轮仅拉取列："
                    + "、".join(export_fill_columns)
                    + "；勿拉未点名列资源。"
                    + (
                        f"建议覆盖已有交付 `{export_fill_target_rel}`，"
                        "按用户ID合并补列并保留原表全部列（勿另存窄表）。"
                        if export_fill_target_rel
                        else "写回时保留原接口全部列（勿另存仅补齐列的新文件）。"
                    )
                    + (
                        f"（{export_fill_scope.reason}）"
                        if export_fill_scope.reason
                        else ""
                    )
                ),
            })
    export_task_spec = None
    export_trace = ExportTrace(run_id=str(export_run_id or ""), source_brief="")
    _append_progress(
        progress_lines,
        f"policy={task_policy.policy_id} reason={task_policy.reason}"
        + (f" mode={export_view_mode}" if export_like else "")
        + (
            f" pinned={','.join(export_pinned_views)}"
            if export_pinned_views
            else ""
        ),
    )
    if export_like and export_view_mode == "single_view" and export_pinned_views:
        messages.append({
            "role": "system",
            "content": (
                "【视图意图·类型 C】用户已点名视图，本轮只允许 query/describe："
                + "、".join(f"`{v}`" for v in export_pinned_views)
                + "。禁止拉未点名资源；需要资源写「无」。"
            ),
        })
    if export_like and followup_sql:
        sql_anchor = (
            "【续话 SQL 导出锚点】用户本轮要求沿用上一轮已确认 SQL 直接导出。"
            "请把下列 SQL 视为已确认执行口径，不要改写成新的列计划、默认视图或另一套统计路径；"
            "若需落盘，必须基于该 SQL 的真实查询结果生成交付。\n"
            f"```sql\n{followup_sql[:12000]}\n```"
        )
        messages.append({"role": "system", "content": sql_anchor})
    export_time_window: dict | None = None
    if export_like:
        # Cross-session prior: usable for gaps/roles soft hints, not for TW hydrate
        _prior_for_tw = export_repair_prior_state
        if (
            _prior_for_tw
            and not export_prior_same_session
        ):
            _prior_for_tw = None
        # Session-scoped prior for intent (even on non-repair turns)
        _intent_prior = _prior_for_tw
        if _intent_prior is None and sandbox:
            try:
                _found_intent = find_latest_run_state(
                    sandbox.id,
                    exclude_run_id=export_run_id,
                    session_id=session_id or "",
                )
                if _found_intent:
                    _cand = _found_intent[1]
                    ps = str((_cand or {}).get("session_id") or "").strip()
                    if not ps or ps == (session_id or "").strip():
                        _intent_prior = _cand
            except Exception:
                pass
        export_time_window, export_tw_from_prior = _resolve_export_time_window(
            user_message,
            source_brief=export_source_brief or _todo_brief or task_brief or "",
            prior_state=_prior_for_tw,
        )
        # Prefer LLM-parsed natural-language day/range when regex miss
        if (
            not export_time_window
            and isinstance(intent_time_window, dict)
            and intent_time_window.get("start_ms") is not None
            and intent_time_window.get("end_ms") is not None
        ):
            export_time_window = dict(intent_time_window)
            export_tw_from_prior = False
        # Always refresh FINAL filter from resolved window (never keep locked prior label)
        if export_time_window:
            export_filter_condition = str(export_time_window.get("label") or "")
        export_turn_intent = _export_turn_state_from_intent(
            turn_intent,
            resolved_tw=export_time_window,
            prior_state=_intent_prior or export_repair_prior_state,
        )
        export_turn_digest = _build_turn_digest(
            user_message,
            intent=export_turn_intent,
            time_window=export_time_window,
        )
        if export_repair_prior_state is not None or export_repair_like:
            if sandbox and isinstance(export_repair_prior_state, dict):
                try:
                    _trace_data = (
                        read_export_trace(sandbox.id, export_repair_prior_run_id)
                        if export_repair_prior_run_id
                        else None
                    )
                    if isinstance(_trace_data, dict):
                        _rp = (
                            _trace_data.get("repair_plan")
                            if isinstance(_trace_data.get("repair_plan"), dict)
                            else {}
                        )
                        if _rp:
                            export_repair_prior_state = {
                                **export_repair_prior_state,
                                "repair_plan": _rp,
                                "repair_trace_run_id": export_repair_prior_run_id,
                            }
                except Exception:
                    logger.exception("load repair_plan for system hint failed")
            window_changed = export_turn_intent == "window_change" or (
                bool(export_time_window)
                and not export_tw_from_prior
                and bool(_hydrate_time_window_from_state(export_repair_prior_state))
                and not _time_windows_equal(
                    export_time_window,
                    _hydrate_time_window_from_state(export_repair_prior_state),
                )
            )
            export_repair_system_hint = _build_export_repair_system_hint(
                user_message=user_message,
                prior_state=export_repair_prior_state,
                prior_run_id=export_repair_prior_run_id,
                resolved_tw=export_time_window,
                window_changed=window_changed,
            )
            if window_changed and export_turn_intent == "unknown":
                export_turn_intent = "window_change"
                export_turn_digest = _build_turn_digest(
                    user_message,
                    intent=export_turn_intent,
                    time_window=export_time_window,
                )
        elif export_turn_intent == "window_change" and export_time_window:
            export_repair_system_hint = (
                "【本轮时间窗已更新】以当前用户消息解析的时间窗为准；"
                "勿沿用上轮 MCP SQL / OFFSET 续页模板。"
                f" 时间窗：{export_time_window.get('label') or ''}"
            )
        if export_repair_system_hint:
            messages.append({"role": "system", "content": export_repair_system_hint})
    export_run_started_at = time.time()
    analyze_max_rounds = _export_analyze_max_rounds(export_todos) if export_like else 0
    ads_views_list_text = ""
    ads_schema_hints: dict[str, ViewSchemaHint] = {}
    export_schema_discovery: dict[str, object] = {
        "required": bool(
            export_like
            and export_view_mode == "multi_fact"
            and export_column_plan
        ),
        "list_ads_views": {},
        "described_views": {},
    }
    export_dim_views: dict[str, str] = {}
    export_dim_force_rounds = 0
    export_dim_budget_exhausted = False
    export_fetch_idle_rounds = 0  # consecutive fetch turns without MCP data success
    if export_like and export_time_window:
        _append_progress(
            progress_lines,
            f"时间窗 {export_time_window['label']} ms=[{export_time_window['start_ms']},{export_time_window['end_ms']})",
        )

    # Structured TaskSpec + ExportTrace (after TW resolved; enables resume/verify)
    if export_like:
        _hdrs = [
            str(c.get("header") or "").strip()
            for c in (export_column_plan or [])
            if isinstance(c, dict) and str(c.get("header") or "").strip()
        ]
        if not _hdrs:
            _hdrs = _export_analyze_column_texts(export_todos)
        export_contract = build_export_contract(
            task_type=export_view_mode or "multi_fact",
            headers=_hdrs,
            time_window=export_time_window,
            resources=export_resource_whitelist,
            pinned_views=export_pinned_views,
            source_brief=str(export_source_brief or task_brief or "")[:2000],
            column_plan=export_column_plan,
            schema_discovery=export_schema_discovery,
        )
        export_task_spec = export_contract.task_spec
        export_task_validation = export_contract.validation
        contract_trace_fields = export_contract.to_trace_fields()
        export_trace.run_id = str(export_run_id or "")
        export_trace.export_contract = contract_trace_fields["export_contract"]
        export_trace.task_spec = contract_trace_fields["task_spec"]
        export_trace.task_spec_validation = contract_trace_fields["task_spec_validation"]
        export_trace.column_plan = contract_trace_fields["column_plan"]
        export_trace.schema_discovery = contract_trace_fields["schema_discovery"]
        export_trace.source_brief = contract_trace_fields["source_brief"]
        if export_task_validation.status != "pass":
            _append_progress(
                progress_lines,
                "TaskSpec validation="
                f"{export_task_validation.status}: "
                + "；".join(
                    i.message for i in export_task_validation.issues[:3]
                ),
            )
            messages.append({
                "role": "system",
                "content": (
                    "【TaskSpec 校验】"
                    f"status={export_task_validation.status}；"
                    "issues="
                    + json.dumps(
                        [i.to_dict() for i in export_task_validation.issues],
                        ensure_ascii=False,
                    )[:1800]
                    + "。勿因校验停止；优先用已注入的意图时间窗 / MCP 继续拉数；"
                    "禁止仅输出澄清文案后结束本轮；若为 error，禁止宣称任务已完成。"
                ),
            })
        export_trace.query_graph = export_contract.query_graph
        schema_summary_init = export_summarize_schema_discovery(
            schema_discovery=export_trace.schema_discovery,
            export_contract=export_trace.export_contract,
        )
        if schema_summary_init.get("required") and not schema_summary_init.get("complete"):
            schema_repair_init = export_build_schema_discovery_repair_plan(
                schema_summary_init
            )
            if schema_repair_init:
                export_trace.repair_plan = schema_repair_init
        if export_repair_like and sandbox:
            try:
                prior_tr = None
                if export_repair_prior_run_id:
                    _prior_trace_data = read_export_trace(
                        sandbox.id,
                        export_repair_prior_run_id,
                    )
                    if isinstance(_prior_trace_data, dict):
                        prior_tr = (export_repair_prior_run_id, _prior_trace_data)
                if prior_tr is None:
                    prior_tr = load_latest_export_trace(
                        sandbox.id,
                        session_id=session_id or "",
                    )
                _pr_run = ""
                _pr_data: dict[str, Any] = {}
                if prior_tr:
                    _pr_run, _pr_data = prior_tr
                if prior_tr or isinstance(export_repair_prior_state, dict):
                    _activated = activate_repair_plan_from_prior(
                        _pr_data,
                        export_repair_prior_state
                        if isinstance(export_repair_prior_state, dict)
                        else None,
                    )
                    _pr_repair = _activated.get("repair_plan") if isinstance(_activated.get("repair_plan"), dict) else {}
                    _prior_node_state_safe = _should_import_prior_node_state(
                        prior_run_id=_pr_run or export_repair_prior_run_id or "",
                        current_run_id=export_run_id,
                        current_fetched_pages=fetched_view_pages,
                    )
                    if _pr_repair:
                        export_trace.repair_plan = (
                            dict(_pr_repair)
                            if _prior_node_state_safe
                            else _strip_repair_plan_node_state(_pr_repair)
                        )
                    if _prior_node_state_safe:
                        export_trace.done_node_keys = list(_activated.get("done_node_keys") or [])
                        export_trace.failed_views = list(_activated.get("failed_views") or [])
                        for k in export_trace.done_node_keys:
                            if k:
                                export_done_node_keys.add(str(k))
                        for v in export_trace.failed_views:
                            if v:
                                export_failed_views.add(str(v))
                    else:
                        export_trace.done_node_keys = []
                        export_trace.failed_views = []
                    _trace_repair = (
                        _pr_data.get("repair_plan")
                        if isinstance(_pr_data.get("repair_plan"), dict)
                        else {}
                    )
                    _state_repair = (
                        export_repair_prior_state.get("repair_plan")
                        if isinstance(export_repair_prior_state, dict)
                        and isinstance(export_repair_prior_state.get("repair_plan"), dict)
                        else {}
                    )
                    _repair_source = (
                        "trace" if _trace_repair else "run_state" if _state_repair else "none"
                    )
                    _append_progress(
                        progress_lines,
                        f"续跑载入 repair source={_repair_source} run={_pr_run or export_repair_prior_run_id or ''} "
                        f"done={len(export_trace.done_node_keys)} "
                        f"failed={len(export_trace.failed_views)} "
                        f"repair={str(_pr_repair.get('status') or 'none')}"
                        + ("" if _prior_node_state_safe else "（跨run不继承节点完成态）"),
                    )
                    if _pr_repair:
                        _acts = [
                            str(a.get("action_type") or "")
                            for a in (_pr_repair.get("actions") or [])
                            if isinstance(a, dict)
                        ]
                        if _acts:
                            _append_progress(
                                progress_lines,
                                "RepairPlan actions=" + ",".join(_acts[:5]),
                            )
            except Exception:
                logger.exception("load prior export_trace failed")
        # LLM column-fill: override RepairPlan + clear done keys for focus nodes
        if export_fill_columns and export_column_plan:
            try:
                _fill_prior = None
                if sandbox and export_repair_prior_run_id:
                    _fill_prior = read_export_trace(
                        sandbox.id, export_repair_prior_run_id,
                    )
                fill_plan = build_column_fill_repair_plan(
                    focus_columns=export_fill_columns,
                    prior_trace=_fill_prior if isinstance(_fill_prior, dict) else None,
                    prior_state=export_repair_prior_state,
                    column_plan=export_column_plan,
                    time_window=export_time_window,
                    dim_views=export_dim_views,
                )
                export_trace.repair_plan = fill_plan
                fill_keys = {
                    str(k).strip()
                    for a in (fill_plan.get("actions") or [])
                    if isinstance(a, dict)
                    for k in (a.get("node_keys") or [])
                    if str(k).strip()
                }
                # Re-fetch focus nodes: drop them from done set
                export_done_node_keys = {
                    k for k in export_done_node_keys if k not in fill_keys
                }
                export_trace.done_node_keys = [
                    k for k in (export_trace.done_node_keys or []) if k not in fill_keys
                ]
                _append_progress(
                    progress_lines,
                    "LLM列补齐 scoped keys="
                    + ",".join(sorted(fill_keys)[:8])
                    + f" cols={','.join(export_fill_columns[:6])}",
                )
            except Exception:
                logger.exception("column fill repair_plan override failed")
        try:
            write_export_trace(sandbox.id, export_run_id, export_trace)
        except Exception:
            logger.exception("initial export_trace write failed")
        if export_task_validation.status != "pass":
            # Soft gate only — never hard-return on static TaskSpec clarification/error
            if should_hard_stop_task_spec(export_task_validation.status):
                final = format_task_spec_validation_reply(export_task_validation)
                step = {
                    "type": "info",
                    "action": "task_spec_validation",
                    "title": "TaskSpec 校验",
                    "status": export_task_validation.status,
                    "content": (final or "")[:800],
                }
                await hub.publish(key, {"type": "step", "op": "append", "index": 0, "step": step})
                meta = json.dumps({
                    "steps": [step],
                    "step_count": 1,
                    "saved_paths": [],
                    "task_spec_validation": export_task_validation.to_dict(),
                }, ensure_ascii=False)
                db.add(ChatMessage(
                    agent_id=agent.id,
                    session_id=session_id,
                    role="assistant",
                    content=final,
                    meta=meta,
                    created_at=now_str(),
                ))
                db.commit()
                await hub.publish(key, {
                    "type": "done",
                    "content": (final or "")[:500],
                    "content_truncated": len(final or "") > 500,
                    "workplace_changed": False,
                })
                _running[key] = False
                return final
            step = {
                "type": "info",
                "action": "task_spec_validation",
                "title": "TaskSpec 校验（继续执行）",
                "status": export_task_validation.status,
                "content": (
                    "；".join(i.message for i in export_task_validation.issues[:5])
                    or str(export_task_validation.status)
                )[:800],
            }
            await hub.publish(key, {"type": "step", "op": "append", "index": 0, "step": step})
            run_steps.append(step)

    # Multi-resource exports never fall through to the legacy role/page loop.
    # Missing capabilities are execution-contract failures, not business rules.
    if export_like and export_view_mode != "single_view":
        missing_capabilities: list[str] = []
        if export_task_spec is None:
            missing_capabilities.append("TaskSpec")
        if llm is None and not followup_sql:
            missing_capabilities.append("LLM")
        if not mcp_ids:
            missing_capabilities.append("MCP binding")
        if sandbox is None:
            missing_capabilities.append("workplace")
        if missing_capabilities:
            reason = "缺少 CTE 执行依赖: " + ", ".join(missing_capabilities)
            final = "本轮未执行数据导出。\n\n原因：" + reason
            visible_steps = [{
                "type": "info",
                "action": "query_contract_preflight",
                "title": "QueryContract 无法执行",
                "status": "error",
                "content": reason,
            }]
            if sandbox is not None:
                export_trace.query_contract = {
                    "status": "failed",
                    "error": reason,
                    "output_columns": list(_hdrs),
                }
                export_trace.export_contract["query_contract"] = dict(
                    export_trace.query_contract,
                )
                export_trace.export_contract["query_graph"] = []
                export_trace.query_graph = []
                write_export_trace(sandbox.id, export_run_id, export_trace)
                _write_export_run_state(
                    sandbox,
                    export_run_id,
                    phase="finalize",
                    mcp_query_count=0,
                    budget=0,
                    todos=export_todos,
                    source_brief=export_source_brief or _todo_brief or task_brief,
                    task_title=export_task_title,
                    time_window=export_time_window,
                    analyze_columns=_hdrs,
                    completeness="query_failed",
                    has_task_data=False,
                    session_id=session_id or "",
                    user_intent=export_turn_intent or "",
                    turn_digest=export_turn_digest or "",
                    export_contract=export_trace.export_contract,
                    column_plan=export_column_plan,
                    schema_discovery=export_trace.schema_discovery,
                )
            meta = json.dumps({
                "steps": visible_steps,
                "step_count": 1,
                "saved_paths": [],
                "query_contract": export_trace.query_contract,
                "export_run_id": export_run_id,
            }, ensure_ascii=False)
            db.add(ChatMessage(
                agent_id=agent.id,
                session_id=session_id,
                role="assistant",
                content=final,
                meta=meta,
                created_at=now_str(),
            ))
            db.commit()
            await hub.publish(key, {
                "type": "done",
                "content": final[:500],
                "content_truncated": False,
                "workplace_changed": bool(sandbox),
            })
            _running[key] = False
            return final

    # Multi-resource exports use one MCP-evidenced CTE contract. ColumnPlan is
    # presentation/verification metadata only; it no longer authorizes queries.
    if (
        export_like
        and export_view_mode != "single_view"
        and export_task_spec is not None
        and (llm is not None or bool(followup_sql))
        and mcp_ids
        and sandbox is not None
    ):
        context_continuity = bool(
            export_repair_prior_state
            and turn_intent.task_relation in ("continue", "revise")
        )
        cte_progress_percent = 8
        cte_attempt_artifacts: list[dict[str, Any]] = []
        last_attempt_sql_fingerprint = ""
        cte_progress_step = {
            "type": "tool",
            "action": "mcp_tool_call",
            "title": "正在连接数据工具",
            "status": "running",
            "progress_percent": cte_progress_percent,
            "task_relation": turn_intent.task_relation,
            "context_continuity": context_continuity,
            "export_run_id": export_run_id,
            "task_title": export_task_title,
        }
        await _push_step(cte_progress_step)
        cte_progress_index = len(run_steps) - 1

        stage_titles = {
            "catalog_list": "正在获取数据资源目录",
            "resource_select": "正在理解需求并选择数据资源",
            "schema_describe": "正在读取资源字段与备注",
            "sql_compose": "正在生成并校验查询语句",
            "method_compose": "正在生成并审计逐列统计方法",
            "query_execute": "正在执行数据查询",
            "file_write": "正在生成并验证 Excel",
            "attempt_retry": "本轮执行失败，正在准备下一轮修复",
        }

        async def _patch_cte_progress(
            title: str,
            *,
            status: str = "running",
            content: str = "",
            progress_percent: int | None = None,
        ) -> None:
            nonlocal cte_progress_percent
            if progress_percent is not None:
                cte_progress_percent = max(
                    cte_progress_percent,
                    min(100, max(0, int(progress_percent))),
                )
            updates = {
                "title": title,
                "status": status,
                "progress_percent": cte_progress_percent,
                "context_available_percent": context_available_percent,
                "task_relation": turn_intent.task_relation,
                "context_continuity": context_continuity,
                "export_run_id": export_run_id,
                "task_title": export_task_title,
            }
            if content:
                updates["content"] = content[:500]
            elif status != "error":
                updates["content"] = ""
            run_steps[cte_progress_index].update(updates)
            await hub.publish(key, {
                "type": "step",
                "op": "patch",
                "index": cte_progress_index,
                "step": updates,
            })

        async def _cte_progress(stage: str, detail: dict[str, Any]) -> None:
            nonlocal last_attempt_sql_fingerprint
            if stage == "attempt_checkpoint":
                attempt = int(detail.get("attempt") or 0)
                total = int(detail.get("total") or max_iters)
                contract = detail.get("contract")
                contract = contract if isinstance(contract, dict) else {}
                sql = str(contract.get("sql") or "").strip()
                sql_fingerprint = (
                    hashlib.sha256(sql.encode("utf-8")).hexdigest() if sql else ""
                )
                sql_changed = bool(
                    sql_fingerprint
                    and last_attempt_sql_fingerprint
                    and sql_fingerprint != last_attempt_sql_fingerprint
                )
                sql_unchanged = bool(
                    sql_fingerprint
                    and last_attempt_sql_fingerprint == sql_fingerprint
                )
                if sql_fingerprint:
                    last_attempt_sql_fingerprint = sql_fingerprint
                artifact_base = f"task/{export_run_id}/attempts/attempt_{attempt:03d}"
                sql_rel = f"{artifact_base}.sql" if sql else ""
                json_rel = f"{artifact_base}.json"
                if sql_rel:
                    _write_workplace(sandbox, sql_rel, sql + "\n")
                artifact = {
                    "attempt": attempt,
                    "total": total,
                    "state": str(detail.get("state") or ""),
                    "stage": str(detail.get("failed_stage") or "completed"),
                    "error": str(detail.get("error") or "")[:4000],
                    "row_count": detail.get("row_count"),
                    "sql_fingerprint": sql_fingerprint,
                    "sql_changed_from_previous": sql_changed,
                    "sql_unchanged_from_previous": sql_unchanged,
                    "sql_path": sql_rel,
                    "contract": contract,
                }
                _write_workplace(
                    sandbox,
                    json_rel,
                    json.dumps(artifact, ensure_ascii=False, indent=2),
                )
                cte_attempt_artifacts.append({
                    "attempt": attempt,
                    "json_path": json_rel,
                    "sql_path": sql_rel,
                    "sql_fingerprint": sql_fingerprint,
                    "sql_changed_from_previous": sql_changed,
                    "sql_unchanged_from_previous": sql_unchanged,
                })
                if sql_changed:
                    change_label = "SQL 已变化"
                elif sql_unchanged:
                    change_label = "SQL 未变化"
                elif sql_fingerprint:
                    change_label = "已生成首版 SQL"
                else:
                    change_label = "尚未生成 SQL"
                state_label = "成功" if detail.get("state") == "succeeded" else "未通过"
                await _push_step({
                    "type": "checkpoint",
                    "action": "cte_attempt",
                    "title": f"第 {attempt}/{total} 轮{state_label} · {change_label}",
                    "status": "done",
                    "content": str(detail.get("error") or "")[:500],
                    "checkpoint_path": json_rel,
                    "sql_checkpoint_path": sql_rel,
                    "progress_percent": cte_progress_percent,
                    "export_run_id": export_run_id,
                    "task_title": export_task_title,
                })
                return
            title = stage_titles.get(stage, "正在处理数据任务")
            current = int(detail.get("current") or 0)
            total = int(detail.get("total") or 0)
            elapsed = int(detail.get("elapsed_seconds") or 0)
            progress_percent = {
                "catalog_list": 14,
                "resource_select": 24,
                "schema_describe": 30,
                "sql_compose": 50,
                "method_compose": 60,
                "query_execute": 68,
                "file_write": 94,
            }.get(stage, cte_progress_percent)
            if stage == "schema_describe" and current and total:
                title += f"（{current}/{total}）"
                progress_percent = 30 + round(16 * current / total)
            elif stage == "query_execute" and detail.get("phase") == "count":
                title = "正在统计待导出的总行数"
                progress_percent = 68
            elif stage == "query_execute" and detail.get("phase") == "page":
                title = f"正在分页获取全部数据（{current}/{total}）"
                if total > 0:
                    progress_percent = 72 + round(18 * min(current, total) / total)
            elif stage == "attempt_retry":
                attempt = int(detail.get("attempt") or 0)
                limit = int(detail.get("total") or max_iters)
                if detail.get("state") == "exhausted":
                    title = f"执行轮次已用完（{attempt}/{limit}）"
                else:
                    title = f"第 {attempt}/{limit} 轮失败，正在处理错误并继续"
            if detail.get("state") == "heartbeat" and elapsed > 0:
                title += f"，已等待 {elapsed} 秒"
            await _patch_cte_progress(
                title,
                content=str(detail.get("error") or "")[:500],
                progress_percent=progress_percent,
            )

        bound_tools: list[dict] = []
        mcp_rows: list[MCP] = []
        for mcp_index, mid in enumerate(mcp_ids, 1):
            await _patch_cte_progress(
                f"正在加载 MCP 工具列表（{mcp_index}/{len(mcp_ids)}）",
            )
            mcp_row = db.query(MCP).filter(MCP.id == mid).first()
            if not mcp_row:
                continue
            mcp_rows.append(mcp_row)
            try:
                bound_tools.extend(await _get_mcp_tools_cached(mcp_row))
            except Exception:
                logger.exception("cte tools/list failed mcp=%s", mid)

        async def _call_cte_mcp(tool_name: str, args: dict[str, Any]) -> str:
            last_error = ""
            for mcp_row in mcp_rows:
                try:
                    result_text = await call_mcp_tool(mcp_row, tool_name, args)
                except Exception as ex:
                    last_error = f"{type(ex).__name__}: {ex}"
                    continue
                if result_text and not _is_mcp_tool_failure(result_text):
                    return result_text
                last_error = result_text or last_error
            return last_error or "MCP 调用失败: 未找到可执行该工具的绑定 MCP"

        prior_delivery_snapshot: dict[str, Any] | None = None
        if turn_intent.verification_required and export_repair_prior_state:
            prior_rel = resolve_export_fill_target_rel(
                sandbox.id,
                export_repair_prior_state,
            )
            prior_path = download_path(sandbox.id, prior_rel) if prior_rel else None
            prior_delivery_snapshot = inspect_query_deliverable(prior_path)

        try:
            cte_result = await run_cte_export(
                llm=llm,
                task_spec=export_task_spec.to_dict(),
                tools=bound_tools,
                call_mcp=_call_cte_mcp,
                context_text="\n".join(
                    x for x in [note_content, export_source_brief, user_message]
                    if str(x or "").strip()
                ),
                prior_sql=followup_sql,
                db=db,
                timeout=min(90, int(getattr(agent, "llm_timeout", None) or 60)),
                max_attempts=max_iters,
                on_progress=_cte_progress,
                require_column_evidence=True,
                is_cancelled=lambda: not _running.get(key, False),
            )
        except ChatStopped:
            await _patch_cte_progress(
                "已停止",
                status="error",
                content="用户点击停止",
            )
            final = "[已停止]"
            visible_steps = list(run_steps)
            meta = json.dumps({
                "steps": visible_steps,
                "step_count": len(visible_steps),
                "saved_paths": saved_paths,
                "export_run_id": export_run_id,
                "progress_percent": cte_progress_percent,
                "stopped": True,
            }, ensure_ascii=False)
            db.add(ChatMessage(
                agent_id=agent.id,
                session_id=session_id,
                role="assistant",
                content=final,
                meta=meta,
                created_at=now_str(),
            ))
            db.commit()
            await hub.publish(key, {
                "type": "done",
                "content": final,
                "content_truncated": False,
                "stopped": True,
                "workplace_changed": False,
            })
            _running[key] = False
            return final
        query_contract = cte_result.contract
        if not _hdrs and query_contract.output_columns:
            _hdrs = list(query_contract.output_columns)
        export_trace.query_contract = query_contract.to_dict()
        export_trace.export_contract["query_contract"] = query_contract.to_dict()
        export_trace.export_contract["query_graph"] = []
        export_trace.query_graph = []
        export_trace.cte_attempts_used = cte_result.attempts_used
        export_trace.cte_attempt_limit = cte_result.attempt_limit
        export_trace.cte_attempt_errors = [dict(row) for row in (cte_result.attempt_errors or [])]
        if cte_result.schemas:
            for resource, schema in cte_result.schemas.items():
                export_trace.record_ads_view_schema(resource, schema)

        sql_rel = ""
        draft_sql_rel = ""
        if query_contract.sql and is_executable_query_sql(query_contract.sql):
            draft_sql_rel = f"task/{export_run_id}/draft_sql.sql"
            draft_sql_write = _write_workplace(sandbox, draft_sql_rel, query_contract.sql + "\n")
            if not draft_sql_write.startswith(("非法", "禁止")):
                saved_paths.append(draft_sql_rel)
            else:
                draft_sql_rel = ""
        if (
            query_contract.sql
            and cte_result.validation.ok
            and is_executable_query_sql(query_contract.sql)
            and cte_result.expected_row_count is not None
        ):
            # Keep the model-authored query with this run. Pagination wrappers
            # are execution details and are intentionally not persisted here.
            sql_rel = f"task/{export_run_id}/final_sql.sql"
            sql_write = _write_workplace(sandbox, sql_rel, query_contract.sql + "\n")
            if not sql_write.startswith(("非法", "禁止")):
                saved_paths.append(sql_rel)
            else:
                sql_rel = ""

        query_plan = query_contract_column_plan(query_contract)
        if query_plan:
            export_column_plan = query_plan
            export_trace.column_plan = query_plan
            export_trace.export_contract["column_plan"] = query_plan

        file_rel = ""
        delivery_error = ""
        query_verification: dict[str, Any] = {}
        if cte_result.executed:
            await _cte_progress("file_write", {"state": "running"})
            preferred_name = export_filename_for_title(
                export_task_title,
                run_id=export_run_id,
            )
            file_rel = write_query_result_deliverable(
                sandbox.id,
                cte_result.rows,
                column_headers=_hdrs,
                column_plan=query_plan,
                preferred_name=preferred_name,
                title=export_source_brief or "导出数据",
            ) or ""
            if file_rel:
                delivered = download_path(sandbox.id, file_rel)
                delivered_headers = read_deliverable_headers(delivered) if delivered else []
                delivered_rows = count_data_rows(delivered) if delivered else None
                if (
                    not delivered
                    or not delivered.is_file()
                    or delivered_headers != _hdrs
                    or delivered_rows != len(cte_result.rows)
                ):
                    delivery_error = (
                        "xlsx 交付验证失败: "
                        f"headers={delivered_headers!r}, rows={delivered_rows!r}, "
                        f"expected_headers={_hdrs!r}, expected_rows={len(cte_result.rows)}"
                    )
                    file_rel = ""
                else:
                    query_verification = verify_query_export_delivery(
                        current=inspect_query_deliverable(delivered),
                        requested_columns=_hdrs,
                        expected_row_count=cte_result.expected_row_count,
                        fetched_row_count=len(cte_result.rows),
                        pages_fetched=cte_result.pages_fetched,
                        mcp_call_count=cte_result.mcp_call_count,
                        prior=prior_delivery_snapshot,
                        verification_targets=turn_intent.verification_targets,
                    )
                    if (
                        query_verification.get("status") == "pass"
                        and turn_intent.verification_required
                        and llm is not None
                    ):
                        try:
                            query_verification["diagnosis"] = (
                                await diagnose_query_export_change(
                                    llm,
                                    prior_sql=followup_sql,
                                    current_sql=query_contract.sql,
                                    schemas=cte_result.schemas,
                                    verification=query_verification,
                                    verification_targets=turn_intent.verification_targets,
                                    user_context=user_message,
                                    db=db,
                                    timeout=min(
                                        45,
                                        int(getattr(agent, "llm_timeout", None) or 45),
                                    ),
                                )
                            )
                        except Exception as ex:
                            query_verification["diagnosis"] = {
                                "summary": (
                                    "差异原因生成失败；查询与文件一致性已验证，"
                                    f"但本轮没有足够的因果说明：{type(ex).__name__}: {ex}"
                                ),
                                "changes": [],
                            }
                    if query_verification.get("status") != "pass":
                        delivery_error = "查询与 xlsx 严格验证未通过"
                        file_rel = ""
                    else:
                        saved_paths.append(file_rel)
                        files_written += 1
            export_trace.verification = dict(query_verification)
            export_trace.export_contract["verification"] = dict(query_verification)
            export_trace.record_query(
                key="cte:final",
                view=",".join(query_contract.resources),
                sql=query_contract.sql,
                row_count=len(cte_result.rows),
                status="ok",
            )
            export_trace.set_materialization(
                file_rel=file_rel,
                row_count=len(cte_result.rows),
                headers=_hdrs,
                mode="cte",
                sql_rel=sql_rel or draft_sql_rel,
                cte_attempts_used=cte_result.attempts_used,
                cte_attempt_limit=cte_result.attempt_limit,
            )
        else:
            export_trace.record_query(
                key="cte:final",
                view=",".join(query_contract.resources),
                sql=query_contract.sql,
                error=cte_result.error,
                status="error",
            )
            export_trace.set_materialization(
                file_rel="",
                row_count=None,
                headers=_hdrs,
                mode="cte",
                sql_rel=sql_rel or draft_sql_rel,
                cte_attempts_used=cte_result.attempts_used,
                cte_attempt_limit=cte_result.attempt_limit,
            )
        write_export_trace(sandbox.id, export_run_id, export_trace)
        _write_export_run_state(
            sandbox,
            export_run_id,
            phase="finalize",
            mcp_query_count=cte_result.mcp_call_count,
            budget=max(cte_result.mcp_call_count, 1),
            todos=export_todos,
            deliverable=file_rel,
            fallback=False,
            source_brief=export_source_brief or _todo_brief or task_brief,
            task_title=export_task_title,
            time_window=export_time_window,
            analyze_columns=_hdrs,
            deliverable_rows=len(cte_result.rows) if cte_result.executed else None,
            completeness=(
                "complete"
                if cte_result.executed and file_rel and not delivery_error
                else "query_failed"
            ),
            has_task_data=cte_result.executed,
            session_id=session_id or "",
            user_intent=export_turn_intent or "",
            turn_digest=export_turn_digest or "",
            export_contract=export_trace.export_contract,
            column_plan=export_column_plan,
            schema_discovery=export_trace.schema_discovery,
            cte_attempts_used=cte_result.attempts_used,
            cte_attempt_limit=cte_result.attempt_limit,
            cte_attempt_errors=cte_result.attempt_errors,
        )

        sql_block = format_query_contract_sql(query_contract)
        method_block = format_query_contract_methods(query_contract)
        verification_block = format_query_export_verification(query_verification)
        if cte_result.executed and file_rel and not delivery_error:
            await _patch_cte_progress(
                f"数据查询与 Excel 导出完成，共 {len(cte_result.rows)} 行",
                status="done",
                progress_percent=100,
            )
            final = (
                f"查询与导出已完成，共 {len(cte_result.rows)} 行。\n\n"
                f"### 下载文件\n\n- `{file_rel}`"
                + (f"\n- `{sql_rel}`" if sql_rel else "")
                + f"\n\n{method_block}"
                + f"\n\n{verification_block}"
                + f"\n\n{sql_block}"
            )
        else:
            result_error = delivery_error or cte_result.error
            await _patch_cte_progress(
                "数据查询或文件导出未完成",
                status="error",
                content=str(result_error or "未知错误"),
            )
            if sql_rel:
                final = (
                    "CTE SQL 已生成，但本轮未完成数据文件。\n\n"
                    + (f"### SQL 文件\n\n- `{sql_rel}`\n\n" if sql_rel else "")
                    + (f"错误：{result_error}\n\n" if result_error else "")
                    + sql_block
                ).strip()
            elif draft_sql_rel:
                final = (
                    "本轮未完成最终导出，但已保留当前 SQL 草稿。\n\n"
                    + f"### SQL 文件\n\n- `{draft_sql_rel}`\n\n"
                    + (f"错误：{result_error}\n\n" if result_error else "")
                    + sql_block
                ).strip()
            else:
                final = (
                    "本轮未生成可用的 CTE SQL，因此没有 SQL 或数据文件可下载。\n\n"
                    + (f"错误：{result_error}" if result_error else "")
                ).strip()
        visible_steps = list(run_steps)
        meta = json.dumps({
            "steps": visible_steps,
            "step_count": len(visible_steps),
            "saved_paths": saved_paths,
            "query_contract": query_contract.to_dict(),
            "verification": query_verification,
            "cte_attempts_used": cte_result.attempts_used,
            "cte_attempt_limit": cte_result.attempt_limit,
            "cte_attempt_errors": cte_result.attempt_errors,
            "export_run_id": export_run_id,
            "progress_percent": cte_progress_percent,
            "context_available_percent": context_available_percent,
            "task_relation": turn_intent.task_relation,
            "context_continuity": context_continuity,
            "task_title": export_task_title,
            "attempt_artifact_count": len(cte_attempt_artifacts),
            "latest_attempt_artifact": (
                cte_attempt_artifacts[-1] if cte_attempt_artifacts else {}
            ),
        }, ensure_ascii=False)
        db.add(ChatMessage(
            agent_id=agent.id,
            session_id=session_id,
            role="assistant",
            content=final,
            meta=meta,
            created_at=now_str(),
        ))
        db.commit()
        await hub.publish(key, {
            "type": "done",
            "content": final[:500],
            "content_truncated": len(final) > 500,
            "workplace_changed": bool(file_rel or sql_rel),
        })
        _running[key] = False
        return final

    # Ambiguous brief: inject clarify hint — do not hard-stop
    if export_like and export_view_mode != "single_view":
        _clarify_q = needs_export_clarify(
            time_window=export_time_window,
            column_headers=_export_analyze_column_texts(export_todos),
            user_message=user_message,
        )
        if _clarify_q and export_turn_intent not in ("repair", "feedback", "window_change"):
            if not export_time_window or (
                not _export_analyze_column_texts(export_todos)
                and _count_numbered_cols(user_message) < 2
            ):
                messages.append({
                    "role": "system",
                    "content": (
                        "【导出对齐提示·非硬停】"
                        + _clarify_q
                        + " 若已有意图时间窗或可通过 MCP 查数，请继续工具调用；"
                        "不要只回复澄清问题就结束。"
                    ),
                })
                step = {
                    "type": "info",
                    "action": "export_clarify",
                    "title": "对齐需求（继续执行）",
                    "status": "done",
                    "content": (_clarify_q or "")[:800],
                }
                await hub.publish(
                    key, {"type": "step", "op": "append", "index": 0, "step": step},
                )
                run_steps.append(step)

    # Type-B / adaptive budget from prior truncation (cross-run improvement)
    if export_like:
        _is_type_b = _is_type_b_export(export_view_mode, export_column_plan)
        _prior_state: dict | None = None
        if sandbox:
            try:
                _found = find_latest_run_state(
                    sandbox.id,
                    exclude_run_id=export_run_id,
                    session_id=session_id or "",
                )
                if _found:
                    _prior_state = _found[1]
            except Exception:
                _prior_state = None
        if export_repair_prior_state and isinstance(export_repair_prior_state, dict):
            _prior_state = export_repair_prior_state
        # Refine intent against budget prior when not already repair-classified
        if export_turn_intent in ("unknown", "new_export") and _prior_state:
            export_turn_intent = _export_turn_state_from_intent(
                turn_intent,
                resolved_tw=export_time_window,
                prior_state=_prior_state,
            )
            export_turn_digest = _build_turn_digest(
                user_message,
                intent=export_turn_intent,
                time_window=export_time_window,
            )
        tw_label = str((export_time_window or {}).get("label") or "")
        _pay_cohort = bool(
            (export_time_window or {}).get("cohort") == "pay"
            or _is_pay_cohort_brief(export_source_brief or _todo_brief or task_brief)
        )
        # Type-B full-fetch: short-page gate + core retries (vs fail-fast force-write)
        export_full_fetch = bool(_is_type_b)
        _same_sess_prior = True
        if isinstance(_prior_state, dict):
            ps = str(_prior_state.get("session_id") or "").strip()
            _same_sess_prior = not ps or ps == (session_id or "").strip()
        _allow_prior_mcp = (
            export_turn_intent == "repair" and _same_sess_prior
        )
        # window_change / new_export with explicit window: never inject old OFFSET SQL
        if export_turn_intent in ("window_change", "new_export"):
            _allow_prior_mcp = False
        mcp_query_budget, export_prior_fetch_hint = _compute_adaptive_export_budget(
            type_b=_is_type_b,
            prior_state=_prior_state if _same_sess_prior else None,
            current_tw_label=tw_label,
            current_tw=export_time_window,
            pay_cohort=_pay_cohort,
            allow_prior_mcp_examples=_allow_prior_mcp,
        )
        # Soft learning hint from cross-session prior without TW inheritance
        if not _same_sess_prior and _prior_state and not export_prior_fetch_hint:
            learn = _prior_state_learning_hint(_prior_state)
            if learn:
                export_prior_fetch_hint = learn
        export_budget_cap = (
            _EXPORT_QUERY_BUDGET_TYPE_B_MAX if _is_type_b else _EXPORT_QUERY_BUDGET_DEFAULT
        )
        if _pay_cohort and _is_type_b:
            export_budget_cap = max(export_budget_cap, _EXPORT_QUERY_BUDGET_TYPE_B_PAY_MAX)
        export_budget_cap = max(export_budget_cap, mcp_query_budget)
        _append_progress(
            progress_lines,
            f"query预算={mcp_query_budget} cap={export_budget_cap}"
            + (" typeB" if _is_type_b else "")
            + (" payCohort" if _pay_cohort else "")
            + (" fullFetch" if export_full_fetch else "")
            + (f" intent={export_turn_intent}" if export_turn_intent else ""),
        )
    else:
        _pay_cohort = False
        export_full_fetch = False

    export_cohort_uid_estimate: int | None = None
    export_fact_page_cap = (
        _EXPORT_MAX_PAGES_PER_VIEW_PAY_COHORT
        if _pay_cohort
        else _EXPORT_MAX_PAGES_PER_VIEW
    )
    export_user_page_cap = _EXPORT_MAX_PAGES_USER
    if export_like:
        export_cohort_uid_estimate = _parse_cohort_uid_estimate(
            export_source_brief or "",
            _todo_brief or "",
            task_brief or "",
            user_message or "",
        )
        if export_cohort_uid_estimate is None and isinstance(_prior_state, dict):
            try:
                pe = _prior_state.get("cohort_uid_estimate")
                if pe is not None:
                    export_cohort_uid_estimate = int(pe)
            except (TypeError, ValueError, AttributeError):
                pass
        export_fact_page_cap = _compute_fact_page_cap(
            pay_cohort=_pay_cohort,
            cohort_uid_estimate=export_cohort_uid_estimate,
        )
        export_user_page_cap = _compute_user_page_cap(export_cohort_uid_estimate)
        # Prior incomplete run → raise fact page cap for deeper OFFSET continue
        if isinstance(_prior_state, dict):
            prior_comp = str(_prior_state.get("completeness") or "").strip()
            if prior_comp in ("truncated", "fact_starved", "iters_exhausted"):
                try:
                    prior_cap = int(_prior_state.get("fact_page_cap") or 0)
                except (TypeError, ValueError):
                    prior_cap = 0
                export_fact_page_cap = max(
                    export_fact_page_cap,
                    prior_cap,
                    export_fact_page_cap + 2,
                )
                export_fact_page_cap = min(20, export_fact_page_cap)
        if _is_type_b:
            bumped = _compute_full_fetch_budget(
                type_b=True,
                pay_cohort=_pay_cohort,
                fact_page_cap=export_fact_page_cap,
                current=mcp_query_budget,
                user_page_cap=export_user_page_cap,
            )
            mcp_query_budget = bumped
            export_budget_cap = max(
                export_budget_cap,
                bumped,
                _EXPORT_QUERY_BUDGET_FULL_FETCH_MAX if export_cohort_uid_estimate else export_budget_cap,
            )
            export_budget_cap = min(
                _EXPORT_QUERY_BUDGET_FULL_FETCH_MAX,
                max(export_budget_cap, mcp_query_budget),
            )
        if export_cohort_uid_estimate:
            _append_progress(
                progress_lines,
                f"cohort目标uid≈{export_cohort_uid_estimate} "
                f"用户页帽={export_user_page_cap} "
                f"事实页帽={export_fact_page_cap} 预算={mcp_query_budget}",
            )
        else:
            _append_progress(
                progress_lines,
                f"用户页帽={export_user_page_cap} "
                f"事实页帽={export_fact_page_cap}（待 COUNT）预算={mcp_query_budget}",
            )

    # Always-on task anchor: full columns + time window (soft context, not a gate)
    if export_like:
        _anchor = _format_export_task_anchor(
            source_brief=export_source_brief or _todo_brief or task_brief,
            time_window=export_time_window,
            column_headers=_export_analyze_column_texts(export_todos),
            target_roles=export_target_roles,
        )
        messages.append({"role": "system", "content": _anchor})
        if export_prior_fetch_hint:
            messages.append({"role": "system", "content": export_prior_fetch_hint})
        else:
            _learn = _prior_state_learning_hint(_prior_state)
            if _learn:
                messages.append({"role": "system", "content": _learn})
        _append_progress(
            progress_lines,
            f"任务锚点已注入（{len(_export_analyze_column_texts(export_todos))} 列）",
        )

    def _coach_nudge(extra: str = "") -> str:
        """Soft next-action coach from live coverage (never blocks tools)."""
        covered_now: list[str] = []
        missing_now: list[str] = []
        if sandbox and export_run_id and export_like:
            covered_now = sorted(_covered_export_roles(sandbox.id, export_run_id))
            missing_now = _missing_export_roles(
            sandbox.id,
            export_run_id,
            export_target_roles,
            abandoned_roles=export_abandoned_roles,
        )
        fact_c = _fact_need_continue_now() if export_like else []
        body = _export_next_action_coach(
            phase=export_phase or "init",
            fetched_view_pages=fetched_view_pages,
            target_roles=export_target_roles,
            covered_roles=covered_now,
            missing_roles=missing_now,
            time_window=export_time_window,
            column_headers=_export_analyze_column_texts(export_todos),
            budget_left=mcp_query_budget - mcp_query_count,
            dim_views=export_dim_views or None,
            user_fetch_complete=user_fetch_complete,
            fact_need_continue=fact_c,
            cohort_uid_estimate=export_cohort_uid_estimate,
            fact_page_cap=export_fact_page_cap,
        )
        trunc_now = _fact_trunc_now() if export_like else []
        if (
            _pay_cohort
            and trunc_now
            and (mcp_query_budget - mcp_query_count) > 0
        ):
            body += (
                "\n【行数完整性·充值用户】明细仍满页截断："
                + "、".join(trunc_now)
                + f"；页帽≤{export_fact_page_cap}，"
                "请同窗 OFFSET/续页至短页后再 SHELL（勿浅拉即 FINAL）。"
            )
            if export_cohort_uid_estimate:
                body += f" 目标 cohort uid≈{export_cohort_uid_estimate}。"
        if extra:
            return extra.rstrip() + "\n\n" + body
        return body

    def _plan_hint_with_anchor() -> str:
        base = _adapt_export_prompt_for_capabilities(
            (task_policy.plan_hint or _EXPORT_PLAN_HINT).rstrip(),
            shell_enabled=shell_enabled,
            session_id=session_id,
        )
        cols = _export_analyze_column_texts(export_todos)
        bits = [base]
        if cols:
            bits.append(
                "【本轮输出列】请在 PLAN「输出列」中逐条抄写（完整表述，含括号）："
            )
            for i, c in enumerate(cols, 1):
                bits.append(f"{i}.{c}")
        if export_time_window and export_time_window.get("label"):
            bits.append(
                f"【字段与筛选】时间窗必须写：{export_time_window.get('label')}；"
                f"user_info 用 sql 过滤 register_time。"
            )
        bits.append(
            "PLAN 完成后请立即按【下一步建议】顺序 MCP query，勿散文收工。"
        )
        bits.append(_coach_nudge())
        return "\n".join(bits)

    def _refresh_todo_progress() -> None:
        # Drop previous TODO block lines then append fresh
        nonlocal progress_lines
        progress_lines[:] = [ln for ln in progress_lines if not (
            ln == "TODO:" or ln.startswith("  [") or ln.startswith("  进度 ")
        )]
        progress_lines.extend(_format_todo_progress_lines(export_todos))
        if len(progress_lines) > _PROGRESS_MAX_LINES:
            del progress_lines[:-_PROGRESS_MAX_LINES]
        _upsert_progress_message(messages, progress_lines)

    def _hydrate_landed_page_state() -> None:
        nonlocal mcp_query_count, user_fetch_complete
        if not export_like or not sandbox or not export_run_id:
            return
        pages, last_rows, max_page = _summarize_run_page_state(
            sandbox.id,
            export_run_id,
        )
        if not pages:
            return
        for view, count in pages.items():
            fetched_view_pages[view] = max(
                int(fetched_view_pages.get(view, 0) or 0),
                int(count or 0),
            )
        for view, rows in last_rows.items():
            fetched_view_last_rows[view] = int(rows or 0)
        if max_page > mcp_query_count:
            mcp_query_count = max_page
        user_views = [v for v in pages if _is_user_info_view(v)]
        if user_views and any(
            _is_short_page_complete(fetched_view_last_rows.get(v, 0), view=v)
            for v in user_views
        ):
            user_fetch_complete = True

    def _sync_export_trace_progress(*, write: bool = False) -> None:
        _hydrate_landed_page_state()
        if not export_like or not sandbox or not export_run_id:
            return
        try:
            export_trace.failed_views = sorted(export_failed_views)
            export_trace.abandoned_roles = sorted(export_abandoned_roles)
            export_trace.done_node_keys = sorted(export_done_node_keys)
            export_trace.fetched_view_pages = dict(fetched_view_pages)
            if isinstance(export_trace.export_contract, dict):
                export_trace.export_contract["cohort_uid_estimate"] = (
                    export_cohort_uid_estimate
                )
            if isinstance(export_trace.task_spec, dict):
                export_trace.task_spec["cohort_uid_estimate"] = export_cohort_uid_estimate
            if write:
                write_export_trace(sandbox.id, export_run_id, export_trace)
        except Exception:
            logger.exception("sync export trace progress failed")

    def _persist_run_state(
        deliverable: str = "",
        fallback: bool = False,
        *,
        completeness_force: str = "",
    ) -> None:
        _hydrate_landed_page_state()
        _sync_export_trace_progress()
        covered_now: list[str] = []
        missing_now: list[str] = []
        if sandbox and export_run_id and export_like:
            covered_now = sorted(
                _covered_export_roles(sandbox.id, export_run_id)
            )
            missing_now = _missing_export_roles(
            sandbox.id,
            export_run_id,
            export_target_roles,
            abandoned_roles=export_abandoned_roles,
        )
        user_pages_now = sum(
            n for v, n in fetched_view_pages.items() if _is_user_info_view(v)
        )
        user_at_cap = user_pages_now >= export_user_page_cap
        user_last_full = any(
            _is_full_page_rows(fetched_view_last_rows.get(v, 0), view=v)
            for v in fetched_view_pages
            if _is_user_info_view(v)
        )
        user_trunc = bool(user_at_cap and (not user_fetch_complete or user_last_full))
        fact_trunc: list[str] = []
        for role in ("pay", "cash", "bet"):
            if role not in (export_target_roles or []):
                continue
            role_views = [v for v in fetched_view_pages if _view_category(v) == role]
            if not role_views:
                continue
            last_full = any(
                _is_full_page_rows(fetched_view_last_rows.get(v, 0), view=v)
                for v in role_views
            )
            if last_full:
                fact_trunc.append(role)
        deliv_rows: int | None = None
        if deliverable and sandbox:
            dp = download_path(sandbox.id, deliverable)
            if dp and dp.is_file():
                deliv_rows = count_data_rows(dp)
        has_task = bool(
            sandbox and task_has_exportable_data(sandbox.id)
        ) or sum(int(n or 0) for n in fetched_view_pages.values()) > 0
        need_cont = _fact_roles_needing_continue(
            export_target_roles,
            fetched_view_pages,
            fetched_view_last_rows,
            budget_left=max(0, mcp_query_budget - mcp_query_count),
            max_fact_pages=export_fact_page_cap,
            pay_cohort=_pay_cohort,
            full_fetch=export_full_fetch,
        )
        if not need_cont:
            need_cont = list(fact_trunc)
        comp = completeness_force or _derive_export_completeness(
            missing_roles=missing_now,
            fact_truncated_roles=fact_trunc,
            user_truncated=user_trunc,
            user_fetch_complete=user_fetch_complete,
            deliverable=deliverable,
            fallback=fallback,
            deliverable_rows=deliv_rows,
            cohort_uid_estimate=export_cohort_uid_estimate,
            fetched_view_pages=fetched_view_pages,
            has_task_data=has_task,
        )
        _write_export_run_state(
            sandbox,
            export_run_id,
            phase=export_phase or "init",
            mcp_query_count=mcp_query_count,
            budget=mcp_query_budget,
            todos=export_todos,
            deliverable=deliverable,
            fallback=fallback,
            covered_roles=covered_now,
            missing_roles=missing_now,
            fetched_view_pages=fetched_view_pages,
            fetched_view_last_rows=fetched_view_last_rows,
            mcp_failures=_summarize_mcp_failures(
                mcp_class_fails, mcp_class_samples, mcp_class_tools,
            ),
            source_brief=export_source_brief or _todo_brief or task_brief,
            task_title=export_task_title,
            time_window=export_time_window,
            target_roles=export_target_roles,
            analyze_columns=[
                str(t.get("text") or "").strip()
                for t in export_todos
                if t.get("phase") == "analyze" and str(t.get("text") or "").strip()
            ],
            user_fetch_complete=user_fetch_complete,
            user_pages=user_pages_now,
            user_truncated=user_trunc,
            fact_truncated_roles=fact_trunc,
            deliverable_rows=deliv_rows,
            cohort_uid_estimate=export_cohort_uid_estimate,
            fact_page_cap=export_fact_page_cap,
            need_continue_roles=need_cont,
            completeness=comp,
            has_task_data=has_task,
            session_id=session_id or "",
            user_intent=export_turn_intent or "",
            turn_digest=export_turn_digest or "",
            constraints=_build_export_constraints(
                time_window=export_time_window,
                target_roles=export_target_roles,
                source_brief=export_source_brief or _todo_brief or task_brief or "",
            ),
            export_contract=(
                export_trace.export_contract
                if isinstance(getattr(export_trace, "export_contract", None), dict)
                else None
            ),
            column_plan=export_column_plan,
            repair_plan=(
                export_trace.repair_plan
                if isinstance(getattr(export_trace, "repair_plan", None), dict)
                else None
            ),
            schema_discovery=(
                export_trace.schema_discovery
                if isinstance(getattr(export_trace, "schema_discovery", None), dict)
                else None
            ),
            done_node_keys=sorted(export_done_node_keys),
            failed_views=sorted(export_failed_views),
        )

    def _sync_phase(reason: str = "") -> None:
        logger.info(
            "export_phase=%s run=%s queries=%s/%s reason=%s",
            export_phase, export_run_id, mcp_query_count, mcp_query_budget, reason or "",
        )
        if reason:
            _append_progress(progress_lines, f"[{export_phase}] {reason}")
        _append_progress(
            progress_lines,
            _format_llm_mcp_progress(
                llm_iter=0,
                max_iters=max_iters,
                mcp_query_count=mcp_query_count,
                mcp_budget=mcp_query_budget,
            ).replace("LLM 0/", "LLM ·/"),
        )
        _refresh_todo_progress()
        _persist_run_state()

    # Concurrent runs share workplace: never quarantine root deliverables on start
    # (would steal sibling-run files). Ownership is enforced at finish via run_id/mtime.
    if export_like and sandbox:
        _refresh_todo_progress()
        _persist_run_state()
    _upsert_progress_message(messages, progress_lines)

    def _enter_export_plan(reason: str = "") -> None:
        nonlocal export_phase, plan_hint_injected
        if not export_like or plan_done or export_phase in ("plan", "fetch", "analyze", "finalize"):
            return
        export_phase = "plan"
        plan_hint_injected = False
        _mark_todos_phase(export_todos, "discover", True)
        sync_reason = reason or "进入 PLAN"
        if "超时" in sync_reason or "样例" in sync_reason or "发现" in sync_reason:
            sync_reason = (
                sync_reason
                + "；若尚未拉齐数据，PLAN 后请立即按任务锚点/下一步建议开始 query"
            )
        _sync_phase(sync_reason)

    def _enter_export_fetch(budget: int, reason: str = "") -> None:
        nonlocal export_phase, plan_done, mcp_query_budget, export_todos
        nonlocal export_budget_cap
        plan_done = True
        type_b_now = _is_type_b_export(export_view_mode, export_column_plan)
        mcp_query_budget, export_budget_cap = _normalize_export_fetch_budget(
            requested_budget=int(budget or 0),
            current_cap=export_budget_cap,
            type_b=type_b_now,
            pay_cohort=_pay_cohort,
            full_fetch=export_full_fetch or type_b_now,
            fact_page_cap=export_fact_page_cap,
            user_page_cap=export_user_page_cap,
        )
        export_phase = "fetch"
        _mark_todos_phase(export_todos, "discover", True)
        _sync_phase(reason or f"PLAN 完成，query 预算 {mcp_query_budget}")

    def _dim_views_hint() -> str:
        if not export_dim_views:
            return (
                " channel/game 请从 list_ads_views 中选「用户渠道/游戏配置」类视图各 query 1 页。"
            )
        bits = [
            f"{r}=`{export_dim_views[r]}`"
            for r in ("channel", "game")
            if export_dim_views.get(r)
        ]
        if not bits:
            return ""
        return " 维表视图：" + "，".join(bits) + "。"

    def _missing_dim_roles() -> list[str]:
        if not sandbox:
            return ["channel", "game"]
        missing = _missing_export_roles(
            sandbox.id,
            export_run_id,
            export_target_roles,
            abandoned_roles=export_abandoned_roles,
        )
        return [r for r in missing if r in ("channel", "game")]

    def _force_dim_fetch_message(dim_miss: list[str] | None = None) -> str:
        """Soft nudge for declared dim gaps — never hard-stall analyze."""
        miss = dim_miss if dim_miss is not None else _missing_dim_roles()
        if not miss:
            return ""
        lines = [
            "【建议维表】列计划仍声明 channel/game 时，可各补 1 页以填渠道/游戏名；"
            "缺维表也可进分析（对应列可空）。优先按绑定/维表映射 query：",
        ]
        for r in miss:
            v = export_dim_views.get(r)
            if v:
                lines.append(
                    f'- {r}: MCP: query_ads_view {{"view":"{v}","limit":2000}}'
                )
            else:
                lines.append(
                    f"- {r}: list_ads_views 后选配置维表再 query 1 页。"
                )
        lines.append(_dim_views_hint().strip())
        return "\n".join(x for x in lines if x)

    def _is_dim_query_view(view: str) -> bool:
        v = (view or "").strip()
        if not v:
            return False
        if v in export_dim_views.values():
            return True
        return _VIEW_IS_DIM(v)

    def _missing_fact_roles() -> list[str]:
        # Only relative to declared targets — never invent pay/cash/bet
        if not sandbox:
            return [
                r
                for r in export_target_roles
                if r in ("pay", "cash", "bet") and r not in export_abandoned_roles
            ]
        missing = _missing_export_roles(
            sandbox.id,
            export_run_id,
            export_target_roles,
            abandoned_roles=export_abandoned_roles,
        )
        return [r for r in missing if r in ("pay", "cash", "bet")]

    def _fact_need_continue_now() -> list[str]:
        return [
            r
            for r in _export_roles_needing_page_continue(
                export_target_roles,
                fetched_view_pages,
                fetched_view_last_rows,
                budget_left=mcp_query_budget - mcp_query_count,
                fact_page_cap=export_fact_page_cap,
                user_page_cap=export_user_page_cap,
                pay_cohort=_pay_cohort,
                full_fetch=export_full_fetch,
            )
            if r not in export_abandoned_roles
            and not _export_role_pull_blocked(
                r,
                failed_views=export_failed_views,
                abandoned_roles=export_abandoned_roles,
                dim_views=export_dim_views,
            )
        ]

    def _still_full_with_budget() -> bool:
        """True when planned roles still look full-page and auto-OFFSET budget remains."""
        return bool(_fact_need_continue_now())

    def _apply_cohort_estimate_from_count(n: int) -> None:
        """Update estimate + dynamically raise page cap / query budget mid-run."""
        nonlocal export_cohort_uid_estimate, export_fact_page_cap, export_user_page_cap
        nonlocal mcp_query_budget, export_budget_cap
        if n is None or int(n) <= 0:
            return
        n = int(n)
        if export_cohort_uid_estimate and n <= export_cohort_uid_estimate:
            # Keep larger prior estimate
            n = max(n, export_cohort_uid_estimate)
        export_cohort_uid_estimate = n
        new_cap = _compute_fact_page_cap(
            pay_cohort=_pay_cohort,
            cohort_uid_estimate=n,
        )
        export_fact_page_cap = max(export_fact_page_cap, new_cap)
        export_user_page_cap = max(
            export_user_page_cap,
            _compute_user_page_cap(n),
        )
        if _is_type_b:
            bumped = _compute_full_fetch_budget(
                type_b=True,
                pay_cohort=_pay_cohort,
                fact_page_cap=export_fact_page_cap,
                current=mcp_query_budget,
                user_page_cap=export_user_page_cap,
            )
            if bumped > mcp_query_budget:
                mcp_query_budget = bumped
            export_budget_cap = min(
                _EXPORT_QUERY_BUDGET_FULL_FETCH_MAX,
                max(export_budget_cap, mcp_query_budget),
            )
        _append_progress(
            progress_lines,
            f"COUNT估量≈{n} → 用户页帽={export_user_page_cap} "
            f"事实页帽={export_fact_page_cap} 预算={mcp_query_budget}",
        )

    def _fact_trunc_now() -> list[str]:
        out: list[str] = []
        for role in ("pay", "cash", "bet"):
            if role not in (export_target_roles or []):
                continue
            role_views = [v for v in fetched_view_pages if _view_category(v) == role]
            if not role_views:
                continue
            if any(
                _is_full_page_rows(fetched_view_last_rows.get(v, 0), view=v)
                for v in role_views
            ):
                out.append(role)
        return out

    def _facts_ok_for_delivery(
        missing_roles: list[str] | None = None,
        *,
        deliverable_rows: int | None = None,
    ) -> bool:
        miss = missing_roles
        if miss is None and sandbox and export_run_id:
            miss = _missing_export_roles(
                sandbox.id,
                export_run_id,
                export_target_roles,
                abandoned_roles=export_abandoned_roles,
            )
        else:
            miss = [
                r
                for r in (miss or [])
                if str(r).strip() not in export_abandoned_roles
            ]
        return _facts_ready_for_analyzed_delivery(
            export_target_roles=export_target_roles,
            missing_roles=miss or [],
            fact_truncated_roles=_fact_trunc_now(),
            budget_left=mcp_query_budget - mcp_query_count,
            pay_cohort=_pay_cohort,
            deliverable_rows=deliverable_rows,
            cohort_uid_estimate=export_cohort_uid_estimate,
        )

    def _roles_ready_for_analyze() -> bool:
        """Roles covered + user short-page (or at cap) + facts not mid-full-page."""
        if not sandbox:
            return False
        missing = _missing_export_roles(
            sandbox.id,
            export_run_id,
            export_target_roles,
            abandoned_roles=export_abandoned_roles,
        )
        user_pages = sum(
            n for v, n in fetched_view_pages.items() if _is_user_info_view(v)
        )
        return _export_roles_ready_for_analyze(
            missing_roles=missing,
            has_data=task_has_exportable_data(sandbox.id),
            user_fetch_complete=user_fetch_complete,
            user_pages=user_pages,
            fact_need_continue=_fact_need_continue_now(),
            max_user_pages=export_user_page_cap,
            target_roles=export_target_roles,
        )

    def _full_fetch_write_allowed_now(*, prefer_fallback: bool) -> bool:
        """Strict full_fetch: refuse force-write until short pages or budget+caps."""
        if not export_full_fetch:
            return True
        missing: list[str] = []
        if sandbox and export_run_id:
            missing = _missing_export_roles(
                sandbox.id,
                export_run_id,
                export_target_roles,
                abandoned_roles=export_abandoned_roles,
            )
        short = _full_fetch_short_pages_ready(
            target_roles=export_target_roles,
            user_fetch_complete=user_fetch_complete,
            fact_truncated_roles=_fact_trunc_now(),
            missing_roles=missing,
            abandoned_roles=export_abandoned_roles,
        )
        core_gone = _full_fetch_core_unavailable(
            target_roles=export_target_roles,
            abandoned_roles=export_abandoned_roles,
            pay_cohort=_pay_cohort,
        )
        caps = _full_fetch_page_caps_exhausted(
            target_roles=export_target_roles,
            fetched_view_pages=fetched_view_pages,
            fact_truncated_roles=_fact_trunc_now(),
            abandoned_roles=export_abandoned_roles,
            user_fetch_complete=user_fetch_complete,
            user_page_cap=export_user_page_cap,
            fact_page_cap=export_fact_page_cap,
        )
        ok = _full_fetch_allow_force_write(
            full_fetch=True,
            prefer_fallback=prefer_fallback,
            short_pages_ready=short,
            core_unavailable=core_gone,
            budget_exhausted=mcp_query_count >= mcp_query_budget,
            page_caps_exhausted=caps,
        )
        if not ok:
            if core_gone:
                abort_role = "user" if "user" in export_abandoned_roles else "pay"
                abort_view = _view_for_export_role(
                    abort_role, dim_views=export_dim_views
                )
                _append_progress(
                    progress_lines,
                    "全齐模式：" + _full_fetch_core_abort_message(abort_role, abort_view),
                )
            else:
                _append_progress(
                    progress_lines,
                    "全齐模式：未短页齐套且未触顶预算+页帽，拒绝强制写表",
                )
        return ok

    def _force_fact_fetch_message(fact_miss: list[str] | None = None) -> str:
        miss = fact_miss if fact_miss is not None else _missing_fact_roles()
        if not miss:
            return ""
        return _format_resource_binding_gap(miss)

    def _shell_task_card_now() -> str:
        page_map = ""
        if sandbox and export_run_id:
            try:
                page_map = list_task_page_view_map(sandbox.id, export_run_id) or ""
            except Exception:
                page_map = ""
        return _format_shell_task_card(
            column_headers=(
                export_deliverable_headers
                or _export_analyze_column_texts(export_todos)
            ),
            page_map=page_map,
            run_id=export_run_id,
            column_plan=export_column_plan,
            fill_target_rel=export_fill_target_rel,
            focus_columns=export_fill_columns,
        )

    def _enter_export_analyze(reason: str = "") -> None:
        nonlocal export_phase, analyze_hint_injected, export_force_finalize
        if not export_like or export_phase in ("analyze", "finalize"):
            return
        export_phase = "analyze"
        analyze_hint_injected = False
        export_force_finalize = False
        if task_has_exportable_data(sandbox.id) if sandbox else False:
            _mark_todos_phase(export_todos, "fetch", True)
        _sync_phase(
            reason
            or ("进入 SHELL 分析阶段" if shell_enabled else "进入平台分析阶段")
        )
        analyze_card = _shell_task_card_now() if shell_enabled else (
            "【平台写表模式】SHELL 已禁用。不要等待或请求 SHELL；"
            "由引擎根据 column_plan 合并 task 页、写入 Excel 并执行交付校验。"
        )
        messages.append({
            "role": "user",
            "content": _analyze_hint_with_pages() + "\n\n" + analyze_card,
        })
        analyze_hint_injected = True

    def _analyze_hint_with_pages() -> str:
        hint = _adapt_export_prompt_for_capabilities(
            _EXPORT_ANALYZE_HINT,
            shell_enabled=shell_enabled,
        )
        if not shell_enabled:
            hint = (
                "【导出·平台分析阶段】白名单资源齐套后禁止再 MCP query；"
                "若仍缺声明资源可先补拉。\n"
                "前提：task 页须覆盖 PLAN「需要资源」/列计划白名单；"
                "禁止在缺声明明细页时用宽表字段凑数。\n"
                "数据齐套后由引擎读取 task 页、按列计划关联计算、写入 xlsx 并校验；"
                "模型无需生成脚本或等待文件工具。"
            )
        if export_time_window:
            hint += (
                f"\n时间窗：{export_time_window.get('label')}；"
                f"毫秒 `[{export_time_window.get('start_ms')}, {export_time_window.get('end_ms')})`。"
            )
        cols = _export_analyze_column_texts(export_todos)
        if cols:
            hint += "\n完整输出列（须作表头，逐条）：\n" + "\n".join(
                f"{i}.{c}" for i, c in enumerate(cols, 1)
            )
        if export_column_plan:
            hint += "\n\n" + format_column_plan_summary(export_column_plan)
        if not sandbox:
            return hint + "\n\n" + _coach_nudge()
        page_map = list_task_page_view_map(sandbox.id, export_run_id)
        missing = _missing_export_roles(
            sandbox.id,
            export_run_id,
            export_target_roles,
            abandoned_roles=export_abandoned_roles,
        )
        extra = ""
        if page_map:
            extra += "\n\n【task 页清单】请按 role/view 正确选用，勿把维表/订单当用户主表：\n" + page_map
        if missing:
            extra += (
                f"\n【缺 role】{', '.join(missing)} — 建议先补齐再写表；"
                f"若预算已尽，仍须写出完整表头（缺列可暂空）。"
                f"（当前目标：{', '.join(export_target_roles) or 'user'}）"
            )
            fact_need = [r for r in missing if r in ("pay", "cash", "bet")]
            if fact_need:
                extra += "\n" + _force_fact_fetch_message(fact_need)
            dim_need = [r for r in missing if r in ("channel", "game")]
            if dim_need:
                extra += "\n" + _force_dim_fetch_message(dim_need)
        elif shell_enabled:
            extra += "\n【角色已齐】建议停止 MCP；禁止只写 prep；立即 join 后 `to_excel` 写当前目录中文表。"
        else:
            extra += "\n【角色已齐】建议停止 MCP；引擎将自动合并 task 页、写 Excel 并校验。"
        extra += "\n\n" + _coach_nudge()
        return hint + extra

    def _full_views_list() -> list[str]:
        return [
            v for v, n in sorted(fetched_view_pages.items())
            if n >= _max_pages_for_view(v, pay_cohort=_pay_cohort, fact_page_cap=export_fact_page_cap, user_page_cap=export_user_page_cap)
        ]

    def _missing_roles_note() -> str:
        if not sandbox:
            return ""
        missing = _missing_export_roles(
            sandbox.id,
            export_run_id,
            export_target_roles,
            abandoned_roles=export_abandoned_roles,
        )
        note = _format_missing_roles_note(
            missing,
            budget_left=mcp_query_budget - mcp_query_count,
            full_views=_full_views_list() or None,
            dim_views=export_dim_views or None,
        )
        if export_time_window and not user_fetch_complete:
            note += (
                "\n【用户主表】尚未拉到短页；请继续同时间窗分页 user_info，"
                f"sql=`{export_time_window.get('sql_select') or export_time_window.get('sql_hint')}`。"
            )
        dim_miss = [r for r in missing if r in ("channel", "game")]
        fact_miss = [r for r in missing if r in ("pay", "cash", "bet")]
        if dim_miss and user_fetch_complete:
            note += (
                "\n【优先维表】用户主表已短页，请先拉列计划内 channel/game；"
                "再仅补仍缺的声明明细 role。"
            )
            note += _dim_views_hint()
        if fact_miss and not dim_miss:
            note += (
                "\n【优先明细】请尽快拉仍缺声明明细 role："
                + "、".join(fact_miss)
                + "；禁止提前写表；勿默认拉未声明的 bet。"
            )
        return note

    def _enter_export_analyze_or_finalize(reason: str = "") -> None:
        nonlocal export_dim_force_rounds, export_dim_budget_exhausted
        # Defer analyze while dims missing and budget remains; reserve dim slots.
        # After _EXPORT_DIM_FORCE_MAX_ROUNDS, degrade (empty channel/game cols OK).
        budget_left = mcp_query_budget - mcp_query_count
        degrade_dims = False
        if export_phase == "fetch" and sandbox:
            user_pages = sum(
                n for v, n in fetched_view_pages.items() if _is_user_info_view(v)
            )
            user_at_cap = user_pages >= export_user_page_cap
            # Idle/stall escape must not re-block forever on short-page / dim force
            stall_escape = any(
                k in (reason or "")
                for k in ("空转", "停滞", "拉取停滞")
            )
            if (
                budget_left > 0
                and not user_fetch_complete
                and not user_at_cap
                and export_dim_force_rounds < _EXPORT_DIM_FORCE_MAX_ROUNDS
                and not stall_escape
            ):
                messages.append({
                    "role": "user",
                    "content": (
                        "【暂缓分析】用户主表未见短页且预算仍有余。"
                        + _fetch_continue_nudge()
                    ),
                })
                _append_progress(progress_lines, f"暂缓分析(用户未短页) {reason}")
                return
            dim_miss = _missing_dim_roles()
            if dim_miss and budget_left > 0:
                # Soft nudge only — do not hard-defer analyze for dims
                export_dim_force_rounds += 1
                if export_dim_force_rounds <= _EXPORT_DIM_FORCE_MAX_ROUNDS:
                    messages.append({
                        "role": "user",
                        "content": _force_dim_fetch_message(dim_miss),
                    })
                    _append_progress(
                        progress_lines,
                        f"建议维表(缺 {'/'.join(dim_miss)}) {reason}",
                    )
                else:
                    export_dim_budget_exhausted = True
                    degrade_dims = True
                # fall through to analyze (维列可空)
            if dim_miss and budget_left <= 0:
                export_dim_budget_exhausted = True
                degrade_dims = True
                _append_progress(
                    progress_lines,
                    f"预算耗尽未拉维表({'/'.join(dim_miss)})，降级进入分析",
                )
            fact_miss = _missing_fact_roles()
            if fact_miss and budget_left > 0 and not degrade_dims and not stall_escape:
                messages.append({
                    "role": "user",
                    "content": _force_fact_fetch_message(fact_miss),
                })
                _append_progress(
                    progress_lines,
                    f"按列补明细(缺 {'/'.join(fact_miss)}) {reason}",
                )
                return
            fact_cont = _fact_need_continue_now()
            if (
                fact_cont
                and budget_left > 0
                and not degrade_dims
                and not stall_escape
                and export_dim_force_rounds < _EXPORT_DIM_FORCE_MAX_ROUNDS
            ):
                messages.append({
                    "role": "user",
                    "content": (
                        "【暂缓分析】明细仍满页可续翻："
                        + "、".join(fact_cont)
                        + "。预算仍有余，请先续拉该 view 再写表。\n"
                        + _force_fact_fetch_message(fact_cont)
                    ),
                })
                _append_progress(
                    progress_lines,
                    f"暂缓分析(明细满页 {'/'.join(fact_cont)}) {reason}",
                )
                return
        if sandbox and task_has_exportable_data(sandbox.id):
            _enter_export_analyze(
                reason
                or ("进入 SHELL 分析" if shell_enabled else "进入平台分析")
            )
        else:
            _enter_export_finalize(reason or "无数据可分析，进入收尾")

    def _fetch_continue_nudge() -> str:
        missing = _missing_export_roles(
            sandbox.id if sandbox else None,
            export_run_id,
            export_target_roles,
        )
        delivery_hint = (
            "齐套后再 SHELL 写中文表头 xlsx（表头=任务锚点完整列）。"
            if shell_enabled
            else "齐套后由引擎自动合并、写中文表头 xlsx 并校验。"
        )
        cont = (
            "【拉取未完成】请继续 MCP 拉齐缺 role；"
            "user_info 未短页则用同一 sql SELECT 续翻；"
            + delivery_hint
            + "请勿散文收工；本轮优先执行工具指令而非 FINAL。"
        )
        if missing:
            cont += " 仍缺: " + "、".join(missing) + "。"
            cont += _dim_views_hint()
        cols = _export_analyze_column_texts(export_todos)
        if cols:
            cont += " 目标列数：" + str(len(cols)) + "。"
        if export_time_window and export_time_window.get("mcp_example"):
            cont += f" user_info 示例：`{export_time_window.get('mcp_example')}`"
        if not user_fetch_complete:
            cont += " 用户主表尚未出现短页，请继续分页。"
        return _coach_nudge(cont)

    def _fetch_idle_tick(*, had_mcp_data: bool = False) -> None:
        """Count fetch turns without MCP data; escape to analyze before max_iters."""
        nonlocal export_fetch_idle_rounds
        if not export_like or export_phase != "fetch":
            return
        if had_mcp_data:
            export_fetch_idle_rounds = 0
            return
        export_fetch_idle_rounds += 1
        if export_fetch_idle_rounds < _EXPORT_FETCH_IDLE_ESCAPE:
            return
        if not sandbox or not task_has_exportable_data(sandbox.id):
            return
        # Soft: still full-page with budget → nudge continue, do not jump analyze
        if _still_full_with_budget():
            export_fetch_idle_rounds = 0
            _append_progress(
                progress_lines,
                "仍满页且有预算，推迟空转收尾；优先引擎代拉/OFFSET 续翻",
            )
            _upsert_progress_message(messages, progress_lines)
            return
        export_fetch_idle_rounds = 0
        _enter_export_analyze_or_finalize(
            f"拉取空转≥{_EXPORT_FETCH_IDLE_ESCAPE}轮（无有效 MCP 落盘），进入分析"
        )

    async def _engine_pull_user_by_pay_uids(self_node: dict | None = None) -> bool:
        """Complete a deferred, bound identity node from landed cohort uids.

        The cohort node owns the time filter. This lookup only uses the explicit
        query-graph resource and uid batches; it never discovers a substitute.
        """
        nonlocal mcp_query_count, user_fetch_complete, export_uid_batch_index
        if not _pay_cohort or not sandbox or not export_run_id:
            return False
        if user_fetch_complete and any(
            _is_user_info_view(v) for v in fetched_view_pages
        ):
            return False
        pay_pages = sum(
            int(n or 0)
            for v, n in fetched_view_pages.items()
            if _VIEW_IS_PAY(v)
        )
        if pay_pages < 1:
            return False
        if "user" in export_abandoned_roles:
            return False
        view = ""
        if isinstance(self_node, dict):
            view = str(self_node.get("view") or "").strip()
        if not view:
            return False
        if view in export_failed_views:
            return False
        uids = collect_export_uids_from_task(sandbox.id, export_run_id)
        if not uids:
            return False
        cols = ""
        if isinstance(self_node, dict):
            cols = str(self_node.get("select_cols") or "").strip()
        if not cols:
            cols = _user_select_cols_from_plan(export_column_plan)
        batch = max(1, int(_EXPORT_USER_UID_BATCH))
        landed_any = False
        uid_offset = max(0, int(export_uid_batch_index or 0))

        async def _mcp_query(sql: str, *, lim: int, title: str, content: str) -> tuple[str, list]:
            nonlocal mcp_query_count
            if mcp_query_count >= mcp_query_budget or not sql:
                return "", []
            trace_key = str((self_node or {}).get("key") or f"field:user:{view}")
            normalized = (
                "MCP: query_ads_view "
                + json.dumps(
                    {"view": view, "sql": sql, "limit": lim},
                    ensure_ascii=False,
                )
            )
            await _push_step({
                "type": "tool",
                "action": "mcp_tool_call",
                "title": title,
                "content": content,
                "status": "running",
            })
            try:
                tr = await execute_action(
                    "mcp_tool_call",
                    normalized,
                    db,
                    agent,
                    sandbox,
                    skill_ids,
                    mcp_ids,
                    httpmcp_ids,
                    rag_ids,
                )
            except Exception as ex:
                logger.exception("uid-batch user_info failed")
                mcp_query_count += 1
                try:
                    export_trace.record_query(
                        key=trace_key,
                        role="user",
                        view=view,
                        sql=sql,
                        error=str(ex),
                        status="error",
                    )
                    _sync_export_trace_progress(write=True)
                except Exception:
                    pass
                return f"exception: {ex}", []
            mcp_query_count += 1
            if not tr or _is_mcp_tool_failure(tr):
                try:
                    export_trace.record_query(
                        key=trace_key,
                        role="user",
                        view=view,
                        sql=sql,
                        error=tr or "empty MCP result",
                        status="error",
                    )
                    _sync_export_trace_progress(write=True)
                except Exception:
                    pass
                return (tr or "empty MCP result"), []
            parsed_rows = _parse_mcp_rows(tr) or []
            try:
                export_trace.record_query(
                    key=trace_key,
                    role="user",
                    view=view,
                    sql=sql,
                    row_count=len(parsed_rows),
                    status="ok",
                    truncated=_is_full_page_rows(
                        len(parsed_rows), view=view, limit=_EXPORT_USER_PAGE_LIMIT,
                    ),
                )
                _sync_export_trace_progress(write=True)
            except Exception:
                pass
            return "", parsed_rows

        async def _fail_user(msg: str) -> bool:
            nonlocal export_uid_batch_index
            await _patch_last_step(status="error", content=msg[:800])
            _append_progress(progress_lines, msg[:500])
            export_failed_views.add(f"node:{str((self_node or {}).get('key') or view)}")
            export_failed_views.add(view)
            export_uid_batch_index = uid_offset
            return landed_any

        async def _land(rows: list, *, consumed: int, batch_no: int) -> None:
            nonlocal landed_any, uid_offset, user_fetch_complete, export_uid_batch_index
            uid_offset += max(0, int(consumed))
            export_uid_batch_index = uid_offset
            if rows:
                write_task_json_page(
                    sandbox.id,
                    rows,
                    run_id=export_run_id,
                    page=mcp_query_count,
                    view=view,
                )
                fetched_view_pages[view] = int(fetched_view_pages.get(view, 0) or 0) + 1
                fetched_view_last_rows[view] = len(rows)
                landed_any = True
                _append_progress(
                    progress_lines,
                    f"uid批次补拉 {view} p{fetched_view_pages[view]} "
                    f"({len(rows)} 行 / +{consumed} uid)",
                )
            await _patch_last_step(
                status="done",
                content=(
                    f"【uid批次】#{batch_no} → {len(rows)} 行；"
                    "时间窗已由 cohort 查询图节点承担"
                )[:800],
            )

        # Probe once before first batch
        if uid_offset == 0 and uids:
            probe_sql = _uid_batch_probe_sql(uids[0], view=view)
            err, _prows = await _mcp_query(
                probe_sql,
                lim=1,
                title="uid探活已绑定资源",
                content=(
                    "【uid探活】时间窗已由 cohort 节点完成；"
                    "当前资源仅按 uid IN 探活"
                ),
            )
            if err:
                return await _fail_user(
                    _uid_batch_fail_message(view, mcp_error=err, detail="探活失败")
                )
            await _patch_last_step(status="done", content="【uid探活】ok")

        batch_no = 0
        while uid_offset < len(uids) and mcp_query_count < mcp_query_budget:
            chunk = uids[uid_offset : uid_offset + batch]
            if not chunk:
                break
            batch_no += 1
            hint = _uid_batch_step_hint(batch_no=batch_no, uid_count=len(chunk))

            async def _try_chunk(chunk_uids: list[str], select_cols: str) -> tuple[str, list]:
                sql = _uid_batch_sql(chunk_uids, view=view, select_cols=select_cols)
                lim = min(len(chunk_uids) + 10, _EXPORT_USER_PAGE_LIMIT)
                return await _mcp_query(
                    sql,
                    lim=lim,
                    title=f"uid批次补拉 user {batch_no}",
                    content=hint,
                )

            err, rows = await _try_chunk(chunk, cols)
            if err and cols.strip() != _UID_BATCH_NARROW_COLS:
                _append_progress(progress_lines, "uid批次宽列失败，降级窄列重试")
                err, rows = await _try_chunk(chunk, _UID_BATCH_NARROW_COLS)
            if err and len(chunk) > 1:
                half = chunk[: max(1, len(chunk) // 2)]
                _append_progress(
                    progress_lines,
                    f"uid批次降级半批重试（{len(half)} uid）",
                )
                err, rows = await _try_chunk(half, _UID_BATCH_NARROW_COLS)
                if not err:
                    await _land(rows, consumed=len(half), batch_no=batch_no)
                    continue
            if err:
                return await _fail_user(_uid_batch_fail_message(view, mcp_error=err))
            await _land(rows, consumed=len(chunk), batch_no=batch_no)

        export_uid_batch_index = uid_offset
        if uid_offset >= len(uids):
            user_fetch_complete = True
            _append_progress(
                progress_lines,
                f"uid批次资源齐套（{len(uids)} uid）",
            )
        _upsert_progress_message(messages, progress_lines)
        _persist_run_state()
        return landed_any

    async def _engine_pull_query_node(node: dict) -> bool:
        """Execute one query-graph node (agg/field/flag/top_n/dim). Returns True if rows landed."""
        nonlocal mcp_query_count, user_fetch_complete
        if not sandbox or not export_run_id:
            return False
        if mcp_query_count >= mcp_query_budget:
            return False
        view = str((node or {}).get("view") or "").strip()
        if not view:
            return False
        node_key = str((node or {}).get("key") or view)
        mode = str((node or {}).get("mode") or "agg").strip().lower()
        role = str((node or {}).get("role") or "").strip().lower()
        # A deferred bound lookup consumes the uid cohort produced by its graph.
        if bool((node or {}).get("deferred")) and _pay_cohort:
            ok = await _engine_pull_user_by_pay_uids(node)
            if ok and user_fetch_complete and node_key:
                export_done_node_keys.add(node_key)
                _sync_export_trace_progress(write=True)
                _persist_run_state()
            return ok
        if f"node:{node_key}" in export_failed_views:
            return False
        if node_key in export_done_node_keys:
            return False
        if _query_node_already_pulled(
            node,
            done_keys=export_done_node_keys,
            failed_views=export_failed_views,
        ):
            return False
        if view in export_failed_views:
            return False
        # Lookup/field pages cover their view. Aggregates/flags/top_n/sequence are
        # node-specific SQL and must be tracked by node key, not by shared view.
        if int(fetched_view_pages.get(view, 0) or 0) >= 1 and mode in (
            "lookup", "field",
        ):
            return False
        normalized = mcp_line_from_query_node(node)
        if not normalized:
            return False
        normalized_args = _parse_mcp_args(normalized)
        query_sql = str(normalized_args.get("sql") or "")
        headers = ",".join((node or {}).get("headers") or [])[:80]
        await _push_step({
            "type": "tool",
            "action": "mcp_tool_call",
            "title": f"查询图·{mode} {view}",
            "content": f"【查询图】{mode} view={view} cols={headers}",
            "status": "running",
        })

        async def _fail_node_once(err_msg: str) -> bool:
            """Record a bound-node failure; retries never substitute a source."""
            nonlocal mcp_query_count
            mcp_query_count += 1
            failure_decision = build_query_node_failure_decision(
                node or {},
                error=err_msg,
                sql=query_sql,
                fetched_view_pages=fetched_view_pages,
                oneshot_modes=_query_node_oneshot_modes(),
            )
            try:
                export_trace.record_query(**failure_decision.trace_record)
            except Exception:
                pass
            # A node error is local evidence. Keep other bound nodes executable.
            if failure_decision.node_only_failure:
                if failure_decision.mark_node_failed:
                    export_failed_views.add(f"node:{node_key}")
                if failure_decision.mark_node_done:
                    export_done_node_keys.add(node_key)
                _sync_export_trace_progress(write=True)
                landed_prior = int(fetched_view_pages.get(view, 0) or 0) >= 1
                note = err_msg + (
                    "（节点放弃，保留已有页；其余绑定节点可继续写表）"
                    if landed_prior
                    else "（节点放弃；其余绑定节点可继续写表并标未完整）"
                )
                await _patch_last_step(status="error", content=note[:800])
                _append_progress(
                    progress_lines,
                    _noncore_abandon_progress(mode=mode, view=view, soft=True),
                )
                return False

            export_failed_views.add(f"node:{node_key}")
            export_failed_views.add(view)
            await _patch_last_step(status="error", content=err_msg[:800])
            _append_progress(progress_lines, f"查询图节点失败 {node_key}：保留错误证据，等待重新绑定或重规划")
            _sync_export_trace_progress(write=True)
            return False

        fail_hint = ""
        # Describe-backed soft align before remote query
        if query_sql:
            _hint = ads_schema_hints.get(view) if isinstance(ads_schema_hints, dict) else None
            _fields = list(getattr(_hint, "fields", None) or []) if _hint else []
            if not _fields:
                _desc = "MCP: describe_ads_view " + json.dumps(
                    {"view_name": view}, ensure_ascii=False,
                )
                try:
                    _dr = await execute_action(
                        "mcp_tool_call",
                        _desc,
                        db,
                        agent,
                        sandbox,
                        skill_ids,
                        mcp_ids,
                        httpmcp_ids,
                        rag_ids,
                    ) or ""
                except Exception:
                    _dr = ""
                if _dr and not _is_mcp_tool_failure(_dr):
                    _sh = parse_describe_schema_hint(_dr, view=view)
                    if _sh.fields:
                        ads_schema_hints[view] = _sh
                        _fields = list(_sh.fields)
                        try:
                            _upd = record_schema_hint(
                                export_trace,
                                view=view,
                                schema_hint=_sh,
                                column_plan=export_column_plan,
                                time_window=export_time_window,
                                schema_hints=ads_schema_hints,
                                dim_views=export_dim_views,
                            )
                            export_column_plan[:] = _upd.column_plan
                        except Exception:
                            pass
            if _fields:
                _aligned, _anotes, _unknown = soft_align_sql_to_schema(query_sql, _fields)
                if _anotes:
                    query_sql = _aligned
                    normalized_args = dict(normalized_args)
                    normalized_args["sql"] = _aligned
                    normalized = "MCP: query_ads_view " + json.dumps(
                        normalized_args, ensure_ascii=False,
                    )
                if _unknown:
                    soft_msg = _enrich_mcp_failure(
                        "query_ads_view",
                        (
                            "MCP 参数软提示: SQL 仍含 describe 未返回的列: "
                            + ", ".join(_unknown)
                            + "。请按真实列名改 SQL（防错预检，不阻止写表/FINAL）。"
                        ),
                        time_window=export_time_window,
                        view=view,
                        schema_hints=ads_schema_hints,
                    )
                    return await _fail_node_once(
                        _query_graph_fail_message(
                            view, hint=fail_hint, mcp_error=soft_msg,
                        )
                    )

        def _mirror_sql_probe(status: str, note: str = "") -> None:
            """Persist soft probe outcome on node + export_trace graph (diagnostic)."""
            try:
                (node or {})["sql_probe"] = status
                if note:
                    prev = [
                        str(x).strip()
                        for x in ((node or {}).get("schema_soft_notes") or [])
                        if str(x).strip()
                    ]
                    if note not in prev:
                        prev.append(note)
                    (node or {})["schema_soft_notes"] = prev
                key = str((node or {}).get("key") or "")
                graphs = []
                if isinstance(getattr(export_trace, "query_graph", None), list):
                    graphs.append(export_trace.query_graph)
                contract = getattr(export_trace, "export_contract", None)
                if isinstance(contract, dict) and isinstance(
                    contract.get("query_graph"), list
                ):
                    graphs.append(contract["query_graph"])
                    notes = [
                        str(x).strip()
                        for x in (contract.get("sql_schema_notes") or [])
                        if str(x).strip()
                    ]
                    if note and note not in notes:
                        notes.append(note)
                        contract["sql_schema_notes"] = notes[:48]
                for graph in graphs:
                    for n in graph:
                        if not isinstance(n, dict):
                            continue
                        if key and str(n.get("key") or "") != key:
                            continue
                        if not key and str(n.get("view") or "") != view:
                            continue
                        n["sql_probe"] = status
                        if note:
                            pn = [
                                str(x).strip()
                                for x in (n.get("schema_soft_notes") or [])
                                if str(x).strip()
                            ]
                            if note not in pn:
                                pn.append(note)
                            n["schema_soft_notes"] = pn
                        if key:
                            break
            except Exception:
                logger.exception("mirror sql_probe failed view=%s", view)

        # Soft LIMIT-1 remote probe before full-page fetch (bet/fact nodes).
        if (
            query_sql
            and should_soft_probe_query_node(node)
            and mcp_query_count < mcp_query_budget
        ):
            probe_sql = sql_with_probe_limit(query_sql, limit=1)
            probe_line = "MCP: query_ads_view " + json.dumps(
                {"view": view, "sql": probe_sql, "limit": 1},
                ensure_ascii=False,
            )
            probe_result = ""
            probe_exc = ""
            try:
                probe_result = await execute_action(
                    "mcp_tool_call",
                    probe_line,
                    db,
                    agent,
                    sandbox,
                    skill_ids,
                    mcp_ids,
                    httpmcp_ids,
                    rag_ids,
                ) or ""
            except Exception as ex:
                probe_exc = _format_mcp_exc_for_step(ex)
                logger.exception(
                    "query graph sql probe failed view=%s sql=%s",
                    view,
                    probe_sql[:200],
                )
            if probe_exc or not probe_result or _is_mcp_tool_failure(probe_result):
                enriched_probe = _enrich_mcp_failure(
                    "query_ads_view",
                    probe_exc or probe_result or "SQL soft probe failed",
                    time_window=export_time_window,
                    view=view,
                    schema_hints=ads_schema_hints,
                )
                probe_note = (
                    f"【SQL软探针】`{view}` 远程失败（可改参重试；不阻止写表/FINAL）: "
                    + (enriched_probe or "")[:240]
                )
                _mirror_sql_probe("error", probe_note)
                _append_progress(progress_lines, probe_note[:200])
                # _fail_node_once burns one budget unit for this soft skip
                return await _fail_node_once(
                    _query_graph_fail_message(
                        view,
                        hint=fail_hint,
                        mcp_error=probe_note,
                    )
                )
            mcp_query_count += 1
            probe_rows = _parse_mcp_rows(probe_result) or []
            if not probe_rows:
                empty_note = (
                    f"【SQL软探针】`{view}` 语法通过但 0 行（时间窗/口径可能偏；不硬失败）"
                )
                _mirror_sql_probe("empty", empty_note)
                _append_progress(progress_lines, empty_note[:200])
            else:
                _mirror_sql_probe("ok")

        try:
            tool_result = await execute_action(
                "mcp_tool_call",
                normalized,
                db,
                agent,
                sandbox,
                skill_ids,
                mcp_ids,
                httpmcp_ids,
                rag_ids,
            )
        except Exception as ex:
            logger.exception(
                "query graph mcp failed view=%s sql=%s",
                view,
                (query_sql or "")[:200],
            )
            return await _fail_node_once(
                _query_graph_fail_message(
                    view,
                    hint=fail_hint,
                    exc=_format_mcp_exc_for_step(ex),
                )
            )
        if not tool_result or _is_mcp_tool_failure(tool_result):
            enriched_err = _enrich_mcp_failure(
                "query_ads_view",
                tool_result or "",
                time_window=export_time_window,
                view=view,
                schema_hints=ads_schema_hints,
            )
            logger.warning(
                "query graph mcp remote fail view=%s sql=%s err=%s",
                view,
                (query_sql or "")[:200],
                (enriched_err or "")[:500],
            )
            return await _fail_node_once(
                _query_graph_fail_message(
                    view, hint=fail_hint, mcp_error=enriched_err or "",
                )
            )
        rows = _parse_mcp_rows(tool_result) or []
        mcp_query_count += 1
        if not rows:
            await _patch_last_step(
                status="done",
                content=f"【查询图】{mode} `{view}` 无行",
            )
            # Count as covered empty page so we don't loop forever
            fetched_view_pages[view] = fetched_view_pages.get(view, 0) + 1
            fetched_view_last_rows[view] = 0
            empty_decision = build_query_node_rows_decision(
                node or {},
                row_count=0,
                full_page_rows=_full_page_row_threshold(_page_limit_for_view(view)),
                oneshot_modes=_query_node_oneshot_modes(),
            )
            if empty_decision.mark_node_done:
                export_done_node_keys.add(node_key)
            try:
                export_trace.record_query(
                    key=node_key,
                    role=role,
                    view=view,
                    sql=query_sql,
                    row_count=0,
                    status="ok",
                )
            except Exception:
                pass
            _sync_export_trace_progress(write=True)
            _persist_run_state()
            return False
        rel = write_task_json_page(
            sandbox.id,
            rows,
            run_id=export_run_id,
            page=mcp_query_count,
            view=view,
        )
        fetched_view_pages[view] = fetched_view_pages.get(view, 0) + 1
        fetched_view_last_rows[view] = len(rows)
        page_lim = _page_limit_for_view(view)
        rows_decision = build_query_node_rows_decision(
            node or {},
            row_count=len(rows),
            full_page_rows=_full_page_row_threshold(page_lim),
            oneshot_modes=_query_node_oneshot_modes(),
        )
        if rows_decision.mark_node_done:
            export_done_node_keys.add(node_key)
        if (
            rows_decision.user_fetch_complete
            and _is_short_page_complete(len(rows), view=view, limit=page_lim)
        ):
            user_fetch_complete = True
        elif _looks_like_remote_tiny_page_cap(len(rows), requested_limit=page_lim):
            user_fetch_complete = False
        try:
            export_trace.record_query(
                key=node_key,
                role=role,
                view=view,
                sql=query_sql,
                row_count=len(rows),
                status="ok",
                truncated=rows_decision.full_page,
            )
        except Exception:
            pass
        _sync_export_trace_progress(write=True)
        note = (
            f"【查询图】{mode} view={view} → `{rel or ''}` "
            f"({len(rows)} 行；query {mcp_query_count}/{mcp_query_budget})"
        )
        _append_progress(progress_lines, f"查询图 {mode} {view} ({len(rows)} 行)")
        # Auto OFFSET for page-shaped nodes. top_n stays one-shot; sequence
        # needs bounded pagination because streak metrics depend on event flow.
        if (
            mode in _query_node_auto_offset_modes()
            and rows_decision.full_page
        ):
            sql_base = _sql_base_for_pagination(
                str(_parse_mcp_args(normalized).get("sql") or ""),
                role,
                export_time_window,
            )
            auto_done = 0
            node_last_rows = len(rows)
            page_cap = _max_pages_for_view(
                view,
                pay_cohort=_pay_cohort,
                fact_page_cap=export_fact_page_cap,
                user_page_cap=export_user_page_cap,
            )
            while sql_base and _should_auto_offset_continue(
                phase=export_phase,
                last_page_rows=node_last_rows,
                pages_done=auto_done + 1,
                page_cap=page_cap,
                budget_left=mcp_query_budget - mcp_query_count,
                auto_done=auto_done,
                page_limit=page_lim,
                view=view,
            ):
                offset = _auto_offset_for_node_page(auto_done, limit=page_lim)
                sql_next = _sql_with_offset(sql_base, offset, limit=page_lim)
                norm_auto = (
                    "MCP: query_ads_view "
                    + json.dumps(
                        {"view": view, "sql": sql_next, "limit": page_lim},
                        ensure_ascii=False,
                    )
                )
                try:
                    tr_auto = await execute_action(
                        "mcp_tool_call",
                        norm_auto,
                        db,
                        agent,
                        sandbox,
                        skill_ids,
                        mcp_ids,
                        httpmcp_ids,
                        rag_ids,
                    )
                except Exception:
                    break
                if not tr_auto or _is_mcp_tool_failure(tr_auto):
                    break
                next_rows = _parse_mcp_rows(tr_auto) or []
                mcp_query_count += 1
                auto_done += 1
                write_task_json_page(
                    sandbox.id,
                    next_rows,
                    run_id=export_run_id,
                    page=mcp_query_count,
                    view=view,
                )
                fetched_view_pages[view] = int(fetched_view_pages.get(view, 0) or 0) + 1
                fetched_view_last_rows[view] = len(next_rows)
                node_last_rows = len(next_rows)
                try:
                    export_trace.record_query(
                        key=node_key,
                        role=role,
                        view=view,
                        sql=sql_next,
                        row_count=len(next_rows),
                        status="ok",
                        truncated=_is_full_page_rows(
                            len(next_rows), view=view, limit=page_lim,
                        ),
                    )
                except Exception:
                    pass
                _sync_export_trace_progress(write=True)
                if _is_short_page_complete(node_last_rows, view=view, limit=page_lim):
                    if role == "user":
                        user_fetch_complete = True
                    break
        await _patch_last_step(status="done", content=note[:800])
        _upsert_progress_message(messages, progress_lines)
        _persist_run_state()
        return True

    async def _engine_run_schema_discovery_repair(
        repair_plan: dict | None,
        *,
        reason: str = "",
    ) -> int:
        """Run RepairPlan schema-discovery actions before data query burst."""
        nonlocal export_column_plan, ads_views_list_text, ads_schema_hints
        nonlocal export_schema_discovery
        if not export_like or not sandbox or not export_run_id:
            return 0
        planned_views = [
            str(n.get("view") or "").strip()
            for n in build_query_graph(
                export_column_plan,
                export_time_window,
                schema_fields_by_view={
                    str(v): list(getattr(h, "fields", None) or [])
                    for v, h in (ads_schema_hints or {}).items()
                    if v and getattr(h, "fields", None)
                },
                bound_only=True,
            )
            if isinstance(n, dict) and str(n.get("view") or "").strip()
        ]
        schema_actions = build_schema_discovery_actions(
            repair_plan=repair_plan,
            schema_discovery=export_trace.schema_discovery,
            planned_views=planned_views,
            schema_hints=ads_schema_hints,
        )
        if not schema_actions:
            if ads_views_list_text and export_column_plan:
                try:
                    bound_resources, _ = await _export_bind_column_resources(
                        db=db,
                        llm=llm,
                        mcp_ids=mcp_ids or [],
                        column_plan=export_column_plan,
                        column_headers=_export_analyze_column_texts(export_todos),
                        catalog_text=ads_views_list_text,
                        resource_schemas={
                            str(v): (
                                f"view={v}; comment={getattr(h, 'view_comment', '')}; "
                                f"fields={','.join(getattr(h, 'fields', []) or [])}"
                            )
                            for v, h in (ads_schema_hints or {}).items()
                        },
                        context_text="\n".join(
                            x for x in [note_content, task_brief, export_source_brief]
                            if str(x or "").strip()
                        ),
                    )
                    export_resource_whitelist[:] = list(bound_resources)
                    update = sync_export_contract_state(
                        export_trace,
                        column_plan=export_column_plan,
                        time_window=export_time_window,
                        schema_hints=ads_schema_hints,
                        schema_required=bool(export_column_plan),
                        dim_views=export_dim_views,
                    )
                    export_column_plan[:] = update.column_plan
                    export_schema_discovery = dict(update.schema_discovery or {})
                    try:
                        write_export_trace(sandbox.id, export_run_id, export_trace)
                    except Exception:
                        logger.exception("write export_trace after bind-only discovery refresh failed")
                except Exception:
                    logger.exception("bind-only discovery refresh failed")
            return 0

        async def _execute_schema_mcp(
            action: dict[str, object],
            normalized: str,
            view: str,
        ) -> str:
            action_type = str(action.get("action_type") or "")
            if action_type == "discover_ads_views":
                title = "RepairPlan · list_ads_views"
                content = "【schema 修复】自动获取 MCP 接口/视图列表"
            else:
                title = f"RepairPlan · describe {view}"
                content = f"【schema 修复】自动获取 `{view}` 字段/表备注"
            await _push_step({
                "type": "tool",
                "action": "mcp_tool_call",
                "title": title,
                "content": content,
                "status": "running",
            })
            try:
                return await execute_action(
                    "mcp_tool_call",
                    normalized,
                    db,
                    agent,
                    sandbox,
                    skill_ids,
                    mcp_ids,
                    httpmcp_ids,
                    rag_ids,
                ) or ""
            except Exception as ex:
                await _patch_last_step(status="error", content=str(ex)[:400])
                return ""

        async def _handle_schema_call_result(call) -> None:
            nonlocal ads_views_list_text, export_schema_discovery
            if call.ok and call.action_type == "discover_ads_views":
                ads_views_list_text = call.text or ""
                export_schema_discovery = record_schema_list(
                    export_trace,
                    ads_views_list_text,
                )
                write_export_trace(sandbox.id, export_run_id, export_trace)
                await _patch_last_step(
                    status="done",
                    content="【schema 修复】已记录 list_ads_views 证据",
                )
            elif not call.ok and call.action_type == "discover_ads_views" and call.error:
                await _patch_last_step(status="error", content=call.error[:800])
            elif call.ok and call.action_type == "describe_ads_views" and call.schema_hint:
                ads_schema_hints[call.view] = call.schema_hint
                update = record_schema_hint(
                    export_trace,
                    view=call.view,
                    schema_hint=call.schema_hint,
                    column_plan=export_column_plan,
                    time_window=export_time_window,
                    schema_hints=ads_schema_hints,
                    dim_views=export_dim_views,
                )
                export_column_plan[:] = update.column_plan
                export_schema_discovery = dict(update.schema_discovery or {})
                write_export_trace(sandbox.id, export_run_id, export_trace)
                await _patch_last_step(
                    status="done",
                    content=(
                        f"【schema 修复】`{call.view}` 已记录 "
                        f"{len(call.schema_hint.fields)} 个字段；列计划/查询图已刷新"
                    )[:800],
                )
            elif (
                not call.ok
                and call.action_type == "describe_ads_views"
                and call.error
            ):
                await _patch_last_step(status="error", content=call.error[:800])

        schema_result = await execute_schema_discovery_actions(
            schema_actions,
            execute_mcp=_execute_schema_mcp,
            is_failure=_is_mcp_tool_failure,
            schema_discovery=export_trace.schema_discovery,
            schema_hints=ads_schema_hints,
            fallback_views=planned_views,
            on_call_result=_handle_schema_call_result,
        )

        if schema_result.ran:
            schema_summary_now = export_summarize_schema_discovery(
                schema_discovery=export_trace.schema_discovery,
                export_contract=(
                    export_trace.export_contract
                    if isinstance(getattr(export_trace, "export_contract", None), dict)
                    else None
                ),
            )
            export_trace.repair_plan = export_strip_completed_schema_repair_actions(
                export_trace.repair_plan
                if isinstance(getattr(export_trace, "repair_plan", None), dict)
                else {},
                schema_summary_now,
            )
            try:
                write_export_trace(sandbox.id, export_run_id, export_trace)
            except Exception:
                logger.exception("write export_trace after schema repair cleanup failed")
            _append_progress(
                progress_lines,
                f"RepairPlan schema discovery 自动完成 {schema_result.ran} 项"
                + (f"（{reason}）" if reason else ""),
            )
            _upsert_progress_message(messages, progress_lines)
            _persist_run_state()
        # Discovery is not a terminal step: once list/describe evidence exists,
        # ask the LLM to bind the original column intent to real resources and
        # rebuild the execution contract from that evidence.
        if ads_views_list_text and export_column_plan:
            try:
                bound_resources, _ = await _export_bind_column_resources(
                    db=db,
                    llm=llm,
                    mcp_ids=mcp_ids or [],
                    column_plan=export_column_plan,
                    column_headers=_export_analyze_column_texts(export_todos),
                    catalog_text=ads_views_list_text,
                    resource_schemas={
                        str(v): (
                            f"view={v}; comment={getattr(h, 'view_comment', '')}; "
                            f"fields={','.join(getattr(h, 'fields', []) or [])}"
                        )
                        for v, h in (ads_schema_hints or {}).items()
                    },
                    context_text="\n".join(
                        x for x in [note_content, task_brief, export_source_brief]
                        if str(x or "").strip()
                    ),
                )
                export_resource_whitelist[:] = list(bound_resources)
                update = sync_export_contract_state(
                    export_trace,
                    column_plan=export_column_plan,
                    time_window=export_time_window,
                    schema_hints=ads_schema_hints,
                    schema_required=bool(export_column_plan),
                    dim_views=export_dim_views,
                )
                export_column_plan[:] = update.column_plan
                export_schema_discovery = dict(update.schema_discovery or {})
            except Exception:
                logger.exception("refresh resource binding after schema discovery failed")
        return schema_result.ran

    async def _engine_fetch_planned_roles(*, reason: str = "") -> int:
        """Pull only the MCP-bound query graph for the current column plan."""
        nonlocal mcp_query_count, user_fetch_complete
        if not export_like or export_phase != "fetch" or not sandbox:
            return 0
        if export_view_mode == "single_view":
            # Type C: only pinned views, one page each if not yet covered
            landed = 0
            for view in list(export_pinned_views or []):
                if mcp_query_count >= mcp_query_budget:
                    break
                if fetched_view_pages.get(view, 0) > 0:
                    continue
                normalized = (
                    "MCP: query_ads_view "
                    + json.dumps(
                        {"view": view, "limit": _EXPORT_PAGE_LIMIT},
                        ensure_ascii=False,
                    )
                )
                await _push_step({
                    "type": "tool",
                    "action": "mcp_tool_call",
                    "title": f"引擎代拉 {view}",
                    "content": f"【引擎代拉·类型C】view={view}",
                    "status": "running",
                })
                try:
                    tool_result = await execute_action(
                        "mcp_tool_call",
                        normalized,
                        db,
                        agent,
                        sandbox,
                        skill_ids,
                        mcp_ids,
                        httpmcp_ids,
                        rag_ids,
                    )
                except Exception as ex:
                    await _patch_last_step(status="error", content=str(ex)[:400])
                    continue
                if not tool_result or _is_mcp_tool_failure(tool_result):
                    await _patch_last_step(status="error", content="类型C 代拉失败")
                    continue
                rows = _parse_mcp_rows(tool_result) or []
                mcp_query_count += 1
                if rows:
                    rel = write_task_json_page(
                        sandbox.id,
                        rows,
                        run_id=export_run_id,
                        page=mcp_query_count,
                        view=view,
                    )
                    fetched_view_pages[view] = fetched_view_pages.get(view, 0) + 1
                    fetched_view_last_rows[view] = len(rows)
                    landed += 1
                    await _patch_last_step(
                        status="done",
                        content=(
                            f"【引擎代拉·类型C】view={view} → `{rel or ''}` "
                            f"({len(rows)} 行)"
                        )[:800],
                    )
                else:
                    await _patch_last_step(status="done", content="类型C 无行")
            if landed and reason:
                _append_progress(progress_lines, f"引擎代拉(类型C) {reason}")
            if landed and _roles_ready_for_analyze():
                _enter_export_analyze("引擎代拉齐套，进入 SHELL 分析")
            return landed

        # Prefer query graph from column_plan (aggregate MCP path)
        if export_column_plan:
            repair_plan_now = (
                export_trace.repair_plan
                if isinstance(getattr(export_trace, "repair_plan", None), dict)
                else {}
            )
            await _engine_run_schema_discovery_repair(
                repair_plan_now,
                reason=reason,
            )
            repair_plan_now = (
                export_trace.repair_plan
                if isinstance(getattr(export_trace, "repair_plan", None), dict)
                else {}
            )
            query_plan = build_query_execution_plan(
                column_plan=export_column_plan,
                time_window=export_time_window,
                dim_views=export_dim_views,
                repair_plan=repair_plan_now,
                repair_intent=export_repair_like,
                page_limit=_EXPORT_PAGE_LIMIT,
                schema_fields_by_view={
                    str(v): list(getattr(h, "fields", None) or [])
                    for v, h in (ads_schema_hints or {}).items()
                    if v and getattr(h, "fields", None)
                },
                bound_only=True,
            )
            nodes = query_plan.nodes
            if export_resource_whitelist:
                nodes = [
                    n
                    for n in nodes
                    if _export_resource_allowed(
                        str(n.get("view") or ""),
                        export_resource_whitelist,
                    )
                ]
            scoped_by_repair = query_plan.scoped_by_repair
            if query_plan.progress_suffix:
                _append_progress(
                    progress_lines,
                    query_plan.progress_suffix,
                )
            landed_n = 0
            # Burst: pull until graph done-enough or budget exhausted (fail-fast)
            while mcp_query_count < mcp_query_budget:
                progress_before = mcp_query_count
                for node in nodes:
                    if mcp_query_count >= mcp_query_budget:
                        break
                    if query_node_should_skip(
                        node,
                        done_node_keys=export_done_node_keys,
                        failed_views=export_failed_views,
                        fetched_view_pages=fetched_view_pages,
                        already_pulled=_query_node_already_pulled(
                            node,
                            done_keys=export_done_node_keys,
                            failed_views=export_failed_views,
                        ),
                        oneshot_modes=_query_node_oneshot_modes(),
                    ):
                        continue
                    if await _engine_pull_query_node(node):
                        landed_n += 1
                if query_execution_ready(
                    nodes,
                    fetched_view_pages=fetched_view_pages,
                    failed_views=export_failed_views,
                    done_node_keys=export_done_node_keys,
                ):
                    break
                if mcp_query_count == progress_before:
                    break  # no progress — avoid infinite loop
            graph_ready = query_execution_ready(
                nodes,
                fetched_view_pages=fetched_view_pages,
                failed_views=export_failed_views,
                done_node_keys=export_done_node_keys,
            )
            if nodes:
                # Non-empty query graph: never fall back to role→hardcoded raw log pulls
                if graph_ready:
                    # Core ready (failed heavy/bet abandoned) → platform write + FINAL
                    if any(_is_user_info_view(v) for v in fetched_view_pages):
                        user_fetch_complete = True
                    if export_abandoned_roles:
                        _append_progress(
                            progress_lines,
                            "未完整角色: " + ",".join(sorted(export_abandoned_roles)),
                        )
                    _append_progress(
                        progress_lines,
                        f"查询图可交付 {landed_n} 节点（{reason or '补缺'}）"
                        f"；{_format_llm_mcp_progress(llm_iter=0, max_iters=max_iters, mcp_query_count=mcp_query_count, mcp_budget=mcp_query_budget)}",
                    )
                    _upsert_progress_message(messages, progress_lines)
                    _mark_todos_phase(export_todos, "fetch", True)
                    _refresh_todo_progress()
                    _persist_run_state()
                    if task_has_exportable_data(sandbox.id) and _try_platform_write_finish(
                        "查询图可交付，平台写表"
                    ):
                        return landed_n
                    if task_has_exportable_data(sandbox.id):
                        _enter_export_analyze("查询图可交付，进入 SHELL 分析")
                    return landed_n
                if landed_n:
                    _append_progress(
                        progress_lines,
                        f"查询图部分完成 {landed_n}（{reason or ''}）",
                    )
                    if (
                        export_repair_like
                        and task_has_exportable_data(sandbox.id)
                        and should_reverify_after_query_progress(
                            repair_plan_now,
                            landed_count=landed_n,
                            scoped_by_repair=scoped_by_repair,
                        )
                        and _try_platform_write_finish(
                            "RepairPlan 补数后重写交付并复验",
                            prefer_fallback=True,
                        )
                    ):
                        return landed_n
                    if (
                        task_has_exportable_data(sandbox.id)
                        and export_failed_views
                        and _try_platform_write_finish("核心已齐、失败段已放弃，平台写表")
                    ):
                        return landed_n
                    return landed_n
                _append_progress(
                    progress_lines,
                    "查询图未齐：缺口交列计划/绑定 resource（不代拉硬编码源表）"
                    + (f"（{reason}）" if reason else ""),
                )
                _upsert_progress_message(messages, progress_lines)
                _persist_run_state()
                return 0

        _append_progress(
            progress_lines,
            "查询图为空：等待 MCP list/describe 与 LLM 资源绑定，不使用旧角色回退"
            + (f"（{reason}）" if reason else ""),
        )
        _upsert_progress_message(messages, progress_lines)
        _persist_run_state()
        return 0

    def _bump_fetch_or_analyze(reply: str) -> bool:
        """During fetch: after no-progress limit, enter analyze instead of aborting to raw dump."""
        nonlocal no_progress
        if export_like and export_phase == "fetch":
            _fetch_idle_tick(had_mcp_data=False)
            no_progress += 1
            if no_progress < _NO_PROGRESS_LIMIT:
                return False
            if sandbox and task_has_exportable_data(sandbox.id):
                if _still_full_with_budget():
                    no_progress = 0
                    _append_progress(
                        progress_lines,
                        "仍满页且有预算，推迟停滞收尾；继续 OFFSET/引擎代拉",
                    )
                    _upsert_progress_message(messages, progress_lines)
                    return False
                no_progress = 0
                _enter_export_analyze_or_finalize("拉取停滞，进入 SHELL 分析")
                return False
            # fall through to normal abort
            no_progress -= 1
        return _bump_no_progress(reply)

    def _enter_export_finalize(reason: str = "") -> None:
        nonlocal export_phase, export_force_finalize
        export_phase = "finalize"
        export_force_finalize = True
        _sync_phase(reason or "进入 finalize")

    def _deliverable_passes_gate(rel: str) -> tuple[bool, list[str], list[str]]:
        """Return (ok, headers, missing)."""
        if not sandbox or not rel:
            return False, [], [t.get("text") for t in export_todos if t.get("phase") == "analyze"]
        p = download_path(sandbox.id, rel)
        if not p or not p.is_file():
            return False, [], [t.get("text") for t in export_todos if t.get("phase") == "analyze"]
        try:
            if p.stat().st_mtime < export_run_started_at - 1:
                return False, [], [t.get("text") for t in export_todos if t.get("phase") == "analyze"]
        except OSError:
            return False, [], []
        headers = read_deliverable_headers(p)
        ok, missing = _todos_satisfied(headers, export_todos)
        return ok, headers, missing

    def _collect_analyze_deliverables() -> list[str]:
        # Column-fill: allow claiming prior deliverable path when overwritten
        if (
            export_fill_target_rel
            and export_fill_target_rel not in saved_paths
            and sandbox
        ):
            p_claim = download_path(sandbox.id, export_fill_target_rel)
            if p_claim and is_valid_deliverable_file(p_claim):
                saved_paths.append(export_fill_target_rel)
        roots = _find_export_root_deliverables(
            sandbox,
            save_dir,
            saved_paths,
            run_id=export_run_id,
            started_at=export_run_started_at,
        )
        # Prefer fill target first when present
        if export_fill_target_rel and export_fill_target_rel in roots:
            roots = [export_fill_target_rel] + [
                r for r in roots if r != export_fill_target_rel
            ]
        elif (
            export_fill_target_rel
            and sandbox
            and download_path(sandbox.id, export_fill_target_rel)
        ):
            roots = [export_fill_target_rel] + [
                r for r in roots if r != export_fill_target_rel
            ]
        passed: list[str] = []
        for rel in roots:
            ok, headers, missing = _deliverable_passes_gate(rel)
            if not ok:
                continue
            passed.append(rel)
            if rel not in saved_paths:
                saved_paths.append(rel)
            _mark_column_todos_from_headers(export_todos, headers)
            _append_progress(progress_lines, f"分析产物达标 {rel}")
        if passed:
            _refresh_todo_progress()
            _persist_run_state(deliverable=passed[0])
        return passed

    def _try_platform_write_finish(
        reason: str = "",
        *,
        prefer_fallback: bool = False,
    ) -> bool:
        """Codex path: merge task pages → dual-sheet xlsx → FINAL (no LLM SHELL)."""
        nonlocal export_phase
        if not export_like or not sandbox or not export_run_id:
            return False
        if not _full_fetch_write_allowed_now(prefer_fallback=prefer_fallback):
            return False
        headers = list(export_deliverable_headers) if export_deliverable_headers else []
        if not headers:
            headers = [
                str(c.get("header") or "").strip()
                for c in (export_column_plan or [])
                if isinstance(c, dict) and str(c.get("header") or "").strip()
            ]
        if not headers:
            headers = _export_analyze_column_texts(export_todos)
        title = ""
        if export_time_window and export_time_window.get("label"):
            title = str(export_time_window.get("label"))
        plan_for_write = list(export_column_plan or [])
        incomplete_roles = set(export_abandoned_roles)
        if prefer_fallback and sandbox and export_run_id:
            incomplete_roles |= set(
                _missing_export_roles(
                    sandbox.id,
                    export_run_id,
                    export_target_roles,
                    abandoned_roles=export_abandoned_roles,
                )
            )
        preferred_write = (
            Path(export_fill_target_rel).name
            if export_fill_target_rel
            else f"用户分析_{export_run_id}.xlsx"
        )
        try:
            materialized = materialize_platform_export(
                sandbox_id=sandbox.id,
                run_id=export_run_id,
                column_plan=plan_for_write or None,
                time_window=export_time_window,
                headers=headers,
                title=title or "导出数据",
                incomplete_roles=incomplete_roles,
                preferred_name=preferred_write,
                fill_prior_rel=export_fill_target_rel,
                focus_columns=export_fill_columns or None,
            )
            rel = materialized.file_rel
            plan_for_write = materialized.column_plan
            if (
                export_fill_target_rel
                and rel
                and rel != export_fill_target_rel
            ):
                _append_progress(
                    progress_lines,
                    f"补齐合并未命中，软回退新建 `{rel}`",
                )
            elif export_fill_target_rel and rel == export_fill_target_rel:
                _append_progress(
                    progress_lines,
                    f"补齐已覆盖原交付 `{rel}`",
                )
        except Exception as exc:  # noqa: BLE001
            _append_progress(progress_lines, f"平台写表失败: {exc}")
            return False
        if not rel:
            return False
        _append_progress(progress_lines, f"平台写表完成: `{rel}`" + (f"（{reason}）" if reason else ""))
        if rel not in saved_paths:
            saved_paths.append(rel)
        # Verifier gate before claiming success FINAL
        try:
            export_trace.failed_views = sorted(export_failed_views)
            export_trace.abandoned_roles = sorted(export_abandoned_roles)
            export_trace.done_node_keys = sorted(export_done_node_keys)
            export_trace.fetched_view_pages = dict(fetched_view_pages)
            export_trace.set_materialization(
                output_path=rel,
                mode="platform",
                reason=reason or "",
            )
            _sync_export_trace_progress()
            fin = verify_export_finalizer(
                sandbox_id=sandbox.id,
                run_id=export_run_id,
                file_rel=rel,
                task_spec=export_task_spec.to_dict() if export_task_spec else {},
                column_plan=plan_for_write or export_column_plan,
                trace=export_trace,
                failed_views=export_failed_views,
                abandoned_roles=export_abandoned_roles,
                prefer_fallback=prefer_fallback,
            )
            _vr = fin.verifier_result
            _outcome = fin.outcome
            if _outcome.should_block_final:
                errs = "；".join(_outcome.blocking_reasons[:4])
                _append_progress(
                    progress_lines,
                    f"交付校验未通过，暂不宣称完成: {errs}",
                )
                messages.append({
                    "role": "user",
                    "content": (
                        "【交付校验未通过】请根据缺口继续补齐或修复，勿输出「已完成」。\n"
                        + format_verifier_block(_vr)
                        + "\n可继续：重试失败 MCP 节点 / 补拉缺失 role / 修正列口径后平台再写表。"
                    ),
                })
                return False
        except Exception as exc:  # noqa: BLE001
            logger.exception("export verifier failed")
            _append_progress(progress_lines, f"交付校验异常: {exc}")
        # Prefer hard finish with platform-assisted label path
        return _try_export_hard_finish(
            reason or "平台合并写表落盘",
            prefer_fallback=prefer_fallback,
        )

    def _try_engine_run_build_script(*, force: bool = False) -> bool:
        """Hybrid: run LLM-authored build_report*.py once when roles are ready.

        Returns True if a gated deliverable appeared after the run.
        """
        nonlocal engine_script_ran, engine_script_executed, analyze_write_shell_ok
        if not shell_enabled or not export_like or not sandbox:
            return False
        if engine_script_ran and not force:
            return False
        missing = _missing_export_roles(
            sandbox.id,
            export_run_id,
            export_target_roles,
            abandoned_roles=export_abandoned_roles,
        )
        if missing:
            return False
        scripts = find_export_build_scripts(sandbox.id)
        if scripts:
            rel = scripts[0].relative_to(ensure_workplace(sandbox.id)).as_posix()
            cmd = f"python3 /workplace/{rel}"
        else:
            # Container /tmp may hold the script when workplace/tmp is empty
            cmd = (
                "if [ -f /tmp/build_report.py ]; then python3 /tmp/build_report.py; "
                "elif [ -f tmp/build_report.py ]; then python3 tmp/build_report.py; "
                "else echo '[engine] no build_report.py'; exit 1; fi"
            )
            # Skip probing /tmp when nothing on host either and not forced after grace
            if not force:
                return False
        engine_script_ran = True
        timeout = int(getattr(agent, "shell_timeout", None) or 120)
        _append_progress(progress_lines, f"引擎代执行脚本: {cmd}")
        try:
            out = _exec_shell(sandbox, cmd, timeout)
        except Exception as exc:  # noqa: BLE001 — hybrid path must not crash finish
            _append_progress(progress_lines, f"引擎代执行失败: {exc}")
            return False
        _append_progress(
            progress_lines,
            f"引擎代执行输出: {_short_text(out or '', 240)}",
        )
        roots = _collect_analyze_deliverables()
        facts_ok = _facts_ok_for_delivery(
            _missing_export_roles(
            sandbox.id,
            export_run_id,
            export_target_roles,
            abandoned_roles=export_abandoned_roles,
        ),
        )
        if roots and facts_ok:
            analyze_write_shell_ok = True
            engine_script_executed = True
            return True
        return False

    def _try_export_hard_finish(reason: str = "", *, prefer_fallback: bool = False) -> bool:
        """Prefer gated LLM SHELL xlsx; then engine-run script; then platform join; raw last.

        Raw materialize is blocked while still in fetch, and during analyze until
        write-grace is exhausted (unless prefer_fallback=True after grace).
        """
        nonlocal final, saved_paths, had_explicit_final, export_fallback, export_missing_cols
        nonlocal export_phase, engine_script_executed, analyze_write_shell_ok
        if not export_like or not sandbox:
            return False
        if not _full_fetch_write_allowed_now(prefer_fallback=prefer_fallback):
            return False
        mode = "fallback"
        made = ""
        missing: list[str] = []
        platform_assisted = False
        engine_executed = False
        # Prefer gated analyze deliverable (LLM SHELL) — unless Type-B facts missing
        roots = _collect_analyze_deliverables()
        missing_roles_gate = _missing_export_roles(
            sandbox.id,
            export_run_id,
            export_target_roles,
            abandoned_roles=export_abandoned_roles,
        )
        facts_ok = _facts_ok_for_delivery(missing_roles_gate)
        if roots and facts_ok:
            made = roots[0]
            mode = "analyzed"
            export_fallback = False
            if engine_script_executed:
                engine_executed = True
        elif roots and not facts_ok:
            fact_miss_gate = _blocking_roles_for_analyzed_delivery(
                export_target_roles=export_target_roles,
                missing_roles=missing_roles_gate,
            )
            budget_left_gate = mcp_query_budget - mcp_query_count
            if prefer_fallback or budget_left_gate <= 0:
                # Exhaust / forced: accept existing root xlsx even if roles incomplete
                made = roots[0]
                mode = "analyzed"
                export_fallback = bool(missing_roles_gate)
                if engine_script_executed:
                    engine_executed = True
                _append_progress(
                    progress_lines,
                    f"强制接受交付(缺 {'/'.join(fact_miss_gate) or '列'}) {reason}",
                )
            elif budget_left_gate > 0:
                export_phase = "fetch"
                messages.append({
                    "role": "user",
                    "content": (
                        "【建议回拉】当前目录虽有 xlsx，但 task 仍缺 role："
                        + "、".join(fact_miss_gate)
                        + "（含 user 时禁止跨旧 run 拼表）。"
                        "请先拉齐再 join 写表（勿把宽表当分析交付）。\n"
                        + _force_fact_fetch_message(
                            [r for r in fact_miss_gate if r in ("pay", "cash", "bet")]
                        )
                    ),
                })
                _append_progress(
                    progress_lines,
                    f"暂缓分析交付(缺 {'/'.join(fact_miss_gate)}) {reason}",
                )
                return False
            else:
                made = ""
                roots = []
        if not made:
            if not task_has_exportable_data(sandbox.id):
                return False
            # Never raw-dump while still fetching — enter analyze first
            # (unless prefer_fallback: force finalize and continue writing)
            if export_phase == "fetch":
                if prefer_fallback:
                    _enter_export_finalize(reason or "强制收工写表")
                else:
                    _enter_export_analyze_or_finalize(reason or "收尾前先进入分析")
                    return False
            # Hybrid: script on disk → engine run before more LLM nudges / grace
            grace_done = _export_write_grace_done(
                shell_enabled=shell_enabled,
                analyze_rounds_used=analyze_rounds_used,
                analyze_max_rounds=analyze_max_rounds,
            )
            scripts_ready = bool(find_export_build_scripts(sandbox.id))
            if (
                export_phase == "analyze"
                and not missing_roles_gate
                and scripts_ready
                and not analyze_write_shell_ok
            ):
                if _try_engine_run_build_script(force=prefer_fallback or grace_done):
                    roots = _collect_analyze_deliverables()
                    if roots:
                        made = roots[0]
                        mode = "analyzed"
                        export_fallback = False
                        engine_executed = True
                        engine_script_executed = True
            if not made and export_phase == "analyze" and not prefer_fallback and not grace_done:
                if scripts_ready and not engine_script_ran:
                    # Script present: run now instead of empty SHELL nudge
                    if _try_engine_run_build_script(force=True):
                        roots = _collect_analyze_deliverables()
                        if roots:
                            made = roots[0]
                            mode = "analyzed"
                            export_fallback = False
                            engine_executed = True
                            engine_script_executed = True
                if not made:
                    messages.append({
                        "role": "user",
                        "content": (
                            "【催写表】请由你 SHELL 完成分析交付，勿依赖平台原始宽表。\n"
                            + _shell_task_card_now()
                        ),
                    })
                    _append_progress(progress_lines, f"催 LLM SHELL 写表 {reason}")
                    return False
            # After grace / prefer_fallback: engine script → platform join → raw
            if not made and (prefer_fallback or grace_done):
                if not missing_roles_gate and not analyze_write_shell_ok:
                    if _try_engine_run_build_script(force=True):
                        roots = _collect_analyze_deliverables()
                        if roots:
                            made = roots[0]
                            mode = "analyzed"
                            export_fallback = False
                            engine_executed = True
                            engine_script_executed = True
                missing_roles_pre = _missing_export_roles(
                    sandbox.id,
                    export_run_id,
                    export_target_roles,
                    abandoned_roles=export_abandoned_roles,
                )
                wants_platform_join = bool(
                    export_column_plan
                    and not analyze_write_shell_ok
                    and not made
                )
                if wants_platform_join and (not missing_roles_pre or prefer_fallback):
                    col_headers = (
                        list(export_deliverable_headers)
                        if export_deliverable_headers
                        else _export_analyze_column_texts(export_todos)
                    )
                    plan_join = list(export_column_plan or [])
                    if prefer_fallback and missing_roles_pre and plan_join:
                        miss_set = set(missing_roles_pre) | set(export_abandoned_roles)
                        plan_join = mark_incomplete_columns(
                            plan_join,
                            miss_set,
                            mark_note=False,
                        )
                    preferred_join = (
                        Path(export_fill_target_rel).name
                        if export_fill_target_rel
                        else f"用户分析_{export_run_id}.xlsx"
                    )
                    analyzed = ""
                    if export_fill_target_rel and export_fill_columns:
                        analyzed = merge_focus_into_prior_deliverable(
                            sandbox_id=sandbox.id,
                            run_id=export_run_id,
                            prior_rel=export_fill_target_rel,
                            focus_columns=export_fill_columns,
                            column_plan=plan_join or None,
                            time_window=export_time_window,
                            title=str(
                                (export_time_window or {}).get("label") or "导出数据"
                            ),
                        ) or ""
                        if analyzed:
                            _append_progress(
                                progress_lines,
                                f"补齐已覆盖原交付 `{analyzed}`",
                            )
                    if not analyzed:
                        analyzed = write_export_deliverable(
                            sandbox.id,
                            export_run_id,
                            time_window=export_time_window,
                            column_headers=col_headers or None,
                            preferred_name=preferred_join,
                            column_plan=plan_join or None,
                            title=str(
                                (export_time_window or {}).get("label") or "导出数据"
                            ),
                        ) or ""
                    if not analyzed:
                        analyzed = materialize_analyzed_export(
                            sandbox.id,
                            export_run_id,
                            time_window=export_time_window,
                            column_headers=col_headers or None,
                            preferred_name=preferred_join,
                            target_dir=save_dir,
                            column_plan=plan_join or None,
                        ) or ""
                    if analyzed:
                        p_an = download_path(sandbox.id, analyzed)
                        headers_an = read_deliverable_headers(p_an) if p_an else []
                        ok_hdr, _miss_hdr = _todos_satisfied(headers_an, export_todos)
                        ok_g, _hdrs_g, _miss_g = _deliverable_passes_gate(analyzed)
                        accept = bool(
                            p_an
                            and is_valid_deliverable_file(p_an)
                            and (prefer_fallback or ok_hdr or ok_g)
                        )
                        if accept:
                            made = analyzed
                            mode = "analyzed"
                            platform_assisted = True
                            export_fallback = bool(missing_roles_pre)
                            _mark_column_todos_from_headers(export_todos, headers_an)
                            _append_progress(
                                progress_lines, f"平台辅助 join 落盘 {made}",
                            )
            if not made and (prefer_fallback or grace_done):
                preferred = f"export_{export_run_id}.xlsx"
                made = materialize_export_deliverable(
                    sandbox.id,
                    preferred_name=preferred,
                    target_dir=save_dir,
                    ignore_existing=True,
                )
                if not made:
                    return False
                mode = "fallback"
                export_fallback = True
                platform_assisted = False
                engine_executed = False
                _, _, missing = _deliverable_passes_gate(made)
                if not missing:
                    missing = [
                        str(t.get("text") or "")
                        for t in export_todos
                        if t.get("phase") == "analyze" and not t.get("done")
                    ]
                export_missing_cols = missing
                _append_progress(progress_lines, f"原始回退落盘 {made}")
            if not made:
                return False
        if made not in saved_paths:
            saved_paths.append(made)
        # Always mark FINAL todo once we produce a deliverable FINAL
        _mark_todos_phase(export_todos, "finalize", True)
        p = download_path(sandbox.id, made)
        file_size = None
        row_hint = _mcp_total_row_hint(mcp_results)
        if p and p.is_file():
            try:
                file_size = p.stat().st_size
            except OSError:
                file_size = None
            file_rows = count_data_rows(p)
            if file_rows is not None:
                row_hint = file_rows
        todo_done = sum(1 for t in export_todos if t.get("done"))
        analysis_md = ""
        missing_roles_now = _missing_export_roles(
            sandbox.id if sandbox else None,
            export_run_id,
            export_target_roles,
        )
        truncated_roles = sorted({
            _view_category(v)
            for v, n in fetched_view_pages.items()
            if n >= _max_pages_for_view(v, pay_cohort=_pay_cohort, fact_page_cap=export_fact_page_cap, user_page_cap=export_user_page_cap) and _VIEW_IS_FACT(v)
        })
        detail_truncated = bool(truncated_roles)
        if mode == "analyzed" and p and p.is_file():
            headers = read_deliverable_headers(p)
            analysis_md = _build_export_analysis_appendix(
                path=p,
                headers=headers,
                todos=export_todos,
                time_window=export_time_window,
                row_hint=row_hint,
                detail_truncated=detail_truncated,
                truncated_roles=truncated_roles,
                missing_roles=missing_roles_now,
                user_fetch_complete=user_fetch_complete,
                dim_budget_exhausted=export_dim_budget_exhausted,
                cohort_uid_estimate=export_cohort_uid_estimate,
                column_plan=export_column_plan or None,
                file_size=file_size,
            )
            # Assist/engine note is added once in _format_export_final — do not prepend here
        elif mode == "fallback":
            analysis_md = _build_export_fallback_appendix(
                missing=missing or export_missing_cols,
                missing_roles=missing_roles_now,
                row_hint=row_hint,
                time_window=export_time_window,
                analyze_incomplete=not bool(missing_roles_now),
                shell_enabled=shell_enabled,
            )
        covered_roles_now = sorted(
            _covered_export_roles(sandbox.id, export_run_id)
        ) if sandbox and export_run_id else []
        analysis_md = _append_export_skill_lesson_section(
            sandbox,
            export_run_id,
            user_message=task_brief,
            mode=mode,
            deliverable=made,
            covered_roles=covered_roles_now,
            missing_roles=missing_roles_now,
            fetched_view_pages=fetched_view_pages,
            export_todos=export_todos,
            time_window=export_time_window,
            analysis_md=analysis_md,
            mcp_failures=_summarize_mcp_failures(
                mcp_class_fails, mcp_class_samples, mcp_class_tools,
            ),
            fact_truncated_roles=truncated_roles if mode == "analyzed" else None,
            deliverable_rows=row_hint,
            cohort_uid_estimate=export_cohort_uid_estimate,
            skill_ids=skill_ids,
            shell_enabled=shell_enabled,
            session_id=session_id,
        )
        _filt = export_filter_condition or (
            str(export_time_window.get("label") or "") if export_time_window else ""
        )
        # Prefer verifier.summary for 概况 (never LLM-invented counts)
        _vr_fin = (
            export_trace.verification
            if isinstance(getattr(export_trace, "verification", None), dict)
            else {}
        )
        if not _vr_fin and sandbox and made:
            try:
                _sync_export_trace_progress()
                fin = verify_export_finalizer(
                    sandbox_id=sandbox.id,
                    run_id=export_run_id,
                    file_rel=made,
                    task_spec=export_task_spec.to_dict() if export_task_spec else {},
                    column_plan=export_column_plan,
                    trace=export_trace,
                    failed_views=export_failed_views,
                    abandoned_roles=export_abandoned_roles,
                    mode=mode,
                    prefer_fallback=prefer_fallback,
                )
                _vr_fin = fin.verifier_result
            except Exception:
                _vr_fin = {}
        _outcome_fin = outcome_from_verification(
            _vr_fin,
            export_trace.repair_plan
            if isinstance(getattr(export_trace, "repair_plan", None), dict)
            else {},
            mode=mode,
            prefer_fallback=prefer_fallback,
        )
        if _vr_fin and _outcome_fin.should_block_final:
            _append_progress(
                progress_lines,
                "FINAL 拦截：校验未通过 "
                + "；".join(_outcome_fin.blocking_reasons[:3]),
            )
            messages.append({
                "role": "user",
                "content": (
                    "【交付校验未通过】禁止宣称已完成。\n"
                    + format_verifier_block(_vr_fin)
                ),
            })
            return False
        if _outcome_fin.can_deliver:
            repair_appendix = ""
            if str(_outcome_fin.verifier_status or "").lower() == "repairable":
                repair_appendix = (
                    format_verifier_block(_vr_fin)
                    + "\n\n"
                    + format_repair_plan_block(
                        export_trace.repair_plan
                        if isinstance(getattr(export_trace, "repair_plan", None), dict)
                        else {}
                    )
                ).strip()
            analysis_md = (
                build_final_summary_from_verifier(
                    _vr_fin,
                    time_label=_filt,
                )
                + "\n\n"
                + (analysis_md or "")
                + ("\n\n" + repair_appendix if repair_appendix else "")
            ).strip()
            sm = _vr_fin.get("summary") if isinstance(_vr_fin.get("summary"), dict) else {}
            if sm.get("row_count") is not None:
                row_hint = int(sm["row_count"])
            if sm.get("file_size") is not None:
                file_size = int(sm["file_size"])
        final = _format_export_final(
            user_message=task_brief,
            file_rel=made,
            file_size=file_size,
            row_hint=row_hint,
            preview_md="",
            mode=mode,
            todo_done=todo_done,
            todo_total=len(export_todos),
            missing=missing if mode == "fallback" else None,
            analysis_md=analysis_md,
            filter_condition=_filt,
            platform_assisted=platform_assisted,
            engine_executed=engine_executed,
            verifier_status=_outcome_fin.verifier_status,
            shell_enabled=shell_enabled,
        )
        if reason:
            final = f"{final}\n\n（{reason}）"
        had_explicit_final = True
        _persist_run_state(deliverable=made, fallback=(mode == "fallback"))
        _refresh_todo_progress()
        return True

    export_read_idle_rounds = 0

    for i in range(max_iters):
        if not _running.get(key, False):
            final = "[已停止]"
            break

        if not llm:
            final = "未配置 LLM"
            break

        mcp_q_at_turn_start = mcp_query_count
        tools_effective_this_turn = False

        # Schema-first Type-B: metadata discovery must happen before PLAN/fetch.
        if export_like and export_phase == "discover" and sandbox and export_column_plan:
            repair_plan_now = (
                export_trace.repair_plan
                if isinstance(getattr(export_trace, "repair_plan", None), dict)
                else {}
            )
            await _engine_run_schema_discovery_repair(
                repair_plan_now,
                reason=f"轮次{i + 1}发现阶段",
            )
            schema_now = export_summarize_schema_discovery(
                schema_discovery=(
                    export_trace.schema_discovery
                    if isinstance(getattr(export_trace, "schema_discovery", None), dict)
                    else export_schema_discovery
                ),
                export_contract=(
                    export_trace.export_contract
                    if isinstance(getattr(export_trace, "export_contract", None), dict)
                    else None
                ),
            )
            if schema_now.get("complete"):
                export_discover_ready = True

        # Engine pull missing planned roles before burning an LLM turn
        if export_like and export_phase == "fetch" and sandbox:
            await _engine_fetch_planned_roles(reason=f"轮次{i + 1}前补缺")
            if export_phase != "fetch":
                # may have entered analyze
                pass
            _append_progress(
                progress_lines,
                _format_llm_mcp_progress(
                    llm_iter=i + 1,
                    max_iters=max_iters,
                    mcp_query_count=mcp_query_count,
                    mcp_budget=mcp_query_budget,
                ),
            )
            _upsert_progress_message(messages, progress_lines)

        # Export phase transitions
        if export_like and export_phase == "discover":
            discover_deadline = max(1, int(max_iters * _EXPORT_DISCOVER_RATIO))
            if export_discover_ready or i >= discover_deadline:
                _enter_export_plan(
                    "发现完成，进入 PLAN" if export_discover_ready else "发现超时，进入 PLAN"
                )
        if export_like and export_phase == "plan" and not plan_done:
            schema_now = export_summarize_schema_discovery(
                schema_discovery=(
                    export_trace.schema_discovery
                    if isinstance(getattr(export_trace, "schema_discovery", None), dict)
                    else export_schema_discovery
                ),
                export_contract=(
                    export_trace.export_contract
                    if isinstance(getattr(export_trace, "export_contract", None), dict)
                    else None
                ),
            )
            if _should_auto_accept_export_plan(
                export_phase=export_phase,
                plan_done=plan_done,
                export_view_mode=export_view_mode,
                column_plan=export_column_plan,
                task_validation_status=str(
                    getattr(export_task_validation, "status", "") or ""
                ),
                schema_summary=schema_now,
            ):
                _enter_export_fetch(
                    mcp_query_budget,
                    "结构化 TaskSpec/Schema 已确认，自动进入 fetch",
                )

        # Roles complete → platform write first (Codex one-pass); else analyze
        if export_like and export_phase == "fetch" and _roles_ready_for_analyze():
            if _try_platform_write_finish("角色齐套，平台写表"):
                break
            _enter_export_analyze("角色齐套，进入 SHELL 分析")

        # Time-window: fetch → analyze (not instant materialize); do not clobber discover/plan.
        time_window = i >= export_finalize_from and (
            not export_like or plan_done or export_phase in ("fetch", "analyze", "finalize")
        )
        if time_window and export_phase == "fetch":
            if _still_full_with_budget():
                _append_progress(
                    progress_lines,
                    "轮次收尾暂缓：仍满页且有预算，继续 OFFSET/引擎代拉",
                )
                _upsert_progress_message(messages, progress_lines)
            else:
                _enter_export_analyze_or_finalize("轮次收尾，进入分析")

        # Fetch/analyze: root gated xlsx → accept as 分析交付（缺明细时硬门禁会拒绝）
        if (
            export_like
            and sandbox
            and export_phase in ("fetch", "analyze")
        ):
            early_roots = _collect_analyze_deliverables()
            if early_roots:
                miss_early = _missing_export_roles(
            sandbox.id,
            export_run_id,
            export_target_roles,
            abandoned_roles=export_abandoned_roles,
        )
                if _facts_ok_for_delivery(miss_early):
                    _enter_export_finalize("根目录分析产物已就绪")
                    if _try_export_hard_finish("已使用 SHELL 分析结果落盘"):
                        break
                elif mcp_query_budget - mcp_query_count > 0:
                    fact_need = _blocking_roles_for_analyzed_delivery(
                        export_target_roles=export_target_roles,
                        missing_roles=miss_early,
                    )
                    messages.append({
                        "role": "user",
                        "content": (
                            "【禁止分析交付】仍缺 role："
                            + "、".join(fact_need)
                            + "。请先拉齐后再写表 FINAL（缺 user 时禁止跨旧 run 拼表）。\n"
                            + _force_fact_fetch_message(
                                [r for r in fact_need if r in ("pay", "cash", "bet")]
                            )
                        ),
                    })

        in_export_analyze = export_like and export_phase == "analyze"
        in_export_finalize = export_like and (
            export_phase == "finalize" or export_force_finalize
        )
        miss_for_mcp: list[str] = []
        if export_like and sandbox and in_export_analyze:
            miss_for_mcp = _missing_export_roles(
            sandbox.id,
            export_run_id,
            export_target_roles,
            abandoned_roles=export_abandoned_roles,
        )
        # Allow MCP during analyze when roles still missing (hybrid refill)
        block_mcp = in_export_finalize or (in_export_analyze and not miss_for_mcp)

        # Analyze: finish when SHELL deliverable exists or rounds exhausted (after write attempt)
        if in_export_analyze and sandbox:
            roots = _collect_analyze_deliverables()
            miss_an = _missing_export_roles(
            sandbox.id,
            export_run_id,
            export_target_roles,
            abandoned_roles=export_abandoned_roles,
        ) if sandbox else []
            facts_ok_an = _facts_ok_for_delivery(miss_an)
            if not shell_enabled and not miss_an and task_has_exportable_data(sandbox.id):
                _enter_export_finalize("SHELL 已禁用，平台直接分析写表")
                if _try_platform_write_finish("无 SHELL 模式自动交付"):
                    break
            elif (
                not shell_enabled
                and miss_an
                and (mcp_query_budget - mcp_query_count) <= 0
                and task_has_exportable_data(sandbox.id)
            ):
                _enter_export_finalize("SHELL 已禁用且补数预算用尽，平台降级写表")
                if _try_platform_write_finish(
                    "无 SHELL 模式预算用尽后降级交付",
                    prefer_fallback=True,
                ):
                    break
            elif roots and facts_ok_an:
                _enter_export_finalize("分析产物已就绪")
                if _try_export_hard_finish("已使用 SHELL 分析结果落盘"):
                    break
            elif roots and not facts_ok_an and (mcp_query_budget - mcp_query_count) > 0:
                fact_need = _blocking_roles_for_analyzed_delivery(
                    export_target_roles=export_target_roles,
                    missing_roles=miss_an,
                )
                export_phase = "fetch"
                messages.append({
                    "role": "user",
                    "content": (
                        "【禁止分析交付】仍缺 role："
                        + "、".join(fact_need)
                        + "。请先拉齐后再写表 FINAL（缺 user 时禁止跨旧 run 拼表）。\n"
                        + _force_fact_fetch_message(
                            [r for r in fact_need if r in ("pay", "cash", "bet")]
                        )
                    ),
                })
                _append_progress(
                    progress_lines,
                    f"分析回退拉取(缺 {'/'.join(fact_need)})",
                )
            elif roots and not facts_ok_an:
                # No budget left: allow only 原始回退（hard finish demotes 分析交付）
                _enter_export_finalize("缺明细且预算耗尽，原始回退")
                if _try_export_hard_finish(
                    "缺明细 role，已按原始数据落盘并标注缺口",
                    prefer_fallback=True,
                ):
                    break
            elif analyze_rounds_used >= analyze_max_rounds:
                # Do not raw-fallback until write-grace exhausted when no gated file yet
                if analyze_rounds_used >= (
                    analyze_max_rounds + _EXPORT_ANALYZE_WRITE_GRACE
                ):
                    _enter_export_finalize("分析轮次用尽，混合收尾")
                    if _try_export_hard_finish(
                        "分析超时，已混合收尾落盘", prefer_fallback=True
                    ):
                        break
                else:
                    # Script on disk → engine run; else nudge LLM write
                    analyze_hint_injected = False
                    if (
                        not miss_an
                        and find_export_build_scripts(sandbox.id)
                        and _try_engine_run_build_script(force=True)
                    ):
                        _enter_export_finalize("引擎代执行脚本完成")
                        if _try_export_hard_finish("引擎代执行已有分析脚本落盘"):
                            break
                    messages.append({
                        "role": "user",
                        "content": (
                            "【催写表】禁止只 ls/读 json。请立即按任务卡 SHELL 写中文分析表。\n"
                            + _shell_task_card_now()
                        ),
                    })

        # Hard stop only in finalize (after analyze or no-data path)
        if in_export_finalize and sandbox and (
            task_has_exportable_data(sandbox.id)
            or _find_export_root_deliverables(
                sandbox,
                save_dir,
                saved_paths,
                run_id=export_run_id,
                started_at=export_run_started_at,
            )
        ):
            # Prefer gated analyze file; otherwise run-scoped fallback merge
            if _try_export_hard_finish(
                "已达收尾条件，已落盘交付文件"
                if export_force_finalize
                else "轮次接近上限，已根据 task/ 数据自动落盘",
                prefer_fallback=False,
            ):
                break

        if i > 0 and i % _TRIM_TOOL_MSG_EVERY == 0:
            _trim_old_tool_messages(messages, keep_recent=trim_keep, cap=trim_cap)
            _upsert_progress_message(messages, progress_lines)

        if export_like and export_phase == "plan" and not plan_done and not plan_hint_injected:
            messages.append({"role": "user", "content": _plan_hint_with_anchor()})
            plan_hint_injected = True
        elif (
            not export_like
            and generic_phase == "plan"
            and not generic_plan_done
            and not generic_plan_hint_injected
        ):
            messages.append({"role": "user", "content": task_policy.plan_hint})
            generic_plan_hint_injected = True
        elif export_like and export_phase == "discover" and i > 0 and i % 3 == 0:
            # Soft rediscovery coach — do not block tools
            messages.append({
                "role": "user",
                "content": (
                    "【发现阶段提醒】请尽快 list/describe 后进入 PLAN，或样例 query 后输出 PLAN；"
                    "勿空转。\n" + _coach_nudge()
                ),
            })
        elif export_like and export_phase == "fetch" and i > 0 and i % 4 == 0:
            messages.append({"role": "user", "content": _coach_nudge()})
        elif in_export_analyze:
            if not analyze_hint_injected:
                messages.append({"role": "user", "content": _analyze_hint_with_pages()})
                analyze_hint_injected = True
        elif in_export_finalize:
            hint = _finalize_hint(
                save_dir,
                export_like=export_like,
                shell_enabled=shell_enabled,
            )
            if not finalize_hint_injected:
                messages.append({"role": "user", "content": hint})
                finalize_hint_injected = True
            elif export_like and i > export_finalize_from and i % 2 == 0:
                # Re-assert every other remaining turn for export
                messages.append({"role": "user", "content": hint})

        try:
            await _push_step({
                "type": "llm",
                "iteration": i + 1,
                "title": f"LLM 推理 (第 {i + 1} 轮)",
                "status": "running",
            })
            reply = await chat_completion(
                llm, messages, max_tokens=4096, db=db,
                timeout=getattr(agent, "llm_timeout", None),
                cancel_check=lambda: not _running.get(key, False),
            )
        except ChatStopped:
            if run_steps and run_steps[-1].get("type") == "llm":
                await _patch_last_step(status="error", content="已停止")
            final = "[已停止]"
            break
        except Exception as e:
            if run_steps and run_steps[-1].get("type") == "llm":
                await _patch_last_step(status="error")
            intent_bits: list[str] = []
            if turn_intent.query_goal:
                intent_bits.append(turn_intent.query_goal)
            elif turn_intent.metrics:
                intent_bits.append("、".join(turn_intent.metrics[:6]))
            recover = ""
            if intent_bits:
                recover = (
                    f"\n\n意图已识别：{'；'.join(intent_bits)}。"
                    "请重试，或补充日期/口径后再问。"
                )
            final = f"LLM 错误: {e}{recover}"
            break

        last_reply = reply
        if (
            turn_intent.result_shape in {"time_series", "detail_list", "mixed"}
            and _has_markdown_table(_clean_display_text(reply or ""))
        ):
            structured_reply_candidate = reply
        if run_steps and run_steps[-1].get("type") == "llm":
            await _patch_last_step(status="done", preview=_step_preview(reply))

        if in_export_analyze:
            analyze_rounds_used += 1

        # Generic PLAN gate (non-export): accept PLAN before tools
        if (
            not export_like
            and generic_phase == "plan"
            and not generic_plan_done
        ):
            parsed = parse_generic_plan(reply)
            if parsed:
                generic_plan_done = True
                generic_phase = "act"
                generic_tool_budget = int(parsed.get("budget") or 8)
                generic_todos[:] = build_generic_todos(
                    list(parsed.get("completion_lines") or [])
                )
                no_progress = 0
                _append_progress(
                    progress_lines,
                    f"[generic] PLAN 完成 budget={generic_tool_budget} "
                    f"criteria={len(generic_todos)} policy={task_policy.reason}",
                )
                messages.append({"role": "assistant", "content": reply})
                messages.append({
                    "role": "user",
                    "content": (
                        f"计划已记录（工具预算约 {generic_tool_budget} 次）。"
                        "请按步骤执行；优先遵循已绑定 Skill。"
                        "完成标准："
                        + "；".join(
                            str(t.get("text") or "")
                            for t in generic_todos
                            if t.get("phase") == "act"
                        )
                        + "。达成后输出 FINAL:。"
                    ),
                })
                continue
            has_tool = bool(extract_tool_steps(reply)) or bool(
                detect_action_from_reply(reply, allowed)[0]
            )
            # Prose answer with no tool call: treat as final (chat/Q&A escape)
            prose = _clean_display_text(reply)
            if (
                prose
                and not has_tool
                and not _looks_like_tool_call(reply)
                and not _narrates_tool_without_call(reply)
            ):
                final = prose
                had_explicit_final = True
                break
            generic_plan_attempts += 1
            messages.append({"role": "assistant", "content": reply})
            if generic_plan_attempts >= 2:
                generic_plan_done = True
                generic_phase = "act"
                generic_todos[:] = build_generic_todos(["完成用户请求并 FINAL"])
                messages.append({
                    "role": "user",
                    "content": (
                        "未收到合法 PLAN，已进入执行阶段。"
                        "请直接调用工具完成任务，最后 FINAL:。"
                    ),
                })
            else:
                messages.append({
                    "role": "user",
                    "content": (
                        task_policy.plan_hint
                        if not has_tool
                        else "请先输出 PLAN:（不要调用工具），再开始执行。"
                    ),
                })
            continue

        # Export PLAN gate: accept PLAN before tools
        if export_like and export_phase == "plan" and not plan_done:
            budget = _parse_export_plan_budget(reply, max_budget=export_budget_cap)
            if budget is not None:
                # Refresh column todos from PLAN + original brief (not bare「补齐」话术)
                _plan_col_src = export_source_brief or _todo_brief or task_brief or user_message
                new_todos = _parse_export_todos(_plan_col_src, reply)
                new_cols = _export_analyze_column_texts(new_todos)
                old_cols = _export_analyze_column_texts(export_todos)
                # Keep richer column list if PLAN parse shrinks headers
                if len(new_cols) >= max(4, len(old_cols) // 2) or not old_cols:
                    export_todos[:] = new_todos
                # Soft: brief N vs plan M — prefer brief numbered list, never block FINAL
                _recon_todos, _recon_nudge = _soft_reconcile_export_column_todos(
                    _plan_col_src,
                    list(export_todos),
                    plan_text=reply,
                )
                if _recon_nudge:
                    export_todos[:] = _recon_todos
                    _append_progress(progress_lines, _recon_nudge)
                    messages.append({
                        "role": "system",
                        "content": (
                            f"【列规划软提示】{_recon_nudge}。"
                            "请按用户原编号列继续拉数与写表（含投注/返奖）；勿因列数不齐中止 FINAL。"
                        ),
                    })
                _mark_todos_phase(export_todos, "discover", True)
                analyze_max_rounds = _export_analyze_max_rounds(export_todos)
                plan_route = route_export_view_intent(
                    _plan_col_src,
                    reply,
                )
                export_view_mode, pins_now = _apply_view_intent_route(plan_route)
                export_target_roles[:] = []
                export_pinned_views[:] = list(pins_now)
                if export_view_mode != "single_view":
                    export_column_plan[:] = carry_forward_bound_columns(
                        build_column_plan(_export_analyze_column_texts(export_todos)),
                        prior_plan=export_column_plan or export_prior_column_plan,
                    )
                    try:
                        update = sync_export_contract_state(
                            export_trace,
                            column_plan=export_column_plan,
                            time_window=export_time_window,
                            schema_hints=ads_schema_hints,
                            schema_required=bool(export_column_plan),
                            dim_views=export_dim_views,
                        )
                        export_column_plan[:] = update.column_plan
                        export_schema_discovery = dict(update.schema_discovery or {})
                    except Exception:
                        pass
                    try:
                        wl_now, bind_now = await _export_bind_column_resources(
                            db=db,
                            llm=llm,
                            mcp_ids=mcp_ids or [],
                            column_plan=export_column_plan,
                            column_headers=_export_analyze_column_texts(export_todos),
                            catalog_text=ads_views_list_text,
                            resource_schemas={
                                str(v): (
                                    f"view={v}; comment={getattr(h, 'view_comment', '')}; "
                                    f"fields={','.join(getattr(h, 'fields', []) or [])}"
                                )
                                for v, h in (ads_schema_hints or {}).items()
                            },
                            context_text="\n".join(
                                x for x in [note_content, task_brief, export_source_brief] if str(x or "").strip()
                            ),
                        )
                        export_resource_whitelist[:] = list(wl_now)
                    except Exception:
                        export_resource_whitelist[:] = resolve_export_resource_whitelist(
                            list(export_column_plan),
                            allow_seed=False,
                            gap_seed=False,
                        )
                _append_progress(
                    progress_lines,
                    f"PLAN mode={export_view_mode}"
                    + (f" roles={','.join(export_target_roles)}" if export_target_roles else "")
                    + (
                        f" resources={','.join(export_resource_whitelist[:12])}"
                        if export_resource_whitelist
                        else ""
                    )
                    + (f" pinned={','.join(export_pinned_views)}" if export_pinned_views else ""),
                )
                _enter_export_fetch(budget, f"PLAN 完成，query 预算 {budget}")
                no_progress = 0
                messages.append({"role": "assistant", "content": reply})
                if export_view_mode == "single_view" and export_pinned_views:
                    fetch_order = (
                        "类型 C：只 query "
                        + "、".join(f"`{v}`" for v in export_pinned_views)
                        + "。"
                    )
                    fetch_tail = _adapt_export_prompt_for_capabilities(
                        _EXPORT_FETCH_HINT_TAIL_SINGLE,
                        shell_enabled=shell_enabled,
                    )
                    dim_bit = ""
                    tw_bit = (
                        f" 时间窗：{export_time_window.get('label')}。"
                        if export_time_window
                        else ""
                    )
                else:
                    res_hint = (
                        "、".join(export_resource_whitelist[:8])
                        if export_resource_whitelist
                        else ""
                    )
                    fetch_order = (
                        "顺序：按 PLAN「需要资源」/列计划白名单拉取"
                        + ("（资源：" + res_hint + "）" if res_hint else "")
                        + "。禁止默认全量拉取；仅执行已绑定资源对应的查询图节点。"
                    )
                    fetch_tail = _adapt_export_prompt_for_capabilities(
                        _EXPORT_FETCH_HINT_TAIL,
                        shell_enabled=shell_enabled,
                    )
                    dim_bit = ""
                    tw_bit = (
                        f" 时间窗：{export_time_window.get('label')}；"
                        f"user_info 必须：`{export_time_window.get('mcp_example')}`。"
                        if export_time_window
                        else ""
                    )
                messages.append({
                    "role": "user",
                    "content": (
                        f"计划已记录。请按预算（最多 {mcp_query_budget} 次 query）拉取数据；"
                        "平台会自动落盘 task/page_N.json。"
                        f"{fetch_order}"
                        f"{fetch_tail}"
                        + dim_bit
                        + tw_bit
                        + (
                            "拉数结束后必须用 SHELL 做关联/计算并写当前目录 xlsx，再 FINAL。"
                            if shell_enabled
                            else "拉数结束后由平台做关联/计算、写 xlsx 并校验。"
                        )
                    ),
                })
                continue
            # Block tools during plan phase
            has_tool = bool(extract_tool_steps(reply)) or bool(detect_action_from_reply(reply, allowed)[0])
            plan_attempts += 1
            messages.append({"role": "assistant", "content": reply})
            if plan_attempts >= _EXPORT_PLAN_MAX_ATTEMPTS:
                _enter_export_fetch(
                    mcp_query_budget,
                    f"PLAN 超时，使用预算 {mcp_query_budget}",
                )
                messages.append({
                    "role": "user",
                    "content": (
                        f"未收到合法 PLAN，已使用预算 {mcp_query_budget}。"
                        + _adapt_export_prompt_for_capabilities(
                            _EXPORT_FETCH_HINT_TAIL,
                            shell_enabled=shell_enabled,
                        )
                        + _dim_views_hint()
                        + (
                            f" 时间窗：{export_time_window.get('label')}；"
                            f"示例 `{export_time_window.get('mcp_example')}`。"
                            if export_time_window
                            else ""
                        )
                        + (
                            "请开始限量 query；平台自动落盘；随后 SHELL 分析再 FINAL。"
                            if shell_enabled
                            else "请开始限量 query；平台自动落盘，齐套后自动分析并写表。"
                        )
                    ),
                })
            else:
                messages.append({
                    "role": "user",
                    "content": (
                        _plan_hint_with_anchor()
                        if not has_tool
                        else "请先输出 PLAN:（不要调用工具），再开始限量拉取。\n"
                        + _coach_nudge()
                    ),
                })
            continue

        async def _run_tool(action: str, normalized: str) -> str | None:
            nonlocal files_written, mcp_results, export_discover_ready, export_discover_queries
            nonlocal mcp_query_count, ran_any_tool, analyze_write_shell_ok
            nonlocal analyze_explore_shells, user_fetch_complete, analyze_empty_reject_count
            nonlocal ads_views_list_text, ads_schema_hints, export_schema_discovery, export_dim_views
            nonlocal final, analyze_rounds_used
            nonlocal fetched_view_last_rows, user_fetch_complete
            nonlocal export_read_idle_rounds
            nonlocal tools_effective_this_turn, soft_finish_requested, empty_llm_streak
            nonlocal export_column_plan, export_target_roles
            ran_any_tool = True
            autofill_notes: list[str] = []

            async def _preflight_query_sql_schema(
                norm: str,
            ) -> tuple[str, str | None, list[str]]:
                """Auto-describe view then soft-align SQL columns; skip remote query if unknowns remain."""
                nonlocal ads_schema_hints, export_schema_discovery, export_column_plan
                nonlocal export_target_roles, mcp_query_count
                args = _parse_mcp_args(norm)
                view = str(args.get("view") or "").strip()
                sql = str(args.get("sql") or "").strip()
                notes: list[str] = []
                if not view or not sql:
                    return norm, None, notes
                hint = ads_schema_hints.get(view) if isinstance(ads_schema_hints, dict) else None
                fields = list(getattr(hint, "fields", None) or []) if hint else []
                if not fields:
                    desc_line = "MCP: describe_ads_view " + json.dumps(
                        {"view_name": view}, ensure_ascii=False,
                    )
                    await _push_step({
                        "type": "tool",
                        "action": "mcp_tool_call",
                        "title": f"列预检 · describe {view}",
                        "content": "【schema 预检】query 前自动 describe，用真实列校验 SQL",
                        "status": "running",
                    })
                    try:
                        desc_result = await execute_action(
                            "mcp_tool_call",
                            desc_line,
                            db,
                            agent,
                            sandbox,
                            skill_ids,
                            mcp_ids,
                            httpmcp_ids,
                            rag_ids,
                        ) or ""
                    except Exception as ex:
                        await _patch_last_step(
                            status="error",
                            content=f"【schema 预检】describe 异常: {ex}"[:400],
                        )
                        return norm, None, ["describe 异常，跳过列预检"]
                    mcp_query_count += 1
                    if desc_result and not _is_mcp_tool_failure(desc_result):
                        schema_hint = parse_describe_schema_hint(desc_result, view=view)
                        if schema_hint.fields:
                            ads_schema_hints[view] = schema_hint
                            fields = list(schema_hint.fields)
                            try:
                                update = record_schema_hint(
                                    export_trace,
                                    view=view,
                                    schema_hint=schema_hint,
                                    column_plan=export_column_plan,
                                    time_window=export_time_window,
                                    schema_hints=ads_schema_hints,
                                    dim_views=export_dim_views,
                                )
                                export_column_plan[:] = update.column_plan
                                export_schema_discovery = dict(
                                    update.schema_discovery or {}
                                )
                                if sandbox and export_run_id:
                                    write_export_trace(
                                        sandbox.id, export_run_id, export_trace,
                                    )
                            except Exception:
                                logger.exception("schema preflight record_schema_hint failed")
                            await _patch_last_step(
                                status="done",
                                content=(
                                    f"【schema 预检】`{view}` 已记录 {len(fields)} 个字段"
                                )[:800],
                            )
                        else:
                            await _patch_last_step(
                                status="done",
                                content="【schema 预检】describe 无字段，跳过列校验",
                            )
                            return norm, None, notes
                    else:
                        await _patch_last_step(
                            status="error",
                            content=((desc_result or "describe 失败")[:400]),
                        )
                        # No hard gate: allow query to proceed without column preflight
                        return norm, None, ["describe 失败，跳过列预检"]

                aligned, align_notes, unknown = soft_align_sql_to_schema(sql, fields)
                if align_notes:
                    notes.extend(align_notes)
                    args2 = dict(args)
                    args2["sql"] = aligned
                    norm = "MCP: query_ads_view " + json.dumps(args2, ensure_ascii=False)
                if unknown:
                    notes.append(
                        "【schema 预检】SQL 含 describe 未返回的列: "
                        + ", ".join(unknown[:20])
                        + "。已软对齐，继续发起查询（远端真实错误比本地猜测更有价值）。"
                    )
                # Never block the query — aligned SQL proceeds; schema differences
                # are recorded as notes for the DecisionEngine to observe.
                return norm, None, notes

            if action == "shell":
                _ensure_sandbox_running(db, sandbox)

            # Export fetch/analyze: soft-block READ idle loops (keep discover READ)
            if (
                export_like
                and action == "file_read"
                and export_phase in ("fetch", "analyze")
            ):
                miss_read: list[str] = []
                if sandbox and export_run_id:
                    try:
                        miss_read = _missing_export_roles(
            sandbox.id,
            export_run_id,
            export_target_roles,
            abandoned_roles=export_abandoned_roles,
        )
                    except Exception:
                        miss_read = list(export_target_roles or [])
                has_root = bool(
                    sandbox
                    and _find_export_root_deliverables(
                        sandbox,
                        save_dir,
                        saved_paths,
                        run_id=export_run_id,
                        started_at=export_run_started_at,
                    )
                )
                if _should_soft_block_export_read(
                    phase=export_phase,
                    missing_roles=miss_read,
                    has_root_xlsx=has_root,
                ):
                    export_read_idle_rounds += 1
                    hint = (
                        "【READ 空转拦截】本阶段禁止靠 READ 推进；"
                        "请 MCP query 规划内 view，或 SHELL 写当前目录中文表。"
                    )
                    if miss_read:
                        hint += " 仍缺: " + "、".join(miss_read) + "。"
                    if export_read_idle_rounds >= 2 and export_phase == "fetch":
                        _fetch_idle_tick(had_mcp_data=False)
                        hint += (
                            f"\n连续 READ≥{export_read_idle_rounds} 次已计入拉取空转；"
                            "请立即 MCP query 缺 role 或等待引擎代拉。"
                        )
                    return hint
                export_read_idle_rounds = 0

            # rewrite → autofill early so Type C / 时间窗 / validate 看到补全后的参数
            if action == "mcp_tool_call":
                miss_for_fill: list[str] = []
                if export_like and sandbox and export_run_id:
                    try:
                        miss_for_fill = _missing_export_roles(
            sandbox.id,
            export_run_id,
            export_target_roles,
            abandoned_roles=export_abandoned_roles,
        )
                    except Exception:
                        miss_for_fill = list(export_target_roles or [])
                normalized, autofill_notes = _rewrite_mcp_with_autofill(
                    normalized,
                    time_window=export_time_window if export_like else None,
                    target_roles=export_target_roles if export_like else None,
                    missing_roles=miss_for_fill if export_like else None,
                    fetched_view_pages=fetched_view_pages if export_like else None,
                    dim_views=export_dim_views if export_like else None,
                    soft_export_page_limit=bool(export_like),
                )
                # Soft-align: strip backtick tool names + schema-unknown top-level args
                bound_tools: list[dict] = []
                for mid in mcp_ids or []:
                    mcp_row = db.query(MCP).filter(MCP.id == mid).first()
                    if not mcp_row:
                        continue
                    try:
                        bound_tools.extend(await _get_mcp_tools_cached(mcp_row))
                    except Exception:
                        pass
                normalized, schema_align_notes = soft_align_mcp_line_to_tool_schema(
                    normalized, bound_tools,
                )
                if schema_align_notes:
                    autofill_notes = list(autofill_notes or []) + list(schema_align_notes)
                # Soft-block query_ads_metric during fetch
                tool_metric = (_mcp_tool_name(normalized) or "").lower()
                if _should_soft_block_metric_tool(tool_metric, phase=export_phase):
                    return (
                        "【工具纠偏】导出 fetch 阶段请用 `query_ads_view` 拉规划内明细，"
                        "不要用 `query_ads_metric`。"
                        + (
                            "\n" + _force_fact_fetch_message()
                            if _missing_fact_roles()
                            else ""
                        )
                    )
                export_read_idle_rounds = 0

            # Export analyze/finalize: prefer SHELL when roles covered (soft — not a ban)
            if block_mcp and action in ("mcp_tool_call", "httpmcp_call"):
                return (
                    "【建议】角色已齐/收尾阶段优先 SHELL 写当前目录 xlsx 再 FINAL。"
                    "若仍需补数可改参继续 MCP；勿同参空转。"
                )

            # Soft: identical MCP signatures are nudged in _track_mcp after failure —
            # never pre-block the tool (OpenClaw-style continuous MCP).

            # Type-B with column plan: list/describe views are schema discovery,
            # not idle; only block generic list_tools churn.
            if (
                export_like
                and export_phase == "fetch"
                and export_column_plan
                and action in ("mcp_tool_call", "httpmcp_call")
            ):
                tool_idle = (_mcp_tool_name(normalized) or "").lower()
                if tool_idle == "list_tools" or (
                    "list" in tool_idle and "tool" in tool_idle
                ):
                    return (
                        "【空转拦截·列计划已齐】禁止 list_tools；"
                        "请使用 list_ads_views / describe_ads_view 校准 schema，"
                        "或按已确认 schema 查询规划内 view。"
                    )

            # Type C: pin whitelist — only query/describe named views
            if (
                export_like
                and export_view_mode == "single_view"
                and export_pinned_views
                and action in ("mcp_tool_call", "httpmcp_call")
            ):
                tool_pin = (_mcp_tool_name(normalized) or "").lower()
                if "list" in tool_pin and "view" in tool_pin:
                    pass  # list_ads_views always allowed for Type C
                elif "query" in tool_pin or "describe" in tool_pin:
                    view_pin = (
                        _extract_mcp_view_name(normalized)
                        or str(
                            _parse_mcp_args(normalized).get("view")
                            or _parse_mcp_args(normalized).get("view_name")
                            or ""
                        ).strip()
                    ).lower()
                    allowed_views = {v.lower() for v in export_pinned_views}
                    if view_pin and view_pin not in allowed_views:
                        return (
                            "【视图白名单·类型 C】本轮建议只查询："
                            + "、".join(f"`{v}`" for v in export_pinned_views)
                            + f"。当前 `{view_pin}` 不在点名列表。"
                        )
                    if not view_pin and "query" in tool_pin:
                        return (
                            "【视图白名单·类型 C】query 缺少 view；"
                            "请使用："
                            + "、".join(f"`{v}`" for v in export_pinned_views)
                        )

            # Type B: column/intent resource whitelist — soft-block unlisted query
            if (
                export_like
                and export_view_mode != "single_view"
                and export_resource_whitelist
                and export_phase == "fetch"
                and action in ("mcp_tool_call", "httpmcp_call")
            ):
                tool_wl = (_mcp_tool_name(normalized) or "").lower()
                if "query" in tool_wl:
                    view_wl = (
                        _extract_mcp_view_name(normalized)
                        or str(
                            _parse_mcp_args(normalized).get("view")
                            or _parse_mcp_args(normalized).get("view_name")
                            or ""
                        ).strip()
                    )
                    if view_wl and not _export_resource_allowed(
                        view_wl, export_resource_whitelist
                    ):
                        return (
                            "【导出·资源白名单】当前 view 不在列意图绑定列表："
                            + ", ".join(f"`{v}`" for v in export_resource_whitelist[:12])
                            + f"。勿默认全量拉取；请改查白名单资源（当前 `{view_wl}`）。"
                        )

            # Export fetch: enforce query budget before call
            if (
                export_like
                and action in ("mcp_tool_call", "httpmcp_call")
                and export_phase == "fetch"
                and mcp_query_count >= mcp_query_budget
            ):
                if sandbox and task_has_exportable_data(sandbox.id):
                    if _try_platform_write_finish(
                        "MCP预算用尽，强制平台写表",
                        prefer_fallback=True,
                    ) or _try_export_hard_finish(
                        "MCP预算用尽，强制落盘",
                        prefer_fallback=True,
                    ):
                        return (
                            f"【预算用尽】MCP query 已达上限 {mcp_query_budget}；"
                            "平台已强制合并 task/ 写出当前目录 xlsx，请 FINAL 收工。"
                        )
                _enter_export_analyze_or_finalize("query 预算已用尽")
                return (
                    f"【预算提示】MCP query 已达上限 {mcp_query_budget}。"
                    "请用 SHELL 分析 task/ 并写当前目录 xlsx，再 FINAL；"
                    "若必须续查请在下一会话提高预算。"
                )

            # Export fetch: same-view page cap (do not hit remote / do not burn budget)
            if (
                export_like
                and action in ("mcp_tool_call", "httpmcp_call")
                and export_phase == "fetch"
            ):
                tool_peek = _mcp_tool_name(normalized)
                if _is_mcp_data_query(tool_peek):
                    view_peek = _extract_mcp_view_name(normalized) or str(
                        _parse_mcp_args(normalized).get("view") or ""
                    ).strip()
                    cap = (
                        _max_pages_for_view(
                            view_peek,
                            pay_cohort=_pay_cohort,
                            fact_page_cap=export_fact_page_cap,
                            user_page_cap=export_user_page_cap,
                        )
                        if view_peek
                        else export_fact_page_cap
                    )
                    if view_peek and fetched_view_pages.get(view_peek, 0) >= cap:
                        missing = _missing_export_roles(
                            sandbox.id if sandbox else None,
                            export_run_id,
                            export_target_roles,
                        )
                        note = _format_missing_roles_note(
                            missing,
                            budget_left=mcp_query_budget - mcp_query_count,
                            full_views=_full_views_list() or [view_peek],
                            dim_views=export_dim_views or None,
                        )
                        last_n = int(fetched_view_last_rows.get(view_peek, 0) or 0)
                        still_full = _is_full_page_rows(last_n, view=view_peek)
                        role_peek = _view_category(view_peek)
                        if still_full and role_peek in ("pay", "cash", "bet", "user"):
                            sql_base = ""
                            pages_done = fetched_view_pages.get(view_peek, 0)
                            offset_hint = ""
                            if sql_base:
                                offset_hint = (
                                    "\n请改用更大 OFFSET 或提高页帽后重试；建议："
                                    + _format_offset_mcp_example(
                                        view_peek,
                                        sql_base,
                                        pages_done,
                                        limit=_page_limit_for_view(view_peek),
                                    )
                                )
                            return (
                                f"【同 view 限流·未短页】`{view_peek}` 已落盘 "
                                f"{pages_done} 页（动态上限 {cap}），末页仍满页"
                                f"（{last_n} 行）。本次不调用远程、不占预算。"
                                "一次拉全未完成——FINAL 须标「未标完整」。"
                                + offset_hint
                                + note
                            )
                        return (
                            f"【同 view 限流】`{view_peek}` 已落盘 "
                            f"{fetched_view_pages.get(view_peek, 0)} 页"
                            f"（上限 {cap}），本次不调用远程、不占预算。"
                            "请改查其他 view 或 SHELL 写表。"
                            + note
                        )

            # Export discover: at most one sample query
            if export_like and action in ("mcp_tool_call", "httpmcp_call") and export_phase == "discover":
                tool_peek = _mcp_tool_name(normalized)
                if _is_mcp_data_query(tool_peek) and export_discover_queries >= 1:
                    export_discover_ready = True
                    _enter_export_plan("样例 query 已完成，请先 PLAN")
                    return (
                        "【发现阶段】样例 query 已完成，禁止继续分页。"
                        f"请先输出 PLAN:（含分页预算 N≤{export_budget_cap} 与 SHELL 分析步骤；"
                        f"页长 fact={_EXPORT_PAGE_LIMIT}/user={_EXPORT_USER_PAGE_LIMIT}）。"
                    )

            def _track_mcp(
                tool: str,
                failed: bool,
                normalized: str = "",
                *,
                error_text: str = "",
            ) -> str:
                """Soft hints on MCP failures; never hard-blocks tools (OpenClaw-style)."""
                if not tool:
                    return ""
                mcp_tool_calls[tool] = mcp_tool_calls.get(tool, 0) + 1
                if not failed:
                    return ""

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
                    # Soft only — never add tool to hard-block set (OpenClaw-style)
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

            if action == "mcp_tool_call":
                early = _mcp_local_validate(
                    normalized,
                    time_window=export_time_window if export_like else None,
                    target_roles=export_target_roles if export_like else None,
                )
                if early:
                    tool = _mcp_tool_name(normalized)
                    enriched = early
                    if autofill_notes:
                        enriched = (
                            "【参数自动补全（仍未通过）】"
                            + "；".join(autofill_notes)
                            + "\n"
                            + enriched
                        )
                    await _push_step({
                        "type": "tool",
                        "action": action,
                        "title": f"MCP 参数纠偏 · {tool}",
                        "content": (enriched or "")[:800],
                        "status": "done",
                        "hidden": False,
                    })
                    await _publish_tool_ws(enriched, action, hidden=False)
                    return enriched

                # Describe-backed SQL column soft preflight (no business hard gate)
                if _mcp_tool_name(normalized) == "query_ads_view":
                    normalized, schema_soft, schema_notes = await _preflight_query_sql_schema(
                        normalized,
                    )
                    if schema_notes:
                        autofill_notes = list(autofill_notes or []) + list(schema_notes)
                    if schema_soft:
                        tool = "query_ads_view"
                        enriched = schema_soft
                        enriched += _track_mcp(
                            tool, failed=True, normalized=normalized, error_text=enriched,
                        )
                        await _push_step({
                            "type": "tool",
                            "action": action,
                            "title": f"MCP 列预检 · {tool}",
                            "content": (enriched or "")[:800],
                            "status": "error",
                            "hidden": False,
                        })
                        await _publish_tool_ws(enriched, action, hidden=False)
                        return enriched

            tool_result = await execute_action(
                action, normalized, db, agent, sandbox,
                skill_ids, mcp_ids, httpmcp_ids, rag_ids,
            )
            if not tool_result:
                return None
            if action in ("mcp_tool_call", "httpmcp_call"):
                failed = _is_mcp_tool_failure(tool_result)
                tool = _mcp_tool_name(normalized)
                mcp_label = _primary_mcp_name(db, mcp_ids)
                if (
                    action == "mcp_tool_call"
                    and autofill_notes
                    and not failed
                ):
                    tool_result = (
                        "【参数自动补全】"
                        + "；".join(autofill_notes)
                        + "\n"
                        + (tool_result or "")
                    )
                if not failed:
                    _record_mcp_result(
                        mcp_results,
                        normalized,
                        tool_result,
                        export_like=export_like,
                        result_shape=turn_intent.result_shape,
                    )
                    rows = _parse_mcp_rows(tool_result)
                    if (
                        not export_like
                        and rows
                        and re.search(r"query", tool or "", re.I)
                    ):
                        tool_result = _format_generic_query_observation(
                            tool_result, rows,
                        )
                    auto_note = ""
                    if export_like:
                        tool_l = (tool or "").lower()
                        if "list" in tool_l and "view" in tool_l:
                            ads_views_list_text = tool_result or ""
                            try:
                                export_schema_discovery = record_schema_list(
                                    export_trace,
                                    ads_views_list_text,
                                )
                                if sandbox and export_run_id:
                                    write_export_trace(sandbox.id, export_run_id, export_trace)
                            except Exception:
                                logger.exception("record ads views schema discovery failed")
                        if "describe" in tool_l:
                            export_discover_ready = True
                            # Also try resolve from describe payload (view name in args)
                            desc_view = _extract_mcp_view_name(normalized) or str(
                                _parse_mcp_args(normalized).get("view_name")
                                or _parse_mcp_args(normalized).get("view")
                                or ""
                            ).strip()
                            if desc_view:
                                schema_hint = parse_describe_schema_hint(
                                    tool_result or "",
                                    view=desc_view,
                                )
                                if schema_hint.fields:
                                    ads_schema_hints[desc_view] = schema_hint
                                    try:
                                        update = record_schema_hint(
                                            export_trace,
                                            view=desc_view,
                                            schema_hint=schema_hint,
                                            column_plan=export_column_plan,
                                            time_window=export_time_window,
                                            schema_hints=ads_schema_hints,
                                            dim_views=export_dim_views,
                                        )
                                        export_column_plan[:] = update.column_plan
                                        export_schema_discovery = dict(update.schema_discovery or {})
                                        if sandbox and export_run_id:
                                            write_export_trace(
                                                sandbox.id,
                                                export_run_id,
                                                export_trace,
                                            )
                                    except Exception:
                                        logger.exception(
                                            "record ads view schema discovery failed"
                                        )
                                    auto_note += (
                                        f"\n\n【schema】`{desc_view}` "
                                        f"已记录 {len(schema_hint.fields)} 个字段；"
                                        "列计划已按真实字段软校准。"
                                    )
                        is_data = _is_mcp_data_query(tool) or bool(rows)
                        if is_data and rows and sandbox:
                            if export_phase == "discover":
                                export_discover_queries += 1
                                export_discover_ready = True
                            mcp_query_count += 1
                            view_name = _extract_mcp_view_name(normalized)
                            args_now = _parse_mcp_args(normalized)
                            sql_now = str(args_now.get("sql") or "")
                            is_count_q = _is_count_sql(sql_now)
                            if is_count_q:
                                cnt_n = _parse_count_from_mcp_rows(rows)
                                if cnt_n:
                                    _apply_cohort_estimate_from_count(cnt_n)
                                    auto_note += (
                                        f"\n\n【COUNT】解析到目标≈{cnt_n}；"
                                        f"已抬用户页帽={export_user_page_cap}、"
                                        f"事实页帽={export_fact_page_cap}、"
                                        f"预算={mcp_query_budget}。"
                                        "请开始 "
                                        f"limit:{_EXPORT_PAGE_LIMIT} 分页；"
                                        "满页由平台自动 OFFSET 续翻至短页。"
                                    )
                            rel = write_task_json_page(
                                sandbox.id,
                                rows,
                                run_id=export_run_id,
                                page=mcp_query_count,
                                view=view_name,
                            )
                            if rel and not is_count_q:
                                if view_name and export_phase in ("fetch", "discover", "analyze"):
                                    fetched_view_pages[view_name] = (
                                        fetched_view_pages.get(view_name, 0) + 1
                                    )
                                    fetched_view_last_rows[view_name] = len(rows)
                                role_landed = (
                                    _view_category(view_name) if view_name else ""
                                )
                                matched_node = None
                                if view_name and sql_now:
                                    sql_sig = " ".join(sql_now.split())
                                    for qn in (export_trace.query_graph or []):
                                        if not isinstance(qn, dict):
                                            continue
                                        if str(qn.get("view") or "") != view_name:
                                            continue
                                        if " ".join(str(qn.get("sql") or "").split()) == sql_sig:
                                            matched_node = qn
                                            break
                                trace_key = (
                                    str((matched_node or {}).get("key") or "")
                                    or f"direct:{view_name}:{sql_now[:120]}"
                                )
                                page_lim = _page_limit_for_view(view_name)
                                try:
                                    req_lim = int(args_now.get("limit") or 0)
                                except (TypeError, ValueError):
                                    req_lim = 0
                                if req_lim > 0:
                                    page_lim = req_lim
                                if matched_node:
                                    rows_decision = build_query_node_rows_decision(
                                        matched_node,
                                        row_count=len(rows),
                                        full_page_rows=_full_page_row_threshold(page_lim),
                                        oneshot_modes=_query_node_oneshot_modes(),
                                    )
                                    if rows_decision.mark_node_done:
                                        export_done_node_keys.add(trace_key)
                                    if (
                                        rows_decision.user_fetch_complete
                                        and _is_short_page_complete(
                                            len(rows), view=view_name, limit=page_lim,
                                        )
                                    ):
                                        user_fetch_complete = True
                                    elif _looks_like_remote_tiny_page_cap(
                                        len(rows), requested_limit=page_lim,
                                    ):
                                        user_fetch_complete = False
                                        auto_note += (
                                            "\n【取数纠偏】本页仅约30行且请求 limit 较大，"
                                            "疑似远端默认短页——未标拉全；将按 "
                                            f"{_EXPORT_PAGE_LIMIT}/{_EXPORT_USER_PAGE_LIMIT} "
                                            "对齐后继续取数。"
                                        )
                                try:
                                    export_trace.record_query(
                                        key=trace_key,
                                        role=role_landed,
                                        view=view_name,
                                        sql=sql_now,
                                        row_count=len(rows),
                                        status="ok",
                                        truncated=_is_full_page_rows(
                                            len(rows), view=view_name, limit=page_lim,
                                        ),
                                    )
                                    _sync_export_trace_progress(write=True)
                                except Exception:
                                    pass
                                auto_note += (
                                    f"\n\n【平台自动落盘】已写入 `{rel}`"
                                    f"（query {mcp_query_count}/{mcp_query_budget}）。"
                                )
                                if (
                                    export_phase == "fetch"
                                    and view_name
                                    and role_landed in ("user", "pay", "cash", "bet")
                                ):
                                    if _is_full_page_rows(
                                        len(rows), view=view_name, limit=page_lim,
                                    ):
                                        if role_landed == "user":
                                            user_fetch_complete = False
                                        sql_base = _sql_base_for_pagination(
                                            sql_now,
                                            role_landed,
                                            export_time_window,
                                        )
                                        # Engine auto OFFSET (up to AUTO_OFFSET_MAX)
                                        auto_done = 0
                                        while True:
                                            pages_done = int(
                                                fetched_view_pages.get(view_name, 0) or 0
                                            )
                                            last_n = int(
                                                fetched_view_last_rows.get(view_name, 0) or 0
                                            )
                                            page_cap = _max_pages_for_view(
                                                view_name,
                                                pay_cohort=_pay_cohort,
                                                fact_page_cap=export_fact_page_cap,
                                                user_page_cap=export_user_page_cap,
                                            )
                                            if not _should_auto_offset_continue(
                                                phase=export_phase,
                                                last_page_rows=last_n,
                                                pages_done=pages_done,
                                                page_cap=page_cap,
                                                budget_left=(
                                                    mcp_query_budget - mcp_query_count
                                                ),
                                                auto_done=auto_done,
                                                page_limit=page_lim,
                                                view=view_name,
                                            ):
                                                if (
                                                    pages_done >= page_cap
                                                    and _is_full_page_rows(
                                                        last_n,
                                                        view=view_name,
                                                        limit=page_lim,
                                                    )
                                                ):
                                                    auto_note += (
                                                        f"\n【平台自动续翻】`{view_name}` "
                                                        f"已达页帽 {page_cap}，末页仍满页"
                                                        f"（{last_n} 行）——"
                                                        "未标完整，停止自动翻页。"
                                                    )
                                                break
                                            if not sql_base:
                                                auto_note += (
                                                    f"\n【平台自动续翻】缺少 `{view_name}` "
                                                    "sql，无法 OFFSET。"
                                                )
                                                break
                                            offset = _offset_for_pages_done(
                                                pages_done, limit=page_lim,
                                            )
                                            sql_next = _sql_with_offset(
                                                sql_base, offset, limit=page_lim,
                                            )
                                            norm_auto = (
                                                "MCP: query_ads_view "
                                                + json.dumps(
                                                    {
                                                        "view": view_name,
                                                        "sql": sql_next,
                                                        "limit": page_lim,
                                                    },
                                                    ensure_ascii=False,
                                                )
                                            )
                                            try:
                                                tr_auto = await execute_action(
                                                    "mcp_tool_call",
                                                    norm_auto,
                                                    db,
                                                    agent,
                                                    sandbox,
                                                    skill_ids,
                                                    mcp_ids,
                                                    httpmcp_ids,
                                                    rag_ids,
                                                )
                                            except Exception as ex:
                                                logger.exception(
                                                    "auto offset mcp failed view=%s",
                                                    view_name,
                                                )
                                                auto_note += (
                                                    f"\n【平台自动续翻】失败：{ex}"
                                                )
                                                break
                                            if (
                                                not tr_auto
                                                or _is_mcp_tool_failure(tr_auto)
                                            ):
                                                auto_note += (
                                                    f"\n【平台自动续翻】OFFSET={offset} "
                                                    "调用失败，停止自动翻页。"
                                                )
                                                break
                                            next_rows = _parse_mcp_rows(tr_auto) or []
                                            mcp_query_count += 1
                                            auto_done += 1
                                            rel_auto = write_task_json_page(
                                                sandbox.id,
                                                next_rows,
                                                run_id=export_run_id,
                                                page=mcp_query_count,
                                                view=view_name,
                                            )
                                            fetched_view_pages[view_name] = (
                                                pages_done + 1
                                            )
                                            fetched_view_last_rows[view_name] = len(
                                                next_rows
                                            )
                                            is_short = _is_short_page_complete(
                                                len(next_rows),
                                                view=view_name,
                                                limit=page_lim,
                                            )
                                            if role_landed == "user":
                                                user_fetch_complete = is_short
                                            auto_note += (
                                                f"\n【平台自动续翻】`{view_name}` "
                                                f"第 {pages_done + 1} 页"
                                                f"（OFFSET={offset}，"
                                                f"{len(next_rows)} 行"
                                                + ("，短页" if is_short else "，满页")
                                                + (
                                                    f"；已写 `{rel_auto}`"
                                                    if rel_auto
                                                    else ""
                                                )
                                                + f"；query {mcp_query_count}/"
                                                f"{mcp_query_budget}）"
                                            )
                                            _append_progress(
                                                progress_lines,
                                                f"自动续翻 {view_name} "
                                                f"p{pages_done + 1} "
                                                f"({len(next_rows)} 行)",
                                            )
                                            if is_short:
                                                if role_landed == "user":
                                                    auto_note += (
                                                        "\n【用户主表】已出现短页，"
                                                        "请先拉列计划内维表/明细至短页；"
                                                        "勿默认齐拉 bet。"
                                                        + _dim_views_hint()
                                                    )
                                                break
                                    elif role_landed == "user" and _is_short_page_complete(
                                        len(rows), view=view_name, limit=page_lim,
                                    ):
                                        user_fetch_complete = True
                                        auto_note += (
                                            "\n【用户主表】已出现短页，请先拉列计划内 "
                                            "维表/明细至短页；勿默认齐拉 bet。"
                                            + _dim_views_hint()
                                        )
                                if export_phase == "fetch":
                                    auto_note += _missing_roles_note()
                                _append_progress(
                                    progress_lines,
                                    f"自动落盘 {rel} ({len(rows)} 行)",
                                )
                                if export_phase in ("fetch", "discover") and mcp_query_count >= 1:
                                    _mark_todos_phase(export_todos, "fetch", True)
                                    _refresh_todo_progress()
                                _upsert_progress_message(messages, progress_lines)
                                _persist_run_state()
                            elif rel and is_count_q:
                                auto_note += (
                                    f"\n\n【平台落盘 COUNT】`{rel}`"
                                    f"（query {mcp_query_count}/{mcp_query_budget}）。"
                                )
                                _persist_run_state()
                            # Roles + user short-page + facts not mid-page → analyze
                            if (
                                export_phase == "fetch"
                                and _roles_ready_for_analyze()
                            ):
                                _enter_export_analyze("短页齐套，进入 SHELL 分析")
                                auto_note += (
                                    "\n【一次拉全·短页齐套】user 已短页/达帽且明细短页；"
                                    "建议停止 MCP query，立即 SHELL 写当前目录中文表头 xlsx。"
                                )
                            elif (
                                export_phase == "fetch"
                                and not _missing_export_roles(
            sandbox.id,
            export_run_id,
            export_target_roles,
            abandoned_roles=export_abandoned_roles,
        )
                                and (mcp_query_budget - mcp_query_count) > 0
                            ):
                                fact_c = _fact_need_continue_now()
                                if not user_fetch_complete:
                                    auto_note += (
                                        "\n【续拉】角色页已有但 user 未见短页；请继续同窗分页 user_info，勿进分析。"
                                    )
                                elif fact_c:
                                    bits = []
                                    for role in fact_c:
                                        view_r = ""
                                        for v, n in fetched_view_pages.items():
                                            if _view_category(v) == role:
                                                view_r = v
                                                break
                                        pages_r = sum(
                                            int(n or 0)
                                            for v, n in fetched_view_pages.items()
                                            if _view_category(v) == role
                                        )
                                        if view_r:
                                            bits.append(
                                                f'MCP: query_ads_view {{"view":"{view_r}",'
                                                f'"limit":{_EXPORT_PAGE_LIMIT}}}'
                                            )
                                    auto_note += (
                                        "\n【续拉·一次拉全】明细仍满页："
                                        + "、".join(fact_c)
                                        + "；请按已拉 view OFFSET 续翻后再写表。"
                                    )
                                    if bits:
                                        auto_note += "\n" + "\n".join(bits)
                            elif (
                                export_phase == "analyze"
                                and _roles_ready_for_analyze()
                            ):
                                auto_note += (
                                    "\n【短页齐套】补缺完成；建议停止 MCP query；"
                                    "请立即 SHELL 写中文表头 xlsx。"
                                )
                            elif (
                                export_phase in ("fetch", "discover")
                                and mcp_query_count >= mcp_query_budget
                            ):
                                _enter_export_analyze_or_finalize(
                                    f"query 达预算 {mcp_query_budget}，进入分析"
                                )
                                auto_note += (
                                    "\n【预算用尽】请立即用 SHELL 分析 task/ 并写当前目录 xlsx，再 FINAL；"
                                    "若明细仍满页须在 FINAL 标明「未标完整」。"
                                )
                            elif export_phase == "discover" and export_discover_queries >= 1:
                                _enter_export_plan("样例数据已落盘，请先 PLAN")
                                auto_note += (
                                    f"\n请先输出 PLAN:（含分页预算 N≤{export_budget_cap} 与 SHELL 分析步骤）。"
                                )
                    if mcp_results:
                        last = mcp_results[-1]
                        _append_progress(
                            progress_lines,
                            f"MCP {last['tool']}: {last.get('row_count', '?')} 行",
                        )
                        _upsert_progress_message(messages, progress_lines)
                    notice = _track_mcp(tool, failed=False, normalized=normalized)
                    tools_effective_this_turn = True
                    empty_llm_streak = 0
                    if not export_like and intent_time_window:
                        auto_note += _temporal_list_contract_annotation(
                            tool,
                            tool_result,
                            intent_time_window,
                        )
                    out = tool_result + auto_note + (notice or "")
                    await _push_step({
                        "type": "tool",
                        "action": action,
                        "title": f"调用工具: {mcp_label}.{tool}",
                        "status": "done",
                    })
                    await _publish_tool_ws(out, action, hidden=False)
                    return out
                else:
                    _fail_args = _parse_mcp_args(normalized)
                    _fail_view = str(
                        _fail_args.get("view")
                        or _fail_args.get("view_name")
                        or ""
                    ).strip()
                    enriched = _enrich_mcp_failure(
                        tool,
                        tool_result,
                        time_window=export_time_window if export_like else None,
                        view=_fail_view,
                        schema_hints=ads_schema_hints if export_like else None,
                        tool_schema_summary=(
                            _format_tool_schema_summary(
                                _find_mcp_tool_def(
                                    [
                                        t
                                        for mid in (mcp_ids or [])
                                        for t in (
                                            (_mcp_tools_cache.get(mid) or (0, []))[1]
                                        )
                                    ],
                                    tool,
                                )
                            )
                        ),
                    )
                    logger.warning(
                        "MCP tool failed tool=%s view=%s err=%s",
                        tool,
                        _fail_view or "-",
                        (enriched or "")[:500],
                    )
                    enriched += _track_mcp(
                        tool, failed=True, normalized=normalized, error_text=enriched,
                    )
                    await _push_step({
                        "type": "tool",
                        "action": action,
                        "title": f"MCP 调用失败 · {tool}",
                        "content": (enriched or "")[:800],
                        "status": "error",
                        "hidden": False,
                    })
                    await _publish_tool_ws(enriched, action, hidden=False)
                    return enriched
            if action == "shell":
                cmd = _short_text(_shell_command(normalized))
                await _push_step({
                    "type": "tool",
                    "action": "shell",
                    "title": f"Shell: {cmd}" if cmd else "Shell",
                    "status": "done",
                })
                await _publish_tool_ws(tool_result, action)
                # fetch/analyze/finalize：python 脚本写表后立即扫根目录门禁
                if export_like and export_phase in ("fetch", "analyze", "finalize") and sandbox:
                    wrote = _shell_looks_like_xlsx_write(normalized)
                    prep_only = _shell_looks_like_prep_only(normalized)
                    explore_only = _shell_looks_like_explore(normalized) and not wrote
                    if wrote or _shell_counts_as_progress(normalized):
                        tools_effective_this_turn = True
                        empty_llm_streak = 0
                    # Explore-only SHELL does not burn analyze write budget
                    if export_phase == "analyze" and explore_only and analyze_rounds_used > 0:
                        analyze_rounds_used = max(0, analyze_rounds_used - 1)
                    roots = _collect_analyze_deliverables()
                    fact_miss_shell = _missing_fact_roles() if export_like else []
                    facts_ok_shell = _facts_ok_for_delivery(
                        (
                            _missing_export_roles(
            sandbox.id,
            export_run_id,
            export_target_roles,
            abandoned_roles=export_abandoned_roles,
        )
                            if sandbox
                            else fact_miss_shell
                        ),
                    )
                    if roots and export_phase == "fetch" and facts_ok_shell:
                        _enter_export_analyze("SHELL 已写出合格交付文件")
                    if export_phase in ("analyze", "finalize") or roots:
                        # Only mark write_ok when gated deliverable exists — write intent
                        # alone must not skip grace and jump to 原始回退.
                        if roots and facts_ok_shell:
                            analyze_write_shell_ok = True
                        if roots and facts_ok_shell:
                            tool_result = (
                                (tool_result or "")
                                + "\n\n【分析检测】当前目录已有合法交付文件: "
                                + ", ".join(f"`{r}`" for r in roots)
                                + "。请立即 FINAL:。"
                            )
                        elif roots and not facts_ok_shell:
                            miss_shell_all = (
                                _missing_export_roles(
            sandbox.id,
            export_run_id,
            export_target_roles,
            abandoned_roles=export_abandoned_roles,
        )
                                if sandbox
                                else fact_miss_shell
                            )
                            block_shell = _blocking_roles_for_analyzed_delivery(
                                export_target_roles=export_target_roles,
                                missing_roles=miss_shell_all,
                            )
                            tool_result = (
                                (tool_result or "")
                                + "\n\n【禁止分析交付】当前目录虽有 xlsx，但仍缺 role："
                                + "、".join(block_shell)
                                + "。请先 MCP 拉齐后再 join 重写表，勿 FINAL 为分析交付"
                                "（缺 user 时禁止跨旧 run 拼表）。\n"
                                + _force_fact_fetch_message(
                                    [r for r in block_shell if r in ("pay", "cash", "bet")]
                                    or fact_miss_shell
                                )
                            )
                        elif prep_only and export_phase == "analyze":
                            analyze_explore_shells += 1
                            tool_result = (
                                (tool_result or "")
                                + "\n\n【分析纠偏】禁止只写/运行 `task/*.py` prep 脚本。"
                                "请把分析脚本写到 `/tmp/build_report.py` 再 `python3 /tmp/build_report.py`；"
                                "必须用 pandas `to_excel` 或 openpyxl `Workbook.save` 写出"
                                "**当前目录**中文表头 xlsx（非 task/），再 FINAL。"
                            )
                        elif wrote and export_phase == "analyze":
                            tail = _shell_output_tail(tool_result, 400)
                            channel_hint = ""
                            empty_hint = ""
                            fact_hint = ""
                            fact_miss_now = _missing_fact_roles()
                            if fact_miss_now:
                                fact_hint = (
                                    "\n另：task 仍缺声明明细 role："
                                    + "、".join(fact_miss_now)
                                    + "。禁止用宽表字段凑数；须先 MCP 拉仍缺声明 role 再 join 写表。"
                                )
                            # If a new xlsx exists but failed gate due to numeric channel / empty cols
                            for cand in _find_export_root_deliverables(
                                sandbox,
                                save_dir,
                                saved_paths,
                                run_id=export_run_id,
                                started_at=export_run_started_at,
                            ):
                                pp = download_path(sandbox.id, cand)
                                if not pp:
                                    continue
                                hdrs = read_deliverable_headers(pp)
                                if _channel_column_mostly_numeric(pp, hdrs):
                                    channel_hint = (
                                        "\n另：注册渠道仍为数字 ID，必须映射为渠道名称后再写表。"
                                    )
                                empty_bad = _critical_empty_column_failures(pp, hdrs)
                                # Do not burn empty-reject quota while fact pages are still missing
                                if empty_bad and not fact_miss_now and analyze_empty_reject_count < 2:
                                    analyze_empty_reject_count += 1
                                    empty_hint = (
                                        "\n另：关键列空值过高（"
                                        + "、".join(empty_bad)
                                        + "）。请按口径重算：流水倍数=总下注/总充值；"
                                        "退款=pay 成功退款填「有」；游戏=bet 聚合 max + join game；"
                                        "渠道=join channel。"
                                        + _dim_views_hint()
                                        + "再写当前目录 xlsx。"
                                    )
                                    break
                            tool_result = (
                                (tool_result or "")
                                + "\n\n【分析纠偏】已执行写表命令，但未检出当前目录合法中文表头 xlsx"
                                "（或缺明细/关键列空值过高）。"
                                "必须用 pandas `to_excel` 或 openpyxl `Workbook.save` 写到"
                                "**工作区当前目录**（例如 `新注册用户运营数据.xlsx`），"
                                "**禁止**写到 `task/` 子目录；表头须为用户完整中文列名"
                                "（含流水倍数等）。请根据下列输出修复后重试：\n"
                                + (tail or "(无输出)")
                                + channel_hint
                                + fact_hint
                                + empty_hint
                            )
                        elif export_phase == "analyze":
                            analyze_explore_shells += 1
                            roots_now = _collect_analyze_deliverables()
                            # Soft: if fetch still looks truncated with budget, nudge MCP first
                            if (
                                _still_full_with_budget()
                                and analyze_explore_shells >= 2
                                and not roots_now
                            ):
                                tool_result = (
                                    (tool_result or "")
                                    + "\n\n【取数未完】探查暂缓：仍有满页明细/用户页且预算充足，"
                                    "请先 MCP/引擎代拉 OFFSET 续翻至短页，再写表。"
                                )
                            elif analyze_explore_shells >= 3 or (
                                analyze_explore_shells >= 2 and roots_now
                            ):
                                soft_finish_requested = True
                                if roots_now:
                                    tool_result = (
                                        (tool_result or "")
                                        + "\n\n【分析强制】探查已够且当前目录已有合法交付: "
                                        + ", ".join(f"`{r}`" for r in roots_now[:4])
                                        + "。禁止再 ls/head；请立即 FINAL:（引擎将收束）。"
                                    )
                                else:
                                    tool_result = (
                                        (tool_result or "")
                                        + "\n\n【分析强制】已探查≥3次。禁止再 ls/head/枚举。"
                                        "请立即 join 写**当前目录**中文表头 xlsx，或让平台辅助写表后 FINAL。"
                                        "脚本写 `/tmp/build_report.py`，禁止往 task/ 写 .py。"
                                    )
                            elif analyze_explore_shells >= 2:
                                tool_result = (
                                    (tool_result or "")
                                    + "\n\n【分析强制】已探查≥2次。禁止再 ls/head/枚举。"
                                    "请立即按 meta role 读取 `task/<run_id>/page_*.json`，join 后 "
                                    "`to_excel` 写出**当前目录**中文表头 xlsx（非 task/）。"
                                    "脚本写 `/tmp/build_report.py`，禁止往 task/ 写 .py。"
                                )
                            else:
                                tool_result = (
                                    (tool_result or "")
                                    + "\n\n【分析纠偏】禁止只 ls/head/cat。"
                                    "请立即 SHELL 用 openpyxl/pandas 写**当前目录**中文表头 xlsx（非 task/）。"
                                )
                return tool_result
            if action == "file_write":
                files_written += 1
                tools_effective_this_turn = True
                empty_llm_streak = 0
                m = re.search(r"已写入\s+(\S+)", tool_result)
                if m:
                    saved_paths.append(m.group(1))
                    _append_progress(progress_lines, f"已写入 {m.group(1)}")
                    _upsert_progress_message(messages, progress_lines)
            if action not in _INTERNAL_TOOL_ACTIONS:
                title = {
                    "file_write": "写入文件",
                    "file_read": "读取文件",
                    "file_search_replace": "替换文件",
                    "skill_read_md": "读取 Skill",
                }.get(action, f"工具 · {action}")
                content = tool_result[:800]
                if action == "file_write":
                    m = re.search(r"已写入\s+(\S+)", tool_result)
                    if m:
                        content = f"已写入 {m.group(1).split('/')[-1]}"
                await _push_step({
                    "type": "tool",
                    "action": action,
                    "title": title,
                    "content": content,
                    "status": "done",
                })
                await _publish_tool_ws(tool_result, action)
            return tool_result

        steps = extract_tool_steps(reply)
        # Soft: rebut invented 30-row / 8-tool / must-dbt limits (no tools → re-prompt)
        if (
            export_like
            and export_phase in ("fetch", "analyze", "plan", "discover")
            and _reply_looks_like_underfetch_myth(reply)
            and not steps
        ):
            messages.append({"role": "assistant", "content": reply})
            messages.append({"role": "user", "content": _soft_rebut_underfetch_myth()})
            continue
        if steps:
            step_results = []
            hit_final = False
            for step in steps:
                if step.is_final or step.action == "done":
                    final = step.reply.replace("FINAL:", "", 1).strip()
                    hit_final = True
                    had_explicit_final = True
                    # Anti-hallucination: no concrete overview numbers without task landing
                    if export_like and sandbox:
                        has_land = bool(task_has_exportable_data(sandbox.id)) or (
                            sum(int(n or 0) for n in fetched_view_pages.values()) > 0
                        )
                        if not has_land and text_has_export_stat_claims(final):
                            final = sanitize_final_without_landing(
                                final,
                                has_task_data=False,
                                plan_card=format_column_plan_card(export_column_plan),
                            )
                    break
                if step.action not in allowed:
                    continue
                if not export_like and generic_phase == "act":
                    generic_tool_count += 1
                tool_result = await _run_tool(step.action, step.reply)
                if tool_result:
                    # Clip before batching so we never hold multi-MB strings in step_results
                    pre = _clip_tool_result(
                        tool_result,
                        _SHELL_OBS_MAX_CHARS if step.action == "shell" else _TOOL_RESULT_MAX_CHARS,
                    )
                    step_results.append(f"[{step.action}] {pre}")
            if hit_final:
                final, _excuse_cont = await _handle_mcp_excuse_on_final(final or "")
                if _excuse_cont:
                    continue
                if not export_like and generic_todos:
                    mark_act_todos_from_evidence(
                        generic_todos,
                        final_text=final or "",
                        saved_paths=saved_paths,
                    )
                    unfinished = unfinished_act_todos(generic_todos)
                    if unfinished and generic_tool_count < generic_tool_budget:
                        had_explicit_final = False
                        final = ""
                        messages.append({"role": "assistant", "content": reply})
                        messages.append({
                            "role": "user",
                            "content": (
                                "【完成标准未齐】尚缺："
                                + "；".join(unfinished[:6])
                                + "。请继续用工具补齐，或在 FINAL 中明确说明无法完成的原因后再收工。"
                            ),
                        })
                        hit_final = False
                        continue
                    for t in generic_todos:
                        if t.get("phase") == "finalize":
                            t["done"] = True
                break
            if step_results:
                mcp_fail_only = _step_results_are_mcp_failures_only(step_results)
                # Explore-only SHELL must not reset no_progress (idle soft-converge).
                # Non-export tools keep legacy reset-on-any-success behavior.
                if not mcp_fail_only and (tools_effective_this_turn or not export_like):
                    no_progress = 0
                    empty_llm_streak = 0
                clipped = []
                for r in step_results:
                    m = re.match(r"^\[([^\]]+)\]\s*(.*)$", r or "", re.DOTALL)
                    if m:
                        act, body = m.group(1), m.group(2)
                        obs = _prepare_tool_observation(
                            body, action=act, sandbox=sandbox, session_id=session_id,
                        )
                        clipped.append(f"[{act}] {obs}")
                        for p in _extract_named_paths(obs):
                            if "checkpoint" in p or p.startswith("task/"):
                                _append_progress(progress_lines, f"checkpoint/过程 {p}")
                    else:
                        obs = _prepare_tool_observation(
                            r, action="tool", sandbox=sandbox, session_id=session_id,
                        )
                        clipped.append(obs)
                        for p in _extract_named_paths(obs):
                            if "checkpoint" in p or p.startswith("task/"):
                                _append_progress(progress_lines, f"checkpoint/过程 {p}")
                _upsert_progress_message(messages, progress_lines)
                messages.append({"role": "assistant", "content": reply})
                messages.append({"role": "user", "content": "工具结果:\n" + "\n".join(clipped)})
                if mcp_fail_only and _bump_no_progress(reply):
                    break
                if soft_finish_requested and export_like and export_phase in (
                    "analyze",
                    "finalize",
                ):
                    soft_finish_requested = False
                    roots = _collect_analyze_deliverables()
                    if roots:
                        _enter_export_finalize("探查收敛：已有合法交付")
                        if _try_export_hard_finish("探查过多，收束已有交付"):
                            break
                    elif _try_platform_write_finish("探查过多，平台写表"):
                        break
                    messages.append({"role": "assistant", "content": reply})
                    messages.append({
                        "role": "user",
                        "content": (
                            "探查类 SHELL 已过多，不再计入有效进展。"
                            "请立刻 SHELL 写出合法根目录 xlsx（或 MCP 落盘后写表），"
                            "不要继续 pandas/info/describe 探查。"
                        ),
                    })
                    continue
                if export_like and export_phase == "analyze":
                    roots = _collect_analyze_deliverables()
                    if roots:
                        _enter_export_finalize("分析产物已就绪")
                        if _try_export_hard_finish("已使用 SHELL 分析结果落盘"):
                            break
                elif in_export_finalize and _try_export_hard_finish("已达收尾条件，已落盘交付文件"):
                    break
                if export_like and export_phase == "fetch":
                    _fetch_idle_tick(had_mcp_data=mcp_query_count > mcp_q_at_turn_start)
                continue
            if _looks_like_tool_call(reply):
                messages.append({"role": "assistant", "content": reply})
                messages.append({"role": "user", "content": "工具调用未能执行，请检查沙箱是否可用，或改用 WRITE:/FINAL: 格式重试。"})
                if _bump_no_progress(reply):
                    break
                continue
            empty_llm_streak = 0
            if i == max_iters - 1:
                final = _clean_display_text(reply) or "任务未完成，请重试。"
                break
            messages.append({"role": "assistant", "content": reply})
            messages.append({
                "role": "user",
                "content": "未执行到允许的工具。请只输出一行工具指令，例如：READ: path/to/file 或 FINAL: 结论",
            })
            if _bump_no_progress(reply):
                break
            continue

        # Empty LLM idle: early FINAL only when analyze/finalize + valid deliverable
        if (
            export_like
            and export_phase in ("analyze", "finalize")
            and _reply_is_empty_idle(reply)
        ):
            empty_llm_streak += 1
            roots = _collect_analyze_deliverables()
            if _export_idle_early_finish_allowed(
                export_like=True,
                export_phase=export_phase,
                empty_llm_streak=empty_llm_streak,
                has_root_deliverable=bool(roots),
            ):
                _enter_export_finalize("空转收尾：已有合法交付")
                if _try_export_hard_finish("空 LLM 收束已有交付"):
                    break
            elif empty_llm_streak >= 2 and not roots:
                # May create a deliverable; never fake FINAL if write fails
                if _try_platform_write_finish("空转后平台 join 写表"):
                    break
            # No deliverable → never fake FINAL; coach only
            messages.append({"role": "assistant", "content": reply or "(空回复)"})
            messages.append({
                "role": "user",
                "content": (
                    "【空转提示】本轮无有效工具/FINAL。"
                    + (
                        "当前目录已有合法交付，请立即输出 FINAL:。"
                        if roots
                        else "尚无合法根目录 xlsx，请 SHELL 写表或 MCP 拉数，勿空回复。"
                    )
                ),
            })
            if not roots and _bump_no_progress(reply):
                break
            continue

        if reply.strip().startswith("FINAL:"):
            final = reply.replace("FINAL:", "", 1).strip()
            final, _excuse_cont = await _handle_mcp_excuse_on_final(final or "")
            if _excuse_cont:
                continue
            if not export_like and generic_todos:
                mark_act_todos_from_evidence(
                    generic_todos,
                    final_text=final or "",
                    saved_paths=saved_paths,
                )
                unfinished = unfinished_act_todos(generic_todos)
                if unfinished and generic_tool_count < generic_tool_budget:
                    messages.append({"role": "assistant", "content": reply})
                    messages.append({
                        "role": "user",
                        "content": (
                            "【完成标准未齐】尚缺："
                            + "；".join(unfinished[:6])
                            + "。请继续用工具补齐，或在 FINAL 中明确说明无法完成的原因后再收工。"
                        ),
                    })
                    continue
                for t in generic_todos:
                    if t.get("phase") == "finalize":
                        t["done"] = True
            had_explicit_final = True
            break

        action, normalized = detect_action_from_reply(reply, allowed)
        if action == "done":
            final = normalized.replace("FINAL:", "", 1).strip()
            final, _excuse_cont = await _handle_mcp_excuse_on_final(final or "")
            if _excuse_cont:
                continue
            if not export_like and generic_todos:
                mark_act_todos_from_evidence(
                    generic_todos,
                    final_text=final or "",
                    saved_paths=saved_paths,
                )
                unfinished = unfinished_act_todos(generic_todos)
                if unfinished and generic_tool_count < generic_tool_budget:
                    messages.append({"role": "assistant", "content": reply})
                    messages.append({
                        "role": "user",
                        "content": (
                            "【完成标准未齐】尚缺："
                            + "；".join(unfinished[:6])
                            + "。请继续用工具补齐后再 FINAL。"
                        ),
                    })
                    continue
            had_explicit_final = True
            break
        if action:
            if not export_like and generic_phase == "act":
                generic_tool_count += 1
            tool_result = await _run_tool(action, normalized)
            if tool_result:
                mcp_preflight = action in ("mcp_tool_call", "httpmcp_call") and _is_mcp_preflight_feedback(
                    tool_result
                )
                mcp_fail = (
                    action in ("mcp_tool_call", "httpmcp_call")
                    and _is_mcp_tool_failure(tool_result)
                    and not mcp_preflight
                )
                if not mcp_fail and (tools_effective_this_turn or not export_like):
                    no_progress = 0
                    empty_llm_streak = 0
                elif (
                    not mcp_fail
                    and action == "shell"
                    and _shell_counts_as_progress(normalized)
                ):
                    tools_effective_this_turn = True
                    no_progress = 0
                    empty_llm_streak = 0
                obs = _prepare_tool_observation(
                    tool_result, action=action or "tool", sandbox=sandbox, session_id=session_id,
                )
                for p in _extract_named_paths(obs):
                    if "checkpoint" in p or p.startswith("task/"):
                        _append_progress(progress_lines, f"checkpoint/过程 {p}")
                _upsert_progress_message(messages, progress_lines)
                messages.append({"role": "assistant", "content": reply})
                messages.append({"role": "user", "content": f"工具结果:\n{obs}"})
                if mcp_fail and _bump_no_progress(reply):
                    break
                if soft_finish_requested and export_like and export_phase in (
                    "analyze",
                    "finalize",
                ):
                    soft_finish_requested = False
                    roots = _collect_analyze_deliverables()
                    if roots:
                        _enter_export_finalize("探查收敛：已有合法交付")
                        if _try_export_hard_finish("探查过多，收束已有交付"):
                            break
                    elif _try_platform_write_finish("探查过多，平台写表"):
                        break
                    messages.append({"role": "assistant", "content": reply})
                    messages.append({
                        "role": "user",
                        "content": (
                            "探查类 SHELL 已过多，不再计入有效进展。"
                            "请立刻 SHELL 写出合法根目录 xlsx（或 MCP 落盘后写表），"
                            "不要继续 pandas/info/describe 探查。"
                        ),
                    })
                    continue
                if export_like and export_phase == "analyze":
                    roots = _collect_analyze_deliverables()
                    if roots:
                        _enter_export_finalize("分析产物已就绪")
                        if _try_export_hard_finish("已使用 SHELL 分析结果落盘"):
                            break
                elif in_export_finalize and _try_export_hard_finish("已达收尾条件，已落盘交付文件"):
                    break
                if export_like and export_phase == "fetch":
                    _fetch_idle_tick(had_mcp_data=mcp_query_count > mcp_q_at_turn_start)
                continue
            messages.append({"role": "assistant", "content": reply})
            messages.append({"role": "user", "content": "工具执行失败，请检查沙箱状态后重试。"})
            if _bump_no_progress(reply):
                break
            continue

        if _looks_like_tool_call(reply):
            messages.append({"role": "assistant", "content": reply})
            messages.append({"role": "user", "content": "检测到工具调用但未能解析，请只输出一种 MCP:/SHELL:/WRITE:/FINAL: 指令。"})
            if _bump_no_progress(reply):
                break
            continue

        cleaned = _clean_display_text(reply)
        # 有实质答复且不是「我准备去读文件」类空转话术时，视为最终回答。
        # 导出/报表类任务必须显式 FINAL:，避免散文提前收工。
        # 未使用工具的 generic 闲聊/问答：短回复也可收工（门槛 1）。
        _prose_min = (
            1
            if (not export_like and not ran_any_tool and files_written <= 0)
            else 50
        )
        if cleaned and len(cleaned) >= _prose_min and not _looks_like_tool_call(reply) and not _narrates_tool_without_call(reply):
            if export_like:
                if in_export_analyze:
                    messages.append({"role": "assistant", "content": reply})
                    messages.append({"role": "user", "content": _analyze_hint_with_pages()})
                    if _bump_no_progress(reply):
                        break
                    continue
                if in_export_finalize and _try_export_hard_finish("已根据 task/ 数据自动落盘"):
                    break
                # fetch 未完成：禁止散文触发原始回退，继续拉缺 role / 续翻
                if export_phase == "fetch":
                    messages.append({"role": "assistant", "content": reply})
                    messages.append({"role": "user", "content": _fetch_continue_nudge()})
                    if _bump_fetch_or_analyze(reply):
                        break
                    continue
                if sandbox and (
                    _find_export_root_deliverables(
                        sandbox,
                        save_dir,
                        saved_paths,
                        run_id=export_run_id,
                        started_at=export_run_started_at,
                    )
                    or task_has_exportable_data(sandbox.id)
                ) and _try_export_hard_finish():
                    break
                messages.append({"role": "assistant", "content": reply})
                messages.append({
                    "role": "user",
                    "content": (
                        "导出任务收工请单独输出一行 FINAL: <结论>。"
                        "若需分析列，先 SHELL 写当前目录 xlsx；建议控制分页，优先交付有效文件。"
                    ),
                })
                if _bump_no_progress(reply):
                    break
                continue
            # Normal tasks: after any tool use, require explicit FINAL
            if ran_any_tool or files_written > 0:
                messages.append({"role": "assistant", "content": reply})
                messages.append({
                    "role": "user",
                    "content": "本轮已执行过工具。请单独输出一行 FINAL: <结论>，不要仅用散文收工。",
                })
                if _bump_no_progress(reply):
                    break
                continue
            if turn_intent.needs_tools:
                messages.append({"role": "assistant", "content": reply})
                messages.append({
                    "role": "user",
                    "content": (
                        "本轮意图合同要求执行工具，但尚无任何工具结果或交付物。"
                        "请立即调用已加载的工具继续执行，不要只描述接下来准备做什么。"
                    ),
                })
                if _bump_no_progress(reply):
                    break
                continue
            final = cleaned
            break

        messages.append({"role": "assistant", "content": reply})
        if in_export_analyze:
            messages.append({"role": "user", "content": _analyze_hint_with_pages()})
        elif export_like and export_phase == "fetch":
            messages.append({"role": "user", "content": _fetch_continue_nudge()})
            if _bump_fetch_or_analyze(reply):
                break
            continue
        elif _narrates_tool_without_call(reply):
            messages.append({
                "role": "user",
                "content": (
                    "不要只用自然语言描述计划。请立刻输出可执行指令（单独一行），例如：\n"
                    "READ: 相对路径\n或\nFINAL: 你的结论"
                ),
            })
        else:
            if export_like and in_export_finalize:
                messages.append({
                    "role": "user",
                    "content": "请立即输出 FINAL: <结论>；禁止再 MCP 查询。平台将合并 task/ 落盘。",
                })
            else:
                messages.append({"role": "user", "content": "请继续完成任务：使用 WRITE: 保存完整内容，或 FINAL: 给出最终回答。"})
        if _bump_no_progress(reply):
            break
        continue

    if not had_explicit_final:
        # Keep clarify / LLM error / no_progress abort — never wipe with exhaust
        if (final or "").strip() and not _looks_like_tool_call((final or "").strip()):
            pass
        elif export_like and sandbox and task_has_exportable_data(sandbox.id):
            # Iters exhaust / hard stop: force platform write even if roles incomplete
            if _try_platform_write_finish(
                "轮次用尽，强制平台写表",
                prefer_fallback=True,
            ) or _try_export_hard_finish(
                "轮次用尽，强制落盘交付文件",
                prefer_fallback=True,
            ):
                pass
            elif export_like or saved_paths:
                exhaust_state: dict | None = None
                if export_run_id:
                    has_task_ex = True
                    _persist_run_state(
                        completeness_force="iters_exhausted",
                    )
                    try:
                        from app.services.skill_lesson import load_run_state

                        exhaust_state = load_run_state(sandbox.id, export_run_id)
                    except Exception:
                        exhaust_state = None
                final, saved_paths = _build_exhaust_fallback(
                    sandbox=sandbox,
                    save_dir=save_dir,
                    saved_paths=saved_paths,
                    progress_lines=progress_lines,
                    last_reply=last_reply,
                    final=final,
                    mcp_results=mcp_results,
                    export_like=export_like,
                    user_message=task_brief,
                    filter_condition=export_filter_condition or (
                        str(export_time_window.get("label") or "") if export_time_window else ""
                    ),
                    run_state=exhaust_state,
                    max_iters=max_iters,
                    shell_enabled=shell_enabled,
                    session_id=session_id,
                )
            else:
                cleaned = _clean_display_text(last_reply or final)
                if cleaned and not _looks_like_tool_call(cleaned):
                    final = cleaned
                elif not final:
                    final = "任务未完成，请重试。"
        elif export_like and sandbox and _try_export_hard_finish(
            "轮次用尽，已落盘交付文件", prefer_fallback=True,
        ):
            pass
        elif export_like or saved_paths:
            exhaust_state = None
            if export_like and sandbox and export_run_id:
                has_task_ex = bool(task_has_exportable_data(sandbox.id)) or (
                    sum(int(n or 0) for n in fetched_view_pages.values()) > 0
                )
                _persist_run_state(
                    completeness_force=(
                        "iters_exhausted" if has_task_ex else "no_data"
                    ),
                )
                try:
                    from app.services.skill_lesson import load_run_state

                    exhaust_state = load_run_state(sandbox.id, export_run_id)
                except Exception:
                    exhaust_state = None
            final, saved_paths = _build_exhaust_fallback(
                sandbox=sandbox,
                save_dir=save_dir,
                saved_paths=saved_paths,
                progress_lines=progress_lines,
                last_reply=last_reply,
                final=final,
                mcp_results=mcp_results,
                export_like=export_like,
                user_message=task_brief,
                filter_condition=export_filter_condition or (
                    str(export_time_window.get("label") or "") if export_time_window else ""
                ),
                run_state=exhaust_state,
                max_iters=max_iters,
                shell_enabled=shell_enabled,
            )
        else:
            cleaned = _clean_display_text(last_reply or final)
            if cleaned and not _looks_like_tool_call(cleaned):
                final = cleaned
            elif saved_paths:
                final = "任务已完成，文件已保存至：\n" + "\n".join(f"- `{p}`" for p in saved_paths)
            elif not final:
                final, _ = _build_exhaust_fallback(
                    sandbox=sandbox,
                    save_dir=save_dir,
                    saved_paths=saved_paths,
                    progress_lines=progress_lines,
                    last_reply=last_reply,
                    final=final,
                    mcp_results=mcp_results,
                    export_like=False,
                    user_message=task_brief,
                    max_iters=max_iters,
                    shell_enabled=shell_enabled,
                    session_id=session_id,
                )
            else:
                final = "已执行工具操作，但未生成最终说明。请查看左侧工作目录或重试。"
    elif not final or _looks_like_tool_call(final):
        cleaned = _clean_display_text(last_reply or final)
        if cleaned and not _looks_like_tool_call(cleaned):
            final = cleaned
        elif saved_paths:
            final = "任务已完成，文件已保存至：\n" + "\n".join(
                f"- `{p}`" for p in saved_paths if not p.startswith("task/")
            ) or "任务已完成，请查看工作目录。"
        else:
            final = cleaned or "任务已完成。"

    platform_assisted_end = False
    engine_executed_end = bool(engine_script_executed)
    if sandbox and export_like:
        # Prefer gated LLM SHELL xlsx; hybrid: engine script → platform join → raw
        made = ""
        mode = "fallback"
        gated = _collect_analyze_deliverables()
        if gated:
            made = gated[0]
            mode = "analyzed"
            export_fallback = False
        if not made:
            for pth in saved_paths:
                if pth and _is_data_export_path(pth) and not pth.startswith("task/"):
                    ok = False
                    try:
                        dp0 = download_path(sandbox.id, pth)
                        ok, _hdrs, _miss = _todos_satisfied(
                            read_deliverable_headers(dp0 or Path()),
                            export_todos if export_todos else _parse_export_todos(
                                export_source_brief or task_brief or user_message
                            ),
                        )
                    except Exception:
                        ok = False
                    dp = download_path(sandbox.id, pth)
                    if dp and is_valid_deliverable_file(dp) and ok:
                        made = pth
                        mode = "analyzed"
                        export_fallback = False
                        break
        if not made:
            missing_roles_pre = _missing_export_roles(
            sandbox.id,
            export_run_id,
            export_target_roles,
            abandoned_roles=export_abandoned_roles,
        )
            if not missing_roles_pre and _try_engine_run_build_script(force=True):
                gated2 = _collect_analyze_deliverables()
                if gated2:
                    made = gated2[0]
                    mode = "analyzed"
                    export_fallback = False
                    engine_executed_end = True
                    engine_script_executed = True
            # Platform join only as last-resort assist (honest label), not silent success
            wants_platform_join = bool(
                export_column_plan
                and not analyze_write_shell_ok
                and not made
            )
            # Allow incomplete roles at end-of-run (iters/budget exhaust already forced above)
            if wants_platform_join:
                col_headers = (
                    list(export_deliverable_headers)
                    if export_deliverable_headers
                    else _export_analyze_column_texts(export_todos)
                )
                preferred_join = (
                    Path(export_fill_target_rel).name
                    if export_fill_target_rel
                    else f"用户分析_{export_run_id}.xlsx"
                )
                analyzed = ""
                if export_fill_target_rel and export_fill_columns:
                    analyzed = merge_focus_into_prior_deliverable(
                        sandbox_id=sandbox.id,
                        run_id=export_run_id,
                        prior_rel=export_fill_target_rel,
                        focus_columns=export_fill_columns,
                        column_plan=export_column_plan or None,
                        time_window=export_time_window,
                        title=str(
                            (export_time_window or {}).get("label") or "导出数据"
                        ),
                    ) or ""
                if not analyzed:
                    analyzed = write_export_deliverable(
                        sandbox.id,
                        export_run_id,
                        time_window=export_time_window,
                        column_headers=col_headers or None,
                        preferred_name=preferred_join,
                        column_plan=export_column_plan or None,
                        title=str(
                            (export_time_window or {}).get("label") or "导出数据"
                        ),
                    ) or ""
                if not analyzed:
                    analyzed = materialize_analyzed_export(
                        sandbox.id,
                        export_run_id,
                        time_window=export_time_window,
                        column_headers=col_headers or None,
                        preferred_name=preferred_join,
                        target_dir=save_dir,
                        column_plan=export_column_plan or None,
                    ) or ""
                if analyzed:
                    dp = download_path(sandbox.id, analyzed)
                    if dp and is_valid_deliverable_file(dp):
                        made = analyzed
                        mode = "analyzed"
                        platform_assisted_end = True
                        export_fallback = bool(missing_roles_pre)
                        headers_an = read_deliverable_headers(dp) or []
                        _mark_column_todos_from_headers(
                            export_todos if export_todos else [],
                            headers_an,
                        )
            if not made:
                preferred = f"export_{export_run_id}.xlsx"
                made = materialize_export_deliverable(
                    sandbox.id,
                    preferred_name=preferred,
                    target_dir=save_dir,
                    ignore_existing=True,
                )
                mode = "fallback"
                export_fallback = True
                platform_assisted_end = False
                engine_executed_end = False
        if made:
            if made not in saved_paths:
                saved_paths.append(made)
            # Strip model sample tables only; platform appendix is rebuilt below
            final = _strip_gfm_tables_preserving_appendix(final or "")
            p = download_path(sandbox.id, made)
            file_size = None
            row_hint = _mcp_total_row_hint(mcp_results)
            if p and p.is_file():
                try:
                    file_size = p.stat().st_size
                except OSError:
                    file_size = None
                file_rows = count_data_rows(p)
                if file_rows is not None:
                    row_hint = file_rows
            name = Path(made).name
            needs_rewrite = (
                not had_explicit_final
                or (name not in (final or "") and made not in (final or ""))
                or "未能在当前目录产出" in (final or "")
                or "未能产出最终 Excel/CSV" in (final or "")
                or (mode == "fallback" and "原始回退" not in (final or ""))
                or (platform_assisted_end and "平台辅助" not in (final or ""))
                or (engine_executed_end and "引擎代执行" not in (final or ""))
                or (
                    mode == "analyzed"
                    and (
                        "### 导出概况" not in (final or "")
                        or "### 字段说明" not in (final or "")
                    )
                )
                or (mode == "fallback" and "### 完整性/缺口说明" not in (final or ""))
            )
            if needs_rewrite or file_size is not None:
                missing = export_missing_cols
                if mode == "fallback" and not missing:
                    missing = [
                        str(t.get("text") or "")
                        for t in (export_todos or [])
                        if t.get("phase") == "analyze" and not t.get("done")
                    ]
                todo_done = sum(1 for t in (export_todos or []) if t.get("done"))
                analysis_md = ""
                missing_roles_now = _missing_export_roles(
                    sandbox.id if sandbox else None,
                    export_run_id,
                    export_target_roles,
                )
                truncated_roles = sorted({
                    _view_category(v)
                    for v, n in fetched_view_pages.items()
                    if n >= _max_pages_for_view(v, pay_cohort=_pay_cohort, fact_page_cap=export_fact_page_cap, user_page_cap=export_user_page_cap)
                    and _VIEW_IS_FACT(v)
                })
                if mode == "analyzed" and p and p.is_file():
                    headers = read_deliverable_headers(p)
                    analysis_md = _build_export_analysis_appendix(
                        path=p,
                        headers=headers,
                        todos=export_todos if export_todos else _parse_export_todos(
                            export_source_brief or task_brief or user_message
                        ),
                        time_window=export_time_window,
                        row_hint=row_hint,
                        detail_truncated=bool(truncated_roles),
                        truncated_roles=truncated_roles,
                        missing_roles=missing_roles_now,
                        user_fetch_complete=user_fetch_complete,
                        dim_budget_exhausted=export_dim_budget_exhausted,
                        cohort_uid_estimate=export_cohort_uid_estimate,
                        column_plan=export_column_plan or None,
                        file_size=file_size,
                    )
                    # Assist/engine note is added once in _format_export_final — do not prepend here
                elif mode == "fallback":
                    analysis_md = _build_export_fallback_appendix(
                        missing=missing,
                        missing_roles=missing_roles_now,
                        row_hint=row_hint,
                        time_window=export_time_window,
                        analyze_incomplete=not bool(missing_roles_now),
                        shell_enabled=shell_enabled,
                    )
                covered_roles_now = sorted(
                    _covered_export_roles(sandbox.id, export_run_id)
                ) if export_run_id else []
                analysis_md = _append_export_skill_lesson_section(
                    sandbox,
                    export_run_id,
                    user_message=task_brief,
                    mode=mode,
                    deliverable=made,
                    covered_roles=covered_roles_now,
                    missing_roles=missing_roles_now,
                    fetched_view_pages=fetched_view_pages,
                    export_todos=export_todos if export_todos else _parse_export_todos(task_brief),
                    time_window=export_time_window,
                    analysis_md=analysis_md,
                    mcp_failures=_summarize_mcp_failures(
                        mcp_class_fails, mcp_class_samples, mcp_class_tools,
                    ),
                    fact_truncated_roles=truncated_roles if mode == "analyzed" else None,
                    deliverable_rows=row_hint,
                    cohort_uid_estimate=export_cohort_uid_estimate,
                    skill_ids=skill_ids,
                    shell_enabled=shell_enabled,
                    session_id=session_id,
                )
                _filt = export_filter_condition or (
                    str(export_time_window.get("label") or "") if export_time_window else ""
                )
                final = _format_export_final(
                    user_message=task_brief,
                    file_rel=made,
                    file_size=file_size,
                    row_hint=row_hint,
                    preview_md="",
                    mode=mode,
                    todo_done=todo_done,
                    todo_total=len(export_todos or []),
                    missing=missing if mode == "fallback" else None,
                    analysis_md=analysis_md,
                    filter_condition=_filt,
                    platform_assisted=platform_assisted_end,
                    engine_executed=engine_executed_end,
                    shell_enabled=shell_enabled,
                )
        else:
            final = _strip_gfm_tables_preserving_appendix(final or "")
            if not _current_dir_deliverables(saved_paths, sandbox):
                has_task_end = bool(task_has_exportable_data(sandbox.id)) or (
                    sum(int(n or 0) for n in fetched_view_pages.values()) > 0
                )
                end_state: dict | None = None
                if export_run_id:
                    _persist_run_state(
                        completeness_force=(
                            "iters_exhausted" if has_task_end else "no_data"
                        ),
                    )
                    try:
                        from app.services.skill_lesson import load_run_state

                        end_state = load_run_state(sandbox.id, export_run_id)
                    except Exception:
                        end_state = None
                if not isinstance(end_state, dict):
                    end_state = {
                        "completeness": "iters_exhausted" if has_task_end else "no_data",
                        "has_task_data": has_task_end,
                        "digest": (
                            "本轮轮次用尽时已有 task/ 过程页，但未写出当前目录交付表。"
                            if has_task_end
                            else "本轮未落盘任何可导出过程页，可能筛选无结果、权限不足或时间窗不对。"
                        ),
                        "next_actions": [],
                    }
                end_comp = str(end_state.get("completeness") or (
                    "iters_exhausted" if has_task_end else "no_data"
                ))
                end_digest = str(end_state.get("digest") or "").strip()
                end_actions = (
                    end_state.get("next_actions")
                    if isinstance(end_state.get("next_actions"), list)
                    else []
                )
                if not end_digest:
                    end_digest = _build_run_state_digest(
                        completeness=end_comp,
                        missing_roles=list(end_state.get("missing_roles") or []),
                        fact_truncated_roles=list(
                            end_state.get("fact_truncated_roles") or []
                        ),
                        need_continue_roles=list(
                            end_state.get("need_continue_roles") or []
                        ),
                        has_task_data=has_task_end,
                    )
                if not end_actions:
                    end_actions = _build_run_state_next_actions(
                        completeness=end_comp,
                        need_continue_roles=list(
                            end_state.get("need_continue_roles") or []
                        ),
                        missing_roles=list(end_state.get("missing_roles") or []),
                        fetched_view_pages=end_state.get("fetched_view_pages")
                        if isinstance(end_state.get("fetched_view_pages"), dict)
                        else fetched_view_pages,
                        time_window=end_state.get("time_window")
                        if isinstance(end_state.get("time_window"), dict)
                        else export_time_window,
                        has_task_data=has_task_end,
                    )
                final = _format_smart_empty_export_final(
                    completeness=end_comp,
                    digest=end_digest,
                    next_actions=end_actions,
                )
                # Still ban fabricated numbers if somehow present
                final = sanitize_final_without_landing(
                    final,
                    has_task_data=has_task_end,
                    plan_card=format_column_plan_card(export_column_plan),
                )

    if files_written == 0 and not export_like and _wants_workplace_save(user_message) and "file_write" in allowed:
        content = _clean_display_text(last_reply or final)
        if _is_saveable_content(content, user_message):
            rel = _autosave_path(user_message, save_dir)
            save_result = _write_workplace(sandbox, rel, content)
            if not (save_result.startswith("禁止") or save_result.startswith("非法")):
                files_written += 1
                saved_paths.append(rel)
                await _push_step({
                    "type": "tool",
                    "action": "file_write",
                    "title": "自动保存到工作目录",
                    "content": save_result,
                    "status": "done",
                    "path": rel,
                })
                await _publish_tool_ws(save_result, "file_write")
                final = f"{final}\n\n（已自动保存至 {rel}）" if final else f"内容已保存至工作目录 `{rel}`"

    if sandbox and not _user_wants_task_dir(user_message):
        saved_paths[:] = cleanup_workplace_temp_dirs(sandbox.id, saved_paths, target_dir=save_dir)
        if not save_dir:
            final = _normalize_final_paths(final, saved_paths)
        final = _ensure_download_paths(final, saved_paths, sandbox)
        other_paths = [
            p for p in saved_paths
            if not _is_data_export_path(p) and not p.startswith("task/")
        ]
        if other_paths and "已保存" not in final and "保存至" not in final and "文件说明" not in final:
            final = (final + "\n\n" if final else "") + "文件已保存至：\n" + "\n".join(f"- `{p}`" for p in other_paths[:5])
    elif sandbox:
        cleanup_workplace_temp_dirs(sandbox.id, saved_paths, target_dir=save_dir)
        final = _ensure_download_paths(final, saved_paths, sandbox)

    final, saved_paths = _reconcile_final_artifacts(
        final,
        saved_paths,
        sandbox,
        export_like=export_like,
    )
    final = _ensure_download_paths(final, saved_paths, sandbox)

    file_content = _load_saved_content(sandbox, saved_paths)
    if file_content and not export_like:
        save_note = f"（已保存至 `{saved_paths[-1]}`）" if saved_paths else ""
        if _looks_like_tool_call(final) or len(_clean_display_text(final)) < 150:
            final = file_content
            if save_note and save_note not in final:
                final = f"{final}\n\n{save_note}"
            final = _ensure_download_paths(final, saved_paths, sandbox)

    visible_steps = _ensure_visible_run_steps(
        _sanitize_steps(run_steps),
        export_like=export_like,
        has_mcp=bool(mcp_ids),
    )
    final = _merge_structured_presentation(
        final,
        structured_reply_candidate,
        result_shape=turn_intent.result_shape,
    )
    if mcp_results or (export_like and fetched_view_pages):
        final = _append_mcp_markdown(
            final,
            mcp_results,
            export_like=export_like,
            fetched_view_pages=fetched_view_pages if export_like else None,
        )
    if (
        mcp_ids
        and mcp_excuse_claim_hit
        and not ran_any_tool
        and mcp_excuse_soft_rejects >= 2
        and "工具软提示" not in (final or "")
    ):
        from app.services.intent_router import append_unavailable_tools_soft_nudge
        final = append_unavailable_tools_soft_nudge(final or "")
    if export_like:
        final = _strip_gfm_tables_preserving_appendix(final or "")
        # Self-heal empty 报表统计 / 输出列说明 after strip
        if _export_appendix_incomplete(final) and sandbox:
            p_fix = None
            for cand in (saved_paths or []):
                if cand and _is_data_export_path(cand) and not str(cand).startswith("task/"):
                    p_fix = download_path(sandbox.id, cand)
                    if p_fix and p_fix.is_file():
                        break
            if p_fix and p_fix.is_file():
                headers_fix = read_deliverable_headers(p_fix)
                todos_fix = export_todos if export_todos else _parse_export_todos(user_message)
                missing_roles_fix = _missing_export_roles(
            sandbox.id,
            export_run_id,
            export_target_roles,
            abandoned_roles=export_abandoned_roles,
        ) if export_run_id else []
                truncated_fix = sorted({
                    _view_category(v)
                    for v, n in (fetched_view_pages or {}).items()
                    if n >= _max_pages_for_view(v, pay_cohort=_pay_cohort, fact_page_cap=export_fact_page_cap, user_page_cap=export_user_page_cap)
                    and _VIEW_IS_FACT(v)
                })
                mode_fix = "fallback" if export_fallback else "analyzed"
                if mode_fix == "analyzed":
                    analysis_fix = _build_export_analysis_appendix(
                        path=p_fix,
                        headers=headers_fix,
                        todos=todos_fix,
                        time_window=export_time_window,
                        row_hint=count_data_rows(p_fix),
                        detail_truncated=bool(truncated_fix),
                        truncated_roles=truncated_fix,
                        missing_roles=missing_roles_fix,
                        user_fetch_complete=user_fetch_complete,
                        dim_budget_exhausted=export_dim_budget_exhausted,
                        cohort_uid_estimate=export_cohort_uid_estimate,
                        column_plan=export_column_plan or None,
                        file_size=None,
                    )
                else:
                    analysis_fix = _build_export_fallback_appendix(
                        missing=export_missing_cols,
                        missing_roles=missing_roles_fix,
                        row_hint=count_data_rows(p_fix),
                        time_window=export_time_window,
                        analyze_incomplete=not bool(missing_roles_fix),
                        shell_enabled=shell_enabled,
                    )
                # Re-attach appendix after file section if headings empty
                if (
                    "### 导出概况" in final
                    or "### 字段说明" in final
                    or "### 分析摘要" in final
                    or "### 报表统计" in final
                ):
                    # drop broken appendix tail and append fresh
                    cut = len(final)
                    for mk in _EXPORT_APPENDIX_MARKERS:
                        i = final.find(mk)
                        if i >= 0:
                            cut = min(cut, i)
                    head = final[:cut].rstrip()
                    final = head + "\n\n" + analysis_fix
                else:
                    final = final.rstrip() + "\n\n" + analysis_fix
                covered_fix = sorted(
                    _covered_export_roles(sandbox.id, export_run_id)
                ) if export_run_id else []
                deliverable_fix = ""
                for cand in (saved_paths or []):
                    if cand and _is_data_export_path(cand) and not str(cand).startswith("task/"):
                        deliverable_fix = cand
                        break
                lesson_fix = _append_export_skill_lesson_section(
                    sandbox,
                    export_run_id,
                    user_message=task_brief,
                    mode=mode_fix,
                    deliverable=deliverable_fix,
                    covered_roles=covered_fix,
                    missing_roles=missing_roles_fix,
                    fetched_view_pages=fetched_view_pages or {},
                    export_todos=todos_fix,
                    time_window=export_time_window,
                    analysis_md="",
                    mcp_failures=_summarize_mcp_failures(
                        mcp_class_fails, mcp_class_samples, mcp_class_tools,
                    ),
                    skill_ids=skill_ids,
                    shell_enabled=shell_enabled,
                    session_id=session_id,
                )
                if lesson_fix and "### 建议写入 Skill" not in (final or ""):
                    final = final.rstrip() + "\n\n" + lesson_fix
    # Safety: export finished without lesson section (e.g. early FINAL)
    if (
        export_like
        and sandbox
        and export_run_id
        and "### 建议写入 Skill" not in (final or "")
    ):
        miss_safe = _missing_export_roles(
            sandbox.id,
            export_run_id,
            export_target_roles,
            abandoned_roles=export_abandoned_roles,
        )
        cov_safe = sorted(_covered_export_roles(sandbox.id, export_run_id))
        mode_safe = "fallback" if export_fallback else "analyzed"
        deliv_safe = ""
        for cand in (saved_paths or []):
            if cand and _is_data_export_path(cand) and not str(cand).startswith("task/"):
                deliv_safe = cand
                break
        lesson_safe = _append_export_skill_lesson_section(
            sandbox,
            export_run_id,
            user_message=task_brief,
            mode=mode_safe,
            deliverable=deliv_safe,
            covered_roles=cov_safe,
            missing_roles=miss_safe,
            fetched_view_pages=fetched_view_pages or {},
            export_todos=export_todos if export_todos else _parse_export_todos(task_brief),
            time_window=export_time_window,
            analysis_md="",
            mcp_failures=_summarize_mcp_failures(
                mcp_class_fails, mcp_class_samples, mcp_class_tools,
            ),
            skill_ids=skill_ids,
            shell_enabled=shell_enabled,
            session_id=session_id,
        )
        if lesson_safe:
            final = (final or "").rstrip() + "\n\n" + lesson_safe
    final = _clean_final_answer(final or "") or (final or "").strip()
    if not (final or "").strip():
        final = "（本轮未产生文字回复；详见执行过程）"
    meta = json.dumps({
        "steps": visible_steps,
        "step_count": max(len(visible_steps), 1),
        "saved_paths": saved_paths,
        "task_relation": turn_intent.task_relation,
        "context_continuity": execution_context_continuity,
        "task_title": export_task_title or execution_context_title,
        "context_available_percent": context_available_percent,
        **{
            key: user_meta[key]
            for key in (
                "source", "channel_id", "chat_id", "chat_type", "user_id",
                "sender_username", "sender_display_name",
            )
            if user_meta.get(key)
        },
    }, ensure_ascii=False)
    db.add(ChatMessage(
        agent_id=agent.id,
        session_id=session_id,
        role="assistant",
        content=final,
        meta=meta,
        created_at=now_str(),
    ))
    db.commit()

    try:
        roll_gap = ""
        if export_like and export_run_id:
            miss_roll = _missing_export_roles(
                sandbox.id if sandbox else None,
                export_run_id,
                export_target_roles,
            )
            roll_gap = gap_tags_for_rolling(
                missing_roles=miss_roll,
                fallback=bool(export_fallback),
                mcp_failures=_summarize_mcp_failures(
                    mcp_class_fails, mcp_class_samples, mcp_class_tools,
                ),
            )
        await _append_rolling_summary(
            db, agent, session_id, llm, final, saved_paths, gap_tag=roll_gap,
        )
    except Exception:
        logger.exception("rolling summary failed agent=%s session=%s", agent.id, session_id)

    await hub.publish(key, {
        "type": "done",
        "content": (final or "")[:500],
        "content_truncated": len(final or "") > 500,
        "workplace_changed": files_written > 0 or bool(saved_paths),
    })
    _running[key] = False
    return final


async def _maybe_summarize(db: Session, agent: Agent, session_id: str, llm) -> str:
    """Append one rolling entry from the latest assistant turn."""
    last = (
        db.query(ChatMessage)
        .filter(
            ChatMessage.agent_id == agent.id,
            ChatMessage.session_id == session_id,
            ChatMessage.role == "assistant",
        )
        .order_by(ChatMessage.id.desc())
        .first()
    )
    if not last or not (last.content or "").strip():
        raise RuntimeError("当前会话暂无助手回复，无法生成滚动总结")
    final = last.content or ""
    saved: list[str] = []
    if last.meta:
        try:
            saved = list(json.loads(last.meta).get("saved_paths") or [])
        except Exception:
            saved = []
    return await _append_rolling_summary(db, agent, session_id, llm, final, saved)


async def generate_session_summary(db: Session, agent: Agent, session_id: str) -> str:
    """Append one rolling summary entry from the latest assistant turn."""
    llm = db.query(LLMResource).filter(LLMResource.id == agent.llm_id).first()
    return await _maybe_summarize(db, agent, session_id, llm)




async def _build_tools_desc(
    db: Session,
    agent: Agent,
    allowed: list[str],
    skill_ids: list[str],
    mcp_ids: list[str],
    rag_ids: list[str],
    save_dir: str = "",
    im_source: str = "",
    export_like: bool = False,
    strategy_blurb: str = "",
) -> str:
    has_shell = "shell" in allowed
    if save_dir:
        path_rule = (
            f"【路径规则】过程产物全部写入 `task/<毫秒时间戳>/`（JSON/CSV 文本）；"
            + (
                f"最终交付的 xlsx：优先用 SHELL（pandas/openpyxl）写入当前目录 `{save_dir}/`；"
                if has_shell else
                "WRITE 会自动创建父目录；最终 xlsx 由平台根据 task JSON/CSV 落盘；"
            ) +
            f"若未写出，平台才按 task JSON 回退合并。"
            f"禁止 WRITE 直接写 *.xlsx/*.pdf 等二进制；禁止把分片堆到当前目录；禁止再套 `workplace/`。"
        )
        write_example = f"WRITE: task/<毫秒时间戳>/final_data.json"
    else:
        path_rule = (
            "【路径规则】过程产物全部写入 `task/<毫秒时间戳>/`（JSON/CSV 文本）；"
            + (
                "最终交付的 xlsx：优先用 SHELL（pandas/openpyxl）写入当前目录（工作区根）；"
                if has_shell else
                "WRITE 会自动创建父目录；最终 xlsx 由平台根据 task JSON/CSV 落盘；"
            ) +
            "若未写出，平台才按 task JSON 回退合并。"
            "禁止 WRITE 直接写 *.xlsx/*.pdf 等二进制；禁止把分片堆到根目录；禁止再套 `workplace/`。"
        )
        write_example = "WRITE: task/<毫秒时间戳>/final_data.json"
    if strategy_blurb.strip():
        export_strategy = strategy_blurb.strip()
    elif export_like:
        export_strategy = (
            "【导出策略·强制阶段】\n"
            "1) 发现：list_* / describe_*，最多 1 次样例 query；\n"
            + (
                "2) 必须输出 PLAN:（目标/视图/筛选/输出列/需要资源/预算/SHELL 步骤）；\n"
                if has_shell
                else "2) 必须输出 PLAN:（目标/视图/筛选/输出列/需要资源/预算/平台交付步骤）；\n"
            )
            + "3) 限量 fetch：仅拉列意图/白名单资源；平台写入 task/page_N.json；"
            "满页须 OFFSET 续翻至短页；\n"
            + (
                "4) 分析：白名单齐套后优先 SHELL 写当前目录交付物；\n"
                if has_shell else
                "4) 分析：白名单齐套后写 task JSON/CSV，由平台生成最终交付物；\n"
            ) +
            "5) FINAL（含缺口诚实说明）。依赖以 PLAN「需要资源」+ 列计划为准，禁止默认全量拉取。\n"
            "建议控制分页；勿在 FINAL 粘贴数据表。"
        )
    else:
        export_strategy = (
            "【通用策略】先 PLAN（目标/步骤/完成标准/工具预算）；"
            "再按步骤调用工具；对照完成标准后 FINAL。优先阅读已绑定 Skill。"
        )
    export_strategy = _adapt_export_prompt_for_capabilities(
        export_strategy,
        shell_enabled=has_shell,
    )
    lines = [
        "【重要】每次只输出一种工具调用，且必须从行首开始，禁止使用 XML/tool_call 格式。",
        (
            "【格式】SHELL:/WRITE:/FINAL: 必须单独占一行，不要在说明文字同一行内夹杂工具指令。"
            if has_shell
            else "【格式】WRITE:/FINAL: 必须单独占一行，不要在说明文字同一行内夹杂工具指令。"
        ),
        path_rule,
        "【报表 FINAL】涉及数据报表/导出时，FINAL 必须精简，结构固定为：\n"
        "开场一句「…已完成！基于真实 ClickHouse / MCP…」\n"
        "### 导出概况（时间范围/总用户/总充值$/有下注/有卡/封禁/退款/文件大小；数字须可从 xlsx 复算）\n"
        "### 字段说明（共 N 列：# | 列名 | 数据来源 | 统计方法）\n"
        "备注（分→美元等）+ ### 下载文件\n"
        "禁止只回工程「交付类型/TODO/统计结果」模板；禁止无落盘编造概况数字；"
        "禁止在 FINAL 中粘贴 markdown 源数据表或样例行；"
        "末尾数据来源只列 MCP 视图名。\n"
        + export_strategy,
        "【发文件到消息渠道】用户要把已有报表/xlsx 发到 TG/飞书/钉钉等绑定渠道时："
        "优先使用工作目录已有文件（含 task/_stale_/），禁止为此再 query_ads_view；"
        "真正推送由平台完成，不要编造渠道 API。",
        "写文本文件示例（JSON/CSV/Markdown）：",
        write_example,
        "# 标题",
        "正文内容...",
    ]
    if (im_source or "").startswith("im:telegram"):
        lines.append(
            "【Telegram】本会话来自 Telegram；写入 workplace 的 xlsx/csv 等报表文件将由平台自动推送到聊天，"
            "无需自行调用 Telegram API。"
        )
    if "shell" in allowed:
        lines.append("- shell: SHELL: <POSIX /bin/sh command>（禁止混入思考文字；workplace 列表请用 READ:）")
    if "file_read" in allowed:
        lines.append("- file_read: READ: <path>（文件返回内容，目录返回列表；路径相对 workplace，根目录写 READ: workplace）")
    if "file_write" in allowed:
        lines.append(
            "- file_write: WRITE: <path>\\n<content>（仅文本；自动创建父目录，无需 mkdir；禁止直接 WRITE *.xlsx/*.pdf）"
        )
    if "shell" not in allowed and ({"file_read", "file_write"} & set(allowed)):
        lines.append(
            "【无 Shell 模式】用 READ 浏览目录、WRITE 创建嵌套文本文件；"
            "不要输出 SHELL，也不要因缺少 ls/mkdir 中止。"
        )
    if "file_search" in allowed:
        lines.append("- file_search: 可用 SHELL 在 workplace 内 find/grep 搜索文件内容")
    if "file_search_replace" in allowed:
        lines.append("- patch: PATCH: <path>\\n<old>\\n<new>")
    if "skill_read_md" in allowed or "skill_run_script" in allowed:
        for sid in skill_ids:
            sk = db.query(Skill).filter(Skill.id == sid).first()
            if sk:
                lines.append(f"- skill {sk.name}: RUN_SKILL: {sk.name} | SKILL_MD: {sid}")
    if mcp_ids and "mcp_tool_call" not in allowed:
        lines.append(
            "- 【软提示】Agent 已绑定 MCP，但未开启 mcp_tool_call 权限；"
            "如需拉数请在 Agent 允许操作中启用 MCP。"
        )
    if "mcp_tool_call" in allowed:
        for mid in mcp_ids:
            mcp = db.query(MCP).filter(MCP.id == mid).first()
            if not mcp:
                continue
            tools = await _get_mcp_tools_cached(mcp)
            if tools:
                lines.extend(_format_mcp_tools_for_prompt(mcp.name or mid, tools))
            else:
                name = mcp.name or mid
                lines.append(
                    f"- mcp {name}: 已绑定，当前 tools/list 暂空或缓存未刷新；"
                    "仍可尝试 `MCP: list_ads_views {}` / 管理页连接刷新后再 tools/list；"
                    "得到大脑常用 list_notes / recall / get_note"
                )
    if "httpmcp_call" in allowed:
        lines.append('- httpmcp: HTTPMCP: <id> {"tool":"<工具名>", ...变量}')
    if "rag_query" in allowed and rag_ids:
        lines.append("- rag: RAG: <query>")
    if "self_ask" in allowed:
        lines.append("- think: THINK: <thought>")
    lines.append("- done: FINAL: <answer>")
    return "\n".join(lines)
