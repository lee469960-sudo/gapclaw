"""Stateless utility functions and constants for the ReAct agent engine."""

from __future__ import annotations

import json
import logging
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, TYPE_CHECKING

from sqlalchemy.orm import Session

if TYPE_CHECKING:
    from app.models.mcp import MCP

logger = logging.getLogger(__name__)

# --- MCP / LLM config ---
_MCP_SOFT_FAIL_HINT_DEFAULT = 5
_MCP_CLASS_HARD_LIMIT = 999  # effectively disabled; kept for compat
_EXPORT_MCP_HARD_BLOCK_LIMIT = 999

# MCP error classes that are safe to retry automatically (transient failures)
_MCP_RETRYABLE_CLASSES: frozenset[str] = frozenset({
    "timeout",
    "connection",
    "mcp_remote",
    "empty",
})
# Max engine-level retries for transient MCP failures
_MCP_MAX_RETRIES = 3
# Retry backoff base in seconds (exponential: base * 2^attempt)
_MCP_RETRY_BACKOFF_BASE = 1.0

# --- Tool result limits ---
_TOOL_RESULT_MAX_CHARS = 6000
_WS_TOOL_CLIP_CHARS = 2000
_SHELL_OBS_MAX_CHARS = 2000
_SHELL_CHECKPOINT_MAX_CHARS = 64 * 1024

# --- Message trimming ---
_TRIM_TOOL_MSG_EVERY = 10
_KEEP_RECENT_TOOL_MSGS = 12
_KEEP_RECENT_TOOL_MSGS_EXPORT = 8
_OLD_TOOL_MSG_CAP = 500
_OLD_TOOL_MSG_CAP_EXPORT = 800
_PROGRESS_MAX_LINES = 80

# --- Export budget ---
_EXPORT_QUERY_BUDGET_DEFAULT = 14
_EXPORT_QUERY_BUDGET_TYPE_B = 18
_EXPORT_QUERY_BUDGET_TYPE_B_MAX = 22
_EXPORT_QUERY_BUDGET_TYPE_B_PAY_MAX = 30
_EXPORT_QUERY_BUDGET_FLOOR = 10
_EXPORT_QUERY_BUDGET_FULL_FETCH_MAX = 45
_EXPORT_PLAN_MAX_ATTEMPTS = 2
_EXPORT_ANALYZE_MAX_ROUNDS = 12
_EXPORT_ANALYZE_WRITE_GRACE = 6

# --- Export pagination ---
_EXPORT_PAGE_LIMIT = 1000
_EXPORT_USER_PAGE_LIMIT = 2000
_EXPORT_TINY_PAGE_LIMIT = 100
_EXPORT_FULL_PAGE_ROWS = 900
_EXPORT_MAX_PAGES_PER_VIEW = 3
_EXPORT_MAX_PAGES_PER_VIEW_PAY_COHORT = 8
_EXPORT_FACT_PAGE_CAP_MAX = 20
_EXPORT_MAX_PAGES_DIM = 1
_EXPORT_MAX_PAGES_USER = 6
_EXPORT_MAX_PAGES_USER_MAX = 12
_EXPORT_AUTO_OFFSET_MAX = 10
_EXPORT_DIM_RESERVE = 2
_EXPORT_DIM_FORCE_MAX_ROUNDS = 3
_EXPORT_CORE_ROLE_MAX_FAILURES = 3
_EXPORT_USER_UID_BATCH = 500

# --- Export timing ---
_FINALIZE_WINDOW = 5
_EXPORT_FINALIZE_RATIO = 0.18
_EXPORT_DISCOVER_RATIO = 0.15
_EXPORT_FETCH_IDLE_ESCAPE = 4
_TOOL_INTENT_ITER_FLOOR = 16
_ROLLING_MAX_ENTRIES = 40

# --- Cohort completeness ---
_COHORT_ROW_COMPLETE_RATIO = 0.85
_REMOTE_TINY_PAGE_LO = 25
_REMOTE_TINY_PAGE_HI = 35

# --- Data file suffixes ---
_DATA_FILE_SUFFIXES = (".xlsx", ".xls", ".csv")


# --- MCP tools cache ---
_mcp_tools_cache: dict[str, tuple[float, list[dict]]] = {}
_MCP_TOOLS_TTL_SEC = 600.0
_MCP_TOOLS_PROMPT_LIMIT = 30

# --- Internal tool actions ---
_INTERNAL_TOOL_ACTIONS = frozenset({"shell", "mcp_tool_call", "httpmcp_call"})

# --- ADS MCP tools ---
_ADS_MCP_TOOLS = frozenset({
    "list_ads_views",
    "describe_ads_view",
    "query_ads_view",
    "query_ads_metric",
})

# --- MCP non-tool fuse classes ---
_MCP_NON_TOOL_FUSE_CLASSES = frozenset({"format_clause"})

# --- Regex patterns ---
_SQL_FORMAT_CLAUSE_RE = re.compile(
    r"(?is)\s*;?\s*FORMAT\s+\w+\s*$|\s+FORMAT\s+\w+\b",
)
_VIEW_NAME_TOKEN_RE = re.compile(r"\b(view_result_[a-zA-Z0-9_]+)\b")
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
    r"需要角色|依赖角色|query_ads|pandas|openpyxl|to_excel|"
    r"page_\*|task/|ID\.txt|无需其他|仅\s*user",
    re.I,
)
_EXPORT_DATE_RANGE_RE = re.compile(
    r"(?P<y1>20\d{2})[-/.年](?P<m1>\d{1,2})[-/.月](?P<d1>\d{1,2})日?"
    r".{0,20}?"
    r"(?P<y2>20\d{2})[-/.年](?P<m2>\d{1,2})[-/.月](?P<d2>\d{1,2})日?",
    re.I | re.S,
)
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
_RE_SEND_CHANNEL_VERB = re.compile(
    r"(?:发送到|发送给|发到|推送到|推送给|推到|推给|发给|推送至)",
    re.I,
)
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
    r"(重新查|再查|重查|重新统计|重新拉取|再统计|再导出|"
    r"重新导出|继续导出|继续生成|重新生成)",
    re.I,
)

# --- Prompt hint constants ---
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
    "仅用户字段可只写身份源；禁止默认全量拉取所有视图）\n"
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
    "仅拉列计划/白名单内 resource；请依据 describe 返回的字段，用完整 SELECT "
    "和平台毫秒窗分页至短页。"
    "where 须为 JSON 对象；时间范围请放 sql 字段；禁止默认全量拉取。"
)
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
_MCP_FORMAT_STRIP_HINT = (
    "【FORMAT 纠偏】禁止在 query_ads_view 的 sql 中写 `FORMAT JSONEachRow` 或结尾分号；"
    "平台会自动 FORMAT。大结果请用 limit + 同窗 OFFSET/分页落 task/page_*.json，"
    "勿 toJSONString(groupArray(...))。"
)
_MCP_QUERY_UNION_HINT = (
    "【invalid_union 纠偏】query_ads_view 的 where 必须是对象，例如 "
    '{"stat_date":"2025-01-01"}，不能是字符串或数组；'
    "过滤字段名须来自 describe_ads_view 返回的真实列名。\n"
    '可执行示例: MCP: query_ads_view {"view":"<视图名>","where":{"stat_date":"YYYY-MM-DD"}}'
)

_PROVIDER_MESSAGE_ALIASES: dict[str, tuple[str, ...]] = {
    "telegram": ("tg", "telegram", "电报"),
    "feishu": ("飞书", "feishu", "lark"),
    "dingtalk": ("钉钉", "dingtalk"),
    "wecom": ("企微", "企业微信", "wecom"),
    "qq": ("qq",),
}


# ============================================================
# Stateless utility functions
# ============================================================

def _clip_tool_result(text: str, limit: int = _TOOL_RESULT_MAX_CHARS) -> str:
    text = text or ""
    if len(text) <= limit:
        return text
    return text[: limit - 12] + "\n…(已截断)"


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


def _short_text(text: str, limit: int = 120) -> str:
    compact = " ".join((text or "").split())
    if len(compact) <= limit:
        return compact
    return compact[: max(0, limit - 1)] + "…"


def _clean_mcp_tool_name(name: str) -> str:
    """Strip markdown/quote wrappers LLM often wraps around tool names."""
    return (name or "").strip().strip("`'\"")


def _mcp_tool_name(normalized: str) -> str:
    match = re.match(r"(?:MCP|HTTPMCP):\s*(\S+)", normalized or "")
    raw = match.group(1) if match else "mcp"
    return _clean_mcp_tool_name(raw) or "mcp"


def _rewrite_mcp_clean_tool_name(normalized: str) -> str:
    """Rewrite MCP: <tool> … so tool has no backticks/quotes."""
    m = re.match(r"(MCP:\s*)(\S+)(\s*)(.*)$", normalized or "", re.DOTALL)
    if not m:
        return normalized or ""
    cleaned = _clean_mcp_tool_name(m.group(2))
    if not cleaned or cleaned == m.group(2):
        return normalized or ""
    return f"{m.group(1)}{cleaned}{m.group(3)}{m.group(4)}"


def _shell_command(normalized: str) -> str:
    text = (normalized or "").strip()
    if text.startswith("SHELL:"):
        return text[6:].strip()
    return text


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


def _extract_mcp_view_name(normalized: str) -> str:
    """Best-effort extract view name from an MCP call line."""
    line = (normalized or "").strip()
    # Try args json first
    m = re.match(r"MCP:\s*\S+\s*(.+)$", line, re.DOTALL)
    if m:
        try:
            args = json.loads(m.group(1))
            if isinstance(args, dict):
                for key in ("view", "view_name", "resource"):
                    v = args.get(key)
                    if isinstance(v, str) and v.strip():
                        return v.strip()
        except Exception:
            pass
    # Fallback: SQL FROM
    sql_view = _extract_view_from_sql(line)
    if sql_view:
        return sql_view
    # Fallback: view name token in line
    for tok in _VIEW_NAME_TOKEN_RE.findall(line):
        return tok
    return ""


def _extract_view_from_sql(sql: str) -> str:
    """Extract view name from SQL FROM clause."""
    m = re.search(r"FROM\s+ads\.(\w+)", sql or "", re.I)
    return m.group(1) if m else ""


def _extract_view_from_mcp_example(example: str) -> str:
    """Extract view name from an MCP example line."""
    return _extract_mcp_view_name(example)


def _parse_mcp_args(normalized: str) -> dict:
    """Parse JSON args from an MCP: <tool> {json} line."""
    text = (normalized or "").strip()
    m = re.match(r"^MCP:\s*\S+\s*(.+)$", text, re.DOTALL)
    if not m:
        return {}
    raw = m.group(1).strip()
    try:
        val = json.loads(raw)
        if isinstance(val, dict):
            return val
    except Exception:
        pass
    # Try to extract JSON object from the text
    m2 = re.search(r"\{[\s\S]*\}", raw)
    if m2:
        try:
            val = json.loads(m2.group(0))
            if isinstance(val, dict):
                return val
        except Exception:
            pass
    return {}


def _tool_input_schema(tool: dict) -> dict:
    if not isinstance(tool, dict):
        return {}
    schema = tool.get("inputSchema")
    if isinstance(schema, dict):
        return schema
    inputs = tool.get("input_schema")
    if isinstance(inputs, dict):
        return inputs
    return {}


def _tool_required_fields(tool: dict) -> list[str]:
    schema = _tool_input_schema(tool)
    req = schema.get("required")
    if isinstance(req, list):
        return [str(r) for r in req]
    return []


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


def _is_mcp_list_or_describe(tool: str) -> bool:
    t = (tool or "").lower()
    return "describe" in t or t.startswith("list_") or t.startswith("list")


def _is_mcp_data_query(tool: str) -> bool:
    """True for query/search style tools (not list/describe)."""
    t = (tool or "").lower()
    if not t or _is_mcp_list_or_describe(t):
        return False
    return "query" in t or "search" in t or "fetch" in t


def _is_mcp_tool_failure(tool_result: str) -> bool:
    """Detect if a tool result indicates a failure."""
    text = (tool_result or "").lower()
    fail_patterns = ["error", "exception", "失败", "错误", "timeout", "超时", "超时"]
    return any(p in text for p in fail_patterns)


def _classify_mcp_error(result: str) -> str:
    """Classify MCP error into a short tag for tracking."""
    r = (result or "").lower()
    if not r:
        return "empty"
    if "timeout" in r or "超时" in r or "timed out" in r:
        return "timeout"
    if "connection" in r or "connect" in r or "refused" in r or "网络" in r:
        return "connection"
    if "permission" in r or "denied" in r or "权限" in r or "auth" in r:
        return "auth"
    if "not found" in r or "不存在" in r or "404" in r or "missing" in r:
        return "not_found"
    if "format" in r or "syntax" in r or "sql" in r or "parse" in r or "语法" in r:
        return "format"
    if "field" in r or "column" in r or "字段" in r or "列" in r:
        return "field"
    if "empty" in r or "null" in r or "无数据" in r or "0 rows" in r:
        return "empty"
    return "other"


def _mcp_error_signature(tool: str, result: str) -> str:
    """Generate a stable error signature for tracking duplicate failures."""
    cls = _classify_mcp_error(result)
    return f"{tool}|{cls}"


def _summarize_mcp_failures(
    error_sigs: list[str],
    tool_fails: dict[str, int],
) -> str:
    """Build a human-readable summary of recent MCP failures."""
    if not error_sigs:
        return ""
    lines = ["### 近期 MCP 错误摘要"]
    shown: set[str] = set()
    for sig in error_sigs[-8:]:
        if sig in shown:
            continue
        shown.add(sig)
        parts = sig.split("|")
        tool = parts[0] if parts else "?"
        cls = parts[1] if len(parts) > 1 else "?"
        count = tool_fails.get(tool, 0)
        lines.append(f"- `{tool}` (×{count}): {cls}")
    return "\n".join(lines)


def _step_results_are_mcp_failures_only(step_results: list[str]) -> bool:
    """True when all step results are MCP failures (no success)."""
    if not step_results:
        return False
    return all(_is_mcp_tool_failure(r) for r in step_results)


def _mcp_arg_hint(tool: str) -> str:
    """Return a brief hint about expected args for known MCP tools."""
    tool = (tool or "").strip()
    if tool == "query_ads_view":
        return 'expected: {"view":"<name>","sql":"SELECT ..."}'
    if tool == "describe_ads_view":
        return 'expected: {"view":"<name>"}'
    if tool == "list_ads_views":
        return "expected: {} (no args)"
    return ""


def _is_data_export_path(path: str) -> bool:
    """True for task/page_*.json and deliverable paths."""
    p = (path or "").strip()
    return bool(
        p.startswith("task/")
        or p.endswith(".xlsx")
        or p.endswith(".xls")
        or p.endswith(".csv")
    )


def _is_user_info_view(view: str) -> bool:
    v = (view or "").lower()
    return "user_info" in v or "user_register" in v


def _is_game_stat_or_bet_view(view: str) -> bool:
    v = (view or "").lower()
    return "game_stat" in v or "bet" in v


def _view_category(view: str) -> str:
    """View name → category for routing (pattern matching, not a role registry)."""
    v = (view or "").lower()
    if not v:
        return ""
    if "user_info" in v or "user_register" in v:
        return "user"
    if "channel" in v:
        return "channel"
    if "pay" in v and "order" in v:
        return "pay"
    if "cash" in v:
        return "cash"
    if "bet" in v or "game_stat" in v:
        return "bet"
    if "game" in v and "stat" not in v:
        return "game"
    return ""


def _is_time_windowed_export_role(view: str) -> bool:
    v = (view or "").lower()
    return bool("user_info" in v or "user_register" in v or "pay" in v or "cash" in v or "bet" in v or "game_stat" in v)


def _is_full_page_rows(
    row_count: int,
    *,
    limit: int | None = None,
    view: str | None = None,
) -> bool:
    """True if the page has enough rows to warrant continuation."""
    thresh = _full_page_row_threshold(limit)
    return row_count >= thresh


def _full_page_row_threshold(limit: int | None = None) -> int:
    lim = int(limit) if limit is not None and int(limit) > 0 else _EXPORT_PAGE_LIMIT
    return min(_EXPORT_FULL_PAGE_ROWS, int(lim * 0.9))


def _page_limit_for_view(view: str | None = None) -> int:
    if view and _is_user_info_view(view):
        return _EXPORT_USER_PAGE_LIMIT
    return _EXPORT_PAGE_LIMIT


def _looks_like_remote_tiny_page_cap(
    limit: int | None = None,
    *,
    view: str | None = None,
) -> bool:
    """Detect if a page limit looks suspiciously small (remote MCP default)."""
    lim = int(limit) if limit is not None else 0
    if lim <= 0:
        return False
    return _REMOTE_TINY_PAGE_LO <= lim <= _REMOTE_TINY_PAGE_HI


def _is_short_page_complete(
    row_count: int,
    *,
    limit: int | None = None,
    view: str | None = None,
) -> bool:
    """True if a short (< full-page threshold) result means fetching is complete."""
    thresh = _full_page_row_threshold(limit)
    return 0 <= row_count < thresh


def _sql_with_offset(sql: str, offset: int, *, limit: int = _EXPORT_PAGE_LIMIT) -> str:
    raw = (sql or "").strip()
    if not raw:
        return raw
    raw = re.sub(r"\s+LIMIT\s+\d+", "", raw, count=1, flags=re.I)
    raw = re.sub(r"\s+OFFSET\s+\d+", "", raw, count=1, flags=re.I)
    raw = raw.strip().rstrip(";").strip()
    return f"{raw} LIMIT {int(limit)} OFFSET {int(offset)}"


def _auto_offset_for_node_page(auto_done: int, *, limit: int = _EXPORT_PAGE_LIMIT) -> int:
    """Compute OFFSET for the Nth continuation page."""
    return int(auto_done) * int(limit)


def _offset_for_pages_done(pages: int, *, limit: int = _EXPORT_PAGE_LIMIT) -> int:
    return int(pages) * int(limit)


def _sql_base_for_pagination(sql: str) -> str:
    """Clean sql for pagination: strip LIMIT/OFFSET/FORMAT/semicolons."""
    raw = (sql or "").strip()
    raw = re.sub(r"\s+LIMIT\s+\d+", "", raw, count=1, flags=re.I)
    raw = re.sub(r"\s+OFFSET\s+\d+", "", raw, count=1, flags=re.I)
    raw = _SQL_FORMAT_CLAUSE_RE.sub(" ", raw)
    return raw.strip().rstrip(";").strip()


def _should_auto_offset_continue(
    *,
    auto_done: int,
    last_row_count: int,
    limit: int | None = None,
    max_pages: int = _EXPORT_AUTO_OFFSET_MAX,
) -> bool:
    if auto_done >= max_pages:
        return False
    if last_row_count <= 0:
        return False
    return _is_full_page_rows(last_row_count, limit=limit)


def _is_pay_cohort_brief(text: str) -> bool:
    """True when brief asks for 充值用户 cohort (pay create_time), not new registers."""
    return bool(re.search(r"充值用户", text or ""))


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


def _compute_user_page_cap(cohort_uid_estimate: int | None = None) -> int:
    """Dynamic user_info page cap from cohort estimate (once-pull-full)."""
    needed = _needed_pages_for_estimate(
        cohort_uid_estimate, page_limit=_EXPORT_USER_PAGE_LIMIT,
    )
    if needed <= 0:
        return _EXPORT_MAX_PAGES_USER
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
    return min(_EXPORT_FACT_PAGE_CAP_MAX, max(base, needed + 2))


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


def _is_export_like_task(user_message: str, skill_blob: str = "") -> bool:
    """True only for analytical export-report tasks (not every 导出/明细/用户数据)."""
    from app.services.task_policy import detect_task_policy
    return detect_task_policy(user_message, skill_blob=skill_blob).export_like


def _effective_max_iterations(
    agent,
    user_message: str = "",
    *,
    needs_tools: bool = False,
) -> int:
    """Honor Agent.max_iterations; floor tool-intent turns so PLAN/MCP can run."""
    del user_message
    configured = max(1, int(getattr(agent, "max_iterations", None) or 100))
    if needs_tools:
        return max(configured, _TOOL_INTENT_ITER_FLOOR)
    return configured


def _export_finalize_start(max_iters: int) -> int:
    """Iteration index (0-based) when export hard-finalize begins."""
    window = max(_FINALIZE_WINDOW, int(max_iters * _EXPORT_FINALIZE_RATIO))
    return max(0, max_iters - window)


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
            "SOP", "步骤", "需要角色", "依赖", "任务类型", "工具预算",
        )):
            continue
        if re.match(
            r"^(?:READ|WRITE|SHELL|MCP|HTTPMCP|FINAL|PATCH|THINK)\s*:",
            raw,
            re.I,
        ):
            continue
        if _EXPORT_TODO_JUNK_RE.search(raw) and "流水" not in raw:
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


def _count_numbered_cols(text: str) -> int:
    return len(_NUMBERED_COL_RE.findall(text or ""))


def _plan_output_columns_section(plan_text: str) -> str:
    """Slice PLAN「输出列」only; stop before 步骤/需要角色/etc. to avoid todo pollution."""
    if not plan_text:
        return ""
    m = re.search(r"输出列\s*[:：]?\s*", plan_text)
    if not m:
        return ""
    rest = plan_text[m.end():]
    stop = re.search(
        r"\n\s*[-*•]?\s*(?:"
        r"需要角色|依赖角色|依赖|步骤|计算与关联|计算|分页预算|分页|"
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
            sec = ""
        for c in _extract_numbered_column_specs(sec):
            if c["header"] not in {x["header"] for x in specs}:
                specs.append(c)
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
    """If brief has N numbered cols and todos have fewer, re-parse from brief (soft)."""
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
    user_pages = sum(int(n or 0) for v, n in pages.items() if _is_user_info_view(v))
    block_non_pay_facts = bool(
        (pay_cohort or full_fetch) and "user" in targets and user_pages < 1
    )
    need: list[str] = []
    for role in ("pay", "cash", "bet"):  # view categories for routing
        if role not in targets:
            continue
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


def _is_count_sql(sql: str) -> bool:
    return bool(re.search(r"\bcount\s*\(|COUNT\s*\(|count\s*\(|COUNT\s*\(", sql or ""))


def _parse_count_from_mcp_rows(rows: list | None) -> int | None:
    if not rows or not isinstance(rows, list) or not rows:
        return None
    first = rows[0]
    if isinstance(first, dict):
        for key in ("cnt", "count", "COUNT", "total", "rows", "row_count"):
            val = first.get(key)
            if val is not None:
                try:
                    return int(val)
                except (TypeError, ValueError):
                    pass
    return None


def _query_covers_time_window(args: dict, tw: dict | None) -> bool:
    if not tw:
        return True
    start_s, end_s = str(tw.get("start_ms")), str(tw.get("end_ms"))
    if not start_s or not end_s or start_s == "None" or end_s == "None":
        return True
    sql = str((args or {}).get("sql") or "")
    if start_s in sql and end_s in sql:
        return True
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


def _ensure_ads_sql_from(view: str, sql: str) -> str:
    """Ensure sql FROM target matches tool view/resource (generic alignment)."""
    raw = (sql or "").strip()
    view = (view or "").strip()
    if not raw or not view:
        return raw
    if re.search(r"FROM\s+ads\.", raw, re.I):
        return raw
    if raw.upper().startswith("SELECT"):
        return raw.replace("SELECT", f"SELECT * FROM ads.{view} WHERE", 1) if "WHERE" not in raw.upper() else raw
    return f"SELECT * FROM ads.{view} WHERE {raw}"


def _clip_mcp_error_text(text: str, *, max_chars: int = 400) -> str:
    text = (text or "").strip()
    if len(text) <= max_chars:
        return text
    return text[:max_chars - 12] + "\n…(已截断)"


def _format_mcp_exc_for_step(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {str(exc)[:200]}"


def _tw_ms_tuple(tw: dict | None) -> tuple[int | None, int | None]:
    if not isinstance(tw, dict):
        return None, None
    try:
        return int(tw.get("start_ms")), int(tw.get("end_ms"))
    except (TypeError, ValueError):
        return None, None


def _time_windows_equal(a: dict | None, b: dict | None) -> bool:
    sa, ea = _tw_ms_tuple(a)
    sb, eb = _tw_ms_tuple(b)
    if sa is not None and ea is not None and sb is not None and eb is not None:
        return sa == sb and ea == eb
    if isinstance(a, dict) and isinstance(b, dict):
        return a.get("label") == b.get("label")
    return a == b


def _export_time_window_label(time_window: dict | None) -> str:
    if not isinstance(time_window, dict):
        return ""
    return str(time_window.get("label") or "")


def _serialize_time_window_for_state(time_window: dict | None) -> dict | None:
    if not isinstance(time_window, dict):
        return None
    return {
        "start_ms": time_window.get("start_ms"),
        "end_ms": time_window.get("end_ms"),
        "label": time_window.get("label"),
        "cohort": time_window.get("cohort"),
    }


def _hydrate_time_window_from_state(state: dict | None) -> dict | None:
    if not isinstance(state, dict):
        return None
    tw = state.get("time_window")
    if isinstance(tw, dict):
        return dict(tw)
    return None


def _export_tz_from_text(text: str) -> tuple[int, str]:
    """Resolve UTC offset hours + label; default ET when 美国时间/美东/美国."""
    if re.search(r"太平洋|PT\b|PDT|PST", text or "", re.I):
        return -7, "美国太平洋时间(UTC-7)"
    if re.search(r"美国时间|美东|东部|ET\b|EDT|EST", text or "", re.I):
        return -4, "美国东部时间(UTC-4)"
    if re.search(r"美国", text or ""):
        return -4, "美国东部时间(UTC-4)"
    return -4, "美国东部时间(UTC-4)"


def _human_file_size(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f} KB"
    return f"{n / (1024 * 1024):.1f} MB"


def _todo_token(text: str) -> str:
    return re.sub(r"\s+", "", (text or "").lower())


def _headers_have_chinese(headers: list[str], *, min_count: int = 3) -> bool:
    n = 0
    for h in headers or []:
        if re.search(r"[一-鿿]", h or ""):
            n += 1
    return n >= min_count


def _header_matches_todo(headers: list[str], todo_text: str) -> bool:
    if not todo_text:
        return False
    tok = _todo_token(todo_text)
    if not tok:
        return False
    for h in headers or []:
        if tok in _todo_token(h):
            return True
    return False


def _find_header_index(headers: list[str], *names: str) -> int:
    for i, h in enumerate(headers or []):
        h_clean = re.sub(r"\s+", "", (h or "").lower())
        for name in names:
            if re.sub(r"\s+", "", (name or "").lower()) in h_clean:
                return i
    return -1


def _is_valid_saved_deliverable(sandbox, rel: str) -> bool:
    """True if rel is a readable file and looks like a deliverable."""
    if not rel:
        return False
    if not rel.lower().endswith(_DATA_FILE_SUFFIXES):
        return False
    if not sandbox:
        return False
    try:
        from app.services.workplace import is_valid_deliverable_file
        return is_valid_deliverable_file(sandbox, rel)
    except Exception:
        return False


def _starts_with_final_or_done(text: str) -> bool:
    """True if text starts with FINAL: or DONE:."""
    return bool(re.match(r"^\s*(?:FINAL|DONE)\s*:", text or ""))


def _looks_like_sql_fragment(text: str) -> bool:
    """True if text looks like a raw SQL fragment (not a full SELECT)."""
    t = (text or "").strip()
    return bool(
        t
        and not t.upper().startswith("SELECT")
        and re.search(r"\b(?:WHERE|AND|OR|IN|BETWEEN|LIKE|ORDER\s+BY|GROUP\s+BY)\b", t, re.I)
    )


def _format_describe_schema_soft_hint(
    view: str | None,
    tool_def: dict | None = None,
) -> str:
    """Build a soft coaching hint when describe schema is missing or incomplete."""
    parts = []
    if view:
        parts.append(f"建议先运行 describe_ads_view 查询 {view} 的完整字段列表")
    if tool_def:
        parts.append(_format_tool_schema_summary(tool_def))
    return "；".join(parts)


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


def _is_export_repair_intent(user_message: str) -> bool:
    """True if user message asks to repair/fix/rework a prior export."""
    text = (user_message or "").strip()
    return bool(
        re.search(
            r"不全|缺|少|补|修复|重跑|重新|续传|补齐|加列|"
            r"继续.{0,12}(?:导出|xlsx|excel|报表)",
            text,
        )
        and not re.search(r"^(?:你好|hi|hello|是吗|真的|确定)\b", text, re.I)
    )


def _is_export_repair_followup_intent(user_message: str) -> bool:
    """True if message looks like a followup to a prior export (fill/column add)."""
    text = (user_message or "").strip()
    return bool(
        re.search(
            r"加[一列列个]|加上|补[一列列个充]|再加|缺了|少了|"
            r"继续.{0,12}(?:导出|xlsx|excel|报表)",
            text,
        )
        and not re.search(r"^(?:你好|hi|hello)\b", text, re.I)
    )


def _is_export_completeness_feedback(user_message: str) -> bool:
    """True when user says '不全' as feedback (not a new brief)."""
    text = (user_message or "").strip()
    return bool(re.search(r"不全|漏了|缺了|少了列|不全啊|好像不全", text))


def _is_export_how_stats_ask(user_message: str) -> bool:
    """True when user asks how prior stats were computed."""
    text = (user_message or "").strip()
    return bool(re.search(r"怎么算|如何统计|数据来源|口径|怎么来的|怎么得出|依据", text))


def _wants_workplace_save(user_message: str) -> bool:
    return bool(re.search(r"保存|save|记住|存储|暂存|存下", user_message or "", re.I))


def _autosave_path(user_message: str, save_dir: str = "") -> str:
    ts = str(int(time.time() * 1000))
    base = save_dir.strip().strip("/") if save_dir else ""
    prefix = f"{base}/" if base else ""
    return f"{prefix}autosave_{ts}.md"


def _user_wants_task_dir(user_message: str) -> bool:
    return bool(re.search(r"task|任务目录|task/", user_message or "", re.I))


def _is_temp_setup_preview(preview: str) -> bool:
    return bool(re.search(r"pip\s+install|apt\s+get|临时|temp|setup", (preview or ""), re.I))


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


def _is_type_b_export(
    view_mode: str,
    column_plan: list[dict] | None,
) -> bool:
    """True for column-driven multi-role export (not single_view)."""
    if (view_mode or "").strip().lower() == "single_view":
        return False
    return any(
        isinstance(c, dict) and str(c.get("header") or "").strip()
        for c in (column_plan or [])
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
    desired = min(cap, max(floor, desired))
    return desired, cap


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


def _compute_full_fetch_budget(
    *,
    type_b: bool = False,
    pay_cohort: bool = False,
    fact_page_cap: int = 0,
    current: int = 0,
    user_page_cap: int = 0,
) -> int:
    """Adaptive full_fetch budget from page caps."""
    base = max(int(current or _EXPORT_QUERY_BUDGET_TYPE_B or 0), _EXPORT_QUERY_BUDGET_FLOOR)
    if not type_b:
        return base
    extra = int(fact_page_cap or 0) + int(user_page_cap or 0)
    if pay_cohort:
        extra += 4
    return min(_EXPORT_QUERY_BUDGET_FULL_FETCH_MAX, base + extra)


# ---- MCP naming helpers ----

def _bound_mcp_names(db: Session, mcp_ids: list[str]) -> list[str]:
    """Return display names for bound MCP resources."""
    from app.models import MCP  # lazy import

    names: list[str] = []
    for mid in mcp_ids or []:
        mcp = db.query(MCP).filter(MCP.id == mid).first()
        if mcp:
            names.append(mcp.name or mid)
    return names


# ---- MCP tool metadata cache ----

_mcp_tools_cache: dict[str, tuple[float, list[dict]]] = {}
_MCP_TOOLS_TTL_SEC: float = 600.0
_MCP_TOOLS_PROMPT_LIMIT: int = 30

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


def _tool_input_schema(tool: dict) -> dict:
    schema = tool.get("inputSchema") or tool.get("input_schema") or {}
    return schema if isinstance(schema, dict) else {}


def _tool_required_fields(tool: dict) -> list[str]:
    schema = _tool_input_schema(tool)
    req = schema.get("required")
    if isinstance(req, list):
        return [str(x) for x in req if x]
    return []


def _get_mcp_tool_hint(tool: str) -> str:
    return _MCP_TOOL_HINTS.get((tool or "").strip(), "")


async def _get_mcp_tools_cached(mcp: MCP) -> list[dict]:
    mid = mcp.id or ""
    now = time.monotonic()
    hit = _mcp_tools_cache.get(mid)
    if hit and now - hit[0] < _MCP_TOOLS_TTL_SEC and hit[1]:
        return hit[1]
    try:
        from app.services.mcp_client import connect_mcp_detail  # lazy import
        detail = await connect_mcp_detail(mcp)
    except Exception:
        return hit[1] if hit else []
    tools = detail.get("tools") or []
    if tools:
        _mcp_tools_cache[mid] = (now, tools)
    return tools


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
