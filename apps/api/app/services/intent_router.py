"""LLM turn-intent router: chat vs data_query vs export_report (no regex primary path).

LLM outputs calendar-level time_window; the engine deterministically converts to ms.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from app.services.task_policy import (
    EXPORT_PLAN_HINT,
    GENERIC_PLAN_HINT,
    TaskPolicy,
    detect_export_report_task,
    extract_numbered_column_headers,
)

logger = logging.getLogger(__name__)

_VALID_INTENTS = frozenset({"chat", "data_query", "export_report", "other_tools"})
_VALID_TASK_RELATIONS = frozenset({"new", "continue", "revise", "unknown"})
_VALID_CONVERSATION_ACTIONS = frozenset({"respond", "summarize_session"})
_VALID_RESULT_SHAPES = frozenset({
    "unspecified", "single_value", "summary", "time_series", "detail_list", "mixed",
})
_VALID_TEMPORAL_GRAINS = frozenset({
    "unspecified", "hour", "day", "week", "month",
})


def build_task_relation_context(
    history: list | None,
    prior_state: dict[str, Any] | None,
) -> str:
    """Build shared dialogue/runtime evidence for all intent-routing entrypoints."""
    lines: list[str] = []
    for item in list(history or [])[-8:]:
        role = str(getattr(item, "role", "") or "").strip()
        content = re.sub(r"\s+", " ", str(getattr(item, "content", "") or "")).strip()
        if role in ("user", "assistant") and content:
            lines.append(f"{role}: {content[:500]}")
        if role != "assistant":
            continue
        raw_meta = getattr(item, "meta", None)
        if isinstance(raw_meta, str):
            try:
                meta = json.loads(raw_meta or "{}")
            except Exception:
                meta = {}
        else:
            meta = raw_meta if isinstance(raw_meta, dict) else {}
        if not isinstance(meta, dict):
            continue
        actions = [
            str(step.get("action") or "").strip()
            for step in (meta.get("steps") or [])
            if isinstance(step, dict) and str(step.get("action") or "").strip()
        ]
        verification = meta.get("verification")
        runtime = {
            "export_run_id": str(meta.get("export_run_id") or ""),
            "saved_paths": [
                str(path) for path in (meta.get("saved_paths") or []) if str(path)
            ][:8],
            "verification_status": str(
                (verification or {}).get("status")
                if isinstance(verification, dict)
                else ""
            ),
            "actions": actions[-8:],
        }
        if any(runtime.values()):
            lines.append("assistant_runtime: " + json.dumps(runtime, ensure_ascii=False))
    state = prior_state if isinstance(prior_state, dict) else {}
    if state:
        export_contract = (
            state.get("export_contract")
            if isinstance(state.get("export_contract"), dict)
            else {}
        )
        task_spec = (
            export_contract.get("task_spec")
            if isinstance(export_contract.get("task_spec"), dict)
            else {}
        )
        query_contract = (
            export_contract.get("query_contract")
            if isinstance(export_contract.get("query_contract"), dict)
            else {}
        )
        columns = list(
            state.get("analyze_columns")
            or task_spec.get("requested_columns")
            or query_contract.get("output_columns")
            or []
        )
        artifacts = []
        if str(query_contract.get("sql") or "").strip():
            artifacts.append("sql")
        if str(state.get("deliverable") or "").strip():
            artifacts.append("data_file")
        summary = {
            "task_title": str(state.get("task_title") or "")[:160],
            "source_brief": str(state.get("source_brief") or "")[:1200],
            "requested_columns": columns,
            "time_window": state.get("time_window") or {},
            "completeness": str(state.get("completeness") or ""),
            "deliverable": str(state.get("deliverable") or ""),
            "available_artifacts": artifacts,
        }
        lines.append("prior_export_state: " + json.dumps(summary, ensure_ascii=False))
    return "\n".join(lines)[-5000:]

_TZ_OFFSETS: dict[str, tuple[int, str]] = {
    "ET": (-4, "美国东部时间(UTC-4)"),
    "EST": (-4, "美国东部时间(UTC-4)"),
    "EDT": (-4, "美国东部时间(UTC-4)"),
    "UTC": (0, "UTC"),
    "GMT": (0, "UTC"),
    "CST": (8, "中国标准时间(UTC+8)"),
    "CN": (8, "中国标准时间(UTC+8)"),
    "UTC+8": (8, "中国标准时间(UTC+8)"),
    "UTC-4": (-4, "美国东部时间(UTC-4)"),
}

_TASKISH_RE = re.compile(
    r"日志|查询|获取|注册|导出|报表|xlsx|sql|统计|人数|金额|提现|充值|分析|mcp|视图|"
    r"重新处理|再处理|排查|返奖|继续处理|继续工作|跟进|查原因|这三个问题",
    re.I,
)
_DIGIT_OR_DATE_RE = re.compile(r"\d|月|日|今天|昨日|昨天|本周|本月")
_TIMEISH_RE = re.compile(
    r"今天|昨日|昨天|本日|本周|本月|至今|最近|\d+\s*月|\d+\s*日|日期|时间窗",
    re.I,
)
_METRICISH_RE = re.compile(
    r"人数|数量|金额|次数|统计|注册|充值|提现|卡|返奖|下注|查询|获取",
    re.I,
)

_INTENT_SYSTEM = """你是 GAP 平台的回合意图分类器。只输出一个 JSON 对象，不要 Markdown，不要解释。

字段：
- intent: chat | data_query | export_report | other_tools
  - chat: 寒暄、能力介绍、闲聊、纯概念问答（不需要查库/写文件）
  - data_query: 查数、统计指标、写/跑 SQL、看视图/日志数据（「新增注册人数」若不是导出多列表 xlsx，选这个）
  - export_report: 明确要导出多列分析报表 / 用户明细列表 / xlsx / excel / 落盘明细表（通常列出多个输出字段）
  - other_tools: 读改 workplace 文件、跑脚本、非数据查询的工具任务
- reason: 一句中文理由
- wants_deliverable: boolean，是否要 xlsx/报表文件
- query_goal: 一句话查数/任务目标；chat 可空字符串
- conversation_action: respond | summarize_session
  - respond: 普通对话回复
  - summarize_session: 用户要求总结、回顾或梳理当前会话中的聊天与任务过程
  该字段由语义意图判断，不要仅凭单个关键词；若为 summarize_session，intent 必须为 chat。
- task_relation: new | continue | revise | unknown
  - new: 与最近对话无关的新任务
  - continue: 继续执行最近任务，未重述的列、时间窗和口径应继承
  - revise: 修改最近任务，继承未修改部分，以本轮明确变更为准
  - unknown: 无法判断或不存在可关联任务
  当输入包含“最近对话”时此字段必填；不得省略。
- verification_required: boolean。用户对上一轮 SQL、统计口径、数据量、完整性或文件结果提出质疑、要求核对时为 true；普通续作或新任务为 false。
- verification_targets: 字符串数组。需要重新验证的具体列、口径、数据量、SQL、文件或用户指出的问题；不得把上一轮助手结论当作验证结果。
- requested_artifacts: 字符串数组。本轮明确希望查看或取得的已有产物，取值为 sql | data_file | methods；没有则为空数组。它描述用户要什么，不负责判断产物是否存在。
- result_shape: unspecified | single_value | summary | time_series | detail_list | mixed
  - single_value: 单个指标值
  - summary: 整个时间窗的一行汇总/合计
  - time_series: 按时间粒度逐行展示完整序列
  - detail_list: 时间窗内的逐条明细记录
  - mixed: 时间序列或明细列表 + 区间汇总
  - unspecified: 用户语义确实无法判断
- temporal_grain: unspecified | hour | day | week | month。仅 time_series/mixed 需要；根据用户表达和时间跨度选择。
  对跨多日的常规运营、趋势、每日表现或要求“按照日期列表显示”的查询，通常选择 time_series + day；
  用户明确要求查看整个视图、全部匹配记录或逐条明细时选择 detail_list，并保留日期字段和视图中与任务相关的完整列；
  只有用户明确要总计、累计、整体概况时才选择 summary。不要把时间范围自动等同于单行区间汇总。
- metrics: 指标数组。每项可为字符串，或对象（推荐）：
  {"goal":"充值卡数量","kind":"count_distinct","entity_hint":"payment_card"}
  kind: count|sum|count_distinct|flag|other；entity_hint 用业务实体英文短词，禁止写死具体视图名/表名
- metric_intents: 可选，与 metrics 对象数组同义（二选一即可）
- time_window: null，或对象（查数/导出涉及日期时必填日历字段）：
  禁止在意图里写 view_result_* / role=pay|cash|bet（资源由运行时 MCP 绑定）。
  - start_date / end_date: YYYY-MM-DD（单日两者相同）
  - inclusive_end_day: true（默认；引擎将 end 扩成次日 00:00 半开区间）
  - tz: ET | UTC | CST（使用运行时给出的默认时区；优先级：用户显式时区 > 会话备注 > 平台默认）
  - label: 人类可读说明（含日期与时区）
  禁止自行编造 start_ms/end_ms（毫秒由平台计算）。年份缺省用「参考此刻」所在年；「今天」相对参考此刻。

规则：
1. 「获取一下X日的新增注册人数」→ data_query + metrics + time_window 日历字段。
2. 仅绑定导出 Skill 不能把单指标查数升级成 export_report。
3. 「导出…xlsx / 14列注册用户报表」→ export_report；若用户消息含编号列清单，metrics 必须带上各列 goal（含投注/返奖/返浆等），禁止默认空数组；禁止编造资源名，资源必须来自 MCP 目录。
   「导出某类用户列表/明细」并明确列出多个返回字段，也属于文件交付任务：即使没有出现 Excel/xlsx，仍选 export_report、wants_deliverable=true。
4. 「你好 / 你能做什么」→ chat。
5. 「获取/分析系统日志」→ other_tools 或 data_query（需要工具），不是 chat。
6. 「重新处理 / 继续排查 / 跟进刚才的问题」等续作、返工不得选 chat（不要只口头答应）。
   若最近任务是导出，且用户要求修复后重新计算/重新导出，仍选 export_report、wants_deliverable=true，
   task_relation 选 continue 或 revise，并继承未修改的列、时间窗和口径。
7. 用户写「返浆」时 metrics goal 用「总返奖金额」或保留「返浆」原词（引擎会同义对齐）。
8. 必须结合提供的最近对话与运行证据判断 task_relation；简短续作不得被当成空白新任务。
   助手上一轮若只有“准备排查/正在修复/随后导出”等计划性回复，但运行证据没有新产物或验证结果，
   表示最近任务仍未执行完成；用户追问进度时应关联该任务，不得回答“没有进行中的任务”。
   最近导出状态中的任务标题、原始需求、输出列、时间窗属于可继承任务契约；助手自然语言中的完成声明不属于验证证据。
9. verification_required=true 时仍要判断用户希望继续/修订的真实任务，不得只复述上一轮结果；后续引擎会据此实际重查和比较。
10. 用户要求“输出/查看上一轮 SQL、文件或统计方法”时，必须在 requested_artifacts 中声明对应产物，并将 task_relation 设为 continue；不要重新生成另一套查询。
11. result_shape=time_series 表示完整时间序列契约：查询覆盖 time_window，按 temporal_grain 排序逐行返回；不得只给一行总计替代时间序列。意图层不得编造结果。
12. 用户使用命名的业务概念、指标集合或上位概念时，query_goal 与 metrics.goal 必须保留用户原始措辞；不要在意图层擅自改名、缩减为任意子集或绑定数据表。具体指标展开由后续 Skill/MCP 语义与运行上下文完成。

示例：
用户：获取一下8月6日的新增注册人数
{"intent":"data_query","reason":"单日注册人数","wants_deliverable":false,"query_goal":"统计8月6日新增注册人数","metrics":[{"goal":"注册人数","kind":"count","entity_hint":"user_register"}],"time_window":{"start_date":"2026-08-06","end_date":"2026-08-06","inclusive_end_day":true,"tz":"ET","label":"2026-08-06 美国东部全日"}}

用户：查询充值卡数量和提现卡数量
{"intent":"data_query","reason":"卡号排重","wants_deliverable":false,"query_goal":"统计充值/提现卡数量","metrics":[{"goal":"充值卡数量","kind":"count_distinct","entity_hint":"payment_card"},{"goal":"提现卡数量","kind":"count_distinct","entity_hint":"payout_card"}],"time_window":null}

用户：查询6月1日至今的注册人数和充值金额
{"intent":"data_query","reason":"区间累计指标","wants_deliverable":false,"query_goal":"6月1日至今注册与充值总计","result_shape":"summary","temporal_grain":"unspecified","metrics":[{"goal":"注册人数","kind":"count","entity_hint":"user_register"},{"goal":"充值金额","kind":"sum","entity_hint":"payment"}],"time_window":{"start_date":"2026-06-01","end_date":"2026-08-07","inclusive_end_day":true,"tz":"ET","label":"2026-06-01至参考日 ET"}}

用户：查询8月1日至今的常规运营统计，按照日期完整列表显示
{"intent":"data_query","reason":"跨日期运营时间序列","wants_deliverable":false,"query_goal":"按日查看8月1日至今的常规运营统计数据","result_shape":"time_series","temporal_grain":"day","metrics":[{"goal":"常规运营统计数据","kind":"other","entity_hint":"operations"}],"time_window":{"start_date":"2026-08-01","end_date":"2026-08-14","inclusive_end_day":true,"tz":"ET","label":"2026-08-01至参考日 ET"}}

用户：导出充值用户分析 xlsx，列含：1.用户ID 2.总充值金额 3.总下注金额 4.总返奖金额
{"intent":"export_report","reason":"多列xlsx含投注返奖","wants_deliverable":true,"query_goal":"导出充值用户分析表","metrics":[{"goal":"用户ID","kind":"other","entity_hint":"user"},{"goal":"总充值金额","kind":"sum","entity_hint":"payment"},{"goal":"总下注金额","kind":"sum","entity_hint":"bet_volume"},{"goal":"总返奖金额","kind":"sum","entity_hint":"bet_payout"}],"time_window":null}

用户：导出昨天在指定游戏中有下注行为的用户列表，包含玩家账号、用户ID、注册时间、下注笔数、生涯充值金额、生涯提现金额
{"intent":"export_report","reason":"导出带多个字段的用户明细列表","wants_deliverable":true,"query_goal":"导出指定游戏下注用户明细","metrics":[{"goal":"玩家账号","kind":"other","entity_hint":"user_account"},{"goal":"用户ID","kind":"other","entity_hint":"user"},{"goal":"注册时间","kind":"other","entity_hint":"registration_time"},{"goal":"下注笔数","kind":"count","entity_hint":"bet"},{"goal":"生涯充值金额","kind":"sum","entity_hint":"lifetime_payment"},{"goal":"生涯提现金额","kind":"sum","entity_hint":"lifetime_payout"}],"time_window":null}

用户：你好
{"intent":"chat","reason":"寒暄","wants_deliverable":false,"query_goal":"","conversation_action":"respond","metrics":[],"time_window":null}

用户：梳理一下我们这次对话里做过的导出任务、返工原因和遗留问题
{"intent":"chat","reason":"总结当前会话中的任务过程与结论","wants_deliverable":false,"query_goal":"总结当前会话","conversation_action":"summarize_session","metrics":[],"time_window":null,"task_relation":"continue"}

最近任务已有 SQL 产物；用户：输出一下 SQL 语句
{"intent":"data_query","reason":"读取最近任务的 SQL 产物","wants_deliverable":false,"query_goal":"输出最近任务 SQL","metrics":[],"time_window":null,"task_relation":"continue","verification_required":false,"verification_targets":[],"requested_artifacts":["sql"]}

用户：重新处理这三个问题并排查返奖数据缺失
{"intent":"other_tools","reason":"续作排查需工具","wants_deliverable":false,"query_goal":"重新处理三问题并排查返奖缺失","metrics":[],"time_window":null}

最近任务：导出充值用户分析表；用户：银行卡数量获取失败，修复后重新计算并导出
{"intent":"export_report","reason":"修订最近导出并重新交付","wants_deliverable":true,"query_goal":"修复并重新导出充值用户分析表","metrics":[{"goal":"充值银行卡数量","kind":"count_distinct","entity_hint":"payment_card"},{"goal":"提现银行卡数量","kind":"count_distinct","entity_hint":"payout_card"}],"time_window":null,"task_relation":"revise","verification_required":true,"verification_targets":["充值银行卡数量","提现银行卡数量","xlsx文件"]}
"""


@dataclass
class MetricIntentItem:
    """MCP-agnostic metric intent (no view/role hardcodes)."""

    goal: str
    kind: str = "other"
    entity_hint: str = ""
    resource_hint: str = ""

    def to_dict(self) -> dict[str, str]:
        return {
            "goal": self.goal,
            "kind": self.kind,
            "entity_hint": self.entity_hint,
            "resource_hint": self.resource_hint,
        }


_VALID_METRIC_KINDS = frozenset({
    "count", "sum", "count_distinct", "flag", "other",
})


@dataclass
class TurnIntent:
    intent: str = "data_query"
    reason: str = ""
    wants_deliverable: bool = False
    query_goal: str = ""
    conversation_action: str = "respond"
    metrics: list[str] = field(default_factory=list)
    metric_intents: list[MetricIntentItem] = field(default_factory=list)
    time_window: dict[str, Any] | None = None
    task_relation: str = "unknown"
    verification_required: bool = False
    verification_targets: list[str] = field(default_factory=list)
    requested_artifacts: list[str] = field(default_factory=list)
    result_shape: str = "unspecified"
    temporal_grain: str = "unspecified"
    raw: dict[str, Any] = field(default_factory=dict)
    source: str = "fallback"  # llm | fallback

    @property
    def is_chat(self) -> bool:
        return self.intent == "chat"

    @property
    def is_export(self) -> bool:
        return self.intent == "export_report"

    @property
    def wants_session_summary(self) -> bool:
        return self.is_chat and self.conversation_action == "summarize_session"

    @property
    def needs_tools(self) -> bool:
        return self.intent != "chat"


def _strip_think_noise(text: str) -> str:
    """Remove model <think>…</think> blocks (and orphan closers) before JSON parse."""
    raw = text or ""
    raw = re.sub(r"<think\b[^>]*>[\s\S]*?</think>", "", raw, flags=re.I)
    raw = re.sub(r"</think>", "", raw, flags=re.I)
    raw = re.sub(r"<think\b[^>]*>", "", raw, flags=re.I)
    return raw.strip()


def _extract_json_object(text: str) -> dict[str, Any] | None:
    raw = _strip_think_noise(text or "")
    if not raw:
        return None
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.I)
        raw = re.sub(r"\s*```$", "", raw)
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else None
    except Exception:
        pass
    # Decode one JSON value at a time. A greedy ``{.*}`` slice makes valid
    # structured output unreadable when a provider adds brace-bearing prose
    # before or after the actual object.
    decoder = json.JSONDecoder()
    for index, char in enumerate(raw):
        if char != "{":
            continue
        try:
            data, _ = decoder.raw_decode(raw[index:])
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        if isinstance(data, dict):
            return data
    return None


def _append_intent_repair(
    messages: list[dict[str, str]],
    raw: str,
    error: str,
) -> None:
    """Give the model its invalid output and contract error for the next attempt."""
    messages.append({
        "role": "assistant",
        "content": (raw or "(空响应)")[:2400],
    })
    relation_hint = ""
    if error == "undecided_task_relation":
        relation_hint = (
            " 已提供最近任务运行证据，task_relation 必须据此选择 new、continue 或 revise，"
            "不能返回 unknown。"
        )
    messages.append({
        "role": "user",
        "content": (
            f"上一次输出未通过意图契约校验，错误为：{error}。"
            "请修复上一份输出，仅返回一个完整 JSON 对象，不要输出推理过程、Markdown 或解释。"
            "必须补齐系统消息要求的字段，并保留当前用户消息中的命名业务概念和原始措辞；"
            "不要自行改名、缩减指标集合或绑定具体数据表。"
            + relation_hint
        ),
    })


def _parse_ymd(value: Any) -> tuple[int, int, int] | None:
    s = str(value or "").strip()
    m = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})$", s)
    if not m:
        return None
    try:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        datetime(y, mo, d)  # validate
        return y, mo, d
    except ValueError:
        return None


def _tz_from_token(tz_raw: str) -> tuple[int, str, str]:
    key = (tz_raw or "ET").strip().upper()
    # Allow labels like 美国东部时间(UTC-4)
    if "UTC-4" in key or "东部" in (tz_raw or "") or key in ("ET", "EST", "EDT"):
        key = "ET"
    elif (
        "UTC+8" in key
        or "ASIA/SHANGHAI" in key
        or "北京" in (tz_raw or "")
        or "中国" in (tz_raw or "")
        or key in ("CST", "CN")
    ):
        key = "CST"
    elif key in ("UTC", "GMT") or (tz_raw or "").strip().upper() == "UTC":
        key = "UTC"
    elif key not in _TZ_OFFSETS:
        key = "ET"
    offset, label = _TZ_OFFSETS[key]
    return offset, label, key


def _timezone_token_from_text(text: str) -> str | None:
    """Resolve an explicitly stated timezone without treating note prose as a task."""
    raw = str(text or "")
    upper = raw.upper()
    if re.search(r"北京时间|北京时区|中国标准时间|亚洲/上海", raw) or re.search(
        r"ASIA/SHANGHAI|UTC\s*\+\s*8", upper,
    ):
        return "CST"
    if re.search(r"美国东部时间|美东时间|美东时区", raw) or re.search(
        r"AMERICA/NEW_YORK|UTC\s*-\s*4|\b(?:ET|EST|EDT)\b", upper,
    ):
        return "ET"
    if re.search(r"\b(?:UTC|GMT)\b", upper):
        return "UTC"
    return None


def resolve_context_timezone(
    user_message: str,
    context_note: str = "",
    *,
    platform_default: str = "ET",
) -> str:
    """Timezone precedence: explicit user text, session note, platform default."""
    return (
        _timezone_token_from_text(user_message)
        or _timezone_token_from_text(context_note)
        or _tz_from_token(platform_default)[2]
    )


def _calendar_window_from_message(
    user_message: str,
    *,
    now: datetime,
    tz_key: str,
) -> dict[str, Any] | None:
    """Deterministic fallback for a single calendar day mentioned by the user."""
    text = str(user_message or "")
    offset_h, _label, normalized_tz = _tz_from_token(tz_key)
    local_now = now.astimezone(timezone(timedelta(hours=offset_h)))
    target = None

    full = re.search(r"(?<!\d)(20\d{2})[-/.年](\d{1,2})[-/.月](\d{1,2})\s*[日号]?", text)
    month_day = re.search(r"(?<!\d)(\d{1,2})\s*月\s*(\d{1,2})\s*[日号]?", text)
    day_only = re.search(r"(?<!\d)(\d{1,2})\s*[号日](?!\d)", text)
    try:
        if full:
            target = datetime(int(full.group(1)), int(full.group(2)), int(full.group(3))).date()
        elif month_day:
            target = datetime(local_now.year, int(month_day.group(1)), int(month_day.group(2))).date()
        elif day_only:
            target = datetime(local_now.year, local_now.month, int(day_only.group(1))).date()
        elif re.search(r"昨天|昨日", text):
            target = (local_now - timedelta(days=1)).date()
        elif re.search(r"今天|今日", text):
            target = local_now.date()
    except ValueError:
        return None
    if target is None:
        return None
    ymd = target.isoformat()
    return {
        "start_date": ymd,
        "end_date": ymd,
        "inclusive_end_day": True,
        "tz": normalized_tz,
        "label": "",
    }


def resolve_time_window_from_intent(
    tw: dict[str, Any] | None,
    *,
    now: datetime | None = None,
) -> dict[str, Any] | None:
    """Prefer calendar fields → deterministic ms; fall back to raw ms."""
    if not isinstance(tw, dict):
        return None
    now = now or datetime.now(timezone.utc)
    start_ymd = _parse_ymd(tw.get("start_date"))
    end_ymd = _parse_ymd(tw.get("end_date")) or start_ymd
    if start_ymd and end_ymd:
        offset_h, tz_label, tz_key = _tz_from_token(str(tw.get("tz") or "ET"))
        tz = timezone(timedelta(hours=offset_h))
        y1, m1, d1 = start_ymd
        y2, m2, d2 = end_ymd
        start_dt = datetime(y1, m1, d1, 0, 0, 0, tzinfo=tz)
        end_day = datetime(y2, m2, d2, 0, 0, 0, tzinfo=tz)
        inclusive = tw.get("inclusive_end_day")
        if inclusive is None:
            inclusive = True
        if inclusive in (True, "true", "1", 1, "yes"):
            end_dt = end_day + timedelta(days=1)
        else:
            end_dt = end_day
        start_ms = int(start_dt.timestamp() * 1000)
        end_ms = int(end_dt.timestamp() * 1000)
        if end_ms <= start_ms:
            return None
        label = str(tw.get("label") or "").strip()
        if not label:
            if start_ymd == end_ymd:
                label = f"{y1:04d}-{m1:02d}-{d1:02d} {tz_label}全日"
            else:
                label = (
                    f"{y1:04d}-{m1:02d}-{d1:02d}至{y2:04d}-{m2:02d}-{d2:02d} {tz_label}"
                )
        return {
            "label": label,
            "start_ms": start_ms,
            "end_ms": end_ms,
            "tz": tz_label,
            "tz_key": tz_key,
            "start_date": f"{y1:04d}-{m1:02d}-{d1:02d}",
            "end_date": f"{y2:04d}-{m2:02d}-{d2:02d}",
            "inclusive_end_day": bool(inclusive in (True, "true", "1", 1, "yes")),
            "source": "calendar",
        }

    # Fallback: trust raw ms only when calendar missing
    try:
        start_ms = tw.get("start_ms")
        end_ms = tw.get("end_ms")
        if start_ms is None or end_ms is None:
            return None
        start_ms = int(start_ms)
        end_ms = int(end_ms)
        if end_ms <= start_ms:
            return None
    except (TypeError, ValueError):
        return None
    _, tz_label, tz_key = _tz_from_token(str(tw.get("tz") or "ET"))
    label = str(tw.get("label") or "").strip() or f"ms=[{start_ms},{end_ms})"
    return {
        "label": label,
        "start_ms": start_ms,
        "end_ms": end_ms,
        "tz": tz_label,
        "tz_key": tz_key,
        "source": "llm_ms_raw",
    }


def _normalize_time_window(
    obj: Any,
    *,
    now: datetime | None = None,
) -> dict[str, Any] | None:
    if not isinstance(obj, dict):
        return None
    return resolve_time_window_from_intent(obj, now=now)


def _normalize_metric_intents(raw: Any) -> list[MetricIntentItem]:
    if not isinstance(raw, list):
        return []
    out: list[MetricIntentItem] = []
    seen: set[str] = set()
    for item in raw:
        if isinstance(item, str):
            goal = item.strip()[:80]
            kind = "other"
            entity = ""
        elif isinstance(item, dict):
            goal = str(item.get("goal") or item.get("name") or item.get("metric") or "").strip()[:80]
            kind = str(item.get("kind") or "other").strip().lower()
            if kind not in _VALID_METRIC_KINDS:
                kind = "other"
            entity = str(item.get("entity_hint") or item.get("entity") or "").strip()[:64]
            resource_hint = str(item.get("resource_hint") or "").strip()[:120]
            # Strip accidental view/role hardcodes from entity_hint
            if re.search(r"view_result_|^(pay|cash|bet)$", entity, re.I):
                entity = ""
        else:
            continue
        if not goal or goal in seen:
            continue
        seen.add(goal)
        out.append(
            MetricIntentItem(
                goal=goal,
                kind=kind,
                entity_hint=entity,
                resource_hint=resource_hint if isinstance(item, dict) else "",
            )
        )
        if len(out) >= 12:
            break
    return out


def _normalize_metrics(raw: Any) -> list[str]:
    """Backward-compat string goals derived from metrics / metric_intents."""
    intents = _normalize_metric_intents(raw)
    return [m.goal for m in intents]


def parse_turn_intent_payload(
    data: dict[str, Any] | None,
    *,
    source: str = "llm",
    now: datetime | None = None,
) -> TurnIntent:
    if not isinstance(data, dict):
        return TurnIntent(source=source)
    intent = str(data.get("intent") or "").strip().lower()
    if intent not in _VALID_INTENTS:
        intent = "data_query"
    wants = data.get("wants_deliverable")
    if isinstance(wants, str):
        wants = wants.strip().lower() in ("1", "true", "yes", "y")
    else:
        wants = bool(wants)
    metric_src = data.get("metric_intents")
    if not isinstance(metric_src, list) or not metric_src:
        metric_src = data.get("metrics")
    metric_intents = _normalize_metric_intents(metric_src)
    task_relation = str(data.get("task_relation") or "unknown").strip().lower()
    if task_relation not in _VALID_TASK_RELATIONS:
        task_relation = "unknown"
    requested_artifacts = list(dict.fromkeys(
        str(x).strip().lower()
        for x in (data.get("requested_artifacts") or [])
        if str(x).strip().lower() in {"sql", "data_file", "methods"}
    ))
    conversation_action = str(
        data.get("conversation_action") or "respond"
    ).strip().lower()
    if conversation_action not in _VALID_CONVERSATION_ACTIONS:
        conversation_action = "respond"
    result_shape = str(data.get("result_shape") or "unspecified").strip().lower()
    if result_shape not in _VALID_RESULT_SHAPES:
        result_shape = "unspecified"
    temporal_grain = str(data.get("temporal_grain") or "unspecified").strip().lower()
    if temporal_grain not in _VALID_TEMPORAL_GRAINS:
        temporal_grain = "unspecified"
    return TurnIntent(
        intent=intent,
        reason=str(data.get("reason") or "").strip()[:300],
        wants_deliverable=wants,
        query_goal=str(data.get("query_goal") or "").strip()[:300],
        conversation_action=conversation_action,
        metrics=[m.goal for m in metric_intents],
        metric_intents=metric_intents,
        time_window=_normalize_time_window(data.get("time_window"), now=now),
        task_relation=task_relation,
        verification_required=bool(data.get("verification_required")),
        verification_targets=list(dict.fromkeys(
            str(x).strip()[:160]
            for x in (data.get("verification_targets") or [])
            if str(x).strip()
        ))[:12],
        requested_artifacts=requested_artifacts,
        result_shape=result_shape,
        temporal_grain=temporal_grain,
        raw=data,
        source=source,
    )


def looks_like_task_message(user_message: str) -> bool:
    text = (user_message or "").strip()
    if not text:
        return False
    if _TASKISH_RE.search(text):
        return True
    if _DIGIT_OR_DATE_RE.search(text) and len(text) > 8:
        return True
    return False


def should_force_chat_override(
    user_message: str,
    *,
    is_light_chat: bool,
    llm_intent: str,
) -> bool:
    """Only force chat for short pure greetings — never for task-like messages."""
    if llm_intent == "chat":
        return False
    if not is_light_chat:
        return False
    if looks_like_task_message(user_message):
        return False
    # Pure short greeting / capability only
    text = (user_message or "").strip()
    if len(text) > 40:
        return False
    if _DIGIT_OR_DATE_RE.search(text):
        return False
    return True


def should_force_tools_override(
    user_message: str,
    *,
    llm_intent: str,
) -> bool:
    """LLM said chat but message is clearly a task / rework → keep tool ring."""
    if llm_intent != "chat":
        return False
    return looks_like_task_message(user_message)


def _metric_intents_from_numbered_headers(headers: list[str]) -> list[MetricIntentItem]:
    out: list[MetricIntentItem] = []
    seen: set[str] = set()
    for h in headers or []:
        goal = str(h or "").strip()
        if not goal:
            continue
        key = goal.lower()
        if key in seen:
            continue
        seen.add(key)
        kind = "other"
        entity = ""
        if re.search(r"充值|支付|pay", goal, re.I):
            kind, entity = "sum", "payment"
        elif re.search(r"下注|投注|bet", goal, re.I):
            kind, entity = "sum", "bet_volume"
        elif re.search(r"返奖|返浆|派彩|win", goal, re.I):
            kind, entity = "sum", "bet_payout"
        elif re.search(r"用户\s*ID|uid", goal, re.I):
            kind, entity = "other", "user"
        out.append(MetricIntentItem(goal=goal, kind=kind, entity_hint=entity))
    return out


def fallback_turn_intent(
    user_message: str,
    *,
    is_light_chat: bool = False,
    skill_blob: str = "",
    now: datetime | None = None,
    default_timezone: str = "ET",
) -> TurnIntent:
    """No-LLM / parse-failure fallback: prefer tools over export hard-gates.

    Export-shaped briefs (xlsx + numbered cols) soft-map to export_report so
    Type-B / full_fetch / page-limit align still arm after non-JSON intent.
    """
    if is_light_chat and not looks_like_task_message(user_message):
        return TurnIntent(
            intent="chat",
            reason="fallback:light_chat",
            source="fallback",
        )
    text = (user_message or "").strip()
    if not text:
        return TurnIntent(intent="chat", reason="fallback:empty", source="fallback")
    is_export, export_reason = detect_export_report_task(text, skill_blob or "")
    headers = extract_numbered_column_headers(text)
    if is_export or (
        headers
        and len(headers) >= 4
        and re.search(r"导出|xlsx|excel|报表", text, re.I)
    ):
        intents = _metric_intents_from_numbered_headers(headers)
        metrics = [m.goal for m in intents]
        return TurnIntent(
            intent="export_report",
            reason=f"fallback:export_shape:{export_reason or 'numbered_cols'}",
            wants_deliverable=True,
            query_goal=(text[:200] if text else "导出分析报表"),
            metrics=metrics,
            metric_intents=intents,
            time_window=None,
            source="fallback",
        )
    now = now or datetime.now(timezone.utc)
    raw_window = _calendar_window_from_message(
        text, now=now, tz_key=default_timezone,
    )
    return TurnIntent(
        intent="data_query",
        reason="fallback:default_data_query",
        query_goal=text[:200],
        time_window=resolve_time_window_from_intent(raw_window, now=now),
        source="fallback",
    )


def soft_upgrade_export_intent(
    turn: TurnIntent,
    user_message: str,
    *,
    skill_blob: str = "",
) -> TurnIntent:
    """If routed intent missed export_report but brief is clearly export-shaped, soft-upgrade."""
    if turn is None or turn.is_export:
        return turn
    is_export, export_reason = detect_export_report_task(
        user_message or "", skill_blob or "",
    )
    headers = extract_numbered_column_headers(user_message or "")
    if not is_export and not (
        headers
        and len(headers) >= 4
        and re.search(r"导出|xlsx|excel|报表", user_message or "", re.I)
    ):
        return turn
    intents = list(turn.metric_intents or [])
    metrics = list(turn.metrics or [])
    if not intents and not metrics and headers:
        intents = _metric_intents_from_numbered_headers(headers)
        metrics = [m.goal for m in intents]
    return TurnIntent(
        intent="export_report",
        reason=f"soft_export_shape:{export_reason}:{turn.reason or turn.source}",
        wants_deliverable=True,
        query_goal=turn.query_goal or (user_message or "").strip()[:200],
        conversation_action=turn.conversation_action,
        metrics=metrics,
        metric_intents=intents,
        time_window=turn.time_window,
        task_relation=turn.task_relation,
        verification_required=turn.verification_required,
        verification_targets=list(turn.verification_targets),
        requested_artifacts=list(turn.requested_artifacts),
        result_shape=turn.result_shape,
        temporal_grain=turn.temporal_grain,
        raw=turn.raw,
        source=turn.source if turn.source == "llm" else "fallback",
    )


def task_policy_from_intent(turn: TurnIntent) -> TaskPolicy:
    if turn.is_export:
        return TaskPolicy(
            policy_id="export_report",
            export_like=True,
            plan_hint=EXPORT_PLAN_HINT,
            require_plan_gate=True,
            reason=f"llm_intent:{turn.intent}:{turn.reason or turn.source}",
        )
    return TaskPolicy(
        policy_id="generic",
        export_like=False,
        plan_hint=GENERIC_PLAN_HINT,
        require_plan_gate=True,
        reason=f"llm_intent:{turn.intent}:{turn.reason or turn.source}",
    )


def should_hard_stop_task_spec(validation_status: str | None) -> bool:
    """Static TaskSpec gates must not abort the turn (clarification or error)."""
    del validation_status
    return False


def _validate_intent_payload(
    data: dict[str, Any] | None,
    *,
    require_task_relation: bool = False,
    require_decided_relation: bool = False,
) -> str | None:
    """Strict schema gate before soft parse. Return error reason or None if ok."""
    if not isinstance(data, dict) or not data:
        return "empty_or_non_object"
    intent = str(data.get("intent") or "").strip()
    if intent not in _VALID_INTENTS:
        return f"invalid_intent:{intent or '<missing>'}"
    reason = str(data.get("reason") or "").strip()
    if not reason:
        return "missing_reason"
    wd = data.get("wants_deliverable")
    if not isinstance(wd, bool):
        return "wants_deliverable_not_bool"
    relation = str(data.get("task_relation") or "").strip().lower()
    if require_task_relation and relation not in _VALID_TASK_RELATIONS:
        return "missing_or_invalid_task_relation"
    if require_decided_relation and relation == "unknown":
        return "undecided_task_relation"
    if "verification_required" in data and not isinstance(data.get("verification_required"), bool):
        return "verification_required_not_bool"
    targets = data.get("verification_targets")
    if "verification_targets" in data and not isinstance(targets, list):
        return "verification_targets_not_list"
    artifacts = data.get("requested_artifacts")
    if "requested_artifacts" in data and not isinstance(artifacts, list):
        return "requested_artifacts_not_list"
    result_shape = str(data.get("result_shape") or "unspecified").strip().lower()
    if result_shape not in _VALID_RESULT_SHAPES:
        return "invalid_result_shape"
    temporal_grain = str(data.get("temporal_grain") or "unspecified").strip().lower()
    if temporal_grain not in _VALID_TEMPORAL_GRAINS:
        return "invalid_temporal_grain"
    conversation_action = str(data.get("conversation_action") or "respond").strip().lower()
    if intent == "chat" and "conversation_action" not in data:
        return "missing_conversation_action"
    if conversation_action not in _VALID_CONVERSATION_ACTIONS:
        return "invalid_conversation_action"
    if conversation_action == "summarize_session" and intent != "chat":
        return "session_summary_not_chat"
    return None


async def analyze_turn_intent(
    llm,
    user_message: str,
    *,
    has_export_skill: bool = False,
    skill_blob: str = "",
    db=None,
    is_light_chat: bool = False,
    timeout: int = 45,
    now: datetime | None = None,
    agent_id: str = "",
    session_id: str = "",
    max_attempts: int = 3,
    context_note: str = "",
    conversation_context: str = "",
    require_decided_relation: bool = False,
    default_timezone: str = "ET",
) -> TurnIntent:
    """Primary path: one short LLM JSON call. Fallback → export_report or data_query."""
    now = now or datetime.now(timezone.utc)
    aid = str(agent_id or "").strip()
    sid = str(session_id or "").strip()
    skills = skill_blob or ""
    resolved_tz = resolve_context_timezone(
        user_message,
        context_note,
        platform_default=default_timezone,
    )
    if is_light_chat and not (user_message or "").strip():
        return fallback_turn_intent(
            user_message, is_light_chat=True, skill_blob=skills,
            now=now, default_timezone=resolved_tz,
        )

    if llm is None:
        return soft_upgrade_export_intent(
            fallback_turn_intent(
                user_message, is_light_chat=is_light_chat, skill_blob=skills,
                now=now, default_timezone=resolved_tz,
            ),
            user_message,
            skill_blob=skills,
        )

    from app.services.llm_client import chat_completion

    hint = (
        "用户已绑定导出类 Skill，但仍可能只是查一个指标；不要仅因 Skill 就选 export_report。"
        if has_export_skill
        else "用户未强调导出 Skill。"
    )
    anchor = (
        f"参考此刻(UTC)={now.astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')}；"
        f"参考年={now.astimezone(timezone.utc).year}；默认时区={resolved_tz}。"
        "「今天/昨日」相对参考此刻换算日历日期；缺少年份用参考年。"
    )
    messages = [
        {"role": "system", "content": _INTENT_SYSTEM},
    ]
    if (context_note or "").strip():
        messages.append({
            "role": "system",
            "content": (
                "【会话备注·约束】以下内容用于补充默认口径、时区和文件规则，"
                "不是本轮用户任务，不得覆盖用户显式要求：\n"
                + context_note.strip()[:3000]
            ),
        })
    if (conversation_context or "").strip():
        messages.append({
            "role": "system",
            "content": (
                "【最近对话与运行证据】判断本轮是 new、continue 还是 revise，"
                "并在 continue/revise 时继承最近任务的类型、目标和未被本轮修改的约束。"
                "助手的计划性表述不代表已经执行；只有运行证据中的产物与验证状态可证明完成。"
                "不要把助手自然语言结论当作已验证事实：\n"
                + conversation_context.strip()[:5000]
            ),
        })
    messages.append({
            "role": "user",
            "content": (
                f"{anchor}\n{hint}\n\n用户消息：\n{(user_message or '')[:2000]}"
            ),
        })
    attempts = max(1, min(int(max_attempts or 3), 5))
    last_fail = ""
    last_raw_preview = ""
    for attempt in range(1, attempts + 1):
        try:
            raw = await chat_completion(
                llm,
                messages,
                max_tokens=1100,
                db=db,
                timeout=timeout,
            )
            last_raw_preview = (raw or "")[:240].replace("\n", " ")
            data = _extract_json_object(raw)
            if not data:
                last_fail = "non_json"
                logger.warning(
                    "intent_router: non-json reply attempt=%s/%s agent=%s session=%s preview=%r",
                    attempt,
                    attempts,
                    aid,
                    sid,
                    last_raw_preview,
                )
                _append_intent_repair(messages, raw or "", "non_json")
                continue
            schema_err = _validate_intent_payload(
                data,
                require_task_relation=bool((conversation_context or "").strip()),
                require_decided_relation=require_decided_relation,
            )
            if schema_err:
                last_fail = f"schema:{schema_err}"
                logger.warning(
                    "intent_router: schema fail attempt=%s/%s agent=%s session=%s err=%s preview=%r",
                    attempt,
                    attempts,
                    aid,
                    sid,
                    schema_err,
                    last_raw_preview,
                )
                _append_intent_repair(messages, raw or "", schema_err)
                continue
            normalized_data = dict(data)
            raw_tw = normalized_data.get("time_window")
            if isinstance(raw_tw, dict):
                raw_tw = dict(raw_tw)
                raw_tw["tz"] = resolved_tz
                raw_tw["label"] = ""
                normalized_data["time_window"] = raw_tw
            elif raw_tw is None:
                inferred = _calendar_window_from_message(
                    user_message, now=now, tz_key=resolved_tz,
                )
                if inferred:
                    normalized_data["time_window"] = inferred
            turn = parse_turn_intent_payload(normalized_data, source="llm", now=now)
            if turn.wants_session_summary:
                return turn
            if should_force_chat_override(
                user_message,
                is_light_chat=is_light_chat,
                llm_intent=turn.intent,
            ):
                return fallback_turn_intent(
                    user_message, is_light_chat=True, skill_blob=skills,
                    now=now, default_timezone=resolved_tz,
                )
            if should_force_tools_override(user_message, llm_intent=turn.intent):
                return TurnIntent(
                    intent="other_tools",
                    reason=f"override:taskish_not_chat:{turn.reason}",
                    wants_deliverable=False,
                    query_goal=turn.query_goal or (user_message or "").strip()[:200],
                    conversation_action=turn.conversation_action,
                    metrics=list(turn.metrics or []),
                    metric_intents=list(turn.metric_intents or []),
                    time_window=turn.time_window,
                    task_relation=turn.task_relation,
                    verification_required=turn.verification_required,
                    verification_targets=list(turn.verification_targets),
                    requested_artifacts=list(turn.requested_artifacts),
                    result_shape=turn.result_shape,
                    temporal_grain=turn.temporal_grain,
                    raw=turn.raw,
                    source="llm",
                )
            return soft_upgrade_export_intent(
                turn, user_message, skill_blob=skills,
            )
        except Exception:
            last_fail = "exception"
            logger.exception(
                "intent_router LLM failed attempt=%s/%s agent=%s session=%s",
                attempt,
                attempts,
                aid,
                sid,
            )
    logger.error(
        "intent_router: fallback after %s attempts reason=%s agent=%s session=%s preview=%r",
        attempts,
        last_fail or "unknown",
        aid,
        sid,
        last_raw_preview,
    )
    return soft_upgrade_export_intent(
        fallback_turn_intent(
            user_message, is_light_chat=is_light_chat, skill_blob=skills,
            now=now, default_timezone=resolved_tz,
        ),
        user_message,
        skill_blob=skills,
    )


def compute_data_query_gaps(
    turn: TurnIntent | None,
    *,
    user_message: str = "",
    bindings: list[Any] | None = None,
) -> list[str]:
    """Return gap ids for need-based data_query: time_window | metrics | resource.

    Chat/export are out of scope (empty). Does not pull data — only diagnoses.
    """
    if turn is None or turn.is_chat or turn.is_export:
        return []
    # Only gate data_query (and toolful turns that look like metrics)
    text = (user_message or "").strip()
    gaps: list[str] = []
    has_metrics = bool(turn.metrics or turn.metric_intents)
    has_goal = bool((turn.query_goal or "").strip())
    if turn.intent == "data_query" and not has_metrics and not has_goal:
        gaps.append("metrics")
    tw = turn.time_window if isinstance(turn.time_window, dict) else None
    has_tw = bool(
        tw
        and tw.get("start_ms") is not None
        and tw.get("end_ms") is not None
    )
    needs_time = False
    if turn.intent == "data_query" and (has_metrics or has_goal):
        blob = " ".join(
            [turn.query_goal or "", text]
            + list(turn.metrics or [])
            + [m.goal for m in (turn.metric_intents or [])]
        )
        # Count/sum style asks need a window; explicit date words also require resolved tw
        if _METRICISH_RE.search(blob) or _TIMEISH_RE.search(blob) or _DIGIT_OR_DATE_RE.search(blob):
            needs_time = True
    if needs_time and not has_tw:
        gaps.append("time_window")
    if bindings is not None:
        for b in bindings:
            resource = str(getattr(b, "resource", None) or (b.get("resource") if isinstance(b, dict) else "") or "").strip()
            try:
                conf = float(
                    getattr(b, "confidence", None)
                    if not isinstance(b, dict)
                    else b.get("confidence", 0.5)
                )
            except (TypeError, ValueError):
                conf = 0.5
            if not resource and conf < 0.55:
                if "resource" not in gaps:
                    gaps.append("resource")
                break
    return gaps


def format_time_window_system_hint(
    tw: dict[str, Any] | None,
    *,
    query_goal: str = "",
    metrics: list[str] | None = None,
    metric_intents: list[MetricIntentItem] | None = None,
    result_shape: str = "unspecified",
    temporal_grain: str = "unspecified",
) -> str:
    parts: list[str] = []
    goal = (query_goal or "").strip()
    intent_items = list(metric_intents or [])
    mets = [str(m).strip() for m in (metrics or []) if str(m).strip()]
    if not mets and intent_items:
        mets = [m.goal for m in intent_items if m.goal]
    if goal or mets or intent_items:
        line = "【意图查数·LLM】"
        if goal:
            line += f"目标：{goal}。"
        if intent_items:
            bits = []
            for mi in intent_items[:8]:
                bit = mi.goal
                if mi.kind and mi.kind != "other":
                    bit += f"({mi.kind})"
                if mi.entity_hint:
                    bit += f"/{mi.entity_hint}"
                bits.append(bit)
            line += "指标：" + "、".join(bits) + "。"
        elif mets:
            line += "指标：" + "、".join(mets) + "。"
        line += "资源名须由 MCP list/describe 绑定，禁止臆造视图。"
        parts.append(line)
    shape = str(result_shape or "unspecified").strip().lower()
    grain = str(temporal_grain or "unspecified").strip().lower()
    if shape != "unspecified":
        shape_line = f"【结果形态·LLM】result_shape={shape}"
        if grain != "unspecified":
            shape_line += f"；temporal_grain={grain}"
        shape_line += "。"
        if shape == "time_series":
            shape_line += (
                "查询须选择时间维度并按该粒度聚合、升序返回 time_window 内完整序列；"
                "最终使用 Markdown 表格逐期列出全部结果，不得只输出区间总计或仅口述概况。"
            )
        elif shape == "detail_list":
            shape_line += (
                "查询并列出时间窗内全部匹配明细；不得用单行聚合代替明细列表。"
            )
        elif shape == "mixed":
            shape_line += "先完整列出序列/明细，再给区间汇总。"
        elif shape in ("summary", "single_value"):
            shape_line += "按用户要求输出区间汇总，不额外扩展为明细导出。"
        parts.append(shape_line)
    if isinstance(tw, dict):
        label = tw.get("label") or ""
        start_ms = tw.get("start_ms")
        end_ms = tw.get("end_ms")
        tz = tw.get("tz") or ""
        start_date = tw.get("start_date") or ""
        end_date = tw.get("end_date") or ""
        if start_ms is not None and end_ms is not None:
            cal = ""
            if start_date or end_date:
                cal = f"日历 {start_date or '?'}～{end_date or start_date or '?'}；"
            parts.append(
                "【意图时间窗·引擎换算】"
                f"{label}；{cal}tz={tz}；毫秒半开区间 start_ms={start_ms} end_ms={end_ms}。"
                "写 MCP sql/where 时优先使用上述毫秒边界（Int64）；"
                "列表型 MCP 若不支持日期参数，必须按 cursor 连续翻页，并在本地按资源时间字段"
                "（默认 created_at/create_time）过滤该半开区间；直到越过起点或 has_more=false。"
                "除非用户明确要求按内容发生时间归档，禁止用标题、正文或活动日期替换创建时间。"
                "完成前校验每条入选记录都在时间窗内，并说明命中总数；"
                "口述结论日期必须与日历字段一致；"
                "查询结果 FINAL 用 Markdown「### 查询结果」表格。"
            )
    return "\n".join(parts)


# ---- Bound-MCP "unavailable tools" excuse (LLM intent, no regex) ----

_MCP_EXCUSE_SOFT_REJECT_MAX = 2

UNAVAILABLE_DATA_TOOLS_COACH = (
    "【工具软提示】本 Agent 已绑定 MCP，请优先 "
    "`MCP: list_ads_views {}` → `describe_ads_view` → `query_ads_view` 拉数；"
    "勿以无 ClickHouse / 无 ads-sync-hub / 沙箱无客户端为由只给散文收工。"
    "此为软教练，不阻止后续 FINAL。"
)

_UNAVAILABLE_TOOLS_SYSTEM = """你是 GAP 平台的短判定器。只输出一个 JSON 对象，不要 Markdown，不要解释。

字段：
- claims_unavailable_data_tools: boolean
- reason: 一句中文理由

为 true 当且仅当：助手回复在主张「环境无法连库 / 没有 MCP / 没有 ads-sync-hub / 没有 clickhouse-client / 没有数据库工具，因此不能查数或只能给 SQL 散文」，且并非在报告一次真实的 MCP 工具调用失败（例如已执行 MCP 后返回的错误）。

为 false：正常结论、澄清需求、已用工具后的总结、或与「环境无数据工具」无关的内容。
"""


def append_unavailable_tools_soft_nudge(text: str) -> str:
    """Append soft coach once; never hard-blocks FINAL."""
    body = (text or "").rstrip()
    if "工具软提示" in body and "list_ads_views" in body:
        return body
    if not body:
        return UNAVAILABLE_DATA_TOOLS_COACH.strip()
    return body + "\n\n" + UNAVAILABLE_DATA_TOOLS_COACH


async def classify_unavailable_data_tools_claim(
    llm,
    assistant_text: str,
    *,
    db=None,
    timeout: int = 30,
) -> bool | None:
    """LLM JSON: claims_unavailable_data_tools.

    Returns True/False on success; None on failure (fail-open: caller must not soft-reject).
    No regex fallback.
    """
    if llm is None:
        return None
    preview = (assistant_text or "").strip()
    if not preview:
        return None
    preview = preview[:800]
    from app.services.llm_client import chat_completion

    messages = [
        {"role": "system", "content": _UNAVAILABLE_TOOLS_SYSTEM},
        {
            "role": "user",
            "content": (
                "上下文：本 Agent 已绑定 MCP。\n\n助手回复：\n" + preview
            ),
        },
    ]
    try:
        raw = await chat_completion(
            llm,
            messages,
            max_tokens=120,
            db=db,
            timeout=timeout,
        )
    except Exception:
        logger.exception("classify_unavailable_data_tools_claim: llm failed")
        return None
    data = _extract_json_object(raw)
    if not isinstance(data, dict):
        logger.warning(
            "classify_unavailable_data_tools_claim: non-json preview=%r",
            (raw or "")[:160],
        )
        return None
    if "claims_unavailable_data_tools" not in data:
        return None
    val = data.get("claims_unavailable_data_tools")
    if isinstance(val, bool):
        return val
    if isinstance(val, str):
        low = val.strip().lower()
        if low in ("true", "1", "yes"):
            return True
        if low in ("false", "0", "no"):
            return False
    return None


async def maybe_soft_reject_unavailable_tools_finish(
    *,
    llm,
    assistant_text: str,
    has_mcp: bool,
    ran_any_tool: bool,
    soft_reject_count: int,
    db=None,
    timeout: int = 30,
    max_rejects: int = _MCP_EXCUSE_SOFT_REJECT_MAX,
) -> tuple[str, int, bool]:
    """Decide soft-reject vs allow for bound-MCP excuse FINISH.

    Returns (action, new_count, claim_true) where action is:
      - "reject": soft-reject FINISH (inject coach)
      - "append": allow finish but append soft nudge (limit reached + claim)
      - "allow": allow finish as-is (no claim / fail-open / already used tools)
    """
    if not has_mcp or ran_any_tool:
        return "allow", soft_reject_count, False
    claim = await classify_unavailable_data_tools_claim(
        llm, assistant_text, db=db, timeout=timeout,
    )
    if claim is not True:
        return "allow", soft_reject_count, False
    if soft_reject_count < max(1, int(max_rejects or _MCP_EXCUSE_SOFT_REJECT_MAX)):
        return "reject", soft_reject_count + 1, True
    return "append", soft_reject_count, True
