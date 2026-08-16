"""Task policies for ReAct: thin generic loop vs specialized export path.

Domain semantics belong in Skill/PLAN; the engine only selects a policy and
enforces structure (PLAN + completion criteria). Export hard-coding stays in
ExportReportPolicy / react_engine adapters — not the default for every task.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# Strong export-report signals (analytical join / multi-fact register report)
_EXPORT_STRONG_RE = re.compile(
    r"新增注册|流水倍数|总充值|总提现|总下注|是否有退款|SC投注|"
    r"注册渠道|连续充值|是否被封禁|pay.?order|user_bet|cash_order",
    re.I,
)
_EXPORT_DELIVERABLE_RE = re.compile(r"导出|报表|xlsx|excel|落盘", re.I)
# Alone these used to force the full export state machine — too broad.
_EXPORT_WEAK_ONLY_RE = re.compile(r"明细|全量|用户数据", re.I)
_NUMBERED_COL_RE = re.compile(
    r"(?:^|\n)\s*(?:\d+[.)、]|[-*•])\s*(.+?)(?=\n|$)",
    re.M,
)
_PLAN_BLOCK_RE = re.compile(r"PLAN\s*[:：]", re.I)
_COMPLETION_LINE_RE = re.compile(
    r"(?:完成标准|验收|done\s*when|success)\s*[:：]?\s*(.+)",
    re.I,
)
_VIEW_NAME_RE = re.compile(r"(?:ads\.)?(view_result_[a-z0-9_]+)", re.I)
_AGG_VIEW_RE = re.compile(r"(?:stat|everyday|daily|summary|agg|看板)", re.I)
_PIN_ONLY_HINT_RE = re.compile(
    r"仅(?:通过|用|查|使用)|只要(?:该|此)?视图|通过该视图|不要(?:再)?(?:查询|拉取|拉)|"
    r"勿(?:再)?(?:查询|拉)|禁止.*(?:user_info|pay_order|cash_order|user_bet)",
    re.I,
)

GENERIC_PLAN_HINT = (
    "【通用·规划闸门】请先输出 PLAN（本轮不要调用工具），格式：\n"
    "PLAN:\n"
    "- 目标:\n"
    "- 步骤:（按序，可引用已绑定 Skill / MCP 工具）\n"
    "- 数据/工具:（需要哪些 MCP/视图/文件；无则写「无」）\n"
    "- 完成标准:（可检查的条目，如「写出 xlsx」「回答含 N」「覆盖字段…」）\n"
    "- 工具预算: 最多 N 次工具调用（建议 3–12）\n"
    "平台会按完成标准对照进度；完成后再 FINAL:。\n"
    "【SQL/MCP】若用户要写 SQL：PLAN 或下一步 FINAL 中给出可复制完整 SQL；"
    "若用户要跑数：list_ads_views → describe_ads_view(view_name) → "
    "query_ads_view(view=…, sql/where)；禁止空参数 {}；"
    "字段名是 view 不是 view_name；where 必须是对象（勿把字符串当 Int64）。"
    "查询结果 FINAL 默认用 Markdown 表格美化（### 查询结果 + GFM 表），勿只堆 JSON/散文。"
)

EXPORT_PLAN_HINT = (
    "【导出·规划闸门】schema/样例已就绪。本轮只输出 PLAN（不要调用工具），格式：\n"
    "PLAN:\n"
    "- 目标:\n"
    "- 任务类型: A-轻量身份 | B-多事实 | C-点名单视图\n"
    "- 数据源/视图:（来自 MCP list/describe，勿臆造）\n"
    "- 字段与筛选:（含时间窗；user_info 用 sql 过滤 register_time）\n"
    "- 输出列:（逐条抄写用户编号列的完整表述）\n"
    "- 需要资源:（按输出列推导的 MCP resource/视图；类型 C 点名 view 时写「无」；"
    "仅身份字段可只写一个身份 resource；禁止默认全量拉取所有视图）\n"
    "- 计算与关联:\n"
    "- 分页预算: 最多 N 次 query（1≤N≤14）\n"
    "- 分析/落盘步骤:\n"
    "- 完成标准:\n"
    "平台会限量拉取并自动写入 task/page_N.json；禁止把原始 MCP 页当最终报表；"
    "拉数后必须完成分析并落盘；勿 WRITE 二进制 xlsx。\n"
    "用户已点名 view_result_* 时属类型 C：只需 query 该视图，勿默认拉取全量明细。"
)


@dataclass
class TaskPolicy:
    """Selected orchestration policy for one user turn."""

    policy_id: str  # "export_report" | "generic"
    export_like: bool
    plan_hint: str
    require_plan_gate: bool = True
    reason: str = ""

    def tools_strategy_blurb(self) -> str:
        if self.export_like:
            return (
                "【导出策略·强制阶段】\n"
                "1) 发现：list_* / describe_*，最多 1 次样例 query；\n"
                "2) 必须输出 PLAN:（目标/视图/筛选/输出列/需要资源/预算/SHELL 步骤）；\n"
                "3) 限量 fetch：仅拉列意图/白名单资源，平台写入 task/page_N.json；\n"
                "4) 分析：禁止再 MCP；SHELL 写当前目录交付物；\n"
                "5) FINAL。依赖以 PLAN「需要资源」+ 列计划为准，禁止默认全量拉取。\n"
                "禁止无限分页；勿在 FINAL 粘贴数据表。"
            )
        return (
            "【通用策略】先 PLAN（目标/步骤/完成标准/工具预算）；"
            "再按步骤调用工具；对照完成标准后 FINAL。"
            "优先阅读已绑定 Skill；不要为未声明的依赖强行拉全量明细。\n"
            "【SQL/MCP】写 SQL：尽快给出可复制完整 SQL；"
            "跑数：list_ads_views → describe_ads_view(view_name) → "
            "query_ads_view(view=真实视图名)；禁止空 {}；"
            "where 用对象；时间戳注意毫秒 Int64；"
            "失败两次后应改参或 FINAL 交付已确认的 SQL，勿同参空转。"
            "查询结果默认 Markdown 表格呈现（### 查询结果），便于前端预览美化。"
        )


def _count_numbered_cols(text: str) -> int:
    n = 0
    for m in _NUMBERED_COL_RE.finditer(text or ""):
        line = (m.group(1) or "").strip()
        if len(line) >= 2:
            n += 1
    return n


def extract_numbered_column_headers(text: str, *, max_cols: int = 32) -> list[str]:
    """Soft-extract numbered column headers from user brief (no hexad invent)."""
    out: list[str] = []
    seen: set[str] = set()
    for m in _NUMBERED_COL_RE.finditer(text or ""):
        line = re.sub(r"\s+", " ", (m.group(1) or "").strip())
        if len(line) < 2 or len(line) > 80:
            continue
        if line.startswith((
            "目标", "数据源", "字段", "输出列", "计算", "分页", "SHELL", "完成标准",
            "SOP", "步骤", "需要资源", "依赖", "任务类型", "工具预算",
        )):
            continue
        if re.match(
            r"^(?:READ|WRITE|SHELL|MCP|HTTPMCP|FINAL|PATCH|THINK)\s*:",
            line,
            re.I,
        ):
            continue
        key = line.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(line)
        if len(out) >= max(1, int(max_cols)):
            break
    return out


_LIGHT_IDENTITY_RE = re.compile(
    r"姓|名|first_?name|last_?name|手机|phone|邮箱|email|身份证",
    re.I,
)
_ANALYTICAL_COL_RE = re.compile(
    r"充值|提现|下注|投注|流水|退款|渠道|游戏|余额|返奖|封禁",
    re.I,
)


def _is_light_identity_export(text: str) -> bool:
    """用户ID/姓/名类轻量导出 — 不得吸入全量分析报表路径。"""
    t = text or ""
    if not _LIGHT_IDENTITY_RE.search(t):
        return False
    if _ANALYTICAL_COL_RE.search(t) or _EXPORT_STRONG_RE.search(t):
        return False
    # Prefer when deliverable/export wording present or few numbered cols
    cols = _count_numbered_cols(t)
    if cols and cols <= 5:
        return True
    if _EXPORT_DELIVERABLE_RE.search(t) or _EXPORT_WEAK_ONLY_RE.search(t):
        return True
    return bool(re.search(r"用户ID|uid", t, re.I))


def detect_export_report_task(user_message: str, skill_blob: str = "") -> tuple[bool, str]:
    """True only for analytical multi-fact export reports — not every 导出/明细.

    Evidence: run 1785421901733 (姓/名 columns) was sucked into the full export
    state machine by weak keywords like 用户数据 — that path is rejected here.
    """
    text = user_message or ""
    skills = skill_blob or ""
    if _is_light_identity_export(text):
        return False, "light_identity"
    strong = bool(_EXPORT_STRONG_RE.search(text))
    deliverable = bool(_EXPORT_DELIVERABLE_RE.search(text))
    cols = _count_numbered_cols(text)
    skill_export = bool(
        re.search(r"export.?report|用户报表|注册用户.*导出|分析列", skills, re.I)
    )

    if strong and (deliverable or cols >= 4):
        return True, "strong_export_signals"
    # Do NOT treat (export skill + strong keyword like 新增注册) alone as export —
    # that sucked single-metric 查数 into TaskSpec. Prefer LLM intent_router.
    if skill_export and deliverable and cols >= 4 and not _is_light_identity_export(text):
        return True, "skill_export_report"
    if deliverable and cols >= 8:
        return True, "many_numbered_columns"
    if deliverable and strong:
        return True, "deliverable_plus_strong"
    if re.search(r"新增注册.*(?:用户|导出|报表)", text) and deliverable:
        return True, "new_register_export"
    return False, "generic"


def detect_task_policy(
    user_message: str,
    *,
    skill_blob: str = "",
) -> TaskPolicy:
    is_export, reason = detect_export_report_task(user_message, skill_blob)
    if is_export:
        return TaskPolicy(
            policy_id="export_report",
            export_like=True,
            plan_hint=EXPORT_PLAN_HINT,
            require_plan_gate=True,
            reason=reason,
        )
    return TaskPolicy(
        policy_id="generic",
        export_like=False,
        plan_hint=GENERIC_PLAN_HINT,
        require_plan_gate=True,
        reason=reason or "generic",
    )


def extract_pinned_views(*texts: str) -> list[str]:
    """Unique view_result_* names mentioned in user/PLAN text (order preserved)."""
    out: list[str] = []
    seen: set[str] = set()
    for text in texts:
        for m in _VIEW_NAME_RE.finditer(text or ""):
            name = (m.group(1) or "").strip().lower()
            if name and name not in seen:
                seen.add(name)
                out.append(name)
    return out


def strip_view_tokens(text: str) -> str:
    """Remove view_result_* tokens so role heuristics do not match inside names."""
    return _VIEW_NAME_RE.sub(" ", text or "")


def plan_declares_fact_views(plan_text: str) -> list[str]:
    """Explicit resource views from PLAN that imply a joined export."""
    m = re.search(
        r"(?:需要资源|resources?)\s*[:：]\s*([^\n]+)",
        plan_text or "",
        re.I,
    )
    if not m:
        return []
    line = (m.group(1) or "").strip()
    if re.match(r"^(无|没有|none|n/?a|-)$", line, re.I):
        return []
    return [v.lower() for v in _VIEW_NAME_RE.findall(line)]


@dataclass
class ViewIntentRoute:
    """Export view-fetch routing for one turn."""

    mode: str  # single_view | multi_fact | light_identity | ambiguous
    pinned_views: list[str] = field(default_factory=list)
    reason: str = ""


def route_export_view_intent(
    user_message: str,
    plan_text: str = "",
) -> ViewIntentRoute:
    """Rule-based view intent: single_view vs multi_fact vs light_identity."""
    pinned = extract_pinned_views(user_message, plan_text)
    fact_from_plan = plan_declares_fact_views(plan_text)

    # Explicit resource views in PLAN upgrade to multi-fact wins over pin-only.
    if fact_from_plan:
        return ViewIntentRoute(
            mode="multi_fact",
            pinned_views=[],
            reason="plan_declares_fact_views",
        )

    if _is_light_identity_export(user_message):
        return ViewIntentRoute(
            mode="light_identity",
            pinned_views=[],
            reason="light_identity",
        )

    pin_hint = bool(_PIN_ONLY_HINT_RE.search(user_message or ""))
    agg_pinned = any(_AGG_VIEW_RE.search(v) for v in pinned)
    stripped = strip_view_tokens(user_message or "")
    strong_cols = bool(
        _EXPORT_STRONG_RE.search(stripped) and _count_numbered_cols(user_message) >= 6
    )

    # Named view(s) without PLAN fact roles → single_view (Type C)
    if pinned:
        if pin_hint or agg_pinned or len(pinned) == 1 or not strong_cols:
            reason = (
                "pinned_agg"
                if agg_pinned
                else ("pinned_only_hint" if pin_hint else "pinned_named_view")
            )
            return ViewIntentRoute(
                mode="single_view",
                pinned_views=pinned,
                reason=reason,
            )

    if strong_cols and not pinned:
        return ViewIntentRoute(
            mode="multi_fact",
            pinned_views=[],
            reason="strong_multi_fact",
        )

    if _EXPORT_STRONG_RE.search(stripped) and not pinned:
        return ViewIntentRoute(
            mode="multi_fact",
            pinned_views=[],
            reason="strong_multi_fact",
        )

    # Ambiguous: export-like but unclear — do not invent roles from keywords
    return ViewIntentRoute(
        mode="ambiguous",
        pinned_views=list(pinned),
        reason="ambiguous",
    )


def parse_generic_plan(reply: str) -> dict | None:
    """Return {budget, completion_lines, raw} if reply looks like an accepted PLAN."""
    if not reply or not _PLAN_BLOCK_RE.search(reply):
        return None
    budget = 8
    m_bud = re.search(
        r"(?:工具预算|预算|最多)\s*[:：]?\s*(?:最多\s*)?(\d+)\s*次",
        reply,
        re.I,
    )
    if m_bud:
        try:
            budget = max(2, min(20, int(m_bud.group(1))))
        except ValueError:
            budget = 8
    completion: list[str] = []
    for line in (reply or "").splitlines():
        cm = _COMPLETION_LINE_RE.search(line)
        if cm:
            item = cm.group(1).strip().strip("-•* ")
            if item:
                completion.append(item[:200])
        elif re.match(r"\s*[-*•]\s+.+(完成|产出|写出|回答|包含)", line):
            # bullet under 完成标准 section — keep short list
            if "完成标准" in reply[: reply.find(line) + 1] or completion:
                t = re.sub(r"^\s*[-*•]\s+", "", line).strip()
                if t and t not in completion:
                    completion.append(t[:200])
    if not completion:
        # Accept PLAN even without explicit criteria; synthesize from 目标
        m_goal = re.search(r"目标\s*[:：]\s*(.+)", reply)
        if m_goal:
            completion.append(m_goal.group(1).strip()[:200])
        else:
            completion.append("完成用户请求并 FINAL")
    return {"budget": budget, "completion_lines": completion, "raw": reply}


def build_generic_todos(completion_lines: list[str]) -> list[dict]:
    todos = [
        {"id": "phase_plan", "text": "PLAN已确认", "done": True, "phase": "plan"},
    ]
    for i, line in enumerate(completion_lines[:12]):
        todos.append({
            "id": f"crit_{i}",
            "text": line,
            "done": False,
            "phase": "act",
        })
    todos.append({"id": "phase_final", "text": "FINAL已输出", "done": False, "phase": "finalize"})
    return todos


def unfinished_act_todos(todos: list[dict]) -> list[str]:
    return [
        str(t.get("text") or "")
        for t in todos
        if t.get("phase") == "act" and not t.get("done") and str(t.get("text") or "").strip()
    ]


def mark_act_todos_from_evidence(
    todos: list[dict],
    *,
    final_text: str = "",
    saved_paths: list[str] | None = None,
) -> None:
    """Best-effort mark completion criteria done from FINAL text / files."""
    blob = (final_text or "").lower()
    paths = " ".join(saved_paths or []).lower()
    combined = f"{blob}\n{paths}"
    for t in todos:
        if t.get("phase") != "act" or t.get("done"):
            continue
        text = str(t.get("text") or "")
        low = text.lower()
        hit = False
        if re.search(r"xlsx|excel|报表|文件", low) and (
            ".xlsx" in combined or ".csv" in combined or "文件" in blob
        ):
            hit = True
        elif any(tok in combined for tok in re.findall(r"[\u4e00-\u9fffA-Za-z0-9_]{2,}", text)[:4]):
            # weak: any significant token from criterion appears in FINAL/paths
            hit = True
        if hit:
            t["done"] = True


def skill_snapshot_for_prompt(skill_mds: list[tuple[str, str]], *, max_chars: int = 4000) -> str:
    """Compact Skill injection (OpenClaw-style skills snapshot)."""
    if not skill_mds:
        return ""
    parts: list[str] = [
        "【已绑定 Skill 摘要】执行前优先遵循下列领域规则；细节可用 SKILL_MD: <id> 再读全文。"
        "导出类请遵守：输出列只写表头；READ/MCP/SHELL 写在步骤；轻量姓/名只要 user；"
        "分析表须由 SHELL 写中文表头，勿把英文原始宽表当分析交付。",
    ]
    used = 0
    # Prefer export-report / ads-sync so pull-order SOP survives truncation
    ordered = sorted(
        skill_mds,
        key=lambda nm: (
            0
            if "export-report" in (nm[0] or "").lower()
            or "ads-sync" in (nm[0] or "").lower()
            else 1,
            nm[0] or "",
        ),
    )
    for name, md in ordered:
        body = (md or "").strip()
        if not body:
            continue
        # Drop YAML frontmatter for prompt noise
        if body.startswith("---"):
            end = body.find("\n---", 3)
            if end != -1:
                body = body[end + 4 :].strip()
        # Keep headings + hard rules + pull-order / MCP SOP lines
        lines = []
        for ln in body.splitlines():
            if ln.startswith("#") or ln.startswith("|") or "禁止" in ln or "必须" in ln:
                lines.append(ln)
            elif (
                "类型 A" in ln or "类型 B" in ln or "类型 C" in ln
                or "输出列" in ln or "轻量" in ln or "点名单视图" in ln
                or "拉取顺序" in ln or "fact_starved" in ln
                or "view_result_" in ln or "query_ads" in ln
                or "SHELL" in ln or "to_excel" in ln
            ):
                lines.append(ln)
            elif ln.strip().startswith(("1.", "2.", "3.", "-", "*")) and len(lines) < 50:
                lines.append(ln)
            if len(lines) >= 48:
                break
        chunk = f"### Skill: {name}\n" + "\n".join(lines[:48])
        if used + len(chunk) > max_chars:
            remain = max_chars - used
            if remain > 200:
                parts.append(chunk[:remain] + "\n…")
            break
        parts.append(chunk)
        used += len(chunk)
    return "\n\n".join(parts)


def load_skill_mds(db, skill_ids: list[str]) -> list[tuple[str, str]]:
    """Load (name, markdown) for agent-bound skills; append key reference snippets."""
    from pathlib import Path

    from app.config import get_settings
    from app.models import Skill
    from app.services import skill_runtime

    out: list[tuple[str, str]] = []
    data_skills = Path(get_settings().data_dir) / "skills"
    for sid in skill_ids or []:
        sk = db.query(Skill).filter(Skill.id == sid).first()
        if not sk:
            continue
        try:
            md = skill_runtime.read_md(sk)
        except Exception:
            md = ""
        # Inject export-report / improvement-habit when present (domain harness)
        extra_bits: list[str] = []
        base = data_skills / str(sk.id)
        if base.is_dir():
            for rel, cap in (
                ("references/export-report.md", 2600),
                ("references/improvement-habit.md", 800),
            ):
                p = base / rel
                if not p.is_file():
                    continue
                try:
                    ref = p.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
                body = ref[:cap]
                # Prefer auto-lessons even when truncated mid-file
                if rel.endswith("export-report.md"):
                    from app.services.skill_lesson import extract_auto_lessons_block

                    auto = extract_auto_lessons_block(ref)
                    if auto and "auto-lessons" not in body and "自动沉淀" not in body:
                        body = body.rstrip() + "\n\n## 自动沉淀（近期）\n" + auto[:900]
                extra_bits.append(f"\n\n---\n# {rel}\n{body}")
        if extra_bits:
            md = (md or "") + "".join(extra_bits)
        out.append((sk.name or sid, md or ""))
    return out


async def classify_view_intent_llm(
    llm,
    db,
    user_message: str,
    provisional: ViewIntentRoute,
    *,
    timeout: int | None = 20,
) -> ViewIntentRoute:
    """One-shot LLM tie-break when rules return ambiguous. On failure, keep provisional with safe fallback."""
    if provisional.mode != "ambiguous":
        return provisional
    if not llm:
        # Prefer single_view when we somehow have pins; else multi_fact (legacy)
        if provisional.pinned_views:
            return ViewIntentRoute(
                mode="single_view",
                pinned_views=list(provisional.pinned_views),
                reason="ambiguous_fallback_pinned",
            )
        return ViewIntentRoute(
            mode="multi_fact",
            pinned_views=[],
            reason="ambiguous_fallback_multi",
        )
    try:
        from app.services.llm_client import chat_completion
    except Exception:
        return ViewIntentRoute(
            mode="multi_fact",
            pinned_views=[],
            reason="ambiguous_fallback_multi",
        )
    prompt = (
        "判断导出任务视图意图，只输出一行 JSON，不要其它文字：\n"
        '{"mode":"single_view|multi_fact|light_identity","views":["view_result_..."],"reason":"..."}\n'
        "- single_view: 用户点名聚合/单表视图，只需查这些 view\n"
        "- multi_fact: 按输出列 join 所需资源（勿默认全量拉取）\n"
        "- light_identity: 只要姓/名等身份字段\n\n"
        f"用户消息：\n{(user_message or '')[:1200]}"
    )
    try:
        raw = await chat_completion(
            llm,
            [{"role": "user", "content": prompt}],
            max_tokens=200,
            db=db,
            timeout=timeout,
        )
    except Exception:
        return ViewIntentRoute(
            mode="multi_fact",
            pinned_views=[],
            reason="ambiguous_llm_error",
        )
    text = (raw or "").strip()
    m = re.search(r"\{[^{}]+\}", text, re.S)
    if not m:
        return ViewIntentRoute(
            mode="multi_fact",
            pinned_views=[],
            reason="ambiguous_llm_parse",
        )
    try:
        import json as _json

        obj = _json.loads(m.group(0))
    except Exception:
        return ViewIntentRoute(
            mode="multi_fact",
            pinned_views=[],
            reason="ambiguous_llm_json",
        )
    mode = str(obj.get("mode") or "").strip().lower()
    views = extract_pinned_views(" ".join(str(v) for v in (obj.get("views") or [])))
    if not views:
        views = list(provisional.pinned_views)
    if mode == "single_view":
        return ViewIntentRoute(
            mode="single_view",
            pinned_views=views or list(provisional.pinned_views),
            reason="llm_single_view",
        )
    if mode == "light_identity":
        return ViewIntentRoute(
            mode="light_identity",
            pinned_views=[],
            reason="llm_light_identity",
        )
    if mode == "multi_fact":
        return ViewIntentRoute(
            mode="multi_fact",
            pinned_views=[],
            reason="llm_multi_fact",
        )
    return ViewIntentRoute(
        mode="multi_fact",
        pinned_views=[],
        reason="ambiguous_llm_unknown",
    )
