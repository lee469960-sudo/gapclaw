"""Export-run → Skill lesson drafts + auto-merge into export-report references.

High-confidence lessons append into `references/export-report.md` auto-lessons
block. Formal SKILL.md body is never rewritten.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from app.services.workplace import ensure_workplace

_AUTO_LESSONS_START = "<!-- auto-lessons -->"
_AUTO_LESSONS_END = "<!-- /auto-lessons -->"
_AUTO_LESSONS_MAX_CHARS = 3500
_HIGH_CONF_LESSON_MARKERS = (
    "mcp_repeat:",
    "row_incomplete",
    "fact_starved",
    "format_clause",
    "FORMAT",
    "满页截断",
    "no_data",
    "iters_exhausted",
)

_FACT_VIEWS = {
    "pay": "view_result_pay_order_log",
    "cash": "view_result_cash_order_log",
    "bet": "view_result_user_bet_log",
}

_MCP_CLASS_SOP: dict[str, str] = {
    "invalid_union": (
        "where 必须是 JSON 对象（如 `{\"stat_date\":\"YYYY-MM-DD\"}`），"
        "不能是字符串/数组；字段名来自 describe_ads_view。"
    ),
    "where_sql": (
        "过滤用 where 对象；时间窗/复杂条件放 sql，且 sql 仅 SELECT + `FROM ads.<view>`。"
    ),
    "select_only": "sql 只允许 SELECT，禁止 INSERT/UPDATE/多语句。",
    "from_ads": "sql 必须含 `FROM ads.<view>`，view 与 FROM 一致。",
    "unknown_tool": "先 list 可用工具名，勿臆造工具。",
    "local_validate": "禁止空参；query 用 view（非 view_name）；describe 用 view_name。",
    "transport": "传输/超时失败：降 limit、换视图或稍后重试，勿同参连打。",
    "format_clause": (
        "禁止在 sql 写 `FORMAT JSONEachRow` 或结尾分号（平台自动 FORMAT）；"
        "大结果用 limit + 同窗 OFFSET 分页落 task/，勿 toJSONString(groupArray)。"
    ),
    "other": "对照 enrichment 纠偏与 export-report §3，改参后再调。",
}


def _safe_run_id(run_id: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in str(run_id or ""))[:48]


def load_run_state(sandbox_id: str, run_id: str) -> dict[str, Any] | None:
    rid = _safe_run_id(run_id)
    if not rid:
        return None
    path = ensure_workplace(sandbox_id) / "task" / rid / "_run_state.json"
    if not path.is_file():
        return None
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return obj if isinstance(obj, dict) else None


def find_latest_run_state(
    sandbox_id: str,
    *,
    exclude_run_id: str = "",
    session_id: str = "",
) -> tuple[str, dict[str, Any]] | None:
    """Newest task/<run>/_run_state.json under workplace (optionally exclude one run).

    When ``session_id`` is set, only states from that session are eligible.
    Returning another session's state would merge unrelated task contracts.
    """
    root = ensure_workplace(sandbox_id) / "task"
    if not root.is_dir():
        return None
    skip = _safe_run_id(exclude_run_id)
    want_sess = (session_id or "").strip()
    candidates: list[tuple[float, str, dict]] = []
    for d in root.iterdir():
        if not d.is_dir() or d.name.startswith("_"):
            continue
        if skip and d.name == skip:
            continue
        state_path = d / "_run_state.json"
        if not state_path.is_file():
            continue
        try:
            obj = json.loads(state_path.read_text(encoding="utf-8"))
            mtime = state_path.stat().st_mtime
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(obj, dict):
            candidates.append((mtime, d.name, obj))
    if not candidates:
        return None
    candidates.sort(key=lambda x: x[0], reverse=True)
    if want_sess:
        for _mtime, rid, state in candidates:
            if str(state.get("session_id") or "").strip() == want_sess:
                return rid, state
        return None
    _, rid, state = candidates[0]
    return rid, state


def find_previous_run_state(
    sandbox_id: str,
    current_run_id: str,
    *,
    session_id: str = "",
) -> tuple[str, dict[str, Any]] | None:
    """Newest other task/<run>/_run_state.json under workplace."""
    return find_latest_run_state(
        sandbox_id, exclude_run_id=current_run_id, session_id=session_id,
    )


def _fact_role_pages(fetched_view_pages: dict | None, role: str) -> int:
    """Sum pages for pay/cash/bet from view names (no engine import)."""
    total = 0
    role = (role or "").strip().lower()
    for view, n in (fetched_view_pages or {}).items():
        v = str(view or "").lower()
        if not v:
            continue
        if role == "pay" and "pay" in v:
            total += int(n or 0)
        elif role == "cash" and "cash" in v:
            total += int(n or 0)
        elif role == "bet" and "bet" in v:
            total += int(n or 0)
    return total


def _is_failed_repair_shell(state: dict | None) -> bool:
    """True when a run has no contract data worth carrying into another turn."""
    if not isinstance(state, dict):
        return True
    pages = state.get("fetched_view_pages") or {}
    has_pages = any(int(n or 0) > 0 for n in pages.values())
    if has_pages:
        return False
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
    has_columns = bool(
        state.get("analyze_columns")
        or task_spec.get("requested_columns")
        or query_contract.get("output_columns")
    )
    if has_columns or str(state.get("deliverable") or "").strip():
        return False
    phase = str(state.get("phase") or "").strip()
    try:
        q = int(state.get("mcp_query_count") or 0)
    except (TypeError, ValueError):
        q = 0
    return phase == "discover" or q == 0 or state.get("completeness") == "query_failed"


def find_repair_base_run_state(
    sandbox_id: str,
    *,
    exclude_run_id: str = "",
    session_id: str = "",
) -> tuple[str, dict[str, Any]] | None:
    """Newest reusable export run for「按缺口补齐」— skip empty failed-repair shells.

    When ``session_id`` is set, only a matching session may provide the repair
    baseline. An unrelated session is never a valid continuation contract.
    """
    root = ensure_workplace(sandbox_id) / "task"
    if not root.is_dir():
        return None
    skip = _safe_run_id(exclude_run_id)
    want_sess = (session_id or "").strip()
    candidates: list[tuple[float, str, dict]] = []
    for d in root.iterdir():
        if not d.is_dir() or d.name.startswith("_"):
            continue
        if skip and d.name == skip:
            continue
        state_path = d / "_run_state.json"
        if not state_path.is_file():
            continue
        try:
            obj = json.loads(state_path.read_text(encoding="utf-8"))
            mtime = state_path.stat().st_mtime
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(obj, dict):
            candidates.append((mtime, d.name, obj))
    if not candidates:
        return None
    candidates.sort(key=lambda x: x[0], reverse=True)

    def _pick(pool: list[tuple[float, str, dict]]) -> tuple[str, dict[str, Any]] | None:
        fallback: tuple[str, dict[str, Any]] | None = None
        for _mtime, rid, state in pool:
            if _is_failed_repair_shell(state):
                continue
            pages = state.get("fetched_view_pages") or {}
            has_pages = any(int(n or 0) > 0 for n in pages.values())
            has_brief = bool(str(state.get("source_brief") or "").strip())
            if has_brief or has_pages:
                return rid, state
            if fallback is None:
                fallback = (rid, state)
        return fallback

    if want_sess:
        same = [
            c for c in candidates
            if str(c[2].get("session_id") or "").strip() == want_sess
        ]
        return _pick(same)
    return _pick(candidates)


def infer_repair_gaps(state: dict | None) -> list[str]:
    """Roles still needed for repair: missing, need_continue, else target-covered."""
    if not isinstance(state, dict):
        return []
    covered = {str(r).strip() for r in (state.get("covered_roles") or []) if str(r).strip()}
    target = [str(r).strip() for r in (state.get("target_roles") or []) if str(r).strip()]
    missing = [str(r).strip() for r in (state.get("missing_roles") or []) if str(r).strip()]
    need_cont = [
        str(r).strip()
        for r in (state.get("need_continue_roles") or [])
        if str(r).strip()
    ]
    pages = state.get("fetched_view_pages") or {}
    if missing:
        gaps = list(missing)
    else:
        gaps = [r for r in target if r not in covered]
    for role in need_cont:
        if role and role not in gaps:
            gaps.append(role)
    # Never expand empty target → pay/cash/bet hexad; only zero-page facts in target/missing
    fact_scope = [r for r in ("pay", "cash", "bet") if r in target or r in missing]
    for role in fact_scope:
        if _fact_role_pages(pages, role) <= 0 and role not in gaps:
            if role in target or role in missing:
                gaps.append(role)
    # Dedupe preserve order
    out: list[str] = []
    seen: set[str] = set()
    for r in gaps:
        if r and r not in seen:
            seen.add(r)
            out.append(r)
    return out


def _roles_from_state(state: dict | None) -> tuple[list[str], list[str]]:
    """Best-effort covered/missing from run_state keys."""
    if not isinstance(state, dict):
        return [], []
    covered = [str(r) for r in (state.get("covered_roles") or []) if r]
    missing = [str(r) for r in (state.get("missing_roles") or []) if r]
    return covered, missing


def _format_repair_plan_section(repair_plan: dict | None) -> list[str]:
    plan = repair_plan if isinstance(repair_plan, dict) else {}
    actions = [a for a in (plan.get("actions") or []) if isinstance(a, dict)]
    if not actions:
        return []
    lines = ["", "### 结构化修复计划（RepairPlan）"]
    status = str(plan.get("status") or "").strip()
    source = str(plan.get("source_status") or "").strip()
    if status or source:
        bits = []
        if status:
            bits.append(f"status=`{status}`")
        if source:
            bits.append(f"source=`{source}`")
        lines.append("- " + "；".join(bits))
    for a in actions[:8]:
        kind = str(a.get("action_type") or "").strip()
        role = str(a.get("role") or "").strip()
        col = str(a.get("column") or "").strip()
        reason = str(a.get("reason") or "").strip()
        keys = [
            str(k).strip()
            for k in (a.get("node_keys") or [])
            if str(k).strip()
        ][:4]
        views = [
            str(v).strip()
            for v in (a.get("views") or [])
            if str(v).strip()
        ][:3]
        item = [kind or "repair"]
        if role:
            item.append(f"role={role}")
        if col:
            item.append(f"column={col}")
        if keys:
            item.append("node_keys=" + ",".join(keys))
        if views:
            item.append("views=" + ",".join(views))
        if reason:
            item.append(reason)
        lines.append("- repair_plan:" + "；".join(item))
    keep = [
        str(x).strip()
        for x in (plan.get("preserve_done_node_keys") or [])
        if str(x).strip()
    ]
    skip = [
        str(x).strip()
        for x in (plan.get("skip_failed_node_keys") or [])
        if str(x).strip()
    ]
    if keep:
        lines.append("- preserve_done_node_keys：" + "、".join(keep[:8]))
    if skip:
        lines.append("- skip_failed_node_keys：" + "、".join(skip[:8]))
    return lines


def build_export_skill_lesson(
    *,
    run_id: str,
    user_message: str = "",
    mode: str = "analyzed",
    deliverable: str = "",
    covered_roles: list[str] | None = None,
    missing_roles: list[str] | None = None,
    fetched_view_pages: dict[str, int] | None = None,
    column_todos: list[str] | None = None,
    time_window_label: str = "",
    prev_run_id: str = "",
    prev_covered: list[str] | None = None,
    prev_missing: list[str] | None = None,
    fallback: bool = False,
    mcp_failures: list[dict] | None = None,
    fact_truncated_roles: list[str] | None = None,
    deliverable_rows: int | None = None,
    cohort_uid_estimate: int | None = None,
    completeness: str = "",
    digest: str = "",
    repair_plan: dict | None = None,
    shell_enabled: bool = True,
) -> str:
    """Rule-based Skill lesson markdown for one export run."""
    covered = list(covered_roles or [])
    missing = list(missing_roles or [])
    pages = dict(fetched_view_pages or {})
    cols = [c for c in (column_todos or []) if str(c).strip()][:12]
    prev_c = list(prev_covered or [])
    prev_m = list(prev_missing or [])
    mcp_fails = [x for x in (mcp_failures or []) if isinstance(x, dict) and x.get("class")][:8]
    fact_trunc = [str(r).strip() for r in (fact_truncated_roles or []) if str(r).strip()]
    comp = str(completeness or "").strip()
    dig = str(digest or "").strip()
    try:
        rows_n = int(deliverable_rows) if deliverable_rows is not None else None
    except (TypeError, ValueError):
        rows_n = None
    try:
        est_n = int(cohort_uid_estimate) if cohort_uid_estimate is not None else None
    except (TypeError, ValueError):
        est_n = None
    row_gap = bool(
        est_n and est_n > 0 and rows_n is not None and rows_n < int(est_n * 0.85)
    )
    incomplete_fetch = bool(fact_trunc or row_gap)

    um = re.sub(r"\s+", " ", (user_message or "").strip())
    if len(um) > 200:
        um = um[:197] + "…"

    lines: list[str] = [
        f"## 导出课程草稿 `{run_id}`",
        "",
        "### 任务摘要",
        f"- 用户需求：{um or '（空）'}",
        f"- 时间窗：{time_window_label or '见用户需求'}",
        f"- 交付模式：{mode}" + ("（原始回退）" if fallback or mode == "fallback" else ""),
        f"- 交付文件：`{deliverable or '（无）'}`",
    ]
    if comp:
        lines.append(f"- 完整度：`{comp}`")
    if dig:
        lines.append(f"- 摘要：{dig}")
    if cols:
        lines.append("- 目标列：" + "；".join(cols[:10]) + ("…" if len(cols) > 10 else ""))
    if rows_n is not None:
        lines.append(f"- 交付行数：{rows_n}")
    if est_n is not None:
        lines.append(f"- cohort 目标 uid≈{est_n}")

    lines += [
        "",
        "### 本轮覆盖",
        f"- 已覆盖 role：{('、'.join(covered) if covered else '无/未知')}",
        f"- 仍缺 role：{('、'.join(missing) if missing else '无')}",
    ]
    if pages:
        page_bits = [f"{v}×{n}" for v, n in sorted(pages.items())[:12]]
        lines.append("- 视图页数：" + "，".join(page_bits))
    if fact_trunc:
        lines.append("- 明细满页截断：" + "、".join(fact_trunc))

    if prev_run_id or prev_c or prev_m:
        newly = [r for r in covered if r and r not in prev_c]
        still = [r for r in missing if r]
        lines += [
            "",
            "### 过程对比",
            f"- 上一轮 run：`{prev_run_id or '未知'}`",
            f"- 上一轮覆盖：{('、'.join(prev_c) if prev_c else '未知')}",
            f"- 上一轮缺口：{('、'.join(prev_m) if prev_m else '未知/无')}",
            f"- 本轮新增补齐：{('、'.join(newly) if newly else '无')}",
            f"- 本轮仍缺：{('、'.join(still) if still else '无')}",
        ]

    causes: list[str] = []
    if comp == "no_data":
        causes.append("no_data：MCP/筛选未落盘任何过程页（时间窗/权限/条件需核对）")
    elif comp == "iters_exhausted":
        causes.append(
            "iters_exhausted：轮次用尽；task/ 可能有页但未写出当前目录 xlsx"
        )
    elif comp == "truncated":
        causes.append("truncated：满页截断或行数缺口，须 OFFSET 续翻至短页")
    elif comp == "fallback" and "fallback" not in "".join(causes):
        pass  # handled below with fallback flag
    fact_miss = [r for r in missing if r in ("pay", "cash", "bet")]
    if fact_miss:
        deep = []
        for r in ("pay", "cash", "bet"):
            n = sum(n for v, n in pages.items() if r in v.lower())
            if n >= 2 and r not in fact_miss:
                deep.append(f"{r}×{n}")
        if deep and fact_miss:
            causes.append(
                "fact_starved：预算可能被 "
                + "、".join(deep)
                + " 深翻占满，导致 "
                + "、".join(fact_miss)
                + " 从未落盘"
            )
        else:
            causes.append(
                "fact_starved：目标明细未齐套（" + "、".join(fact_miss) + "）"
            )
    if fact_trunc:
        causes.append(
            "row_incomplete：明细满页截断（"
            + "、".join(fact_trunc)
            + "）仍收工 → 须同窗续页至短页"
        )
    if row_gap and est_n is not None and rows_n is not None:
        causes.append(
            f"row_incomplete：交付行数={rows_n} << cohort目标≈{est_n}"
            f"（<{int(est_n * 0.85)}）"
        )
    if fallback or mode == "fallback":
        if not missing:
            causes.append("analyze_incomplete：角色可能已齐但 SHELL 未写当前目录中文表")
        else:
            causes.append("fallback：分析未达标，走了原始合并")
    for item in mcp_fails:
        cls = str(item.get("class") or "other")
        cnt = int(item.get("count") or 0)
        tool = str(item.get("tool") or "")
        causes.append(f"mcp_repeat:{cls}：`{tool or 'mcp'}` 同类失败 {cnt} 次")
    if not causes:
        if mode == "analyzed" and not missing and not incomplete_fetch:
            causes.append("本轮分析交付较完整；可将成功 SOP 固化到 Skill")
        else:
            causes.append("未见明显规则缺口；请对照 task 页清单与 MCP 报错")

    lines += ["", "### 根因候选（规则生成）"]
    for c in causes:
        lines.append(f"- {c}")

    if dig and dig not in "\n".join(causes):
        lines.append(f"- digest：{dig}")

    if mcp_fails:
        lines += ["", "### MCP 失败摘要"]
        for item in mcp_fails:
            cls = str(item.get("class") or "other")
            cnt = int(item.get("count") or 0)
            tool = str(item.get("tool") or "")
            sample = re.sub(r"\s+", " ", str(item.get("sample") or ""))[:120]
            lines.append(f"- `{cls}` ×{cnt} tool=`{tool}` sample={sample}")

    lines += _format_repair_plan_section(repair_plan)

    lines += ["", "### 建议写入 Skill（可复制）", ""]
    lines.append("```markdown")
    if mcp_fails:
        lines.append("#### MCP 反例\n")
        for item in mcp_fails[:4]:
            cls = str(item.get("class") or "other")
            sop = _MCP_CLASS_SOP.get(cls, _MCP_CLASS_SOP["other"])
            lines.append(
                f"- `{cls}`（{item.get('tool') or 'mcp'} ×{item.get('count') or 0}）：{sop}\n"
            )
        lines.append(
            "#### MCP SOP 补丁\n"
            "1. 同类校验错误禁止同参连打；改 where/sql/view 后再调。\n"
            "2. 对照 `export-report.md` §3 常见 MCP 错误表。\n"
            "3. FORMAT 类错误去掉 FORMAT 后继续分页，勿整工具放弃。\n"
        )
    if comp in ("no_data", "iters_exhausted"):
        if comp == "no_data":
            lines.append(
                "#### 反例\n"
                "筛选/时间窗/权限导致无落盘页 → FINAL 空交付。\n\n"
                "#### SOP 补丁\n"
                "1. 先 COUNT 核对时间窗是否有数据，再改窗/改参。\n"
                "2. list/describe 确认视图权限后再 query。\n"
            )
        else:
            lines.append(
                "#### 反例\n"
                "轮次用尽时 task/ 已有 page 却未写当前目录 xlsx。\n\n"
                "#### SOP 补丁\n"
                "1. 有 task/page_*.json 时优先 SHELL 写当前目录中文表，勿空 FINAL。\n"
                "2. 新会话说「按缺口补齐重新导出」续翻缺口 role。\n"
            )
    if incomplete_fetch:
        lines.append(
            "#### 反例\n"
            "角色≥1 页但明细满页截断 / 交付行数远小于 COUNT DISTINCT → 假完整。\n\n"
            "#### SOP 补丁\n"
            "1. 充值用户：pay 建 cohort 后，对**已声明**明细 role 续翻至短页再写表。\n"
            "2. 禁止 sql 写 FORMAT；用 limit + OFFSET 落 task/page_*.json。\n"
            "3. FINAL 前对照交付行数与 cohort 目标量级。\n"
        )
    if fact_miss:
        lines.append(
            f"#### 反例\n"
            f"预算花在其它明细深翻上，{('、'.join(fact_miss))} 仍为 0 页 → FINAL 报缺 role。\n"
        )
        lines.append(
            "#### SOP 补丁\n"
            "1. 只补**已声明**缺 role（列计划/白名单/PLAN 需要资源），勿默认齐拉 pay/cash/bet。\n"
            "2. 任一目标明细仍为 0 页时，禁止深翻已有明细；按绑定 resource 各至少 1 页。\n"
            "3. 缺 role 时 list/describe 后绑定再 query；勿硬拉 user_bet_log。\n"
            "4. sql 须含 `FROM ads.<view>`；where 用对象，时间放 sql。\n"
            "5. 落盘当前目录 xlsx 后再 FINAL，勿只写 prep。\n"
        )
    elif fallback and comp not in ("no_data", "iters_exhausted"):
        lines.append(
            "#### 反例\n"
            "角色已齐套但未写出当前目录中文表头 xlsx → 原始回退。\n\n"
            "#### SOP 补丁\n"
            "1. 分析阶段禁止再 MCP；SHELL 读 task/page_*.json → `to_excel` 写当前目录。\n"
            "2. 脚本写 `/tmp/build_report.py`，禁止往 `task/` 写 `.py`。\n"
            "3. PLAN「输出列」只写表头，勿混入 READ/MCP/SHELL 步骤。\n"
        )
    elif (
        not mcp_fails
        and not incomplete_fetch
        and not fact_miss
        and comp not in ("no_data", "iters_exhausted", "truncated", "fact_starved")
    ):
        lines.append(
            "#### 成功要点（可固化）\n"
            "1. 按 PLAN「需要资源」/列白名单拉取；各事实先 1 页再加深至短页。\n"
            "2. join 后写当前目录中文表头 xlsx，再 FINAL。\n"
            "3. 输出列与步骤分栏，避免门禁假未命中。\n"
        )
    lines.append("```")
    lines += [
        "",
        "---",
        f"草稿路径：`lessons/export_{run_id}.md` ；合并方式见 Skill `references/improvement-habit.md`。",
        "**正式 SKILL.md 正文仍人工维护**；高置信反例会自动沉淀进 `export-report.md` 的 auto-lessons 节。",
    ]
    lesson = "\n".join(lines) + "\n"
    if not shell_enabled:
        lesson = lesson.replace(
            "有 task/page_*.json 时优先 SHELL 写当前目录中文表，勿空 FINAL。",
            "有 task/page_*.json 时由引擎按列计划生成并校验中文表，勿空 FINAL。",
        ).replace(
            "分析阶段禁止再 MCP；SHELL 读 task/page_*.json → `to_excel` 写当前目录。",
            "分析阶段禁止再 MCP；由引擎读取 task/page_*.json、关联计算并写当前目录。",
        ).replace(
            "PLAN「输出列」只写表头，勿混入 READ/MCP/SHELL 步骤。",
            "PLAN「输出列」只写表头，勿混入工具协议步骤。",
        )
    return lesson


def lesson_is_high_confidence(lesson_md: str) -> bool:
    text = lesson_md or ""
    return any(m in text for m in _HIGH_CONF_LESSON_MARKERS)


def _extract_auto_lesson_bullets(lesson_md: str, run_id: str) -> list[str]:
    """Short bullets tagged with run_id for the auto-lessons block."""
    rid = _safe_run_id(run_id) or "run"
    digest: list[str] = []
    for ln in (lesson_md or "").splitlines():
        s = ln.strip()
        if not s:
            continue
        if (
            s.startswith("- fact_starved")
            or s.startswith("- analyze_incomplete")
            or s.startswith("- fallback")
            or s.startswith("- mcp_repeat:")
            or s.startswith("- row_incomplete")
            or s.startswith("- repair_plan:")
            or s.startswith("- no_data")
            or s.startswith("- iters_exhausted")
            or s.startswith("- truncated")
            or "满页截断" in s
            or "format_clause" in s
            or (s.startswith("- ") and "FORMAT" in s)
        ):
            if rid not in s:
                s = f"{s} `{rid}`"
            digest.append(s)
        if len(digest) >= 5:
            break
    if not digest and lesson_is_high_confidence(lesson_md):
        digest.append(f"- 高置信缺口（见 lessons/export_{rid}.md） `{rid}`")
    # Dedupe
    out: list[str] = []
    seen: set[str] = set()
    for b in digest:
        key = re.sub(r"\s+", " ", b).strip()
        if key and key not in seen:
            seen.add(key)
            out.append(b)
    return out[:5]


def find_export_report_path(skill_dir: Path | str) -> Path | None:
    """Locate references/export-report.md under a skill package root."""
    base = Path(skill_dir)
    if not base.is_dir():
        return None
    direct = base / "references" / "export-report.md"
    if direct.is_file():
        return direct
    for p in base.rglob("references/export-report.md"):
        if p.is_file():
            return p
    return None


def extract_auto_lessons_block(text: str) -> str:
    """Return inner markdown of <!-- auto-lessons --> … <!-- /auto-lessons -->."""
    if not text or _AUTO_LESSONS_START not in text:
        return ""
    m = re.search(
        re.escape(_AUTO_LESSONS_START) + r"(.*?)" + re.escape(_AUTO_LESSONS_END),
        text,
        flags=re.DOTALL,
    )
    return (m.group(1) if m else "").strip()


def auto_merge_export_lesson_into_skill(
    skill_dir: Path | str,
    lesson_md: str,
    *,
    run_id: str = "",
    max_chars: int = _AUTO_LESSONS_MAX_CHARS,
) -> bool:
    """Append high-confidence lesson bullets into export-report auto-lessons.

    Skips when markers absent, run_id already present, or file missing.
    Caps block length by dropping oldest auto bullets. Returns True if written.
    """
    if not lesson_is_high_confidence(lesson_md):
        return False
    path = find_export_report_path(skill_dir)
    if path is None:
        return False
    rid = _safe_run_id(run_id)
    bullets = _extract_auto_lesson_bullets(lesson_md, rid or run_id)
    if not bullets:
        return False
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return False
    if rid and f"`{rid}`" in extract_auto_lessons_block(text):
        return False

    if _AUTO_LESSONS_START not in text:
        text = (
            text.rstrip()
            + "\n\n## 自动沉淀（引擎）\n\n"
            + f"{_AUTO_LESSONS_START}\n"
            + "（高置信反例自动追加；正式 SKILL.md 正文仍人工维护）\n"
            + f"{_AUTO_LESSONS_END}\n"
        )

    inner = extract_auto_lessons_block(text)
    existing_lines = [ln for ln in inner.splitlines() if ln.strip()]
    # Keep header comment lines that are not bullets
    header = [ln for ln in existing_lines if not ln.strip().startswith("- ")]
    old_bullets = [ln for ln in existing_lines if ln.strip().startswith("- ")]
    new_block_lines = header + old_bullets + bullets
    # Cap: drop oldest bullets first
    while True:
        body = "\n".join(new_block_lines).strip() + "\n"
        if len(body) <= max_chars:
            break
        bullet_idxs = [i for i, ln in enumerate(new_block_lines) if ln.strip().startswith("- ")]
        if not bullet_idxs:
            break
        # Prefer dropping bullets that are not from this run
        drop_i = bullet_idxs[0]
        for i in bullet_idxs:
            if rid and f"`{rid}`" not in new_block_lines[i]:
                drop_i = i
                break
        else:
            # Only this-run bullets left and still over cap — stop adding more
            if all(f"`{rid}`" in new_block_lines[i] for i in bullet_idxs if rid):
                new_block_lines = header + old_bullets
                body = "\n".join(new_block_lines).strip() + "\n"
                break
            drop_i = bullet_idxs[0]
        del new_block_lines[drop_i]

    replacement = (
        f"{_AUTO_LESSONS_START}\n"
        + ("\n".join(new_block_lines).strip() + "\n" if new_block_lines else "")
        + f"{_AUTO_LESSONS_END}"
    )
    updated = re.sub(
        re.escape(_AUTO_LESSONS_START) + r".*?" + re.escape(_AUTO_LESSONS_END),
        replacement,
        text,
        count=1,
        flags=re.DOTALL,
    )
    if updated == text and _AUTO_LESSONS_START in text:
        # run already present or no change
        return False
    try:
        path.write_text(updated, encoding="utf-8")
    except OSError:
        return False
    return True


def format_skill_lesson_final_section(
    lesson_md: str,
    *,
    run_id: str,
    missing_roles: list[str] | None = None,
    fallback: bool = False,
    auto_merged: bool = False,
) -> str:
    """Short FINAL appendix pointing at the full lesson file."""
    if not (lesson_md or "").strip():
        return ""
    miss = [str(r) for r in (missing_roles or []) if r]
    has_mcp = "mcp_repeat:" in lesson_md or "### MCP 失败摘要" in lesson_md
    merge_note = (
        "- 高置信条已自动沉淀进 `references/export-report.md`（auto-lessons）。"
        if auto_merged
        else "- 高置信条可自动沉淀进 `export-report.md` auto-lessons；正式 SKILL.md 正文仍人工维护。"
    )
    if (
        not miss
        and not fallback
        and not has_mcp
        and "fact_starved" not in lesson_md
        and "fallback：" not in lesson_md
        and "row_incomplete" not in lesson_md
    ):
        return (
            "### 建议写入 Skill\n"
            "- 无缺口可跳过合并（本轮分析交付较完整）；可选固化成功 SOP。\n"
            f"- 完整草稿：`lessons/export_{run_id}.md`（及 `task/{run_id}/_skill_lesson.md`）\n"
            f"{merge_note}"
        )
    digest: list[str] = []
    for ln in lesson_md.splitlines():
        s = ln.strip()
        if (
            s.startswith("- fact_starved")
            or s.startswith("- analyze_incomplete")
            or s.startswith("- fallback")
            or s.startswith("- mcp_repeat:")
            or s.startswith("- row_incomplete")
            or s.startswith("- repair_plan:")
        ):
            digest.append(s)
        if s.startswith("1.") or s.startswith("2.") or s.startswith("3."):
            digest.append(s)
        if len(digest) >= 6:
            break
    if not digest:
        for ln in lesson_md.splitlines():
            if ln.strip().startswith("- ") and (
                "fact_" in ln or "回退" in ln or "齐全" in ln or "完整" in ln
            ):
                digest.append(ln.strip())
            if len(digest) >= 4:
                break
    body = "\n".join(digest[:6]) if digest else "- 见完整草稿（过程对比与可复制 SOP）"
    return (
        "### 建议写入 Skill\n"
        f"{body}\n"
        f"- 完整草稿：`lessons/export_{run_id}.md`（及 `task/{run_id}/_skill_lesson.md`）\n"
        f"{merge_note}"
    )


def write_export_skill_lesson(
    sandbox_id: str,
    run_id: str,
    lesson_md: str,
) -> list[str]:
    """Write lessons/export_<run>.md and task/<run>/_skill_lesson.md. Return rel paths."""
    rid = _safe_run_id(run_id)
    if not rid or not (lesson_md or "").strip():
        return []
    root = ensure_workplace(sandbox_id)
    written: list[str] = []
    targets = [
        root / "lessons" / f"export_{rid}.md",
        root / "task" / rid / "_skill_lesson.md",
    ]
    for path in targets:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(lesson_md, encoding="utf-8")
            written.append(str(path.relative_to(root)).replace("\\", "/"))
        except OSError:
            continue
    return written


def materialize_export_skill_lesson(
    sandbox_id: str,
    run_id: str,
    **kwargs: Any,
) -> tuple[str, list[str], str]:
    """Build + write lesson; return (markdown, rel_paths, final_section).

    Optional kwargs:
      skill_dirs: list[Path|str] — ads-sync-hub roots for auto-merge into export-report.
    """
    skill_dirs = kwargs.pop("skill_dirs", None) or []
    session_id = str(kwargs.pop("session_id", "") or "").strip()
    prev = find_previous_run_state(
        sandbox_id,
        run_id,
        session_id=session_id,
    )
    prev_run_id = ""
    prev_covered: list[str] = []
    prev_missing: list[str] = []
    if prev:
        prev_run_id, prev_state = prev
        prev_covered, prev_missing = _roles_from_state(prev_state)

    kwargs.setdefault("prev_run_id", prev_run_id)
    kwargs.setdefault("prev_covered", prev_covered)
    kwargs.setdefault("prev_missing", prev_missing)
    kwargs.setdefault("run_id", run_id)

    md = build_export_skill_lesson(**kwargs)
    paths = write_export_skill_lesson(sandbox_id, run_id, md)
    auto_merged = False
    for d in skill_dirs:
        try:
            if auto_merge_export_lesson_into_skill(d, md, run_id=run_id):
                auto_merged = True
        except Exception:
            continue
    section = format_skill_lesson_final_section(
        md,
        run_id=_safe_run_id(run_id) or run_id,
        missing_roles=list(kwargs.get("missing_roles") or []),
        fallback=bool(kwargs.get("fallback") or kwargs.get("mode") == "fallback"),
        auto_merged=auto_merged,
    )
    return md, paths, section


def gap_tags_for_rolling(
    *,
    missing_roles: list[str] | None = None,
    fallback: bool = False,
    mcp_failures: list[dict] | None = None,
) -> str:
    """Short trailing tags for rolling summary lines."""
    tags: list[str] = []
    miss = [str(r) for r in (missing_roles or []) if r]
    if miss:
        tags.append("缺口:" + ",".join(miss[:4]))
    if fallback:
        tags.append("原始回退")
    seen_cls: set[str] = set()
    for item in mcp_failures or []:
        if not isinstance(item, dict):
            continue
        cls = str(item.get("class") or "").strip()
        if not cls or cls in seen_cls:
            continue
        seen_cls.add(cls)
        tags.append(f"MCP:{cls}")
        if len(seen_cls) >= 3:
            break
    return (" " + " ".join(f"[{t}]" for t in tags)) if tags else ""


def load_recent_mcp_lesson_hints(
    sandbox_id: str,
    *,
    max_files: int = 3,
    max_chars: int = 800,
) -> str:
    """Build 【近期 MCP 反例】 from recent lesson drafts / run_state (no Skill write)."""
    if not sandbox_id:
        return ""
    root = ensure_workplace(sandbox_id)
    lessons_dir = root / "lessons"
    bullets: list[str] = []
    files: list[tuple[float, Path]] = []
    if lessons_dir.is_dir():
        for p in lessons_dir.glob("export_*.md"):
            try:
                files.append((p.stat().st_mtime, p))
            except OSError:
                continue
    files.sort(key=lambda x: x[0], reverse=True)
    for _, path in files[:max_files]:
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        in_mcp = False
        for ln in text.splitlines():
            s = ln.strip()
            if s.startswith("#### MCP 反例") or s.startswith("### MCP 失败摘要"):
                in_mcp = True
                continue
            if in_mcp and s.startswith("#### ") and "MCP" not in s:
                break
            if in_mcp and s.startswith("### ") and "MCP" not in s:
                break
            if s.startswith("- mcp_repeat:"):
                bullets.append(s.lstrip("- ").strip())
            elif in_mcp and s.startswith("- ") and ("`" in s or "where" in s.lower() or "sql" in s.lower()):
                bullets.append(s.lstrip("- ").strip())
            if len(bullets) >= 12:
                break
        if len(bullets) >= 12:
            break

    if not bullets:
        # Fallback: newest run_state mcp_failures
        task_root = root / "task"
        if task_root.is_dir():
            states: list[tuple[float, dict]] = []
            for d in task_root.iterdir():
                sp = d / "_run_state.json"
                if not sp.is_file():
                    continue
                try:
                    obj = json.loads(sp.read_text(encoding="utf-8"))
                    mtime = sp.stat().st_mtime
                except (OSError, json.JSONDecodeError):
                    continue
                if isinstance(obj, dict) and obj.get("mcp_failures"):
                    states.append((mtime, obj))
            states.sort(key=lambda x: x[0], reverse=True)
            for _, st in states[:max_files]:
                for item in st.get("mcp_failures") or []:
                    if not isinstance(item, dict):
                        continue
                    cls = str(item.get("class") or "")
                    if not cls:
                        continue
                    sop = _MCP_CLASS_SOP.get(cls, _MCP_CLASS_SOP["other"])
                    bullets.append(
                        f"`{cls}` ×{item.get('count') or 0} "
                        f"({item.get('tool') or 'mcp'}): {sop}"
                    )
                    if len(bullets) >= 8:
                        break
                if len(bullets) >= 8:
                    break

    if not bullets:
        # Still inject FORMAT soft anti-example even without lesson files
        header = "【近期 MCP 反例】（来自 lessons/ + Skill auto-lessons；改参续查）\n"
        soft_format = (
            "`format_clause`: 禁止 sql 写 FORMAT JSONEachRow/结尾分号；"
            "平台自动 FORMAT；用 limit+OFFSET 分页落 task/"
        )
        return header + f"- {soft_format}"

    # Dedupe preserving order
    seen: set[str] = set()
    uniq: list[str] = []
    for b in bullets:
        key = b[:80]
        if key in seen:
            continue
        seen.add(key)
        uniq.append(b)

    header = "【近期 MCP 反例】（来自 lessons/ + Skill auto-lessons；改参续查）\n"
    soft_format = (
        "`format_clause`: 禁止 sql 写 FORMAT JSONEachRow/结尾分号；"
        "平台自动 FORMAT；用 limit+OFFSET 分页落 task/"
    )
    # Prefer lesson bullets first so short max_chars still surfaces run failures
    body_lines = [f"- {b}" for b in uniq]
    if not any("format" in b.lower() for b in uniq):
        body_lines.append(f"- {soft_format}")
    out = header + "\n".join(body_lines)
    if len(out) > max_chars:
        out = out[: max_chars - 1].rstrip() + "…"
    return out


def load_recent_fetch_gap_hints(
    sandbox_id: str,
    *,
    max_runs: int = 2,
    max_chars: int = 700,
) -> str:
    """Build 【近期拉取缺口】 from recent _run_state completeness fields."""
    if not sandbox_id:
        return ""
    root = ensure_workplace(sandbox_id) / "task"
    if not root.is_dir():
        return ""
    states: list[tuple[float, str, dict]] = []
    for d in root.iterdir():
        if not d.is_dir() or d.name.startswith("_"):
            continue
        sp = d / "_run_state.json"
        if not sp.is_file():
            continue
        try:
            obj = json.loads(sp.read_text(encoding="utf-8"))
            mtime = sp.stat().st_mtime
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(obj, dict):
            states.append((mtime, d.name, obj))
    if not states:
        return ""
    states.sort(key=lambda x: x[0], reverse=True)
    bullets: list[str] = []
    for _, rid, st in states[:max_runs]:
        completeness = str(st.get("completeness") or "").strip()
        digest = str(st.get("digest") or "").strip()
        need_cont = [
            str(r).strip()
            for r in (st.get("need_continue_roles") or [])
            if str(r).strip()
        ]
        user_pages = st.get("user_pages")
        user_complete = st.get("user_fetch_complete")
        user_trunc = bool(st.get("user_truncated"))
        fact_trunc = [
            str(r).strip()
            for r in (st.get("fact_truncated_roles") or [])
            if str(r).strip()
        ]
        missing = [
            str(r).strip() for r in (st.get("missing_roles") or []) if str(r).strip()
        ]
        rows = st.get("deliverable_rows")
        try:
            est = st.get("cohort_uid_estimate")
            est_i = int(est) if est is not None else None
        except (TypeError, ValueError):
            est_i = None
        try:
            budget = int(st.get("budget") or 0)
        except (TypeError, ValueError):
            budget = 0
        bits: list[str] = [f"run `{rid}`"]
        if completeness:
            bits.append(f"completeness={completeness}")
        if digest:
            bits.append(f"摘要:{digest[:120]}")
        if user_pages is not None:
            bits.append(f"user_pages={user_pages}")
        if user_complete is False or user_trunc:
            bits.append("user未短页/截断→须续翻至短页或达帽")
        elif user_complete is True:
            bits.append("user已短页")
        if need_cont:
            bits.append("须续翻:" + "/".join(need_cont))
        elif fact_trunc:
            bits.append("明细满页截断:" + "/".join(fact_trunc) + "→同窗OFFSET续页")
        if missing:
            bits.append("仍缺role:" + "/".join(missing))
        if rows is not None:
            bits.append(f"交付行数={rows}")
        if est_i:
            bits.append(f"目标uid≈{est_i}")
            try:
                rows_i = int(rows) if rows is not None else None
            except (TypeError, ValueError):
                rows_i = None
            if rows_i is not None and rows_i < int(est_i * 0.85):
                bits.append(
                    f"行数缺口严重（<{int(est_i * 0.85)}）→先拉全 pay cohort"
                )
        if budget:
            bits.append(f"上轮预算={budget}")
        repair_plan = st.get("repair_plan") if isinstance(st.get("repair_plan"), dict) else {}
        repair_actions = [
            a for a in (repair_plan.get("actions") or [])
            if isinstance(a, dict)
        ]
        if repair_actions:
            action_bits: list[str] = []
            for a in repair_actions[:3]:
                kind = str(a.get("action_type") or "").strip()
                role = str(a.get("role") or "").strip()
                col = str(a.get("column") or "").strip()
                if not kind:
                    continue
                tail = role or col
                action_bits.append(f"{kind}({tail})" if tail else kind)
            if action_bits:
                bits.append("RepairPlan:" + "/".join(action_bits))
            keep = [
                str(x).strip()
                for x in (repair_plan.get("preserve_done_node_keys") or [])
                if str(x).strip()
            ]
            skip = [
                str(x).strip()
                for x in (repair_plan.get("skip_failed_node_keys") or [])
                if str(x).strip()
            ]
            if keep:
                bits.append("保留成功节点:" + "/".join(keep[:3]))
            if skip:
                bits.append("跳过失败重节点:" + "/".join(skip[:3]))
        # Prefer concrete next_actions MCP examples
        for a in (st.get("next_actions") or [])[:2]:
            if not isinstance(a, dict):
                continue
            mcp = str(a.get("mcp_example") or "").strip()
            hint = str(a.get("hint") or "").strip()
            if mcp:
                bits.append(f"建议:{mcp}")
            elif hint:
                bits.append(f"建议:{hint}")
        # Only keep runs that signal a real gap
        if (
            completeness in (
                "truncated", "fact_starved", "iters_exhausted", "no_data", "fallback",
            )
            or user_complete is False
            or user_trunc
            or fact_trunc
            or need_cont
            or missing
            or repair_actions
            or digest
            or (est_i and rows is not None)
        ):
            bullets.append("；".join(bits))
    if not bullets:
        return ""
    header = (
        "【近期拉取缺口】（来自 task/_run_state；本轮优先补齐截断，勿再浅拉即收工）\n"
        "可执行：pay/cash/bet 同窗 `limit=1000` 续页至短页；"
        "禁止 sql 含 FORMAT。\n"
    )
    out = header + "\n".join(f"- {b}" for b in bullets)
    if len(out) > max_chars:
        out = out[: max_chars - 1].rstrip() + "…"
    return out
